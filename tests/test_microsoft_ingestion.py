"""Continuous ingestion — Microsoft/SharePoint sync runner: change detection, idempotency, run records."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import document_facts, documents, engine, metadata, people
from app.importers import sharepoint
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import microsoft_ingestion as mi

document_sources = metadata.tables["document_sources"]

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_SEEN_DOCS: set = set()


@pytest.fixture(autouse=True)
def _clear_cache():
    sharepoint._database.cache_clear()          # importer uses the test DB (DATABASE_URL env)
    yield
    with engine.begin() as c:
        ids = list(_SEEN_DOCS)
        if ids:
            c.execute(document_facts.delete().where(document_facts.c.document_id.in_(ids)))
            c.execute(document_sources.delete().where(document_sources.c.document_id.in_(ids)))
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
    _SEEN_DOCS.clear()


def _item(tmp_path, *, name, uri, body="hello world statement", modified="2024-01-01T00:00:00",
          deleted=False):
    f = tmp_path / f"{uuid.uuid4().hex}_{name}"
    if not deleted:
        f.write_text(body)
    item = {"name": name, "web_url": uri, "item_id": uri.rsplit("/", 1)[-1],
            "site": "Site1", "library": "Docs", "modified_at": modified}
    if deleted:
        item["deleted"] = True
    else:
        item["local_path"] = str(f)
        item["size"] = f.stat().st_size
    return item


def _run(items, tmp_path, **kw):
    r = mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"), ocr=False, **kw)
    _track()
    return r


def _track():
    with engine.connect() as c:
        for did in c.execute(select(document_sources.c.document_id)
                             .where(document_sources.c.source_system == "SharePoint")).scalars():
            _SEEN_DOCS.add(did)


def _canonical_count(uri):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(document_sources)
                         .where(document_sources.c.source_system == "SharePoint",
                                document_sources.c.source_uri == uri)).scalar()


def _doc_for(uri):
    with engine.connect() as c:
        return c.execute(select(document_sources.c.document_id, document_sources.c.available)
                         .where(document_sources.c.source_system == "SharePoint",
                                document_sources.c.source_uri == uri)
                         .order_by(document_sources.c.id.desc())).mappings().first()


# --- change detection ------------------------------------------------------------------------------

def test_first_seen_ingested_once(tmp_path):
    uri = f"https://sp/{_A}/a1"
    r = _run([_item(tmp_path, name="a.txt", uri=uri)], tmp_path)
    assert r["canonical_created"] == 1 and _canonical_count(uri) == 1


def test_unchanged_skipped_on_next_run(tmp_path):
    uri = f"https://sp/{_A}/a2"
    item = _item(tmp_path, name="a.txt", uri=uri)
    _run([item], tmp_path)
    r2 = _run([item], tmp_path)                       # same size + modified -> skipped
    assert r2["skipped"] == 1 and r2["canonical_created"] == 0
    assert r2.get("affected_document_ids", []) == []  # nothing re-analyzed (no OCR)
    assert _canonical_count(uri) == 1                 # no duplicate


def test_changed_item_reprocessed(tmp_path):
    uri = f"https://sp/{_A}/a3"
    _run([_item(tmp_path, name="a.txt", uri=uri, body="v1", modified="2024-01-01T00:00:00")], tmp_path)
    r2 = _run([_item(tmp_path, name="a.txt", uri=uri, body="v2 different content",
                     modified="2024-02-02T00:00:00")], tmp_path)
    assert r2["skipped"] == 0 and r2["canonical_created"] == 1   # new content -> new canonical version


def test_duplicate_source_event_no_duplicate_canonical(tmp_path):
    uri = f"https://sp/{_A}/a4"
    item = _item(tmp_path, name="a.txt", uri=uri)
    _run([item, item], tmp_path)                      # same uri twice in one run
    assert _canonical_count(uri) == 1


def test_source_deletion_keeps_canonical(tmp_path):
    uri = f"https://sp/{_A}/a5"
    _run([_item(tmp_path, name="a.txt", uri=uri)], tmp_path)
    did = _doc_for(uri)["document_id"]
    _run([_item(tmp_path, name="a.txt", uri=uri, deleted=True)], tmp_path)
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(documents.c.id == did)).scalar() == did  # kept
    assert _doc_for(uri)["available"] is False        # source reference marked unavailable


def test_scan_idempotency(tmp_path):
    uri = f"https://sp/{_A}/a6"
    item = _item(tmp_path, name="a.txt", uri=uri)
    _run([item], tmp_path)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents)).scalar()
    _run([item], tmp_path); _run([item], tmp_path)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert after == before                            # repeated runs create no new canonical documents


# --- pipeline invocation + OCR-on-need -------------------------------------------------------------

def test_new_document_invokes_pipeline(tmp_path):
    uri = f"https://sp/{_A}/a7"
    email = f"sp-{_TAG}@mail.com"
    from app.db import person_source_links, source_contacts
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"Spperson {_A}", active=True)
                        .returning(people.c.id)).scalar_one()
        sid = c.execute(source_contacts.insert().values(source_system="TaxDome", source_file="t",
              source_record_id=uuid.uuid4().hex, source_hash=uuid.uuid4().hex, email=email, raw_data={})
              .returning(source_contacts.c.id)).scalar_one()
        c.execute(person_source_links.insert().values(person_id=pid, source_contact_id=sid,
                  match_method="email", confirmed=True))
    _run([_item(tmp_path, name="s.txt", uri=uri, body=f"remit to {email}")], tmp_path)
    did = _doc_for(uri)["document_id"]
    with engine.connect() as c:
        fact = c.execute(select(document_facts.c.fact_value).where(
            document_facts.c.document_id == did, document_facts.c.fact_type == "owner_proposal",
            document_facts.c.is_current.is_(True))).scalar()
    assert fact is not None                           # pipeline ran on the new doc (proposal persisted)
    with engine.begin() as c:                          # cleanup the extra person
        c.execute(person_source_links.delete().where(person_source_links.c.person_id == pid))
        c.execute(source_contacts.delete().where(source_contacts.c.id == sid))
        c.execute(people.delete().where(people.c.id == pid))
    assert _owner_all_null(did)                        # ingestion assigns NO ownership


def _owner_all_null(did):
    with engine.connect() as c:
        r = c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                      .where(documents.c.id == did)).first()
    return r == (None, None, None)


# --- failure isolation + run record ----------------------------------------------------------------

def test_failure_isolated_and_run_recorded(tmp_path):
    uri = f"https://sp/{_A}/a8"
    good = _item(tmp_path, name="good.txt", uri=uri)
    bad = {"name": "bad.txt", "web_url": f"https://sp/{_A}/bad", "item_id": "bad"}  # no local_path -> raises
    r = _run([bad, good], tmp_path)
    assert r["status"] == "completed_with_errors" and len(r["errors"]) == 1
    assert _canonical_count(uri) == 1                 # the good item still ingested
    status = {s["source"]: s for s in mi.ingestion_status()}
    assert status["SharePoint"]["status"] == "completed_with_errors"
    assert status["SharePoint"]["scanned"] >= 2


def test_no_client_created(tmp_path):
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(people)).scalar()
    _run([_item(tmp_path, name="a.txt", uri=f"https://sp/{_A}/a9",
                body=f"Dear Unknownperson {_A}, contact new-{_TAG}@x.com")], tmp_path)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(people)).scalar()
    assert after == before                            # ingestion never creates a client


# --- authorization ---------------------------------------------------------------------------------

def test_ingestion_status_page_requires_identity_manage():
    dep = require_capability("identity.manage")
    admin = Principal(1, "a@t", "Admin", frozenset({"identity.manage"}))
    assert dep(principal=admin) is admin
    with pytest.raises(HTTPException) as exc:
        dep(principal=Principal(2, "x@t", "Staff", frozenset({"client.read"})))
    assert exc.value.status_code == 403


def test_route_registered():
    from app.main import app
    assert "/admin/ingestion" in {getattr(r, "path", None) for r in app.routes}


# --- connector staging entrypoint resolution (regression for the production ImportError) ----------

def test_resolve_stager_uses_real_connector_entrypoint():
    import types
    mod = types.SimpleNamespace(
        stage_sharepoint_content=lambda *, site_ids=None, dry_run=False: ([{"name": "a.txt"}], {"status": "ok"}))
    stager = mi.resolve_sharepoint_stager(module=mod, dry_run=True)
    assert stager() == [{"name": "a.txt"}]                # adapts to the connector's real function + return


def test_resolve_stager_errors_clearly_when_entrypoint_absent():
    import types
    mod = types.SimpleNamespace(some_other_helper=lambda: None)   # connector without a known stager
    stager = mi.resolve_sharepoint_stager(module=mod)
    with pytest.raises(RuntimeError) as exc:
        stager()
    msg = str(exc.value)
    assert "some_other_helper" in msg and "--manifest" in msg    # lists real callables + the reliable path


def test_script_has_no_import_time_connector_dependency():
    import importlib
    import inspect
    m = importlib.import_module("scripts.run_sharepoint_sync")     # must import without the connector present
    assert hasattr(m, "main")
    src = inspect.getsource(m)
    # the bug was a hard import of a guessed connector function; it must not return
    assert "sharepoint_content import" not in src and "stage_sharepoint_content" not in src


def test_resolve_stager_reads_manifest_path_result(tmp_path):
    import json
    import types
    mf = tmp_path / "m.json"
    mf.write_text(json.dumps([{"name": "b.txt"}]))
    mod = types.SimpleNamespace(stage=lambda: {"manifest_path": str(mf)})
    assert mi.resolve_sharepoint_stager(module=mod)() == [{"name": "b.txt"}]


# --- connector run(*, drive_id, root, download, limit, manifest) wiring ----------------------------

def _run_stub(recorder, *, extra_required=False):
    import types
    if extra_required:
        def run(*, drive_id, needs_this):     # a required arg we cannot source
            recorder["called"] = True
        return types.SimpleNamespace(run=run)

    def run(*, drive_id, root, download, limit, manifest):
        recorder.update({"drive_id": drive_id, "root": root, "download": download,
                         "limit": limit, "manifest": manifest})
        return ([{"name": "x.txt", "web_url": f"https://sp/{drive_id}", "item_id": drive_id}], {"status": "ok"})
    return types.SimpleNamespace(run=run)


def test_resolver_supplies_run_required_kwargs():
    rec = {}
    stager = mi.resolve_sharepoint_stager(module=_run_stub(rec), drive_ids=["drv1"], dry_run=True)
    items = stager()
    assert rec["drive_id"] == "drv1" and rec["download"] is False        # dry-run -> no download
    assert rec["root"] and isinstance(rec["limit"], int) and rec["manifest"]
    assert items and items[0]["item_id"] == "drv1"


def test_missing_drive_configuration_is_clear_error():
    stager = mi.resolve_sharepoint_stager(module=_run_stub({}), drive_ids=[], dry_run=True)
    with pytest.raises(RuntimeError) as exc:
        stager()
    assert "drive_id" in str(exc.value)


def test_unfillable_required_arg_is_clear_error():
    stager = mi.resolve_sharepoint_stager(module=_run_stub({}, extra_required=True), drive_ids=["d1"])
    with pytest.raises(RuntimeError) as exc:
        stager()
    assert "needs_this" in str(exc.value)


def test_stage_dry_run_is_read_only(tmp_path):
    import types
    from pathlib import Path

    def run(*, drive_id, root, download, limit, manifest):
        f = Path(tmp_path) / f"{drive_id}.txt"
        f.write_text("hello statement")
        return ([{"name": "a.txt", "web_url": f"https://sp/{drive_id}/x", "item_id": "x",
                  "site": "S", "library": "D", "modified_at": "2024-01-01T00:00:00",
                  "local_path": str(f), "size": f.stat().st_size}], {})
    mod = types.SimpleNamespace(run=run)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents)).scalar()
    stager = mi.resolve_sharepoint_stager(module=mod, drive_ids=["drvX"], dry_run=True)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "dest"),
                                     dry_run=True, ocr=False)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert summary["status"] == "dry_run" and after == before        # dry-run imports nothing


# --- staging diagnostics (why did staging return zero items?) --------------------------------------

def test_diag_reports_drive_and_item_counts():
    rec = {}
    diag = {}
    stager = mi.resolve_sharepoint_stager(module=_run_stub(rec), drive_ids=["drvA", "drvB"],
                                           dry_run=True, diag=diag)
    items = stager()
    assert diag["drive_count"] == 2 and diag["drive_ids"] == ["drvA", "drvB"]
    assert diag["total_items"] == len(items) == 2          # one item per drive from the stub
    assert diag["staging_root"] and diag["entrypoint"] == "run"
    assert all(d["download"] is False for d in diag["drives"])   # dry-run: no download


def test_diag_reads_manifest_file_when_run_returns_summary(tmp_path):
    import json
    import types

    def run(*, drive_id, root, download, limit, manifest):
        # connector writes items to the manifest FILE and returns a summary (no items in the return)
        from pathlib import Path
        Path(manifest).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest).write_text(json.dumps([{"name": "m.txt", "web_url": "u", "item_id": "i"}]))
        return {"status": "ok", "files_seen": 1}           # summary only — items are in the file
    mod = types.SimpleNamespace(run=run)
    diag = {}
    stager = mi.resolve_sharepoint_stager(module=mod, drive_ids=["drvC"], dry_run=True, diag=diag)
    # point staging root at tmp so the manifest lands there
    import os
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path)
    try:
        items = stager()
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    assert len(items) == 1 and diag["drives"][0]["source"] == "manifest_file"


def test_staging_diagnostics_is_read_only():
    d = mi.sharepoint_staging_diagnostics()
    assert set(("staging_root", "discovered_drive_count", "discovered_drive_ids",
                "MICROSOFT_SHAREPOINT_SITE_IDS", "existing_sharepoint_source_refs")) <= set(d)
    assert isinstance(d["existing_sharepoint_source_refs"], int)


# --- dry-run enumerates metadata WITHOUT downloading (connector couples download+manifest) ----------

def _enumerating_connector(tmp_path, recorder):
    """Mimics production: run(download=False) alone returns a summary with 0 items and writes no
    manifest; only when an enumerate/dry-run flag is set does it list metadata + write the manifest
    (still no downloads)."""
    import json
    import types
    from pathlib import Path

    def run(*, drive_id, root, download, limit, manifest, dry_run=False):
        recorder.append({"drive_id": drive_id, "download": download, "dry_run": dry_run})
        if download or dry_run:                # enumerate metadata (no content download) into the manifest
            Path(manifest).parent.mkdir(parents=True, exist_ok=True)
            Path(manifest).write_text(json.dumps([
                {"name": f"{drive_id}.pdf", "web_url": f"https://sp/{drive_id}/f", "item_id": "f1",
                 "site": "S", "library": "D", "modified_at": "2024-01-01T00:00:00", "size": 10}]))
            return {"status": "ok", "files_seen": 1, "files_downloaded": (1 if download else 0)}
        return {"status": "ok", "files_seen": 0, "files_downloaded": 0}   # download=False, no flag -> nothing
    return types.SimpleNamespace(run=run)


def test_dry_run_enumerates_metadata(tmp_path):
    import os
    rec = []
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "stage")
    try:
        diag = {}
        stager = mi.resolve_sharepoint_stager(module=_enumerating_connector(tmp_path, rec),
                                               drive_ids=["d1"], dry_run=True, diag=diag)
        items = stager()
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    assert rec and rec[0]["download"] is False and rec[0]["dry_run"] is True   # download stays OFF, enumerate ON
    assert len(items) == 1 and diag["total_items"] == 1
    assert diag["drives"][0]["manifest_exists"] is True and diag["drives"][0]["source"] == "manifest_file"


def test_dry_run_creates_no_documents_or_ownership(tmp_path):
    import os
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "stage")
    try:
        with engine.connect() as c:
            before = c.execute(select(func.count()).select_from(documents)).scalar()
        stager = mi.resolve_sharepoint_stager(module=_enumerating_connector(tmp_path, []),
                                               drive_ids=["d2"], dry_run=True)
        summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "dest"),
                                         dry_run=True, ocr=False)
        with engine.connect() as c:
            after = c.execute(select(func.count()).select_from(documents)).scalar()
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    assert summary["status"] == "dry_run"
    assert summary["items_examined"] >= 1          # enumerated metadata
    assert after == before                         # but no canonical documents created / no ownership


def test_dry_run_downloads_no_files(tmp_path):
    import os
    dest = tmp_path / "dest"
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "stage")
    try:
        stager = mi.resolve_sharepoint_stager(module=_enumerating_connector(tmp_path, []),
                                               drive_ids=["d3"], dry_run=True)
        mi.run_sharepoint_sync(stager=stager, destination_root=str(dest), dry_run=True, ocr=False)
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    # the importer's destination root has no downloaded/copied files in dry-run
    assert not dest.exists() or not any(dest.rglob("*.*"))


def test_stager_items_extracts_from_alt_dict_keys():
    assert mi._stager_items({"staged": [{"name": "x"}]}) == [{"name": "x"}]
    assert mi._stager_items({"documents": [{"name": "y"}]}) == [{"name": "y"}]
    assert mi._stager_items({"status": "ok", "files_seen": 5}) == []   # summary only -> no items


# --- Graph driveItem delta semantics + initial $top + progress + timeout --------------------------

_DRIVE = "b!an9eHY8SsUSoKpMiW4vy9GHKx9mbaS1Eo_ha68ZssFa86B3K4zuaRJVGisg9UoId"


def test_initial_delta_url_uses_small_top():
    url = mi.initial_delta_url(_DRIVE, top=10)
    assert url == f"https://graph.microsoft.com/v1.0/drives/{_DRIVE}/root/delta?$top=10"
    assert mi.initial_delta_url(_DRIVE).endswith("/root/delta")   # no $top when unset


def test_delta_pages_follow_nextlink_with_progress():
    pages = {
        f"https://graph.microsoft.com/v1.0/drives/{_DRIVE}/root/delta?$top=2":
            {"value": [{"id": "1"}, {"id": "2"}], "@odata.nextLink": "https://g/next2"},
        "https://g/next2": {"value": [{"id": "3"}]},   # no nextLink -> stop
    }
    events = []
    def fetch(url, timeout):
        return pages[url]
    got = list(mi.iter_delta_pages(_DRIVE, fetch, top=2, progress=events.append))
    assert [len(p) for p in got] == [2, 1]                         # two pages, then stop
    kinds = [e["kind"] for e in events if e["phase"] == "response"]
    assert kinds == ["initial", "nextLink"]                        # initial then continuation
    assert all("elapsed" in e for e in events if e["phase"] == "response")


def test_delta_page_failure_surfaces_cleanly():
    def fetch(url, timeout):
        raise TimeoutError("read timed out")
    with pytest.raises(RuntimeError) as exc:
        list(mi.iter_delta_pages(_DRIVE, fetch, top=10))
    assert "delta page 1" in str(exc.value) and "read timed out" in str(exc.value)   # not a hang


def test_limit_and_top_passthrough():
    rec = {}
    import types

    def run(*, drive_id, root, download, limit, manifest, top=None, dry_run=False):
        rec.update({"limit": limit, "top": top, "download": download})
        return ([{"name": "a", "item_id": "1", "web_url": "u"}], {})
    stager = mi.resolve_sharepoint_stager(module=types.SimpleNamespace(run=run), drive_ids=["d1"],
                                          dry_run=True, limit=10, top=5)
    stager()
    assert rec["limit"] == 10 and rec["top"] == 5 and rec["download"] is False   # small page, no download


def test_connector_timeout_surfaced_not_hung():
    import time
    import types

    def run(*, drive_id, root, download, limit, manifest):
        time.sleep(0.4)                                            # simulate a slow initial delta page
        return {"status": "ok"}
    stager = mi.resolve_sharepoint_stager(module=types.SimpleNamespace(run=run), drive_ids=["d1"],
                                          dry_run=True, timeout=1)   # 0.4s < 1s -> returns
    stager()                                                       # does not hang

    stager2 = mi.resolve_sharepoint_stager(module=types.SimpleNamespace(run=run), drive_ids=["d1"],
                                           dry_run=True, timeout=0)  # 0 disables; still returns
    stager2()

    def slow(*, drive_id, root, download, limit, manifest):
        time.sleep(0.5)
        return {}
    diag = {}
    stager3 = mi.resolve_sharepoint_stager(module=types.SimpleNamespace(run=slow), drive_ids=["d1"],
                                           dry_run=True, timeout=0.1, diag=diag)
    with pytest.raises(TimeoutError) as exc:
        stager3()
    assert "exceeded 0.1s" in str(exc.value) and "--top" in str(exc.value)   # clean timeout message


# --- dry-run stages metadata via the connector's delta ITERATOR (iter_drive_items), no download -------
# Production connector: run(download=False) enumerates but returns ONLY a summary (no items, no manifest),
# so the importer saw 0 staged items and (before the fix) reported the whole corpus missing. The adapter
# now stages metadata via the connector's own delta iterator instead — files only, no content download.

def _delta_connector(recorder, *, files=10, folders=5):
    """A connector shaped like production: run() couples download+manifest (unusable for dry-run), while
    iter_drive_items() yields raw Graph driveItems (files + folders) with NO download."""
    import types

    def run(*, drive_id, root, download, limit, manifest):     # present (needs_drive); NOT used in dry-run
        recorder.append(("run", drive_id, download))
        return {"status": "ok", "files_seen": 0}               # download=False -> summary only, 0 items

    def iter_drive_items(*, drive_id, top=None, limit=None, download=True, dry_run=False):
        recorder.append(("iter", drive_id, download, dry_run, top, limit))
        for i in range(folders):                               # folders must be skipped (files only)
            yield {"id": f"F{i}", "name": f"Folder{i}", "folder": {"childCount": 2},
                   "parentReference": {"path": "/drive/root:", "driveId": drive_id, "siteId": "S1"}}
        for i in range(files):
            yield {"id": f"D{i}", "name": f"doc{i}.pdf", "file": {"mimeType": "application/pdf"},
                   "size": 100 + i, "webUrl": f"https://sp/{drive_id}/doc{i}.pdf",
                   "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                   "parentReference": {"path": "/drive/root:/Clients", "driveId": drive_id, "siteId": "S1"}}

    return types.SimpleNamespace(run=run, iter_drive_items=iter_drive_items)


def test_iter_drive_items_stages_file_metadata_without_download():
    rec = []
    diag = {}
    stager = mi.resolve_sharepoint_stager(module=_delta_connector(rec), drive_ids=["drvZ"],
                                          dry_run=True, diag=diag)
    items = stager()
    # 10 file records (5 folders skipped); each carries the base metadata import_sharepoint_items needs.
    assert len(items) == 10 and diag["total_items"] == 10
    assert diag["enumerator"] == "iter_drive_items"
    r = items[0]
    for k in ("drive_id", "item_id", "name", "parent_path", "web_url", "target", "size_bytes",
              "modified_at"):
        assert k in r, k
    assert r["drive_id"] == "drvZ" and r["dry_run"] is True and r["status"] == "dry_run_metadata"
    assert r["web_url"] == "https://sp/drvZ/doc0.pdf" and r["size_bytes"] == 100
    # enumeration ran with download OFF; run() (the download path) was never invoked
    assert ("iter", "drvZ", False, True, None, None) in rec
    assert not any(c[0] == "run" for c in rec)
    assert diag["drives"][0]["source"] == "delta_enumeration" and diag["drives"][0]["download"] is False


def test_iter_dry_run_limit_10_examines_10_no_canonical_no_downloads(tmp_path):
    # The exact --limit 10 dry-run validation shape: 10 staged metadata records -> items_examined == 10,
    # while NO canonical documents are created and NO files are downloaded.
    dest = tmp_path / "dest"
    with engine.connect() as c:
        docs_before = c.execute(select(func.count()).select_from(documents)).scalar()
    stager = mi.resolve_sharepoint_stager(module=_delta_connector([], files=12, folders=3),
                                          drive_ids=["drvL"], dry_run=True, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(dest), dry_run=True, ocr=False)
    with engine.connect() as c:
        docs_after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert summary["status"] == "dry_run"
    assert summary["items_examined"] == 10                     # capped by --limit 10
    assert docs_after == docs_before                           # canonical_created (in DB) == 0
    assert not dest.exists() or not any(dest.rglob("*.*"))     # downloads == 0
    assert summary["missing"] == 0                             # partial dry-run never reconciles missing
    assert summary.get("missing_reconciliation_skipped")


def test_missing_not_reconciled_against_empty_staging(tmp_path):
    # A REAL sync that stages ZERO items (connector/enumeration failure) must NOT mark the existing
    # corpus missing — this is the 21,697-false-missing guard.
    it = _item(tmp_path, name="keep.pdf", uri=f"https://sp/{_A}/keep")
    _run([it], tmp_path)                                       # ingest one real ref (available)
    assert _doc_for(it["web_url"])["available"] is True
    summary = _run([], tmp_path)                               # next sync stages nothing
    assert summary["missing"] == 0 and summary.get("missing_reconciliation_skipped")
    assert _doc_for(it["web_url"])["available"] is True        # untouched, still available


def test_partial_dry_run_does_not_report_existing_corpus_missing(tmp_path):
    # With existing refs in the corpus, a partial (limited) dry-run enumerating only some items must NOT
    # report the un-enumerated remainder as missing.
    a = _item(tmp_path, name="a.pdf", uri=f"https://sp/{_A}/a")
    b = _item(tmp_path, name="b.pdf", uri=f"https://sp/{_A}/b")
    _run([a, b], tmp_path)                                     # two real refs exist
    # dry-run enumerates a single (already-known) item
    summary = mi.run_sharepoint_sync(items=[{"name": "a.pdf", "web_url": a["web_url"], "item_id": "a",
                                             "size": a["size"], "modified_at": "2024-01-01T00:00:00",
                                             "dry_run": True}],
                                     destination_root=str(tmp_path / "d"), dry_run=True, ocr=False)
    _track()
    assert summary["missing"] == 0 and summary.get("missing_reconciliation_skipped")
    assert _doc_for(b["web_url"])["available"] is True         # the un-enumerated ref stays available


# --- dry-run enumerator builds its session via the connector's OWN auth (latest_account -> token ->
#     graph_session), then calls iter_drive_items(session, drive_id) — no generic auth abstraction ------

def _session_delta_connector(recorder, *, files=10, folders=5):
    """A connector shaped like production: iter_drive_items(session, drive_id) needs an authenticated
    requests.Session, and run() builds it from latest_account -> get_microsoft_access_token -> graph_session.
    The adapter must reuse those exact three functions to construct the session for dry-run enumeration."""
    import types

    def latest_account():
        recorder.append("latest_account")
        return {"id": "acct-1"}

    def get_microsoft_access_token(account):
        recorder.append(("token", account["id"]))
        return f"tok:{account['id']}"

    def graph_session(token):
        recorder.append(("session", token))
        return {"__session__": True, "token": token}          # stand-in for requests.Session

    def run(*, drive_id, root, download, limit, manifest):     # present (needs_drive); NOT used in dry-run
        recorder.append(("run", drive_id))
        return {"status": "ok"}

    def iter_drive_items(session, drive_id):                   # exact production signature (positional)
        recorder.append(("iter", session, drive_id))
        assert isinstance(session, dict) and session.get("__session__") is True   # the built session
        for i in range(folders):
            yield {"id": f"F{i}", "name": f"Folder{i}", "folder": {"childCount": 1},
                   "parentReference": {"driveId": drive_id, "siteId": "S1", "path": "/drive/root:"}}
        for i in range(files):
            yield {"id": f"D{i}", "name": f"doc{i}.pdf", "file": {"mimeType": "application/pdf"},
                   "size": 100 + i, "webUrl": f"https://sp/{drive_id}/doc{i}.pdf",
                   "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                   "parentReference": {"driveId": drive_id, "siteId": "S1", "path": "/drive/root:/Clients"}}

    return types.SimpleNamespace(latest_account=latest_account,
                                 get_microsoft_access_token=get_microsoft_access_token,
                                 graph_session=graph_session, run=run,
                                 iter_drive_items=iter_drive_items)


def test_enumerator_builds_session_from_connector_auth_path():
    rec = []
    diag = {}
    stager = mi.resolve_sharepoint_stager(module=_session_delta_connector(rec), drive_ids=["drvS"],
                                          dry_run=True, diag=diag)
    items = stager()
    # session was built via the connector's OWN three functions, in order...
    assert rec[0] == "latest_account"
    assert ("token", "acct-1") in rec and ("session", "tok:acct-1") in rec
    # ...and iter_drive_items received exactly that authenticated session + the drive id
    icall = next(c for c in rec if isinstance(c, tuple) and c[0] == "iter")
    assert icall[1] == {"__session__": True, "token": "tok:acct-1"} and icall[2] == "drvS"
    assert not any(c[0] == "run" for c in rec if isinstance(c, tuple))   # NOT a run() fallback
    assert len(items) == 10 and diag["total_items"] == 10 and not diag.get("enum_errors")
    assert items[0]["web_url"] == "https://sp/drvS/doc0.pdf" and items[0]["dry_run"] is True


def test_session_enumerator_dry_run_10_items_no_download_no_canonical(tmp_path):
    dest = tmp_path / "dest"
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents)).scalar()
    stager = mi.resolve_sharepoint_stager(module=_session_delta_connector([], files=12, folders=4),
                                          drive_ids=["drvS2"], dry_run=True, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(dest), dry_run=True, ocr=False)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert summary["status"] == "dry_run"
    assert summary["items_examined"] == 10                     # capped by --limit 10
    assert summary["missing"] == 0                             # guard: partial dry-run never reconciles
    assert after == before                                     # no canonical DB mutation
    assert not dest.exists() or not any(dest.rglob("*.*"))     # no download


def test_enumerator_falls_back_to_run_only_when_auth_path_absent():
    # If the connector does NOT expose the three auth functions, session can't be built and iter_drive_items
    # (which requires session) is unusable -> clean fallback to run(), recorded in enum_errors.
    import types
    rec = []

    def run(*, drive_id, root, download, limit, manifest):
        rec.append(("run", drive_id, download))
        return {"status": "ok", "files_seen": 0}

    def iter_drive_items(session, drive_id):     # requires session, but no auth path exposed
        yield {"id": "x", "name": "x.pdf", "file": {}, "webUrl": "u"}

    diag = {}
    stager = mi.resolve_sharepoint_stager(module=types.SimpleNamespace(run=run,
                                          iter_drive_items=iter_drive_items),
                                          drive_ids=["d1"], dry_run=True, diag=diag)
    stager()
    assert diag.get("enum_errors") and "session" in diag["enum_errors"][0]
    assert any(c[0] == "run" for c in rec)       # fell back to run()


# --- dry-run enumerator STREAMS the delta and stops at --limit (never materializes the full feed) -----
# The production drive has ~55,708 files; list(iter_drive_items(...)) drains it all and hits the 120s
# timeout. Correct behavior (like the connector's run()): stream and break once `limit` FILE records are
# collected. The iterator below RAISES if pulled past the first 15 raw items, proving no materialization.

def _counting_connector(consumed, *, files=10, folders=5):
    import types

    def latest_account():
        return {"id": "acct-1"}

    def get_microsoft_access_token(account):
        return f"tok:{account['id']}"

    def graph_session(token):
        return {"__session__": True, "token": token}

    def run(*, drive_id, root, download, limit, manifest):    # present (needs_drive); not used in dry-run
        return {"status": "ok"}

    def iter_drive_items(session, drive_id):
        assert session and session.get("__session__") is True
        for i in range(folders):                              # 5 folders first (skipped, don't count)
            consumed.append(("folder", i))
            yield {"id": f"F{i}", "name": f"Folder{i}", "folder": {"childCount": 1},
                   "parentReference": {"driveId": drive_id, "siteId": "S1", "path": "/drive/root:"}}
        for i in range(files):                                # then 10 files
            consumed.append(("file", i))
            yield {"id": f"D{i}", "name": f"doc{i}.pdf", "file": {"mimeType": "application/pdf"},
                   "size": 100 + i, "webUrl": f"https://sp/{drive_id}/doc{i}.pdf",
                   "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                   "parentReference": {"driveId": drive_id, "siteId": "S1", "path": "/drive/root:/Clients"}}
        consumed.append(("OVERRUN",))                         # item 16+ must NEVER be pulled
        raise AssertionError("iterator consumed past the first 15 raw items (limit not honored)")
        yield  # pragma: no cover — unreachable; makes this unambiguously a generator

    return types.SimpleNamespace(latest_account=latest_account,
                                 get_microsoft_access_token=get_microsoft_access_token,
                                 graph_session=graph_session, run=run,
                                 iter_drive_items=iter_drive_items)


def test_enumerator_streams_and_stops_at_limit_without_materializing():
    consumed = []
    diag = {}
    stager = mi.resolve_sharepoint_stager(module=_counting_connector(consumed), drive_ids=["drvBig"],
                                          dry_run=True, diag=diag, limit=10)
    items = stager()
    assert len(items) == 10                              # 5 folders skipped -> 10 file records
    assert len(consumed) == 15                           # exactly 5 folders + 10 files consumed...
    assert ("OVERRUN",) not in consumed                 # ...and item 16 was NEVER pulled
    assert not diag.get("enum_errors")                  # streamed successfully, no run() fallback
    assert diag["total_items"] == 10 and diag["drives"][0]["download"] is False


def test_streamed_dry_run_examines_10_missing_0_no_download(tmp_path):
    dest = tmp_path / "dest"
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents)).scalar()
    stager = mi.resolve_sharepoint_stager(module=_counting_connector([]), drive_ids=["drvBig2"],
                                          dry_run=True, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(dest), dry_run=True, ocr=False)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert summary["status"] == "dry_run"
    assert summary["items_examined"] == 10              # streamed + stopped at the limit
    assert summary["missing"] == 0                      # guard intact
    assert after == before                              # no canonical DB mutation
    assert not dest.exists() or not any(dest.rglob("*.*"))   # no download
