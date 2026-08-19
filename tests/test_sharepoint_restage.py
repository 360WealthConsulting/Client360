"""Tests for the targeted SharePoint re-stage recovery (app/services/sharepoint_restage.py).

Every test is hermetic: a unique ``uploaded_by`` tag and an out-of-band ``created_at`` window scope
each test to only its own inserted rows, so the assertions are independent of any other data in the
disposable test database. Apply-mode tests inject a fake connector (no network) and route
``backfill_local_source`` at a tmp destination, so the REAL SHA-verified backfill is exercised while
all files stay under tmp.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.db import document_ocr, documents, engine, metadata
from app.importers.sharepoint import backfill_local_source
from app.services import sharepoint_restage as rs

document_sources = metadata.tables["document_sources"]

# Out-of-band window + unique uploaded_by tag → this module's rows never collide with any other data.
WINDOW_FROM = datetime(2099, 1, 1, 0, 0, 0)
WINDOW_TO = datetime(2099, 1, 2, 0, 0, 0)
IN_WINDOW = datetime(2099, 1, 1, 12, 0, 0)
# The realistic isolated-path last_error; contains the marker the selector scopes on.
NOT_FOUND_ERROR = ("OCR failed at stage 'extract': FileNotFoundError: "
                   "OCR source file not found for document 123: None")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _insert_doc(*, uploaded_by, sha, storage_uri=None, storage_path="/nonexistent/missing.pdf",
                created_at=IN_WINDOW, status="active", ocr_status="failed",
                last_error=NOT_FOUND_ERROR, original_name="Return.pdf"):
    """Insert a canonical document (+ its OCR row) and return its id."""
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=original_name, stored_name=f"sp:{uuid.uuid4().hex}",
            storage_path=storage_path, storage_uri=storage_uri, storage_provider="Client360 Local",
            size_bytes=len(sha), sha256=sha, status=status, archived=False,
            uploaded_by=uploaded_by, created_at=created_at,
        ).returning(documents.c.id)).scalar_one()
        if ocr_status is not None:
            c.execute(document_ocr.insert().values(
                document_id=did, status=ocr_status, last_error=last_error, attempts=1))
    return did


def _insert_ref(document_id, *, item_id, source_hash, available=True, site="SiteA",
                library="LibA", source_uri=None):
    uri = source_uri or f"https://contoso.sharepoint.com/{item_id}"
    with engine.begin() as c:
        c.execute(document_sources.insert().values(
            document_id=document_id, source_system="SharePoint", source_uri=uri,
            source_path=f"LibA/{item_id}.pdf", source_external_id=item_id, source_hash=source_hash,
            available=available,
            metadata={"site": site, "library": library, "size": 10, "modified": None},
        ))


class FakeConnector:
    """Offline stand-in for RestageConnector: writes known bytes to the staged dest, records calls."""

    def __init__(self, blobs, *, drive_map=None, fail=()):
        self.blobs = blobs                       # item_id -> bytes written on download
        self.drive_map = drive_map               # (site, library) -> drive_id (None => resolvable as drive-<site>)
        self.fail = set(fail)                    # item_ids whose download raises
        self.downloaded: list[tuple[str, str]] = []

    def resolve_drive(self, site_id, library_name):
        if self.drive_map is not None:
            return self.drive_map.get((site_id, library_name))
        return f"drive-{site_id}"

    def download(self, drive_id, item_id, dest):
        self.downloaded.append((drive_id, item_id))
        if item_id in self.fail:
            raise RuntimeError("simulated transient download failure")
        data = self.blobs[item_id]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data), _sha(data)


@pytest.fixture
def tag():
    return f"SharePoint Sync {uuid.uuid4().hex[:8]}"


def _apply(connector, tmp_path, uploaded_by, **kw):
    return rs.run_restage(
        uploaded_by=uploaded_by, created_from=WINDOW_FROM, created_to=WINDOW_TO,
        apply=True, staging_root=tmp_path / "staging", connector=connector,
        backfill_fn=lambda did, sp: backfill_local_source(did, sp, destination_root=tmp_path / "canonical"),
        **kw)


def _cat(report, doc_id):
    return next(r.category for r in report.records if r.document_id == doc_id)


def _doc_row(doc_id):
    with engine.connect() as c:
        return dict(c.execute(select(documents).where(documents.c.id == doc_id)).mappings().one())


# --- 1. single-ref recovery --------------------------------------------------

def test_single_ref_recovery(tag, tmp_path):
    blob = b"a genuine pdf body"
    sha = _sha(blob)
    did = _insert_doc(uploaded_by=tag, sha=sha)
    _insert_ref(did, item_id="item-1", source_hash=sha)

    report = _apply(FakeConnector({"item-1": blob}), tmp_path, tag)

    assert _cat(report, did) == rs.CATEGORY_RECOVERED
    row = _doc_row(did)
    from pathlib import Path
    assert Path(row["storage_uri"]).exists()                     # OCR can now resolve the file
    assert _sha(Path(row["storage_uri"]).read_bytes()) == sha    # and it is exactly this document


# --- 2. multi-ref deterministic choice ---------------------------------------

def test_multi_ref_deterministic_choice(tag, tmp_path):
    # Two available refs, SAME content hash (same bytes in two SharePoint locations): safe, so the
    # lexicographically-first item_id is chosen deterministically — never both downloaded.
    blob = b"shared bytes"
    sha = _sha(blob)
    did = _insert_doc(uploaded_by=tag, sha=sha)
    _insert_ref(did, item_id="item-b", source_hash=sha)
    _insert_ref(did, item_id="item-a", source_hash=sha)
    conn = FakeConnector({"item-a": blob, "item-b": blob})

    report = _apply(conn, tmp_path, tag)

    assert _cat(report, did) == rs.CATEGORY_RECOVERED
    assert conn.downloaded == [("drive-SiteA", "item-a")]        # exactly one download, the min item_id


# --- 3. hash verification (positive) is covered by test_single_ref_recovery ---
#        and asserted directly here at the pure-function level.

def test_choose_ref_prefers_exact_hash_match(tag):
    doc_sha = _sha(b"canonical")
    refs = [
        {"source_external_id": "z", "source_uri": "u2", "source_hash": "other", "available": True,
         "metadata": {}},
        {"source_external_id": "a", "source_uri": "u1", "source_hash": doc_sha, "available": True,
         "metadata": {}},
    ]
    chosen, ambiguous, any_avail = rs.choose_ref(refs, doc_sha)
    assert not ambiguous and any_avail
    assert chosen["source_external_id"] == "a"                   # the exact-hash ref wins


# --- 4. hash mismatch --------------------------------------------------------

def test_hash_mismatch_does_not_backfill(tag, tmp_path):
    blob = b"downloaded body"
    did = _insert_doc(uploaded_by=tag, sha=_sha(blob))
    # The ref advertises a source_hash that the downloaded bytes will NOT match.
    _insert_ref(did, item_id="item-1", source_hash=_sha(b"a different content hash"))

    report = _apply(FakeConnector({"item-1": blob}), tmp_path, tag)

    assert _cat(report, did) == rs.CATEGORY_HASH_MISMATCH
    row = _doc_row(did)
    assert row["storage_uri"] is None                            # storage untouched — nothing marked usable
    assert row["storage_path"] == "/nonexistent/missing.pdf"


# --- 5. unavailable ref ------------------------------------------------------

def test_source_unavailable_when_no_available_ref(tag, tmp_path):
    did = _insert_doc(uploaded_by=tag, sha=_sha(b"x"))
    _insert_ref(did, item_id="gone", source_hash=_sha(b"x"), available=False)
    conn = FakeConnector({"gone": b"x"})

    report = _apply(conn, tmp_path, tag)

    assert _cat(report, did) == rs.CATEGORY_SOURCE_UNAVAILABLE
    assert conn.downloaded == []                                 # never attempted


# --- 6. already-present local file -------------------------------------------

def test_already_present_is_skipped(tag, tmp_path):
    existing = tmp_path / "already_here.pdf"
    existing.write_bytes(b"present")
    did = _insert_doc(uploaded_by=tag, sha=_sha(b"present"), storage_uri=str(existing))
    _insert_ref(did, item_id="item-1", source_hash=_sha(b"present"))
    conn = FakeConnector({"item-1": b"present"})

    report = _apply(conn, tmp_path, tag)

    assert _cat(report, did) == rs.CATEGORY_ALREADY_PRESENT
    assert conn.downloaded == []                                 # existing good file never overwritten


# --- 7. idempotent rerun -----------------------------------------------------

def test_apply_is_idempotent_on_rerun(tag, tmp_path):
    blob = b"idempotent body"
    sha = _sha(blob)
    did = _insert_doc(uploaded_by=tag, sha=sha)
    _insert_ref(did, item_id="item-1", source_hash=sha)

    first = _apply(FakeConnector({"item-1": blob}), tmp_path, tag)
    assert _cat(first, did) == rs.CATEGORY_RECOVERED

    conn2 = FakeConnector({"item-1": blob})
    second = _apply(conn2, tmp_path, tag)
    assert _cat(second, did) == rs.CATEGORY_ALREADY_PRESENT      # now resolves → no-op
    assert conn2.downloaded == []                                # and no second download


# --- 8. exact baseline / failure scoping -------------------------------------

def test_scoping_selects_only_missing_source_failures(tag):
    sha = _sha(b"scope")
    match = _insert_doc(uploaded_by=tag, sha=sha)               # qualifies
    _insert_ref(match, item_id="m", source_hash=sha)
    # Disqualifiers — each differs in exactly one scoping dimension:
    _insert_doc(uploaded_by=tag, sha=_sha(b"o1"), created_at=datetime(2098, 1, 1, 12, 0))   # out of window
    _insert_doc(uploaded_by="Other Uploader", sha=_sha(b"o2"))                               # wrong uploaded_by
    _insert_doc(uploaded_by=tag, sha=_sha(b"o3"), ocr_status="completed", last_error=None)   # not failed
    _insert_doc(uploaded_by=tag, sha=_sha(b"o4"), last_error="pdf text layer unreadable")    # other failure
    _insert_doc(uploaded_by=tag, sha=_sha(b"o5"), status="deleted")                          # deleted

    with engine.connect() as c:
        rows = rs.select_candidates(c, uploaded_by=tag, created_from=WINDOW_FROM, created_to=WINDOW_TO)
    assert [r["id"] for r in rows] == [match]


# --- 9. no ownership changes -------------------------------------------------

def test_recovery_never_touches_ownership_or_identity(tag, tmp_path):
    blob = b"ownership guard"
    sha = _sha(blob)
    did = _insert_doc(uploaded_by=tag, sha=sha)
    _insert_ref(did, item_id="item-1", source_hash=sha)
    before = _doc_row(did)

    _apply(FakeConnector({"item-1": blob}), tmp_path, tag)

    after = _doc_row(did)
    # ONLY storage_uri / storage_path may change; every other column is identical.
    changed = {k for k in before if before[k] != after[k]}
    assert changed <= {"storage_uri", "storage_path"}
    for k in ("person_id", "household_id", "organization_id", "uploaded_by", "sha256", "status",
              "original_name", "stored_name"):
        assert before[k] == after[k]


# --- 10. no source-reference deletion / mutation -----------------------------

def test_recovery_never_deletes_or_mutates_source_refs(tag, tmp_path):
    blob = b"refs intact"
    sha = _sha(blob)
    did = _insert_doc(uploaded_by=tag, sha=sha)
    _insert_ref(did, item_id="item-1", source_hash=sha)
    _insert_ref(did, item_id="item-2", source_hash=sha)

    def _refs():
        with engine.connect() as c:
            return [dict(r) for r in c.execute(select(document_sources)
                    .where(document_sources.c.document_id == did)
                    .order_by(document_sources.c.source_external_id)).mappings()]

    before = _refs()
    _apply(FakeConnector({"item-1": blob, "item-2": blob}), tmp_path, tag)
    after = _refs()
    assert before == after                                       # every source ref byte-for-byte unchanged


# --- extra: ambiguous multi-ref (differing content) --------------------------

def test_ambiguous_multi_ref_is_left_for_operator(tag, tmp_path):
    did = _insert_doc(uploaded_by=tag, sha=_sha(b"canonical"))
    # Two available refs with DIFFERENT source_hash and neither equal to the canonical sha →
    # cannot safely disambiguate which content is authoritative.
    _insert_ref(did, item_id="item-1", source_hash=_sha(b"variant one"))
    _insert_ref(did, item_id="item-2", source_hash=_sha(b"variant two"))
    conn = FakeConnector({"item-1": b"variant one", "item-2": b"variant two"})

    report = _apply(conn, tmp_path, tag)

    assert _cat(report, did) == rs.CATEGORY_AMBIGUOUS_MULTI_REF
    assert conn.downloaded == []                                 # nothing downloaded when ambiguous


# --- extra: download failure -------------------------------------------------

def test_download_failure_is_recorded_not_fatal(tag, tmp_path):
    good = b"good body"
    bad_doc_sha = _sha(b"bad body")
    d1 = _insert_doc(uploaded_by=tag, sha=_sha(good))
    _insert_ref(d1, item_id="ok", source_hash=_sha(good))
    d2 = _insert_doc(uploaded_by=tag, sha=bad_doc_sha)
    _insert_ref(d2, item_id="boom", source_hash=bad_doc_sha)
    conn = FakeConnector({"ok": good, "boom": b"bad body"}, fail=("boom",))

    report = _apply(conn, tmp_path, tag)

    assert _cat(report, d2) == rs.CATEGORY_DOWNLOAD_FAILED       # failure recorded
    assert _cat(report, d1) == rs.CATEGORY_RECOVERED             # the batch continued past it


# --- extra: preview has zero side effects ------------------------------------

def test_preview_makes_no_network_calls_and_no_writes(tag, tmp_path):
    blob = b"preview body"
    sha = _sha(blob)
    did = _insert_doc(uploaded_by=tag, sha=sha)
    _insert_ref(did, item_id="item-1", source_hash=sha)
    conn = FakeConnector({"item-1": blob})

    report = rs.run_restage(uploaded_by=tag, created_from=WINDOW_FROM, created_to=WINDOW_TO,
                            apply=False, connector=conn)

    assert report.mode == "preview"
    assert _cat(report, did) == rs.CATEGORY_PLANNED
    assert conn.downloaded == []                                 # preview never hits the connector
    row = _doc_row(did)
    assert row["storage_uri"] is None                            # and never writes storage
