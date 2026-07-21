"""Annual Review Workspace service (Phase D.11).

A *composition layer* that assembles existing Client360 capabilities into one
advisor-facing workspace for conducting annual client reviews. It answers
"what do I need to review with this client today?" by READING existing services:

    Client360 / Meeting Workspace  -> advisor_workspace.get_meeting_brief
    Advisor Intelligence           -> advisor_intelligence.get_client_signals   (reused, never regenerated)
    Advisor Work (D.9)             -> advisor_work.person_work
    Activity Timeline (D.10)       -> activity_timeline.service.client_timeline
    Compliance (D.6-D.8)           -> compliance.reviews.person_reviews
    Portfolio                      -> the snapshot already inside get_meeting_brief (no 2nd fetch)

It consumes those services; it does not replace them, duplicate their business
logic, execute recommendations, automate compliance, or add planning logic. The
dependency direction is strict: existing domains never import Annual Review.

The ONLY thing it persists is a review *session* (``annual_review_sessions``) — an
advisor-activity record holding notes and a presentation-only checklist. A session
records that a review happened; it never changes a source-domain record. Sessions
are mutable (edited in place) with an explicit status set — no workflow engine.

Authorization: the routes gate on ``annual_review.read/create/update`` and this
service enforces person record scope (scope-first). Each composed section is
included only when the principal holds the OWNING capability (advisor_work.read /
timeline.read / compliance.review.read) — Annual Review is never a bypass around
them.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.db import annual_review_sessions, engine, people, users
from app.security.authorization import record_in_scope
from app.services import advisor_work
from app.services.activity_timeline import service as timeline_svc
from app.services.advisor_intelligence import get_client_signals
from app.services.advisor_workspace import get_meeting_brief
from app.services.calendar import get_person_calendar_events
from app.services.client_summary import get_client_summary
from app.services.compliance import reviews as compliance_reviews

# --- review checklist (presentation only) ------------------------------------
# A fixed, display-only checklist. Completing an item records advisor activity in
# the session's checklist_state; it does NOT modify any planning/domain logic.
CHECKLIST_ITEMS: tuple[tuple[str, str], ...] = (
    ("client_information", "Client information"),
    ("beneficiaries", "Beneficiaries"),
    ("risk_tolerance", "Risk tolerance"),
    ("investment_allocation", "Investment allocation"),
    ("retirement_goals", "Retirement goals"),
    ("insurance", "Insurance"),
    ("tax_planning", "Tax planning"),
    ("estate_planning", "Estate planning"),
    ("cash_flow", "Cash flow"),
    ("business_planning", "Business planning"),
    ("pending_advisor_work", "Pending advisor work"),
    ("compliance_items", "Compliance items"),
    ("meeting_follow_up", "Meeting follow-up"),
)
CHECKLIST_KEYS = frozenset(k for k, _ in CHECKLIST_ITEMS)

# --- session lifecycle (no workflow engine — an explicit status set) ---------
STATUSES = ("draft", "in_progress", "completed", "archived")
EDITABLE_STATUSES = frozenset({"draft", "in_progress"})
_OPEN_STATUSES = frozenset({"draft", "in_progress"})

RECENT_ACTIVITY_LIMIT = 5

# Compliance-summary status buckets (read-only classification; recreates no logic).
_COMPLIANCE_PENDING = frozenset(
    {"pending_submission", "pending_assignment", "pending_review"})
_COMPLIANCE_BLOCKED = frozenset({"blocked_pending_authorized_reviewer"})
_COMPLIANCE_COMPLETED = frozenset(
    {"approved", "approved_with_conditions", "returned", "declined"})


class SessionNotFoundError(Exception):
    """The requested review session does not exist or is out of scope."""


class InvalidSessionTransitionError(Exception):
    """The requested session status change is not allowed from the current status."""


def _now() -> datetime:
    return datetime.now(UTC)


# --- composition -------------------------------------------------------------

def compose_workspace(principal, person_id: int, *, session: dict | None = None) -> dict | None:
    """Assemble the read-first workspace for one client. Scope-first: returns
    ``None`` if the client is out of the principal's record scope (route -> 404).

    Sections gated by their owning capability are omitted (set to ``None``) when the
    principal lacks it — never silently exposed. No source domain is recomputed or
    mutated; Advisor Intelligence is read once (not regenerated), the portfolio is
    reused from the meeting-brief snapshot (no second fetch), and no query is issued
    per-recommendation (no N+1).
    """
    if not record_in_scope(principal, "person", person_id):
        return None
    brief = get_meeting_brief(person_id)
    if brief is None:
        return None

    # 2. Advisor Intelligence — reuse existing recommendations (never regenerate).
    recommendations = [s for s in get_client_signals(principal, person_id)
                       if s.category == "recommendation"]

    sections: dict = {
        # 1. Client Snapshot (read-only) + 6. Portfolio Overview (reuse snapshot).
        "person": brief["person"],
        "household_id": brief["household_id"],
        "household_name": brief["household_name"],
        "snapshot": brief["snapshot"],
        "review_date": _now().date(),
        # 1. Client Snapshot extras — reuse existing reads (last/next meeting, status).
        "client": _client_meta(person_id),
        # 7. Meeting Preparation — reuse the meeting workspace brief.
        "meeting": {
            "meeting_event": brief["meeting_event"],
            "open_tasks": brief["open_tasks"],
            "notes": brief["notes"],
            "reviews": brief["reviews"],
        },
        "recommendations": recommendations,
    }

    # 3. Outstanding Advisor Work — reuse D.9 (only if advisor_work.read).
    sections["work"] = (
        _with_owner_names(advisor_work.person_work(principal, person_id, open_only=True))
        if principal.can("advisor_work.read") else None)

    # 4. Recent Activity — reuse D.10 timeline (only if timeline.read).
    sections["activity"] = (
        timeline_svc.client_timeline(principal, person_id, page=1,
                                     page_size=RECENT_ACTIVITY_LIMIT)
        if principal.can("timeline.read") else None)

    # 5. Compliance Summary — reuse D.6-D.8 (only if compliance.review.read).
    sections["compliance"] = (
        _compliance_summary(compliance_reviews.person_reviews(principal, person_id))
        if principal.can("compliance.review.read") else None)

    # 8. Review Checklist (presentation only) bound to the session state.
    sections["checklist"] = build_checklist(session)
    sections["session"] = session

    # Business development (Phase D.13) — read-only opportunity visibility for an existing
    # client (cross-sell / pending / recent wins & losses). Reuses the Opportunity domain via
    # an additive scoped read; the Opportunity domain remains the pipeline owner. Gated on
    # opportunity.view, so it is omitted (None) without the capability.
    sections["business_development"] = (_business_development(principal, person_id)
                                        if principal.can("opportunity.view") else None)
    return sections


def _business_development(principal, person_id: int) -> dict:
    """Read-only opportunity summary for an existing client — never duplicates pipeline
    ownership; the Opportunity domain stays authoritative."""
    from app.services.opportunity import service as opp_svc
    rows = opp_svc.opportunities_for_person(principal, person_id, limit=50)
    return {
        "open": [o for o in rows if o["status"] == "open"],
        "recent_wins": [o for o in rows if o["status"] == "won"][:5],
        "recent_losses": [o for o in rows if o["status"] == "lost"][:5],
        "total": len(rows),
    }


def _client_meta(person_id: int) -> dict:
    """Client Snapshot facts assembled from existing reads (no new logic): last
    contact/meeting (client summary), next scheduled meeting (calendar), and status."""
    summary = get_client_summary(person_id)
    upcoming = get_person_calendar_events(person_id, upcoming_only=True, limit=1)
    with engine.connect() as conn:
        active = conn.scalar(select(people.c.active).where(people.c.id == person_id))
    return {
        "active": active,
        "last_contact_at": summary.get("last_contact_at"),
        "last_event_title": summary.get("last_event_title"),
        "next_meeting": dict(upcoming[0]) if upcoming else None,
    }


def _with_owner_names(work_rows: list[dict]) -> list[dict]:
    """Attach owner display names to advisor-work rows in ONE users query (no N+1)."""
    owner_ids = {r["owner_principal_id"] for r in work_rows if r.get("owner_principal_id")}
    names: dict[int, str] = {}
    if owner_ids:
        with engine.connect() as conn:
            names = {r["id"]: r["display_name"] for r in conn.execute(
                select(users.c.id, users.c.display_name)
                .where(users.c.id.in_(tuple(owner_ids)))).mappings()}
    for r in work_rows:
        r["owner_name"] = names.get(r.get("owner_principal_id"))
    return work_rows


def _compliance_summary(review_rows: list[dict]) -> dict:
    """Read-only classification of the client's reviews into pending / blocked /
    completed counts plus current reviewer assignments. Recreates no compliance logic."""
    pending = blocked = completed = 0
    assignments = []
    for r in review_rows:
        status = r.get("status")
        if status in _COMPLIANCE_BLOCKED:
            blocked += 1
        elif status in _COMPLIANCE_COMPLETED:
            completed += 1
        elif status in _COMPLIANCE_PENDING:
            pending += 1
        if r.get("assigned_reviewer_name") or r.get("assigned_reviewer_role"):
            assignments.append({
                "review_id": r["id"],
                "reviewer_name": r.get("assigned_reviewer_name"),
                "reviewer_role": r.get("assigned_reviewer_role"),
                "status": status,
            })
    return {
        "pending": pending,
        "blocked": blocked,
        "completed": completed,
        "total": len(review_rows),
        "assignments": assignments,
        "reviews": review_rows,
    }


def build_checklist(session: dict | None) -> list[dict]:
    """The fixed presentation-only checklist merged with the session's stored state."""
    state = (session or {}).get("checklist_state") or {}
    return [{"key": key, "label": label, "checked": bool(state.get(key))}
            for key, label in CHECKLIST_ITEMS]


# --- review sessions ---------------------------------------------------------

def _row(conn, session_id: int) -> dict | None:
    row = conn.execute(select(annual_review_sessions)
                       .where(annual_review_sessions.c.id == session_id)).mappings().first()
    return dict(row) if row else None


def get_session(principal, session_id: int) -> dict | None:
    """A single review session, scope-first on its client. ``None`` if missing/out of scope."""
    with engine.connect() as conn:
        session = _row(conn, session_id)
    if session is None:
        return None
    if not record_in_scope(principal, "person", session["person_id"]):
        return None
    return session


def open_session_for(principal, person_id: int) -> dict | None:
    """The current OPEN (draft/in_progress) session for this client, if any. Scope-first."""
    if not record_in_scope(principal, "person", person_id):
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(annual_review_sessions).where(and_(
                annual_review_sessions.c.person_id == person_id,
                annual_review_sessions.c.status.in_(tuple(_OPEN_STATUSES))))
            .order_by(annual_review_sessions.c.id.desc())).mappings().first()
    return dict(row) if row else None


def list_completed_sessions(principal, person_id: int, *, limit: int = 10) -> list[dict]:
    """Recently completed/archived sessions for this client (read-only history). Scope-first."""
    if not record_in_scope(principal, "person", person_id):
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(annual_review_sessions).where(and_(
                annual_review_sessions.c.person_id == person_id,
                annual_review_sessions.c.status.in_(("completed", "archived"))))
            .order_by(annual_review_sessions.c.completed_at.desc().nullslast(),
                      annual_review_sessions.c.id.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def start_session(principal, person_id: int, *, advisor_id: int | None) -> dict:
    """Begin (or resume) a review. Idempotent: if an OPEN session already exists for
    this advisor + client it is returned unchanged (the partial-unique DB guard also
    enforces one open session per advisor per client). A new session starts
    ``in_progress`` with ``started_at`` recorded.
    """
    if not record_in_scope(principal, "person", person_id, write=True):
        raise SessionNotFoundError("client is out of write scope")
    with engine.begin() as conn:
        existing = conn.execute(
            select(annual_review_sessions).where(and_(
                annual_review_sessions.c.person_id == person_id,
                annual_review_sessions.c.advisor_id == advisor_id,
                annual_review_sessions.c.status.in_(tuple(_OPEN_STATUSES))))
            .order_by(annual_review_sessions.c.id.desc())).mappings().first()
        if existing is not None:
            return dict(existing)
        household_id = conn.execute(
            select(people.c.household_id).where(people.c.id == person_id)).scalar()
        now = _now()
        row = conn.execute(annual_review_sessions.insert().values(
            person_id=person_id, household_id=household_id, advisor_id=advisor_id,
            started_at=now, status="in_progress", notes="", checklist_state={},
            created_at=now, updated_at=now).returning(annual_review_sessions)).mappings().one()
    return dict(row)


def save_session(principal, session_id: int, *, notes: str | None = None,
                 checklist_state: dict | None = None) -> dict:
    """Persist session notes and/or checklist state. Editable only while the session
    is draft/in_progress. Records advisor activity only — changes no source domain.
    Unknown checklist keys are ignored (the checklist is a fixed, presentation-only set).
    """
    with engine.begin() as conn:
        session = _row(conn, session_id)
        if session is None:
            raise SessionNotFoundError(str(session_id))
        if not record_in_scope(principal, "person", session["person_id"], write=True):
            raise SessionNotFoundError(str(session_id))
        if session["status"] not in EDITABLE_STATUSES:
            raise InvalidSessionTransitionError(
                f"cannot edit a {session['status']} session")
        values: dict = {"updated_at": _now()}
        if notes is not None:
            values["notes"] = notes
        if checklist_state is not None:
            values["checklist_state"] = {k: bool(v) for k, v in checklist_state.items()
                                         if k in CHECKLIST_KEYS}
        conn.execute(annual_review_sessions.update()
                     .where(annual_review_sessions.c.id == session_id).values(**values))
        return _row(conn, session_id)


def set_status(principal, session_id: int, *, new_status: str) -> dict:
    """Transition a session's status. Explicit allowed-source map — no workflow engine.

    draft/in_progress -> completed (records completed_at); any open/completed -> archived.
    """
    transitions = {
        "in_progress": _OPEN_STATUSES,
        "completed": _OPEN_STATUSES,
        "archived": frozenset({"draft", "in_progress", "completed"}),
    }
    if new_status not in transitions:
        raise InvalidSessionTransitionError(f"unknown status {new_status!r}")
    with engine.begin() as conn:
        session = _row(conn, session_id)
        if session is None:
            raise SessionNotFoundError(str(session_id))
        if not record_in_scope(principal, "person", session["person_id"], write=True):
            raise SessionNotFoundError(str(session_id))
        if session["status"] not in transitions[new_status]:
            raise InvalidSessionTransitionError(
                f"cannot move to {new_status} from {session['status']}")
        now = _now()
        values: dict = {"status": new_status, "updated_at": now}
        if new_status == "completed":
            values["completed_at"] = now
        conn.execute(annual_review_sessions.update()
                     .where(annual_review_sessions.c.id == session_id).values(**values))
        return _row(conn, session_id)
