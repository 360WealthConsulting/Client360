"""Communications & Client Engagement platform tests (Phase D.18).

Covers conversation/message CRUD, template CRUD + deterministic rendering, threading, recipient
handling, the delivery lifecycle (queued→sent→delivered→read + cancel + invalid transitions),
authorization + record scope (person / household / organization anchors + firm-wide), document
attachment references, notification-ledger reuse (metadata-only transport), approved Timeline
lifecycle events, Analytics consumption (``active_conversations``), the append-only audit ledger,
and architecture invariants. The notification ledger, outbox, M365 integrations, legacy
``communication.read/write`` capabilities, and the D.5 golden are untouched.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select, update

from app.db import (
    communication_conversations,
    communication_events,
    communication_templates,
    documents,
    engine,
    people,
    record_assignments,
    relationship_entities,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.analytics import sources
from app.services.communications import delivery
from app.services.communications import service as svc
from app.services.communications import templates as tmpl

CAPS = frozenset({"communications.view", "communications.send", "communications.manage_templates",
                  "communications.audit", "communications.admin", "record.read_all",
                  "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup(*, with_org=False):
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"cm-{tag}@e.test", normalized_email=f"cm-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        stranger = c.execute(users.insert().values(
            email=f"str-{tag}@e.test", normalized_email=f"str-{tag}@e.test",
            display_name=f"S {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
        org_id = None
        if with_org:
            org_id = c.execute(relationship_entities.insert().values(
                entity_type="organization", name=f"Org {tag}").returning(
                relationship_entities.c.id)).scalar_one()
            c.execute(insert(record_assignments).values(
                user_id=uid, entity_type="organization", entity_id=org_id,
                assignment_type="owner", effective_date=date.today()))
    return {"uid": uid, "stranger": stranger, "pid": pid, "org_id": org_id, "tag": tag}


def _teardown(ids):
    # communication_events is append-only (trigger-blocked) and RESTRICT-anchors the conversation,
    # so conversations cannot be deleted: detach mutable anchors and leave them as leftovers.
    with engine.begin() as c:
        c.execute(update(communication_conversations)
                  .where(communication_conversations.c.person_id == ids["pid"])
                  .values(person_id=None))
        if ids.get("org_id"):
            c.execute(update(communication_conversations)
                      .where(communication_conversations.c.organization_id == ids["org_id"])
                      .values(organization_id=None))
        c.execute(delete(timeline_events).where(timeline_events.c.source == "communication",
                                                timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        if ids.get("org_id"):
            c.execute(delete(record_assignments).where(
                record_assignments.c.entity_id == ids["org_id"],
                record_assignments.c.entity_type == "organization"))
        c.execute(delete(documents).where(documents.c.stored_name.like(f"%{ids['tag']}%")))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        if ids.get("org_id"):
            c.execute(delete(relationship_entities).where(relationship_entities.c.id == ids["org_id"]))
        c.execute(delete(users).where(users.c.id.in_((ids["uid"], ids["stranger"]))))
        c.execute(delete(communication_templates)
                  .where(communication_templates.c.code.like(f"t-{ids['tag']}%")))


# --- conversation + message CRUD --------------------------------------------

def test_create_conversation_and_send_message():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conv = svc.create_conversation(p, subject="Quarterly check-in", category="review",
                                       channel="email", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        assert conv["id"] and conv["default_thread_id"]
        assert conv["status"] == "open"

        detail = svc.get_conversation(p, conv["id"])
        assert detail["conversation"]["subject"] == "Quarterly check-in"
        assert len(detail["threads"]) == 1

        msg = svc.send_message(p, conv["id"], body="Hello there", subject="Hi",
                               recipients_in=[{"recipient_ref": "client@e.test", "recipient_type":
                                               "external", "recipient_role": "to"}],
                               actor_user_id=ids["uid"])
        assert msg["status"] == "sent"           # mark_sent default
        assert msg["sent_at"] is not None
        assert msg["notification_uid"]           # reused notification ledger recorded intent

        detail = svc.get_conversation(p, conv["id"])
        assert len(detail["messages"]) == 1
        assert len(detail["messages"][0]["recipients"]) == 1
        # last_message_at bumped
        assert detail["conversation"]["last_message_at"] is not None
    finally:
        _teardown(ids)


def test_list_conversations_filters():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        svc.create_conversation(p, subject="Alpha review", category="review", person_id=ids["pid"],
                                actor_user_id=ids["uid"])
        svc.create_conversation(p, subject="Beta tax", category="tax", person_id=ids["pid"],
                                actor_user_id=ids["uid"])
        res = svc.list_conversations(p)
        assert res["total"] >= 2
        tax = svc.list_conversations(p, category="tax")
        assert all(r["category"] == "tax" for r in tax["rows"])
        found = svc.list_conversations(p, search="Alpha")
        assert any("Alpha" in r["subject"] for r in found["rows"])
    finally:
        _teardown(ids)


# --- templates ---------------------------------------------------------------

def test_template_crud_and_deterministic_render():
    ids = _setup()
    try:
        code = f"t-{ids['tag']}"
        t = tmpl.create_template(code=code, name="Welcome", category="onboarding", channel="email",
                                 subject="Welcome {{first_name}}", body="Hi {{first_name}}, {{note}}",
                                 actor_user_id=ids["uid"])
        assert t["id"] and t["active"] is True
        rendered = tmpl.render(t, {"first_name": "Sam", "note": "great to meet you"})
        assert rendered["subject"] == "Welcome Sam"
        assert rendered["body"] == "Hi Sam, great to meet you"
        # deterministic: same inputs, same output; unknown placeholder -> blank (no invention)
        assert tmpl.render(t, {"first_name": "Sam"})["body"] == "Hi Sam, "
        assert tmpl.render(t, {"first_name": "Sam"}) == tmpl.render(t, {"first_name": "Sam"})

        updated = tmpl.update_template(t["id"], active=False)
        assert updated["active"] is False
        with pytest.raises(tmpl.TemplateError):
            tmpl.create_template(code=code, name="dup", body="x")   # duplicate code
    finally:
        _teardown(ids)


def test_seeded_starter_templates_exist():
    for code in ("welcome", "annual_review", "tax_organizer", "missing_documents"):
        assert tmpl.get_template(code=code) is not None


def test_send_message_from_template_renders_body():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        code = f"t-{ids['tag']}"
        tmpl.create_template(code=code, name="Rev", category="review", channel="email",
                             subject="Review {{year}}", body="Time for your {{year}} review.",
                             actor_user_id=ids["uid"])
        conv = svc.create_conversation(p, subject="Review", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        msg = svc.send_message(p, conv["id"], template_code=code,
                               template_context={"year": "2026"}, mark_sent=False,
                               actor_user_id=ids["uid"])
        assert msg["body"] == "Time for your 2026 review."
        assert msg["subject"] == "Review 2026"
        assert msg["status"] == "queued"        # mark_sent=False
    finally:
        _teardown(ids)


# --- delivery lifecycle ------------------------------------------------------

def test_delivery_lifecycle_and_invalid_transition():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conv = svc.create_conversation(p, subject="Delivery", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        msg = svc.send_message(p, conv["id"], body="hi", mark_sent=True, actor_user_id=ids["uid"])
        assert msg["status"] == "sent"
        msg = svc.transition_delivery(p, msg["id"], "delivered", actor_user_id=ids["uid"])
        assert msg["status"] == "delivered" and msg["delivered_at"]
        msg = svc.mark_read(p, msg["id"], actor_user_id=ids["uid"])
        assert msg["status"] == "read" and msg["read_at"]
        # read is terminal -> cannot go back to sent
        with pytest.raises((svc.CommunicationError, delivery.DeliveryError)):
            svc.transition_delivery(p, msg["id"], "sent", actor_user_id=ids["uid"])

        hist = svc.message_delivery_history(p, msg["id"])
        assert [h["status"] for h in hist] == ["sent", "delivered", "read"]
        assert "delivered" in delivery.allowed_next("sent")
    finally:
        _teardown(ids)


def test_cancel_message():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conv = svc.create_conversation(p, subject="Cancel", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        msg = svc.send_message(p, conv["id"], body="hi", mark_sent=False, actor_user_id=ids["uid"])
        msg = svc.cancel_message(p, msg["id"], actor_user_id=ids["uid"])
        assert msg["status"] == "cancelled"
    finally:
        _teardown(ids)


# --- authorization + record scope -------------------------------------------

def test_scope_blocks_stranger_person_anchor():
    ids = _setup()
    try:
        owner = _principal(ids["uid"])
        conv = svc.create_conversation(owner, subject="Private", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], {"communications.view", "communications.send"})
        assert svc.get_conversation(stranger, conv["id"]) is None
        rows = svc.list_conversations(stranger)["rows"]
        assert all(r["id"] != conv["id"] for r in rows)
        with pytest.raises(svc.CommunicationNotFound):
            svc.audit_history(stranger, conv["id"])
    finally:
        _teardown(ids)


def test_scoped_owner_sees_person_conversation():
    ids = _setup()
    try:
        # owner has NO record.read_all — only the person assignment
        owner = _principal(ids["uid"], {"communications.view", "communications.send"})
        conv = svc.create_conversation(owner, subject="Scoped", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        assert svc.get_conversation(owner, conv["id"]) is not None
        assert any(r["id"] == conv["id"] for r in svc.list_conversations(owner)["rows"])
    finally:
        _teardown(ids)


def test_organization_anchor_scope():
    ids = _setup(with_org=True)
    try:
        owner = _principal(ids["uid"], {"communications.view", "communications.send"})
        conv = svc.create_conversation(owner, subject="Org", organization_id=ids["org_id"],
                                       actor_user_id=ids["uid"])
        assert svc.get_conversation(owner, conv["id"]) is not None
        stranger = _principal(ids["stranger"], {"communications.view"})
        assert svc.get_conversation(stranger, conv["id"]) is None
    finally:
        _teardown(ids)


def test_create_conversation_requires_write_scope():
    ids = _setup()
    try:
        stranger = _principal(ids["stranger"], {"communications.send"})
        with pytest.raises(svc.CommunicationError):
            svc.create_conversation(stranger, subject="X", person_id=ids["pid"],
                                    actor_user_id=ids["stranger"])
    finally:
        _teardown(ids)


# --- document reference ------------------------------------------------------

def test_document_attachment_reference():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        with engine.begin() as c:
            doc_id = c.execute(documents.insert().values(
                original_name="statement.pdf", stored_name=f"{ids['tag']}-stmt.pdf",
                storage_path=f"/tmp/{ids['tag']}.pdf", size_bytes=10, sha256=ids["tag"] * 8,
                person_id=ids["pid"]).returning(documents.c.id)).scalar_one()
        conv = svc.create_conversation(p, subject="Docs", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        msg = svc.send_message(p, conv["id"], body="see attached",
                               attachment_document_ids=[doc_id], mark_sent=False,
                               actor_user_id=ids["uid"])
        detail = svc.get_conversation(p, conv["id"])
        atts = detail["messages"][0]["attachments"]
        assert len(atts) == 1 and atts[0]["document_id"] == doc_id
        # the unique constraint blocks a duplicate document attachment on the same message
        with pytest.raises(Exception):
            svc.add_attachment(p, msg["id"], doc_id, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- timeline integration ----------------------------------------------------

def test_timeline_lifecycle_events_published():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conv = svc.create_conversation(p, subject="TL", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        msg = svc.send_message(p, conv["id"], body="hi", mark_sent=True, actor_user_id=ids["uid"])
        svc.transition_delivery(p, msg["id"], "delivered", actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "communication",
                timeline_events.c.person_id == ids["pid"])))
        assert "conversation_opened" in types
        assert "communication_sent" in types
        assert "communication_delivered" in types
    finally:
        _teardown(ids)


# --- analytics consumption ---------------------------------------------------

def test_analytics_active_conversations_metric():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        before = sources.active_conversation_count(p)
        svc.create_conversation(p, subject="Metric", person_id=ids["pid"], actor_user_id=ids["uid"])
        after = sources.active_conversation_count(p)
        assert after == before + 1
        from app.services.analytics.metrics import METRICS
        assert "active_conversations" in METRICS
    finally:
        _teardown(ids)


# --- audit ledger ------------------------------------------------------------

def test_audit_ledger_records_and_is_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        conv = svc.create_conversation(p, subject="Audit", person_id=ids["pid"],
                                       actor_user_id=ids["uid"])
        svc.send_message(p, conv["id"], body="hi", mark_sent=True, actor_user_id=ids["uid"])
        history = svc.audit_history(p, conv["id"])
        etypes = [e["event_type"] for e in history]
        assert "conversation_opened" in etypes
        assert "message_created" in etypes
        assert "delivery_sent" in etypes
        # append-only: UPDATE/DELETE on communication_events must be blocked by the trigger
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(communication_events)
                          .where(communication_events.c.conversation_id == conv["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(communication_events)
                          .where(communication_events.c.conversation_id == conv["id"]))
    finally:
        _teardown(ids)


# --- architecture invariants -------------------------------------------------

def test_communications_does_not_import_analytics():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "delivery.py", "templates.py"):
        src = (root / name).read_text()
        assert "import analytics" not in src and "services.analytics" not in src, name


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/communications") for pattern, _cap in RULES)
    assert not any(pattern.search("/communications/5") for pattern, _cap in RULES)
