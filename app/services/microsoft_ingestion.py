"""Continuous ingestion — Microsoft/SharePoint sync runner (Phase C of the change-detection milestone).

A thin, repeatable orchestration over the EXISTING pieces — no second extraction or matching engine:

    stage (connector, incremental delta/state)  ->  import_sharepoint_items (canonical dedupe + change
    detection: NEW ingests, CHANGED re-ingests a new canonical version, UNCHANGED is skipped without
    re-hash/OCR, DELETED marks the source reference unavailable and NEVER deletes the canonical document)
    ->  OCR-on-need analysis for ONLY the new documents  ->  durable run record (audit) for admin visibility.

The staging step (live Microsoft Graph enumeration + download) is the DEPLOYMENT connector
``app.connectors.microsoft365.sharepoint_content`` (environment-specific; not a tracked module — it is
staged per deployment). It is injected here as ``stager`` (or pre-staged ``items``) so this runner is
testable and reuses whatever real connector the deployment ships. ``resolve_sharepoint_stager`` discovers
the connector's real public staging entrypoint at call time rather than hard-importing a specific name, so
a deployment whose connector exposes a different function does not break at import. Nothing here creates a
client, assigns ambiguous ownership, or re-OCRs unchanged documents. Failures are isolated: a bad staged
item is recorded and skipped by the importer, and a whole-run failure is recorded, not raised.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import audit_events, engine
from app.security.audit import write_audit_event

INGESTION_RUN_ACTION = "ingestion.sync_run"
# Recommended production cadence (documented; scheduling is enabled separately, after manual validation).
RECOMMENDED_CADENCE = {"SharePoint": "every 5-10 minutes", "Outlook": "every 5-10 minutes",
                       "Drake": "every 10-15 minutes"}


def _ocr_analyze(document_id):
    """Run the EXISTING pipeline with OCR-on-need for one NEW document (native first; OCR only if native
    text is insufficient), persisting the proposal. Guarded — never raises, never assigns ownership."""
    from app.services.document_pipeline import analyze_and_persist
    try:
        with engine.begin() as conn:
            return analyze_and_persist(document_id, conn=conn, ocr=True) is not None
    except Exception:  # noqa: BLE001
        return False


def _record_run(source, summary, *, started, actor_user_id=None, trigger_source="manual", error=None):
    status = "error" if error else summary.get("status", "completed")
    write_audit_event(
        action=INGESTION_RUN_ACTION, entity_type="ingestion_source", entity_id=source,
        actor_user_id=actor_user_id, request_id=f"ingestion-{uuid.uuid4()}", outcome=("denied" if error else "success"),
        metadata={"source": source, "status": status,
                  "scanned": summary.get("items_examined", 0), "new": summary.get("canonical_created", 0),
                  "reused": summary.get("reused_canonical", 0), "changed": summary.get("metadata_updated", 0),
                  "unchanged": summary.get("skipped", 0), "deleted": summary.get("deleted", 0),
                  "missing": summary.get("missing", 0), "errors": len(summary.get("errors", [])),
                  "ocr_analyzed": summary.get("ocr_analyzed", 0),
                  "started_at": started.isoformat(), "finished_at": datetime.now(UTC).isoformat(),
                  "trigger_source": trigger_source, "error": error})


def run_sharepoint_sync(*, items=None, stager=None, destination_root=None, actor_user_id=None,
                        request_id=None, trigger_source="manual", dry_run=False, ocr=True):
    """Repeatable, incremental SharePoint sync. Provide pre-staged ``items`` (a manifest list) OR a
    ``stager`` callable (the deployment connector's incremental Graph enumeration). Returns the importer
    summary enriched with ``ocr_analyzed``; always records a durable run. Never raises on a run failure."""
    from app.importers.sharepoint import import_sharepoint_items
    started = datetime.now(UTC)
    try:
        staged = list(items) if items is not None else (list(stager()) if stager else [])
    except Exception as exc:  # noqa: BLE001 — staging/connector failure is a recorded run, not a crash
        summary = {"status": "error", "errors": [f"staging: {exc}"]}
        _record_run("SharePoint", summary, started=started, actor_user_id=actor_user_id,
                    trigger_source=trigger_source, error=str(exc))
        return summary
    try:
        summary = import_sharepoint_items(staged, destination_root=destination_root,
                                          actor_user_id=actor_user_id, request_id=request_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        _record_run("SharePoint", {"status": "error"}, started=started, actor_user_id=actor_user_id,
                    trigger_source=trigger_source, error=str(exc))
        return {"status": "error", "errors": [str(exc)]}

    ocr_analyzed = 0
    if ocr and not dry_run:
        for did in summary.get("affected_document_ids", []):    # NEW documents only; unchanged never here
            if _ocr_analyze(did):
                ocr_analyzed += 1
    summary["ocr_analyzed"] = ocr_analyzed
    _record_run("SharePoint", summary, started=started, actor_user_id=actor_user_id,
                trigger_source=trigger_source)
    return summary


# Public SharePoint staging entrypoints tried, in priority order, on the deployment connector. The runner
# adapts to whatever the connector exposes rather than hard-importing one name (the cause of the earlier
# production ImportError). The connector's designed decoupled contract is: connector CLI -> manifest ->
# importer, so a manifest is always the reliable fallback.
_SHAREPOINT_STAGER_NAMES = ("stage_sharepoint_content", "stage_content", "stage", "run_sync", "sync", "run")
_DEFAULT_SYNC_LIMIT = 100000


_ITEM_LIST_KEYS = ("items", "manifest", "manifest_items", "staged", "staged_items", "documents",
                   "results", "entries", "files")


def _stager_items(result):
    import json
    from pathlib import Path
    if isinstance(result, tuple) and result and isinstance(result[0], list):
        return result[0]                                   # (manifest_items, run_record)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in _ITEM_LIST_KEYS:
            v = result.get(key)
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
        mp = result.get("manifest_path") or result.get("manifest")
    else:
        items = getattr(result, "items", None)
        if isinstance(items, list):
            return items
        mp = getattr(result, "manifest_path", None)
    if isinstance(mp, str) and Path(mp).exists():
        return json.loads(Path(mp).read_text())
    return []


def _staging_root():
    import os
    return (os.getenv("CLIENT360_SHAREPOINT_SOURCE_ROOT")
            or os.getenv("CLIENT360_SHAREPOINT_DOCUMENT_ROOT")
            or r"C:\Client360\Data\Documents\SharePoint\_staging")


def _connector_staging_root(mod):
    """The staging root to hand the connector's ``run(root=...)`` for a REAL sync. Prefer the connector's
    OWN configured storage root so downloaded temp files (``.part`` under its content root) and their final
    destination live on the SAME Windows volume — a cross-volume ``os.replace`` raises WinError 17. Order:
    the connector's declared default (which already resolves ``CLIENT360_SHAREPOINT_STAGING_ROOT`` on the
    connector's volume, e.g. D:\\Client360\\Content\\SharePoint), then that env var, then the adapter's
    existing staging root. No new path is invented — this reuses the connector's own configuration."""
    import os
    for cand in (getattr(mod, "DEFAULT_STAGING_ROOT", None), getattr(mod, "STAGING_ROOT", None),
                 getattr(mod, "CONTENT_ROOT", None), os.getenv("CLIENT360_SHAREPOINT_STAGING_ROOT")):
        if cand:
            return str(cand)
    return _staging_root()


def _is_cross_volume_error(exc):
    """True if an OSError is a cross-device/volume move refusal (Windows WinError 17 / POSIX EXDEV)."""
    import errno
    return isinstance(exc, OSError) and (getattr(exc, "winerror", None) == 17 or exc.errno == errno.EXDEV)


def _copy_across_volume(src, dst):
    """Copy ``src`` -> ``dst`` across a volume boundary: stream + fsync + verify SHA-256, then remove
    ``src`` ONLY after the copy verifies. Uses no rename/replace, so it is safe to call from inside a
    replace/rename patch without re-entering it. Returns the destination Path."""
    import hashlib
    import os
    import shutil
    from pathlib import Path
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    def _sha(p):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    src_sha = _sha(src)
    with open(src, "rb") as s, open(dst, "wb") as d:
        shutil.copyfileobj(s, d, 1 << 20)
        d.flush()
        os.fsync(d.fileno())
    if _sha(dst) != src_sha:
        raise OSError(f"cross-volume finalize verification failed for {dst}")
    os.unlink(str(src))                                 # temp removed only after a verified copy
    return dst


def safe_finalize(tmp, dest):
    """Move a staged temp file to ``dest`` tolerantly across Windows volumes. Same volume -> atomic
    ``os.replace``. Cross-volume (WinError 17 / EXDEV) -> copy + verify + unlink. Returns the dest Path."""
    import os
    from pathlib import Path
    tmp, dest = Path(tmp), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(str(tmp), str(dest))                 # atomic within one filesystem
        return dest
    except OSError as exc:
        if not _is_cross_volume_error(exc):
            raise
    return _copy_across_volume(tmp, dest)


@contextmanager
def _cross_volume_safe_moves():
    """Wrap the connector's file finalization so a cross-volume move — its downloaded temp on one disk
    (e.g. D:\\Client360\\Content\\SharePoint\\.tmp) and the destination on another (e.g. C:\\...\\_staging)
    — transparently falls back to copy+verify+unlink instead of raising WinError 17. The connector is
    untracked/per-deployment and finalizes with ``Path.replace`` / ``os.replace`` / ``os.rename`` (all of
    which refuse cross-volume moves), so we patch that exact call family for the duration of the connector
    download call ONLY, then restore. This is the tracked wrapper around the real, untracked finalize op."""
    import os
    from pathlib import Path
    orig = {"os_replace": os.replace, "os_rename": os.rename,
            "p_replace": Path.replace, "p_rename": Path.rename}

    def _os_safe(base):
        def _fn(src, dst, *a, **k):
            try:
                return base(src, dst, *a, **k)
            except OSError as exc:
                if not _is_cross_volume_error(exc):
                    raise
                _copy_across_volume(src, dst)
        return _fn

    def _path_safe(base):
        def _fn(self, target):
            try:
                return base(self, target)
            except OSError as exc:
                if not _is_cross_volume_error(exc):
                    raise
                return _copy_across_volume(self, target)
        return _fn

    os.replace = _os_safe(orig["os_replace"])
    os.rename = _os_safe(orig["os_rename"])
    Path.replace = _path_safe(orig["p_replace"])
    Path.rename = _path_safe(orig["p_rename"])
    try:
        yield
    finally:
        os.replace, os.rename = orig["os_replace"], orig["os_rename"]
        Path.replace, Path.rename = orig["p_replace"], orig["p_rename"]


def _discovered_drive_ids():
    """Drive ids the platform has already discovered (populated by the existing microsoft_document_sync
    job) — the existing configuration source for which drives to stage. No new setting invented."""
    from app.db import metadata
    t = metadata.tables.get("microsoft_drives")
    if t is None:
        return []
    with engine.connect() as conn:
        return [d for d in conn.execute(select(t.c.microsoft_drive_id)).scalars() if d]


def _connector_values(*, root, drive_id, dry_run, limit=None, top=None, progress=None):
    """Values for the connector's known parameter names, sourced from EXISTING SharePoint configuration
    (staging root env, discovered drive, site-id env) and the run mode. For a DRY RUN we keep
    ``download`` disabled (no file downloads) but ALSO set every standard enumerate-without-download flag
    the connector might declare (``dry_run``/``plan_only``/``enumerate_only``/``metadata_only``/
    ``no_download``/``list_only``). ``limit`` (cap items) and ``top`` (the initial delta ``$top`` page size,
    for a small first page) and a ``progress`` callback are supplied when the connector declares them.
    ``_build_kwargs`` passes ONLY the parameters the connector actually declares, so unknown values are
    simply ignored (never overriding a connector default with None)."""
    import os
    from pathlib import Path
    # The deployment connector declares run(root: Path, manifest: Path) and its append_manifest() calls
    # manifest.parent — so root/manifest MUST be pathlib.Path, not str. Dry-run never wrote a manifest, so
    # this only surfaced on the first REAL download. Diagnostics/JSON stringify these at print time.
    root = Path(root)
    manifest = root / (f"{drive_id}_manifest.json" if drive_id else "manifest.json")
    site_ids = [s.strip() for s in (os.getenv("MICROSOFT_SHAREPOINT_SITE_IDS") or "").split(",") if s.strip()]
    if limit is None:
        try:
            limit = int(os.getenv("CLIENT360_SHAREPOINT_SYNC_LIMIT", str(_DEFAULT_SYNC_LIMIT)))
        except ValueError:
            limit = _DEFAULT_SYNC_LIMIT
    values = {"drive_id": drive_id, "root": root, "staging_root": root,
              "manifest": manifest, "manifest_path": manifest,
              "download": not dry_run, "limit": int(limit),
              "site_ids": site_ids, "site_id": (site_ids[0] if site_ids else None)}
    for flag in ("dry_run", "plan_only", "enumerate_only", "metadata_only", "no_download", "list_only"):
        values[flag] = dry_run
    if top is not None:                                    # small first page for the initial /root/delta
        for k in ("top", "page_size", "page_top", "first_page_size", "delta_page_size"):
            values[k] = int(top)
    if progress is not None:                               # per-page progress if the connector supports it
        for k in ("progress", "on_page", "page_callback"):
            values[k] = progress
    return values


def _build_kwargs(fn, values):
    """Supply exactly the parameters the connector function declares, from `values`. Any REQUIRED
    parameter (no default) we cannot source raises a clear, actionable error naming it."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    kwargs, missing = {}, []
    for name, p in params.items():
        if name in values:
            kwargs[name] = values[name]
        elif p.default is inspect.Parameter.empty and p.kind in (
                inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            missing.append(name)
    if missing:
        raise RuntimeError(
            f"The SharePoint connector entrypoint requires argument(s) {missing} that continuous ingestion "
            "cannot source from existing configuration. Set the corresponding SharePoint configuration "
            "(e.g. MICROSOFT_SHAREPOINT_SITE_IDS, CLIENT360_SHAREPOINT_SOURCE_ROOT), or stage via the "
            "connector's own CLI and pass its manifest with --manifest <path>.")
    return kwargs


def _read_manifest_file(path):
    """Read the manifest the connector was told to write (the designed connector->importer handoff)."""
    import json
    from pathlib import Path
    if not path or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (ValueError, OSError):
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return None


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def initial_delta_url(drive_id, *, top=None, base_url=GRAPH_BASE_URL):
    """The Microsoft Graph driveItem delta URL for a drive; ``$top`` requests a small FIRST page so the
    initial /root/delta returns quickly instead of appearing to hang on a large drive."""
    url = f"{base_url}/drives/{drive_id}/root/delta"
    return f"{url}?$top={int(top)}" if top else url


def iter_delta_pages(drive_id, fetch, *, top=None, base_url=GRAPH_BASE_URL, timeout=120, progress=None,
                     max_pages=None):
    """Yield each page's driveItems using Microsoft Graph driveItem DELTA semantics — GET
    /drives/{id}/root/delta (optionally ``$top`` for a small first page), then follow ``@odata.nextLink``.
    ``fetch(url, timeout)`` performs the authenticated GET and returns the JSON (injected so this is
    testable and reuses the connector's auth). Prints/records progress BEFORE each request and AFTER each
    response (page number, initial/nextLink, elapsed, item count) and surfaces a slow/failed page as a
    CLEAR error instead of hanging. Read-only: enumerates metadata only, no downloads. This is the delta
    contract — not a replacement crawler and no recursive traversal (delta already walks the hierarchy)."""
    import time
    url = initial_delta_url(drive_id, top=top, base_url=base_url)
    page = 0
    while url:
        page += 1
        kind = "initial" if page == 1 else "nextLink"
        if progress:
            progress({"phase": "request", "page": page, "kind": kind})
        t0 = time.monotonic()
        try:
            data = fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001 — surface, don't hang
            raise RuntimeError(f"Graph delta page {page} ({kind}) failed after "
                               f"{time.monotonic() - t0:.1f}s: {exc}") from exc
        elapsed = time.monotonic() - t0
        items = data.get("value", []) if isinstance(data, dict) else []
        if progress:
            progress({"phase": "response", "page": page, "kind": kind,
                      "elapsed": round(elapsed, 2), "items": len(items)})
        yield items
        url = data.get("@odata.nextLink") if isinstance(data, dict) else None
        if max_pages and page >= max_pages:
            break


def _call_with_timeout(fn, kwargs, timeout):
    """Call the connector with a wall-clock timeout so a slow initial /root/delta page surfaces a clear
    error instead of appearing hung. Without a timeout, calls straight through."""
    if not timeout:
        return fn(**kwargs)
    import threading
    box = {}

    def _worker():
        try:
            box["result"] = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(
            f"SharePoint connector call exceeded {timeout}s (the initial /root/delta page can be slow on a "
            "large drive) — request a smaller first page with --top and/or cap with --limit.")
    if "error" in box:
        raise box["error"]
    return box.get("result")


# Connector per-drive delta ENUMERATORS (yield driveItems / metadata) — used for a dry run so metadata is
# staged WITHOUT downloading content, when the connector's run(download=False) returns only a summary.
_ENUMERATOR_NAMES = ("iter_drive_items", "iter_items", "enumerate_drive_items", "delta_items",
                     "list_drive_items", "walk_drive")


def _driveitem_to_record(drive_id, it):
    """Convert a Microsoft Graph driveItem into the base metadata record import_sharepoint_items needs —
    NO content, clearly marked dry-run. Deleted items carry the deleted marker for reconciliation."""
    pr = it.get("parentReference") or {}
    rec = {"drive_id": drive_id, "item_id": it.get("id"), "name": it.get("name"),
           "parent_path": pr.get("path"), "folder_path": pr.get("path"),
           "web_url": it.get("webUrl"), "target": it.get("webUrl"),
           "size": it.get("size"), "size_bytes": it.get("size"),
           "modified_at": it.get("lastModifiedDateTime"),
           "site": pr.get("siteId"), "library": pr.get("driveId") or drive_id,
           "status": "dry_run_metadata", "dry_run": True}
    if "deleted" in it:
        rec["deleted"] = True
    return rec


def _connector_session(mod):
    """Build an authenticated Graph session EXACTLY as the connector's own run() does — no generic auth
    abstraction, just the three functions this deployment's connector already exposes:

        account = latest_account()
        token   = get_microsoft_access_token(account)
        session = graph_session(token)

    Returns the authenticated ``requests.Session`` (what ``iter_drive_items(session, drive_id)`` needs),
    or None if the connector does not expose this exact auth path."""
    latest = getattr(mod, "latest_account", None)
    get_token = getattr(mod, "get_microsoft_access_token", None)
    make_session = getattr(mod, "graph_session", None)
    if not (callable(latest) and callable(get_token) and callable(make_session)):
        return None
    account = latest()
    token = get_token(account)
    return make_session(token)


def _enumerate_metadata(mod, fn, drive_id, *, top, limit, timeout, diag, progress):
    """Enumerate FILE metadata for a drive via the connector's delta iterator (no download). Yields the
    base metadata records import_sharepoint_items consumes. Files only (folders skipped)."""
    import time
    values = {"drive_id": drive_id, "limit": limit, "download": False, "dry_run": True}
    # The connector's iter_drive_items(session, drive_id) needs an authenticated session; build it the
    # same way run() does. _build_kwargs passes it only to an enumerator that declares `session`.
    session = _connector_session(mod)
    if session is not None:
        values["session"] = session
    if top is not None:
        for k in ("top", "page_size", "first_page_size"):
            values[k] = int(top)
    if progress is not None:
        for k in ("progress", "on_page"):
            values[k] = progress
        progress({"phase": "drive_request", "drive_id": drive_id, "enumerator": fn.__name__})

    def _collect(**kw):
        # STREAM the delta iterator and STOP as soon as `limit` FILE records are collected — exactly as
        # the connector's own run() does. Never materialize the full feed (list(fn(...)) would drain the
        # entire ~55k-item delta and hit the wall-clock timeout). The generator is consumed lazily; the
        # break stops it, so item limit+1 (and beyond) is never pulled. `limit` counts FILE records, so
        # folders/non-files passed over do not consume the budget.
        records = []
        for it in fn(**kw):
            if not isinstance(it, dict) or "folder" in it:        # files only; skip folders
                continue
            records.append(_driveitem_to_record(drive_id, it))
            if limit and len(records) >= int(limit):
                break
        return records

    t0 = time.monotonic()
    records = _call_with_timeout(_collect, _build_kwargs(fn, values), timeout)
    elapsed = round(time.monotonic() - t0, 2)
    if progress is not None:
        progress({"phase": "drive_response", "drive_id": drive_id, "elapsed": elapsed, "items": len(records)})
    if diag is not None:
        diag["drives"].append({"drive_id": drive_id, "download": False, "limit": limit, "top": top,
                               "elapsed_seconds": elapsed, "items": len(records),
                               "source": "delta_enumeration", "result_type": "iter",
                               "enumerator": fn.__name__})
    return records


def _stage_one(fn, root, drive_id, dry_run, diag, *, limit=None, top=None, timeout=None, progress=None):
    import time
    values = _connector_values(root=root, drive_id=drive_id, dry_run=dry_run, limit=limit, top=top,
                               progress=progress)
    if progress:
        progress({"phase": "drive_request", "drive_id": drive_id, "top": top, "limit": values["limit"]})
    t0 = time.monotonic()
    # Real download/finalize path: wrap the connector call so its cross-volume Path.replace/os.replace
    # (temp on one disk, dest on another) falls back to copy+verify+unlink instead of raising WinError 17.
    with _cross_volume_safe_moves():
        result = _call_with_timeout(fn, _build_kwargs(fn, values), timeout)
    elapsed = round(time.monotonic() - t0, 2)
    if progress:
        progress({"phase": "drive_response", "drive_id": drive_id, "elapsed": elapsed})
    manifest_path = values["manifest"]
    # Prefer the manifest FILE the connector was told to write (deterministic), else parse the return.
    from_file = _read_manifest_file(manifest_path)
    if from_file is not None:
        items, source = from_file, "manifest_file"
    else:
        items, source = _stager_items(result), "run_return"
    # Failure semantics: a connector that SAW files but downloaded none (all failed) must not look like a
    # successful empty run. Surface it as a staging error so the run status is error, not "completed".
    if isinstance(result, dict):
        files_seen = result.get("files_seen") or result.get("seen") or 0
        downloaded = result.get("downloaded") or result.get("files_downloaded") or 0
        failed = result.get("failed") or result.get("files_failed") or 0
        if files_seen and not downloaded and not items:
            raise RuntimeError(
                f"connector downloaded 0 of {files_seen} files for drive {drive_id} "
                f"(failed={failed}); no items staged — treating the run as failed, not completed.")
    if diag is not None:
        from pathlib import Path
        result_keys = sorted(result.keys()) if isinstance(result, dict) else None
        result_counts = ({k: v for k, v in result.items() if isinstance(v, int)}
                         if isinstance(result, dict) else None)
        diag["drives"].append({
            "drive_id": drive_id, "download": values["download"], "limit": values["limit"],
            "top": top, "elapsed_seconds": elapsed,
            "manifest": str(manifest_path),   # str for JSON/printing; the connector got a Path
            "manifest_exists": bool(manifest_path and Path(manifest_path).exists()),
            "result_type": type(result).__name__, "result_keys": result_keys,
            "result_counts": result_counts, "items": len(items), "source": source})
    return items


def resolve_sharepoint_stager(*, site_ids=None, drive_ids=None, dry_run=False, module=None, diag=None,
                              limit=None, top=None, timeout=None, progress=None):
    """Return a zero-arg ``stager`` callable that invokes the REAL deployment connector's SharePoint
    staging entrypoint and yields the manifest items. It does NOT hard-import a specific function name:
    it discovers the connector's public staging callable and supplies exactly the arguments that callable
    declares, sourced from EXISTING SharePoint configuration (staging-root env, discovered drives, site-id
    env; download=not dry_run). Per-drive connectors (``run(*, drive_id, ...)``) run once per discovered
    drive; items come from the manifest FILE the connector writes (else its return). When ``diag`` is a
    dict it is filled with SAFE staging diagnostics (drive count/ids, staging root, per-drive manifest path
    + item counts) — no secrets/tokens. ``module``/``drive_ids`` are test hooks."""
    def _do():
        mod = module
        if mod is None:
            from app.connectors.microsoft365 import (
                sharepoint_content as mod,  # lazy: no import-time dep
            )
        fn = next((getattr(mod, n) for n in _SHAREPOINT_STAGER_NAMES
                   if callable(getattr(mod, n, None))), None)
        if fn is None:
            public = sorted(n for n in dir(mod)
                            if not n.startswith("_") and callable(getattr(mod, n, None)))
            raise RuntimeError(
                "No known SharePoint staging entrypoint in "
                f"app.connectors.microsoft365.sharepoint_content (tried {list(_SHAREPOINT_STAGER_NAMES)}). "
                f"Available public callables: {public}. Stage via the connector's own CLI "
                "(python -m app.connectors.microsoft365.sharepoint_content --manifest <path>) and pass that "
                "manifest to the sync with --manifest <path>.")
        import inspect
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        # REAL sync stages on the connector's OWN volume (temp .part + final dest same disk -> no WinError
        # 17). Dry-run enumerates only (no downloads/manifest writes), so its root is unchanged.
        root = _staging_root() if dry_run else _connector_staging_root(mod)
        needs_drive = "drive_id" in params and params["drive_id"].default is inspect.Parameter.empty
        ids = ((list(drive_ids) if drive_ids is not None else _discovered_drive_ids())
               if needs_drive else [None])
        # Dry-run metadata enumerator (the connector's own delta iterator) — used to stage metadata
        # WITHOUT downloading when run(download=False) returns only a summary.
        enum_fn = next((getattr(mod, n) for n in _ENUMERATOR_NAMES
                        if callable(getattr(mod, n, None))), None) if dry_run else None
        if diag is not None:
            diag.update({"entrypoint": fn.__name__, "entrypoint_params": list(params),
                         "staging_root": root, "needs_drive": needs_drive,
                         "enumerator": enum_fn.__name__ if enum_fn else None,
                         "connector_callables": sorted(n for n in dir(mod)
                                                       if not n.startswith("_") and callable(getattr(mod, n, None))),
                         "drive_count": len([d for d in ids if d is not None]),
                         "drive_ids": [d for d in ids if d is not None][:50], "drives": []})
        if needs_drive and not ids:
            raise RuntimeError(
                "The SharePoint connector requires a drive_id but no drives are configured/discovered. "
                "Set MICROSOFT_SHAREPOINT_SITE_IDS and run the existing document sync to discover drives "
                "(they are stored in microsoft_drives), or stage via the connector CLI and use --manifest.")
        out = []
        for d in ids:
            if enum_fn is not None:
                # Dry-run: stage metadata via the connector's delta iterator (no download).
                try:
                    out += _enumerate_metadata(mod, enum_fn, d, top=top, limit=limit, timeout=timeout,
                                               diag=diag, progress=progress)
                    continue
                except Exception as exc:  # noqa: BLE001 — fall back to run(); never crash the run
                    if diag is not None:
                        diag.setdefault("enum_errors", []).append(f"{d}: {exc}")
            out += _stage_one(fn, root, d, dry_run, diag, limit=limit, top=top, timeout=timeout,
                              progress=progress)
        if diag is not None:
            diag["total_items"] = len(out)
        return out
    return _do


def sharepoint_staging_diagnostics():
    """READ-ONLY config/data diagnostics for the staging layer (no Microsoft Graph call, no downloads):
    staging root, discovered-drive count/ids, site-id scope, and the count of existing SharePoint source
    references already in the corpus. Explains 'why did staging return zero items?' without live I/O."""
    import os

    from app.db import metadata
    ds = metadata.tables.get("document_sources")
    existing_refs = 0
    if ds is not None:
        with engine.connect() as conn:
            existing_refs = conn.execute(select(func.count()).select_from(ds)
                                         .where(ds.c.source_system == "SharePoint")).scalar()
    drive_ids = _discovered_drive_ids()
    connector_callables, entrypoint, entrypoint_params = None, None, None
    _mod = None
    real_staging_root, connector_temp_root = _staging_root(), None
    try:
        from app.connectors.microsoft365 import sharepoint_content as _mod
        connector_callables = sorted(n for n in dir(_mod)
                                     if not n.startswith("_") and callable(getattr(_mod, n, None)))
        fn = next((getattr(_mod, n) for n in _SHAREPOINT_STAGER_NAMES
                   if callable(getattr(_mod, n, None))), None)
        if fn is not None:
            import inspect
            entrypoint = fn.__name__
            entrypoint_params = list(inspect.signature(fn).parameters)
        # Resolve exactly what a REAL run would use (same runtime function ingestion uses), and probe the
        # connector's temp/content root if it exposes one (production exposes none -> the cross-volume-safe
        # finalize handles it; this is why the exact temp volume no longer has to match).
        real_staging_root = _connector_staging_root(_mod)
        for attr in ("TEMP_ROOT", "TMP_ROOT", "_TMP_DIR", "TMP_DIR", "CONTENT_ROOT", "DEFAULT_STAGING_ROOT",
                     "STAGING_ROOT"):
            v = getattr(_mod, attr, None)
            if v:
                connector_temp_root = f"{attr}={v}"
                break
        else:
            connector_temp_root = "not exposed by connector (cross-volume-safe finalize handles any volume)"
    except Exception as exc:  # noqa: BLE001 — connector import is environment-specific
        connector_callables = f"connector import failed: {exc}"
    return {
        "staging_root": _staging_root(),
        "real_staging_root": real_staging_root,              # what a REAL run hands the connector (root=)
        "connector_temp_root": connector_temp_root,          # where the connector downloads .part (if exposed)
        "cross_volume_safe_finalize": True,                  # WinError 17 handled via copy+verify+unlink
        "connector_callables": connector_callables,
        "entrypoint": entrypoint, "entrypoint_params": entrypoint_params,
        "CLIENT360_SHAREPOINT_SOURCE_ROOT_set": bool(os.getenv("CLIENT360_SHAREPOINT_SOURCE_ROOT")),
        "CLIENT360_SHAREPOINT_DOCUMENT_ROOT_set": bool(os.getenv("CLIENT360_SHAREPOINT_DOCUMENT_ROOT")),
        "MICROSOFT_SHAREPOINT_SITE_IDS": [s.strip() for s in
                                          (os.getenv("MICROSOFT_SHAREPOINT_SITE_IDS") or "").split(",") if s.strip()],
        "discovered_drive_count": len(drive_ids),
        "discovered_drive_ids": drive_ids[:50],
        "existing_sharepoint_source_refs": existing_refs,
    }


def ingestion_status(sources=("SharePoint", "Drake", "Outlook"), *, scan=500):
    """Latest run per source (for the admin status page): source, last_run, last_success, and the counts.
    READ-ONLY over the append-only audit run records."""
    latest, last_success = {}, {}
    with engine.connect() as conn:
        rows = conn.execute(select(audit_events.c.entity_id, audit_events.c.metadata,
                                   audit_events.c.occurred_at)
                            .where(audit_events.c.action == INGESTION_RUN_ACTION)
                            .order_by(audit_events.c.id.desc()).limit(scan)).mappings()
        for r in rows:
            md = r["metadata"] or {}
            src = r["entity_id"] or md.get("source")
            if not src:
                continue
            if src not in latest:
                latest[src] = {"source": src, "last_run": r["occurred_at"],
                               "status": md.get("status"), "scanned": md.get("scanned", 0),
                               "new": md.get("new", 0), "changed": md.get("changed", 0),
                               "unchanged": md.get("unchanged", 0), "deleted": md.get("deleted", 0),
                               "errors": md.get("errors", 0), "ocr_analyzed": md.get("ocr_analyzed", 0)}
            if md.get("status") in ("completed", "completed_with_errors") and src not in last_success:
                last_success[src] = r["occurred_at"]
    out = []
    for src in sources:
        row = latest.get(src, {"source": src, "last_run": None, "status": "never run"})
        row["last_success"] = last_success.get(src)
        row["cadence"] = RECOMMENDED_CADENCE.get(src)
        out.append(row)
    # include any other sources that have run but aren't in the default list
    for src, row in latest.items():
        if src not in sources:
            row["last_success"] = last_success.get(src)
            row["cadence"] = RECOMMENDED_CADENCE.get(src)
            out.append(row)
    return out
