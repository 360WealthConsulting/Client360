"""Coverage for the Documents/Vault consolidation — one Documents tab shows canonical + Vault documents.

Robinson worked example: vault document (Alicia, person 6746) appears in both the person and household
Documents tabs; the canonical household document (doc-800 shape) stays visible; download links use the
correct route per source; checksum duplicates are deduped; and the standalone Vault tab is gone from nav.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import delete

from app.db import documents, engine, households, metadata, people
from app.security.models import Principal
from app.services.client360 import registry
from app.services.client360.household import _documents as household_documents
from app.services.client360.sections import documents as person_documents

_vault_documents = metadata.tables["vault_documents"]
_vault_document_links = metadata.tables["vault_document_links"]
_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "households": [], "vault_documents": []}
# record.read_all + vault.access.all so the Vault service authorizes the seeded vault docs.
_P = Principal(999200, "u@e.com", "U",
               frozenset({"client.read", "documents.view", "record.read_all", "vault.access.all"}))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["vault_documents"]:
            c.execute(delete(_vault_document_links).where(
                _vault_document_links.c.document_id.in_(_C["vault_documents"])))
            c.execute(delete(_vault_documents).where(_vault_documents.c.id.in_(_C["vault_documents"])))
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        if _C["people"]:
            c.execute(people.delete().where(people.c.id.in_(_C["people"])))
        if _C["households"]:
            c.execute(households.delete().where(households.c.id.in_(_C["households"])))
    for k in _C:
        _C[k].clear()


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _person(full, first, last, hid):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full, first_name=first, last_name=last,
                                               active=True, household_id=hid).returning(people.c.id)
                        ).scalar_one()
    _C["people"].append(pid)
    return pid


def _canonical_household_doc(hid, name, sha=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=hid, organization_id=None, original_name=name,
            stored_name=f"cv-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri=f"D:\\Client360\\Content\\Households\\HH\\{uuid.uuid4().hex}.pdf", size_bytes=10,
            sha256=sha or hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            category="tax").returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _vault_doc(display_name, *, person_id=None, household_id=None, checksum=None, category="tax"):
    with engine.begin() as c:
        vid = c.execute(_vault_documents.insert().values(
            display_name=display_name, original_filename=display_name, category=category,
            storage_key=f"vault/{_TAG}/{uuid.uuid4().hex}",
            checksum_sha256=checksum or hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        ).returning(_vault_documents.c.id)).scalar_one()
        c.execute(_vault_document_links.insert().values(
            document_id=vid, person_id=person_id, household_id=household_id))
    _C["vault_documents"].append(vid)
    return vid


def _robinson():
    hid = _household(f"Robinson Household {_TAG}")
    alicia = _person(f"Alicia Robinson {_TAG}", "Alicia", "Robinson", hid)
    doc800 = _canonical_household_doc(hid, f"2025 Tax Return (ROBINSON) {_TAG}.pdf")   # doc-800 shape
    vault1 = _vault_doc(f"2024 Tax Return {_TAG}", person_id=alicia)                   # vault doc 1
    return hid, alicia, doc800, vault1


# --- person + household Documents tabs include Vault ---------------------------

def test_person_documents_include_linked_vault_document():
    _hid, alicia, _d800, vault1 = _robinson()
    docs = person_documents(_P, {"entity_type": "person", "entity_id": alicia})["documents"]
    v = next((d for d in docs if d.get("vault_document_id") == vault1), None)
    assert v is not None and v["source"] == "Vault"
    assert v["download_url"] == f"/api/vault/documents/{vault1}/download"


def test_household_documents_include_vault_via_member_and_keep_canonical():
    hid, alicia, doc800, vault1 = _robinson()
    docs = household_documents(_P, {"household_id": hid, "member_ids": [alicia]})["documents"]
    ids_vault = {d.get("vault_document_id") for d in docs}
    assert vault1 in ids_vault                                        # vault doc via member 6746-analog
    canon = next((d for d in docs if d["id"] == doc800 and d["source_kind"] == "canonical"), None)
    assert canon is not None                                         # doc-800 canonical still visible
    assert canon["download_url"] == f"/documents/{doc800}/download"


def test_download_routes_are_source_correct():
    hid, alicia, doc800, vault1 = _robinson()
    docs = household_documents(_P, {"household_id": hid, "member_ids": [alicia]})["documents"]
    by = {(d["source_kind"], d.get("vault_document_id") or d["id"]): d for d in docs}
    assert by[("canonical", doc800)]["download_url"].startswith("/documents/")
    assert by[("vault", vault1)]["download_url"].startswith("/api/vault/documents/")


# --- dedup by checksum --------------------------------------------------------

def test_checksum_duplicate_vault_document_is_deduped():
    hid = _household(f"Dedup HH {_TAG}")
    alicia = _person(f"Dee Dup {_TAG}", "Dee", "Dup", hid)
    shared = hashlib.sha256(b"same-underlying-file").hexdigest()
    canon = _canonical_household_doc(hid, f"1040 {_TAG}.pdf", sha=shared)
    dup_vault = _vault_doc(f"1040 vault copy {_TAG}", person_id=alicia, checksum=shared)
    docs = household_documents(_P, {"household_id": hid, "member_ids": [alicia]})["documents"]
    assert any(d["id"] == canon and d["source_kind"] == "canonical" for d in docs)
    assert dup_vault not in {d.get("vault_document_id") for d in docs}   # same checksum -> deduped


# --- navigation ---------------------------------------------------------------

def test_vault_tab_removed_from_navigation():
    assert "vault" not in registry.SECTION_KEYS
    assert "documents" in registry.SECTION_KEYS
    assert "vault" not in {s.key for s in registry.visible_sections(_P)}
    assert "documents" in {s.key for s in registry.visible_sections(_P)}
