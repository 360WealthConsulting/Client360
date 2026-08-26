"""HTTP-level portal download isolation — both API versions (P0 regression).

``GET /api/v1/portal/documents/{id}/download`` read the CANONICAL staff ``documents`` table and
authorized on ``documents.person_id`` + ``require_scope`` alone. That table has no ``client_visible``
column, so a client holding a documents grant could download any non-archived canonical document filed
against their own person — internal workpapers included — and the download was never audited. The
``88305f5`` isolation fix corrected listing but not this route, and nothing caught it because every
existing isolation test drives the vault SERVICE; no test exercised the route.

These tests are therefore deliberately route-level. ``test_a_canonical_staff_document_is_blocked_v1``
fails against the pre-fix implementation.

They also pin the denial contract the compliance criterion requires: every resource-level failure is an
identical generic 404, so "does not exist" and "exists but is not yours" are externally
indistinguishable, while the surface gate stays 403 (feature unavailable is a different fact).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, insert, select

from app.db import audit_events, documents, engine, vault_documents
from app.routes.portal import api_portal_document_download
from app.routes.portal_api import api_download_document
from app.services.vault import service as vault
from tests._portal_util import fake_request
from tests.test_portal_vault import _Env

pytestmark = pytest.mark.usefixtures("portal_documents_download_on")

GENERIC = "Document not found"

#: Must never appear in a denial body.
INTERNAL = ("storage_path", "storage_uri", "stored_name", "sha256", "storage_key", "/srv/",
            "sharepoint", "person_id", "household_id", "organization_id", "Traceback",
            "outside portal access scope", "not available to this portal account")


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def _canonical_doc(person_id, name="INTERNAL-WORKPAPER.pdf"):
    """A canonical staff document — exactly what must NOT be downloadable by a client.

    ``documents`` and ``vault_documents`` have independent id sequences, so a canonical id can collide
    with an unrelated vault id on a populated database. The point of this fixture is a canonical row
    with NO vault twin, so insert until that holds rather than assuming it."""
    for _ in range(25):
        with engine.begin() as c:
            doc_id = c.execute(insert(documents).values(
                person_id=person_id, original_name=name, stored_name=f"{uuid.uuid4().hex}.pdf",
                storage_path=f"/srv/docs/{uuid.uuid4().hex}.pdf", size_bytes=11,
                sha256=uuid.uuid4().hex * 2, content_type="application/pdf",
                archived=False, status="active", uploaded_by="staff@firm.test",
            ).returning(documents.c.id)).scalar_one()
        with engine.connect() as c:
            twin = c.scalar(select(func.count()).select_from(vault_documents)
                            .where(vault_documents.c.id == doc_id))
        if not twin:
            return doc_id
    raise AssertionError("could not allocate a canonical document id without a vault twin")


def _downloadable(env, person_id):
    """A properly exposed vault document: client_visible + approved + linked to person_id."""
    doc_id = env.staff_doc(person_id, client_visible=True)
    vault.update_metadata(env.staff, doc_id, changes={"status": "approved"},
                          actor_user_id=env.user_id)
    return doc_id


def _v1(principal, document_id):
    return api_portal_document_download(fake_request(), document_id, principal=principal)


def _v0(principal, document_id):
    return api_download_document(fake_request(), document_id, principal=principal)


def _denied(fn, principal, document_id):
    """Call a download route expecting denial; return (status, detail)."""
    with pytest.raises(HTTPException) as exc:
        fn(principal, document_id)
    return exc.value.status_code, exc.value.detail


def _audits(action, document_id):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == action,
            audit_events.c.entity_id == str(document_id))).scalar_one()


# --- A/B. the canonical bypass, on both versions -----------------------------

def test_a_canonical_staff_document_is_blocked_v1(env):
    """THE REGRESSION: a canonical doc on the client's OWN person must not download."""
    _, principal, pid, _ = env.account()
    doc_id = _canonical_doc(pid)
    with engine.connect() as c:
        assert c.scalar(select(documents.c.person_id).where(documents.c.id == doc_id)) == pid
        assert c.scalar(select(func.count()).select_from(vault_documents)
                        .where(vault_documents.c.id == doc_id)) == 0      # not a vault document

    status, detail = _denied(_v1, principal, doc_id)
    assert status == 404, "canonical staff document was reachable through the v1 portal download"
    assert detail == GENERIC


def test_b_canonical_staff_document_is_blocked_v0(env):
    _, principal, pid, _ = env.account()
    doc_id = _canonical_doc(pid)
    assert _denied(_v0, principal, doc_id) == (404, GENERIC)


def test_a_and_b_agree_on_the_canonical_document(env):
    _, principal, pid, _ = env.account()
    doc_id = _canonical_doc(pid)
    assert _denied(_v1, principal, doc_id) == _denied(_v0, principal, doc_id)


# --- C. the legitimate path still works on both versions ---------------------

def test_c_client_visible_vault_document_downloads_on_both_versions(env):
    _, principal, pid, _ = env.account()
    doc_id = _downloadable(env, pid)

    r1 = _v1(principal, doc_id)
    assert getattr(r1, "status_code", 200) == 200
    assert r1.path and str(r1.path).endswith(".pdf")

    r0 = _v0(principal, doc_id)
    assert getattr(r0, "status_code", 200) == 200
    assert str(r0.path) == str(r1.path), "v0 and v1 delivered different bytes for the same document"


# --- D. cross-client ----------------------------------------------------------

def test_d_cross_client_download_is_denied_on_both_versions(env):
    _, principal_a, _pid_a, _ = env.account()
    _, _principal_b, pid_b, _ = env.account()
    b_doc = _downloadable(env, pid_b)

    assert _denied(_v1, principal_a, b_doc) == (404, GENERIC)
    assert _denied(_v0, principal_a, b_doc) == (404, GENERIC)


# --- E. nonexistent vs out-of-scope are indistinguishable --------------------

def test_e_nonexistent_and_out_of_scope_are_identical_v1(env):
    _, principal_a, _, _ = env.account()
    _, _pb, pid_b, _ = env.account()
    b_doc = _downloadable(env, pid_b)
    missing = 99_000_000 + b_doc

    assert _denied(_v1, principal_a, missing) == _denied(_v1, principal_a, b_doc) == (404, GENERIC)


def test_e_nonexistent_and_out_of_scope_are_identical_v0(env):
    _, principal_a, _, _ = env.account()
    _, _pb, pid_b, _ = env.account()
    b_doc = _downloadable(env, pid_b)
    missing = 99_000_000 + b_doc

    assert _denied(_v0, principal_a, missing) == _denied(_v0, principal_a, b_doc) == (404, GENERIC)


# --- F. client_visible = False -------------------------------------------------

def test_f_client_visible_false_is_denied_on_both_versions(env):
    _, principal, pid, _ = env.account()
    hidden = env.staff_doc(pid, client_visible=False)
    vault.update_metadata(env.staff, hidden, changes={"status": "approved"},
                          actor_user_id=env.user_id)

    assert _denied(_v1, principal, hidden) == (404, GENERIC)
    assert _denied(_v0, principal, hidden) == (404, GENERIC)


def test_f_unapproved_status_is_denied_on_both_versions(env):
    """client_visible but not a downloadable status, and not the client's own upload."""
    _, principal, pid, _ = env.account()
    doc_id = env.staff_doc(pid, client_visible=True)          # status stays 'uploaded'
    assert _denied(_v1, principal, doc_id) == (404, GENERIC)
    assert _denied(_v0, principal, doc_id) == (404, GENERIC)


# --- G. no internal disclosure --------------------------------------------------

def test_g_denials_disclose_nothing_internal(env):
    _, principal_a, pid_a, _ = env.account()
    _, _pb, pid_b, _ = env.account()
    cases = [_canonical_doc(pid_a), _downloadable(env, pid_b),
             env.staff_doc(pid_a, client_visible=False), 99_123_456]
    for fn in (_v1, _v0):
        for doc_id in cases:
            status, detail = _denied(fn, principal_a, doc_id)
            assert status == 404
            body = str(detail).lower()
            for leak in INTERNAL:
                assert leak.lower() not in body, f"denial leaked {leak!r}: {detail!r}"
            assert str(doc_id) not in body


# --- H. audit -------------------------------------------------------------------

def test_h_successful_download_is_audited_once_per_version(env):
    # Deltas, not absolutes: audit_events is append-only and earlier tests in the same run may already
    # have written rows for a recycled entity id.
    _, principal, pid, _ = env.account()
    doc_id = _downloadable(env, pid)
    before = _audits("portal.document.downloaded", doc_id)

    _v1(principal, doc_id)
    assert _audits("portal.document.downloaded", doc_id) == before + 1, \
        "v1 download was not audited exactly once"

    _v0(principal, doc_id)
    assert _audits("portal.document.downloaded", doc_id) == before + 2, "v0 download was not audited"


def test_h_denied_download_writes_no_success_audit(env):
    _, principal_a, pid_a, _ = env.account()
    _, _pb, pid_b, _ = env.account()
    canonical = _canonical_doc(pid_a)
    b_doc = _downloadable(env, pid_b)

    before_canonical = _audits("portal.document.downloaded", canonical)
    before_b = _audits("portal.document.downloaded", b_doc)
    for fn in (_v1, _v0):
        _denied(fn, principal_a, canonical)
        _denied(fn, principal_a, b_doc)
    assert _audits("portal.document.downloaded", canonical) == before_canonical, \
        "a denied download wrote a success audit event"
    assert _audits("portal.document.downloaded", b_doc) == before_b, \
        "a denied cross-client download wrote a success audit event"


# --- I. the surface gate stays a DIFFERENT answer from resource denial ---------

def test_i_gate_off_is_403_not_404(env, portal_gates):
    """Feature unavailable must not be reported as a resource-existence answer."""
    _, principal, pid, _ = env.account()
    doc_id = _downloadable(env, pid)
    portal_gates({"portal.enabled", "portal.production_signed_off"})   # download gate OFF

    for fn in (_v1, _v0):
        status, detail = _denied(fn, principal, doc_id)
        assert status == 403, "a disabled feature was reported as 404"
        assert detail != GENERIC


def test_i_gate_off_still_leaks_no_document_detail(env, portal_gates):
    _, principal, pid, _ = env.account()
    doc_id = _downloadable(env, pid)
    portal_gates({"portal.enabled", "portal.production_signed_off"})
    for fn in (_v1, _v0):
        _status, detail = _denied(fn, principal, doc_id)
        body = str(detail).lower()
        for leak in INTERNAL:
            assert leak.lower() not in body


# --- parity ---------------------------------------------------------------------

def test_v0_v1_parity_across_every_authorization_state(env):
    """Same authorization state → same status and same disclosure on both versions."""
    _, principal_a, pid_a, _ = env.account()
    _, _pb, pid_b, _ = env.account()

    states = {
        "authorized": _downloadable(env, pid_a),
        "nonexistent": 99_654_321,
        "out_of_scope": _downloadable(env, pid_b),
        "canonical_only": _canonical_doc(pid_a),
        "client_visible_false": env.staff_doc(pid_a, client_visible=False),
    }
    for label, doc_id in states.items():
        if label == "authorized":
            r1, r0 = _v1(principal_a, doc_id), _v0(principal_a, doc_id)
            assert str(r1.path) == str(r0.path), f"{label}: different bytes"
            continue
        assert _denied(_v1, principal_a, doc_id) == _denied(_v0, principal_a, doc_id) == (404, GENERIC), \
            f"{label}: v0/v1 disagree"


def test_v1_no_longer_reads_the_canonical_documents_table():
    """Structural guard: the route must delegate, never query canonical documents again."""
    import inspect

    from app.routes import portal
    src = inspect.getsource(portal.api_portal_document_download)
    assert "portal_vault.download_document" in src, "v1 must delegate to the vault service"
    assert "documents.c." not in src, "v1 must not query the canonical documents table"
    assert "require_scope(" not in src, "v1 must not re-implement scope authorization"
