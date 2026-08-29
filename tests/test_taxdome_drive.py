"""TaxDome Drive one-way local document sync — coverage.

Verifies the sync treats the source drive as read-only, maintains durable verified local copies under a
destination root (never Z:\\), is idempotent, retains local copies when a source file disappears (and
only removes them with the explicit --purge-missing flag), never exposes a partial file on a failed
copy, handles duplicate filenames across folders, blocks path traversal, links folders to people
conservatively, makes dry-run a no-op, and surfaces local copies through the existing person-document
query + download route. All tests use temporary directories — no production data is touched.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath

import pytest
from starlette.requests import Request

from sqlalchemy import text as sa_text

from app.db import documents, engine, households, people
from app.demo import taxdome_drive as page
from app.importers import taxdome_drive as td
from app.security.models import Principal
from app.services.documents import get_person_documents

_TEST_NAMES = ("Taylor Hawthorne", "Okoro Family", "Delgado, Alex", "Michael White", "Debra White")


@pytest.fixture(autouse=True)
def _clean_taxdome():
    """Isolate the shared test DB: remove TaxDome docs + test people/households before and after."""
    def _wipe():
        with engine.begin() as conn:
            conn.execute(documents.delete().where(td.taxdome_filter(documents)))
            conn.execute(people.delete().where(people.c.full_name.in_(_TEST_NAMES)))
            conn.execute(households.delete().where(households.c.name.like("TESTHH%")))
    _wipe()
    td._database.cache_clear()
    yield
    _wipe()


def _household(name="TESTHH White"):
    with engine.begin() as conn:
        return conn.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()


def _person_in(full_name, household_id=None):
    with engine.begin() as conn:
        return conn.execute(people.insert().values(
            full_name=full_name, household_id=household_id).returning(people.c.id)).scalar_one()


def _tree(root: Path) -> Path:
    (root / "Hawthorne, Taylor").mkdir(parents=True)
    (root / "Hawthorne, Taylor" / "2025 W-2.pdf").write_text("w2 content")
    (root / "Hawthorne, Taylor" / "sub").mkdir()
    (root / "Hawthorne, Taylor" / "sub" / "1099-DIV.pdf").write_text("1099")
    (root / "Okoro Family").mkdir()
    (root / "Okoro Family" / "Engagement Agreement.pdf").write_text("agreement")
    return root


def _dirs(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    return src, dst


def _sync(src, dst, **kw):
    return td.sync(src, dst, progress=lambda *_a, **_k: None, **kw)


def _taxdome_rows():
    with engine.connect() as conn:
        return conn.execute(documents.select().where(td.taxdome_filter(documents))).mappings().all()


def _person(full_name):
    with engine.begin() as conn:
        return conn.execute(people.insert().values(full_name=full_name).returning(people.c.id)).scalar_one()


# --- new file copied ---------------------------------------------------------

def test_new_file_is_copied_and_recorded(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    summary = _sync(src, dst)
    assert summary["copied"] == 3 and summary["updated"] == 0 and summary["status"] == "completed"
    # durable local copies exist beneath the destination, preserving source-relative structure
    local = dst / "Hawthorne, Taylor" / "2025 W-2.pdf"
    assert local.exists() and local.read_text() == "w2 content"
    row = next(r for r in _taxdome_rows() if r["original_name"] == "2025 W-2.pdf")
    assert row["storage_provider"] == "Client360 Local"
    assert row["storage_uri"] == str(local.resolve())
    assert row["storage_path"] == os.path.join("Hawthorne, Taylor", "2025 W-2.pdf")
    assert len(row["sha256"]) == 64 and row["size_bytes"] == len("w2 content")
    assert row["tags"]["source_system"] == "TaxDome Drive"
    assert row["tags"]["available_from_source"] is True and row["tags"]["retained_locally"] is True
    assert row["tags"]["taxdome_folder"] == "Hawthorne, Taylor"


def test_source_drive_is_never_modified(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    before = {p.relative_to(src).as_posix(): p.read_text() for p in src.rglob("*") if p.is_file()}
    _sync(src, dst)
    after = {p.relative_to(src).as_posix(): p.read_text() for p in src.rglob("*") if p.is_file()}
    assert after == before                                   # read-only source


# --- unchanged skipped / idempotent -----------------------------------------

def test_unchanged_file_is_skipped(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    summary = _sync(src, dst)
    assert summary["copied"] == 0 and summary["updated"] == 0 and summary["skipped"] == 3


def test_repeated_runs_are_idempotent(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    local = dst / "Okoro Family" / "Engagement Agreement.pdf"
    first = (local.read_bytes(), local.stat().st_mtime)
    for _ in range(2):
        summary = _sync(src, dst)
        assert summary["copied"] == 0 and summary["updated"] == 0
    assert len(_taxdome_rows()) == 3                          # no duplicate rows
    assert (local.read_bytes(), local.stat().st_mtime) == first    # untouched on disk


# --- changed file updated ----------------------------------------------------

def test_changed_file_is_updated(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    (src / "Okoro Family" / "Engagement Agreement.pdf").write_text("agreement UPDATED CONTENT longer")
    summary = _sync(src, dst)
    assert summary["updated"] == 1 and summary["skipped"] == 2 and summary["copied"] == 0
    local = dst / "Okoro Family" / "Engagement Agreement.pdf"
    assert local.read_text() == "agreement UPDATED CONTENT longer"


def test_same_size_changed_content_detected_via_hash(tmp_path):
    src, dst = _dirs(tmp_path)
    src_file = src / "Okoro Family"
    src_file.mkdir(parents=True)
    (src_file / "note.txt").write_text("AAAA")
    _sync(src, dst)
    p = src_file / "note.txt"
    p.write_text("BBBB")                                     # same length, different content
    os.utime(p, (p.stat().st_atime + 5, p.stat().st_mtime + 5))
    summary = _sync(src, dst)
    assert summary["updated"] == 1
    assert (dst / "Okoro Family" / "note.txt").read_text() == "BBBB"


# --- source deletion retains local copy -------------------------------------

def test_source_deletion_retains_local_copy(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    (src / "Okoro Family" / "Engagement Agreement.pdf").unlink()
    summary = _sync(src, dst)
    assert summary["missing"] == 1 and summary["purged"] == 0
    local = dst / "Okoro Family" / "Engagement Agreement.pdf"
    assert local.exists()                                    # local copy retained
    row = next(r for r in _taxdome_rows() if r["original_name"] == "Engagement Agreement.pdf")
    assert row["status"] == "active" and row["archived"] is False      # not archived by disappearance
    assert row["tags"]["available_from_source"] is False
    assert row["tags"]["retained_locally"] is True and row["tags"]["source_status"] == "missing"


def test_missing_is_not_recounted_on_subsequent_runs(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    (src / "Okoro Family" / "Engagement Agreement.pdf").unlink()
    assert _sync(src, dst)["missing"] == 1
    assert _sync(src, dst)["missing"] == 0                   # already flagged; not repeatedly counted


# --- optional purge ----------------------------------------------------------

def test_purge_missing_removes_local_copy_only_when_requested(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    (src / "Okoro Family" / "Engagement Agreement.pdf").unlink()
    local = dst / "Okoro Family" / "Engagement Agreement.pdf"
    # default sync keeps it
    _sync(src, dst)
    assert local.exists()
    # explicit purge removes it and archives the record
    summary = _sync(src, dst, purge_missing=True)
    assert summary["purged"] == 1
    assert not local.exists()
    row = next(r for r in _taxdome_rows() if r["original_name"] == "Engagement Agreement.pdf")
    assert row["archived"] is True and row["status"] == "archived"
    assert row["tags"]["retained_locally"] is False and row["tags"]["available_from_source"] is False


# --- failed copy leaves no partial file -------------------------------------

def test_failed_copy_leaves_no_partial_destination(tmp_path, monkeypatch):
    src, dst = _dirs(tmp_path)
    _tree(src)

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(a, b):
        # Fail the atomic swap for the very first file, succeed afterwards.
        if calls["n"] == 0:
            calls["n"] += 1
            raise OSError("simulated atomic-replace failure")
        return real_replace(a, b)

    monkeypatch.setattr(td.os, "replace", flaky_replace)
    summary = _sync(src, dst)
    assert len(summary["errors"]) == 1                       # the failure was recorded
    assert summary["copied"] == 2                            # processing continued past the failure
    # No partial or temp file anywhere in the destination tree.
    leftovers = [p.name for p in dst.rglob("*") if p.is_file() and (".part" in p.name or p.name.startswith(".sync-"))]
    assert leftovers == []
    assert len(_taxdome_rows()) == 2                         # the failed file produced no row


# --- duplicate filenames across folders -------------------------------------

def test_duplicate_filenames_in_different_folders(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / "Folder A").mkdir()
    (src / "Folder B").mkdir()
    (src / "Folder A" / "W-2.pdf").write_text("A")
    (src / "Folder B" / "W-2.pdf").write_text("B")
    summary = _sync(src, dst)
    assert summary["copied"] == 2
    assert (dst / "Folder A" / "W-2.pdf").read_text() == "A"
    assert (dst / "Folder B" / "W-2.pdf").read_text() == "B"
    uris = {r["storage_uri"] for r in _taxdome_rows()}
    assert len(uris) == 2                                    # two distinct rows / destinations


# --- path traversal protection ----------------------------------------------

@pytest.mark.parametrize("evil", ["../etc/passwd", "a/../../secret", r"C:\Windows\system32",
                                  "/abs/path", "..\\..\\x"])
def test_sanitize_relative_path_blocks_traversal(evil):
    with pytest.raises(ValueError):
        td.sanitize_relative_path(evil)


def test_sanitize_relative_path_allows_normal_nested_path():
    assert td.sanitize_relative_path("Okoro Family/2025/W-2.pdf") == PurePosixPath("Okoro Family/2025/W-2.pdf")


def test_sanitize_relative_path_allows_legitimate_tilde_filename():
    # A leading "~" is a valid filename character (only "~$…" Office lock files are ignored elsewhere).
    assert td.sanitize_relative_path("Okoro Family/~budget.xlsx") == PurePosixPath("Okoro Family/~budget.xlsx")


def test_destination_path_rejects_escape(tmp_path):
    root = tmp_path / "dst"
    root.mkdir()
    with pytest.raises(ValueError):
        td._destination_path(root, PurePosixPath("..") / "outside.txt")


# --- person linking ----------------------------------------------------------

def test_folder_autolinks_on_unique_exact_name_only(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    pid = _person("Taylor Hawthorne")                        # exact normalized match to "Hawthorne, Taylor"
    _sync(src, dst)
    linked = [r for r in _taxdome_rows() if r["person_id"] == pid]
    assert len(linked) == 2                                  # both Hawthorne files linked
    okoro = [r for r in _taxdome_rows() if r["tags"]["taxdome_folder"] == "Okoro Family"]
    assert all(r["person_id"] is None for r in okoro)        # no match -> unresolved


def test_ambiguous_name_is_not_autolinked(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _person("Taylor Hawthorne")
    _person("Taylor Hawthorne")                              # duplicate -> ambiguous -> no auto-link
    _sync(src, dst)
    assert all(r["person_id"] is None for r in _taxdome_rows())


def test_unresolved_folder_available_for_review(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    with engine.connect() as conn:
        unresolved = {u["folder"] for u in page._unresolved_folders(conn)}
    assert "Okoro Family" in unresolved and "Hawthorne, Taylor" in unresolved


# --- dry run -----------------------------------------------------------------

def test_dry_run_makes_no_file_or_database_changes(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    summary = _sync(src, dst, dry_run=True)
    assert summary["dry_run"] is True and summary["status"] == "dry_run"
    assert summary["copied"] == 3                            # reports what WOULD be copied
    assert not dst.exists() or list(dst.rglob("*")) == []    # no files written
    assert _taxdome_rows() == []                             # no documents rows created


def test_dry_run_after_sync_reports_no_pending_changes(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    summary = _sync(src, dst, dry_run=True)
    assert summary["copied"] == 0 and summary["updated"] == 0 and summary["skipped"] == 3


# --- local document appears through the existing person query + download ------

def test_local_document_appears_in_person_documents_and_downloads_local_copy(tmp_path):
    from app.routes.documents import download_document
    src, dst = _dirs(tmp_path)
    _tree(src)
    pid = _person("Taylor Hawthorne")
    _sync(src, dst)
    docs = get_person_documents(pid)                         # the EXISTING person-documents query
    names = {d["original_name"] for d in docs}
    assert "2025 W-2.pdf" in names and "1099-DIV.pdf" in names
    doc = next(d for d in docs if d["original_name"] == "2025 W-2.pdf")
    assert doc["storage_provider"] == "Client360 Local"
    # download serves the Client360 local copy, not the source drive
    resp = download_document(doc["id"], Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "state": {}}))
    assert Path(resp.path) == (dst / "Hawthorne, Taylor" / "2025 W-2.pdf").resolve()
    assert str(src) not in str(resp.path)


def test_retained_but_unavailable_document_still_downloads(tmp_path):
    from app.routes.documents import download_document
    src, dst = _dirs(tmp_path)
    _tree(src)
    pid = _person("Taylor Hawthorne")
    _sync(src, dst)
    (src / "Hawthorne, Taylor" / "2025 W-2.pdf").unlink()
    _sync(src, dst)                                          # source gone; local retained
    doc = next(d for d in get_person_documents(pid) if d["original_name"] == "2025 W-2.pdf")
    resp = download_document(doc["id"], Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "state": {}}))                      # retained copy still serves
    assert Path(resp.path).read_text() == "w2 content"


# --- legacy metadata-only row migration --------------------------------------

def _legacy_row(src, abs_path, folder, *, person_id=None):
    """Insert a row exactly as the OLD metadata-only importer did: stored_name keyed off the ABSOLUTE
    source path, storage_provider "TaxDome Drive", storage_uri = the Z:\\ path, no local copy."""
    rel = os.path.relpath(abs_path, src)
    content = Path(abs_path).read_bytes()
    with engine.begin() as conn:
        return conn.execute(documents.insert().values(
            stored_name=td._legacy_stored_name(abs_path),
            original_name=Path(abs_path).name, storage_path=rel, storage_provider="TaxDome Drive",
            storage_uri=abs_path, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
            category="tax_document", person_id=person_id, status="active", archived=False,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder, "relative_path": rel,
                  "available": True}).returning(documents.c.id)).scalar_one()


def test_legacy_absolute_path_row_upgraded_in_place(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    abs_path = str(src / "Okoro Family" / "Engagement Agreement.pdf")
    legacy_id = _legacy_row(src, abs_path, "Okoro Family")
    summary = _sync(src, dst)
    assert summary["legacy_rows_upgraded"] >= 1
    # SAME row id, now converted to the new relative-path key + local provider + verified local copy
    row = next(r for r in _taxdome_rows() if r["id"] == legacy_id)
    assert row["stored_name"] == td._stored_name(os.path.relpath(abs_path, src))
    assert row["storage_provider"] == "Client360 Local"
    assert row["storage_uri"] == str((dst / "Okoro Family" / "Engagement Agreement.pdf").resolve())
    assert (dst / "Okoro Family" / "Engagement Agreement.pdf").exists()


def test_legacy_upgrade_preserves_person_id(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    pid = _person("Delgado, Alex")
    abs_path = str(src / "Okoro Family" / "Engagement Agreement.pdf")
    legacy_id = _legacy_row(src, abs_path, "Okoro Family", person_id=pid)
    _sync(src, dst)
    row = next(r for r in _taxdome_rows() if r["id"] == legacy_id)
    assert row["person_id"] == pid                           # linkage preserved through the upgrade


def test_legacy_upgrade_creates_no_duplicate(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)                                               # 3 files
    for folder, name in (("Hawthorne, Taylor", "2025 W-2.pdf"),
                         ("Hawthorne, Taylor", "sub/1099-DIV.pdf"),
                         ("Okoro Family", "Engagement Agreement.pdf")):
        _legacy_row(src, str(src / folder / name), folder.split("/")[0] if "/" not in folder else folder)
    _sync(src, dst)
    assert len(_taxdome_rows()) == 3                         # upgraded in place; no duplicate rows


def test_mixed_legacy_and_new_rows_reconciled(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    pid = _person("Delgado, Alex")
    abs_path = str(src / "Okoro Family" / "Engagement Agreement.pdf")
    _sync(src, dst)                                          # creates a new-style row (no person)
    _legacy_row(src, abs_path, "Okoro Family", person_id=pid)   # legacy row for the SAME file, linked
    # Dependent intelligence on the row that SURVIVES must come through the reconcile intact.
    legacy_id = [r["id"] for r in _taxdome_rows() if r["person_id"] == pid][0]
    _dependent_ocr(legacy_id, text="engagement agreement text")
    summary = _sync(src, dst)
    assert summary["reconciled"] >= 1
    matches = [r for r in _taxdome_rows() if r["original_name"] == "Engagement Agreement.pdf"]
    assert len(matches) == 1                                 # reconciled to one canonical row
    assert matches[0]["person_id"] == pid                    # the linked row was preserved
    assert matches[0]["stored_name"] == td._stored_name(os.path.relpath(abs_path, src))
    assert _ocr_text(legacy_id) == "engagement agreement text", \
        "the surviving row's OCR text must not be destroyed by reconciliation"


def test_missing_count_correct_after_legacy_matching(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    for folder, name in (("Hawthorne, Taylor", "2025 W-2.pdf"), ("Okoro Family", "Engagement Agreement.pdf")):
        _legacy_row(src, str(src / folder / name), folder)
    summary = _sync(src, dst)
    assert summary["missing"] == 0                           # legacy rows matched, NOT reported missing


def test_dry_run_reports_legacy_rows_to_upgrade(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _legacy_row(src, str(src / "Okoro Family" / "Engagement Agreement.pdf"), "Okoro Family")
    summary = _sync(src, dst, dry_run=True)
    assert summary["legacy_rows_to_upgrade"] == 1 and summary["missing"] == 0
    assert _taxdome_rows()[0]["storage_provider"] == "TaxDome Drive"   # unchanged by dry-run


def test_legacy_migration_is_idempotent(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _legacy_row(src, str(src / "Okoro Family" / "Engagement Agreement.pdf"), "Okoro Family")
    _sync(src, dst)
    summary = _sync(src, dst)
    assert summary["legacy_rows_upgraded"] == 0 and summary["copied"] == 0 and summary["updated"] == 0
    assert summary["skipped"] == 3 and len(_taxdome_rows()) == 3


# --- temp / system files ignored ---------------------------------------------

def test_office_lock_and_system_files_are_ignored(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / "Okoro Family").mkdir(parents=True)
    (src / "Okoro Family" / "Engagement Agreement.pdf").write_text("agreement")
    (src / "Okoro Family" / "~$gagement Agreement.pdf").write_text("office lock")
    (src / "Okoro Family" / "draft.tmp").write_text("tmp")
    (src / "Okoro Family" / "Thumbs.db").write_text("thumbs")
    (src / "Okoro Family" / "desktop.ini").write_text("ini")
    summary = _sync(src, dst)
    assert summary["ignored"] == 4 and summary["errors"] == []
    assert summary["copied"] == 1                            # only the real document
    assert not (dst / "Okoro Family" / "~$gagement Agreement.pdf").exists()


def test_legitimate_tilde_file_is_synced(tmp_path):
    src, dst = _dirs(tmp_path)
    (src / "Okoro Family").mkdir(parents=True)
    (src / "Okoro Family" / "~budget.xlsx").write_text("budget")   # legit name, not a lock file
    summary = _sync(src, dst)
    assert summary["copied"] == 1 and summary["ignored"] == 0
    assert (dst / "Okoro Family" / "~budget.xlsx").read_text() == "budget"


# --- household / joint folder resolution -------------------------------------

def _white_tree(src):
    (src / "Michael and Debra White").mkdir(parents=True)
    (src / "Michael and Debra White" / "2025 Joint 1040.pdf").write_text("joint return")
    (src / "Michael and Debra White" / "Estate Plan.pdf").write_text("estate")
    return src


def test_resolve_folder_joint_maps_to_shared_household():
    hh = _household()
    _person_in("Michael White", hh)
    _person_in("Debra White", hh)
    with engine.connect() as conn:
        household_id, person_id = td.resolve_folder(conn, "Michael and Debra White")
    assert household_id == hh and person_id is None       # joint -> household, not a single person


def test_resolve_folder_single_person_maps_to_person():
    pid = _person_in("Taylor Hawthorne")
    with engine.connect() as conn:
        household_id, person_id = td.resolve_folder(conn, "Hawthorne, Taylor")
    assert person_id == pid and household_id is None


def test_joint_folder_import_sets_household_and_is_visible_to_both_spouses(tmp_path):
    src, dst = _dirs(tmp_path)
    _white_tree(src)
    hh = _household()
    michael = _person_in("Michael White", hh)
    debra = _person_in("Debra White", hh)
    _sync(src, dst)
    rows = _taxdome_rows()
    assert rows and all(r["household_id"] == hh and r["person_id"] is None for r in rows)
    # Both spouses see the joint household documents via the existing person-document query.
    for pid in (michael, debra):
        names = {d["original_name"] for d in get_person_documents(pid)}
        assert {"2025 Joint 1040.pdf", "Estate Plan.pdf"} <= names


def test_household_document_visible_to_both_members_via_household_id(tmp_path):
    src, dst = _dirs(tmp_path)
    _white_tree(src)
    hh = _household()
    a = _person_in("Michael White", hh)
    b = _person_in("Debra White", hh)
    _sync(src, dst)
    # A person NOT in the household sees nothing; both members do.
    outsider = _person_in("Taylor Hawthorne")
    assert get_person_documents(outsider) == []
    assert get_person_documents(a) and get_person_documents(b)


# --- repair command (relink existing rows without re-copying) -----------------

def test_repair_relinks_existing_joint_rows(tmp_path):
    src, dst = _dirs(tmp_path)
    _white_tree(src)
    _sync(src, dst)                                       # imported while NO people existed -> unlinked
    assert all(r["household_id"] is None and r["person_id"] is None for r in _taxdome_rows())
    before = {(r["id"], r["storage_path"], r["sha256"], r["current_version"]) for r in _taxdome_rows()}
    # Now the canonical household/people exist; repair relinks by folder metadata.
    hh = _household()
    _person_in("Michael White", hh)
    _person_in("Debra White", hh)
    summary = td.repair_person_links()
    assert summary["linked_household"] == 1 and summary["documents_updated"] == 2
    rows = _taxdome_rows()
    assert all(r["household_id"] == hh for r in rows)
    # No duplicates, and storage_path / hashes / version history preserved.
    assert len(rows) == 2
    after = {(r["id"], r["storage_path"], r["sha256"], r["current_version"]) for r in rows}
    assert after == before


def test_repair_relinks_single_person_row(tmp_path):
    src, dst = _dirs(tmp_path)
    _tree(src)
    _sync(src, dst)
    pid = _person_in("Taylor Hawthorne")
    summary = td.repair_person_links()
    assert summary["linked_person"] >= 1
    linked = [r for r in _taxdome_rows() if r["person_id"] == pid]
    assert len(linked) == 2                               # both Hawthorne files linked


def test_repair_is_idempotent_and_creates_no_duplicates(tmp_path):
    src, dst = _dirs(tmp_path)
    _white_tree(src)
    _sync(src, dst)
    hh = _household()
    _person_in("Michael White", hh)
    _person_in("Debra White", hh)
    td.repair_person_links()
    count_after_first = len(_taxdome_rows())
    summary = td.repair_person_links()                    # second run
    assert summary["documents_updated"] == 0             # nothing left to fill
    assert len(_taxdome_rows()) == count_after_first     # no duplicate rows


def test_repair_dry_run_makes_no_changes(tmp_path):
    src, dst = _dirs(tmp_path)
    _white_tree(src)
    _sync(src, dst)
    hh = _household()
    _person_in("Michael White", hh)
    _person_in("Debra White", hh)
    summary = td.repair_person_links(dry_run=True)
    assert summary["documents_updated"] == 2 and summary["dry_run"] is True
    assert all(r["household_id"] is None for r in _taxdome_rows())   # unchanged


def test_repair_preserves_manual_person_link(tmp_path):
    src, dst = _dirs(tmp_path)
    _white_tree(src)
    _sync(src, dst)
    # A human already linked one document to a specific person; repair must not overwrite it.
    manual_person = _person_in("Delgado, Alex")
    rows = _taxdome_rows()
    with engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == rows[0]["id"]).values(person_id=manual_person))
    hh = _household()
    _person_in("Michael White", hh)
    _person_in("Debra White", hh)
    td.repair_person_links()
    kept = next(r for r in _taxdome_rows() if r["id"] == rows[0]["id"])
    assert kept["person_id"] == manual_person            # manual link preserved
    assert kept["household_id"] == hh                    # household still filled in (was NULL)


# --- demo dashboard (existing surface still works) ---------------------------

FAKE = Principal(4242, "r@e.example", "Reviewer", frozenset({"record.read_all", "client.read"}))


def _request(*, demo=True, query=b""):
    scope = {"type": "http", "method": "GET", "path": "/demo/taxdome-drive", "headers": [],
             "query_string": query, "state": {}}
    request = Request(scope)
    request.state.principal = FAKE
    if demo:
        request.state.demo_mode = True
    return request


def test_dashboard_page_loads_with_counts(tmp_path):
    src, dst = _dirs(tmp_path)
    _sync(_tree(src), dst)
    resp = page.taxdome_dashboard(_request(), q="", principal=FAKE)
    html = resp.body.decode()
    assert resp.status_code == 200
    assert "TaxDome Drive" in html and "Total TaxDome folders" in html
    assert "Total indexed files" in html and "Unresolved folder mappings" in html


def test_search_synced_documents(tmp_path):
    src, dst = _dirs(tmp_path)
    _sync(_tree(src), dst)
    with engine.connect() as conn:
        results = td._search_documents(conn, "1099")
    assert any(r["original_name"] == "1099-DIV.pdf" for r in results)
    html = page.taxdome_dashboard(_request(query=b"q=1099"), q="1099", principal=FAKE).body.decode()
    assert "1099-DIV.pdf" in html


def test_resolve_keep_separate_creates_person(tmp_path, monkeypatch):
    monkeypatch.setattr(page, "write_audit_event", lambda **kw: None)
    src, dst = _dirs(tmp_path)
    _sync(_tree(src), dst)
    page.taxdome_resolve(_request(), folder="Okoro Family", action="keep_separate",
                         person_id="", principal=FAKE)
    linked = [r for r in _taxdome_rows()
              if r["tags"]["taxdome_folder"] == "Okoro Family" and r["person_id"] is not None]
    assert len(linked) == 1


# --- legacy-key reconciliation must never CASCADE away document intelligence --------------------
#
# Deleting a documents row cascades to its OCR, classifications, facts, source references,
# relationships and events, and aborts on the NO ACTION referrers. These pin the safety rule:
# a duplicate that anything still references is never deleted.

def _dependent_ocr(document_id, *, text="cached text"):
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT INTO document_ocr (document_id, status, text, char_count) "
            "VALUES (:d, 'completed', :t, :n)"), {"d": document_id, "t": text, "n": len(text)})


def _ocr_text(document_id):
    with engine.connect() as conn:
        return conn.execute(sa_text("SELECT text FROM document_ocr WHERE document_id = :d"),
                            {"d": document_id}).scalar()


def _dependent_source(document_id):
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT INTO document_sources (document_id, source_system, source_uri) "
            "VALUES (:d, 'TaxDome Drive', :u)"), {"d": document_id, "u": f"probe://{document_id}"})


def _dependent_fact_and_classification(document_id):
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT INTO document_facts (document_id, fact_type, fact_value, extraction_engine) "
            "VALUES (:d, 'probe', 'value', 'test')"), {"d": document_id})
        conn.execute(sa_text(
            "INSERT INTO document_classifications (document_id, doc_type, classifier_version) "
            "VALUES (:d, 'probe_type', 'test')"), {"d": document_id})


def _count(table, document_id, column="document_id"):
    with engine.connect() as conn:
        return conn.execute(sa_text(
            f"SELECT count(*) FROM {table} WHERE {column} = :d"), {"d": document_id}).scalar()


def _reconcile_pair(tmp_path):
    """A legacy row (linked, canonical) + a new-style row (the duplicate holding the target key)."""
    src, dst = _dirs(tmp_path)
    _tree(src)
    pid = _person("Delgado, Alex")
    abs_path = str(src / "Okoro Family" / "Engagement Agreement.pdf")
    _sync(src, dst)                                          # creates the new-style row
    _legacy_row(src, abs_path, "Okoro Family", person_id=pid)
    rows = {r["stored_name"]: r for r in _taxdome_rows()
            if r["original_name"] == "Engagement Agreement.pdf"}
    key = td._stored_name(os.path.relpath(abs_path, src))
    return src, dst, pid, rows[key]["id"]                    # the duplicate that would be deleted


def test_dependent_ocr_row_prevents_deletion(tmp_path):
    src, dst, _pid, dup_id = _reconcile_pair(tmp_path)
    _dependent_ocr(dup_id)
    summary = _sync(src, dst)
    assert summary["reconcile_skipped_has_dependents"] >= 1
    assert _count("documents", dup_id, "id") == 1, "the referenced duplicate must survive"
    assert _ocr_text(dup_id) == "cached text", "its OCR text must survive"
    # The blocked duplicate HOLDS the target stored_name, so the canonical row cannot take that key.
    # The file must be skipped cleanly rather than raising a unique violation the run then records.
    assert summary["errors"] == [], f"reconcile should skip cleanly, not error: {summary['errors']}"


def test_dependent_document_sources_row_prevents_deletion(tmp_path):
    src, dst, _pid, dup_id = _reconcile_pair(tmp_path)
    _dependent_source(dup_id)
    summary = _sync(src, dst)
    assert summary["reconcile_skipped_has_dependents"] >= 1
    assert _count("documents", dup_id, "id") == 1
    assert _count("document_sources", dup_id) >= 1, "provenance must survive"


def test_dependent_facts_and_classification_survive(tmp_path):
    src, dst, _pid, dup_id = _reconcile_pair(tmp_path)
    _dependent_fact_and_classification(dup_id)
    _sync(src, dst)
    assert _count("document_facts", dup_id) == 1
    assert _count("document_classifications", dup_id) == 1


def test_no_action_reference_is_skipped_rather_than_aborting_the_sync(tmp_path):
    """exceptions.document_id is ON DELETE NO ACTION — deleting would raise mid-sync."""
    src, dst, _pid, dup_id = _reconcile_pair(tmp_path)
    with engine.begin() as conn:
        etype = conn.execute(sa_text("SELECT id FROM exception_types LIMIT 1")).scalar()
        conn.execute(sa_text(
            "INSERT INTO exceptions (exception_type_id, domain, category, severity, title,"
            " document_id) VALUES (:t, 'operations', 'document', 'low', 'probe', :d)"),
            {"t": etype, "d": dup_id})
    try:
        summary = _sync(src, dst)                            # must NOT raise
        assert summary["status"] != "failed"
        assert summary["reconcile_skipped_has_dependents"] >= 1
        assert _count("documents", dup_id, "id") == 1
    finally:
        # NO ACTION also blocks the suite's document wipe; release it for teardown.
        with engine.begin() as conn:
            conn.execute(sa_text("DELETE FROM exceptions WHERE document_id = :d"), {"d": dup_id})


def test_summary_records_the_skipped_reconciliation(tmp_path):
    src, dst, _pid, dup_id = _reconcile_pair(tmp_path)
    _dependent_ocr(dup_id)
    summary = _sync(src, dst)
    details = [d for d in summary["reconcile_skipped_details"] if d["document_id"] == dup_id]
    assert len(details) == 1
    assert "document_ocr.document_id" in details[0]["dependents"]


def test_an_unreferenced_legacy_duplicate_is_still_reconciled(tmp_path):
    """The narrow existing behaviour is unchanged when nothing references the duplicate."""
    src, dst, pid, dup_id = _reconcile_pair(tmp_path)
    summary = _sync(src, dst)
    assert summary["reconcile_skipped_has_dependents"] == 0
    assert _count("documents", dup_id, "id") == 0, "an orphaned duplicate is still removed"
    matches = [r for r in _taxdome_rows() if r["original_name"] == "Engagement Agreement.pdf"]
    assert len(matches) == 1 and matches[0]["person_id"] == pid


def test_dry_run_previews_the_skip_without_changing_anything(tmp_path):
    src, dst, _pid, dup_id = _reconcile_pair(tmp_path)
    _dependent_ocr(dup_id)
    summary = _sync(src, dst, dry_run=True)
    assert summary["reconcile_skipped_has_dependents"] >= 1
    assert _count("documents", dup_id, "id") == 1
