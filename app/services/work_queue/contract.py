"""The normalized, read-only UnifiedWorkItem contract (Phase D.39).

A UnifiedWorkItem carries ONLY presentation + routing metadata (references, not duplicated business
payloads). It never becomes the source of truth for the underlying item. The original source status is
preserved verbatim in ``status``; ``status_group`` is a display/filter-only normalization. SLA/priority
are normalized into presentation states only — the authoritative SLA/priority logic stays in the owning
domain (and in ``work_intelligence``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

# Priority normalization (display + sort). Source priorities map onto this ordered vocabulary.
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "medium": 2, "low": 3}
_PRIORITY_CANON = {"urgent": "urgent", "high": "high", "normal": "normal", "medium": "normal",
                   "low": "low", "blocker": "urgent"}

# Status normalization — display/filter ONLY; the source status is preserved on the item.
_STATUS_GROUP = {
    # open / new
    "new": "open", "open": "open", "pending": "open", "planned": "open", "scheduled": "open",
    "pending_submission": "open", "pending_assignment": "open", "requested": "open", "draft": "open",
    "received": "open", "reopened": "open",
    # in progress
    "assigned": "in_progress", "in_progress": "in_progress", "active": "in_progress",
    "acknowledged": "in_progress", "pending_review": "in_progress", "confirmed": "in_progress",
    "underwriting": "in_progress", "fact_find": "in_progress", "proposed": "in_progress",
    # waiting / blocked
    "waiting": "waiting", "on_hold": "waiting", "paused": "waiting", "deferred": "waiting",
    "blocked": "blocked", "blocked_pending_authorized_reviewer": "blocked", "escalated": "blocked",
    "review": "review", "ready_for_review": "review", "manager_review": "review",
    # done / closed
    "completed": "done", "complete": "done", "closed": "done", "cancelled": "done", "archived": "done",
    "resolved": "done", "approved": "done", "approved_with_conditions": "done", "declined": "done",
    "returned": "done", "issued": "done", "satisfied": "done", "waived": "done", "no_show": "done",
}

SLA_STATES = ("on_track", "due_soon", "overdue", "breached", "escalated", "unknown")
STATUS_GROUPS = ("open", "in_progress", "waiting", "blocked", "review", "done")


def normalize_priority(value) -> str:
    return _PRIORITY_CANON.get(str(value or "").lower(), "normal")


def normalize_status(value) -> str:
    return _STATUS_GROUP.get(str(value or "").lower(), "open")


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=UTC)
    return None


@dataclass
class UnifiedWorkItem:
    work_item_key: str
    source_domain: str
    source_type: str
    source_id: int | str
    title: str
    status: str                       # preserved source status
    status_group: str                 # display/filter normalization
    priority: str
    deep_link: str
    capability: str
    allowed_actions: tuple = ()
    summary: str | None = None
    due_at: datetime | None = None
    overdue: bool = False
    age_days: int | None = None
    sla_state: str = "unknown"
    assignee_user_id: int | None = None
    assignee_name: str | None = None
    team: str | None = None
    person_id: int | None = None
    person_name: str | None = None
    household_id: int | None = None
    household_name: str | None = None
    workflow_instance_id: int | None = None
    exception_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source_reference: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("due_at", "created_at", "updated_at"):
            if isinstance(d.get(k), (datetime, date)):
                d[k] = d[k].isoformat()
        d["allowed_actions"] = list(self.allowed_actions)
        return d


def make_item(*, source_domain, source_type, source_id, title, status, priority, deep_link,
              capability, allowed_actions=(), summary=None, due_at=None, sla_due_at=None,
              escalated=False, assignee_user_id=None, assignee_name=None, team=None, person_id=None,
              person_name=None, household_id=None, household_name=None, workflow_instance_id=None,
              exception_id=None, created_at=None, updated_at=None, source_reference=None,
              now=None) -> UnifiedWorkItem:
    """Build a UnifiedWorkItem, computing the derived presentation fields (key, overdue, age,
    sla_state, status_group). ``sla_due_at``/``escalated`` feed the SLA presentation state only."""
    now = now or datetime.now(UTC)
    due = _aware(due_at)
    sla_due = _aware(sla_due_at)
    created = _aware(created_at)
    is_done = normalize_status(status) == "done"
    overdue = bool(due and not is_done and due < now)
    sla_state = _sla_state(due=due, sla_due=sla_due, escalated=escalated, is_done=is_done, now=now)
    age_days = (now - created).days if created else None
    key = f"{source_domain}:{source_type}:{source_id}"
    return UnifiedWorkItem(
        work_item_key=key, source_domain=source_domain, source_type=source_type, source_id=source_id,
        title=title or f"{source_type} {source_id}", status=status,
        status_group=normalize_status(status), priority=normalize_priority(priority),
        deep_link=deep_link, capability=capability, allowed_actions=tuple(allowed_actions),
        summary=summary, due_at=due, overdue=overdue, age_days=age_days, sla_state=sla_state,
        assignee_user_id=assignee_user_id, assignee_name=assignee_name, team=team,
        person_id=person_id, person_name=person_name, household_id=household_id,
        household_name=household_name, workflow_instance_id=workflow_instance_id,
        exception_id=exception_id, created_at=created, updated_at=_aware(updated_at),
        source_reference=source_reference or {})


def _sla_state(*, due, sla_due, escalated, is_done, now):
    """Normalize authoritative deadlines into a presentation SLA state. Unknown stays unknown — no
    deadline is invented when the source has none."""
    if is_done:
        return "on_track"
    if escalated:
        return "escalated"
    deadline = sla_due or due
    if deadline is None:
        return "unknown"
    remaining = (deadline - now).total_seconds() / 3600.0
    if remaining < 0:
        return "breached" if sla_due is not None else "overdue"
    if remaining <= 24:
        return "due_soon"
    return "on_track"
