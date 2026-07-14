"""Release 0.9.7 authorization regression tests.

Negative (default-deny) coverage for every confirmed RC9 High-priority finding
remediated in this release, plus positive controls (compliance approval mapping,
firm-wide bypass) and explicit 403/404 assertions.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import (
    capabilities, engine, households, people, portal_accounts, portal_messages,
    portal_threads, record_assignments, relationship_entities, relationships,
    relationship_types, roles, tasks, users,
)
from app.security.authorization import accessible_person_ids, record_in_scope
from app.security.models import Principal
from app.services.identity import assign_record, assign_role, compose_role
from app.services.work_management import (
    assign_work, authorize_assignment_target, authorize_existing_assignment,
)


def _req(principal=None):
    return SimpleNamespace(
        state=SimpleNamespace(request_id="test-" + uuid.uuid4().hex, principal=principal),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
    )


def _user(label):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{label}-{suffix}@example.com", normalized_email=f"{label}-{suffix}@example.com",
            display_name=label.title(), auth_subject=f"{label}-{suffix}", status="active",
        ).returning(users.c.id)).scalar_one()


def _person(name="Client"):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        household_id = c.execute(households.insert().values(name=f"HH {suffix}").returning(households.c.id)).scalar_one()
        person_id = c.execute(people.insert().values(household_id=household_id, full_name=f"{name} {suffix}", active=True).returning(people.c.id)).scalar_one()
    return person_id, household_id


# --- H1: work assignment privilege escalation -------------------------------

def test_advisor_self_assignment_to_client_record_is_denied():
    actor = _user("advisor")
    person_id, _ = _person()
    advisor = Principal(actor, "a@example.com", "Advisor", frozenset({"work.write"}))
    with pytest.raises(PermissionError):
        authorize_assignment_target(advisor, "person", person_id)


def test_cross_client_assignment_requires_scope_even_with_assignment_manage():
    actor = _user("ops")
    person_id, _ = _person()
    # Has assignment.manage but no scope over this specific record.
    ops = Principal(actor, "o@example.com", "Ops", frozenset({"work.write", "assignment.manage"}))
    with pytest.raises(PermissionError):
        authorize_assignment_target(ops, "person", person_id)
    # Grant scope -> now allowed.
    assign_record(actor, "person", person_id, "primary")
    authorize_assignment_target(ops, "person", person_id)  # does not raise


def test_api_assign_route_returns_403_on_escalation():
    from app.routes.work import AssignmentCreate, api_assign
    actor = _user("advisor")
    person_id, _ = _person()
    advisor = Principal(actor, "a@example.com", "Advisor", frozenset({"work.write"}))
    payload = AssignmentCreate(entity_type="person", entity_id=person_id, assignment_role="secondary", user_id=actor)
    with pytest.raises(HTTPException) as exc:
        api_assign(payload, _req(), advisor)
    assert exc.value.status_code == 403


# --- H8: work assignment reassign/remove IDOR -------------------------------

def test_advisor_cannot_remove_another_users_assignment():
    from app.routes.work import api_remove
    owner = _user("owner"); intruder = _user("intruder")
    person_id, _ = _person()
    assignment_id = assign_work(entity_type="task", entity_id=_task(person_id), assignment_role="primary", user_id=owner, actor_user_id=owner, request_id="seed")
    principal = Principal(intruder, "i@example.com", "Intruder", frozenset({"work.write"}))
    with pytest.raises(HTTPException) as exc:
        api_remove(assignment_id, _req(), principal)
    assert exc.value.status_code == 403


def test_assignment_owner_can_manage_own_assignment():
    owner = _user("owner")
    person_id, _ = _person()
    assignment_id = assign_work(entity_type="task", entity_id=_task(person_id), assignment_role="primary", user_id=owner, actor_user_id=owner, request_id="seed")
    principal = Principal(owner, "o@example.com", "Owner", frozenset({"work.write"}))
    assert authorize_existing_assignment(principal, assignment_id) is not None


def _task(person_id):
    with engine.begin() as c:
        return c.execute(tasks.insert().values(person_id=person_id, title="T", status="open", priority="normal").returning(tasks.c.id)).scalar_one()


# --- H2: role composition / self-escalation ---------------------------------

def _role_and_cap(role_code, cap_code):
    with engine.connect() as c:
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == role_code))
        cap_id = c.scalar(select(capabilities.c.id).where(capabilities.c.code == cap_code))
    return role_id, cap_id


def test_role_manage_cannot_grant_capability_it_does_not_hold():
    advisor_role, identity_cap = _role_and_cap("advisor", "identity.manage")
    with pytest.raises(PermissionError):
        compose_role(advisor_role, [identity_cap], actor_capabilities=frozenset({"role.manage"}))


def test_administrator_role_is_protected_from_recomposition():
    admin_role, read_cap = _role_and_cap("administrator", "client.read")
    with pytest.raises(PermissionError):
        compose_role(admin_role, [read_cap], actor_capabilities=frozenset({"role.manage", "client.read"}))


def test_cannot_assign_role_more_powerful_than_actor():
    admin_role, _ = _role_and_cap("administrator", "client.read")
    target = _user("victim")
    with pytest.raises(PermissionError):
        assign_role(target, admin_role, actor_capabilities=frozenset({"role.manage"}))


# --- H3: tax review / correction IDOR ---------------------------------------

def _engagement():
    from app.services.tax_domain import create_engagement
    actor = _user("preparer")
    person_id, household_id = _person()
    result = create_engagement({"tax_year": 2026, "return_type": "1040", "filing_status": "single",
        "person_id": person_id, "household_id": household_id, "assignee_user_id": actor},
        actor_user_id=actor, request_id=f"eng-{uuid.uuid4().hex[:8]}")
    return actor, result["return_id"]


def test_tax_review_decision_out_of_scope_returns_404():
    from app.routes.tax_returns import api_review_decision, ReviewDecision
    from app.services.tax_return_lifecycle import request_review
    actor, return_id = _engagement()
    reviewer = _user("reviewer")  # distinct from requester (segregation of duties)
    review_id = request_review(return_id, "manager", requested_by_user_id=actor, reviewer_user_id=reviewer)
    outsider = Principal(_user("outsider"), "x@example.com", "Outsider", frozenset({"tax.review"}))
    with pytest.raises(HTTPException) as exc:
        api_review_decision(review_id, ReviewDecision(decision="approved"), _req(), outsider)
    assert exc.value.status_code == 404


def test_tax_correction_resolve_out_of_scope_returns_404():
    from app.routes.tax_returns import api_resolve
    from app.services.tax_return_lifecycle import request_review, decide_review, transition_return
    from app.db import tax_review_corrections
    actor, return_id = _engagement()
    reviewer = _user("reviewer")
    transition_return(return_id, "ready_to_prepare", actor_user_id=actor)
    transition_return(return_id, "in_preparation", actor_user_id=actor)
    transition_return(return_id, "manager_review", actor_user_id=actor)
    review_id = request_review(return_id, "manager", requested_by_user_id=actor, reviewer_user_id=reviewer)
    decide_review(review_id, "returned", reviewer_user_id=reviewer, corrections=["Fix basis"])
    with engine.connect() as c:
        correction_id = c.scalar(select(tax_review_corrections.c.id).where(tax_review_corrections.c.tax_return_review_id == review_id))
    outsider = Principal(_user("outsider"), "x@example.com", "Outsider", frozenset({"tax.write"}))
    with pytest.raises(HTTPException) as exc:
        api_resolve(correction_id, outsider)
    assert exc.value.status_code == 404


# --- H4: compliance workflow approval capability mapping --------------------

def test_workflow_approval_maps_to_work_approve_not_work_write():
    from app.security.middleware import RULES
    def capability_for(path):
        cap = next((code for pattern, code in RULES if pattern.search(path)), None)
        return cap.replace(".read", ".write") if cap else None  # POST inference
    assert capability_for("/api/v1/workflows/approvals/5/decision") == "work.approve"
    # A generic workflow mutation still requires work.write.
    assert capability_for("/api/v1/workflows/9/pause") == "work.write"


def test_tax_review_routes_map_to_tax_review_not_tax_write():
    from app.security.middleware import RULES
    def capability_for(path):
        cap = next((code for pattern, code in RULES if pattern.search(path)), None)
        return cap.replace(".read", ".write") if cap else None
    assert capability_for("/api/v1/tax/returns/reviews/3/decision") == "tax.review"
    assert capability_for("/api/v1/tax/returns/7/reviews") == "tax.review"
    # Correction resolution stays on tax.write.
    assert capability_for("/api/v1/tax/returns/review-corrections/2/resolve") == "tax.write"


# --- H5: relationship deactivation IDOR -------------------------------------

def _relationship(owner_person_id):
    with engine.begin() as c:
        entity_id = c.execute(relationship_entities.insert().values(entity_type="person", person_id=owner_person_id, name="Owner Entity").returning(relationship_entities.c.id)).scalar_one()
        rtype = c.scalar(select(relationship_types.c.id).limit(1))
        rel_id = c.execute(relationships.insert().values(from_entity_id=entity_id, to_entity_id=entity_id, relationship_type_id=rtype, active=True).returning(relationships.c.id)).scalar_one()
    return rel_id


def test_relationship_deactivation_denied_for_unauthorized_owner():
    from app.routes.relationships import end_relationship
    owner_person, _ = _person("Owner")
    other_person, _ = _person("Other")
    rel_id = _relationship(owner_person)
    actor = _user("advisor")
    assign_record(actor, "person", other_person, "primary")  # scoped to a different client
    principal = Principal(actor, "a@example.com", "Advisor", frozenset({"client.write"}))
    response = end_relationship(rel_id, other_person, _req(principal))
    assert response.status_code == 403


def test_relationship_deactivation_allowed_for_record_owner():
    from app.routes.relationships import end_relationship
    owner_person, _ = _person("Owner")
    rel_id = _relationship(owner_person)
    actor = _user("advisor")
    assign_record(actor, "person", owner_person, "primary")
    principal = Principal(actor, "a@example.com", "Advisor", frozenset({"client.write"}))
    response = end_relationship(rel_id, owner_person, _req(principal))
    assert response.status_code == 303  # redirect on success


# --- H6: client enumeration --------------------------------------------------

def test_accessible_person_ids_scopes_to_assigned_records():
    actor = _user("advisor")
    mine, _ = _person("Mine")
    theirs, _ = _person("Theirs")
    assign_record(actor, "person", mine, "primary")
    principal = Principal(actor, "a@example.com", "Advisor", frozenset({"client.read"}))
    with engine.connect() as c:
        allowed = accessible_person_ids(c, principal)
    assert mine in allowed
    assert theirs not in allowed


def test_record_read_all_sees_all_people():
    principal = Principal(_user("admin"), "admin@example.com", "Admin", frozenset({"record.read_all"}))
    with engine.connect() as c:
        assert accessible_person_ids(c, principal) is None


# --- H7: portal secure messaging denial -------------------------------------

def _portal_account(person_id, household_id, messages):
    from app.portal.service import invite_portal_account
    actor = _user("staff")
    account, _ = invite_portal_account(person_id=person_id, household_id=household_id,
        email=f"client-{uuid.uuid4().hex[:8]}@example.com", display_name="Client", access_type="self",
        invited_by_user_id=actor, permissions={"tasks": True, "documents": True, "messages": messages})
    return account


def test_portal_message_read_denied_without_messages_permission():
    from app.portal.service import PortalPrincipal, list_messages
    person_id, household_id = _person("Portal")
    account = _portal_account(person_id, household_id, messages=False)
    with engine.begin() as c:
        thread_id = c.execute(portal_threads.insert().values(person_id=person_id, household_id=household_id, subject="Secure", created_by_portal_account_id=account).returning(portal_threads.c.id)).scalar_one()
        c.execute(portal_messages.insert().values(thread_id=thread_id, sender_portal_account_id=account, body="hi", visibility="client"))
    principal = PortalPrincipal(account, person_id, "c@example.com", "Client")
    with pytest.raises(PermissionError):
        list_messages(principal, thread_id)


def test_portal_message_read_allowed_with_messages_permission():
    from app.portal.service import PortalPrincipal, list_messages
    person_id, household_id = _person("Portal")
    account = _portal_account(person_id, household_id, messages=True)
    with engine.begin() as c:
        thread_id = c.execute(portal_threads.insert().values(person_id=person_id, household_id=household_id, subject="Secure", created_by_portal_account_id=account).returning(portal_threads.c.id)).scalar_one()
        c.execute(portal_messages.insert().values(thread_id=thread_id, sender_portal_account_id=account, body="hi", visibility="client"))
    principal = PortalPrincipal(account, person_id, "c@example.com", "Client")
    assert list_messages(principal, thread_id) is not None


# --- H9: reminders scope -----------------------------------------------------

def test_reminder_trigger_denied_without_firm_wide_authority():
    from app.routes.tax_intake import api_reminders
    principal = Principal(_user("office"), "o@example.com", "Office", frozenset({"tax.intake.write"}))
    with pytest.raises(HTTPException) as exc:
        api_reminders(_req(), principal)
    assert exc.value.status_code == 403


def test_reminder_trigger_allowed_with_record_read_all():
    from app.routes.tax_intake import api_reminders
    principal = Principal(_user("firm"), "f@example.com", "Firm", frozenset({"tax.intake.write", "record.read_all"}))
    result = api_reminders(_req(), principal)
    assert "sent" in result
