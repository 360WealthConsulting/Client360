"""TaxDome Drive indexer + demo dashboard — coverage.

Verifies the indexer treats Z:\\ as a read-only external repository indexed into the EXISTING
documents model (no parallel platform, no file copying), that rescans are idempotent (skip /
update / insert / mark-missing, no duplicate rows), that folder->person matching is conservative
with a review queue, and that the demo page + nav render.
"""
from pathlib import Path

import pytest
from starlette.requests import Request

from app.db import documents, engine, people
from app.demo import taxdome_drive as page
from app.importers import taxdome_drive as td
from app.security.models import Principal
from app.services.documents import get_person_documents

PROVIDER = "TaxDome Drive"


_TEST_NAMES = ("Taylor Hawthorne", "Okoro Family", "Delgado, Alex")


@pytest.fixture(autouse=True)
def _clean_taxdome():
    """Isolate (shared test DB): remove TaxDome documents AND the test-created people by name,
    before and after each test, so the conservative exact-name auto-link stays deterministic."""
    def _wipe():
        with engine.begin() as conn:
            conn.execute(documents.delete().where(documents.c.storage_provider == PROVIDER))
            conn.execute(people.delete().where(people.c.full_name.in_(_TEST_NAMES)))
    _wipe()
    td._database.cache_clear()
    yield
    _wipe()


def _tree(root: Path):
    (root / "Hawthorne, Taylor").mkdir()
    (root / "Hawthorne, Taylor" / "2025 W-2.pdf").write_text("w2 content")
    (root / "Hawthorne, Taylor" / "sub").mkdir()
    (root / "Hawthorne, Taylor" / "sub" / "1099-DIV.pdf").write_text("1099")
    (root / "Okoro Family").mkdir()
    (root / "Okoro Family" / "Engagement Agreement.pdf").write_text("agreement")
    return root


def _person(full_name):
    with engine.begin() as conn:
        return conn.execute(people.insert().values(full_name=full_name).returning(people.c.id)).scalar_one()


# --- recursive scan ----------------------------------------------------------

def test_recursive_scan_indexes_metadata_only(tmp_path):
    root = _tree(tmp_path)
    summary = td.scan(root)
    assert summary["folders"] == 2
    assert summary["new"] == 3 and summary["files_scanned"] == 3
    with engine.connect() as conn:
        rows = conn.execute(documents.select().where(documents.c.storage_provider == PROVIDER)).mappings().all()
    assert len(rows) == 3
    sample = next(r for r in rows if r["original_name"] == "2025 W-2.pdf")
    assert sample["storage_provider"] == PROVIDER
    assert sample["storage_uri"] == str(root / "Hawthorne, Taylor" / "2025 W-2.pdf")   # external ref
    assert sample["sha256"] and sample["size_bytes"] > 0
    assert sample["category"] == "tax_document"                                        # path/name inference
    assert sample["tags"]["taxdome_folder"] == "Hawthorne, Taylor"
    assert sample["tags"]["source_system"] == PROVIDER
    assert "relative_path" in sample["tags"] and "file_modified" in sample["tags"]


# --- no file copying ---------------------------------------------------------

def test_no_file_copying(tmp_path):
    root = _tree(tmp_path)
    repo_docs = Path("documents")
    before = sum(1 for _ in repo_docs.rglob("*")) if repo_docs.exists() else 0
    tree_before = {p.name: p.read_text() for p in root.rglob("*") if p.is_file()}

    td.scan(root)

    after = sum(1 for _ in repo_docs.rglob("*")) if repo_docs.exists() else 0
    tree_after = {p.name: p.read_text() for p in root.rglob("*") if p.is_file()}
    assert after == before                              # nothing written into the repo document store
    assert tree_after == tree_before                    # Z:\ is untouched (read-only)
    # only metadata is stored — the documents table has no file-content column, and sha256 is a hash
    with engine.connect() as conn:
        row = conn.execute(documents.select().where(documents.c.storage_provider == PROVIDER)).mappings().first()
    assert len(row["sha256"]) == 64 and "content" not in {c.name for c in documents.columns}


# --- idempotent rescan -------------------------------------------------------

def test_idempotent_rescan_no_duplicates(tmp_path):
    root = _tree(tmp_path)
    td.scan(root)
    summary = td.scan(root)
    assert summary["new"] == 0 and summary["changed"] == 0 and summary["unchanged"] == 3
    with engine.connect() as conn:
        assert conn.scalar(documents.select().where(documents.c.storage_provider == PROVIDER)
                           .with_only_columns(td.func.count())) == 3


# --- changed file detection --------------------------------------------------

def test_changed_file_detected(tmp_path):
    root = _tree(tmp_path)
    td.scan(root)
    (root / "Okoro Family" / "Engagement Agreement.pdf").write_text("agreement UPDATED CONTENT")
    summary = td.scan(root)
    assert summary["changed"] == 1 and summary["unchanged"] == 2 and summary["new"] == 0


# --- missing file handling ---------------------------------------------------

def test_missing_file_marked_unavailable(tmp_path):
    root = _tree(tmp_path)
    td.scan(root)
    (root / "Okoro Family" / "Engagement Agreement.pdf").unlink()
    summary = td.scan(root)
    assert summary["missing"] == 1
    with engine.connect() as conn:
        missing = conn.execute(documents.select().where(
            (documents.c.storage_provider == PROVIDER) & (documents.c.status == "archived"))).mappings().all()
    assert len(missing) == 1 and missing[0]["original_name"] == "Engagement Agreement.pdf"
    assert missing[0]["archived"] is True


# --- folder -> person matching + review --------------------------------------

def test_folder_autolinks_on_unique_exact_name_only(tmp_path):
    root = _tree(tmp_path)
    pid = _person("Taylor Hawthorne")     # exact normalized match to "Hawthorne, Taylor"
    td.scan(root)
    with engine.connect() as conn:
        linked = conn.execute(documents.select().where(
            (documents.c.storage_provider == PROVIDER) & (documents.c.person_id == pid))).mappings().all()
        # both Hawthorne files auto-linked; Okoro (no match) left unresolved
        assert len(linked) == 2
        unresolved = page._unresolved_folders(conn)
    folders = {u["folder"] for u in unresolved}
    assert "Okoro Family" in folders and "Hawthorne, Taylor" not in folders


def test_ambiguous_name_is_not_autolinked(tmp_path):
    root = _tree(tmp_path)
    _person("Taylor Hawthorne")
    _person("Taylor Hawthorne")           # duplicate name -> ambiguous -> must NOT auto-link
    td.scan(root)
    with engine.connect() as conn:
        linked = conn.scalar(documents.select().where(
            (documents.c.storage_provider == PROVIDER) & (documents.c.person_id.isnot(None)))
            .with_only_columns(td.func.count()))
    assert linked == 0                     # conservative: no weak/ambiguous auto-merge


def test_person_page_shows_linked_taxdome_documents(tmp_path):
    root = _tree(tmp_path)
    pid = _person("Taylor Hawthorne")
    td.scan(root)
    docs = get_person_documents(pid)       # the EXISTING person-documents query
    names = {d["original_name"] for d in docs}
    assert "2025 W-2.pdf" in names and "1099-DIV.pdf" in names


# --- UI page + navigation ----------------------------------------------------

FAKE = Principal(4242, "r@e.example", "Reviewer", frozenset({"record.read_all", "client.read"}))


def _request(*, demo=True, query=b""):
    scope = {"type": "http", "method": "GET", "path": "/demo/taxdome-drive", "headers": [],
             "query_string": query, "state": {}}
    request = Request(scope)
    request.state.principal = FAKE
    if demo:
        request.state.demo_mode = True
    return request


def test_dashboard_page_loads_with_counts_and_scan_button(tmp_path):
    td.scan(_tree(tmp_path))
    resp = page.taxdome_dashboard(_request(), q="", principal=FAKE)
    html = resp.body.decode()
    assert resp.status_code == 200
    assert "TaxDome Drive" in html and "Scan TaxDome Drive" in html
    assert "Total TaxDome folders" in html and "Total indexed files" in html
    assert "Unresolved folder mappings" in html


def test_nav_taxdome_visible_in_demo_only(tmp_path):
    td.scan(_tree(tmp_path))
    demo_html = page.taxdome_dashboard(_request(demo=True), q="", principal=FAKE).body.decode()
    assert "TaxDome Drive" in demo_html and "◇" in demo_html      # nav item (unique nav icon)
    prod_html = page.taxdome_dashboard(_request(demo=False), q="", principal=FAKE).body.decode()
    assert "◇" not in prod_html                                   # hidden without demo_mode


def test_search_indexed_documents(tmp_path):
    td.scan(_tree(tmp_path))
    with engine.connect() as conn:
        results = td._search_documents(conn, "1099")
    assert any(r["original_name"] == "1099-DIV.pdf" for r in results)
    html = page.taxdome_dashboard(_request(query=b"q=1099"), q="1099", principal=FAKE).body.decode()
    assert "1099-DIV.pdf" in html


def test_resolve_keep_separate_creates_person(tmp_path, monkeypatch):
    monkeypatch.setattr(page, "write_audit_event", lambda **kw: None)
    td.scan(_tree(tmp_path))
    page.taxdome_resolve(_request(), folder="Okoro Family", action="keep_separate",
                         person_id="", principal=FAKE)
    with engine.connect() as conn:
        linked = conn.scalar(documents.select().where(
            (documents.c.storage_provider == PROVIDER)
            & (documents.c.tags["taxdome_folder"].astext == "Okoro Family")
            & (documents.c.person_id.isnot(None))).with_only_columns(td.func.count()))
    assert linked == 1                     # Okoro folder now linked to a new canonical person
