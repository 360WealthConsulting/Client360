"""Deterministic operational advisor-signal tests (Phase D.5B).

Exercises the four approved operational producers activated on the D.5A framework:
client_review_overdue, open_client_exception, overdue_open_task, and
upcoming_client_meeting. Every signal must be factual, evidence-backed,
record-scoped, propose-only, PolicyGate.NONE — and an inaccessible record must
never reach producer logic.
"""
import json
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, insert

from app.db import (
    accounts,
    engine,
    exception_types,
    exceptions,
    households,
    people,
    record_assignments,
    tasks,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.advisor_intelligence import (
    Priority,
    get_client_signals,
    get_dashboard_signals,
    get_household_signals,
    list_registered_signals,
)
from app.services.advisor_workspace import FIRM_TZ

ADVISOR_CAPS = frozenset({"client.read", "client.write", "work.read", "task.read", "exception.read"})
READ_ALL_CAPS = ADVISOR_CAPS | {"record.read_all"}


def _tax_type_id(conn):
    return conn.execute(
        exception_types.select().where(exception_types.c.domain == "tax")
    ).mappings().first()["id"]


def _seed_client(conn, tag, now, *, assigned_to=None, household_id=None,
                 review=True, exception=True, task_overdue=True, meeting="today",
                 exc_severity="high"):
    """Seed one client with the records that drive each signal. Flags toggle which
    records exist so negative cases (current review, resolved exception, future/
    completed task, out-of-window/non-calendar meeting) can be built."""
    today = now.date()
    pid = conn.execute(people.insert().values(
        full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
        normalized_email=f"{tag}@e.test", household_id=household_id, active=True,
    ).returning(people.c.id)).scalar_one()

    # Account — never-reviewed (review=True) -> overdue High; recent -> current.
    conn.execute(insert(accounts).values(
        person_id=pid, custodian="Schwab", account_number=f"ACCT-{tag}",
        account_name=f"Acct {tag}", status="open",
        last_review_date=None if review else today))

    if exception:
        conn.execute(insert(exceptions).values(
            exception_type_id=_tax_type_id(conn), domain="tax", category="client",
            severity=exc_severity, status="open", title=f"Exc {tag}", source="system",
            opened_at=now, escalation_level=0, notification_count=0, person_id=pid))

    # Task — overdue (past due, open) vs future (due next month, open).
    due = today - timedelta(days=5) if task_overdue else today + timedelta(days=30)
    conn.execute(insert(tasks).values(
        person_id=pid, title=f"Task {tag}", status="open", priority="normal", due_date=due))

    if meeting == "today":
        conn.execute(insert(timeline_events).values(
            person_id=pid, source="microsoft", event_type="calendar_event",
            title=f"Review meeting {tag}",
            event_time=datetime.combine(today, time(10, 0), tzinfo=FIRM_TZ)))
    elif meeting == "far":  # outside the window (5 days out)
        conn.execute(insert(timeline_events).values(
            person_id=pid, source="microsoft", event_type="calendar_event",
            title=f"Far meeting {tag}",
            event_time=datetime.combine(today + timedelta(days=5), time(10, 0), tzinfo=FIRM_TZ)))
    elif meeting == "noncal":  # a non-calendar timeline event today
        conn.execute(insert(timeline_events).values(
            person_id=pid, source="staff", event_type="note_added",
            title=f"Note {tag}",
            event_time=datetime.combine(today, time(10, 0), tzinfo=FIRM_TZ)))

    if assigned_to is not None:
        conn.execute(insert(record_assignments).values(
            user_id=assigned_to, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=today))
    return pid


def _setup(**a_flags):
    tag = uuid.uuid4().hex[:8]
    # Fixed firm-tz "now" on a weekday so the meeting window is deterministic.
    now = datetime(2026, 7, 16, 9, 0, tzinfo=FIRM_TZ)  # Thursday
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"adv-{tag}@e.test", normalized_email=f"adv-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = conn.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        a = _seed_client(conn, f"A{tag}", now, assigned_to=uid, household_id=hh, **a_flags)
        b = _seed_client(conn, f"B{tag}", now, assigned_to=None)  # unassigned / inaccessible
        conn.execute(insert(record_assignments).values(
            user_id=uid, entity_type="household", entity_id=hh,
            assignment_type="owner", effective_date=now.date()))
    return {"uid": uid, "a": a, "b": b, "hh": hh, "now": now,
            "principal": Principal(uid, "a@e.com", "Adv", ADVISOR_CAPS),
            "read_all": Principal(uid, "a@e.com", "Adv", READ_ALL_CAPS)}


def _teardown(ids):
    with engine.begin() as conn:
        for pid in (ids["a"], ids["b"]):
            conn.execute(delete(exceptions).where(exceptions.c.person_id == pid))
            conn.execute(delete(tasks).where(tasks.c.person_id == pid))
            conn.execute(delete(timeline_events).where(timeline_events.c.person_id == pid))
            conn.execute(delete(accounts).where(accounts.c.person_id == pid))
        conn.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        conn.execute(delete(people).where(people.c.id.in_((ids["a"], ids["b"]))))
        conn.execute(delete(households).where(households.c.id == ids["hh"]))


def _types(signals):
    return {s.id.split(":", 1)[0] for s in signals}


def _by_type(signals, t):
    return [s for s in signals if s.id.startswith(t + ":")]


# --- framework & generation --------------------------------------------------

def test_registry_contains_only_approved_operational_producers():
    keys = {r.key for r in list_registered_signals()}
    assert keys == {"client_review_overdue", "open_client_exception",
                    "overdue_open_task", "upcoming_client_meeting"}


def test_full_signal_set_is_deterministic_ids_order_and_no_dupes():
    ids = _setup()
    try:
        first = get_client_signals(ids["principal"], ids["a"], now=ids["now"])
        second = get_client_signals(ids["principal"], ids["a"], now=ids["now"])
        # All four operational signals for a client that has every trigger.
        assert _types(first) == {"client_review_overdue", "open_client_exception",
                                 "overdue_open_task", "upcoming_client_meeting"}
        # Deterministic: identical ids and order across calls.
        assert [s.id for s in first] == [s.id for s in second]
        # No duplicate ids.
        assert len({s.id for s in first}) == len(first)
        # Ordered by (priority desc, id) — ranks are non-increasing.
        ranks = [s.priority.rank for s in first]
        assert ranks == sorted(ranks, reverse=True)
    finally:
        _teardown(ids)


def test_serialization_and_explainability_are_populated():
    ids = _setup()
    try:
        for s in get_client_signals(ids["principal"], ids["a"], now=ids["now"]):
            d = s.to_dict()
            json.loads(json.dumps(d))  # JSON-safe
            assert d["policy_gate"] == "none"
            assert d["source_record"] is not None
            assert d["evidence"]  # non-empty
            assert d["explainability"]["why"]
            assert d["explainability"]["source_service"]
            assert d["explainability"]["confidence"] == 1.0  # deterministic, not probabilistic
            assert d["route"]
    finally:
        _teardown(ids)


# --- review signal -----------------------------------------------------------

def test_overdue_review_emits_high_and_current_review_does_not():
    ids = _setup(review=True, exception=False, task_overdue=False, meeting="none")
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "client_review_overdue")
        assert len(sig) == 1
        assert sig[0].priority is Priority.HIGH  # never reviewed = materially overdue
        assert sig[0].source_service == "portfolio"
    finally:
        _teardown(ids)

    ids = _setup(review=False, exception=False, task_overdue=False, meeting="none")
    try:
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "client_review_overdue") == []
    finally:
        _teardown(ids)


# --- exception signal --------------------------------------------------------

def test_open_exception_emits_and_preserves_severity():
    ids = _setup(review=False, exception=True, task_overdue=False, meeting="none", exc_severity="blocker")
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "open_client_exception")
        assert len(sig) == 1
        assert sig[0].priority is Priority.CRITICAL  # source's most-severe label (blocker)
        assert "severity=blocker" in sig[0].evidence
        assert sig[0].source_service == "exception_engine"
    finally:
        _teardown(ids)


def test_resolved_exception_does_not_emit():
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="none")
    try:
        with engine.begin() as conn:
            conn.execute(insert(exceptions).values(
                exception_type_id=_tax_type_id(conn), domain="tax", category="client",
                severity="high", status="resolved", title="Done", source="system",
                opened_at=ids["now"], escalation_level=0, notification_count=0, person_id=ids["a"]))
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "open_client_exception") == []
    finally:
        _teardown(ids)


# --- task signal -------------------------------------------------------------

def test_overdue_task_emits_and_future_and_completed_do_not():
    ids = _setup(review=False, exception=False, task_overdue=True, meeting="none")
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "overdue_open_task")
        assert len(sig) == 1
        assert sig[0].source_service == "tasks"
        assert sig[0].priority in (Priority.MEDIUM, Priority.HIGH)
    finally:
        _teardown(ids)

    # Future open task -> no overdue signal.
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="none")
    try:
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "overdue_open_task") == []
    finally:
        _teardown(ids)

    # Completed task (even if past due) -> no signal.
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="none")
    try:
        with engine.begin() as conn:
            conn.execute(insert(tasks).values(
                person_id=ids["a"], title="Done", status="complete", priority="normal",
                due_date=date(2026, 1, 1)))
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "overdue_open_task") == []
    finally:
        _teardown(ids)


# --- meeting signal ----------------------------------------------------------

def test_meeting_signal_window_and_type_and_person():
    # Qualifying calendar event today -> emits.
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="today")
    try:
        sig = _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "upcoming_client_meeting")
        assert len(sig) == 1
        assert sig[0].route.startswith(f"/workspace/meetings/{ids['a']}?event=")
    finally:
        _teardown(ids)

    # Non-calendar timeline event today -> no signal.
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="noncal")
    try:
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "upcoming_client_meeting") == []
    finally:
        _teardown(ids)

    # Calendar event outside the window (5 days out) -> no signal.
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="far")
    try:
        assert _by_type(get_client_signals(ids["principal"], ids["a"], now=ids["now"]), "upcoming_client_meeting") == []
    finally:
        _teardown(ids)


def test_meeting_for_another_person_does_not_emit():
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="today")
    try:
        # B has a calendar event today, but is not accessible and not this client.
        client_sigs = get_client_signals(ids["principal"], ids["a"], now=ids["now"])
        for s in client_sigs:
            assert f"person_id={ids['b']}" not in s.evidence
    finally:
        _teardown(ids)


# --- authorization -----------------------------------------------------------

def test_inaccessible_person_never_reaches_producer_logic():
    ids = _setup()
    try:
        # Scoped advisor cannot see B -> scope gate returns () before any producer.
        assert get_client_signals(ids["principal"], ids["b"], now=ids["now"]) == ()
        # Same B records DO produce for a read_all principal -> the empty result
        # above is the authorization gate, not absence of data.
        assert get_client_signals(ids["read_all"], ids["b"], now=ids["now"]) != ()
    finally:
        _teardown(ids)


def test_inaccessible_household_never_reaches_producer_logic():
    ids = _setup()
    try:
        with engine.begin() as conn:
            other_hh = conn.execute(households.insert().values(
                name="other").returning(households.c.id)).scalar_one()
        try:
            assert get_household_signals(ids["principal"], other_hh, now=ids["now"]) == ()
        finally:
            with engine.begin() as conn:
                conn.execute(delete(households).where(households.c.id == other_hh))
    finally:
        _teardown(ids)


def test_dashboard_is_book_scoped_and_excludes_unassigned():
    ids = _setup()
    try:
        sigs = get_dashboard_signals(ids["principal"], now=ids["now"])
        assert sigs  # A is in the book and has triggers
        for s in sigs:
            # No evidence or route may reference the unassigned client B.
            assert f"person_id={ids['b']}" not in s.evidence
            assert f"/people/{ids['b']}" != (s.route or "")
            assert f"/workspace/meetings/{ids['b']}" not in (s.route or "")
        # A's review signal is present.
        assert any(s.id.startswith("client_review_overdue:") for s in sigs)
    finally:
        _teardown(ids)


def test_read_all_principal_receives_only_valid_signals_for_a_record():
    ids = _setup()
    try:
        sigs = get_client_signals(ids["read_all"], ids["b"], now=ids["now"])
        assert _types(sigs) == {"client_review_overdue", "open_client_exception",
                                "overdue_open_task", "upcoming_client_meeting"}
        for s in sigs:
            assert s.policy_gate.value == "none"
    finally:
        _teardown(ids)


# --- content safety ----------------------------------------------------------

def test_no_recommendation_scoring_or_policy_language():
    ids = _setup()
    banned = ("should invest", "should replace", "should convert", "opportunity",
              "recommend", "best action", "suitable", "appropriate strategy",
              "roth", "1035", "rollover", "coverage gap", "risk score",
              "probability", "ai-generated", "%")
    try:
        for s in get_client_signals(ids["principal"], ids["a"], now=ids["now"]):
            blob = " ".join((s.title, s.summary, s.explainability.why,
                             *s.evidence)).lower()
            for term in banned:
                assert term not in blob, f"banned term {term!r} in {s.id}"
            assert s.policy_gate.value == "none"
    finally:
        _teardown(ids)


# --- UI ----------------------------------------------------------------------

def test_dashboard_panel_renders_populated_signals_and_links():
    from starlette.requests import Request

    from app.routes.workspace import workspace_dashboard
    ids = _setup()
    try:
        req = Request({"type": "http", "method": "GET", "path": "/workspace",
                       "headers": [], "query_string": b""})
        body = workspace_dashboard(req, principal=ids["principal"]).body.decode()
        assert "Advisor Intelligence" in body
        # A populated table linking to existing protected routes for A.
        assert f"/people/{ids['a']}" in body
        assert "Exception remains open." in body or "review is overdue" in body
        # No action controls in the panel.
        for control in ("Approve", "Reject", "Dismiss", "Snooze", "Create task"):
            assert control not in body
    finally:
        _teardown(ids)


def test_dashboard_panel_empty_state_when_no_signals():
    from starlette.requests import Request

    from app.routes.workspace import workspace_dashboard
    # A client with no triggering records at all.
    ids = _setup(review=False, exception=False, task_overdue=False, meeting="none")
    try:
        req = Request({"type": "http", "method": "GET", "path": "/workspace",
                       "headers": [], "query_string": b""})
        body = workspace_dashboard(req, principal=ids["principal"]).body.decode()
        assert "No advisor signals" in body
    finally:
        _teardown(ids)
