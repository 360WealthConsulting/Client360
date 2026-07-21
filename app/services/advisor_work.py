"""Advisor Work Management service (Phase D.9).

Turns Advisor Intelligence *recommendations* into advisor-facing operational work,
while preserving their deterministic origin. It CONSUMES Advisor Intelligence (to
snapshot a recommendation); it never modifies it, never executes a recommendation, and
is never imported by it. Completing a work item records operational activity only — it
does NOT suppress, resolve, or alter the recommendation, its evidence, or its id.

Separate, namespaced layer: it does not touch the existing Work Management system
(``/work``, ``work.read``/``work.write``, tasks/exceptions/workflow steps). Persistence
is ``advisor_work_items`` + the append-only ``advisor_work_events`` history. Creation is
explicit and idempotent (at most one OPEN item per recommendation/person/rule). Small
inline helpers keep this service decoupled from the compliance package.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select

from app.db import advisor_work_events, advisor_work_items, engine, people
from app.security.authorization import accessible_person_ids, record_in_scope
from app.services.advisor_intelligence import get_client_signals

OPEN_STATUSES = frozenset({"new", "assigned", "in_progress", "waiting"})

# Explicit allowed source states per status change (no generic workflow engine).
_TRANSITIONS = {
    "in_progress": frozenset({"new", "assigned", "waiting"}),
    "waiting": frozenset({"assigned", "in_progress"}),
    "completed": frozenset({"new", "assigned", "in_progress", "waiting"}),
    "cancelled": frozenset({"new", "assigned", "in_progress", "waiting"}),
    "archived": frozenset({"completed", "cancelled"}),
}
_ASSIGN_FROM = OPEN_STATUSES


class AdvisorWorkError(RuntimeError):
    """Base class for advisor-work domain errors."""


class IneligibleRecommendationError(AdvisorWorkError):
    """The target is not a governed recommendation in the caller's scope."""


class StaleWorkError(AdvisorWorkError):
    """The item changed since the caller loaded it; the action was rejected."""


class InvalidTransitionError(AdvisorWorkError):
    """The action is not allowed from the item's current status."""


def _now() -> datetime:
    return datetime.now(UTC)


def _load_for_update(conn, item_id, expected_status):
    row = conn.execute(
        select(advisor_work_items).where(advisor_work_items.c.id == item_id).with_for_update()
    ).mappings().first()
    if row is None:
        raise AdvisorWorkError("work item not found")
    if expected_status is not None and row["status"] != expected_status:
        raise StaleWorkError(
            f"work item is now {row['status']!r}, not {expected_status!r}; reload and retry")
    return row


def _append_event(conn, item_id, *, event_type, prior_status, new_status, actor, note):
    conn.execute(advisor_work_events.insert().values(
        advisor_work_item_id=item_id, event_type=event_type, prior_status=prior_status,
        new_status=new_status, actor_principal_id=actor, occurred_at=_now(), note=note))


# --- eligibility + creation --------------------------------------------------

def eligible_recommendation(principal, person_id: int, recommendation_id: str):
    """Return the governed recommendation Signal for this person + id, or ``None``.
    Enforces person record-scope first, so an inaccessible client can never be used."""
    if not record_in_scope(principal, "person", person_id):
        return None
    for sig in get_client_signals(principal, person_id):
        if sig.id == recommendation_id and sig.category == "recommendation":
            return sig
    return None


def _household(person_id: int) -> int | None:
    with engine.connect() as conn:
        return conn.scalar(select(people.c.household_id).where(people.c.id == person_id))


def create_from_recommendation(principal, *, person_id: int, recommendation_id: str,
                               actor_user_id: int, due_date=None):
    """Create (idempotently) an advisor work item from a governed recommendation. If an
    OPEN item already exists for the same (recommendation, person, governing rule) it is
    returned unchanged — no duplicate. Snapshots the recommendation (title, description,
    evidence, governing rule, version, priority, policy gate)."""
    sig = eligible_recommendation(principal, person_id, recommendation_id)
    if sig is None:
        raise IneligibleRecommendationError("Not a governed recommendation in your scope.")
    rec = sig.recommendation
    with engine.begin() as conn:
        existing = conn.execute(
            select(advisor_work_items).where(
                advisor_work_items.c.recommendation_id == sig.id,
                advisor_work_items.c.person_id == person_id,
                advisor_work_items.c.governing_rule == rec.governing_rule,
                advisor_work_items.c.status.in_(tuple(OPEN_STATUSES)),
            )
        ).mappings().first()
        if existing is not None:
            return dict(existing)
        now = _now()
        row = conn.execute(advisor_work_items.insert().values(
            recommendation_id=sig.id, recommendation_type=rec.recommendation_type,
            governing_rule=rec.governing_rule, rule_version=rec.rule_version,
            policy_gate=sig.policy_gate.value, priority=sig.priority.value,
            recommendation_snapshot=sig.to_dict(), person_id=person_id,
            household_id=_household(person_id), created_by=actor_user_id,
            status="new", due_date=due_date, created_at=now, updated_at=now,
        ).returning(advisor_work_items)).mappings().one()
        _append_event(conn, row["id"], event_type="created", prior_status=None,
                      new_status="new", actor=actor_user_id, note=None)
    return dict(row)


def open_work_index(principal, person_id: int) -> dict:
    """Map ``recommendation_id -> {id, status}`` for this person's OPEN work items, so the
    Advisor Workspace can show "Work Exists →" vs "Create Work". Scope-first."""
    if not record_in_scope(principal, "person", person_id):
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            select(advisor_work_items.c.id, advisor_work_items.c.recommendation_id,
                   advisor_work_items.c.status).where(
                advisor_work_items.c.person_id == person_id,
                advisor_work_items.c.status.in_(tuple(OPEN_STATUSES)))
        ).mappings().all()
    return {r["recommendation_id"]: {"id": r["id"], "status": r["status"]} for r in rows}


# --- lifecycle ---------------------------------------------------------------

def assign(principal, item_id: int, *, owner_principal_id: int | None, expected_status: str,
           actor_user_id: int):
    """Set the owner. A ``new`` item becomes ``assigned``. No automated routing."""
    with engine.begin() as conn:
        item = _load_for_update(conn, item_id, expected_status)
        if item["status"] not in _ASSIGN_FROM:
            raise InvalidTransitionError(f"cannot assign from status {item['status']}")
        new_status = "assigned" if item["status"] == "new" else item["status"]
        conn.execute(advisor_work_items.update().where(advisor_work_items.c.id == item_id).values(
            owner_principal_id=owner_principal_id, status=new_status, updated_at=_now()))
        _append_event(conn, item_id, event_type="assigned", prior_status=item["status"],
                      new_status=new_status, actor=actor_user_id,
                      note=f"owner={owner_principal_id}")
    return {"status": new_status}


def update_status(principal, item_id: int, *, new_status: str, expected_status: str,
                  actor_user_id: int, note: str | None = None):
    """Explicit status transition (in_progress/waiting/cancelled/archived)."""
    if new_status not in _TRANSITIONS:
        raise InvalidTransitionError(f"unknown status {new_status!r}")
    with engine.begin() as conn:
        item = _load_for_update(conn, item_id, expected_status)
        if item["status"] not in _TRANSITIONS[new_status]:
            raise InvalidTransitionError(f"cannot move to {new_status} from {item['status']}")
        values = {"status": new_status, "updated_at": _now()}
        if new_status == "archived":
            values["archived_at"] = _now()
        conn.execute(advisor_work_items.update().where(advisor_work_items.c.id == item_id).values(**values))
        _append_event(conn, item_id, event_type=new_status, prior_status=item["status"],
                      new_status=new_status, actor=actor_user_id, note=note)
    return {"status": new_status}


def complete(principal, item_id: int, *, completion_notes: str | None, expected_status: str,
             actor_user_id: int):
    """Record completion. This records operational activity ONLY — it never suppresses,
    resolves, or alters the underlying recommendation, its evidence, or its id."""
    with engine.begin() as conn:
        item = _load_for_update(conn, item_id, expected_status)
        if item["status"] not in _TRANSITIONS["completed"]:
            raise InvalidTransitionError(f"cannot complete from status {item['status']}")
        now = _now()
        conn.execute(advisor_work_items.update().where(advisor_work_items.c.id == item_id).values(
            status="completed", completed_at=now, completed_by=actor_user_id,
            completion_notes=completion_notes, updated_at=now))
        _append_event(conn, item_id, event_type="completed", prior_status=item["status"],
                      new_status="completed", actor=actor_user_id, note=completion_notes)
    return {"status": "completed"}


# --- reads -------------------------------------------------------------------

def _scope_clause(principal, conn):
    ids = accessible_person_ids(conn, principal)
    if ids is None:
        return None
    if not ids:
        return "empty"
    return or_(
        advisor_work_items.c.person_id.in_(tuple(ids)),
        and_(advisor_work_items.c.person_id.is_(None),
             advisor_work_items.c.household_id.is_not(None),
             advisor_work_items.c.household_id.in_(
                 select(people.c.household_id).where(people.c.id.in_(tuple(ids))))))


def list_work(principal, *, search=None, status=None, priority=None, owner=None,
              recommendation_type=None, governing_rule=None, policy_gate=None,
              sort="created_at", descending=True, page=1, page_size=25):
    """Record-scoped, filtered, sorted, paginated advisor-work queue."""
    sort_cols = {
        "created_at": advisor_work_items.c.created_at,
        "status": advisor_work_items.c.status,
        "priority": advisor_work_items.c.priority,
        "recommendation_type": advisor_work_items.c.recommendation_type,
        "governing_rule": advisor_work_items.c.governing_rule,
        "due_date": advisor_work_items.c.due_date,
    }
    col = sort_cols.get(sort, advisor_work_items.c.created_at)
    with engine.connect() as conn:
        scope = _scope_clause(principal, conn)
        if scope == "empty":
            return {"rows": [], "total": 0, "page": 1, "page_size": page_size, "pages": 0}
        conds = []
        if scope is not None:
            conds.append(scope)
        for column, value in (
            (advisor_work_items.c.status, status),
            (advisor_work_items.c.priority, priority),
            (advisor_work_items.c.owner_principal_id, owner),
            (advisor_work_items.c.recommendation_type, recommendation_type),
            (advisor_work_items.c.governing_rule, governing_rule),
            (advisor_work_items.c.policy_gate, policy_gate),
        ):
            if value:
                conds.append(column == value)
        if search:
            like = f"%{search.strip().lower()}%"
            conds.append(or_(
                func.lower(advisor_work_items.c.recommendation_id).like(like),
                func.lower(advisor_work_items.c.governing_rule).like(like),
                func.lower(advisor_work_items.c.recommendation_type).like(like)))
        where = and_(*conds) if conds else None
        total = conn.scalar(
            select(func.count()).select_from(advisor_work_items).where(where)
            if where is not None else select(func.count()).select_from(advisor_work_items))
        page = max(1, page)
        page_size = max(1, min(page_size, 200))
        stmt = select(advisor_work_items)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.order_by(col.desc().nullslast() if descending else col.asc().nullslast(),
                             advisor_work_items.c.id.desc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        rows = [dict(r) for r in conn.execute(stmt).mappings()]
    pages = (total + page_size - 1) // page_size if total else 0
    return {"rows": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def get_work(principal, item_id: int):
    """A single work item (record-scoped) with its append-only event history, or ``None``."""
    with engine.connect() as conn:
        item = conn.execute(
            select(advisor_work_items).where(advisor_work_items.c.id == item_id)
        ).mappings().first()
        if item is None:
            return None
        item = dict(item)
        pid, hid = item["person_id"], item["household_id"]
        if pid is not None and not record_in_scope(principal, "person", pid):
            return None
        if pid is None and hid is not None and not record_in_scope(principal, "household", hid):
            return None
        item["events"] = [dict(e) for e in conn.execute(
            select(advisor_work_events)
            .where(advisor_work_events.c.advisor_work_item_id == item_id)
            .order_by(advisor_work_events.c.occurred_at.asc(), advisor_work_events.c.id.asc())
        ).mappings()]
    return item
