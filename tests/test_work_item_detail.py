"""Unified work-item DETAIL screen + connected actions (extends the existing work engine).

Covers the detail composer (reusing the queue adapters + record_assignments + work_approvals +
vault_document_links), the detail route render + 404, the newly-connected dispatch actions
(waiting / task-complete) with capability gating, and Vault-document linking via the existing
vault_document_links.work_item_id column (no new schema).
"""
import io
import uuid

import pytest
from sqlalchemy import delete, insert, select
from starlette.requests import Request

from app.db import engine, households, people, tasks, users, vault_document_links, vault_documents
from app.security.models import Principal
from app.services.vault import service as vault
from app.services.work_queue import dispatch as qdispatch
from app.services.work_queue.detail import work_item_detail

STAFF = frozenset({"work.read", "work.write", "work.approve", "record.read_all",
                   "vault.view", "vault.upload", "vault.manage", "vault.access.all"})
READONLY = frozenset({"work.read", "record.read_all"})


@pytest.fixture
def work_item():
    tag = uuid.uuid4().hex[:8]
    created = {"docs": []}
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=f"w{tag}@e.test", normalized_email=f"w{tag}@e.test", display_name="Worker",
            auth_subject=f"w{tag}", status="active").returning(users.c.id)).scalar_one()
        hid = c.execute(insert(households).values(name=f"WH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {tag}",
                        active=True).returning(people.c.id)).scalar_one()
        tid = c.execute(insert(tasks).values(person_id=pid, household_id=hid, title=f"Prepare {tag}",
                        status="open", priority="high", work_type="general").returning(tasks.c.id)).scalar_one()
    created.update(uid=uid, hid=hid, pid=pid, tid=tid, tag=tag)
    created["principal"] = Principal(uid, "w@e.test", "Worker", STAFF)
    yield created
    with engine.begin() as c:
        for doc in created["docs"]:
            c.execute(delete(vault_documents).where(vault_documents.c.id == doc))   # cascades links
        c.execute(delete(tasks).where(tasks.c.id == created["tid"]))
        c.execute(delete(people).where(people.c.id == created["pid"]))
        c.execute(delete(households).where(households.c.id == created["hid"]))


def _key(work_item):
    return f"tasks:task:{work_item['tid']}"


def _req(qs=b""):
    return Request({"type": "http", "method": "GET", "path": "/work/tasks/1",
                    "headers": [], "query_string": qs, "state": {}})


# --- composer ----------------------------------------------------------------

def test_detail_composer_returns_item(work_item):
    d = work_item_detail(work_item["principal"], "tasks", work_item["tid"])
    assert d is not None
    assert d["item"]["title"].startswith("Prepare")
    assert d["entity_type"] == "task" and d["source_id"] == work_item["tid"]
    assert d["item"]["person_id"] == work_item["pid"]


def test_detail_composer_none_for_unknown(work_item):
    assert work_item_detail(work_item["principal"], "tasks", 99999999) is None


# --- Vault linking (existing work_item_id column, no new schema) --------------

def test_linked_vault_document_appears_in_detail(work_item):
    doc_id = vault.create_document(
        work_item["principal"], source=io.BytesIO(b"tax organizer"), original_filename="org.pdf",
        display_name="Tax Organizer", category="tax", actor_user_id=work_item["uid"],
        person_id=work_item["pid"])
    work_item["docs"].append(doc_id)
    with engine.begin() as c:
        c.execute(insert(vault_document_links).values(document_id=doc_id, work_item_id=work_item["tid"]))
    d = work_item_detail(work_item["principal"], "tasks", work_item["tid"])
    assert doc_id in {doc["id"] for doc in d["documents"]}


# --- connected dispatch actions ----------------------------------------------

def test_waiting_action_sets_waiting_on(work_item):
    res = qdispatch.dispatch_action(work_item["principal"], work_item_key=_key(work_item),
                                    action="waiting", params={"waiting_on": "client"})
    assert res["ok"]
    with engine.connect() as c:
        assert c.scalar(select(tasks.c.waiting_on).where(tasks.c.id == work_item["tid"])) == "client"


def test_complete_action_completes_task(work_item):
    res = qdispatch.dispatch_action(work_item["principal"], work_item_key=_key(work_item),
                                    action="complete")
    assert res["ok"]
    with engine.connect() as c:
        assert c.scalar(select(tasks.c.status).where(tasks.c.id == work_item["tid"])) == "complete"


def test_actions_require_write_capability(work_item):
    ro = Principal(work_item["uid"], "ro@e.test", "RO", READONLY)
    res = qdispatch.dispatch_action(ro, work_item_key=_key(work_item), action="waiting",
                                    params={"waiting_on": "client"})
    assert not res["ok"] and res["outcome"] == "denied"
    with engine.connect() as c:
        assert c.scalar(select(tasks.c.waiting_on).where(tasks.c.id == work_item["tid"])) is None


# --- route render ------------------------------------------------------------

def test_detail_route_renders(work_item):
    from app.routes.work import work_item_detail_page
    resp = work_item_detail_page("tasks", work_item["tid"], _req(), principal=work_item["principal"])
    html = resp.body.decode()
    assert resp.status_code == 200
    assert "Prepare" in html and "Actions" in html and "Linked Vault documents" in html
    assert "History" in html and "Approvals" in html


def test_detail_route_404_for_unknown(work_item):
    from app.routes.work import work_item_detail_page
    resp = work_item_detail_page("tasks", 99999999, _req(), principal=work_item["principal"])
    assert resp.status_code == 404


def test_readonly_principal_cannot_act_in_detail(work_item):
    from app.routes.work import work_item_detail_page
    ro = Principal(work_item["uid"], "ro@e.test", "RO", READONLY)
    html = work_item_detail_page("tasks", work_item["tid"], _req(), principal=ro).body.decode()
    assert "read-only access" in html
