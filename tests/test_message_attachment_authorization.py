"""Who can actually fetch a secure-message attachment — pinned for every Messages role.

Batch 3b shipped vault-backed client attachments and reported the gap this file now closes and
records: the Messages roles and the vault-download roles are different sets.

  administrator  senior_tax  tax_staff   -> already held vault.download
  client_service                          -> GRANTED here (vaultdl01)
  advisor        operations               -> STILL BLOCKED, deliberately

client_service already held ``vault.view`` + ``vault.category.general``, so it could list and open
the metadata of exactly these documents (client attachments are uploaded as ``category='general'``)
and was refused only the bytes. The grant converts existing VIEW access into FETCH access over the
SAME set — it adds no category, no record scope and no other capability, which is what
``test_the_grant_widens_nothing_but_the_door`` proves.

``advisor`` and ``operations`` hold NO vault capability at all. Making downloads work for them would
mean granting ``vault.view`` + a category + ``vault.download``, newly exposing the whole general
vault category — for advisor, firm-wide, since it holds ``record.read_all``. That is a business
decision about who may read client documents, not an engineering one, so they remain blocked and
this file says so out loud rather than hiding it under a privileged principal.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import capabilities, engine, role_capabilities, roles, vault_documents
from app.portal import vault_documents as portal_vault
from app.portal.service import create_thread, send_message
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.vault import service as vault
from app.services.vault.service import VaultPermissionError
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("production_identity_provider")

MESSAGES_ROLES = ("administrator", "senior_tax", "tax_staff", "client_service",
                  "advisor", "operations")
#: The route door. The service behind it re-checks category and record scope independently.
DOWNLOAD_CAPABILITY = "vault.download"


@pytest.fixture
def gates(portal_master_on):
    portal_master_on.update({"portal.messaging_enabled", "portal.documents.upload_enabled",
                             "portal.documents.download_enabled"})
    return portal_master_on


def _role_caps(role_code):
    with engine.connect() as c:
        return set(c.scalars(select(capabilities.c.code).select_from(
            roles.join(role_capabilities, role_capabilities.c.role_id == roles.c.id)
                 .join(capabilities, capabilities.c.id == role_capabilities.c.capability_id))
            .where(roles.c.code == role_code)))


def _principal_for(role_code, uid=None):
    """A principal carrying exactly the seeded capability set of that production role."""
    return Principal(uid or seed_staff_user(), f"{role_code}@example.com", role_code,
                     frozenset(_role_caps(role_code)))


def _assign(staff_uid, person_id):
    """Give the staff user real RECORD SCOPE on this client, the way the firm actually does it.

    None of these roles hold ``record.read_all``, so without an assignment the vault would refuse
    them on scope and a passing test would prove nothing about the capability under examination."""
    from datetime import date

    from app.db import record_assignments
    with engine.begin() as c:
        c.execute(record_assignments.insert().values(
            entity_type="person", entity_id=person_id, user_id=staff_uid,
            assignment_type="owner", effective_date=date.today()))


def _client_attachment(gates_unused=None):
    """A real client-uploaded message attachment: vault document + message + link."""
    staff_uid = seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    _assign(staff_uid, person_id)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Docs", body="Opening")
    vault_id = portal_vault.upload_document(
        principal, source=io.BytesIO(b"%PDF-1.4 attachment"),
        original_filename=f"w2-{uuid.uuid4().hex[:8]}.pdf", display_name="W-2")
    send_message(principal, thread_id, "Attached", attachment_vault_document_ids=[vault_id])
    return principal, person_id, thread_id, vault_id, staff_uid


# --- 1. the matrix, pinned per role ------------------------------------------

@pytest.mark.parametrize("role", MESSAGES_ROLES)
def test_every_messages_role_holds_the_messages_capability(role):
    assert "communications.message.read" in _role_caps(role), role


@pytest.mark.parametrize("role,expected", [
    ("administrator", True), ("senior_tax", True), ("tax_staff", True),
    ("client_service", True),                      # granted by vaultdl01
    ("advisor", False), ("operations", False),     # deliberately still blocked
])
def test_the_download_door_matches_the_intended_matrix(role, expected):
    assert (DOWNLOAD_CAPABILITY in _role_caps(role)) is expected, role


@pytest.mark.parametrize("role", ("advisor", "operations"))
def test_the_blocked_roles_hold_no_vault_capability_at_all(role):
    """Why they are not simply granted the door: there is nothing to open. Enabling them means
    granting view + a category as well, which newly exposes the whole general vault category."""
    assert not [c for c in _role_caps(role) if c.startswith("vault.")], role


def test_the_grant_widens_nothing_but_the_door():
    """The safety argument for vaultdl01, asserted rather than described: client_service gains the
    download capability and NOTHING else — no category, no scope, no upload, no manage."""
    caps = _role_caps("client_service")
    assert DOWNLOAD_CAPABILITY in caps
    assert {c for c in caps if c.startswith("vault.category.")} == {"vault.category.general"}
    for never in ("vault.access.all", "vault.upload", "vault.manage",
                  "record.read_all", "record.write_all"):
        assert never not in caps, never


def test_no_other_role_gained_the_capability():
    """vaultdl01 touches one role. advisor and operations must be exactly as they were."""
    for role in ("advisor", "operations", "reviewer", "read_only"):
        assert DOWNLOAD_CAPABILITY not in _role_caps(role), role


# --- 2. effective behaviour, not just the grant -------------------------------

def test_client_service_can_now_fetch_a_message_attachment(gates):
    """End to end for the role this batch unblocks: the door opens AND the service authorizes."""
    _, _, _, vault_id, staff_uid = _client_attachment()
    coordinator = _principal_for("client_service", staff_uid)

    require_capability(DOWNLOAD_CAPABILITY)(principal=coordinator)      # door: passes
    path, filename, _mime = vault.download_target(coordinator, vault_id)
    assert path.is_file() and filename


@pytest.mark.parametrize("role", ("advisor", "operations"))
def test_the_blocked_roles_are_refused_at_the_door(gates, role):
    """BLOCKED, recorded. They can open the conversation; they cannot fetch the file."""
    _, _, _, vault_id, staff_uid = _client_attachment()
    blocked = _principal_for(role, staff_uid)

    with pytest.raises(HTTPException) as excinfo:
        require_capability(DOWNLOAD_CAPABILITY)(principal=blocked)
    assert excinfo.value.status_code == 403
    with pytest.raises(VaultPermissionError):        # and the service refuses them too
        vault.download_target(blocked, vault_id)


def test_the_category_gate_still_applies_after_the_grant(gates):
    """client_service holds only vault.category.general. A tax-category document stays refused, so
    the grant did not turn the coordinator into a firm-wide document reader."""
    _, _, _, vault_id, staff_uid = _client_attachment()
    with engine.begin() as c:
        c.execute(vault_documents.update().where(
            vault_documents.c.id == vault_id).values(category="tax"))

    with pytest.raises(VaultPermissionError):
        vault.download_target(_principal_for("client_service", staff_uid), vault_id)


def test_record_scope_still_applies_after_the_grant(gates):
    """A coordinator with the SAME capabilities but no assignment to this client is refused. The
    grant opened a door; it did not make every client's documents reachable."""
    _, _, _, vault_id, _ = _client_attachment()
    outsider = _principal_for("client_service")          # a different user, no record assignment

    with pytest.raises(VaultPermissionError):
        vault.download_target(outsider, vault_id)


def test_a_guessed_vault_id_is_still_insufficient(gates):
    coordinator = _principal_for("client_service")
    with pytest.raises(Exception):
        vault.download_target(coordinator, 999_999_999)


def test_another_client_still_cannot_download_it(gates):
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, _, _, vault_id, _ = _client_attachment()
    with pytest.raises(PermissionError):
        portal_vault.download_document(alice, vault_id)


def test_the_download_is_still_audited(gates):
    """The grant changes who may download, never whether a download is recorded."""
    from app.db import vault_document_audit_events

    _, _, _, vault_id, staff_uid = _client_attachment()
    coordinator = _principal_for("client_service", staff_uid)
    with engine.connect() as c:
        before = len(c.execute(select(vault_document_audit_events).where(
            vault_document_audit_events.c.document_id == vault_id,
            vault_document_audit_events.c.action == "download")).all())

    vault.download_target(coordinator, vault_id, actor_user_id=coordinator.user_id)

    with engine.connect() as c:
        after = c.execute(select(vault_document_audit_events).where(
            vault_document_audit_events.c.document_id == vault_id,
            vault_document_audit_events.c.action == "download")).mappings().all()
    assert len(after) == before + 1
    assert after[-1]["user_id"] == coordinator.user_id


def test_the_library_and_the_seed_agree():
    """role_library is the single source of truth for what a profile ends up holding; the migration
    and the library must not drift (tests/test_production_role_library.py folds this in)."""
    from app.security.role_library import POST_SEED_GRANTS, effective_capabilities

    assert DOWNLOAD_CAPABILITY in POST_SEED_GRANTS["client_service"]
    assert DOWNLOAD_CAPABILITY in effective_capabilities("client_service")
    assert DOWNLOAD_CAPABILITY not in effective_capabilities("read_only")
