"""Audit symmetry for secure messaging: a client reply (send_message) now writes a service-level
``portal.message.sent`` event, matching create_thread (opening message) and staff_send_message.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import audit_events, engine
from app.portal.service import create_thread, send_message, staff_send_message
from tests._portal_util import seed_portal_account, seed_staff_user


def _sent_total():
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "portal.message.sent"))


def _sent_for_message(message_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == "portal.message.sent")
            & (audit_events.c.entity_id == str(message_id))))


def test_client_reply_writes_service_level_audit():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    create_thread(principal, household_id=hid, person_id=pid, subject="Q", body="opening")
    before = _sent_total()
    message_id = send_message(principal, _latest_thread(principal), "a reply")
    assert _sent_total() == before + 1
    assert _sent_for_message(message_id) == 1              # audited against the exact message id


def test_out_of_scope_reply_writes_no_mutation_audit():
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, bob, bob_pid, bob_hid = seed_portal_account(seed_staff_user())
    bob_thread = create_thread(bob, household_id=bob_hid, person_id=bob_pid, subject="B", body="B")
    before = _sent_total()
    with pytest.raises(PermissionError):
        send_message(alice, bob_thread, "sneak")
    assert _sent_total() == before                          # failed reply is never audited


def test_staff_reply_audit_behavior_unchanged():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="S", body="B")
    staff_uid = seed_staff_user()
    before = _sent_total()
    msg_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="staff answer")
    assert _sent_total() == before + 1
    assert _sent_for_message(msg_id) == 1
    # An internal note uses a different action, not portal.message.sent.
    before2 = _sent_total()
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="note", internal_note=True)
    assert _sent_total() == before2                         # internal note is not a client-visible send


def _latest_thread(principal):
    from app.portal.service import client_threads
    return client_threads(principal)[0]["id"]
