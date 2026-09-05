"""Drake integration foundation — coverage.

Drake as a canonical SOURCE PROVIDER (ADR-072): discovery, SHA-256 canonical reuse (dedup across
sources), canonical creation, source references, client/household linking (ADR-073), incremental
sync, deleted-source handling, and the document_sources read that powers the Documents tab. Temp dirs
+ test rows only — no real Drake data.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, metadata, people
from app.importers import drake
from app.services.document_sources import (
    resolve_or_create_canonical,
    sources_for_document,
)

_TAG = "DRAKE"


@pytest.fixture(autouse=True)
def _clean():
    ds = metadata.tables["document_sources"]

    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            doc_ids = list(c.scalars(select(documents.c.id).where(documents.c.stored_name.like("drake:%"))))
            doc_ids += list(c.scalars(select(documents.c.id).where(documents.c.original_name.like(f"%{_TAG}%"))))
            if doc_ids:
                c.execute(delete(ds).where(ds.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            if pids:
                c.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    drake._database.cache_clear()
    yield
    _wipe()


def _dirs(tmp_path):
    src, dst = tmp_path / "drake", tmp_path / "dst"
    src.mkdir()
    return src, dst


def _sync(src, dst, **kw):
    return drake.sync(src, dst, progress=lambda *_a, **_k: None, **kw)


def _drake_rows(document_id=None):
    """Drake source references; pass ``document_id`` to scope the count to ONE document.

    An UNSCOPED count is only correct while no other test in the suite produces a Drake source
    reference, and that does not hold. SHA-256 canonical reuse (ADR-072) legitimately attaches a
    Drake reference to a document some other test created, and this file's name-pattern teardown
    cannot see that row to clean it up — so a global count picks up the survivor and reports a
    duplicate that never existed. Any assertion about idempotency must name its document."""
    ds = metadata.tables["document_sources"]
    stmt = select(ds).where(ds.c.source_system == "Drake")
    if document_id is not None:
        stmt = stmt.where(ds.c.document_id == document_id)
    with engine.connect() as c:
        return c.execute(stmt).mappings().all()


def _document_id_for(content: str) -> int:
    """The canonical document holding this content — resolved by SHA-256, the same identity the
    Drake importer resolves on."""
    sha = hashlib.sha256(content.encode()).hexdigest()
    with engine.connect() as c:
        return c.execute(select(documents.c.id).where(documents.c.sha256 == sha)).scalar_one()


# --- discovery + canonical creation + source ref -----------------------------

def test_discovery_creates_canonical_with_drake_source(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / f"White {_TAG}").mkdir()
    (src / f"White {_TAG}" / "2024 Federal 1040.pdf").write_text("federal return bytes")
    summary = _sync(src, dst)
    assert summary["canonical_created"] == 1 and summary["source_refs_added"] == 1
    assert (dst / f"White {_TAG}" / "2024 Federal 1040.pdf").exists()   # canonical local copy
    row = _drake_rows()[0]
    assert row["source_system"] == "Drake" and row["metadata"]["drake_doc_type"] == "federal_return"


def test_doc_type_classification():
    assert drake.drake_doc_type("2024 State CA Return.pdf") == "state_return"
    assert drake.drake_doc_type("IRS Acknowledgement.pdf") == "irs_ack"
    assert drake.drake_doc_type("Client Organizer.pdf") == "organizer"
    assert drake.drake_doc_type("Sch K1 Partner.pdf") == "k1"
    assert drake.drake_doc_type("export.xml") == "xml_export"


# --- SHA-256 reuse / dedup across sources ------------------------------------

def test_identical_document_reuses_canonical_and_adds_source_ref(tmp_path):
    src, dst = _dirs(tmp_path)
    content = "shared return bytes"
    sha = hashlib.sha256(content.encode()).hexdigest()
    # An existing canonical document from TaxDome with the same hash.
    from app.services.document_sources import add_source_reference
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=f"1040 {_TAG}.pdf", stored_name=f"taxdome:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/1040",
            size_bytes=len(content), sha256=sha, status="active", archived=False,
            tags={"source_system": "TaxDome Drive"}).returning(documents.c.id)).scalar_one()
        # Its TaxDome source reference (as the backfill migration would create in production).
        add_source_reference(c, did, source_system="TaxDome Drive", source_uri="Z:/1040", source_hash=sha)
    (src / f"White {_TAG}").mkdir()
    (src / f"White {_TAG}" / f"1040 {_TAG}.pdf").write_text(content)
    before = engine.connect().execute(select(documents.c.id).where(documents.c.sha256 == sha)).rowcount
    summary = _sync(src, dst)
    assert summary["reused_canonical"] == 1 and summary["canonical_created"] == 0
    after = engine.connect().execute(select(documents.c.id).where(documents.c.sha256 == sha)).rowcount
    assert after == before                                   # NO duplicate canonical document
    systems = {s["source_system"] for s in sources_for_document(did)}
    assert systems == {"TaxDome Drive", "Drake"}             # both sources reference one document


def test_resolve_or_create_direct_reuse():
    content = "direct bytes"
    sha = hashlib.sha256(content.encode()).hexdigest()
    r1 = resolve_or_create_canonical(
        sha256=sha, original_name=f"a {_TAG}.pdf", stored_name=f"drake:{_TAG}{uuid.uuid4().hex}",
        storage_provider="Client360 Local", storage_uri="/x/a", storage_path="a", size_bytes=11,
        source_system="Drake", source_uri="/drake/a")
    r2 = resolve_or_create_canonical(
        sha256=sha, original_name=f"a {_TAG}.pdf", stored_name=f"upload:{_TAG}{uuid.uuid4().hex}",
        storage_provider="Client360 Local", storage_uri="/x/a", storage_path="a", size_bytes=11,
        source_system="Upload", source_uri="/uploads/a")
    assert r1["reused"] is False and r2["reused"] is True and r1["document_id"] == r2["document_id"]


# --- client / household linking (ADR-073) ------------------------------------

def test_folder_name_does_not_assign_household_ownership(tmp_path):
    """INVERTED, deliberately. This test previously asserted ``doc == hid``.

    That assertion encoded the defect: Drake ingestion resolved the top-level folder name through the
    TaxDome ``resolve_folder`` matcher and wrote the result. A folder name matched against
    ``people.full_name`` is the weakest identity signal in the platform, and because every ownership
    write requires all three owner columns to be NULL, writing it at ingestion permanently locked out
    Drake's own SSN/EIN identifier-hash resolution for that document.

    The scenario below is the STRONGEST case the old behaviour had — a joint folder whose two names
    both match canonical people sharing one household, i.e. exactly the match ``resolve_folder`` is
    most confident about. Even here ingestion must leave the document unowned so that
    ``propose_drake_document_owner`` can evaluate it and a human can confirm. Ownership is not lost,
    it is deferred to a stronger mechanism.
    """
    src, dst = _dirs(tmp_path)
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"{_TAG} White Household").returning(
            households.c.id)).scalar_one()
        for nm in ("Michael White", "Debra White"):
            pid = c.execute(people.insert().values(
                first_name=nm.split()[0], last_name="White", full_name=nm, household_id=hid,
                active=True).returning(people.c.id)).scalar_one()
            c.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="member"))
    (src / "Michael and Debra White").mkdir()
    (src / "Michael and Debra White" / f"2024 1040 {_TAG}.pdf").write_text("joint")
    try:
        summary = _sync(src, dst)
        with engine.connect() as c:
            row = c.execute(select(documents.c.person_id, documents.c.household_id,
                                   documents.c.organization_id, documents.c.tags)
                            .where(documents.c.original_name == f"2024 1040 {_TAG}.pdf")).mappings().one()
        assert row["household_id"] is None                   # NOT linked by folder name
        assert row["person_id"] is None
        assert row["organization_id"] is None
        # The folder is still recorded as provenance — corroboration stays available downstream.
        assert row["tags"]["taxdome_folder"] == "Michael and Debra White"
        assert summary["left_unassigned"] == 1
    finally:
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.in_(["Michael White", "Debra White"]))))
            c.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(pids)))
            c.execute(delete(people).where(people.c.id.in_(pids)))


# --- incremental + deleted + idempotent -------------------------------------

def test_incremental_skips_unchanged(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / f"C {_TAG}").mkdir()
    (src / f"C {_TAG}" / f"doc {_TAG}.pdf").write_text("x")
    _sync(src, dst)
    summary = _sync(src, dst)
    assert summary["skipped"] == 1 and summary["canonical_created"] == 0


def test_idempotent_no_duplicate_source_refs(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / f"C {_TAG}").mkdir()
    # UNIQUE content. A one-byte fixture can share its SHA-256 with an unrelated test's synthetic
    # document, and ADR-072 would then correctly attach this Drake reference to THAT document —
    # making "the document this test produced" ambiguous. Unique content keeps it unambiguous.
    content = f"{_TAG} idempotency {uuid.uuid4().hex}"
    (src / f"C {_TAG}" / f"doc {_TAG}.pdf").write_text(content)
    _sync(src, dst)
    _sync(src, dst)
    document_id = _document_id_for(content)
    # Scoped to THIS document: re-syncing an unchanged file must refresh the existing reference
    # (uq_document_source_ref → on_conflict_do_update), never insert a second one.
    assert len(_drake_rows(document_id)) == 1


def test_deleted_source_marked_unavailable(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / f"C {_TAG}").mkdir()
    f = src / f"C {_TAG}" / f"gone {_TAG}.pdf"
    f.write_text("z")
    _sync(src, dst)
    f.unlink()
    summary = _sync(src, dst)
    assert summary["missing"] == 1
    row = _drake_rows()[0]
    assert row["available"] is False                         # source flagged; canonical/local retained
    # canonical document still exists
    with engine.connect() as c:
        assert c.scalar(select(documents.c.id).where(documents.c.id == row["document_id"])) is not None


# --- dry run -----------------------------------------------------------------

def test_dry_run_makes_no_changes(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / f"C {_TAG}").mkdir()
    (src / f"C {_TAG}" / f"doc {_TAG}.pdf").write_text("q")
    summary = _sync(src, dst, dry_run=True)
    assert summary["dry_run"] is True and summary["canonical_created"] == 1
    assert _drake_rows() == [] and not dst.exists() or list(dst.rglob("*")) == []
