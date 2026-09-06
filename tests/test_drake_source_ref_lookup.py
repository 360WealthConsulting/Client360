"""Deterministic Drake source-reference lookup.

``uq_document_source_ref`` is UNIQUE (document_id, source_system, source_uri), so a Drake
``source_uri`` is unique per document but NOT globally. One physical Drake file can therefore hold a
reference on two canonical documents at once. Production reached exactly that state: an out-of-band
migration registered a file against a document that a later cleanup soft-deleted, and the first
production sync — which must not reuse a deleted document as canonical — registered the same file
again against a fresh active document.

With an unordered ``.first()`` the importer's incremental lookup resolved arbitrarily between those
two rows, so an unchanged file could be skipped on one run and re-copied plus re-reconciled on the
next, and ``_resolve_source_external_id`` could read the recorded client id off either row.

These tests pin the preference: a reference that is available AND whose document is still active
always wins; a historical reference (unavailable, or on a soft-deleted document) is a fallback that
is returned only when nothing live exists. They also pin the invariant the preference must not
weaken — a soft-deleted document is still never resurrected as the canonical document.

Temp/test rows only, all tagged and torn down.
"""
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.db import engine, metadata
from app.importers import drake
from app.importers.drake import _existing_source_ref

documents = metadata.tables["documents"]
document_sources = metadata.tables["document_sources"]

BODY = b"%PDF-1.4 drake determinism fixture\n"
SHA = hashlib.sha256(BODY).hexdigest()


# ==================================================================================================
# Fixtures
# ==================================================================================================

@pytest.fixture
def tag():
    """A unique 8-hex marker, usable both as a Drake client id and as a cleanup key."""
    marker = uuid.uuid4().hex[:8].upper()
    yield marker
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(document_sources.c.document_id)
            .where(document_sources.c.source_system == "Drake",
                   document_sources.c.source_uri.contains(marker, autoescape=True)))]
        if ids:
            c.execute(delete(document_sources).where(document_sources.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _doc(c, tag, *, status):
    return c.execute(documents.insert().values(
        original_name=f"{tag} return.pdf", stored_name=f"drake:{tag}{uuid.uuid4().hex}",
        storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{tag}",
        size_bytes=len(BODY), sha256=SHA, status=status,
        archived=False).returning(documents.c.id)).scalar_one()


def _ref(c, document_id, uri, *, available, tag, meta=None):
    now = datetime.now(UTC)
    return c.execute(document_sources.insert().values(
        document_id=document_id, source_system="Drake", source_uri=uri, source_path=uri,
        source_external_id=tag, source_hash=SHA, available=available,
        first_seen_at=now, last_synced_at=now,
        metadata=meta or {}).returning(document_sources.c.id)).scalar_one()


def _lookup(uri):
    db = drake._database()
    with db.engine.connect() as conn:
        return _existing_source_ref(conn, db, uri)


def _uri(tag):
    return rf"C:\DRAKE23\DT\8\{tag}\Documents\{tag} return.pdf"


# ==================================================================================================
# Preference — the live reference wins regardless of physical row order
# ==================================================================================================

def test_prefers_the_live_reference_over_a_historical_one(tag):
    """1. Live row written FIRST (lower id): the newest-id tie-breaker must not overturn the tier."""
    uri = _uri(tag)
    with engine.begin() as c:
        live_doc = _doc(c, tag, status="active")
        _ref(c, live_doc, uri, available=True, tag=tag)
        dead_doc = _doc(c, tag, status="deleted")
        _ref(c, dead_doc, uri, available=False, tag=tag)

    assert _lookup(uri)["document_id"] == live_doc


def test_insertion_order_does_not_change_the_winner(tag):
    """2. Reversed: historical row written FIRST, live row second. Same winner."""
    uri = _uri(tag)
    with engine.begin() as c:
        dead_doc = _doc(c, tag, status="deleted")
        _ref(c, dead_doc, uri, available=False, tag=tag)
        live_doc = _doc(c, tag, status="active")
        _ref(c, live_doc, uri, available=True, tag=tag)

    assert _lookup(uri)["document_id"] == live_doc


@pytest.mark.parametrize("available,status", [
    (True, "deleted"),     # available, but its document is gone
    (False, "active"),     # document alive, but the source copy is gone
    (False, "deleted"),    # both gone — the production shape
])
def test_a_historical_reference_never_wins_over_a_live_one(tag, available, status):
    """3. Every non-live shape loses. 'Live' requires available AND a non-deleted document."""
    uri = _uri(tag)
    with engine.begin() as c:
        loser = _doc(c, tag, status=status)
        _ref(c, loser, uri, available=available, tag=tag)
        live_doc = _doc(c, tag, status="active")
        _ref(c, live_doc, uri, available=True, tag=tag)

    assert _lookup(uri)["document_id"] == live_doc


def test_repeated_lookups_agree(tag):
    """4. Determinism is the whole point: the same inputs must resolve the same way every call."""
    uri = _uri(tag)
    with engine.begin() as c:
        dead_doc = _doc(c, tag, status="deleted")
        _ref(c, dead_doc, uri, available=False, tag=tag)
        live_doc = _doc(c, tag, status="active")
        _ref(c, live_doc, uri, available=True, tag=tag)

    assert {_lookup(uri)["document_id"] for _ in range(8)} == {live_doc}


# ==================================================================================================
# Fallback, single-row and empty paths — unchanged behaviour
# ==================================================================================================

def test_a_lone_historical_reference_is_still_returned(tag):
    """5. The fallback matters: a lone historical row must keep suppressing a spurious 'new
    document' classification and keep supplying its recorded client id."""
    uri = _uri(tag)
    with engine.begin() as c:
        dead_doc = _doc(c, tag, status="deleted")
        _ref(c, dead_doc, uri, available=False, tag=tag)

    row = _lookup(uri)
    assert row is not None
    assert row["document_id"] == dead_doc
    assert row["source_external_id"] == tag


def test_a_single_live_reference_is_returned_unchanged(tag):
    """6. The ordinary fast path — one document, one reference — is untouched."""
    uri = _uri(tag)
    meta = {"size": len(BODY), "mtime": "2025-02-26T18:57:51.576269+00:00"}
    with engine.begin() as c:
        live_doc = _doc(c, tag, status="active")
        _ref(c, live_doc, uri, available=True, tag=tag, meta=meta)

    row = _lookup(uri)
    assert row["document_id"] == live_doc
    assert row["metadata"] == meta          # the skip check reads size/mtime off this row
    assert row["source_hash"] == SHA


def test_no_reference_returns_none(tag):
    """7. The no-ref path still reports None, which is what classifies a NEW candidate."""
    assert _lookup(_uri(tag)) is None


def test_multiple_live_references_resolve_to_the_newest(tag):
    """8. Should two live rows ever coexist, resolution is still total and stable."""
    uri = _uri(tag)
    with engine.begin() as c:
        older = _doc(c, tag, status="active")
        _ref(c, older, uri, available=True, tag=tag)
        newer = _doc(c, tag, status="active")
        _ref(c, newer, uri, available=True, tag=tag)

    assert _lookup(uri)["document_id"] == newer
    assert _lookup(uri)["document_id"] == newer


# ==================================================================================================
# The invariant the preference must not weaken
# ==================================================================================================

def _ddm_tree(root, client_id):
    folder = root / "DT" / "8" / client_id / "Documents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{client_id} return.pdf"
    path.write_bytes(BODY)
    return path


def test_sync_does_not_resurrect_a_soft_deleted_document(tag, tmp_path):
    """9. A historical reference on a soft-deleted document must NOT make the sync adopt that
    document. It stays deleted; the file lands on a new active canonical row."""
    source, dest = tmp_path / "DRAKE23", tmp_path / "dest"
    abs_path = _ddm_tree(source, tag)
    with engine.begin() as c:
        dead_doc = _doc(c, tag, status="deleted")
        _ref(c, dead_doc, str(abs_path), available=False, tag=tag)

    summary = drake.sync(source_root=source, destination_root=dest, progress=None)

    assert summary["canonical_created"] == 1        # a NEW document, not the deleted one
    assert summary["reused_canonical"] == 0
    with engine.connect() as c:
        assert c.execute(select(documents.c.status)
                         .where(documents.c.id == dead_doc)).scalar() == "deleted"
        live = c.execute(
            select(document_sources.c.document_id)
            .select_from(document_sources.join(documents,
                                               documents.c.id == document_sources.c.document_id))
            .where(document_sources.c.source_system == "Drake",
                   document_sources.c.source_uri == str(abs_path),
                   documents.c.status == "active")).scalars().all()
    assert len(live) == 1
    assert live[0] != dead_doc


def test_a_repeat_sync_is_stable_when_a_historical_duplicate_exists(tag, tmp_path):
    """10. The regression itself: with both rows present, a second run must resolve to the live one
    and skip, rather than flip-flopping into a re-copy."""
    source, dest = tmp_path / "DRAKE23", tmp_path / "dest"
    abs_path = _ddm_tree(source, tag)
    with engine.begin() as c:
        dead_doc = _doc(c, tag, status="deleted")
        _ref(c, dead_doc, str(abs_path), available=False, tag=tag)

    first = drake.sync(source_root=source, destination_root=dest, progress=None)
    assert first["canonical_created"] == 1

    for _ in range(3):
        again = drake.sync(source_root=source, destination_root=dest, progress=None)
        assert again["skipped"] == 1                 # resolved to the live row, every time
        assert again["canonical_created"] == 0
        assert again["reused_canonical"] == 0

    with engine.connect() as c:
        docs = c.execute(
            select(document_sources.c.document_id)
            .where(document_sources.c.source_system == "Drake",
                   document_sources.c.source_uri == str(abs_path))).scalars().all()
    assert len(docs) == 2                            # the historical row is preserved, not rewritten
    assert dead_doc in docs
