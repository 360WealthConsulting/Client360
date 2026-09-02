"""MCP read-only interface — authorization, scoping, projection and protocol tests.

The fixture builds one realistic slice of the firm: a household with two members, a document on each
member, one on the household, one soft-deleted, and one belonging to a client NOBODY in these tests
is assigned to. Two staff users then look at it — ``owner`` is assigned to the household, ``stranger``
is assigned to nothing — which is what makes "scoped correctly" a claim these tests can actually
falsify rather than assert against an empty database.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    capabilities,
    document_ocr,
    documents,
    engine,
    household_relationships,
    households,
    mcp_access_tokens,
    people,
    record_assignments,
    role_capabilities,
    roles,
    user_roles,
    users,
)
from app.mcp import auth as mcp_auth
from app.mcp import config as mcp_config
from app.mcp import protocol
from app.mcp import tokens as mcp_tokens
from app.mcp.errors import McpDenied, McpUnauthenticated, McpUnavailable
from app.mcp.scopes import CLIENT_READ, DOCUMENT_CONTENT_READ, DOCUMENT_READ
from app.mcp.tokens import McpToken
from app.mcp.tools import TOOLS
from app.security.models import Principal

# The capabilities a fully-privileged MCP caller holds: the door, the three MCP read capabilities,
# and the ordinary app capabilities the same actions need in the web UI.
FULL_CAPS = frozenset({
    "mcp.access", "mcp.client.read", "mcp.document.read", "mcp.document.content.read",
    "client.read", "documents.view",
})


@pytest.fixture(autouse=True)
def _mcp_enabled(monkeypatch):
    """Every test runs with the interface switched on; the off-by-default case is tested explicitly."""
    monkeypatch.setenv("CLIENT360_MCP_ENABLED", "true")


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def world():
    """A household with two members, four documents, and two staff users with different scope."""
    tag = _tag()
    with engine.begin() as c:
        owner_uid = c.execute(users.insert().values(
            email=f"mcp-own-{tag}@e.test", normalized_email=f"mcp-own-{tag}@e.test",
            display_name=f"Owner {tag}", auth_subject=f"mcp-own-{tag}",
            status="active").returning(users.c.id)).scalar_one()
        stranger_uid = c.execute(users.insert().values(
            email=f"mcp-str-{tag}@e.test", normalized_email=f"mcp-str-{tag}@e.test",
            display_name=f"Stranger {tag}", auth_subject=f"mcp-str-{tag}",
            status="active").returning(users.c.id)).scalar_one()

        hh_id = c.execute(households.insert().values(
            name=f"Ashford Household {tag}", city="Roanoke").returning(
            households.c.id)).scalar_one()
        spouse_a = c.execute(people.insert().values(
            full_name=f"Alice Ashford {tag}", first_name="Alice", last_name=f"Ashford{tag}",
            primary_email=f"alice-{tag}@e.test", normalized_email=f"alice-{tag}@e.test",
            household_id=hh_id, active=True).returning(people.c.id)).scalar_one()
        spouse_b = c.execute(people.insert().values(
            full_name=f"Bob Ashford {tag}", first_name="Bob", last_name=f"Ashford{tag}",
            primary_email=f"bob-{tag}@e.test", normalized_email=f"bob-{tag}@e.test",
            household_id=hh_id, active=True).returning(people.c.id)).scalar_one()
        outsider = c.execute(people.insert().values(
            full_name=f"Zed Outsider {tag}", first_name="Zed", last_name=f"Outsider{tag}",
            primary_email=f"zed-{tag}@e.test", normalized_email=f"zed-{tag}@e.test",
            active=True).returning(people.c.id)).scalar_one()
        for pid, rel in ((spouse_a, "spouse"), (spouse_b, "spouse")):
            c.execute(household_relationships.insert().values(
                household_id=hh_id, person_id=pid, relationship_type=rel,
                is_primary=(pid == spouse_a)))

        # The owner is assigned to the household AND to each member. Both are needed to model a
        # real advisor: the document PLATFORM scopes a listing by the household-expanded person set
        # but scopes a single document by direct assignment (app/services/document_platform:
        # _scope_clause vs _visible), so a household-only assignment would let this suite list a
        # document it could not then open — a pre-existing asymmetry in the platform, not something
        # for these tests to paper over or to depend on.
        c.execute(insert(record_assignments).values(
            user_id=owner_uid, entity_type="household", entity_id=hh_id,
            assignment_type="owner", effective_date=date.today()))
        for pid in (spouse_a, spouse_b):
            c.execute(insert(record_assignments).values(
                user_id=owner_uid, entity_type="person", entity_id=pid,
                assignment_type="owner", effective_date=date.today()))

        def _doc(**kw):
            base = dict(original_name="doc.pdf", stored_name=f"{uuid.uuid4().hex}.pdf",
                        storage_path=f"/never/read/{uuid.uuid4().hex}.pdf", size_bytes=10,
                        sha256=uuid.uuid4().hex, content_type="application/pdf",
                        status="active", classification="tax", current_version=1,
                        tags={"tax_year": "2024", "source_system": "TaxDome"})
            base.update(kw)
            return c.execute(documents.insert().values(**base).returning(
                documents.c.id)).scalar_one()

        doc_alice = _doc(person_id=spouse_a, display_name=f"Alice W-2 {tag}")
        doc_bob = _doc(person_id=spouse_b, display_name=f"Bob 1099 {tag}")
        doc_household = _doc(household_id=hh_id, display_name=f"Joint 1040 {tag}")
        doc_2023 = _doc(person_id=spouse_a, display_name=f"Alice W-2 2023 {tag}",
                        tags={"tax_year": "2023", "source_system": "TaxDome"})
        doc_deleted = _doc(person_id=spouse_a, display_name=f"Deleted Return {tag}",
                           status="deleted", deleted_at=datetime.now(UTC))
        doc_outsider = _doc(person_id=outsider, display_name=f"Outsider Return {tag}")

        # Only Alice's W-2 has extracted text.
        c.execute(document_ocr.insert().values(
            document_id=doc_alice, status="completed", text="WAGES AND TAX STATEMENT 2024",
            char_count=28, page_count=1, engine="tesseract",
            ocr_completed_at=datetime.now(UTC)))
        c.execute(document_ocr.insert().values(
            document_id=doc_bob, status="failed", text=None, char_count=0, attempts=3))

    ids = {"tag": tag, "owner_uid": owner_uid, "stranger_uid": stranger_uid, "hh_id": hh_id,
           "alice": spouse_a, "bob": spouse_b, "outsider": outsider,
           "doc_alice": doc_alice, "doc_bob": doc_bob, "doc_household": doc_household,
           "doc_2023": doc_2023, "doc_deleted": doc_deleted, "doc_outsider": doc_outsider}
    yield ids

    with engine.begin() as c:
        doc_ids = [ids[k] for k in ("doc_alice", "doc_bob", "doc_household", "doc_2023",
                                    "doc_deleted", "doc_outsider")]
        c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(doc_ids)))
        c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
        c.execute(delete(mcp_access_tokens).where(
            mcp_access_tokens.c.user_id.in_([owner_uid, stranger_uid])))
        c.execute(delete(record_assignments).where(
            record_assignments.c.user_id.in_([owner_uid, stranger_uid])))
        c.execute(delete(household_relationships).where(
            household_relationships.c.household_id == hh_id))
        c.execute(delete(people).where(people.c.id.in_([spouse_a, spouse_b, outsider])))
        c.execute(delete(households).where(households.c.id == hh_id))
        c.execute(delete(user_roles).where(user_roles.c.user_id.in_([owner_uid, stranger_uid])))
        # The users themselves are LEFT BEHIND on purpose. MCP calls write audit events that
        # reference the actor, and audit_events is append-only at the database level — deleting the
        # user would fire the FK's ON DELETE SET NULL against a table whose trigger forbids UPDATE.
        # That immutability is the point of the audit chain, so the test cleans up around it.


def _token(user_id, *, caps=FULL_CAPS, scopes=(CLIENT_READ, DOCUMENT_READ, DOCUMENT_CONTENT_READ),
           email="staff@e.test"):
    """An in-memory McpToken. Lets a test vary capabilities and scopes without minting real tokens."""
    return McpToken(token_id=1, principal=Principal(user_id, email, "Staff", frozenset(caps)),
                    scopes=tuple(scopes), label="test")


def _call(token, tool, args):
    """Invoke a tool through the real protocol path (authorize + audit + projection)."""
    body = protocol.call_tool(token, tool, args, context={"request_id": "test"})
    payload = json.loads(body["content"][0]["text"])
    return body, payload


# --- unauthorized access is denied -------------------------------------------

def test_no_credential_is_unauthenticated():
    with pytest.raises(McpUnauthenticated):
        mcp_auth.authenticate(None)
    with pytest.raises(McpUnauthenticated):
        mcp_auth.authenticate("Bearer not-a-real-token")


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer", "token abc", "Bearer   "])
def test_malformed_authorization_headers_yield_no_credential(header):
    assert mcp_auth.bearer_token(header) is None


def test_disabled_interface_denies_before_any_credential_check(monkeypatch):
    monkeypatch.setenv("CLIENT360_MCP_ENABLED", "false")
    with pytest.raises(McpUnavailable):
        mcp_auth.authenticate("Bearer anything")


def test_token_without_mcp_access_capability_is_denied(world):
    """A valid, unexpired token whose owner lacks the door capability opens nothing."""
    tag = _tag()
    with engine.begin() as c:
        role_id = c.execute(roles.insert().values(
            code=f"mcp-role-{tag}", name=f"Role {tag}", active=True).returning(
            roles.c.id)).scalar_one()
        # client.read only — deliberately NOT mcp.access.
        cap_id = c.scalar(select(capabilities.c.id).where(capabilities.c.code == "client.read"))
        if cap_id:
            c.execute(role_capabilities.insert().values(role_id=role_id, capability_id=cap_id))
        c.execute(user_roles.insert().values(user_id=world["owner_uid"], role_id=role_id))
    secret = mcp_tokens.issue_token(user_id=world["owner_uid"], scopes=[CLIENT_READ])
    with pytest.raises(McpDenied):
        mcp_auth.authenticate(f"Bearer {secret}")


def test_expired_and_revoked_tokens_do_not_resolve(world):
    expired = mcp_tokens.issue_token(user_id=world["owner_uid"], scopes=[CLIENT_READ])
    with engine.begin() as c:
        c.execute(mcp_access_tokens.update()
                  .where(mcp_access_tokens.c.user_id == world["owner_uid"])
                  .values(expires_at=datetime.now(UTC) - timedelta(hours=1)))
    assert mcp_tokens.resolve(expired) is None

    live = mcp_tokens.issue_token(user_id=world["owner_uid"], scopes=[CLIENT_READ])
    resolved = mcp_tokens.resolve(live)
    assert resolved is not None
    assert mcp_tokens.revoke_token(resolved.token_id) is True
    assert mcp_tokens.resolve(live) is None
    # Revocation is idempotent, not an error.
    assert mcp_tokens.revoke_token(resolved.token_id) is False


def test_issued_token_is_never_stored_in_clear(world):
    secret = mcp_tokens.issue_token(user_id=world["owner_uid"], scopes=[CLIENT_READ])
    with engine.connect() as c:
        stored = [r["token_hash"] for r in c.execute(select(
            mcp_access_tokens.c.token_hash).where(
            mcp_access_tokens.c.user_id == world["owner_uid"])).mappings()]
    assert stored and secret not in stored
    assert all(len(h) == 64 for h in stored)


def test_issue_refuses_a_token_with_no_recognised_scope(world):
    with pytest.raises(ValueError):
        mcp_tokens.issue_token(user_id=world["owner_uid"], scopes=["definitely:not:a:scope"])


# --- scope and capability boundaries ----------------------------------------

def test_scope_missing_from_token_denies_the_tool(world):
    """Holding every capability is not enough — the TOKEN must also carry the scope."""
    token = _token(world["owner_uid"], scopes=(CLIENT_READ,))
    body, payload = _call(token, "list_client_documents", {"person_id": world["alice"]})
    assert body["isError"] is True
    assert "document:read" in payload["message"]


def test_capability_missing_from_principal_denies_the_tool(world):
    """And holding the scope is not enough — the OWNER must still hold the app capability."""
    token = _token(world["owner_uid"], caps=frozenset({"mcp.access", "mcp.document.read"}))
    body, payload = _call(token, "list_client_documents", {"person_id": world["alice"]})
    assert body["isError"] is True
    assert "documents.view" in payload["message"]


def test_document_content_scope_is_separable_from_document_metadata(world):
    """A metadata-only token reads the document but never its text."""
    token = _token(world["owner_uid"], scopes=(DOCUMENT_READ,))
    body, _ = _call(token, "get_document", {"document_id": world["doc_alice"]})
    assert "isError" not in body

    body, payload = _call(token, "get_document_text", {"document_id": world["doc_alice"]})
    assert body["isError"] is True
    assert "document:content:read" in payload["message"]


# --- client search is scoped -------------------------------------------------

def test_client_search_returns_in_scope_clients_only(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "search_clients", {"query": f"Ashford{world['tag']}"})
    found = {(r["entity_type"], r["id"]) for r in payload["results"]}
    assert ("person", world["alice"]) in found
    assert ("person", world["bob"]) in found

    _, payload = _call(owner, "search_clients", {"query": f"Outsider{world['tag']}"})
    assert [r for r in payload["results"] if r["id"] == world["outsider"]] == []


def test_client_search_returns_nothing_for_an_unassigned_user(world):
    stranger = _token(world["stranger_uid"])
    _, payload = _call(stranger, "search_clients", {"query": f"Ashford{world['tag']}"})
    assert payload["results"] == []
    assert payload["count"] == 0


def test_search_results_omit_contact_details(world):
    """Search must not become a bulk PII extractor: no email or phone on any result."""
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "search_clients", {"query": f"Ashford{world['tag']}"})
    assert payload["results"], "fixture should produce results"
    for row in payload["results"]:
        assert set(row) == {"entity_type", "id", "display_name", "household_id", "status",
                            "relationship_context"}
    blob = json.dumps(payload)
    assert f"alice-{world['tag']}@e.test" not in blob
    assert f"bob-{world['tag']}@e.test" not in blob


def test_get_client_is_denied_for_an_out_of_scope_person(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "get_client",
                          {"entity_type": "person", "entity_id": world["outsider"]})
    assert body["isError"] is True
    # Same wording as a genuinely missing record — never an existence oracle.
    assert payload["message"] == "Client not found or not permitted"

    body_missing, payload_missing = _call(
        owner, "get_client", {"entity_type": "person", "entity_id": 2_000_000_000})
    assert payload_missing["message"] == payload["message"]


def test_get_client_returns_household_membership(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "get_client",
                       {"entity_type": "person", "entity_id": world["alice"]})
    assert payload["entity_type"] == "person"
    assert payload["household"]["id"] == world["hh_id"]
    member_ids = {m["person_id"] for m in payload["household_members"]}
    assert member_ids == {world["alice"], world["bob"]}
    assert all("primary_email" not in m and "primary_phone" not in m
               for m in payload["household_members"])


def test_get_client_household_lists_its_members(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "get_client",
                       {"entity_type": "household", "entity_id": world["hh_id"]})
    assert payload["entity_type"] == "household"
    assert {m["person_id"] for m in payload["household_members"]} == {world["alice"], world["bob"]}


# --- the person/household document union ------------------------------------

def test_person_documents_include_the_household_documents(world):
    """Asking about Alice must surface the JOINT return, not only documents keyed to her."""
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents", {"person_id": world["alice"], "limit": 50})
    ids = {d["document_id"] for d in payload["documents"]}
    assert world["doc_alice"] in ids
    assert world["doc_household"] in ids, "the household's joint document must appear for a member"


def test_household_documents_include_every_members_documents(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents",
                       {"household_id": world["hh_id"], "limit": 50})
    ids = {d["document_id"] for d in payload["documents"]}
    assert {world["doc_alice"], world["doc_bob"], world["doc_household"]} <= ids


def test_document_union_never_crosses_into_another_client(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents",
                       {"household_id": world["hh_id"], "limit": 50})
    ids = {d["document_id"] for d in payload["documents"]}
    assert world["doc_outsider"] not in ids


def test_listing_another_clients_documents_is_denied(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "list_client_documents", {"person_id": world["outsider"]})
    assert body["isError"] is True
    assert payload["message"] == "Client not found or not permitted"


def test_name_filter_matches_the_name_the_tool_reports(world):
    """A caller must be able to filter by the display name it was just shown."""
    owner = _token(world["owner_uid"])
    _, listed = _call(owner, "list_client_documents",
                      {"person_id": world["alice"], "limit": 50})
    shown = {d["document_id"]: d["display_name"] for d in listed["documents"]}
    assert shown[world["doc_household"]] == f"Joint 1040 {world['tag']}"

    _, payload = _call(owner, "list_client_documents",
                       {"person_id": world["alice"], "name_contains": "Joint 1040", "limit": 50})
    assert {d["document_id"] for d in payload["documents"]} == {world["doc_household"]}


def test_category_filter_matches_the_category_the_tool_reports(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents",
                       {"person_id": world["alice"], "category": "tax", "limit": 50})
    ids = {d["document_id"] for d in payload["documents"]}
    assert world["doc_alice"] in ids
    assert all(d["category"] == "tax" for d in payload["documents"])

    _, payload = _call(owner, "list_client_documents",
                       {"person_id": world["alice"], "category": "insurance", "limit": 50})
    assert payload["documents"] == []


def test_tax_year_filter_narrows_the_union(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents",
                       {"person_id": world["alice"], "tax_year": "2023", "limit": 50})
    ids = {d["document_id"] for d in payload["documents"]}
    assert ids == {world["doc_2023"]}


# --- soft-deleted documents are never returned -------------------------------

def test_deleted_documents_are_excluded_from_listings(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents", {"person_id": world["alice"], "limit": 50})
    ids = {d["document_id"] for d in payload["documents"]}
    assert world["doc_deleted"] not in ids


def test_deleted_documents_cannot_be_fetched_directly(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "get_document", {"document_id": world["doc_deleted"]})
    assert body["isError"] is True
    assert payload["message"] == "Document not found or not permitted"


def test_deleted_document_text_cannot_be_fetched(world):
    owner = _token(world["owner_uid"])
    body, _ = _call(owner, "get_document_text", {"document_id": world["doc_deleted"]})
    assert body["isError"] is True


def test_deleted_documents_are_excluded_from_search(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "search_documents",
                       {"query": f"Deleted Return {world['tag']}", "limit": 50})
    assert [d for d in payload["documents"] if d["document_id"] == world["doc_deleted"]] == []


# --- document permission boundaries -----------------------------------------

def test_out_of_scope_document_is_not_readable(world):
    owner = _token(world["owner_uid"])
    body, _ = _call(owner, "get_document", {"document_id": world["doc_outsider"]})
    assert body["isError"] is True


def test_unassigned_user_sees_no_documents_at_all(world):
    stranger = _token(world["stranger_uid"])
    body, _ = _call(stranger, "get_document", {"document_id": world["doc_alice"]})
    assert body["isError"] is True
    _, payload = _call(stranger, "search_documents", {"query": world["tag"], "limit": 50})
    assert payload["documents"] == []


def test_document_payloads_never_expose_storage_internals(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "get_document", {"document_id": world["doc_alice"]})
    for forbidden in ("storage_path", "storage_uri", "stored_name", "sha256"):
        assert forbidden not in payload, f"{forbidden} must never leave the building"
    assert payload["download_reference"] == f"/documents/{world['doc_alice']}/download"
    assert "/never/read/" not in json.dumps(payload)


# --- document text only when it already exists -------------------------------

def test_document_text_is_returned_when_extraction_completed(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "get_document_text", {"document_id": world["doc_alice"]})
    assert "isError" not in body
    assert payload["available"] is True
    assert "WAGES AND TAX STATEMENT" in payload["text"]


def test_document_text_reports_unavailable_when_extraction_failed(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "get_document_text", {"document_id": world["doc_bob"]})
    assert payload["available"] is False
    assert payload["text"] is None
    assert payload["ocr_status"] == "failed"


def test_document_text_reports_unavailable_when_never_extracted(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "get_document_text", {"document_id": world["doc_household"]})
    assert payload["available"] is False
    assert payload["ocr_status"] == "not_extracted"


def test_get_document_text_does_not_trigger_ocr(world, monkeypatch):
    """The tool must never start extraction — that would let a chat message queue firm-wide CPU."""
    import app.services.document_ocr as ocr_module

    def _explode(*args, **kwargs):
        raise AssertionError("MCP must never trigger OCR")

    monkeypatch.setattr(ocr_module, "run_ocr", _explode)
    monkeypatch.setattr(ocr_module, "extract_text", _explode)
    owner = _token(world["owner_uid"])
    _call(owner, "get_document_text", {"document_id": world["doc_household"]})


def test_extracted_text_is_truncated_to_the_configured_ceiling(world, monkeypatch):
    monkeypatch.setattr(mcp_config, "MAX_TEXT_CHARS", 10)
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "get_document_text", {"document_id": world["doc_alice"]})
    assert payload["truncated"] is True
    assert len(payload["text"]) == 10


def test_listing_declares_whether_text_can_be_fetched(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "list_client_documents", {"person_id": world["alice"], "limit": 50})
    by_id = {d["document_id"]: d for d in payload["documents"]}
    assert by_id[world["doc_alice"]]["ocr_text_available"] is True
    assert by_id[world["doc_household"]]["ocr_text_available"] is False


# --- malformed input ---------------------------------------------------------

@pytest.mark.parametrize("bad", ["abc", "", "12.5", "-3", "0", None, True, [1], {"a": 1},
                                 "1 OR 1=1", "1; DROP TABLE documents", " 1 2 "])
def test_malformed_document_ids_are_rejected(world, bad):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "get_document", {"document_id": bad})
    assert body["isError"] is True
    assert payload["error"] == "McpInvalidInput"


def test_list_client_documents_requires_an_anchor(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "list_client_documents", {})
    assert body["isError"] is True
    assert "person_id or household_id is required" in payload["message"]


def test_unknown_entity_type_is_rejected(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "get_client",
                          {"entity_type": "employee", "entity_id": world["alice"]})
    assert body["isError"] is True
    assert payload["error"] == "McpInvalidInput"


def test_malformed_tax_year_is_rejected(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "list_client_documents",
                          {"person_id": world["alice"], "tax_year": "24"})
    assert body["isError"] is True
    assert "four-digit year" in payload["message"]


def test_empty_and_oversized_queries_are_rejected(world):
    owner = _token(world["owner_uid"])
    body, _ = _call(owner, "search_clients", {"query": "   "})
    assert body["isError"] is True
    body, _ = _call(owner, "search_clients", {"query": "x" * 5000})
    assert body["isError"] is True


def test_unknown_tool_is_refused(world):
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "run_sql", {"sql": "select 1"})
    assert body["isError"] is True
    assert payload["error"] == "unknown_tool"


def test_non_object_arguments_are_refused(world):
    owner = _token(world["owner_uid"])
    body = protocol.call_tool(owner, "search_clients", ["not", "an", "object"])
    assert body["isError"] is True


def test_an_internal_error_never_leaks_its_text(world, monkeypatch):
    def _boom(token, args):
        raise RuntimeError("connection string postgresql://user:hunter2@db/secret")

    monkeypatch.setitem(TOOLS["search_clients"], "handler", _boom)
    owner = _token(world["owner_uid"])
    body, payload = _call(owner, "search_clients", {"query": "anything"})
    assert body["isError"] is True
    assert payload["message"] == "The request could not be completed."
    assert "hunter2" not in json.dumps(body)


# --- bounded and paginated output -------------------------------------------

def test_limits_are_clamped_to_the_ceiling():
    assert mcp_config.clamp_limit(10_000) == mcp_config.MAX_LIMIT
    assert mcp_config.clamp_limit(0) == 1
    assert mcp_config.clamp_limit(-5) == 1
    assert mcp_config.clamp_limit(None) == mcp_config.DEFAULT_LIMIT
    assert mcp_config.clamp_limit("nonsense") == mcp_config.DEFAULT_LIMIT
    assert mcp_config.clamp_limit("7") == 7


def test_document_listing_is_paginated(world):
    owner = _token(world["owner_uid"])
    _, first = _call(owner, "list_client_documents",
                     {"household_id": world["hh_id"], "limit": 1, "page": 1})
    _, second = _call(owner, "list_client_documents",
                      {"household_id": world["hh_id"], "limit": 1, "page": 2})
    assert first["count"] == 1 and second["count"] == 1
    assert first["page_size"] == 1
    assert first["total"] >= 3
    assert first["pages"] >= 3
    assert (first["documents"][0]["document_id"]
            != second["documents"][0]["document_id"]), "pages must not repeat a row"


def test_search_results_never_exceed_the_requested_limit(world):
    owner = _token(world["owner_uid"])
    _, payload = _call(owner, "search_clients", {"query": world["tag"], "limit": 1})
    assert len(payload["results"]) <= 1
    _, payload = _call(owner, "search_documents", {"query": world["tag"], "limit": 1})
    assert len(payload["documents"]) <= 1


# --- protocol ----------------------------------------------------------------

def test_initialize_advertises_tools_only(world):
    response = protocol.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, _token(world["owner_uid"]))
    result = response["result"]
    assert result["protocolVersion"] == mcp_config.PROTOCOL_VERSION
    assert set(result["capabilities"]) == {"tools"}
    assert "sampling" not in result["capabilities"]


def test_tools_list_exposes_exactly_the_six_read_only_tools(world):
    response = protocol.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _token(world["owner_uid"]))
    names = [t["name"] for t in response["result"]["tools"]]
    assert names == ["search_clients", "get_client", "list_client_documents", "get_document",
                     "search_documents", "get_document_text"]
    for tool in response["result"]["tools"]:
        assert tool["inputSchema"]["additionalProperties"] is False, (
            f"{tool['name']} must reject unknown arguments")


def test_notifications_receive_no_response(world):
    assert protocol.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _token(world["owner_uid"])) is None


def test_unknown_method_is_a_protocol_error(world):
    response = protocol.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, _token(world["owner_uid"]))
    assert response["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_batches_are_dispatched(world):
    responses = protocol.handle_payload(
        [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
         {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], _token(world["owner_uid"]))
    assert [r["id"] for r in responses] == [1, 2]
