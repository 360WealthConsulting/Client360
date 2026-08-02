"""TaxDome Drive one-way local document sync — coverage.

Verifies the sync treats the source drive as read-only, maintains durable verified local copies under a
destination root (never Z:\\), is idempotent, retains local copies when a source file disappears (and
only removes them with the explicit --purge-missing flag), never exposes a partial file on a failed
copy, handles duplicate filenames across folders, blocks path traversal, links folders to people
conservatively, makes dry-run a no-op, and surfaces local copies through the existing person-document
query + download route. All tests use temporary directories — no production data is touched.
"""
import os
from pathlib import Path, PurePosixPath

import pytest
from starlette.requests import Request

from app.db import documents, engine, people
from app.demo import taxdome_drive as page
from app.importers import taxdome_drive as td
from app.security.models import Principal
from app.services.documents import get_person_documents

_TEST_NAMES = ("Taylor Hawthorne", "Okoro Family", "Delgado, Alex")


@pytest.fixture(autouse=True)
def _clean_taxdome():
    """Isolate the shared test DB: remove TaxDome docs + test people before and after each test."""
    def _wipe():
        with engine.begin() as conn:
            conn.execute(documents.delete().where(td.taxdome_filter(documents)))
            conn.execute(people.delete().where(people.c.full_name.in_(_TEST_NAMES)))
    _wipe()
    td._database.cache_clear()
    yield
    _wipe()


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
                                  "/abs/path", "~/secret", "..\\..\\x"])
def test_sanitize_relative_path_blocks_traversal(evil):
    with pytest.raises(ValueError):
        td.sanitize_relative_path(evil)


def test_sanitize_relative_path_allows_normal_nested_path():
    assert td.sanitize_relative_path("Okoro Family/2025/W-2.pdf") == PurePosixPath("Okoro Family/2025/W-2.pdf")


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
    resp = download_document(doc["id"])
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
    resp = download_document(doc["id"])                      # retained copy still serves
    assert Path(resp.path).read_text() == "w2 content"


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
