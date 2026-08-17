"""SharePoint integration foundation (PR 4A) — coverage.

SharePoint as a canonical SOURCE PROVIDER (ADR-072): discovery + type classification, SHA-256 canonical
reuse (dedup across sources), canonical creation, source references with preserved SharePoint metadata
(site/library/folder/author/created/modified), client + household linking (ADR-073), incremental sync,
metadata updates, deleted-item handling, audit logging, permission/record-scope enforcement on the
Documents tab, and the Documents-tab rendering of the SharePoint source reference. Temp files + test
rows only — no real SharePoint tenant.
"""
import hashlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, metadata, people
from app.importers import sharepoint
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.document_sources import add_source_reference, sources_for_document

_TAG = "SHPT"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})
_ACTOR = {"uid": None}


@pytest.fixture(autouse=True)
def _clean():
    ds = metadata.tables["document_sources"]

    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            doc_ids = list(c.scalars(select(documents.c.id).where(documents.c.stored_name.like("sharepoint:%"))))
            doc_ids += list(c.scalars(select(documents.c.id).where(documents.c.original_name.like(f"%{_TAG}%"))))
            if doc_ids:
                c.execute(delete(ds).where(ds.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            if pids:
                c.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    sharepoint._database.cache_clear()
    with engine.begin() as c:
        from app.db import users
        tag = uuid.uuid4().hex[:8]
        _ACTOR["uid"] = c.execute(users.insert().values(
            email=f"shpt{tag}@e.test", normalized_email=f"shpt{tag}@e.test",
            display_name="SHPT Sync", status="active").returning(users.c.id)).scalar_one()
    yield
    _wipe()


def _stage(tmp_path, name, content):
    p = tmp_path / f"stage-{uuid.uuid4().hex[:8]}-{name}"
    p.write_bytes(content.encode() if isinstance(content, str) else content)
    return str(p)


def _item(tmp_path, name=f"Plan {_TAG}.pdf", content="sharepoint bytes", **over):
    it = {
        "name": name, "local_path": _stage(tmp_path, name, content),
        "web_url": f"https://contoso.sharepoint.com/sites/Wealth/Shared/{uuid.uuid4().hex[:8]}/{name}",
        "item_id": f"01ABC{uuid.uuid4().hex[:10].upper()}",
        "site": "Wealth", "library": "Client Documents", "folder_path": "White Household/2024",
        "author": "Advisor One", "created_at": datetime(2025, 1, 5, 9, 0, 0),
        "modified_at": datetime(2025, 2, 1, 12, 0, 0), "size": len(content),
        "content_type": "application/pdf",
    }
    it.update(over)
    return it


def _run(items, **kw):
    kw.setdefault("actor_user_id", _ACTOR["uid"])
    return sharepoint.import_sharepoint_items(items, progress=None, **kw)


def _sp_rows():
    ds = metadata.tables["document_sources"]
    with engine.connect() as c:
        return c.execute(select(ds).where(ds.c.source_system == "SharePoint")).mappings().all()


# --- discovery + classification ----------------------------------------------

def test_doc_type_classification():
    assert sharepoint.sharepoint_doc_type("2024 Plan.pdf") == "pdf"
    assert sharepoint.sharepoint_doc_type("Engagement.docx") == "word"
    assert sharepoint.sharepoint_doc_type("Model.xlsx") == "excel"
    assert sharepoint.sharepoint_doc_type("Review.pptx") == "powerpoint"
    assert sharepoint.sharepoint_doc_type("ID.png") == "image"
    assert sharepoint.sharepoint_doc_type("notes.txt") == "text"
    assert sharepoint.sharepoint_doc_type("Message.msg") == "email_attachment"
    assert sharepoint.sharepoint_doc_type("archive.zip") == "other"


def test_discovery_creates_canonical_with_sharepoint_source(tmp_path):
    it = _item(tmp_path)
    summary = _run([it], destination_root=tmp_path / "dst")
    assert summary["canonical_created"] == 1 and summary["source_refs_added"] == 1
    row = _sp_rows()[0]
    assert row["source_system"] == "SharePoint"
    assert row["source_uri"] == it["web_url"] and row["source_external_id"] == it["item_id"]
    # SharePoint metadata is preserved on the source reference.
    md = row["metadata"]
    assert md["site"] == "Wealth" and md["library"] == "Client Documents"
    assert md["folder"] == "White Household/2024" and md["author"] == "Advisor One"
    assert md["created"] is not None and md["sharepoint_doc_type"] == "pdf"


# --- SHA-256 reuse / dedup across sources ------------------------------------

def test_identical_document_reuses_canonical_and_adds_source_ref(tmp_path):
    content = "shared plan bytes"
    sha = hashlib.sha256(content.encode()).hexdigest()
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=f"Plan {_TAG}.pdf", stored_name=f"taxdome:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/plan",
            size_bytes=len(content), sha256=sha, status="active", archived=False,
            tags={"source_system": "TaxDome Drive"}).returning(documents.c.id)).scalar_one()
        add_source_reference(c, did, source_system="TaxDome Drive", source_uri="Z:/plan", source_hash=sha)
    before = engine.connect().execute(select(documents.c.id).where(documents.c.sha256 == sha)).rowcount
    summary = _run([_item(tmp_path, content=content)], destination_root=tmp_path / "dst")
    assert summary["reused_canonical"] == 1 and summary["canonical_created"] == 0
    after = engine.connect().execute(select(documents.c.id).where(documents.c.sha256 == sha)).rowcount
    assert after == before                                    # NO duplicate canonical document
    systems = {s["source_system"] for s in sources_for_document(did)}
    assert systems == {"TaxDome Drive", "SharePoint"}         # both sources reference one document


def test_duplicate_detection_no_second_local_copy(tmp_path):
    dst = tmp_path / "dst"
    # Same content arriving as two different SharePoint items → one canonical, no second stored copy.
    a = _item(tmp_path, name=f"A {_TAG}.pdf", content="same bytes")
    b = _item(tmp_path, name=f"B {_TAG}.pdf", content="same bytes")
    _run([a], destination_root=dst)
    n_after_a = len(list(dst.rglob("*.pdf")))
    summary = _run([b], destination_root=dst)
    assert summary["reused_canonical"] == 1
    with engine.connect() as c:
        n_docs = c.execute(select(documents.c.id).where(documents.c.sha256 == hashlib.sha256(b"same bytes").hexdigest())).rowcount
    assert n_docs == 1                                        # single canonical row
    assert len(list(dst.rglob("*.pdf"))) == n_after_a         # reuse did not write a second local copy


# --- client / household linking (ADR-073) ------------------------------------

def test_client_linking_direct(tmp_path):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Solo", last_name="Client", full_name=f"Solo Client {_TAG}",
            active=True).returning(people.c.id)).scalar_one()
    summary = _run([_item(tmp_path, person_id=pid)], destination_root=tmp_path / "dst")
    assert summary["linked_person"] == 1
    with engine.connect() as c:
        assert c.scalar(select(documents.c.person_id).where(
            documents.c.stored_name.like("sharepoint:%"))) == pid


def test_household_linking_via_folder(tmp_path):
    # A tag-unique surname the resolver keys on (exact name match) so this test can never resolve to
    # another suite's "White" household; the tag also lets _wipe reclaim these rows.
    surname = f"{_TAG}{uuid.uuid4().hex[:6]}"
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"{_TAG} {surname} Household").returning(
            households.c.id)).scalar_one()
        for first in ("Michael", "Debra"):
            pid = c.execute(people.insert().values(
                first_name=first, last_name=surname, full_name=f"{first} {surname}",
                household_id=hid, active=True).returning(people.c.id)).scalar_one()
            c.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="member"))
    it = _item(tmp_path, client_folder=f"Michael and Debra {surname}")
    summary = _run([it], destination_root=tmp_path / "dst")
    assert summary["linked_household"] == 1
    with engine.connect() as c:
        assert c.scalar(select(documents.c.household_id).where(
            documents.c.stored_name.like("sharepoint:%"))) == hid


# --- incremental + metadata updates + deleted -------------------------------

def test_incremental_skips_unchanged(tmp_path):
    dst = tmp_path / "dst"
    it = _item(tmp_path)
    _run([it], destination_root=dst)
    # Re-present the SAME item (same uri + size + modified) — skipped without re-hashing/copying.
    summary = _run([it], destination_root=dst)
    assert summary["skipped"] == 1 and summary["canonical_created"] == 0


def test_metadata_update_refreshes_source_ref(tmp_path):
    dst = tmp_path / "dst"
    it = _item(tmp_path)
    _run([it], destination_root=dst)
    it2 = {**it, "modified_at": datetime(2025, 6, 1, 8, 0, 0), "author": "Advisor Two",
           "local_path": _stage(tmp_path, it["name"], "sharepoint bytes")}
    summary = _run([it2], destination_root=dst)
    assert summary["metadata_updated"] == 1 and summary["skipped"] == 0
    assert _sp_rows()[0]["metadata"]["author"] == "Advisor Two"


def test_idempotent_single_source_ref(tmp_path):
    dst = tmp_path / "dst"
    it = _item(tmp_path)
    _run([it], destination_root=dst)
    _run([it], destination_root=dst)
    assert len(_sp_rows()) == 1                               # one SharePoint source ref, not two


def test_deleted_item_marks_source_unavailable(tmp_path):
    dst = tmp_path / "dst"
    it = _item(tmp_path)
    _run([it], destination_root=dst)
    summary = _run([{**it, "deleted": True}], destination_root=dst)
    assert summary["deleted"] == 1
    row = _sp_rows()[0]
    assert row["available"] is False                          # source flagged; canonical/local retained
    with engine.connect() as c:
        assert c.scalar(select(documents.c.id).where(documents.c.id == row["document_id"])) is not None


def test_missing_item_marked_unavailable_on_next_sync(tmp_path):
    dst = tmp_path / "dst"
    it = _item(tmp_path)
    _run([it], destination_root=dst)
    # A subsequent COMPLETE (authoritative) sync no longer contains the item → marked unavailable
    # (never deletes canonical). Missing reconciliation only runs for an authoritative full snapshot.
    summary = _run([_item(tmp_path)], destination_root=dst, authoritative=True)
    assert summary["missing"] == 1
    gone = next(r for r in _sp_rows() if r["source_uri"] == it["web_url"])
    assert gone["available"] is False


# --- audit -------------------------------------------------------------------

def test_import_is_audited(tmp_path):
    from app.db import audit_events
    _run([_item(tmp_path)], destination_root=tmp_path / "dst", request_id="sp-t")
    with engine.connect() as c:
        assert c.scalar(select(audit_events.c.id).where(
            audit_events.c.action == "sharepoint.imported").limit(1)) is not None


# --- Documents-tab integration + permission enforcement ----------------------

def test_documents_tab_shows_sharepoint_source_and_link(tmp_path):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Doc", last_name="Owner", full_name=f"Doc Owner {_TAG}",
            active=True).returning(people.c.id)).scalar_one()
    it = _item(tmp_path, person_id=pid)
    _run([it], destination_root=tmp_path / "dst")
    ws = get_workspace(Principal(0, "a@e.test", "A", _CAPS), person_id=pid)
    docs = ws["sections"]["documents"]["documents"]
    d = next(x for x in docs if x["name"] == it["name"])
    assert "SharePoint" in d["source_systems"]
    assert any(s["source_system"] == "SharePoint" and s["source_uri"] == it["web_url"]
               for s in d["sources"])                         # linkable external source in the tab


def test_permission_scope_hides_out_of_scope_document(tmp_path):
    with engine.begin() as c:
        mine = c.execute(people.insert().values(
            first_name="Mine", last_name="Client", full_name=f"Mine {_TAG}",
            active=True).returning(people.c.id)).scalar_one()
        theirs = c.execute(people.insert().values(
            first_name="Theirs", last_name="Client", full_name=f"Theirs {_TAG}",
            active=True).returning(people.c.id)).scalar_one()
    _run([_item(tmp_path, name=f"Mine {_TAG}.pdf", person_id=mine)], destination_root=tmp_path / "dst")
    _run([_item(tmp_path, name=f"Theirs {_TAG}.pdf", person_id=theirs)], destination_root=tmp_path / "dst")
    ws = get_workspace(Principal(0, "a@e.test", "A", _CAPS), person_id=mine)
    names = {d["name"] for d in ws["sections"]["documents"]["documents"]}
    assert f"Mine {_TAG}.pdf" in names and f"Theirs {_TAG}.pdf" not in names


# --- dry run -----------------------------------------------------------------

def test_dry_run_makes_no_changes(tmp_path):
    dst = tmp_path / "dst"
    summary = _run([_item(tmp_path)], destination_root=dst, dry_run=True)
    assert summary["dry_run"] is True and summary["canonical_created"] == 1
    assert _sp_rows() == []
    assert not dst.exists() or list(dst.rglob("*")) == []
