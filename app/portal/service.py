from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib, secrets, uuid
from sqlalchemy import and_, func, or_, select

from app.db import (documents, document_versions, engine, people, portal_access_grants,
    portal_accounts, portal_auth_tokens, portal_devices, portal_document_requests,
    portal_invitations, portal_message_attachments, portal_message_receipts,
    portal_messages, portal_notifications, portal_sessions, portal_thread_participants,
    portal_threads, timeline_events, workflow_instances, workflow_steps)
from app.portal.providers import NOTIFICATION_PROVIDERS
from app.security.audit import write_audit_event
from app.services.timeline import add_timeline_event
from app.services.workflow_automation import complete_step

@dataclass(frozen=True)
class PortalPrincipal:
    account_id: int
    person_id: int
    email: str
    display_name: str

def _hash(value): return hashlib.sha256(value.encode()).hexdigest()
def _active_grant():
    today = date.today()
    return and_(portal_access_grants.c.effective_date <= today, or_(portal_access_grants.c.inactive_date.is_(None), portal_access_grants.c.inactive_date >= today))

def invite_portal_account(*, person_id, household_id, email, display_name, access_type, invited_by_user_id, permissions=None, expires_hours=72, organization_id=None):
    normalized = email.strip().lower(); raw = secrets.token_urlsafe(32)
    # Employer portal accounts (organization_id set) keep the HR-contact person on the grant
    # so they can use the existing person-scoped secure messages/documents, and add the
    # organization scope used for the employer "Action Needed" surface.
    grant_person_id = person_id if (access_type == "self" or organization_id is not None) else None
    with engine.begin() as connection:
        account_id = connection.execute(portal_accounts.insert().values(person_id=person_id, email=email, normalized_email=normalized, display_name=display_name, status="invited").returning(portal_accounts.c.id)).scalar_one()
        connection.execute(portal_access_grants.insert().values(portal_account_id=account_id, household_id=household_id, person_id=grant_person_id, organization_id=organization_id, access_type=access_type, permissions=permissions or {"messages": True, "documents": True, "tasks": True}, granted_by_user_id=invited_by_user_id))
        connection.execute(portal_invitations.insert().values(portal_account_id=account_id, token_hash=_hash(raw), invited_by_user_id=invited_by_user_id, expires_at=datetime.now(timezone.utc)+timedelta(hours=expires_hours)))
    write_audit_event(action="portal.invited", entity_type="portal_account", entity_id=account_id, actor_user_id=invited_by_user_id, request_id=f"portal-invite-{uuid.uuid4()}", metadata={"person_id": person_id, "household_id": household_id, "access_type": access_type})
    return account_id, raw

def accept_invitation(token, auth_subject, mfa_verified):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        invitation = connection.execute(select(portal_invitations).where(portal_invitations.c.token_hash == _hash(token), portal_invitations.c.accepted_at.is_(None), portal_invitations.c.revoked_at.is_(None), portal_invitations.c.expires_at > now).with_for_update()).mappings().one_or_none()
        if not invitation: raise ValueError("Invitation is invalid or expired")
        if not mfa_verified: raise ValueError("MFA verification is required")
        connection.execute(portal_invitations.update().where(portal_invitations.c.id == invitation["id"]).values(accepted_at=now))
        connection.execute(portal_accounts.update().where(portal_accounts.c.id == invitation["portal_account_id"]).values(status="active", auth_subject=auth_subject, mfa_enabled=True, updated_at=now))
    return invitation["portal_account_id"]

def request_password_reset(email, expires_minutes=30):
    raw = secrets.token_urlsafe(32); now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        account_id = connection.scalar(select(portal_accounts.c.id).where(portal_accounts.c.normalized_email == email.strip().lower(), portal_accounts.c.status == "active"))
        if account_id: connection.execute(portal_auth_tokens.insert().values(portal_account_id=account_id, token_type="password_reset", token_hash=_hash(raw), expires_at=now+timedelta(minutes=expires_minutes)))
    return raw if account_id else None

def consume_password_reset(token):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        row = connection.execute(select(portal_auth_tokens).where(portal_auth_tokens.c.token_hash == _hash(token), portal_auth_tokens.c.token_type == "password_reset", portal_auth_tokens.c.used_at.is_(None), portal_auth_tokens.c.expires_at > now).with_for_update()).mappings().one_or_none()
        if not row: raise ValueError("Reset token is invalid or expired")
        connection.execute(portal_auth_tokens.update().where(portal_auth_tokens.c.id == row["id"]).values(used_at=now))
    return row["portal_account_id"]

def create_portal_session(account_id, *, device_fingerprint, device_name=None, ip_address=None, user_agent=None, hours=8):
    raw = secrets.token_urlsafe(32); fingerprint = _hash(device_fingerprint); now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        account = connection.execute(select(portal_accounts).where(portal_accounts.c.id == account_id, portal_accounts.c.status == "active")).mappings().one_or_none()
        if not account or (account["mfa_required"] and not account["mfa_enabled"]): raise ValueError("Portal account is not ready for sign-in")
        device_id = connection.scalar(select(portal_devices.c.id).where(portal_devices.c.portal_account_id == account_id, portal_devices.c.fingerprint_hash == fingerprint))
        if device_id: connection.execute(portal_devices.update().where(portal_devices.c.id == device_id).values(last_seen_at=now, name=device_name))
        else: device_id = connection.execute(portal_devices.insert().values(portal_account_id=account_id, fingerprint_hash=fingerprint, name=device_name).returning(portal_devices.c.id)).scalar_one()
        connection.execute(portal_sessions.insert().values(portal_account_id=account_id, session_hash=_hash(raw), device_id=device_id, expires_at=now+timedelta(hours=hours), last_seen_at=now, ip_address=ip_address, user_agent=user_agent))
        connection.execute(portal_accounts.update().where(portal_accounts.c.id == account_id).values(last_login_at=now))
    write_audit_event(action="portal.login", entity_type="portal_account", entity_id=account_id, request_id=f"portal-login-{uuid.uuid4()}", ip_address=ip_address, user_agent=user_agent)
    return raw

def resolve_portal_session(raw):
    if not raw: return None
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        row = connection.execute(select(portal_sessions.c.id.label("session_id"), portal_accounts.c.id.label("portal_account_id"), portal_accounts.c.person_id, portal_accounts.c.email, portal_accounts.c.display_name).join(portal_accounts, portal_accounts.c.id == portal_sessions.c.portal_account_id).where(portal_sessions.c.session_hash == _hash(raw), portal_sessions.c.revoked_at.is_(None), portal_sessions.c.expires_at > now, portal_accounts.c.status == "active")).mappings().one_or_none()
        if not row: return None
        connection.execute(portal_sessions.update().where(portal_sessions.c.id == row["session_id"]).values(last_seen_at=now))
    return PortalPrincipal(row["portal_account_id"], row["person_id"], row["email"], row["display_name"])

def revoke_portal_session(raw):
    if raw:
        with engine.begin() as connection: connection.execute(portal_sessions.update().where(portal_sessions.c.session_hash == _hash(raw)).values(revoked_at=datetime.now(timezone.utc)))

def portal_scope(account_id, *, permission=None):
    """Resolve the portal account's reachable person/household ids.

    When ``permission`` is supplied, only grants that explicitly allow that
    permission contribute to the reachable set (default-deny). This makes the
    permission correlate to the specific grant covering a record rather than
    passing if *any* grant on the account has the permission, and it lets the
    secure-messaging paths enforce ``messages`` consistently (H7)."""
    with engine.connect() as connection:
        grants = connection.execute(select(portal_access_grants).where(portal_access_grants.c.portal_account_id == account_id, _active_grant())).mappings().all()
        if permission is not None:
            grants = [g for g in grants if (g["permissions"] or {}).get(permission)]
        household_ids = {r["household_id"] for r in grants}; person_ids = {r["person_id"] for r in grants if r["person_id"]}
        shared_household_ids = {r["household_id"] for r in grants if r["access_type"] in {"joint", "trusted", "delegated"}}
        if shared_household_ids: person_ids |= set(connection.scalars(select(people.c.id).where(people.c.household_id.in_(shared_household_ids))))
        organization_ids = {r["organization_id"] for r in grants if r["organization_id"]}
    return {"household_ids": household_ids, "shared_household_ids": shared_household_ids, "person_ids": person_ids, "organization_ids": organization_ids, "grants": grants}


def require_org_scope(principal, organization_id, *, permission="benefits"):
    """Employer portal scope: the account must hold a grant for this Organization that
    allows the permission. Out-of-scope raises PermissionError (routes map to 404)."""
    scope = portal_scope(principal.account_id, permission=permission)
    if organization_id not in scope["organization_ids"]:
        raise PermissionError("Organization is outside portal access scope")
    return scope

def require_scope(principal, *, person_id=None, household_id=None, permission=None):
    # Filter reachability by the requested permission first, so membership is
    # only satisfied via a grant that actually allows the action (H7).
    scope = portal_scope(principal.account_id, permission=permission)
    if person_id is not None and person_id not in scope["person_ids"]:
        raise PermissionError("Person is outside portal access scope" if permission is None else f"Portal grant does not allow {permission}")
    if household_id is not None and household_id not in scope["household_ids"]:
        raise PermissionError("Household is outside portal access scope" if permission is None else f"Portal grant does not allow {permission}")
    return scope

def create_thread(principal, *, household_id, person_id, subject, body):
    require_scope(principal, person_id=person_id, household_id=household_id, permission="messages")
    with engine.begin() as connection:
        thread_id = connection.execute(portal_threads.insert().values(household_id=household_id, person_id=person_id, subject=subject, created_by_portal_account_id=principal.account_id).returning(portal_threads.c.id)).scalar_one()
        connection.execute(portal_thread_participants.insert().values(thread_id=thread_id, portal_account_id=principal.account_id, participant_role="client"))
        message_id = connection.execute(portal_messages.insert().values(thread_id=thread_id, sender_portal_account_id=principal.account_id, body=body, visibility="client").returning(portal_messages.c.id)).scalar_one()
    add_timeline_event(person_id=person_id, household_id=household_id, source="client_portal", event_type="secure_message", title="Secure portal message", external_id=f"portal-message-{message_id}", event_metadata={"thread_id": thread_id})
    write_audit_event(action="portal.message.sent", entity_type="portal_message", entity_id=message_id, request_id=f"portal-message-{uuid.uuid4()}", metadata={"portal_account_id": principal.account_id, "thread_id": thread_id})
    return thread_id

def send_message(principal, thread_id, body, attachment_document_ids=None):
    scope = portal_scope(principal.account_id, permission="messages")
    with engine.begin() as connection:
        thread = connection.execute(select(portal_threads).where(portal_threads.c.id == thread_id, or_(portal_threads.c.person_id.in_(scope["person_ids"]), portal_threads.c.household_id.in_(scope["shared_household_ids"])))).mappings().one_or_none()
        if not thread: raise PermissionError("Thread is outside portal access scope")
        message_id = connection.execute(portal_messages.insert().values(thread_id=thread_id, sender_portal_account_id=principal.account_id, body=body, visibility="client").returning(portal_messages.c.id)).scalar_one()
        for document_id in attachment_document_ids or []:
            owner = connection.scalar(select(documents.c.person_id).where(documents.c.id == document_id))
            if owner not in scope["person_ids"]: raise PermissionError("Attachment is outside portal access scope")
            connection.execute(portal_message_attachments.insert().values(message_id=message_id, document_id=document_id))
    add_timeline_event(person_id=thread["person_id"], household_id=thread["household_id"], source="client_portal", event_type="secure_message", title="Secure portal message", external_id=f"portal-message-{message_id}", event_metadata={"thread_id": thread_id})
    return message_id

def staff_send_message(*, thread_id, user_id, body, internal_note=False, attachment_document_ids=None):
    with engine.begin() as connection:
        thread = connection.execute(select(portal_threads).where(portal_threads.c.id == thread_id)).mappings().one_or_none()
        if not thread: raise ValueError("Thread not found")
        message_id = connection.execute(portal_messages.insert().values(thread_id=thread_id, sender_user_id=user_id, body=body, visibility="internal" if internal_note else "client").returning(portal_messages.c.id)).scalar_one()
        for document_id in attachment_document_ids or []: connection.execute(portal_message_attachments.insert().values(message_id=message_id, document_id=document_id))
    if not internal_note:
        add_timeline_event(person_id=thread["person_id"], household_id=thread["household_id"], source="client_portal", event_type="secure_message", title="Secure staff message", external_id=f"portal-message-{message_id}", event_metadata={"thread_id": thread_id})
    write_audit_event(action="portal.internal_note.created" if internal_note else "portal.message.sent", entity_type="portal_message", entity_id=message_id, actor_user_id=user_id, request_id=f"portal-staff-message-{uuid.uuid4()}", metadata={"thread_id": thread_id, "visibility": "internal" if internal_note else "client"})
    return message_id

def list_messages(principal, thread_id):
    scope = portal_scope(principal.account_id, permission="messages")
    with engine.connect() as connection:
        allowed = connection.scalar(select(portal_threads.c.id).where(portal_threads.c.id == thread_id, or_(portal_threads.c.person_id.in_(scope["person_ids"]), portal_threads.c.household_id.in_(scope["shared_household_ids"]))))
        if not allowed: raise PermissionError("Thread is outside portal access scope")
        return connection.execute(select(portal_messages).where(portal_messages.c.thread_id == thread_id, portal_messages.c.visibility == "client").order_by(portal_messages.c.sent_at)).mappings().all()

def mark_read(principal, message_id):
    with engine.begin() as connection:
        thread_id = connection.scalar(select(portal_messages.c.thread_id).where(portal_messages.c.id == message_id, portal_messages.c.visibility == "client"))
        if not thread_id: raise ValueError("Message not found")
        scope = portal_scope(principal.account_id, permission="messages")
        if not connection.scalar(select(portal_threads.c.id).where(portal_threads.c.id == thread_id, or_(portal_threads.c.person_id.in_(scope["person_ids"]), portal_threads.c.household_id.in_(scope["shared_household_ids"])))): raise PermissionError("Message is outside portal access scope")
        existing = connection.scalar(select(portal_message_receipts.c.id).where(portal_message_receipts.c.message_id == message_id, portal_message_receipts.c.portal_account_id == principal.account_id))
        if existing: return existing
        return connection.execute(portal_message_receipts.insert().values(message_id=message_id, portal_account_id=principal.account_id).returning(portal_message_receipts.c.id)).scalar_one()

def create_document_request(*, person_id, household_id, title, requested_by_user_id, description=None, due_date=None, workflow_instance_id=None, workflow_step_id=None):
    with engine.begin() as connection: request_id = connection.execute(portal_document_requests.insert().values(person_id=person_id, household_id=household_id, title=title, description=description, due_date=due_date, workflow_instance_id=workflow_instance_id, workflow_step_id=workflow_step_id, requested_by_user_id=requested_by_user_id).returning(portal_document_requests.c.id)).scalar_one()
    add_timeline_event(person_id=person_id, household_id=household_id, source="client_portal", event_type="document_requested", title=title, external_id=f"portal-document-request-{request_id}")
    return request_id

def confirm_request_upload(principal, request_id, document_id):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        request = connection.execute(select(portal_document_requests).where(portal_document_requests.c.id == request_id).with_for_update()).mappings().one_or_none()
        if not request: raise ValueError("Document request not found")
        require_scope(principal, person_id=request["person_id"], household_id=request["household_id"], permission="documents")
        if connection.scalar(select(documents.c.person_id).where(documents.c.id == document_id)) != request["person_id"]: raise PermissionError("Document owner does not match request")
        version = (connection.scalar(select(func.max(document_versions.c.version_number)).where(document_versions.c.document_id == document_id)) or 0) + 1
        connection.execute(document_versions.insert().values(document_id=document_id, version_number=version, uploaded_by_portal_account_id=principal.account_id))
        connection.execute(portal_document_requests.update().where(portal_document_requests.c.id == request_id).values(uploaded_document_id=document_id, uploaded_at=now, status="uploaded", updated_at=now))
    add_timeline_event(person_id=request["person_id"], household_id=request["household_id"], source="client_portal", event_type="document_uploaded", title="Requested document uploaded", external_id=f"portal-request-upload-{request_id}-{document_id}")
    from app.db import tax_checklist_items
    with engine.connect() as connection:
        tax_return_id = connection.scalar(select(tax_checklist_items.c.tax_engagement_return_id).where(tax_checklist_items.c.portal_document_request_id == request_id))
    if tax_return_id:
        from app.services.tax_intake import sync_documents
        sync_documents(tax_return_id)
    return version

def approve_request_upload(request_id, *, approved_by_user_id, approved=True):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        request = connection.execute(select(portal_document_requests).where(portal_document_requests.c.id == request_id).with_for_update()).mappings().one_or_none()
        if not request or request["status"] != "uploaded": raise ValueError("Uploaded document request not found")
        status = "approved" if approved else "rejected"
        connection.execute(portal_document_requests.update().where(portal_document_requests.c.id == request_id).values(status=status, approved_by_user_id=approved_by_user_id, approved_at=now, updated_at=now))
    add_timeline_event(person_id=request["person_id"], household_id=request["household_id"], source="client_portal", event_type=f"document_{status}", title=f"Requested document {status}", external_id=f"portal-request-{status}-{request_id}")
    write_audit_event(action=f"portal.document.{status}", entity_type="portal_document_request", entity_id=request_id, actor_user_id=approved_by_user_id, request_id=f"portal-document-{uuid.uuid4()}")
    return status

def client_tasks(principal, scope=None):
    scope = scope or portal_scope(principal.account_id)
    with engine.connect() as connection:
        rows = connection.execute(select(workflow_steps, workflow_instances.c.name.label("workflow_name")).join(workflow_instances).where(or_(workflow_instances.c.person_id.in_(scope["person_ids"]), workflow_instances.c.household_id.in_(scope["shared_household_ids"])), workflow_steps.c.status.in_(("active", "pending")))).mappings().all()
    return [r for r in rows if r["waiting_on"] == "client" or (r["definition_snapshot"] or {}).get("assignment_config", {}).get("audience") == "client"]

def complete_client_task(principal, step_id):
    allowed = next((r for r in client_tasks(principal) if r["id"] == step_id), None)
    if not allowed: raise PermissionError("Task is outside portal scope or not client-facing")
    complete_step(step_id, actor_user_id=None, request_id=f"portal-task-{uuid.uuid4()}")
    write_audit_event(action="portal.task.completed", entity_type="workflow_step", entity_id=step_id, request_id=f"portal-task-{uuid.uuid4()}", metadata={"portal_account_id": principal.account_id})

def notify(account_id, notification_type, title, body=None, *, channel="in_app", entity_type=None, entity_id=None, idempotency_key):
    provider = NOTIFICATION_PROVIDERS.get(channel)
    if not provider: raise ValueError("Unsupported notification channel")
    with engine.begin() as connection:
        existing = connection.scalar(select(portal_notifications.c.id).where(portal_notifications.c.idempotency_key == idempotency_key))
        if existing: return existing
        result = provider.deliver(recipient=account_id, title=title, body=body, metadata={"entity_type": entity_type, "entity_id": entity_id})
        return connection.execute(portal_notifications.insert().values(portal_account_id=account_id, channel=channel, notification_type=notification_type, title=title, body=body, status="delivered" if result["delivered"] else "disabled", entity_type=entity_type, entity_id=entity_id, idempotency_key=idempotency_key, delivery_metadata=result).returning(portal_notifications.c.id)).scalar_one()

def client_document_requests(principal, scope=None):
    scope = scope or portal_scope(principal.account_id)
    with engine.connect() as connection:
        return connection.execute(select(portal_document_requests).where(portal_document_requests.c.person_id.in_(scope["person_ids"]), portal_document_requests.c.status.in_(("open", "uploaded"))).order_by(portal_document_requests.c.due_date)).mappings().all()

def client_notifications(principal):
    with engine.connect() as connection:
        return connection.execute(select(portal_notifications).where(portal_notifications.c.portal_account_id == principal.account_id).order_by(portal_notifications.c.created_at.desc()).limit(20)).mappings().all()

def client_threads(principal, scope=None):
    scope = scope or portal_scope(principal.account_id)
    with engine.connect() as connection:
        return connection.execute(select(portal_threads).where(or_(portal_threads.c.person_id.in_(scope["person_ids"]), portal_threads.c.household_id.in_(scope["shared_household_ids"]))).order_by(portal_threads.c.updated_at.desc()).limit(20)).mappings().all()

def client_documents(principal, scope=None):
    scope = scope or portal_scope(principal.account_id)
    with engine.connect() as connection:
        return connection.execute(select(documents).where(documents.c.person_id.in_(scope["person_ids"]), documents.c.archived.is_(False)).order_by(documents.c.created_at.desc()).limit(20)).mappings().all()

def client_action_needed(principal, scope=None, *, include_resolved=False):
    """Client-safe "action needed" items (client-visible tax exceptions) within the
    portal account's scope. Projection + allowlist live in the canonical Exception
    Engine; this only bridges portal scope to it."""
    scope = scope or portal_scope(principal.account_id)
    from app.services.exception_engine import client_action_items
    return client_action_items(scope, include_resolved=include_resolved)

def client_action_detail(principal, exception_id):
    """One client-visible exception by id, portal-scoped. Raises
    ExceptionNotFoundError for anything out-of-scope / not client-visible."""
    scope = portal_scope(principal.account_id)
    from app.services.exception_engine import client_action_item
    return client_action_item(scope, exception_id)


# --- employer portal (Release 0.9.11, Phase 7) -------------------------------

def employer_organization_ids(principal):
    return sorted(portal_scope(principal.account_id, permission="benefits")["organization_ids"])


def employer_action_needed(principal, scope=None, *, include_resolved=False):
    """Employer-safe "action needed" items (employer-visible benefits exceptions) for the
    portal account's organizations. Projection + allowlist live in the Exception Engine."""
    scope = scope or portal_scope(principal.account_id, permission="benefits")
    from app.services.exception_engine import employer_action_items
    return employer_action_items(scope, include_resolved=include_resolved)


def employer_action_detail(principal, exception_id):
    scope = portal_scope(principal.account_id, permission="benefits")
    from app.services.exception_engine import employer_action_item
    return employer_action_item(scope, exception_id)


def employer_census_upload(principal, organization_id, *, original_name, source, content_type=None):
    """Employer self-service census upload: stores the file as a document (on the HR contact)
    and links it to the Organization as a census document — which clears the census-overdue
    exception on the next scan. Reuses the existing document store; no new document system."""
    require_org_scope(principal, organization_id, permission="census")
    from app.db import benefit_document_links
    from app.services.documents import save_person_document
    document_id = save_person_document(person_id=principal.person_id, original_name=original_name or "census.csv",
                                       source=source, content_type=content_type, category="benefits_census",
                                       description="Employer census upload", uploaded_by=principal.display_name)
    with engine.begin() as connection:
        connection.execute(benefit_document_links.insert().values(
            document_id=document_id, organization_id=organization_id, doc_kind="census"))
    write_audit_event(action="benefits.employer.census_uploaded", entity_type="organization",
                      entity_id=organization_id, request_id=f"portal-census-{uuid.uuid4()}",
                      metadata={"portal_account_id": principal.account_id, "document_id": document_id})
    return document_id


def notify_employer(account_id, *, title, body, entity_type=None, entity_id=None, idempotency_key):
    """Auditable employer notification via the existing provider/outcome architecture. Records
    an honest sent/disabled outcome; carries no sensitive employee/EIN/compensation data."""
    return notify(account_id, "benefits_action", title, body=body, channel="in_app",
                  entity_type=entity_type, entity_id=entity_id, idempotency_key=idempotency_key)

def dashboard(principal):
    scope = portal_scope(principal.account_id); now = datetime.now(timezone.utc)
    # Narrow endpoints (/documents, /requests, /tasks, /notifications, /messages)
    # reuse the single-purpose function they need instead of computing the whole
    # dashboard (RC8/RC9). Output is unchanged.
    requests = client_document_requests(principal, scope)
    notifications = client_notifications(principal)
    threads = client_threads(principal, scope)
    docs = client_documents(principal, scope)
    with engine.connect() as connection:
        meetings = connection.execute(select(timeline_events).where(or_(timeline_events.c.person_id.in_(scope["person_ids"]), timeline_events.c.household_id.in_(scope["shared_household_ids"])), timeline_events.c.event_type == "calendar_event", timeline_events.c.event_time >= now).order_by(timeline_events.c.event_time).limit(20)).mappings().all()
    tasks = client_tasks(principal, scope)
    from app.services.tax_intake import portal_intakes
    from app.services.tax_return_lifecycle import portal_returns
    tax_intakes = portal_intakes(principal, scope)
    tax_returns = portal_returns(principal, scope)
    action_items = client_action_needed(principal, scope)
    employer_actions = employer_action_needed(principal)
    is_employer = bool(employer_organization_ids(principal))
    from app.services.insurance_portal import portal_policies  # local import avoids a cycle
    insurance_policies = portal_policies(principal)  # own insurance-permission scope (opt-in)
    return {"tasks": tasks, "document_requests": requests, "messages": threads, "notifications": notifications, "documents": docs, "meetings": meetings, "tax_intakes": tax_intakes, "tax_returns": tax_returns, "action_items": action_items, "employer_action_items": employer_actions, "is_employer": is_employer, "insurance_policies": insurance_policies, "workflow_progress": [{"name": r["workflow_name"], "step": r["name"], "status": r["status"]} for r in tasks]}
