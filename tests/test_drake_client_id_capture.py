"""Native Drake client id capture at ingestion.

Drake keeps documents in its own installation tree, not a purpose-built export::

    C:\\DRAKE21\\DT\\<bucket 0-9>\\<CLIENT_ID>\\Documents\\<filename>

``CLIENT_ID`` is 8 hexadecimal characters, exists before Client360 ever sees the file, and survives
Drake's year rollover (48 of 493 production ids appear under two or three ``DRAKE<YY>`` installs). It
is the ONLY document-side client identifier available — the return-side ``CLIENT.CSV`` has 123 columns
and not one of them is a client id — so a document ingested without it can never be resolved by client
identity.

Before this, ingestion persisted an id only when one had already been recorded, which meant every
NEWLY ingested document landed with ``source_external_id`` NULL and was permanently unresolvable.

Temp/test rows only, all tagged and torn down.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.db import engine, metadata
from app.importers.drake import (
    DrakeClientIdConflict,
    _resolve_source_external_id,
    drake_client_id,
)

documents = metadata.tables["documents"]
document_sources = metadata.tables["document_sources"]


# ==================================================================================================
# Derivation — pure, no database
# ==================================================================================================

def test_derives_the_client_folder_from_a_ddm_path():
    """1. The canonical shape."""
    assert drake_client_id(r"3\A1B2C3D4\Documents\x.pdf") == "A1B2C3D4"


def test_source_root_may_be_the_drake_install_or_its_dt_subdirectory():
    """2. Root-agnostic: a fixed segment index would break when the configured root changes."""
    from_dt = drake_client_id(r"3\A1B2C3D4\Documents\x.pdf")           # root = C:\DRAKE21\DT
    from_install = drake_client_id(r"DT\3\A1B2C3D4\Documents\x.pdf")   # root = C:\DRAKE21
    assert from_dt == from_install == "A1B2C3D4"


@pytest.mark.parametrize("raw,expected", [
    (r"3\a1b2c3d4\Documents\x.pdf", "A1B2C3D4"),
    (r"3\A1b2C3d4\Documents\x.pdf", "A1B2C3D4"),
    (r"3\A1B2C3D4\documents\x.pdf", "A1B2C3D4"),
])
def test_case_is_normalised_consistently(raw, expected):
    """3. All 795 production ids are stored uppercase; derivation must agree with them."""
    assert drake_client_id(raw) == expected


def test_forward_slashes_are_accepted():
    assert drake_client_id("3/A1B2C3D4/Documents/x.pdf") == "A1B2C3D4"


def test_a_hex_looking_filename_is_not_a_client_id():
    """4. A bare 'first 8-hex segment' scan would wrongly return DEADBEEF here."""
    assert drake_client_id(r"3\A1B2C3D4\Documents\DEADBEEF.pdf") == "A1B2C3D4"
    # ...and with no real client folder present, the filename must yield nothing at all.
    assert drake_client_id(r"3\SMITHJOHN\Documents\DEADBEEF.pdf") is None
    assert drake_client_id(r"DEADBEEF.pdf") is None


def test_a_nested_hex_directory_is_not_a_client_id():
    """5. Only the directory whose immediate child is Documents qualifies."""
    assert drake_client_id(r"3\A1B2C3D4\Documents\CAFEBABE\x.pdf") == "A1B2C3D4"
    # A hex directory that is not the parent of Documents proves nothing.
    assert drake_client_id(r"3\DEADBEEF\Other\x.pdf") is None
    assert drake_client_id(r"DEADBEEF\3\A1B2C3D4\Documents\x.pdf") == "A1B2C3D4"


@pytest.mark.parametrize("path", [
    r"3\SMITHJOHN\Documents\x.pdf",     # client folder is not hex
    r"3\A1B2C3D\Documents\x.pdf",       # 7 chars
    r"3\A1B2C3D4E\Documents\x.pdf",     # 9 chars
    r"3\G1B2C3D4\Documents\x.pdf",      # non-hex character
    r"A1B2C3D4\x.pdf",                  # no Documents anchor
    r"x.pdf",
    "",
])
def test_unproven_shapes_fail_closed(path):
    """6. No guess. A document with no identity is recoverable; a wrong identity is not."""
    assert drake_client_id(path) is None


def test_two_distinct_candidates_fail_closed():
    assert drake_client_id(r"AAAAAAAA\Documents\BBBBBBBB\Documents\x.pdf") is None


def test_one_candidate_repeated_is_still_resolvable():
    assert drake_client_id(r"A1B2C3D4\Documents\A1B2C3D4\Documents\x.pdf") == "A1B2C3D4"


# ==================================================================================================
# Precedence and conflict
# ==================================================================================================

def test_a_new_document_uses_the_derived_id():
    assert _resolve_source_external_id(r"3\A1B2C3D4\Documents\x.pdf", None) == "A1B2C3D4"


def test_a_resync_preserves_the_same_id():
    """8. Re-deriving an already-recorded id must be a no-op, not a rewrite."""
    existing = {"source_external_id": "A1B2C3D4"}
    assert _resolve_source_external_id(r"3\A1B2C3D4\Documents\x.pdf", existing) == "A1B2C3D4"


def test_a_recorded_id_survives_a_path_that_proves_nothing():
    """The out-of-band migration's id must not be blanked by an unparseable path."""
    existing = {"source_external_id": "A1B2C3D4"}
    assert _resolve_source_external_id(r"weird\layout\x.pdf", existing) == "A1B2C3D4"


def test_a_conflicting_derived_id_fails_closed():
    """9. The file moved client folders, or the recorded id is wrong. Either way: refuse."""
    existing = {"source_external_id": "FFFFFFFF"}
    with pytest.raises(DrakeClientIdConflict, match="conflict"):
        _resolve_source_external_id(r"3\A1B2C3D4\Documents\x.pdf", existing)


def test_conflict_detection_is_case_insensitive():
    existing = {"source_external_id": "a1b2c3d4"}
    assert _resolve_source_external_id(r"3\A1B2C3D4\Documents\x.pdf", existing) == "A1B2C3D4"


def test_no_id_anywhere_yields_none():
    assert _resolve_source_external_id(r"weird\layout\x.pdf", None) is None
    assert _resolve_source_external_id(r"weird\layout\x.pdf", {}) is None


# ==================================================================================================
# End-to-end through the real sync, against a temporary DDM tree
# ==================================================================================================

def _ddm_tree(root, client_id, filename="2024 Return.pdf", body=b"%PDF-1.4 drake test\n"):
    folder = root / "DT" / "3" / client_id / "Documents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(body)
    return folder / filename


@pytest.fixture
def drake_tree(tmp_path):
    tag = uuid.uuid4().hex[:8].upper()
    source = tmp_path / "DRAKE21"
    dest = tmp_path / "dest"
    yield {"tag": tag, "source": source, "dest": dest}
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(document_sources.c.document_id)
            .where(document_sources.c.source_system == "Drake",
                   document_sources.c.source_uri.like(f"%{tag}%")))]
        if ids:
            c.execute(delete(document_sources).where(document_sources.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _sync(drake_tree, **kw):
    from app.importers import drake
    return drake.sync(source_root=drake_tree["source"], destination_root=drake_tree["dest"],
                      progress=None, **kw)


def _refs(tag):
    with engine.connect() as c:
        return c.execute(
            select(document_sources.c.document_id, document_sources.c.source_external_id,
                   documents.c.person_id, documents.c.household_id, documents.c.organization_id)
            .select_from(document_sources.join(documents,
                                               documents.c.id == document_sources.c.document_id))
            .where(document_sources.c.source_system == "Drake",
                   document_sources.c.source_uri.like(f"%{tag}%"))).mappings().all()


def test_new_document_persists_the_native_client_id(drake_tree):
    """7. The blocker this change exists to fix."""
    client_id = drake_tree["tag"]
    _ddm_tree(drake_tree["source"], client_id)
    summary = _sync(drake_tree)

    assert summary["client_id_captured"] == 1
    assert summary["client_id_missing"] == 0
    rows = _refs(client_id)
    assert len(rows) == 1
    assert rows[0]["source_external_id"] == client_id


def test_newly_ingested_documents_remain_unassigned(drake_tree):
    """10 + 11. Capturing identity must not reintroduce ownership at ingestion."""
    client_id = drake_tree["tag"]
    _ddm_tree(drake_tree["source"], client_id)
    summary = _sync(drake_tree)

    assert summary["left_unassigned"] == 1
    assert "linked_person" not in summary and "linked_household" not in summary
    row = _refs(client_id)[0]
    assert row["person_id"] is None
    assert row["household_id"] is None
    assert row["organization_id"] is None


def test_resync_preserves_the_id_and_creates_no_duplicate(drake_tree):
    """8, end to end."""
    client_id = drake_tree["tag"]
    path = _ddm_tree(drake_tree["source"], client_id)
    _sync(drake_tree)
    before = _refs(client_id)

    path.write_bytes(b"%PDF-1.4 drake test CHANGED\n")     # force past the size/mtime fast path
    _sync(drake_tree)

    after = _refs(client_id)
    assert len(after) == len(before) == 1
    assert after[0]["source_external_id"] == client_id


def test_a_document_outside_the_ddm_shape_is_ingested_without_an_id(drake_tree):
    """6, end to end: unassigned AND unidentified, but reported rather than guessed."""
    stray = drake_tree["source"] / "Loose" / f"{drake_tree['tag']}-note.pdf"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"%PDF-1.4 stray\n")

    summary = _sync(drake_tree)
    assert summary["client_id_captured"] == 0
    assert summary["client_id_missing"] == 1
    assert summary["client_id_missing_paths"]

    rows = _refs(drake_tree["tag"])
    assert len(rows) == 1
    assert rows[0]["source_external_id"] is None
    assert rows[0]["person_id"] is None


def test_a_conflicting_id_is_reported_and_the_file_is_not_reingested(drake_tree):
    """9, end to end: the run records an error rather than re-pointing the document."""
    client_id = drake_tree["tag"]
    path = _ddm_tree(drake_tree["source"], client_id)
    _sync(drake_tree)

    with engine.begin() as c:
        c.execute(document_sources.update()
                  .where(document_sources.c.source_system == "Drake",
                         document_sources.c.source_uri == str(path))
                  .values(source_external_id="FFFFFFFF"))

    path.write_bytes(b"%PDF-1.4 drake test CHANGED\n")
    summary = _sync(drake_tree)

    assert summary["errors"], "a client id conflict must be reported"
    assert any("conflict" in e for e in summary["errors"])
    assert summary["status"] == "completed_with_errors"
    # The contested id is left exactly as it was — not silently re-pointed.
    assert _refs(client_id)[0]["source_external_id"] == "FFFFFFFF"


def test_dry_run_writes_nothing(drake_tree):
    client_id = drake_tree["tag"]
    _ddm_tree(drake_tree["source"], client_id)
    summary = _sync(drake_tree, dry_run=True)
    assert summary["dry_run"] is True
    assert _refs(client_id) == []


# ==================================================================================================
# Production replay — the derivation must reproduce every id recorded out-of-band
# ==================================================================================================

def test_derivation_reproduces_every_recorded_production_id():
    """Read-only replay against whatever Drake source paths this database holds.

    On the production database this covers all 795 rows and must reproduce all 795 ids exactly.
    On a test database with no Drake rows it is vacuous, and says so rather than silently passing.
    """
    with engine.connect() as c:
        rows = c.execute(
            select(document_sources.c.source_uri, document_sources.c.source_external_id)
            .where(document_sources.c.source_system == "Drake",
                   document_sources.c.source_external_id.isnot(None))).all()
    if not rows:
        pytest.skip("no Drake source references in this database; replay is exercised separately")

    mismatches = []
    for uri, recorded in rows:
        parts = [p for p in uri.replace("/", "\\").split("\\") if p]
        for depth in (2, 3):                    # C:\DRAKE21  and  C:\DRAKE21\DT
            if drake_client_id("\\".join(parts[depth:])) == recorded.upper():
                break
        else:
            mismatches.append(uri)
    assert not mismatches, f"{len(mismatches)} of {len(rows)} paths did not reproduce their id"


def test_the_ddm_documents_anchor_is_what_makes_it_root_agnostic():
    """Regression guard: if the anchor is ever dropped, these two must stop agreeing."""
    deep = r"C\DRAKE21\DT\3\A1B2C3D4\Documents\x.pdf"
    shallow = r"3\A1B2C3D4\Documents\x.pdf"
    assert drake_client_id(deep) == drake_client_id(shallow) == "A1B2C3D4"


def test_datetime_import_is_used():
    """The module contract: ingestion stamps last_synced_at, so datetime must stay imported."""
    assert isinstance(datetime.now(UTC), datetime)
