"""Client Workspace — Timeline / Tasks / Notes / Audit tabs (PR 2E) coverage.

These tabs reuse the authoritative task/notes/timeline/audit services (no parallel systems), scoped to
the client's ownership (ADR-073). Covers scope, rendering, task + note mutations (audited, scope-
enforced), audit rendering, capability gating, empty/fail-soft states. Temp/test rows only.
"""
import uuid

import pytest
from sqlalchemy import delete, select
from starlette.requests import Request

from app.db import engine, people
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.registry import SECTION_KEYS

_TAG = "CWTABS"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "timeline.read", "audit.read"})
_ACTOR = {"uid": None}


@pytest.fixture
def person():
    from app.db import metadata, users
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"actor{tag}@e.test", normalized_email=f"actor{tag}@e.test",
            display_name=f"Actor {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            first_name="Tab", last_name=f"{_TAG}{tag}", full_name=f"Tab {_TAG}{tag}",
            active=True).returning(people.c.id)).scalar_one()
    _ACTOR["uid"] = uid
    yield pid
    # tasks/notes reference the person; audit_events are append-only and reference the actor user,
    # so best-effort cleanup only (leave audit + the user it points to).
    person_notes = metadata.tables["person_notes"]
    tasks = metadata.tables["tasks"]

    def _try(stmt):
        try:
            with engine.begin() as c:
                c.execute(stmt)
        except Exception:
            pass
    _try(delete(tasks).where(tasks.c.person_id == pid))
    _try(delete(person_notes).where(person_notes.c.person_id == pid))
    _try(delete(people).where(people.c.id == pid))


def _principal(caps=_CAPS):
    return Principal(_ACTOR["uid"] or 1, "adv@e.test", "Advisor", caps)


def _req(pid):
    r = Request({"type": "http", "method": "POST", "path": f"/client/{pid}", "headers": [],
                 "query_string": b"", "state": {}})
    r.state.principal = _principal()
    r.state.request_id = f"t-{uuid.uuid4()}"
    return r


# --- registry: the beta tabs exist -------------------------------------------

def test_beta_tabs_registered():
    for key in ("timeline", "tasks", "notes", "audit"):
        assert key in SECTION_KEYS


# --- tasks: render + create + complete (mutations audited, scoped) -----------

def test_tasks_section_lists_client_tasks(person):
    from app.services.tasks import create_task
    create_task(person, title=f"Do thing {_TAG}", actor_user_id=_ACTOR["uid"], request_id="t")
    sec = get_workspace(_principal(), person_id=person)["sections"]["tasks"]
    assert any(t["title"].startswith("Do thing") for t in sec["tasks"])
    assert sec["can_write"] is True and sec["primary_person_id"] == person


def test_create_task_route_creates_and_audits(person):
    from app.db import audit_events
    from app.routes.client360 import create_client_task
    resp = create_client_task(_req(person), person, title=f"Route task {_TAG}", priority="high",
                              due_date="2025-04-15", principal=_principal())
    assert resp.status_code == 303
    with engine.connect() as c:
        n = c.scalar(select(audit_events.c.id).where(audit_events.c.action == "task.created",
                     audit_events.c.metadata["person_id"].astext == str(person)).limit(1))
    assert n is not None


def test_complete_task_route(person):
    from app.routes.client360 import complete_client_task
    from app.services.tasks import create_task, tasks_with_assignee
    tid = create_task(person, title=f"Finish {_TAG}", actor_user_id=_ACTOR["uid"], request_id="t")
    complete_client_task(_req(person), person, tid, principal=_principal())
    statuses = {t["id"]: t["status"] for t in tasks_with_assignee(person)}
    assert statuses[tid] == "complete"


def test_task_mutations_require_capability():
    from fastapi import HTTPException

    from app.security.dependencies import require_capability
    gate = require_capability("client.write")
    with pytest.raises(HTTPException) as exc:
        gate(principal=Principal(0, "x@e.test", "X", frozenset({"client.read"})))
    assert exc.value.status_code == 403


def test_task_route_out_of_scope_is_404(person):
    from app.routes.client360 import create_client_task
    limited = Principal(0, "x@e.test", "X", frozenset({"client.write", "client.read"}))  # no read_all
    resp = create_client_task(_req(person), 999_000_001, title="x", principal=limited)
    assert resp.status_code == 404


# --- notes: render + add (internal, audited) --------------------------------

def test_add_note_route_creates_and_audits(person):
    from app.db import audit_events
    from app.routes.client360 import add_client_note
    resp = add_client_note(_req(person), person, body=f"Internal note {_TAG}", note_type="note",
                           principal=_principal())
    assert resp.status_code == 303
    sec = get_workspace(_principal(), person_id=person)["sections"]["notes"]
    assert any(f"Internal note {_TAG}" in n["body"] for n in sec["notes"])
    assert sec["internal_only"] is True
    with engine.connect() as c:
        assert c.scalar(select(audit_events.c.id).where(
            audit_events.c.action == "note.created",
            audit_events.c.entity_id == str(person)).limit(1)) is not None


# --- audit: scoped read, capability-gated -----------------------------------

def test_audit_section_scoped_to_client(person):
    from app.services.tasks import create_task
    create_task(person, title=f"Audit seed {_TAG}", actor_user_id=_ACTOR["uid"], request_id="t")
    sec = get_workspace(_principal(), person_id=person)["sections"]["audit"]
    assert any(e["action"] == "task.created" for e in sec["events"])


def test_audit_events_show_human_labels_not_raw_ids(person):
    # The Audit tab must resolve actor + entity to names, never expose raw internal ids ("#1" / "task #4").
    from app.db import engine, users
    from app.services.tasks import create_task
    title = f"Labelled task {_TAG}"
    create_task(person, title=title, actor_user_id=_ACTOR["uid"], request_id="t")
    with engine.connect() as c:
        actor_name = c.execute(select(users.c.display_name).where(users.c.id == _ACTOR["uid"])).scalar_one()
        person_name = c.execute(select(people.c.full_name).where(people.c.id == person)).scalar_one()
    events = get_workspace(_principal(), person_id=person)["sections"]["audit"]["events"]
    task_ev = next(e for e in events if e["action"] == "task.created")
    assert task_ev["actor_name"] == actor_name                 # not "#<id>"
    assert task_ev["entity_label"] == title                    # task title, not "task #<id>"
    note_ev = next((e for e in events if e["action"] == "note.created"), None)
    for e in events:                                            # nothing raw leaks through
        assert not str(e["entity_label"]).startswith("#")
        assert "#" not in str(e["actor_name"])
    # a person-entity event resolves to the person's name
    person_ev = next((e for e in events if e["entity_type"] == "person"), note_ev)
    if person_ev is not None:
        assert person_ev["entity_label"] == person_name


def test_audit_tab_hidden_without_capability(person):
    ws = get_workspace(Principal(0, "x@e.test", "X", frozenset({"client.read", "record.read_all"})),
                       person_id=person)
    assert "audit" not in ws["section_keys"]        # gated by audit.read
    assert "tasks" in ws["section_keys"] and "notes" in ws["section_keys"]


# --- empty / fail-soft -------------------------------------------------------

def test_empty_client_tabs_no_error(person):
    ws = get_workspace(_principal(), person_id=person)
    assert ws["sections"]["tasks"]["tasks"] == []
    assert ws["sections"]["notes"]["notes"] == []
    assert ws["sections"]["audit"]["events"] == []


# --- render ------------------------------------------------------------------

def test_tabs_render(person):
    from app.routes.client360 import client_workspace
    from app.services.tasks import create_task
    create_task(person, title=f"Render task {_TAG}", actor_user_id=_ACTOR["uid"], request_id="t")

    def _get():
        r = Request({"type": "http", "method": "GET", "path": f"/client/{person}", "headers": [],
                     "query_string": b"", "state": {}})
        r.state.principal = _principal()
        r.state.request_id = "t"
        return r
    # Needles are each panel's OWN heading. They used to be satisfied incidentally by the
    # sub-tab strip, which title-cased the section key; Activity and Internal are single-
    # section tabs in the Phase 3 model, so no strip renders and the panel must say it.
    for tab, needle in (("tasks", "Render task"), ("notes", "Internal notes"), ("audit", "Audit history"),
                        ("timeline", "Activity timeline")):
        html = client_workspace(_get(), person_id=person, tab=tab, principal=_principal()).body.decode()
        assert needle in html, tab
