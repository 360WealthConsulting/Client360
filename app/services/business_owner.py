"""Business Owner Planning Workspace service (Phase D.12).

A COMPOSITION layer that assembles the firm's tax-centered business-owner planning
picture from existing authoritative domains — it answers "what planning opportunities,
risks, obligations, and follow-up items exist across this client's businesses?" without
the advisor hopping between contact, business, tax, retirement, benefits, insurance,
work, compliance, and annual-review screens.

It READS existing services and never mutates them. Its ONLY persistence is
``business_planning_profiles`` (succession / continuity / buy-sell / valuation /
key-person facts that the audit proved have no authoritative home). Reuse map:

    Ownership graph  -> organization_service.list_person_business_ownership (pure read)
    Business facts    -> organization_profiles (via the ownership read; EIN masked here)
    Tax               -> tax_domain.business_engagements (form/year/status only — no figures)
    Retirement/Benefits -> benefits_domain.list_plans (org-scoped)
    Insurance         -> insurance.business_policies (scope-filtered; purpose unmodeled)
    Advisor Intelligence -> get_client_signals (reused, grouped by durable recommendation_type)
    Advisor Work      -> advisor_work.person_work
    Activity Timeline -> activity_timeline.client_timeline (bounded preview)
    Compliance        -> compliance.reviews.person_reviews (counts only)
    Annual Review     -> annual_review.open_session_for / list_completed_sessions

Dependency direction is strict: existing domains never import this module. Business-owner
STATUS is derived only from active ownership edges — never from occupation/employer/free
text. Every composed section is gated on its OWNING capability (and, for benefits, org
record scope) — the workspace is never a bypass. "Restricted" (lacks permission) and
"missing" (no data) are kept distinct. No new recommendation engine, no fabricated
tax/comp/valuation/contribution figures, no workflow engine.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import business_planning_profiles, engine, people
from app.security import benefits_crypto
from app.security.authorization import organization_in_scope, record_in_scope
from app.services import advisor_work, organization_service, tax_domain
from app.services import insurance as insurance_svc
from app.services.activity_timeline import service as timeline_svc
from app.services.advisor_intelligence import get_client_signals
from app.services.advisor_workspace import get_meeting_brief
from app.services.benefits_domain import list_plans as benefits_list_plans
from app.services.client_summary import get_client_summary
from app.services.compliance import reviews as compliance_reviews
from app.services.timeline import add_timeline_event

PLANNING_STATUS_VOCAB = ("unknown", "not_started", "in_progress", "documented",
                         "review_needed", "complete", "not_applicable")
SOURCE_VOCAB = ("advisor_entered", "client_reported", "document_derived")
_STATUS_FIELDS = ("succession_plan_status", "buy_sell_status",
                  "continuity_plan_status", "key_person_risk_status")
_UNRESOLVED_STATUS = frozenset({"unknown"})
_UNDOCUMENTED_STATUS = frozenset({"unknown", "not_started"})

RECENT_ACTIVITY_LIMIT = 5
_COMPLIANCE_PENDING = frozenset({"pending_submission", "pending_assignment", "pending_review"})
_COMPLIANCE_BLOCKED = frozenset({"blocked_pending_authorized_reviewer"})
_COMPLIANCE_COMPLETED = frozenset({"approved", "approved_with_conditions", "returned", "declined"})


class PlanningValidationError(Exception):
    """A planning-profile update used a value outside the controlled vocabulary."""


class BusinessNotInScopeError(Exception):
    """The business is not validated for this person/principal (blocks enumeration)."""


def _now():
    return datetime.now(UTC)


# --- business-owner status + ownership ---------------------------------------

def _person_businesses(person_id: int) -> list[dict]:
    """Pure read of the person's owned businesses with a per-business active flag."""
    rows = organization_service.list_person_business_ownership(person_id)
    for r in rows:
        # A business is "active" for planning if the entity is active and the ownership
        # edge is active and not ended.
        r["active"] = bool(r.get("entity_active")) and bool(r.get("relationship_active")) \
            and r.get("inactive_date") is None
    return rows


def is_business_owner(principal, person_id: int) -> bool:
    """True only when the person has at least one ACTIVE validated ownership edge to a
    business. Never inferred from occupation/employer/tax-document/free text."""
    if not record_in_scope(principal, "person", person_id):
        return False
    return any(b["active"] for b in _person_businesses(person_id))


def _planning_profiles(business_ids: list[int]) -> dict[int, dict]:
    """Batched load of planning profiles for a set of businesses (no N+1)."""
    if not business_ids:
        return {}
    with engine.connect() as c:
        rows = c.execute(select(business_planning_profiles).where(
            business_planning_profiles.c.business_id.in_(tuple(business_ids)))).mappings().all()
    return {r["business_id"]: dict(r) for r in rows}


def _ein_display(principal, ein_ciphertext) -> dict:
    """Server-side EIN handling: present-flag is derived from the ciphertext (independent of
    view permission), but the value is decrypted only with ``benefits.sensitive.read``."""
    present = bool(ein_ciphertext)
    if not present:
        return {"ein": None, "ein_present": False}
    if principal.can("benefits.sensitive.read"):
        return {"ein": benefits_crypto.decrypt(ein_ciphertext), "ein_present": True}
    return {"ein": None, "ein_present": True, "ein_restricted": True}


def _business_card(principal, b: dict, profile: dict | None) -> dict:
    """One business's presentation facts + planning-status badges + data-quality summary."""
    card = {
        "business_id": b["business_id"],
        "name": b.get("legal_name") or b["business_name"],
        "legal_name": b.get("legal_name"),
        "entity_form": b.get("entity_form"),
        "industry": b.get("industry"),
        "status": b.get("status"),
        "active": b["active"],
        "ownership_percentage": b.get("ownership_percentage"),
        "voting_percentage": b.get("voting_percentage"),
        "ownership_type": b.get("ownership_type"),
        "is_direct": b.get("is_direct"),
        "effective_date": b.get("effective_date"),
        "inactive_date": b.get("inactive_date"),
        "provenance": {"edge_source": b.get("edge_source"),
                       "confidence": b.get("confidence_level"),
                       "evidence_source": b.get("evidence_source")},
        "planning": {f: (profile or {}).get(f, "unknown") for f in _STATUS_FIELDS},
        "planning_source": (profile or {}).get("source_type"),
    }
    card.update(_ein_display(principal, b.get("ein")))
    return card


# --- person-level workspace --------------------------------------------------

def compose_person_workspace(principal, person_id: int) -> dict | None:
    """The person-level Business Owner Planning workspace. Scope-first on the person
    (out of scope -> None -> 404). Bounded: one ownership read, one batched planning-profile
    read, one Advisor Intelligence call, one bounded timeline preview, and person-scoped
    work/compliance reads — no per-business heavy domain queries here (those live on the
    business detail view)."""
    if not record_in_scope(principal, "person", person_id):
        return None
    brief = get_meeting_brief(person_id)
    if brief is None:
        return None

    businesses = _person_businesses(person_id)
    profiles = _planning_profiles([b["business_id"] for b in businesses])
    cards = [_business_card(principal, b, profiles.get(b["business_id"])) for b in businesses]
    active_cards = [c for c in cards if c["active"]]

    summary = get_client_summary(person_id)
    open_work = advisor_work.person_work(principal, person_id, open_only=True) \
        if principal.can("advisor_work.read") else None
    compliance = _compliance_summary(compliance_reviews.person_reviews(principal, person_id)) \
        if principal.can("compliance.review.read") else None
    recommendations = [s for s in get_client_signals(principal, person_id)
                       if s.category == "recommendation"]

    return {
        "person": brief["person"],
        "household_id": brief["household_id"],
        "household_name": brief["household_name"],
        "advisor_name": principal.display_name,
        "review_date": _now().date(),
        "is_business_owner": bool(active_cards),
        "snapshot": {
            "client_status": ("Active" if brief["person"].get("active", True) else "Inactive"),
            "active_business_count": len(active_cards),
            "ownership_relationship_count": len(cards),
            "last_contact_at": summary.get("last_contact_at"),
            "open_work_count": (len(open_work) if open_work is not None else None),
            "pending_compliance_count": (compliance["pending"] if compliance else None),
        },
        "businesses": cards,
        "recommendation_groups": _group_recommendations(recommendations),
        "work": open_work,
        "compliance": compliance,
        "annual_review": _annual_review_summary(principal, person_id),
        "activity": (timeline_svc.client_timeline(principal, person_id, page=1,
                                                  page_size=RECENT_ACTIVITY_LIMIT)
                     if principal.can("timeline.read") else None),
        "missing_information": _person_missing_information(principal, cards),
        # Business development (Phase D.13) — read-only business opportunities for this owner
        # and their businesses; the Opportunity domain remains the pipeline owner. Gated.
        "opportunities": (_business_opportunities(principal, person_id, businesses)
                          if principal.can("opportunity.view") else None),
    }


def _business_opportunities(principal, person_id: int, businesses: list[dict]) -> dict:
    """Read-only opportunity visibility for a business owner: opportunities targeting the
    person plus any targeting their businesses. Never duplicates pipeline ownership."""
    from app.services.opportunity import service as opp_svc
    person_opps = opp_svc.opportunities_for_person(principal, person_id, limit=50)
    business_opps = []
    for b in businesses:
        business_opps.extend(opp_svc.opportunities_for_organization(
            principal, b["business_id"], limit=25))
    return {"person": person_opps, "business": business_opps,
            "open_count": sum(1 for o in person_opps + business_opps if o["status"] == "open")}


def _group_recommendations(recommendations: list) -> list[dict]:
    """Group reused Advisor Intelligence recommendations by their DURABLE structured
    ``recommendation_type`` only. No AI, no keyword matching, no invented business-planning
    categories — recommendations lacking a durable type fall under "Other"."""
    groups: dict[str, list] = {}
    for s in recommendations:
        key = (s.recommendation.recommendation_type if s.recommendation
               and s.recommendation.recommendation_type else "other")
        groups.setdefault(key, []).append(s)
    return [{"key": k, "label": k.replace("_", " ").title(), "items": v}
            for k, v in sorted(groups.items())]


def _annual_review_summary(principal, person_id: int) -> dict | None:
    if not principal.can("annual_review.read"):
        return None
    from app.services import annual_review
    open_session = annual_review.open_session_for(principal, person_id)
    completed = annual_review.list_completed_sessions(principal, person_id, limit=1)
    latest = open_session or (completed[0] if completed else None)
    return {"latest": latest}


def _compliance_summary(review_rows: list[dict]) -> dict:
    pending = blocked = completed = 0
    for r in review_rows:
        status = r.get("status")
        if status in _COMPLIANCE_BLOCKED:
            blocked += 1
        elif status in _COMPLIANCE_COMPLETED:
            completed += 1
        elif status in _COMPLIANCE_PENDING:
            pending += 1
    return {"pending": pending, "blocked": blocked, "completed": completed,
            "total": len(review_rows)}


def _person_missing_information(principal, cards: list[dict]) -> list[dict]:
    """Deterministic, objective data-quality observations from already-loaded data (no AI,
    no extra queries). Uses the EIN present-flag (not view permission) so RESTRICTED data is
    never mislabeled as MISSING."""
    obs = []
    for c in cards:
        label = c["name"]
        if not c.get("ein_present"):
            obs.append({"business_id": c["business_id"], "business": label,
                        "issue": "EIN missing"})
        if not c.get("entity_form"):
            obs.append({"business_id": c["business_id"], "business": label,
                        "issue": "Entity type unknown"})
        if c["active"] and c.get("ownership_percentage") is None:
            obs.append({"business_id": c["business_id"], "business": label,
                        "issue": "This owner's ownership percentage is missing"})
        planning = c["planning"]
        if planning["succession_plan_status"] in _UNDOCUMENTED_STATUS:
            obs.append({"business_id": c["business_id"], "business": label,
                        "issue": "Succession plan not documented"})
        if planning["buy_sell_status"] in _UNRESOLVED_STATUS:
            obs.append({"business_id": c["business_id"], "business": label,
                        "issue": "Buy-sell agreement status unknown"})
        if planning["key_person_risk_status"] in _UNRESOLVED_STATUS:
            obs.append({"business_id": c["business_id"], "business": label,
                        "issue": "Key-person risk not assessed"})
    return obs


# --- business scope + detail -------------------------------------------------

def business_in_scope(principal, person_id: int, business_id: int) -> bool:
    """A business is visible when the PERSON is in scope AND (the business is validated-owned
    by that person, OR the business is independently in the principal's organization scope).
    Never inferred from a name match — blocks URL enumeration of unrelated businesses."""
    if not record_in_scope(principal, "person", person_id):
        return False
    owned = any(b["business_id"] == business_id for b in _person_businesses(person_id))
    return owned or organization_in_scope(principal, business_id)


def compose_business_detail(principal, person_id: int, business_id: int) -> dict | None:
    """Deep single-business planning detail. Scope-first: returns None unless the person is in
    scope and the business is validated (ownership or org scope). Each sensitive section is
    gated on its owning capability (and, for benefits, org record scope) and marked
    ``restricted`` rather than exposed."""
    if not business_in_scope(principal, person_id, business_id):
        return None
    businesses = _person_businesses(person_id)
    match = next((b for b in businesses if b["business_id"] == business_id), None)
    profile = _planning_profiles([business_id]).get(business_id)
    card = _business_card(principal, match, profile) if match else \
        {"business_id": business_id, "name": f"Business {business_id}", "planning":
         {f: (profile or {}).get(f, "unknown") for f in _STATUS_FIELDS}}

    return {
        "person_id": person_id,
        "business": card,
        "ownership": _ownership_structure(principal, business_id),
        "tax": _tax_profile(principal, business_id),
        "owner_compensation": _owner_compensation_placeholder(),
        "retirement": _plan_section(principal, business_id, retirement=True),
        "benefits": _plan_section(principal, business_id, retirement=False),
        "insurance": _insurance_section(principal, business_id),
        "planning_profile": profile,
        "planning_status_vocab": PLANNING_STATUS_VOCAB,
        "missing_information": _business_missing_information(principal, card),
    }


def _resolve_names(person_ids: set[int]) -> dict[int, str]:
    ids = {p for p in person_ids if p}
    if not ids:
        return {}
    with engine.connect() as c:
        return {r["id"]: r["full_name"] for r in c.execute(
            select(people.c.id, people.c.full_name).where(people.c.id.in_(tuple(ids)))).mappings()}


def _ownership_structure(principal, business_id: int) -> dict:
    """Full owner list for the business (via the owning service), with unresolved-total and
    conflict detection. Requires organization.read + org scope; otherwise restricted."""
    if not principal.can("organization.read") or not organization_in_scope(principal, business_id):
        return {"status": "restricted", "owners": None}
    owners = organization_service.list_owners(business_id, principal=principal)
    total = sum(float(o["ownership_percentage"]) for o in owners
                if o.get("ownership_percentage") is not None)
    missing_pct = [o for o in owners if o.get("ownership_percentage") is None]
    # Conflict: same owner entity appears on multiple ownership edges with divergent %.
    seen: dict[int, set] = {}
    for o in owners:
        seen.setdefault(o["owner_entity_id"], set()).add(
            None if o.get("ownership_percentage") is None else float(o["ownership_percentage"]))
    conflicts = [oid for oid, pcts in seen.items() if len([p for p in pcts if p is not None]) > 1]
    return {
        "status": "ok",
        "owners": owners,
        "total_percentage": total,
        "totals_incomplete": bool(missing_pct) or (owners and round(total, 2) != 100.0),
        "missing_percentage_count": len(missing_pct),
        "conflict_owner_ids": conflicts,
    }


def _tax_profile(principal, business_id: int) -> dict:
    """Business tax engagement facts (form/year/status only). Restricted without tax.read.
    Return CONTENT (K-1/W-2/QBI/distributions/S-election) is not tracked by the tax domain."""
    if not principal.can("tax.read"):
        return {"status": "restricted", "engagements": None}
    engagements = tax_domain.business_engagements(business_id)
    return {"status": "ok", "engagements": engagements,
            "untracked": ["K-1 detail", "W-2 wages", "Guaranteed payments", "Distributions",
                          "QBI facts", "S-election", "Accounting method"]}


def _owner_compensation_placeholder() -> dict:
    """Owner compensation is NOT tracked as structured data anywhere in the system (the tax
    domain stores workflow/document metadata only). Every component is honestly 'Not
    available' — never fabricated or inferred."""
    return {"status": "not_available",
            "components": ["W-2 wages", "Guaranteed payments", "Distributions", "Draws",
                           "Employer retirement contributions", "Fringe benefits",
                           "Deferred compensation"]}


def _plan_section(principal, business_id: int, *, retirement: bool) -> dict:
    """Retirement- or health-line benefit plans for the business. Requires benefits.read AND
    org record scope; otherwise restricted. Contribution/limit amounts are not tracked."""
    if not principal.can("benefits.read") or not organization_in_scope(principal, business_id):
        return {"status": "restricted", "plans": None}
    plans = benefits_list_plans(business_id, principal=principal)
    with engine.connect() as c:
        from app.db import benefit_plan_types
        types = {r["id"]: r for r in c.execute(select(benefit_plan_types)).mappings()}
    out = []
    for p in plans:
        t = types.get(p["plan_type_id"], {})
        is_ret = (t.get("line_of_coverage") == "retirement")
        if is_ret != retirement:
            continue
        out.append({**p, "plan_type_code": t.get("code"), "plan_type_name": t.get("name"),
                    "line_of_coverage": t.get("line_of_coverage")})
    return {"status": "ok", "plans": out}


def _insurance_section(principal, business_id: int) -> dict:
    """Business-owned insurance policies (scope-filtered). Requires insurance.read. Policy
    numbers are shown only with insurance.sensitive.read. Policy PURPOSE is not modeled — it
    is surfaced as 'unconfirmed', never guessed from data."""
    if not principal.can("insurance.read"):
        return {"status": "restricted", "policies": None}
    can_sensitive = principal.can("insurance.sensitive.read")
    policies = insurance_svc.business_policies(principal, business_id)
    out = []
    for p in policies:
        out.append({
            "id": p["id"], "carrier_name": p.get("carrier_name"),
            "policy_number": (p.get("policy_number") if can_sensitive else None),
            "policy_number_present": bool(p.get("policy_number")),
            "face_amount": p.get("face_amount"), "premium_amount": p.get("premium_amount"),
            "status": p.get("status"), "purpose": "unconfirmed",
        })
    return {"status": "ok", "policies": out}


def _business_missing_information(principal, card: dict) -> list[dict]:
    obs = []
    if not card.get("ein_present"):
        obs.append({"issue": "EIN missing"})
    if not card.get("entity_form"):
        obs.append({"issue": "Entity type unknown"})
    for field, text in (("succession_plan_status", "Succession plan not documented"),
                        ("buy_sell_status", "Buy-sell agreement status unknown"),
                        ("continuity_plan_status", "Continuity plan status unknown"),
                        ("key_person_risk_status", "Key-person risk not assessed")):
        if card["planning"].get(field) in _UNRESOLVED_STATUS:
            obs.append({"issue": text})
    return obs


# --- planning profile persistence (the one editable, D.12-owned record) ------

def get_planning_profile(business_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(business_planning_profiles).where(
            business_planning_profiles.c.business_id == business_id)).mappings().first()
    return dict(row) if row else None


def upsert_planning_profile(principal, *, person_id: int, business_id: int, fields: dict) -> dict:
    """Create or update the business's planning profile (the only editable D.12 record).
    Validates the controlled status/source vocabulary. Emits a durable timeline event (via
    the shared writer — no second event table) only on creation or a status/valuation change,
    anchored to the owning person whose workspace made the change. Never mutates any
    source-domain record."""
    if not business_in_scope(principal, person_id, business_id):
        raise BusinessNotInScopeError(str(business_id))
    clean: dict = {}
    for f in _STATUS_FIELDS:
        if f in fields:
            v = (fields[f] or "unknown").strip()
            if v not in PLANNING_STATUS_VOCAB:
                raise PlanningValidationError(f"{f}={v!r}")
            clean[f] = v
    if "source_type" in fields:
        st = (fields["source_type"] or "advisor_entered").strip()
        if st not in SOURCE_VOCAB:
            raise PlanningValidationError(f"source_type={st!r}")
        clean["source_type"] = st
    for f in ("notes", "successor_person_id", "emergency_contact_person_id",
              "buy_sell_reviewed_at", "valuation_amount", "valuation_as_of"):
        if f in fields:
            clean[f] = fields[f] or None
    if "notes" in fields:
        clean["notes"] = fields["notes"] or ""

    prior = get_planning_profile(business_id)
    now = _now()
    with engine.begin() as c:
        if prior is None:
            clean.update(business_id=business_id, created_by=principal.user_id,
                         updated_by=principal.user_id, created_at=now, updated_at=now)
            c.execute(business_planning_profiles.insert().values(**clean))
        else:
            clean.update(updated_by=principal.user_id, updated_at=now)
            c.execute(business_planning_profiles.update().where(
                business_planning_profiles.c.business_id == business_id).values(**clean))
    current = get_planning_profile(business_id)

    _emit_planning_event(prior, current, person_id=person_id, business_id=business_id,
                         household_id=None)
    return current


def _emit_planning_event(prior, current, *, person_id, business_id, household_id):
    """Emit a durable timeline event only for meaningful, durable changes (creation, a
    status transition, or a valuation update). Nothing is emitted for no-op saves."""
    changed = []
    if prior is None:
        changed.append("created")
    else:
        for f in _STATUS_FIELDS:
            if prior.get(f) != current.get(f):
                changed.append(f)
        if prior.get("valuation_amount") != current.get("valuation_amount") or \
                prior.get("valuation_as_of") != current.get("valuation_as_of"):
            changed.append("valuation")
    if not changed:
        return
    add_timeline_event(
        source="business_owner", event_type="business_planning_updated",
        title=("Business planning profile created" if prior is None
               else "Business planning updated"),
        summary=", ".join(c.replace("_", " ") for c in changed),
        person_id=person_id, household_id=household_id,
        external_id=f"business-planning-{business_id}-{int(current['updated_at'].timestamp())}",
        event_metadata={"business_id": business_id, "changed": changed})


# --- household integration ---------------------------------------------------

def household_business_ownership(principal, household_id: int) -> dict | None:
    """Bounded household business-ownership summary: businesses owned directly by the
    household entity plus household members with a validated ownership relationship. Scope-
    first on the household. One ownership read + one members read (no per-business queries)."""
    if not record_in_scope(principal, "household", household_id):
        return None
    direct = organization_service.list_household_business_ownership(household_id)
    with engine.connect() as c:
        members = c.execute(select(people.c.id, people.c.full_name).where(
            people.c.household_id == household_id)).mappings().all()
    owning_members = []
    active_business_ids = set()
    for m in members:
        biz = [b for b in _person_businesses(m["id"]) if b["active"]]
        if biz:
            owning_members.append({"person_id": m["id"], "full_name": m["full_name"],
                                   "business_count": len(biz)})
            active_business_ids.update(b["business_id"] for b in biz)
    return {
        "household_owned_businesses": direct,
        "owning_members": owning_members,
        "active_business_count": len(active_business_ids) + len(direct),
    }
