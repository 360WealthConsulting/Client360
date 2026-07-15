"""Release 0.9.10 / Sprint 5.5 — Work Management integration & queues (Phase 5) tests."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import (assignment_events, engine, exceptions, record_assignments, team_memberships, teams)
from app.security.models import Principal
from app.services import exception_engine as ee
from app.services import exception_work as ew
from app.services import work_management as wm

CAPS = frozenset({"exception.read", "exception.write", "work.read", "capacity.read", "record.read_all"})


def _case():
    from tests.test_tax_intake import _case as intake_case
    user_id, person_id, household_id, portal, result = intake_case()
    return user_id, person_id, household_id, result["return_id"]


def _admin(u):
    return Principal(u, "a@e.com", "A", CAPS)


def _raise(r, p, h, code="FILING_REJECTED", dedupe=None, u=1):
    return ee.raise_exception(code=code, actor_user_id=u, tax_engagement_return_id=r,
                              person_id=p, household_id=h, dedupe_key=dedupe or f"{code}-{r}")


# --- assignment via record_assignments ---------------------------------------

def test_assign_reassign_remove_and_history():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)
    aid = ew.assign_exception(ex["id"], principal=admin, assignment_role="primary", user_id=u, actor_user_id=u)
    assert ee._fetch(ex["id"])["owner_user_id"] == u
    with engine.connect() as c:  # real record_assignment created
        ra = c.execute(select(record_assignments).where(record_assignments.c.id == aid)).mappings().one()
    assert ra["entity_type"] == "exception" and ra["entity_id"] == ex["id"] and ra["assignment_type"] == "primary"
    # secondary + supervisor
    ew.assign_exception(ex["id"], principal=admin, assignment_role="secondary", user_id=u, actor_user_id=u)
    ew.assign_exception(ex["id"], principal=admin, assignment_role="supervisor", user_id=u, actor_user_id=u)
    with engine.connect() as c:
        team_id = c.scalar(select(teams.c.id).where(teams.c.code == "operations"))
    new_id = ew.reassign_exception(aid, principal=admin, team_id=team_id, actor_user_id=u, reason="rebalance")
    assert ee._fetch(ex["id"])["owner_team_id"] == team_id  # primary reassigned to a team
    ew.remove_exception_assignment(new_id, principal=admin, actor_user_id=u, reason="done")
    assert ee._fetch(ex["id"])["owner_user_id"] is None  # owner cache cleared on removal of primary
    with engine.connect() as c:  # assignment history recorded (existing model)
        events = [e[0] for e in c.execute(select(assignment_events.c.event_type)
                  .where(assignment_events.c.entity_type == "exception", assignment_events.c.entity_id == ex["id"])
                  .order_by(assignment_events.c.id))]
    assert "assignment_created" in events and "assignment_changed" in events and "assignment_removed" in events


def test_team_only_ownership_and_team_visibility():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)
    with engine.begin() as c:
        team_id = c.scalar(select(teams.c.id).where(teams.c.code == "operations"))
        member = c.execute(select(team_memberships.c.user_id).where(team_memberships.c.team_id == team_id)).scalars().first()
    ew.assign_exception(ex["id"], principal=admin, assignment_role="primary", team_id=team_id, actor_user_id=u)
    row = ee._fetch(ex["id"])
    assert row["owner_team_id"] == team_id and row["owner_user_id"] is None
    if member:  # a team member sees the team-owned exception in their work
        team_principal = Principal(member, "t@e.com", "T", frozenset({"exception.read", "work.read"}))
        ids = {(i["entity_type"], i["entity_id"]) for i in wm.work_items(team_principal)}
        assert ("exception", ex["id"]) in ids


# --- My Work / Team Work visibility + authorization --------------------------

def test_my_work_visibility_and_authorization():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)
    ew.assign_exception(ex["id"], principal=admin, assignment_role="primary", user_id=u, actor_user_id=u)
    owner = Principal(u, "o@e.com", "O", frozenset({"exception.read", "work.read"}))  # assigned, no read_all
    outsider = Principal(9_700_001, "x@e.com", "X", frozenset({"exception.read", "work.read"}))
    assert ("exception", ex["id"]) in {(i["entity_type"], i["entity_id"]) for i in wm.work_items(owner)}
    assert ("exception", ex["id"]) not in {(i["entity_type"], i["entity_id"]) for i in wm.work_items(outsider)}


def test_authorization_filters_before_queue_evaluation():
    u, p, h, r = _case()
    _raise(r, p, h, code="COMPLIANCE_SOD_VIOLATION", u=u)
    outsider = Principal(9_700_002, "x@e.com", "X", frozenset({"exception.read", "work.read"}))
    detail = wm.queue_detail(outsider, "compliance_exceptions")
    assert detail["items"] == []  # criteria matches nothing because authorization removed the rows first


# --- queues ------------------------------------------------------------------

def test_queue_parity_and_critical_and_compliance_filtering():
    u, p, h, r = _case()
    admin = _admin(u)
    _raise(r, p, h, code="FILING_REJECTED", dedupe=f"blk-{r}", u=u)          # blocker
    _raise(r, p, h, code="FILING_DEADLINE_AT_RISK", dedupe=f"hi-{r}", u=u)   # high
    _raise(r, p, h, code="CLIENT_UNRESPONSIVE", dedupe=f"med-{r}", u=u)      # medium
    _raise(r, p, h, code="COMPLIANCE_SOD_VIOLATION", dedupe=f"cmp-{r}", u=u) # blocker + compliance
    d = wm.dashboard(admin)
    counts = {q["code"]: q["count"] for q in d["queues"]}
    for code in ("tax_exceptions", "tax_exceptions_critical", "compliance_exceptions"):
        assert counts[code] == len(wm.queue_detail(admin, code)["items"])  # count/detail parity
    crit = [i for i in wm.queue_detail(admin, "tax_exceptions_critical")["items"] if i["entity_type"] == "exception"]
    assert crit and all(i["severity"] in ("blocker", "high") for i in crit)  # medium excluded
    comp = [i for i in wm.queue_detail(admin, "compliance_exceptions")["items"] if i["entity_type"] == "exception"]
    assert comp and all(i["category"] == "compliance" for i in comp)


# --- work-item behavior ------------------------------------------------------

def test_unassigned_exceptions_appear_with_flag():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)  # not assigned
    item = next(i for i in wm.work_items(admin) if i["entity_type"] == "exception" and i["entity_id"] == ex["id"])
    assert item["assigned"] is False


def test_resolved_and_cancelled_excluded_from_active_work():
    u, p, h, r = _case()
    admin = _admin(u)
    resolved = _raise(r, p, h, dedupe=f"res-{r}", u=u)
    ee.acknowledge(resolved["id"], principal=None); ee.begin_work(resolved["id"], principal=None); ee.resolve(resolved["id"], "x", principal=None)
    cancelled = _raise(r, p, h, code="CLIENT_UNRESPONSIVE", dedupe=f"can-{r}", u=u)
    ee.cancel(cancelled["id"], principal=None)
    ids = {i["entity_id"] for i in wm.work_items(admin) if i["entity_type"] == "exception"}
    assert resolved["id"] not in ids and cancelled["id"] not in ids


def test_capacity_and_bottlenecks_include_exceptions():
    u, p, h, r = _case()
    admin = _admin(u)
    base = wm.dashboard(admin)["capacity"]["committed_minutes"]
    ex = _raise(r, p, h, u=u)
    ee.acknowledge(ex["id"], principal=None); ee.begin_work(ex["id"], principal=None); ee.place_waiting(ex["id"], principal=None)
    d = wm.dashboard(admin)
    assert d["capacity"]["committed_minutes"] > base  # exception adds estimated minutes
    assert any(b["reason"] == "exception_waiting" for b in d["bottlenecks"])  # waiting exception is a bottleneck


def test_daily_agenda_ordering_and_deterministic_scoring():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)  # blocker → urgent priority
    with engine.begin() as c:  # breached → high sla score
        c.execute(exceptions.update().where(exceptions.c.id == ex["id"])
                  .values(sla_due_at=datetime.now(timezone.utc) - timedelta(hours=2)))
    from app.services.work_intelligence import priority_score
    items = wm.work_items(admin)
    item = next(i for i in items if i["entity_type"] == "exception" and i["entity_id"] == ex["id"])
    s1, s2 = priority_score(item), priority_score(item)
    assert s1 == s2  # deterministic
    low = {"priority": "low", "status": "open", "due_date": None, "sla_due_at": None}
    assert priority_score(item) > priority_score(low)  # blocker+breached outranks low
    agenda = wm.dashboard(admin)["items"]
    scores = [i["priority_score"] for i in agenda]
    assert scores == sorted(scores, reverse=True)  # agenda ordered by score desc


def test_no_duplicate_work_items():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)
    ew.assign_exception(ex["id"], principal=admin, assignment_role="primary", user_id=u, actor_user_id=u)
    matches = [i for i in wm.work_items(admin) if i["entity_type"] == "exception" and i["entity_id"] == ex["id"]]
    assert len(matches) == 1  # single work item despite an assignment record


def test_no_mutation_of_exception_source_records():
    u, p, h, r = _case()
    admin = _admin(u)
    ex = _raise(r, p, h, u=u)
    before = ee._fetch(ex["id"])
    wm.dashboard(admin); wm.work_items(admin); wm.queue_detail(admin, "tax_exceptions")
    after = ee._fetch(ex["id"])
    assert (before["status"], before["severity"], before["escalation_level"], before["updated_at"]) == \
           (after["status"], after["severity"], after["escalation_level"], after["updated_at"])
