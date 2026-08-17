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


# --- REAL (non-dry-run) invocation: connector run(root: Path, manifest: Path); append_manifest uses
#     manifest.parent. The Path-vs-str boundary must hold (dry-run never wrote a manifest). ------------

def test_connector_values_root_and_manifest_are_paths():
    from pathlib import Path
    v = mi._connector_values(root="/tmp/stage", drive_id="d1", dry_run=False, limit=10)
    assert isinstance(v["root"], Path) and isinstance(v["staging_root"], Path)
    assert isinstance(v["manifest"], Path) and isinstance(v["manifest_path"], Path)
    assert v["manifest"].name == "d1_manifest.json" and v["manifest"].parent == Path("/tmp/stage")
    # the exact operation that failed in production ('str' has no attribute 'parent') now works:
    assert v["manifest"].parent == v["root"]


def _path_asserting_connector(rec):
    """Production-shaped run(root: Path, manifest: Path); append_manifest() touches manifest.parent."""
    import json
    import types
    from pathlib import Path

    def run(*, drive_id, root, download, limit, manifest):
        rec.append({"root_is_path": isinstance(root, Path), "manifest_is_path": isinstance(manifest, Path),
                    "download": download, "limit": limit})
        manifest.parent.mkdir(parents=True, exist_ok=True)      # <- the '.parent' access that crashed
        staged = manifest.parent / f"{drive_id}_f1.txt"
        staged.write_text("hello statement body")               # a real downloaded file
        manifest.write_text(json.dumps([{                       # append_manifest handoff
            "name": "f1.pdf", "web_url": f"https://sp/{drive_id}/f1", "item_id": "f1",
            "site": "S", "library": "D", "modified_at": "2024-01-01T00:00:00",
            "size": staged.stat().st_size, "local_path": str(staged)}]))
        return {"status": "ok", "files_seen": 1, "files_downloaded": 1}

    return types.SimpleNamespace(run=run)


def test_real_run_receives_path_root_and_manifest_no_parent_error(tmp_path):
    import os
    rec = []
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "stage")
    try:
        stager = mi.resolve_sharepoint_stager(module=_path_asserting_connector(rec),
                                               drive_ids=["drvP"], dry_run=False, limit=10)
        summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "dest"),
                                         dry_run=False, ocr=False)
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    _track()
    # no 'str object has no attribute parent' anywhere, and the file actually imported
    assert not any("has no attribute 'parent'" in e for e in summary.get("errors", []))
    assert summary["status"] == "completed" and summary["canonical_created"] == 1
    # root AND manifest reached the connector as pathlib.Path
    assert rec and rec[0]["root_is_path"] is True and rec[0]["manifest_is_path"] is True
    assert rec[0]["download"] is True and rec[0]["limit"] == 10   # real run downloads; --limit preserved


def test_real_run_reuses_already_downloaded_file_no_duplicate(tmp_path):
    # If a prior attempt already staged/imported a file, a re-run dedupes (SHA-256) — no second canonical.
    import os
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "stage")
    try:
        for _ in range(2):                                       # run the same real sync twice
            stager = mi.resolve_sharepoint_stager(module=_path_asserting_connector([]),
                                                   drive_ids=["drvDup"], dry_run=False, limit=10)
            mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "dest"),
                                   dry_run=False, ocr=False)
            _track()
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    assert _canonical_count("https://sp/drvDup/f1") == 1         # single source ref, no duplicate


# --- Windows cross-volume staging: connector temp on D:, adapter default target on C: -> WinError 17 ---
# Fix: hand the connector its OWN staging root so temp .part and final dest share a volume. safe_finalize
# is the tracked cross-volume-safe fallback (copy+verify+unlink) if staging ever must span volumes.

def test_connector_staging_root_prefers_connector_own_config():
    import os
    import types
    mod = types.SimpleNamespace(DEFAULT_STAGING_ROOT=r"D:\Client360\Content\SharePoint")
    assert mi._connector_staging_root(mod) == r"D:\Client360\Content\SharePoint"
    os.environ["CLIENT360_SHAREPOINT_STAGING_ROOT"] = r"D:\Alt\Staging"
    try:                                                     # env fallback when connector exposes nothing
        assert mi._connector_staging_root(types.SimpleNamespace()) == r"D:\Alt\Staging"
    finally:
        del os.environ["CLIENT360_SHAREPOINT_STAGING_ROOT"]


def test_safe_finalize_same_volume_uses_atomic_replace(tmp_path):
    src = tmp_path / "a.part"
    src.write_bytes(b"content-xyz")
    dest = tmp_path / "sub" / "a.bin"
    mi.safe_finalize(src, dest)
    assert dest.read_bytes() == b"content-xyz" and not src.exists()   # moved atomically, temp gone


def test_safe_finalize_cross_volume_copies_verifies_and_removes_temp(tmp_path, monkeypatch):
    import errno
    import os
    src = tmp_path / "b.part"
    payload = b"cross-volume-bytes-preserved"
    src.write_bytes(payload)
    dest = tmp_path / "other" / "b.bin"

    def _exdev(a, b):                                        # simulate a cross-disk os.replace
        raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
    monkeypatch.setattr(os, "replace", _exdev)
    mi.safe_finalize(src, dest)                             # must NOT raise WinError 17
    assert dest.exists() and dest.read_bytes() == payload  # staged file exists, content/hash preserved
    assert not src.exists()                                 # temp removed only AFTER the verified copy


def _volume_of(path, base):
    from pathlib import Path
    rel = Path(path).resolve().relative_to(Path(base).resolve())
    return rel.parts[0] if rel.parts else ""


def _volume_sensitive_connector(base, rec, *, expose_root=True):
    """Connector whose temp lives under its OWN content root (pretend D:). Its atomic finalize raises
    WinError 17 when the target root is on a different 'volume' than the temp — exactly like production."""
    import json
    import os
    import types
    from pathlib import Path
    content_root = Path(base) / "Dvol" / "Content" / "SharePoint"
    tmp_dir = content_root / ".tmp"

    def run(*, drive_id, root, download, limit, manifest):
        rec.append(str(root))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        part = tmp_dir / f"{drive_id}.part"
        part.write_bytes(b"hello sharepoint statement body")
        dest = Path(root) / f"{drive_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _volume_of(part, base) != _volume_of(dest, base):     # cross-volume replace -> WinError 17
            raise OSError(17, "The system cannot move the file to a different disk drive")
        os.replace(str(part), str(dest))
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps([{"name": f"{drive_id}.pdf",
            "web_url": f"https://sp/{drive_id}/f1", "item_id": "f1", "site": "S", "library": "D",
            "modified_at": "2024-01-01T00:00:00", "size": dest.stat().st_size,
            "local_path": str(dest)}]))
        return {"status": "ok", "files_seen": 1, "downloaded": 1}

    ns = types.SimpleNamespace(run=run)
    if expose_root:
        ns.DEFAULT_STAGING_ROOT = str(content_root)
    return ns


def test_real_run_stages_on_connector_volume_no_winerror17(tmp_path):
    rec = []
    conn = _volume_sensitive_connector(tmp_path, rec)
    stager = mi.resolve_sharepoint_stager(module=conn, drive_ids=["drvV"], dry_run=False, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "canonical"),
                                     dry_run=False, ocr=False)
    _track()
    assert not any("disk drive" in e for e in summary.get("errors", []))   # no WinError 17
    assert summary["status"] == "completed" and summary["canonical_created"] == 1
    assert rec and "Dvol" in rec[0]                         # adapter handed the connector its OWN root


def test_cross_volume_reproduces_winerror17_without_alignment(tmp_path):
    # Control: connector temp on Dvol, but its own root is hidden and the adapter root is forced to Cvol
    # -> the connector's replace crosses volumes and fails, proving the alignment fix is what avoids it.
    import os
    rec = []
    conn = _volume_sensitive_connector(tmp_path, rec, expose_root=False)
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "Cvol" / "_staging")
    try:
        stager = mi.resolve_sharepoint_stager(module=conn, drive_ids=["drvX"], dry_run=False, limit=10)
        summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "canonical"),
                                         dry_run=False, ocr=False)
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    assert summary["status"] == "error" and any("disk drive" in e for e in summary["errors"])
    assert "Cvol" in rec[0]                                 # forced to the wrong volume in this control


# --- production-equivalent: connector finalizes with a REAL Path.replace/os.replace across volumes ------
# The untracked prod connector downloads temp to D:\...\.tmp and replaces into the passed root on C:.
# The tracked adapter wraps the connector call so that cross-volume replace/rename transparently falls
# back to copy+verify+unlink — no WinError 17 — WITHOUT editing the per-deployment connector.

def _real_replace_connector(base, rec, *, use_path_replace=False, ok_counts=True):
    import json
    import os
    import types
    from pathlib import Path
    tmp_dir = Path(base) / "Dvol" / "Content" / "SharePoint" / ".tmp"     # connector temp on 'D:'

    def run(*, drive_id, root, download, limit, manifest):
        rec.append(str(root))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        part = tmp_dir / f"{drive_id}.part"
        part.write_bytes(b"hello sharepoint statement body")
        dest = Path(root) / f"{drive_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if use_path_replace:
            part.replace(dest)                          # pathlib.Path.replace (the dev connector's op)
        else:
            os.replace(str(part), str(dest))            # os.replace
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps([{"name": f"{drive_id}.pdf",
            "web_url": f"https://sp/{drive_id}/f1", "item_id": "f1", "site": "S", "library": "D",
            "modified_at": "2024-01-01T00:00:00", "size": dest.stat().st_size, "local_path": str(dest)}]))
        return ({"status": "ok", "files_seen": 1, "downloaded": 1, "failed": 0} if ok_counts
                else {"status": "ok", "files_seen": 1, "downloaded": 0, "failed": 1})

    return types.SimpleNamespace(run=run)


def _install_exdev(monkeypatch, tmp_path):
    """Make os.replace raise EXDEV whenever src/dst are on different simulated volumes (else real)."""
    import errno
    import os
    real = os.replace

    def _repl(src, dst, *a, **k):
        if _volume_of(src, tmp_path) != _volume_of(dst, tmp_path):
            raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", _repl)


def test_prod_path_os_replace_cross_volume_no_winerror17(tmp_path, monkeypatch):
    _install_exdev(monkeypatch, tmp_path)
    monkeypatch.setenv("CLIENT360_SHAREPOINT_SOURCE_ROOT", str(tmp_path / "Cvol" / "_staging"))
    rec = []
    conn = _real_replace_connector(tmp_path, rec)       # temp Dvol, dest Cvol -> cross-volume replace
    stager = mi.resolve_sharepoint_stager(module=conn, drive_ids=["drvR"], dry_run=False, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "canonical"),
                                     dry_run=False, ocr=False)
    _track()
    assert not any("disk drive" in e for e in summary.get("errors", []))     # no WinError 17
    assert summary["status"] == "completed" and summary["canonical_created"] == 1
    assert "Cvol" in rec[0]                              # dest genuinely on the other volume
    # staged file landed on the C: dest with correct content, and the D: temp was cleaned up
    dest = tmp_path / "Cvol" / "_staging" / "drvR.bin"
    assert dest.read_bytes() == b"hello sharepoint statement body"
    assert not (tmp_path / "Dvol" / "Content" / "SharePoint" / ".tmp" / "drvR.part").exists()


def test_prod_path_pathlib_replace_cross_volume_no_winerror17(tmp_path, monkeypatch):
    _install_exdev(monkeypatch, tmp_path)
    monkeypatch.setenv("CLIENT360_SHAREPOINT_SOURCE_ROOT", str(tmp_path / "Cvol" / "_staging"))
    rec = []
    conn = _real_replace_connector(tmp_path, rec, use_path_replace=True)
    stager = mi.resolve_sharepoint_stager(module=conn, drive_ids=["drvPR"], dry_run=False, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "canonical"),
                                     dry_run=False, ocr=False)
    _track()
    assert not any("disk drive" in e for e in summary.get("errors", []))
    assert summary["status"] == "completed" and summary["canonical_created"] == 1
    assert (tmp_path / "Cvol" / "_staging" / "drvPR.bin").read_bytes() == b"hello sharepoint statement body"


def test_prod_path_same_volume_dd_still_atomic(tmp_path, monkeypatch):
    # Preferred config: connector temp and destination both on 'D:' -> plain atomic replace, no fallback.
    _install_exdev(monkeypatch, tmp_path)
    monkeypatch.setenv("CLIENT360_SHAREPOINT_SOURCE_ROOT",
                       str(tmp_path / "Dvol" / "Content" / "SharePoint" / "_staging"))
    rec = []
    conn = _real_replace_connector(tmp_path, rec)
    stager = mi.resolve_sharepoint_stager(module=conn, drive_ids=["drvDD"], dry_run=False, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "canonical"),
                                     dry_run=False, ocr=False)
    _track()
    assert summary["status"] == "completed" and summary["canonical_created"] == 1
    assert "Dvol" in rec[0]                              # both temp and dest on the same volume
    assert (tmp_path / "Dvol" / "Content" / "SharePoint" / "_staging" / "drvDD.bin").exists()


def test_all_downloads_failed_reports_error_not_completed(tmp_path):
    # files_seen>0, downloaded=0, no items -> must NOT report "completed".
    import types
    conn = types.SimpleNamespace(run=lambda **kw: {"status": "ok", "files_seen": 10,
                                                   "downloaded": 0, "failed": 10})
    stager = mi.resolve_sharepoint_stager(module=conn, drive_ids=["drvF"], dry_run=False, limit=10)
    summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "dest"),
                                     dry_run=False, ocr=False)
    assert summary["status"] == "error" and summary["status"] != "completed"
    assert any("download" in e.lower() for e in summary.get("errors", []))


def test_diagnostics_report_real_staging_root_and_cross_volume_flag():
    d = mi.sharepoint_staging_diagnostics()
    assert "real_staging_root" in d and "connector_temp_root" in d
    assert d["cross_volume_safe_finalize"] is True      # WinError 17 handled on the real path


# --- manifest parsing: array / JSONL append-only, failed excluded, retries deduped ------------------
# Production connector downloads succeed and write a manifest, but _read_manifest_file returned 0 because
# the file is append-only (JSONL / retried records), not a single JSON array. Parser must handle both,
# drop failed records, and dedupe retries by SharePoint identity (drive_id+item_id / web_url).

def _mrec(tmp_path, i, *, drive="drvM", status="downloaded", body=None):
    body = body if body is not None else f"statement body number {i}"
    f = tmp_path / f"{drive}_{i}_{uuid.uuid4().hex}.txt"
    f.write_text(body)
    return {"name": f"doc{i}.pdf", "web_url": f"https://sp/{drive}/doc{i}", "item_id": f"itm{i}",
            "drive_id": drive, "site": "S", "library": "D", "modified_at": "2024-01-01T00:00:00",
            "size": f.stat().st_size, "local_path": str(f), "status": status}


def test_parse_manifest_json_array(tmp_path):
    recs = [_mrec(tmp_path, i) for i in range(10)]
    p = tmp_path / "manifest.json"
    p.write_text(__import__("json").dumps(recs, indent=2))
    got = mi._read_manifest_file(str(p))
    assert len(got) == 10


def test_parse_manifest_jsonl_appendonly_excludes_failed_and_dedupes(tmp_path):
    import json
    lines = []
    for i in range(10):                                     # 10 good downloads
        lines.append(json.dumps(_mrec(tmp_path, i)))
    lines.insert(3, json.dumps({"name": "doc3.pdf", "web_url": "https://sp/drvM/doc3",
                                "item_id": "itm3", "drive_id": "drvM", "status": "failed",
                                "error": "HTTP 500"}))       # a FAILED attempt for an item that later succeeds
    lines.append(json.dumps(_mrec(tmp_path, 0)))            # a RETRY duplicate of item 0 (success again)
    lines.append(json.dumps({"name": "z.pdf", "web_url": "https://sp/drvM/z", "item_id": "z",
                             "drive_id": "drvM", "status": "download_failed"}))   # purely-failed item
    p = tmp_path / "manifest.jsonl"
    p.write_text("\n".join(lines) + "\n")
    got = mi._read_manifest_file(str(p))
    ids = sorted(r["item_id"] for r in got)
    assert ids == [f"itm{i}" for i in range(10)]           # 10 unique successes; failed + dup + z excluded
    assert len(got) == 10


def test_analyze_manifest_reports_counts(tmp_path):
    import json
    recs = [_mrec(tmp_path, i) for i in range(10)]
    recs.append({"item_id": "itm0", "drive_id": "drvM", "web_url": "https://sp/drvM/doc0",
                 "name": "doc0.pdf", "status": "downloaded"})           # duplicate of itm0
    recs.append({"item_id": "bad", "drive_id": "drvM", "status": "failed"})
    p = tmp_path / "m.json"
    p.write_text(json.dumps(recs))
    d = mi.analyze_manifest(str(p))
    assert d["record_count"] == 12 and d["successful_records"] == 11 and d["failed_records"] == 1
    assert d["unique_item_ids"] == 11 and d["duplicate_records"] == 1 and d["parsed_staged_items"] == 10


def test_manifest_jsonl_end_to_end_items_examined_10_and_reuse(tmp_path):
    import json
    recs = [_mrec(tmp_path, i) for i in range(10)]
    recs.insert(0, {"item_id": "itm0", "drive_id": "drvM", "web_url": "https://sp/drvM/doc0",
                    "status": "failed", "error": "timeout"})            # early failed attempt (deduped out)
    p = tmp_path / "manifest.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    items = mi.load_manifest_items(str(p))
    assert len(items) == 10
    r1 = mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"),
                                dry_run=False, ocr=False)
    _track()
    assert r1["status"] == "completed" and r1["items_examined"] == 10
    assert r1["canonical_created"] == 10 and r1["missing"] == 0
    # rerun the SAME manifest -> reused/skipped, no duplicate canonical documents
    r2 = mi.run_sharepoint_sync(items=mi.load_manifest_items(str(p)),
                                destination_root=str(tmp_path / "dest"), dry_run=False, ocr=False)
    _track()
    assert r2["items_examined"] == 10 and r2["canonical_created"] == 0 and r2["missing"] == 0
    assert _canonical_count("https://sp/drvM/doc0") == 1   # no duplicate canonical on rerun


def test_manifest_reconcile_via_stage_one_from_connector(tmp_path):
    # The full staging path: connector writes an append-only JSONL manifest; _stage_one must parse it to
    # 10 items (not 0), with failed rows excluded.
    import json
    import types
    from pathlib import Path

    def run(*, drive_id, root, download, limit, manifest):
        Path(manifest).parent.mkdir(parents=True, exist_ok=True)
        recs = [_mrec(tmp_path, i, drive=drive_id) for i in range(10)]
        recs.append({"item_id": "itm0", "drive_id": drive_id, "status": "failed"})   # retried-failed dup
        Path(manifest).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return {"status": "ok", "files_seen": 11, "downloaded": 10, "failed": 1}

    import os
    os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"] = str(tmp_path / "stage")
    try:
        diag = {}
        stager = mi.resolve_sharepoint_stager(module=types.SimpleNamespace(run=run),
                                               drive_ids=["drvC"], dry_run=False, diag=diag, limit=10)
        summary = mi.run_sharepoint_sync(stager=stager, destination_root=str(tmp_path / "dest"),
                                         dry_run=False, ocr=False)
    finally:
        del os.environ["CLIENT360_SHAREPOINT_SOURCE_ROOT"]
    _track()
    assert diag["total_items"] == 10 and diag["drives"][0]["source"] == "manifest_file"
    assert summary["status"] == "completed" and summary["items_examined"] == 10 and summary["missing"] == 0


# --- BUG1 target->local_path normalization + BUG2 authoritative/full-snapshot reconciliation ----------
# Production successful records carry the download path in `target`, not `local_path`; and --manifest is a
# PARTIAL batch that must never reconcile the 21,697 existing refs as missing.

def _trec(tmp_path, i, *, drive="drvT", field="target", status="downloaded"):
    """A production-shaped successful record: identity + `target` (download destination), NO local_path."""
    f = tmp_path / f"{drive}_{i}_{uuid.uuid4().hex}.txt"
    f.write_text(f"statement body {i}")
    r = {"name": f"doc{i}.pdf", "web_url": f"https://sp/{drive}/doc{i}", "item_id": f"it{i}",
         "drive_id": drive, "site": "S", "library": "D", "modified_at": "2024-01-01T00:00:00",
         "size_bytes": f.stat().st_size, "status": status}
    r[field] = str(f)                                       # connector's destination field, not local_path
    return r


def test_manifest_target_field_normalized_to_local_path(tmp_path):
    import json
    recs = [_trec(tmp_path, i) for i in range(10)]
    assert all("local_path" not in r for r in recs)         # production shape: target only
    p = tmp_path / "m.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    items = mi.load_manifest_items(str(p))
    assert len(items) == 10 and all(it.get("local_path") for it in items)   # target -> local_path
    summary = mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"),
                                     dry_run=False, ocr=False, authoritative=False)
    _track()
    assert summary["items_examined"] == 10
    assert not any("local_path" in e for e in summary.get("errors", []))     # no missing-local_path errors
    assert summary["canonical_created"] == 10 and summary["missing"] == 0
    assert summary["status"] == "completed"


def test_direct_items_target_normalized_by_importer(tmp_path):
    # Even without the adapter parser, the importer resolves `target` -> local_path.
    recs = [_trec(tmp_path, i, drive="drvD") for i in range(3)]
    summary = mi.run_sharepoint_sync(items=recs, destination_root=str(tmp_path / "dest"),
                                     dry_run=False, ocr=False, authoritative=False)
    _track()
    assert summary["items_examined"] == 3 and summary["canonical_created"] == 3
    assert not summary.get("errors")


def test_partial_manifest_does_not_reconcile_existing_refs(tmp_path):
    seeds = [_item(tmp_path, name=f"s{i}.txt", uri=f"https://sp/{_A}/seed{i}") for i in range(3)]
    _run(seeds, tmp_path, authoritative=True)               # establish 3 existing available refs
    for s in seeds:
        assert _doc_for(s["web_url"])["available"] is True
    other = [_trec(tmp_path, 99, drive="drvP")]             # a partial batch of a DIFFERENT item
    summary = mi.run_sharepoint_sync(items=other, destination_root=str(tmp_path / "dest"),
                                     dry_run=False, ocr=False, authoritative=False)
    _track()
    assert summary["missing"] == 0
    assert summary.get("missing_reconciliation_skipped") == "partial/non-authoritative batch"
    for s in seeds:
        assert _doc_for(s["web_url"])["available"] is True  # existing corpus untouched


def test_authoritative_full_sync_still_reconciles_missing(tmp_path):
    a = _item(tmp_path, name="a.txt", uri=f"https://sp/{_A}/full_a")
    _run([a], tmp_path, authoritative=True)
    b = _item(tmp_path, name="b.txt", uri=f"https://sp/{_A}/full_b")
    summary = _run([b], tmp_path, authoritative=True)       # complete snapshot without 'a' -> a missing
    assert summary["missing"] == 1
    assert _doc_for(a["web_url"])["available"] is False


def test_dry_run_never_authoritative_even_if_flag_true(tmp_path):
    # authoritative must never override dry-run into reconciliation.
    a = _item(tmp_path, name="a.txt", uri=f"https://sp/{_A}/dr_a")
    _run([a], tmp_path, authoritative=True)
    summary = _run([_item(tmp_path, name="b.txt", uri=f"https://sp/{_A}/dr_b")], tmp_path,
                   authoritative=True, dry_run=True)
    assert summary["missing"] == 0 and summary.get("missing_reconciliation_skipped")
    assert _doc_for(a["web_url"])["available"] is True      # dry-run marked nothing


def test_all_manifest_items_fail_status_not_success(tmp_path):
    recs = [{"name": f"x{i}.pdf", "web_url": f"https://sp/dF/x{i}", "item_id": f"x{i}", "drive_id": "dF",
             "size": 10, "modified_at": "2024-01-01T00:00:00",
             "target": str(tmp_path / f"missing_{i}.bin"), "status": "downloaded"} for i in range(10)]
    summary = mi.run_sharepoint_sync(items=recs, destination_root=str(tmp_path / "dest"),
                                     dry_run=False, ocr=False, authoritative=False)
    assert summary["status"] in ("completed_with_errors", "error") and summary["status"] != "completed"
    assert len(summary.get("errors", [])) == 10 and summary["canonical_created"] == 0
    assert summary["missing"] == 0                          # partial batch never reconciles, even on failure


def test_manifest_path_records_diagnostic(tmp_path):
    import json
    recs = [_trec(tmp_path, i) for i in range(3)]
    recs.append({"name": "bad.pdf", "item_id": "bad", "drive_id": "drvT", "status": "failed"})
    p = tmp_path / "m.json"
    p.write_text(json.dumps(recs))
    rows = mi.manifest_path_records(str(p))
    assert len(rows) == 4
    good = [r for r in rows if not r["failed"]]
    assert len(good) == 3 and all(r["file_exists"] and r["target"] for r in good)
    assert rows[-1]["failed"] is True and rows[-1]["file_exists"] is False
    assert good[0]["item_id"] == "it0" and good[0]["drive_id"] == "drvT"


# --- OCR batch resilience: counters, isolation, resume-without-download, read-only status --------------

def test_run_summary_exposes_ocr_counters_and_isolates(tmp_path, monkeypatch):
    items = [_item(tmp_path, name=f"o{i}.txt", uri=f"https://sp/{_A}/ocr{i}", body=f"unique body {i}")
             for i in range(3)]
    statuses = iter(["completed", "timed_out", "failed"])   # one each: analyzed / timed_out / failed
    calls = []

    def fake(did):
        calls.append(did)
        return next(statuses)
    monkeypatch.setattr(mi, "_ocr_analyze", fake)
    summary = mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"),
                                     dry_run=False, ocr=True, authoritative=False)
    _track()
    assert len(calls) == 3                                   # every document processed (batch completed)
    assert summary["ocr_analyzed"] == 1
    assert summary["ocr_timed_out"] == 1 and summary["ocr_failed"] == 1
    assert summary["status"] == "completed_with_errors"     # OCR issues surfaced, batch still completes


def test_resume_ocr_runs_on_already_imported_docs_no_download(tmp_path, monkeypatch):
    items = [_item(tmp_path, name=f"r{i}.txt", uri=f"https://sp/{_A}/res{i}", body=f"resume body {i}")
             for i in range(3)]
    mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"), dry_run=False,
                           ocr=False, authoritative=False)  # import only (interrupted-run state)
    _track()
    seen = []
    monkeypatch.setattr(mi, "_ocr_analyze", lambda did: (seen.append(did), "completed")[1])
    counts = mi.resume_ocr_for_items(items)
    assert counts["ocr_documents"] == 3 and counts["ocr_analyzed"] == 3 and len(seen) == 3


def test_manifest_ocr_status_reports_imported_and_pending(tmp_path):
    import json
    items = [_item(tmp_path, name=f"m{i}.txt", uri=f"https://sp/{_A}/mst{i}", body=f"distinct body {i}")
             for i in range(2)]
    mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"), dry_run=False,
                           ocr=False, authoritative=False)  # imported, OCR not yet run
    _track()
    recs = [{"name": it["name"], "web_url": it["web_url"], "item_id": it["item_id"], "drive_id": "d",
             "target": it["local_path"], "status": "downloaded"} for it in items]
    recs.append({"name": "z.pdf", "web_url": f"https://sp/{_A}/notimported", "item_id": "z",
                 "drive_id": "d", "status": "downloaded", "target": str(tmp_path / "nope.bin")})
    p = tmp_path / "man.json"
    p.write_text(json.dumps(recs))
    d = mi.manifest_ocr_status(str(p))
    assert d["manifest_items"] == 3 and d["imported"] == 2 and d["not_imported"] == 1
    assert d["ocr_pending"] == 2 and d["ocr_completed"] == 0


def test_ocr_analyze_finalizes_native_text_docs_not_pending(tmp_path, monkeypatch):
    # A document analyzed via native text must end terminal (not 'none'/pending) after _ocr_analyze.
    from app.db import documents
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=f"{_A}_native.docx", stored_name=f"n-{uuid.uuid4().hex[:8]}",
            storage_path=str(tmp_path / "n.docx"), storage_provider="Client360 Local",
            storage_uri=str(tmp_path / "n.docx"), size_bytes=3,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex, status="active", archived=False)
            .returning(documents.c.id)).scalar_one()
    _SEEN_DOCS.add(did)
    monkeypatch.setattr("app.services.document_pipeline.analyze_and_persist",
                        lambda document_id, *, conn, idx=None, ocr=False: None)  # native path, no OCR row
    status = mi._ocr_analyze(did)
    assert status == "unsupported"                              # .docx -> terminal, not 'none'
    assert mi._ocr_status_for(did) == "unsupported"


def test_manifest_ocr_status_dedupes_ocr_counts_by_document(tmp_path):
    # Two manifest items with identical content dedupe to ONE canonical document (two source refs). The
    # aggregate OCR counts must be per-unique-document so they match the per-document problem list
    # (reconciles the pilot's ocr_failed=27 aggregate vs 26 detailed).
    import json
    a = _item(tmp_path, name="dup.txt", uri=f"https://sp/{_A}/dupA", body="identical content body")
    b = _item(tmp_path, name="dup.txt", uri=f"https://sp/{_A}/dupB", body="identical content body")
    mi.run_sharepoint_sync(items=[a, b], destination_root=str(tmp_path / "dest"), dry_run=False,
                           ocr=False, authoritative=False)
    _track()
    recs = [{"name": it["name"], "web_url": it["web_url"], "item_id": it["item_id"], "drive_id": "d",
             "target": it["local_path"], "status": "downloaded"} for it in (a, b)]
    p = tmp_path / "m.json"
    p.write_text(json.dumps(recs))
    d = mi.manifest_ocr_status(str(p))
    assert d["manifest_items"] == 2 and d["imported"] == 2      # per-item references (unchanged)
    assert d["unique_documents"] == 1                           # deduped to one canonical document
    total = (d["ocr_completed"] + d["ocr_pending"] + d["ocr_failed"] + d["ocr_timed_out"]
             + d["ocr_unsupported"])
    assert total == 1                                           # OCR buckets sum to unique docs, not items


# --- doc 39364 class: a reused/migrated canonical with no resolvable local file must get an OCR source ---

def _sha_of(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def test_import_reuse_backfills_missing_local_source(tmp_path):
    # A prior storage-less canonical (empty storage) with content hash S; importing a SharePoint item with
    # the same content reuses it and must BACKFILL a local file so OCR/analysis has a source.
    from app.db import documents
    body = "vanguard/yates statement content — unique for reuse backfill"
    sha = _sha_of(body)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name="s2067.pdf", stored_name=f"pre-{uuid.uuid4().hex[:8]}",
            storage_provider="Client360 Local", storage_uri="", storage_path="",
            size_bytes=len(body), sha256=sha, status="active", archived=False)
            .returning(documents.c.id)).scalar_one()
    _SEEN_DOCS.add(did)
    item = _item(tmp_path, name="s2067.pdf", uri=f"https://sp/{_A}/yates", body=body)
    summary = mi.run_sharepoint_sync(items=[item], destination_root=str(tmp_path / "canon"),
                                     dry_run=False, ocr=False, authoritative=False)
    _track()
    assert summary.get("storage_backfilled") == 1              # reuse detected the missing file and filled it
    from app.importers.sharepoint import _has_resolvable_file
    with engine.connect() as c:
        row = c.execute(select(documents.c.storage_uri, documents.c.storage_path)
                        .where(documents.c.id == did)).mappings().first()
    assert _has_resolvable_file(row)                           # OCR source now resolves


def test_backfill_local_source_refuses_on_sha_mismatch(tmp_path):
    from app.db import documents
    from app.importers.sharepoint import backfill_local_source
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name="x.pdf", stored_name=f"x-{uuid.uuid4().hex[:6]}",
            storage_provider="Client360 Local", storage_uri="", storage_path="",
            size_bytes=3, sha256="a" * 64, status="active", archived=False)
            .returning(documents.c.id)).scalar_one()
    _SEEN_DOCS.add(did)
    wrong = tmp_path / "wrong.pdf"
    wrong.write_text("different content — wrong file")         # sha != 'a'*64
    assert backfill_local_source(did, str(wrong), destination_root=str(tmp_path / "c")) is False
    with engine.connect() as c:
        row = c.execute(select(documents.c.storage_uri).where(documents.c.id == did)).mappings().first()
    assert not row["storage_uri"]                              # never linked the wrong file


def test_repair_ocr_source_paths_backfills_from_manifest(tmp_path):
    # Reproduce 39364: an imported doc whose canonical storage no longer resolves, repaired from the
    # manifest's still-present staged file (no Graph, no download).
    import json

    from app.db import documents
    body = "s2067 content used for the repair-from-manifest test path"
    item = _item(tmp_path, name="s2067.pdf", uri=f"https://sp/{_A}/repair", body=body)
    mi.run_sharepoint_sync(items=[item], destination_root=str(tmp_path / "canon"), dry_run=False,
                           ocr=False, authoritative=False)
    _track()
    did = _doc_for(item["web_url"])["document_id"]
    with engine.begin() as c:                                   # break the canonical storage pointer
        c.execute(documents.update().where(documents.c.id == did)
                  .values(storage_uri=str(tmp_path / "gone" / "missing.pdf"), storage_path=""))
    p = tmp_path / "m.json"
    p.write_text(json.dumps([{"name": "s2067.pdf", "web_url": item["web_url"], "item_id": item["item_id"],
                              "drive_id": "d", "target": item["local_path"], "status": "downloaded"}]))
    # dry-run reports without changing anything
    dry = mi.repair_ocr_source_paths(str(p), destination_root=str(tmp_path / "canon"), dry_run=True)
    assert dry["missing_source"] == 1 and dry["repaired"] == 0
    res = mi.repair_ocr_source_paths(str(p), destination_root=str(tmp_path / "canon"))
    assert res["missing_source"] == 1 and res["repaired"] == 1
    from app.importers.sharepoint import _has_resolvable_file
    with engine.connect() as c:
        row = c.execute(select(documents.c.storage_uri, documents.c.storage_path)
                        .where(documents.c.id == did)).mappings().first()
    assert _has_resolvable_file(row)                           # OCR source path restored


# --- durable per-drive delta checkpoint (@odata.deltaLink) for the canonical recurring sync -----------

_DRIVE_DELTA = "b!DELTAtestdrive" + uuid.uuid4().hex[:8]


@pytest.fixture()
def _clean_drive():
    from app.db import metadata
    t = metadata.tables["microsoft_drives"]
    with engine.begin() as c:
        c.execute(t.delete().where(t.c.microsoft_drive_id == _DRIVE_DELTA))
    yield
    with engine.begin() as c:
        c.execute(t.delete().where(t.c.microsoft_drive_id == _DRIVE_DELTA))


def test_delta_checkpoint_persisted_and_reused(_clean_drive):
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) is None          # initial: no checkpoint

    # First (initial) sync: no stored link -> starts at /root/delta, returns all items + a deltaLink.
    base = "https://graph.microsoft.com/v1.0"
    pages_initial = {
        f"{base}/drives/{_DRIVE_DELTA}/root/delta": {
            "value": [{"id": "1", "name": "a.pdf", "file": {}, "webUrl": "u1"},
                      {"id": "F", "name": "Folder", "folder": {}},          # folder skipped
                      {"id": "2", "name": "b.pdf", "file": {}, "webUrl": "u2"}],
            "@odata.deltaLink": f"{base}/drives/{_DRIVE_DELTA}/root/delta?token=DELTA1"}}
    r1 = mi.sharepoint_delta_changes(_DRIVE_DELTA, lambda url, t: pages_initial[url])
    assert r1["resumed"] is False and len(r1["changed"]) == 2 and r1["deleted"] == []
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) == f"{base}/drives/{_DRIVE_DELTA}/root/delta?token=DELTA1"

    # Recurring sync: RESUMES from the stored deltaLink (not /root/delta), returns only changes + tombstones.
    seen = {}
    pages_resume = {
        f"{base}/drives/{_DRIVE_DELTA}/root/delta?token=DELTA1": {
            "value": [{"id": "2", "name": "b.pdf", "file": {}, "webUrl": "u2"},   # modified
                      {"id": "1", "deleted": {"state": "deleted"}, "name": "a.pdf"}],  # tombstone
            "@odata.deltaLink": f"{base}/drives/{_DRIVE_DELTA}/root/delta?token=DELTA2"}}

    def fetch(url, t):
        seen["url"] = url
        return pages_resume[url]
    r2 = mi.sharepoint_delta_changes(_DRIVE_DELTA, fetch)
    assert seen["url"].endswith("token=DELTA1")                    # resumed from the persisted checkpoint
    assert r2["resumed"] is True
    assert [i["id"] for i in r2["changed"]] == ["2"]               # only the changed file
    assert [i["id"] for i in r2["deleted"]] == ["1"]              # tombstone -> deletion (source only)
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) == f"{base}/drives/{_DRIVE_DELTA}/root/delta?token=DELTA2"


def test_delta_changes_follows_nextlink_and_persist_false(_clean_drive):
    base = "https://graph.microsoft.com/v1.0"
    pages = {
        f"{base}/drives/{_DRIVE_DELTA}/root/delta": {
            "value": [{"id": "1", "name": "a.pdf", "file": {}}],
            "@odata.nextLink": f"{base}/next2"},
        f"{base}/next2": {
            "value": [{"id": "2", "name": "b.pdf", "file": {}}],
            "@odata.deltaLink": f"{base}/delta?token=T"}}
    r = mi.sharepoint_delta_changes(_DRIVE_DELTA, lambda url, t: pages[url], persist=False)
    assert r["pages"] == 2 and len(r["changed"]) == 2
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) is None          # persist=False -> checkpoint not written


def test_delta_diagnostics_reports_checkpoint(_clean_drive):
    mi.save_canonical_delta_link(_DRIVE_DELTA, "https://graph/delta?token=Z")
    rows = mi.sharepoint_delta_diagnostics(drive_ids=[_DRIVE_DELTA])
    assert len(rows) == 1 and rows[0]["canonical_checkpoint"] is True
    assert rows[0]["canonical_last_synced_at"] is not None
    assert rows[0]["source_sync_checkpoint"] is False           # legacy checkpoint untouched/separate


# --- recurring delta-checkpointed canonical sync: seed, delta-only, deletions, hold-on-failure, rerun ---

_B = "https://graph.microsoft.com/v1.0"


def _dl_stub(tmp_path):
    def download(drive_id, it):
        f = tmp_path / f"{it['id']}.bin"
        f.write_text(f"content of {it['id']}")             # deterministic per id -> stable sha (dedupe)
        return str(f)
    return download


def _changed(item_id, drive):
    return {"id": item_id, "name": f"{item_id}.pdf", "file": {}, "size": 7 + len(item_id),
            "webUrl": f"https://sp/{drive}/{item_id}", "lastModifiedDateTime": "2024-01-01T00:00:00"}


def test_delta_sync_initial_seeds_checkpoint_and_imports(tmp_path, _clean_drive):
    pages = {f"{_B}/drives/{_DRIVE_DELTA}/root/delta": {
        "value": [_changed("i1", _DRIVE_DELTA), _changed("i2", _DRIVE_DELTA),
                  {"id": "F", "name": "Folder", "folder": {}}],          # folder skipped
        "@odata.deltaLink": f"{_B}/delta?token=D1"}}
    res = mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=lambda u, t: pages[u],
                                       download=_dl_stub(tmp_path),
                                       destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    assert res["status"] == "completed" and res["changed"] == 2 and res["imported"] == 2
    assert res["drives"][0]["resumed"] is False and res["checkpoints_advanced"] == 1
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) == f"{_B}/delta?token=D1"     # checkpoint seeded
    assert _canonical_count(f"https://sp/{_DRIVE_DELTA}/i1") == 1


def test_delta_sync_subsequent_processes_only_changes_and_deletions(tmp_path, _clean_drive):
    seed = {f"{_B}/drives/{_DRIVE_DELTA}/root/delta": {
        "value": [_changed("keep", _DRIVE_DELTA)], "@odata.deltaLink": f"{_B}/delta?token=D1"}}
    dl = _dl_stub(tmp_path)
    mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=lambda u, t: seed[u], download=dl,
                                 destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    did = _doc_for(f"https://sp/{_DRIVE_DELTA}/keep")["document_id"]

    resume = {f"{_B}/delta?token=D1": {
        "value": [_changed("new1", _DRIVE_DELTA),
                  {"id": "keep", "deleted": {"state": "deleted"}}],       # tombstone, no webUrl
        "@odata.deltaLink": f"{_B}/delta?token=D2"}}
    seen = {}

    def fetch2(url, t):
        seen["u"] = url
        return resume[url]
    res = mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=fetch2, download=dl,
                                       destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    assert seen["u"].endswith("token=D1") and res["drives"][0]["resumed"] is True   # resumed from checkpoint
    assert res["changed"] == 1 and res["deleted"] == 1 and res["imported"] == 1
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) == f"{_B}/delta?token=D2"          # advanced
    assert _canonical_count(f"https://sp/{_DRIVE_DELTA}/new1") == 1                  # only the new item
    from app.db import documents
    with engine.connect() as c:                                                     # canonical NOT deleted
        assert c.execute(select(documents.c.id).where(documents.c.id == did)).scalar() == did
    assert _doc_for(f"https://sp/{_DRIVE_DELTA}/keep")["available"] is False         # source ref unavailable


def test_delta_sync_holds_checkpoint_after_download_failure(tmp_path, _clean_drive):
    pages = {f"{_B}/drives/{_DRIVE_DELTA}/root/delta": {
        "value": [_changed("ok", _DRIVE_DELTA), _changed("boom", _DRIVE_DELTA)],
        "@odata.deltaLink": f"{_B}/delta?token=D1"}}

    def dl(drive_id, it):
        if it["id"] == "boom":
            raise RuntimeError("network reset during download")
        f = tmp_path / f"{it['id']}.bin"
        f.write_text("ok")
        return str(f)
    res = mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=lambda u, t: pages[u], download=dl,
                                       destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    assert res["status"] == "completed_with_errors"
    assert res["drives"][0]["advanced"] is False and res["drives"][0]["download_failures"] == 1
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) is None                # HELD — changes re-delivered next sync
    assert any(e["phase"] == "download" for e in res["exceptions"])     # failure preserved
    assert _canonical_count(f"https://sp/{_DRIVE_DELTA}/ok") == 1        # the good item still imported


def test_delta_sync_rerun_is_idempotent(tmp_path, _clean_drive):
    page = {"value": [_changed("d1", _DRIVE_DELTA)], "@odata.deltaLink": f"{_B}/delta?token=SAME"}
    pages = {f"{_B}/drives/{_DRIVE_DELTA}/root/delta": page, f"{_B}/delta?token=SAME": page}
    dl = _dl_stub(tmp_path)
    r1 = mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=lambda u, t: pages[u], download=dl,
                                      destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    r2 = mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=lambda u, t: pages[u], download=dl,
                                      destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    assert r1["imported"] == 1 and r1["drives"][0]["resumed"] is False
    assert r2["drives"][0]["resumed"] is True                           # second run resumed from checkpoint
    assert _canonical_count(f"https://sp/{_DRIVE_DELTA}/d1") == 1        # no duplicate canonical on rerun


def test_old_source_sync_delta_link_does_not_skip_canonical_baseline(tmp_path, _clean_drive):
    # The pre-existing microsoft_document_sync checkpoint (microsoft_drives.delta_link) is populated, but
    # the canonical downloader has NO checkpoint of its own -> it must do the FULL initial baseline from
    # /root/delta, never resume from the legacy link, and never disturb it.
    from datetime import UTC, datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db import metadata
    t = metadata.tables["microsoft_drives"]
    with engine.begin() as c:
        c.execute(pg_insert(t).values(
            microsoft_drive_id=_DRIVE_DELTA, source_type="sharepoint",
            delta_link=f"{_B}/delta?token=LEGACY", last_synced_at=datetime.now(UTC)
        ).on_conflict_do_update(index_elements=[t.c.microsoft_drive_id],
                                set_={"delta_link": f"{_B}/delta?token=LEGACY"}))
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) is None        # canonical checkpoint absent

    seen = {}
    pages = {f"{_B}/drives/{_DRIVE_DELTA}/root/delta": {
        "value": [_changed("base1", _DRIVE_DELTA)], "@odata.deltaLink": f"{_B}/delta?token=CANON1"}}

    def fetch(url, _t):
        seen["u"] = url
        return pages[url]
    res = mi.run_sharepoint_delta_sync(drive_ids=[_DRIVE_DELTA], fetch=fetch, download=_dl_stub(tmp_path),
                                       destination_root=str(tmp_path / "canon"), ocr=False)
    _track()
    # Full canonical baseline ran from /root/delta (NOT the legacy LEGACY link)
    assert seen["u"].endswith("/root/delta") and res["drives"][0]["resumed"] is False
    assert res["imported"] == 1 and res["checkpoints_advanced"] == 1
    # Canonical checkpoint saved; legacy delta_link preserved untouched
    assert mi.load_canonical_delta_link(_DRIVE_DELTA) == f"{_B}/delta?token=CANON1"
    with engine.connect() as c:
        legacy = c.execute(select(t.c.delta_link).where(t.c.microsoft_drive_id == _DRIVE_DELTA)).scalar()
    assert legacy == f"{_B}/delta?token=LEGACY"                     # legacy job's checkpoint intact

    # Diagnostics show the two checkpoints SEPARATELY.
    d = mi.sharepoint_delta_diagnostics(drive_ids=[_DRIVE_DELTA])[0]
    assert d["source_sync_checkpoint"] is True and d["canonical_checkpoint"] is True
