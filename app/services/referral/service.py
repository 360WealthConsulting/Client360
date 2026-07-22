"""Referral Source service (Phase D.14) — CRUD, lifecycle, computed metrics, book scope.

Referral sources are advisor/firm assets. Visibility is book-scoped: a source is visible to its
primary advisor, its supporting advisors, its creator, or ``record.read_all``. Metrics are
COMPUTED from attributed opportunities (never stored). Lifecycle (active/inactive) appends to the
``referral_source_events`` log; when the source is an existing client (``person_id``), an approved
add/deactivate event is additionally published to that client's Activity Timeline.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select

from app.db import (
    engine,
    opportunities,
    people,
    referral_source_advisors,
    referral_source_events,
    referral_sources,
    relationship_entities,
)
from app.services.timeline import add_timeline_event

_SOURCE_TYPES = frozenset({
    "individual", "organization", "existing_client", "cpa", "attorney", "bank",
    "financial_advisor", "insurance_agent", "estate_planner", "mortgage_broker", "coi",
    "employee", "marketing_vendor", "website", "event", "advertising", "other"})
_PLAIN_FIELDS = frozenset({"name", "source_type", "relationship_type", "email", "phone",
                           "notes", "primary_advisor_id"})


class ReferralError(Exception):
    """Validation error."""


class ReferralNotFound(Exception):
    """Referral source does not exist or is out of scope."""


def _now():
    return datetime.now(UTC)


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def _scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    return or_(
        referral_sources.c.primary_advisor_id == principal.user_id,
        referral_sources.c.created_by == principal.user_id,
        referral_sources.c.id.in_(select(referral_source_advisors.c.referral_source_id)
                                  .where(referral_source_advisors.c.user_id == principal.user_id)))


def _visible(principal, row, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if principal.user_id in (row.get("primary_advisor_id"), row.get("created_by")):
        return True
    return c.scalar(select(referral_source_advisors.c.id).where(
        referral_source_advisors.c.referral_source_id == row["id"],
        referral_source_advisors.c.user_id == principal.user_id)) is not None


# --- CRUD --------------------------------------------------------------------

def create_referral_source(principal, *, name, source_type, actor_user_id, relationship_type=None,
                           person_id=None, organization_id=None, email=None, phone=None,
                           primary_advisor_id=None, introduced_by_user_id=None, notes=None) -> dict:
    if not (name or "").strip():
        raise ReferralError("name is required")
    if source_type not in _SOURCE_TYPES:
        raise ReferralError(f"unknown source_type {source_type!r}")
    with engine.begin() as c:
        if person_id is not None and c.scalar(select(people.c.id).where(people.c.id == person_id)) is None:
            raise ReferralError("linked person does not exist")
        if organization_id is not None and c.scalar(
                select(relationship_entities.c.id).where(relationship_entities.c.id == organization_id)) is None:
            raise ReferralError("linked organization does not exist")
        now = _now()
        row = c.execute(referral_sources.insert().values(
            name=name.strip(), source_type=source_type, status="active",
            relationship_type=relationship_type, person_id=person_id, organization_id=organization_id,
            email=email, phone=phone, primary_advisor_id=primary_advisor_id or actor_user_id,
            introduced_by_user_id=introduced_by_user_id, notes=notes or "",
            created_by=actor_user_id, updated_by=actor_user_id, created_at=now,
            updated_at=now).returning(referral_sources)).mappings().one()
        src = dict(row)
        _event(c, src["id"], event_type="added", actor=actor_user_id)
        _publish_client(src, event_type="added", title=f"Referral source added — {src['name']}")
    return src


def get_referral_source(principal, referral_source_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(referral_sources).where(
            referral_sources.c.id == referral_source_id)).mappings().first()
        if row is None or not _visible(principal, dict(row), c):
            return None
        src = dict(row)
        src["supporting_advisors"] = [dict(r) for r in c.execute(
            select(referral_source_advisors).where(
                referral_source_advisors.c.referral_source_id == referral_source_id)).mappings()]
        src["events"] = [dict(r) for r in c.execute(
            select(referral_source_events).where(
                referral_source_events.c.referral_source_id == referral_source_id)
            .order_by(referral_source_events.c.occurred_at.desc())).mappings()]
    src["metrics"] = referral_metrics(principal, referral_source_id)
    if principal.can("documents.view"):
        from app.services.document_platform.relationships import documents_for_entity
        src["documents"] = documents_for_entity(principal, "referral_source", referral_source_id, limit=25)
    else:
        src["documents"] = None
    return src


def list_referral_sources(principal, *, status=None, source_type=None, search=None,
                          page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(referral_sources.c.status == status)
        if source_type:
            conds.append(referral_sources.c.source_type == source_type)
        if search:
            conds.append(referral_sources.c.name.ilike(f"%{search.strip()}%"))
        where = and_(*conds) if conds else None
        total = c.scalar(select(func.count()).select_from(referral_sources).where(where)
                         if where is not None else select(func.count()).select_from(referral_sources))
        stmt = select(referral_sources)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(referral_sources.c.name).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def update_referral_source(principal, referral_source_id: int, *, actor_user_id, fields: dict) -> dict:
    values = {k: v for k, v in fields.items() if k in _PLAIN_FIELDS}
    with engine.begin() as c:
        _load_scoped(c, principal, referral_source_id)
        if values.get("source_type") and values["source_type"] not in _SOURCE_TYPES:
            raise ReferralError("unknown source_type")
        if values:
            values["updated_by"] = actor_user_id
            values["updated_at"] = _now()
            c.execute(referral_sources.update().where(
                referral_sources.c.id == referral_source_id).values(**values))
        return dict(c.execute(select(referral_sources).where(
            referral_sources.c.id == referral_source_id)).mappings().one())


def set_status(principal, referral_source_id: int, *, new_status: str, actor_user_id, note=None) -> dict:
    if new_status not in ("active", "inactive"):
        raise ReferralError("status must be active or inactive")
    with engine.begin() as c:
        _load_scoped(c, principal, referral_source_id)
        c.execute(referral_sources.update().where(referral_sources.c.id == referral_source_id)
                  .values(status=new_status, updated_by=actor_user_id, updated_at=_now()))
        event_type = "deactivated" if new_status == "inactive" else "reactivated"
        _event(c, referral_source_id, event_type=event_type, actor=actor_user_id, note=note)
        updated = dict(c.execute(select(referral_sources).where(
            referral_sources.c.id == referral_source_id)).mappings().one())
        _publish_client(updated, event_type=event_type,
                        title=f"Referral source {event_type} — {updated['name']}")
    return updated


def delete_referral_source(principal, referral_source_id: int) -> None:
    with engine.begin() as c:
        _load_scoped(c, principal, referral_source_id)
        # opportunities.referral_source_id is ON DELETE SET NULL — attribution detached, not lost.
        c.execute(referral_sources.delete().where(referral_sources.c.id == referral_source_id))


def add_supporting_advisor(principal, referral_source_id: int, user_id: int, *, actor_user_id) -> dict:
    with engine.begin() as c:
        _load_scoped(c, principal, referral_source_id)
        existing = c.scalar(select(referral_source_advisors.c.id).where(
            referral_source_advisors.c.referral_source_id == referral_source_id,
            referral_source_advisors.c.user_id == user_id))
        if existing:
            return {"id": existing}
        row = c.execute(referral_source_advisors.insert().values(
            referral_source_id=referral_source_id, user_id=user_id, role="supporting",
            created_at=_now()).returning(referral_source_advisors)).mappings().one()
    return dict(row)


# --- computed metrics --------------------------------------------------------

def referral_metrics(principal, referral_source_id: int) -> dict:
    """Metrics computed from opportunities attributed to this referral source (scoped to what the
    principal can see). Never stored — always live."""
    from app.services.opportunity.service import _scope_clause as opp_scope
    with engine.connect() as c:
        scope = opp_scope(principal, c)
        conds = [opportunities.c.referral_source_id == referral_source_id]
        if scope is not None:
            conds.append(scope)
        rows = [dict(r) for r in c.execute(select(opportunities).where(and_(*conds))).mappings()]
    won = [o for o in rows if o["status"] == "won"]
    lost = [o for o in rows if o["status"] == "lost"]
    open_ = [o for o in rows if o["status"] == "open"]
    closed = len(won) + len(lost)
    close_days = [(o["closed_at"].date() - o["created_at"].date()).days
                  for o in won if o["closed_at"]]
    return {
        "total_referrals": len(rows),
        "won_referrals": len(won),
        "lost_referrals": len(lost),
        "open_referrals": len(open_),
        "conversion_rate": (round(len(won) / closed, 4) if closed else None),
        "estimated_revenue": float(sum(_num(o["expected_revenue"]) for o in open_)),
        "actual_revenue": float(sum(_num(o["expected_revenue"]) for o in won)),
        "lifetime_value": float(sum(_num(o["expected_revenue"]) for o in won)),
        "average_close_time_days": (round(sum(close_days) / len(close_days), 1) if close_days else None),
        "last_referral_at": max((o["created_at"] for o in rows), default=None),
    }


# --- internals ---------------------------------------------------------------

def _load_scoped(c, principal, referral_source_id: int) -> dict:
    row = c.execute(select(referral_sources).where(
        referral_sources.c.id == referral_source_id)).mappings().first()
    if row is None or not _visible(principal, dict(row), c):
        raise ReferralNotFound(str(referral_source_id))
    return dict(row)


def _event(c, referral_source_id, *, event_type, actor=None, note=None):
    c.execute(referral_source_events.insert().values(
        referral_source_id=referral_source_id, event_type=event_type, actor_user_id=actor,
        note=note, occurred_at=_now()))


def _publish_client(src: dict, *, event_type: str, title: str) -> None:
    """When the referral source IS an existing client, an approved add/deactivate event is
    published to that client's Activity Timeline (client-anchored). Firm-only sources have no
    client anchor and are recorded in referral_source_events only."""
    if src.get("person_id") is None:
        return
    add_timeline_event(
        source="referral", event_type=f"referral_{event_type}", title=title,
        person_id=src["person_id"],
        external_id=f"referral-{src['id']}-{event_type}-{int(src['updated_at'].timestamp())}",
        event_metadata={"referral_source_id": src["id"], "event": event_type})
