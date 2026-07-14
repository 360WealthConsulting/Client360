from datetime import date, datetime, timedelta, timezone
import io, uuid
import pytest
from sqlalchemy import func, select

from app.db import (audit_events, document_versions, documents, engine, households,
    people, portal_access_grants, portal_accounts, portal_devices,
    portal_document_requests, portal_message_receipts, portal_messages,
    portal_notifications, portal_sessions, roles, signature_requests, timeline_events,
    user_roles, users, workflow_instances, workflow_steps)
from app.main import app
from app.portal.providers import SignatureProvider, SignatureResult
from app.portal.service import (accept_invitation, approve_request_upload, client_tasks,
    complete_client_task, confirm_request_upload, create_document_request,
    create_portal_session, create_thread, dashboard, invite_portal_account,
    list_messages, mark_read, notify, portal_scope, request_password_reset,
    consume_password_reset, resolve_portal_session, revoke_portal_session,
    send_message, staff_send_message)
from app.portal.signatures import apply_signature_event, create_signature_request, registry

def _seed_household(label="Portal"):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as connection:
        household_id = connection.execute(households.insert().values(name=f"{label} {suffix}").returning(households.c.id)).scalar_one()
        people_ids = [connection.execute(people.insert().values(household_id=household_id, full_name=f"{name} {suffix}", active=True).returning(people.c.id)).scalar_one() for name in ("Primary", "Joint")]
        role_id = connection.scalar(select(roles.c.id).where(roles.c.code == "advisor"))
        user_id = connection.execute(users.insert().values(email=f"staff-{suffix}@example.com", normalized_email=f"staff-{suffix}@example.com", display_name="Portal Staff", auth_subject=f"staff-{suffix}", status="active").returning(users.c.id)).scalar_one()
        connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))
    return household_id, people_ids, user_id, suffix

def _activate(person_id, household_id, user_id, suffix, access_type="self", permissions=None):
    account_id, invitation = invite_portal_account(person_id=person_id, household_id=household_id, email=f"portal-{suffix}-{access_type}@example.com", display_name="Portal Client", access_type=access_type, invited_by_user_id=user_id, permissions=permissions)
    accept_invitation(invitation, f"portal-subject-{suffix}-{access_type}", True)
    return account_id

def _principal(account_id):
    token = create_portal_session(account_id, device_fingerprint=f"device-{uuid.uuid4()}", device_name="Test Browser")
    return token, resolve_portal_session(token)

def test_invitation_mfa_session_device_reset_and_revocation():
    household_id, person_ids, user_id, suffix = _seed_household()
    account_id, invitation = invite_portal_account(person_id=person_ids[0], household_id=household_id, email=f"identity-{suffix}@example.com", display_name="Identity Client", access_type="self", invited_by_user_id=user_id)
    with pytest.raises(ValueError): accept_invitation(invitation, f"subject-{suffix}", False)
    assert accept_invitation(invitation, f"subject-{suffix}", True) == account_id
    token = create_portal_session(account_id, device_fingerprint="browser-one", device_name="Laptop", ip_address="127.0.0.1")
    principal = resolve_portal_session(token); assert principal.account_id == account_id
    reset = request_password_reset(f"identity-{suffix}@example.com"); assert consume_password_reset(reset) == account_id
    with pytest.raises(ValueError): consume_password_reset(reset)
    revoke_portal_session(token); assert resolve_portal_session(token) is None
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(portal_devices).where(portal_devices.c.portal_account_id == account_id)) == 1
        assert connection.scalar(select(func.count()).select_from(portal_sessions).where(portal_sessions.c.portal_account_id == account_id)) == 1

def test_household_joint_and_delegated_scope_isolated_from_other_households():
    household_id, person_ids, user_id, suffix = _seed_household()
    account_id = _activate(person_ids[0], household_id, user_id, suffix, "delegated", {"messages": True, "documents": True, "tasks": True})
    _, principal = _principal(account_id); scope = portal_scope(account_id)
    assert set(person_ids) <= scope["person_ids"] and household_id in scope["household_ids"]
    other_household, other_people, _, _ = _seed_household("Other")
    with pytest.raises(PermissionError): create_thread(principal, household_id=other_household, person_id=other_people[0], subject="Forbidden", body="No access")
    self_account = _activate(person_ids[0], household_id, user_id, f"{suffix}-self", "self", {"messages": True, "documents": True, "tasks": True})
    _, self_principal = _principal(self_account)
    assert person_ids[1] not in portal_scope(self_account)["person_ids"]
    with pytest.raises(PermissionError): create_thread(self_principal, household_id=household_id, person_id=person_ids[1], subject="Joint private", body="Must remain isolated")

def test_secure_messages_hide_internal_notes_publish_timeline_audit_and_receipts():
    household_id, person_ids, user_id, suffix = _seed_household()
    account_id = _activate(person_ids[0], household_id, user_id, suffix, permissions={"messages": True, "documents": True, "tasks": True})
    _, principal = _principal(account_id)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_ids[0], subject="Tax question", body="Please advise")
    internal_id = staff_send_message(thread_id=thread_id, user_id=user_id, body="Internal tax analysis", internal_note=True)
    visible_id = staff_send_message(thread_id=thread_id, user_id=user_id, body="We are reviewing this", internal_note=False)
    message_id = send_message(principal, thread_id, "Thank you")
    visible = list_messages(principal, thread_id)
    assert internal_id not in {m["id"] for m in visible} and {visible_id, message_id} <= {m["id"] for m in visible}
    receipt_id = mark_read(principal, visible_id); assert mark_read(principal, visible_id) == receipt_id
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(timeline_events).where(timeline_events.c.person_id == person_ids[0], timeline_events.c.source == "client_portal")) >= 3
        assert connection.scalar(select(func.count()).select_from(audit_events).where(audit_events.c.entity_type == "portal_message")) >= 2
    with pytest.raises(Exception):
        with engine.begin() as connection: connection.execute(portal_messages.update().where(portal_messages.c.id == message_id).values(body="tampered"))

def test_document_request_upload_version_approval_and_scope():
    household_id, person_ids, user_id, suffix = _seed_household()
    account_id = _activate(person_ids[0], household_id, user_id, suffix, permissions={"messages": True, "documents": True, "tasks": True}); _, principal = _principal(account_id)
    request_id = create_document_request(person_id=person_ids[0], household_id=household_id, title="Upload W-2", requested_by_user_id=user_id, due_date=date.today()+timedelta(days=7))
    with engine.begin() as connection:
        document_id = connection.execute(documents.insert().values(person_id=person_ids[0], original_name="w2.pdf", stored_name=f"w2-{suffix}.pdf", storage_path=f"/tmp/w2-{suffix}.pdf", size_bytes=10, sha256=("a"*54)+suffix, category="tax").returning(documents.c.id)).scalar_one()
    assert confirm_request_upload(principal, request_id, document_id) == 1
    assert approve_request_upload(request_id, approved_by_user_id=user_id) == "approved"
    with engine.connect() as connection:
        row = connection.execute(select(portal_document_requests).where(portal_document_requests.c.id == request_id)).mappings().one()
        assert row["status"] == "approved" and row["uploaded_document_id"] == document_id
        assert connection.scalar(select(func.count()).select_from(document_versions).where(document_versions.c.document_id == document_id)) == 1

def test_notifications_are_idempotent_and_external_hooks_disabled():
    household_id, person_ids, user_id, suffix = _seed_household(); account_id = _activate(person_ids[0], household_id, user_id, suffix)
    key = f"notice-{suffix}"; first = notify(account_id, "request.created", "Document requested", idempotency_key=key)
    assert notify(account_id, "request.created", "Document requested", idempotency_key=key) == first
    email_id = notify(account_id, "request.created", "Email hook", channel="email", idempotency_key=f"email-{suffix}")
    with engine.connect() as connection:
        assert connection.scalar(select(portal_notifications.c.status).where(portal_notifications.c.id == first)) == "delivered"
        assert connection.scalar(select(portal_notifications.c.status).where(portal_notifications.c.id == email_id)) == "disabled"

def test_client_workflow_task_visibility_completion_and_dashboard():
    household_id, person_ids, user_id, suffix = _seed_household(); account_id = _activate(person_ids[0], household_id, user_id, suffix); _, principal = _principal(account_id)
    with engine.begin() as connection:
        workflow_id = connection.execute(workflow_instances.insert().values(name="Portal onboarding", person_id=person_ids[0], household_id=household_id, status="active").returning(workflow_instances.c.id)).scalar_one()
        step_id = connection.execute(workflow_steps.insert().values(workflow_instance_id=workflow_id, name="Confirm profile", sequence=10, status="active", waiting_on="client", definition_snapshot={"assignment_config": {"audience": "client"}}).returning(workflow_steps.c.id)).scalar_one()
    assert step_id in {row["id"] for row in client_tasks(principal)}
    assert dashboard(principal)["tasks"][0]["id"] == step_id
    complete_client_task(principal, step_id)
    with engine.connect() as connection: assert connection.scalar(select(workflow_steps.c.status).where(workflow_steps.c.id == step_id)) == "completed"

class FakeSignatureProvider(SignatureProvider):
    key = "fake"
    def create_request(self, **kwargs): return SignatureResult(f"fake-{uuid.uuid4()}", "sent", {"sandbox": True})
    def get_status(self, external_id): return SignatureResult(external_id, "sent", {})
    def cancel(self, external_id): return SignatureResult(external_id, "cancelled", {})

def test_signature_provider_abstraction_and_completion_event():
    household_id, person_ids, user_id, suffix = _seed_household(); registry.register(FakeSignatureProvider())
    request_id = create_signature_request(provider_key="fake", person_id=person_ids[0], household_id=household_id, requested_by_user_id=user_id, documents=[1], recipients=[{"email": "client@example.com"}], callback_url="https://example.com/callback")
    with engine.connect() as connection: external_id = connection.scalar(select(signature_requests.c.external_id).where(signature_requests.c.id == request_id))
    assert apply_signature_event("fake", external_id, "completed") == request_id
    with engine.connect() as connection: assert connection.scalar(select(signature_requests.c.status).where(signature_requests.c.id == request_id)) == "completed"

def test_portal_api_routes_and_templates_are_registered_and_renderable():
    routes = {(route.path, method) for route in app.routes for method in (getattr(route, "methods", None) or set())}
    expected = {("/api/v1/portal/dashboard", "GET"), ("/api/v1/portal/profile", "GET"), ("/api/v1/portal/messages", "GET"), ("/api/v1/portal/messages", "POST"), ("/api/v1/portal/documents", "GET"), ("/api/v1/portal/requests", "GET"), ("/api/v1/portal/tasks", "GET"), ("/api/v1/portal/notifications", "GET"), ("/portal/{page:path}", "GET")}
    assert expected <= routes
    schema = app.openapi(); assert all(path in schema["paths"] for path in ("/api/v1/portal/dashboard", "/api/v1/portal/messages", "/api/v1/portal/requests/{request_id}/upload"))
    from app.routes.portal import templates
    for name in ("login", "dashboard", "messages", "documents", "requests", "tasks", "notifications", "settings"):
        templates.get_template(f"portal/{name}.html")
