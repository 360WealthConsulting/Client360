"""Opportunity & Pipeline service (Phase D.13) — authoritative CRUD, lifecycle, and scope.

Owns pipeline/opportunity/activity data. References People/Households/Organizations by
validated FK (never creates them; never infers ownership). Configurable pipelines/stages —
lifecycle logic keys off ``opportunity_stages.category`` (open/won/lost/dormant/cancelled),
never a hard-coded stage name. Lifecycle transitions append to the append-only
``opportunity_events`` ledger and emit APPROVED events to the shared Activity Timeline writer
(created / qualified / proposal / won / lost / advisor reassigned) — never on a field edit.
Activities may reference an existing Microsoft 365 ``timeline_events`` row (no duplication).
Scope is enforced in-service (this router is outside the middleware RECORD_PATH): an
opportunity is visible to its primary/supporting/creating advisor, to a principal whose book
contains the target client (person or household), or to ``record.read_all``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from app.db import (
    advisor_work_items,
    campaigns,
    engine,
    households,
    opportunities,
    opportunity_activities,
    opportunity_attributions,
    opportunity_events,
    opportunity_participants,
    opportunity_pipelines,
    opportunity_stages,
    opportunity_work_links,
    people,
    referral_sources,
    relationship_entities,
    timeline_events,
    users,
)
from app.security.authorization import accessible_person_ids
from app.services.timeline import add_timeline_event

_TERMINAL = frozenset({"won", "lost", "dormant", "cancelled"})
# Stage transitions that publish a client Activity Timeline event (approved durable events).
_TIMELINE_STAGE_CODES = frozenset({"qualified", "proposal"})
_UPDATABLE_FIELDS = frozenset({
    "title", "primary_service_line", "secondary_service_lines", "source",
    "referral_source_person_id", "referral_source_text", "originating_campaign",
    "probability", "expected_revenue", "expected_close_date", "next_action",
    "next_action_date", "tags", "notes",
})


class OpportunityError(Exception):
    """A validation or reference error (e.g. a target person/org that does not exist)."""


class OpportunityNotFound(Exception):
    """The opportunity does not exist or is out of the principal's scope."""


def _now():
    return datetime.now(UTC)


# --- pipelines / stages ------------------------------------------------------

def list_pipelines() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(opportunity_pipelines).where(opportunity_pipelines.c.active.is_(True))
            .order_by(opportunity_pipelines.c.id)).mappings()]


def default_pipeline_id(c) -> int:
    pid = c.scalar(select(opportunity_pipelines.c.id)
                   .where(opportunity_pipelines.c.is_default.is_(True)).limit(1))
    if pid is None:
        pid = c.scalar(select(opportunity_pipelines.c.id).order_by(opportunity_pipelines.c.id).limit(1))
    return pid


def list_stages(pipeline_id: int) -> list[dict]:
    with engine.connect() as c:
        return _stages(c, pipeline_id)


def _stages(c, pipeline_id: int) -> list[dict]:
    return [dict(r) for r in c.execute(
        select(opportunity_stages).where(opportunity_stages.c.pipeline_id == pipeline_id,
                                         opportunity_stages.c.active.is_(True))
        .order_by(opportunity_stages.c.sort_order)).mappings()]


def _stage(c, stage_id: int) -> dict | None:
    row = c.execute(select(opportunity_stages).where(opportunity_stages.c.id == stage_id)).mappings().first()
    return dict(row) if row else None


# --- scope -------------------------------------------------------------------

def _scope_clause(principal, c):
    """SQL clause selecting opportunities visible to the principal. ``None`` = firm-wide
    (record.read_all); otherwise: mine (primary/supporting/creator) OR targeting a client in
    my book (person or their household)."""
    if principal.can("record.read_all"):
        return None
    conds = [opportunities.c.primary_advisor_id == principal.user_id,
             opportunities.c.supporting_advisor_id == principal.user_id,
             opportunities.c.created_by == principal.user_id]
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(opportunities.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(opportunities.c.household_id.in_(tuple(hh)))
    return or_(*conds)


def _visible(principal, row: dict, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if principal.user_id in (row.get("primary_advisor_id"), row.get("supporting_advisor_id"),
                             row.get("created_by")):
        return True
    ids = accessible_person_ids(c, principal)
    if ids:
        if row.get("person_id") in ids:
            return True
        hh = c.scalar(select(people.c.household_id).where(people.c.id == row.get("person_id"))) \
            if row.get("person_id") else None
        if row.get("household_id") is not None:
            member = c.scalar(select(people.c.id).where(
                people.c.household_id == row["household_id"], people.c.id.in_(tuple(ids))).limit(1))
            if member is not None:
                return True
        if hh is not None and row.get("household_id") == hh:
            return True
    return False


# --- reference validation ----------------------------------------------------

def _validate_targets(c, *, person_id, household_id, organization_id, primary_advisor_id):
    if person_id is not None and c.scalar(select(people.c.id).where(people.c.id == person_id)) is None:
        raise OpportunityError("target person does not exist")
    if household_id is not None and c.scalar(
            select(households.c.id).where(households.c.id == household_id)) is None:
        raise OpportunityError("target household does not exist")
    if organization_id is not None:
        ent = c.execute(select(relationship_entities.c.entity_type)
                        .where(relationship_entities.c.id == organization_id)).scalar()
        if ent is None:
            raise OpportunityError("target organization does not exist")
    if primary_advisor_id is not None and c.scalar(
            select(users.c.id).where(users.c.id == primary_advisor_id)) is None:
        raise OpportunityError("primary advisor is not a user")


# --- CRUD --------------------------------------------------------------------

def create_opportunity(principal, *, title, actor_user_id, pipeline_id=None, stage_code=None,
                       person_id=None, household_id=None, organization_id=None,
                       primary_advisor_id=None, supporting_advisor_id=None,
                       primary_service_line=None, source=None, expected_revenue=None,
                       expected_close_date=None, probability=None, next_action=None,
                       next_action_date=None, referral_source_person_id=None,
                       referral_source_text=None, originating_campaign=None) -> dict:
    """Create an opportunity. Targets (person/household/organization) are OPTIONAL — a raw
    prospect/lead may have none — but any provided target must already exist (never created
    here). Stage defaults to the pipeline's first stage; probability defaults to the stage's
    default_probability."""
    if not (title or "").strip():
        raise OpportunityError("title is required")
    with engine.begin() as c:
        pid = pipeline_id or default_pipeline_id(c)
        stages = _stages(c, pid)
        if not stages:
            raise OpportunityError("pipeline has no stages")
        stage = next((s for s in stages if s["code"] == stage_code), stages[0])
        _validate_targets(c, person_id=person_id, household_id=household_id,
                          organization_id=organization_id,
                          primary_advisor_id=primary_advisor_id or actor_user_id)
        now = _now()
        row = c.execute(opportunities.insert().values(
            pipeline_id=pid, stage_id=stage["id"], title=title.strip(), status=stage["category"],
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            primary_advisor_id=primary_advisor_id or actor_user_id,
            supporting_advisor_id=supporting_advisor_id, primary_service_line=primary_service_line,
            source=source, probability=(probability if probability is not None else stage["default_probability"]),
            expected_revenue=expected_revenue, expected_close_date=expected_close_date,
            next_action=next_action, next_action_date=next_action_date,
            referral_source_person_id=referral_source_person_id,
            referral_source_text=referral_source_text, originating_campaign=originating_campaign,
            closed_at=(now if stage["category"] in _TERMINAL else None),
            created_by=actor_user_id, updated_by=actor_user_id, created_at=now, updated_at=now,
        ).returning(opportunities)).mappings().one()
        opp = dict(row)
        _append_event(c, opp["id"], event_type="created", to_stage_id=stage["id"],
                      to_status=stage["category"], actor=actor_user_id, note=None)
        _publish_timeline(opp, event_type="created", title=f"Opportunity created — {opp['title']}")
    return opp


def get_opportunity(principal, opportunity_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(opportunities).where(
            opportunities.c.id == opportunity_id)).mappings().first()
        if row is None or not _visible(principal, dict(row), c):
            return None
        opp = dict(row)
        opp["stage"] = _stage(c, opp["stage_id"])
        opp["events"] = [dict(r) for r in c.execute(
            select(opportunity_events).where(opportunity_events.c.opportunity_id == opportunity_id)
            .order_by(opportunity_events.c.occurred_at.desc(), opportunity_events.c.id.desc())).mappings()]
        opp["activities"] = [dict(r) for r in c.execute(
            select(opportunity_activities).where(opportunity_activities.c.opportunity_id == opportunity_id)
            .order_by(opportunity_activities.c.activity_date.desc()).limit(50)).mappings()]
        opp["linked_work"] = _linked_work(c, opportunity_id)
        # Documents (Phase D.16) — read-only visibility of documents related to this opportunity.
        if principal.can("documents.view"):
            from app.services.document_platform.relationships import documents_for_entity
            opp["documents"] = documents_for_entity(principal, "opportunity", opportunity_id, limit=25)
        else:
            opp["documents"] = None
        opp["participants"] = [dict(r) for r in c.execute(
            select(opportunity_participants).where(
                opportunity_participants.c.opportunity_id == opportunity_id)).mappings()]
    return opp


def _clamp(page, page_size):
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    return page, page_size


def list_opportunities(principal, *, stage_id=None, status=None, advisor_id=None,
                       service_line=None, source=None, search=None, page=1, page_size=50) -> dict:
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if stage_id:
            conds.append(opportunities.c.stage_id == stage_id)
        if status:
            conds.append(opportunities.c.status == status)
        if advisor_id:
            conds.append(or_(opportunities.c.primary_advisor_id == advisor_id,
                             opportunities.c.supporting_advisor_id == advisor_id))
        if service_line:
            conds.append(opportunities.c.primary_service_line == service_line)
        if source:
            conds.append(opportunities.c.source == source)
        if search:
            conds.append(opportunities.c.title.ilike(f"%{search.strip()}%"))
        where = and_(*conds) if conds else None
        from sqlalchemy import func
        total = c.scalar(select(func.count()).select_from(opportunities).where(where)
                         if where is not None else select(func.count()).select_from(opportunities))
        page, page_size = _clamp(page, page_size)
        stmt = select(opportunities)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.order_by(opportunities.c.updated_at.desc(), opportunities.c.id.desc()) \
            .limit(page_size).offset((page - 1) * page_size)
        rows = [dict(r) for r in c.execute(stmt).mappings()]
    pages = (total + page_size - 1) // page_size if total else 0
    return {"rows": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def update_opportunity(principal, opportunity_id: int, *, actor_user_id, fields: dict) -> dict:
    """Edit non-lifecycle fields. Does NOT emit a timeline event (field edits are not events)."""
    with engine.begin() as c:
        opp = _load_scoped(c, principal, opportunity_id)
        values = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not values:
            return opp
        values["updated_by"] = actor_user_id
        values["updated_at"] = _now()
        c.execute(opportunities.update().where(opportunities.c.id == opportunity_id).values(**values))
        return _reload(c, opportunity_id)


def change_stage(principal, opportunity_id: int, *, new_stage_id: int, actor_user_id, note=None) -> dict:
    with engine.begin() as c:
        opp = _load_scoped(c, principal, opportunity_id)
        stage = _stage(c, new_stage_id)
        if stage is None or stage["pipeline_id"] != opp["pipeline_id"]:
            raise OpportunityError("stage does not belong to this opportunity's pipeline")
        now = _now()
        values = {"stage_id": stage["id"], "status": stage["category"], "updated_by": actor_user_id,
                  "updated_at": now, "probability": stage["default_probability"]}
        values["closed_at"] = now if stage["category"] in _TERMINAL else None
        if stage["category"] in _TERMINAL:
            values["attribution_locked"] = True   # attribution immutable after close
        c.execute(opportunities.update().where(opportunities.c.id == opportunity_id).values(**values))
        event_type = ("won" if stage["category"] == "won" else "lost" if stage["category"] == "lost"
                      else "stage_changed")
        _append_event(c, opportunity_id, event_type=event_type, from_stage_id=opp["stage_id"],
                      to_stage_id=stage["id"], from_status=opp["status"], to_status=stage["category"],
                      actor=actor_user_id, note=note)
        updated = _reload(c, opportunity_id)
        if stage["category"] in ("won", "lost") or stage["code"] in _TIMELINE_STAGE_CODES:
            label = {"won": "Opportunity won", "lost": "Opportunity lost",
                     "qualified": "Opportunity qualified",
                     "proposal": "Proposal sent"}.get(
                         stage["category"] if stage["category"] in ("won", "lost") else stage["code"],
                         "Opportunity stage changed")
            _publish_timeline(updated, event_type=event_type, title=f"{label} — {updated['title']}")
    return updated


def close_opportunity(principal, opportunity_id: int, *, outcome: str, actor_user_id, reason=None) -> dict:
    """Close an opportunity as won or lost (records the win/loss reason). Moves to the
    pipeline's won/lost stage."""
    if outcome not in ("won", "lost"):
        raise OpportunityError("outcome must be 'won' or 'lost'")
    with engine.begin() as c:
        opp = _load_scoped(c, principal, opportunity_id)
        stages = _stages(c, opp["pipeline_id"])
        target = next((s for s in stages if s["category"] == outcome), None)
        if target is None:
            raise OpportunityError(f"pipeline has no {outcome} stage")
        now = _now()
        values = {"stage_id": target["id"], "status": outcome, "closed_at": now,
                  "probability": target["default_probability"], "updated_by": actor_user_id,
                  "updated_at": now, "attribution_locked": True}
        values["win_reason" if outcome == "won" else "loss_reason"] = reason
        c.execute(opportunities.update().where(opportunities.c.id == opportunity_id).values(**values))
        _append_event(c, opportunity_id, event_type=outcome, from_stage_id=opp["stage_id"],
                      to_stage_id=target["id"], from_status=opp["status"], to_status=outcome,
                      actor=actor_user_id, note=reason)
        updated = _reload(c, opportunity_id)
        _publish_timeline(updated, event_type=outcome,
                          title=f"Opportunity {outcome} — {updated['title']}")
    return updated


def assign_advisor(principal, opportunity_id: int, *, primary_advisor_id=None,
                   supporting_advisor_id="__keep__", actor_user_id) -> dict:
    with engine.begin() as c:
        opp = _load_scoped(c, principal, opportunity_id)
        if primary_advisor_id is not None and c.scalar(
                select(users.c.id).where(users.c.id == primary_advisor_id)) is None:
            raise OpportunityError("primary advisor is not a user")
        values = {"updated_by": actor_user_id, "updated_at": _now()}
        if primary_advisor_id is not None:
            values["primary_advisor_id"] = primary_advisor_id
        if supporting_advisor_id != "__keep__":
            values["supporting_advisor_id"] = supporting_advisor_id
        c.execute(opportunities.update().where(opportunities.c.id == opportunity_id).values(**values))
        _append_event(c, opportunity_id, event_type="advisor_reassigned", actor=actor_user_id,
                      note=f"primary={values.get('primary_advisor_id', opp['primary_advisor_id'])}")
        updated = _reload(c, opportunity_id)
        _publish_timeline(updated, event_type="advisor_reassigned",
                          title=f"Advisor reassigned — {updated['title']}")
    return updated


def delete_opportunity(principal, opportunity_id: int) -> None:
    with engine.begin() as c:
        _load_scoped(c, principal, opportunity_id)
        # Hard delete (guarded by opportunity.delete). Child events/activities/participants/
        # work-links CASCADE. Security-relevant deletion is captured separately by the audit log.
        c.execute(opportunities.delete().where(opportunities.c.id == opportunity_id))


# --- attribution (Phase D.14 — opportunity-owned linkage to campaigns/referrals) ---

_ATTR_FIELDS = frozenset({"origin", "lead_method", "marketing_medium", "referral_type"})


def set_attribution(principal, opportunity_id: int, *, actor_user_id, campaign_id="__keep__",
                    referral_source_id="__keep__", override=False, fields=None,
                    secondary=None) -> dict:
    """Set an opportunity's business-development attribution (primary campaign / referral source
    + origin/lead-method/marketing-medium/referral-type, and optional weighted secondary
    touchpoints). Attribution is IMMUTABLE after the opportunity closes unless ``override=True``.
    Referenced campaigns / referral sources must exist (never created)."""
    with engine.begin() as c:
        opp = _load_scoped(c, principal, opportunity_id)
        if opp.get("attribution_locked") and not override:
            raise OpportunityError("attribution is locked (opportunity closed); pass override")
        values = {"updated_by": actor_user_id, "updated_at": _now()}
        if campaign_id != "__keep__":
            if campaign_id is not None and c.scalar(
                    select(campaigns.c.id).where(campaigns.c.id == campaign_id)) is None:
                raise OpportunityError("campaign does not exist")
            values["campaign_id"] = campaign_id
        if referral_source_id != "__keep__":
            if referral_source_id is not None and c.scalar(
                    select(referral_sources.c.id).where(referral_sources.c.id == referral_source_id)) is None:
                raise OpportunityError("referral source does not exist")
            values["referral_source_id"] = referral_source_id
        for k, v in (fields or {}).items():
            if k in _ATTR_FIELDS:
                values[k] = v
        c.execute(opportunities.update().where(opportunities.c.id == opportunity_id).values(**values))
        # Rebuild attribution touchpoints: one primary (from the resolved primary refs) + any
        # weighted secondary entries provided.
        updated = _reload(c, opportunity_id)
        c.execute(opportunity_attributions.delete().where(
            opportunity_attributions.c.opportunity_id == opportunity_id))
        if updated.get("campaign_id") or updated.get("referral_source_id"):
            c.execute(opportunity_attributions.insert().values(
                opportunity_id=opportunity_id, campaign_id=updated.get("campaign_id"),
                referral_source_id=updated.get("referral_source_id"), weight=100, is_primary=True,
                created_at=_now()))
        for s in (secondary or []):
            c.execute(opportunity_attributions.insert().values(
                opportunity_id=opportunity_id, campaign_id=s.get("campaign_id"),
                referral_source_id=s.get("referral_source_id"), weight=s.get("weight", 0),
                is_primary=False, created_at=_now()))
    return updated


def attribution_for(opportunity_id: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(opportunity_attributions).where(
            opportunity_attributions.c.opportunity_id == opportunity_id)
            .order_by(opportunity_attributions.c.is_primary.desc())).mappings()]


def opportunities_for_campaign(principal, campaign_id: int, *, limit=5000) -> list[dict]:
    """Opportunities attributed to a campaign, scoped to the principal's pipeline."""
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = [opportunities.c.campaign_id == campaign_id]
        if scope is not None:
            conds.append(scope)
        return [dict(r) for r in c.execute(select(opportunities).where(and_(*conds))
                                           .limit(limit)).mappings()]


def opportunities_for_referral_source(principal, referral_source_id: int, *, limit=5000) -> list[dict]:
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = [opportunities.c.referral_source_id == referral_source_id]
        if scope is not None:
            conds.append(scope)
        return [dict(r) for r in c.execute(select(opportunities).where(and_(*conds))
                                           .limit(limit)).mappings()]


# --- activities + work links -------------------------------------------------

def log_activity(principal, opportunity_id: int, *, activity_type, actor_user_id, subject=None,
                 body=None, timeline_event_id=None, activity_date=None) -> dict:
    with engine.begin() as c:
        _load_scoped(c, principal, opportunity_id)
        if timeline_event_id is not None and c.scalar(
                select(timeline_events.c.id).where(timeline_events.c.id == timeline_event_id)) is None:
            raise OpportunityError("referenced timeline event does not exist")
        row = c.execute(opportunity_activities.insert().values(
            opportunity_id=opportunity_id, activity_type=activity_type, subject=subject, body=body,
            actor_user_id=actor_user_id, timeline_event_id=timeline_event_id,
            activity_date=activity_date or _now(), created_at=_now()).returning(
                opportunity_activities)).mappings().one()
        _append_event(c, opportunity_id, event_type="activity_logged", actor=actor_user_id,
                      note=f"{activity_type}: {subject or ''}"[:200])
    return dict(row)


def link_work(principal, opportunity_id: int, advisor_work_item_id: int, *, actor_user_id) -> dict:
    """Reference an existing Advisor Work item from this opportunity (the Opportunity domain
    owns the link; Advisor Work is never modified and never owns the opportunity)."""
    with engine.begin() as c:
        _load_scoped(c, principal, opportunity_id)
        if c.scalar(select(advisor_work_items.c.id)
                    .where(advisor_work_items.c.id == advisor_work_item_id)) is None:
            raise OpportunityError("advisor work item does not exist")
        existing = c.scalar(select(opportunity_work_links.c.id).where(
            opportunity_work_links.c.opportunity_id == opportunity_id,
            opportunity_work_links.c.advisor_work_item_id == advisor_work_item_id))
        if existing:
            return {"id": existing}
        row = c.execute(opportunity_work_links.insert().values(
            opportunity_id=opportunity_id, advisor_work_item_id=advisor_work_item_id,
            created_by=actor_user_id, created_at=_now()).returning(opportunity_work_links)).mappings().one()
    return dict(row)


def _linked_work(c, opportunity_id: int) -> list[dict]:
    rows = c.execute(
        select(opportunity_work_links.c.advisor_work_item_id, advisor_work_items.c.status,
               advisor_work_items.c.recommendation_type)
        .select_from(opportunity_work_links.join(
            advisor_work_items, advisor_work_items.c.id == opportunity_work_links.c.advisor_work_item_id))
        .where(opportunity_work_links.c.opportunity_id == opportunity_id)).mappings().all()
    return [dict(r) for r in rows]


# --- additive scoped reads for consumers -------------------------------------

def opportunities_for_person(principal, person_id: int, *, open_only=False, limit=50) -> list[dict]:
    """This client's opportunities (Annual Review / Business Owner Planning composition read).
    Scope-first via the standard opportunity scope; returns [] if out of scope."""
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = [or_(opportunities.c.person_id == person_id,
                     opportunities.c.household_id.in_(
                         select(people.c.household_id).where(people.c.id == person_id,
                                                             people.c.household_id.is_not(None))))]
        if scope is not None:
            conds.append(scope)
        if open_only:
            conds.append(opportunities.c.status == "open")
        rows = c.execute(select(opportunities).where(and_(*conds))
                         .order_by(opportunities.c.updated_at.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def all_in_scope(principal, *, statuses=None, limit=5000) -> list[dict]:
    """Every opportunity visible to the principal (for reporting / pipeline intelligence).
    Bounded; scope-clause applied before the fetch (never firm-wide unless record.read_all)."""
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if statuses:
            conds.append(opportunities.c.status.in_(tuple(statuses)))
        stmt = select(opportunities)
        if conds:
            stmt = stmt.where(and_(*conds))
        rows = c.execute(stmt.order_by(opportunities.c.id).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def latest_activity_dates(opportunity_ids) -> dict[int, datetime]:
    """Most-recent activity timestamp per opportunity (one query; for stalled-pipeline
    detection). Opportunities with no activity are absent from the map."""
    if not opportunity_ids:
        return {}
    from sqlalchemy import func
    with engine.connect() as c:
        rows = c.execute(
            select(opportunity_activities.c.opportunity_id,
                   func.max(opportunity_activities.c.activity_date).label("last"))
            .where(opportunity_activities.c.opportunity_id.in_(tuple(opportunity_ids)))
            .group_by(opportunity_activities.c.opportunity_id)).all()
    return {oid: last for oid, last in rows}


def opportunities_for_organization(principal, organization_id: int, *, limit=50) -> list[dict]:
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = [opportunities.c.organization_id == organization_id]
        if scope is not None:
            conds.append(scope)
        rows = c.execute(select(opportunities).where(and_(*conds))
                         .order_by(opportunities.c.updated_at.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def opportunities_for_people(person_ids, *, open_only=True, limit=500) -> list[dict]:
    """Opportunities targeting a pre-resolved set of person ids (Pipeline Intelligence read).
    The caller has already resolved scope onto ``person_ids``; bounded."""
    if not person_ids:
        return []
    with engine.connect() as c:
        conds = [opportunities.c.person_id.in_(tuple(person_ids))]
        if open_only:
            conds.append(opportunities.c.status == "open")
        rows = c.execute(select(opportunities).where(and_(*conds))
                         .order_by(opportunities.c.updated_at.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


# --- internals ---------------------------------------------------------------

def _load_scoped(c, principal, opportunity_id: int) -> dict:
    row = c.execute(select(opportunities).where(opportunities.c.id == opportunity_id)).mappings().first()
    if row is None or not _visible(principal, dict(row), c):
        raise OpportunityNotFound(str(opportunity_id))
    return dict(row)


def _reload(c, opportunity_id: int) -> dict:
    return dict(c.execute(select(opportunities).where(
        opportunities.c.id == opportunity_id)).mappings().one())


def _append_event(c, opportunity_id, *, event_type, from_stage_id=None, to_stage_id=None,
                  from_status=None, to_status=None, actor=None, note=None) -> int:
    return c.execute(opportunity_events.insert().values(
        opportunity_id=opportunity_id, event_type=event_type, from_stage_id=from_stage_id,
        to_stage_id=to_stage_id, from_status=from_status, to_status=to_status,
        actor_user_id=actor, note=note, occurred_at=_now()).returning(
            opportunity_events.c.id)).scalar_one()


def _publish_timeline(opp: dict, *, event_type: str, title: str) -> None:
    """Emit an APPROVED opportunity event to the shared Activity Timeline writer (no second
    event table). Only client-anchored opportunities appear on a client timeline; a raw
    prospect with no person/household is skipped (it has no client to attach to)."""
    if opp.get("person_id") is None and opp.get("household_id") is None:
        return
    add_timeline_event(
        source="opportunity", event_type=f"opportunity_{event_type}", title=title,
        summary=opp.get("primary_service_line") or opp.get("source") or "",
        person_id=opp.get("person_id"), household_id=opp.get("household_id"),
        external_id=f"opportunity-{opp['id']}-{event_type}-{int(opp['updated_at'].timestamp())}",
        event_metadata={"opportunity_id": opp["id"], "event": event_type})
