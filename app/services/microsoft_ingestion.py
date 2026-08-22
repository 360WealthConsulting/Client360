"""Continuous ingestion — Microsoft/SharePoint sync runner (Phase C of the change-detection milestone).

A thin, repeatable orchestration over the EXISTING pieces — no second extraction or matching engine:

    stage (connector, incremental delta/state)  ->  import_sharepoint_items (canonical dedupe + change
    detection: NEW ingests, CHANGED re-ingests a new canonical version, UNCHANGED is skipped without
    re-hash/OCR, DELETED marks the source reference unavailable and NEVER deletes the canonical document)
    ->  OCR-on-need analysis for ONLY the new documents  ->  durable run record (audit) for admin visibility.

The staging step (live Microsoft Graph enumeration + download) is the connector
``app.connectors.microsoft365.sharepoint_content`` (version-controlled and tested, but deployment-
CONFIGURED — site IDs + staging root come from env, never hard-coded). It is injected here as ``stager``
(or pre-staged ``items``) so this runner is
testable and reuses whatever real connector the deployment ships. ``resolve_sharepoint_stager`` discovers
the connector's real public staging entrypoint at call time rather than hard-importing a specific name, so
a deployment whose connector exposes a different function does not break at import. Nothing here creates a
client, assigns ambiguous ownership, or re-OCRs unchanged documents. Failures are isolated: a bad staged
item is recorded and skipped by the importer, and a whole-run failure is recorded, not raised.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import audit_events, engine
from app.security.audit import write_audit_event

log = logging.getLogger("client360.microsoft_ingestion")

INGESTION_RUN_ACTION = "ingestion.sync_run"
# Recommended production cadence (documented; scheduling is enabled separately, after manual validation).
RECOMMENDED_CADENCE = {"SharePoint": "every 5-10 minutes", "Outlook": "every 5-10 minutes",
                       "Drake": "every 10-15 minutes"}


def _ocr_status_for(document_id):
    """The persisted OCR state for a document (completed / timed_out / failed / unsupported / pending),
    or 'none' when no OCR was needed/attempted (e.g. native text extraction)."""
    from app.db import metadata
    t = metadata.tables.get("document_ocr")
    if t is None:
        return "none"
    with engine.connect() as conn:
        st = conn.execute(select(t.c.status).where(t.c.document_id == document_id)).scalar()
    return st or "none"


def _doc_name(document_id):
    from app.db import documents
    with engine.connect() as conn:
        return conn.execute(select(documents.c.original_name)
                            .where(documents.c.id == document_id)).scalar()


def _ocr_analyze(document_id):
    """Run the EXISTING pipeline with OCR-on-need for one document (native first; OCR only if native text
    is insufficient), persisting the proposal. Guarded — never raises, never hangs (OCR is bounded per
    page/document), never assigns ownership. Returns the resulting OCR status string."""
    from app.services.document_ocr import finalize_document_ocr_state
    from app.services.document_pipeline import analyze_and_persist
    try:
        with engine.begin() as conn:
            analyze_and_persist(document_id, conn=conn, ocr=True)
    except Exception:  # noqa: BLE001 — analysis/OCR failure must never break the batch
        return "error"
    # Native-text / non-OCR documents never pass through run_ocr; give them a terminal, truthful OCR
    # state so a fully-processed document is not left 'pending' forever.
    finalize_document_ocr_state(document_id)
    return _ocr_status_for(document_id)


# OCR status -> run-summary counter bucket.
_OCR_BUCKET = {"completed": "ocr_analyzed", "timed_out": "ocr_timed_out",
               "failed": "ocr_failed", "error": "ocr_failed"}

# OCR status -> OcrRunTracker outcome bucket (native/cached success == completed; anything else that still
# advanced but isn't a fresh OCR result == skipped).
_TRACKER_OUTCOME = {"completed": "completed", "timed_out": "timed_out", "failed": "failed",
                    "error": "failed", "unsupported": "unsupported"}


def _tracker_outcome(status):
    return _TRACKER_OUTCOME.get(status, "skipped")


def _safe(fn, *args, **kwargs):
    """Run an observational status-tracker call; a tracking failure must NEVER change baseline behavior."""
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        pass


def _new_ocr_tracker(total):
    """A persistent :class:`OcrRunTracker` scoped to EXACTLY this baseline/resume batch (``total`` is the
    real ``len(document_ids)`` passed to :func:`_ocr_documents`, never a firm-wide count). Reuses the
    runner's status config (``OCR_STATUS_ENABLED``), run-id helper, and status format — no second format.
    Observational only: any failure returns None so tracking never alters the OCR loop."""
    try:
        from app.jobs.ocr_runner import _default_run_id, _status_enabled
        if not _status_enabled(None):
            return None
        from app.jobs.ocr_status import OcrRunTracker
        return OcrRunTracker(run_id=_default_run_id("sharepoint-baseline"),
                             mode="sharepoint-baseline", total=total)
    except Exception:  # noqa: BLE001
        return None


class _HeartbeatObserver:
    """Forwards ONLY heartbeats to the tracker. The baseline loop drives on_document_start/on_document_result
    itself (so cached/native documents advance too), so run_ocr's observer must not also count documents —
    it exists purely to keep the heartbeat alive DURING a long isolated OCR document."""
    __slots__ = ("_tracker",)

    def __init__(self, tracker):
        self._tracker = tracker

    def heartbeat(self):
        self._tracker.heartbeat()


def _install_ocr_heartbeat(tracker):
    """Publish a heartbeat observer on the live-OCR context var so _live_ocr → run_ocr → subprocess
    isolation routes its heartbeats to this tracker (reusing the existing isolation heartbeat callback).
    Returns a reset token, or None."""
    if tracker is None:
        return None
    try:
        from app.services.document_ocr import live_ocr_observer
        return live_ocr_observer.set(_HeartbeatObserver(tracker))
    except Exception:  # noqa: BLE001
        return None


def _remove_ocr_heartbeat(token):
    if token is None:
        return
    try:
        from app.services.document_ocr import live_ocr_observer
        live_ocr_observer.reset(token)
    except Exception:  # noqa: BLE001
        pass


def _finish_tracker(tracker, *, ok, error=None):
    if tracker is None:
        return
    try:
        from app.jobs.ocr_status import COMPLETED, FAILED
        tracker.finish(COMPLETED if ok else FAILED, error=error)
    except Exception:  # noqa: BLE001
        pass


def _ocr_documents(document_ids, *, progress=None):
    """OCR/analyze a list of documents with per-document isolation, bounded time, and progress. One
    timed-out/failed document is counted and the loop continues. Cache-aware: a document with usable OCR
    text already persisted is not re-OCR'd. Returns counters (ocr_analyzed/ocr_failed/ocr_timed_out/
    ocr_other).

    Persists a scoped operational status/heartbeat artifact for THIS batch (total == len(document_ids))
    via the shared OcrRunTracker, so a resumed baseline is observable (working vs. frozen). Tracking is
    purely observational — it never changes queue construction, scope, cache behavior, or the OCR result."""
    import time
    counts = {"ocr_analyzed": 0, "ocr_failed": 0, "ocr_timed_out": 0, "ocr_other": 0}
    total = len(document_ids)
    tracker = _new_ocr_tracker(total)                 # None if tracking disabled/unavailable
    token = _install_ocr_heartbeat(tracker)           # route live-OCR heartbeats to this tracker
    if tracker is not None:
        _safe(tracker.start)
        _safe(tracker.mark_running)
    try:
        for idx, did in enumerate(document_ids, 1):
            name = _doc_name(did)
            if tracker is not None:
                _safe(tracker.on_document_start, did, name)
            t0 = time.monotonic()
            status = _ocr_analyze(did)
            elapsed = round(time.monotonic() - t0, 1)
            counts[_OCR_BUCKET.get(status, "ocr_other")] += 1
            if tracker is not None:
                _safe(tracker.on_document_result, did, name, _tracker_outcome(status))
            if progress:
                progress({"phase": "ocr", "index": idx, "total": total, "document_id": did,
                          "file": name, "status": status, "elapsed": elapsed})
    except BaseException as exc:                       # a crash in the loop → record FAILED, then propagate
        _finish_tracker(tracker, ok=False, error=exc)
        raise
    finally:
        _remove_ocr_heartbeat(token)
    _finish_tracker(tracker, ok=True)
    return counts


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
                        request_id=None, trigger_source="manual", dry_run=False, ocr=True,
                        authoritative=False, ocr_progress=None):
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
                                          actor_user_id=actor_user_id, request_id=request_id,
                                          dry_run=dry_run, authoritative=authoritative)
    except Exception as exc:  # noqa: BLE001
        _record_run("SharePoint", {"status": "error"}, started=started, actor_user_id=actor_user_id,
                    trigger_source=trigger_source, error=str(exc))
        return {"status": "error", "errors": [str(exc)]}

    summary["ocr_analyzed"] = summary["ocr_failed"] = summary["ocr_timed_out"] = 0
    if ocr and not dry_run:
        counts = _ocr_documents(summary.get("affected_document_ids", []), progress=ocr_progress)
        summary["ocr_analyzed"], summary["ocr_failed"], summary["ocr_timed_out"] = (
            counts["ocr_analyzed"], counts["ocr_failed"], counts["ocr_timed_out"])
        # OCR errors are isolated: the batch completes, but a timeout/failure is surfaced in the status.
        if summary["ocr_failed"] or summary["ocr_timed_out"]:
            summary.setdefault("errors", []).append(
                f"OCR: {summary['ocr_timed_out']} timed out, {summary['ocr_failed']} failed "
                f"(documents imported; OCR can be resumed with --resume-ocr)")
            if summary.get("status") == "completed":
                summary["status"] = "completed_with_errors"
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
    # The overloaded CLIENT360_SHAREPOINT_SOURCE_ROOT/DOCUMENT_ROOT still win (compat); else derive from
    # CLIENT360_DATA_ROOT; else the legacy _staging default. Unchanged when no base/override is set.
    from app.services.storage_paths import sharepoint_staging_root
    return sharepoint_staging_root()


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
    job) — the existing configuration source for which drives to stage. No new setting invented.

    Read-side filter: a known system/cache library (e.g. an already-persisted PersonalCacheLibrary row) is
    NOT selected for a canonical baseline. The existing row is never deleted or mutated — it is simply not
    returned here."""
    from app.db import metadata
    from app.jobs.microsoft_document_sync import is_system_library
    t = metadata.tables.get("microsoft_drives")
    if t is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.microsoft_drive_id, t.c.name)).all()
    return [d for d, name in rows if d and not is_system_library(name)]


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


# A manifest record is "failed" (never importable) if it explicitly says so; anything else — including a
# record with no status field (the classic array format) — is treated as a successful staged item.
_MANIFEST_FAILED_STATUSES = {"failed", "failure", "error", "download_failed", "downloadfailed",
                             "errored", "skipped_error", "incomplete"}


def _record_failed(r):
    if not isinstance(r, dict):
        return True
    status = str(r.get("status") or r.get("result") or r.get("outcome") or r.get("state") or "").lower()
    return (status in _MANIFEST_FAILED_STATUSES or r.get("failed") is True
            or r.get("ok") is False or r.get("success") is False or bool(r.get("error")))


def _manifest_identity(r):
    """Stable SharePoint identity for dedup: drive_id+item_id, else the web URL, else name+size."""
    did = r.get("drive_id") or r.get("driveId")
    iid = r.get("item_id") or r.get("itemId") or r.get("id")
    if did and iid:
        return ("di", str(did), str(iid))
    uri = r.get("web_url") or r.get("webUrl") or r.get("source_uri") or r.get("uri")
    if uri:
        return ("uri", str(uri))
    return ("ns", str(r.get("name")), str(r.get("size")))


def _normalize_staged_record(r):
    """Set ``local_path`` from the connector's actual download path field (``target``/...) when it isn't
    already a valid local file — so the importer can find the downloaded file. Other fields are preserved.
    Also mirror ``size_bytes`` -> ``size`` for the importer's change-detection. Never fabricates a path."""
    from app.importers.sharepoint import resolved_staged_path
    out = dict(r)
    staged = resolved_staged_path(out)
    if staged:
        out["local_path"] = staged
    if out.get("size") is None and out.get("size_bytes") is not None:
        out["size"] = out["size_bytes"]
    return out


def _usable_staged_records(records):
    """From raw manifest records keep the importable ones: drop explicitly-failed records, dedup by stable
    SharePoint identity so append-only/retried manifests don't import the same file twice (latest success
    per identity wins, first-seen order), and normalize each record's staged local_path (``target`` ->
    ``local_path`` when it is an existing file)."""
    by_key, order = {}, []
    for r in (records or []):
        if _record_failed(r):
            continue
        key = _manifest_identity(r)
        if key not in by_key:
            order.append(key)
        by_key[key] = r                                    # last successful record wins
    return [_normalize_staged_record(by_key[k]) for k in order]


def _parse_manifest_records(text):
    """Parse a connector manifest tolerant of the real on-disk shapes: a JSON array, an object wrapping a
    list (items/manifest/...), JSONL (one object per line, append-only), or concatenated JSON objects.
    Returns a list of record dicts (possibly empty), or None if nothing parseable was found."""
    import json
    text = (text or "").strip()
    if not text:
        return []
    try:                                                   # 1) whole-file JSON (array or object)
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in _ITEM_LIST_KEYS:
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
        vals = [v for v in data.values() if isinstance(v, dict)]
        return vals if vals else [data]
    records = []                                           # 2) JSONL / one object per line (append-only)
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
        elif isinstance(obj, list):
            records.extend(r for r in obj if isinstance(r, dict))
    if records:
        return records
    dec = json.JSONDecoder()                               # 3) concatenated objects, no separators
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = dec.raw_decode(text, idx)
        except ValueError:
            break
        if isinstance(obj, dict):
            records.append(obj)
        elif isinstance(obj, list):
            records.extend(r for r in obj if isinstance(r, dict))
        idx = end
    return records or None


def _read_manifest_file(path):
    """Read + parse the connector manifest (the designed connector->importer handoff), tolerant of array /
    object / JSONL / concatenated formats, returning the USABLE staged records (failed dropped, deduped)."""
    from pathlib import Path
    if not path or not Path(path).exists():
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    records = _parse_manifest_records(text)
    if records is None:
        return None
    return _usable_staged_records(records)


def load_manifest_items(path):
    """Public: usable staged items from a manifest file (for reconciling an already-staged/downloaded run
    WITHOUT re-downloading). Empty list if the file is absent/unparseable."""
    return _read_manifest_file(path) or []


def analyze_manifest(path):
    """READ-ONLY manifest diagnostics — no Graph, no downloads, no imports: record count, successful vs
    failed records, unique SharePoint item ids, duplicate records, and the parsed staged-item count."""
    from collections import Counter
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace")
    records = _parse_manifest_records(text) or []
    ok = [r for r in records if not _record_failed(r)]
    failed = [r for r in records if _record_failed(r)]
    ids = [str(r.get("item_id") or r.get("itemId") or r.get("id") or "") for r in records]
    dup_counts = Counter(_manifest_identity(r) for r in ok)
    duplicates = sum(c - 1 for c in dup_counts.values() if c > 1)
    fmt = ("json_array/object" if text.strip()[:1] in ("[", "{")
           and _parse_is_whole_json(text) else "jsonl_or_concatenated")
    return {
        "exists": True, "path": str(p), "bytes": len(text), "format": fmt,
        "record_count": len(records),
        "successful_records": len(ok),
        "failed_records": len(failed),
        "unique_item_ids": len({i for i in ids if i}),
        "duplicate_records": duplicates,
        "parsed_staged_items": len(_usable_staged_records(records)),
    }


def _parse_is_whole_json(text):
    import json
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def manifest_path_records(path, *, limit=200):
    """READ-ONLY per-record path fields for a manifest (no contents, no Graph, no import): name, status,
    target, local_path, file_exists (does a real staged file resolve?), drive_id, item_id. For confirming
    that successful records point at existing downloaded files before reconciling."""
    from pathlib import Path

    from app.importers.sharepoint import resolved_staged_path
    p = Path(path)
    if not p.exists():
        return []
    records = _parse_manifest_records(p.read_text(encoding="utf-8", errors="replace")) or []
    rows = []
    for r in records[:limit]:
        rows.append({
            "name": r.get("name"),
            "status": r.get("status") or r.get("result") or r.get("outcome"),
            "failed": _record_failed(r),
            "target": r.get("target") or r.get("dest") or r.get("destination") or r.get("downloaded_path"),
            "local_path": r.get("local_path"),
            "file_exists": bool(resolved_staged_path(r)),
            "drive_id": r.get("drive_id") or r.get("driveId"),
            "item_id": r.get("item_id") or r.get("itemId") or r.get("id"),
        })
    return rows


def _document_ids_for_items(items):
    """Map staged items to their ALREADY-IMPORTED canonical document ids (via the SharePoint source ref),
    de-duplicated, preserving order. Items not yet imported are simply absent (no Graph, no download)."""
    from app.db import metadata
    from app.importers.sharepoint import _item_uri
    ds = metadata.tables.get("document_sources")
    if ds is None:
        return []
    ids, seen = [], set()
    with engine.connect() as conn:
        for it in items:
            did = conn.execute(
                select(ds.c.document_id).where(ds.c.source_system == "SharePoint",
                                               ds.c.source_uri == _item_uri(it))
                .order_by(ds.c.id.desc())).scalar()
            if did and did not in seen:
                seen.add(did)
                ids.append(did)
    return ids


def resume_ocr_for_items(items, *, ocr_progress=None):
    """Resume OCR/analysis on the ALREADY-IMPORTED documents for these staged items — NO Graph, NO
    download. Cache-aware (documents with usable OCR text are skipped), bounded per page/document, and
    per-document isolated. Returns OCR counters plus the number of matched documents."""
    dids = _document_ids_for_items(items)
    counts = _ocr_documents(dids, progress=ocr_progress)
    counts["ocr_documents"] = len(dids)
    return counts


def manifest_ocr_status(manifest_path):
    """READ-ONLY status for a manifest (no Graph, no downloads, no imports): how many items are already
    imported (have a SharePoint source ref) vs not, and the OCR state of the imported ones
    (completed / pending / failed / timed_out / unsupported)."""
    from pathlib import Path

    from app.db import metadata
    from app.importers.sharepoint import _item_uri
    p = Path(manifest_path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    items = _usable_staged_records(
        _parse_manifest_records(p.read_text(encoding="utf-8", errors="replace")) or [])
    ds = metadata.tables.get("document_sources")
    doc_ocr = metadata.tables.get("document_ocr")
    out = {"exists": True, "path": str(p), "manifest_items": len(items), "unique_documents": 0,
           "imported": 0, "not_imported": 0, "ocr_completed": 0, "ocr_pending": 0, "ocr_failed": 0,
           "ocr_timed_out": 0, "ocr_unsupported": 0}
    seen = set()
    with engine.connect() as conn:
        for it in items:
            did = (conn.execute(select(ds.c.document_id).where(
                ds.c.source_system == "SharePoint", ds.c.source_uri == _item_uri(it))
                .order_by(ds.c.id.desc())).scalar() if ds is not None else None)
            if not did:
                out["not_imported"] += 1
                continue
            out["imported"] += 1                             # per-item (references)
            if did in seen:
                continue                                     # OCR state counted once per unique document
            seen.add(did)
            st = (conn.execute(select(doc_ocr.c.status).where(doc_ocr.c.document_id == did)).scalar()
                  if doc_ocr is not None else None)
            key = {"completed": "ocr_completed", "timed_out": "ocr_timed_out", "failed": "ocr_failed",
                   "unsupported": "ocr_unsupported"}.get(st, "ocr_pending")
            out[key] += 1
    out["unique_documents"] = len(seen)                       # OCR buckets sum to this, matching the detail
    return out


def repair_ocr_source_paths(manifest_path, *, destination_root=None, dry_run=False):
    """Repair the OCR-source linkage for already-imported documents whose canonical row has NO resolvable
    local file (e.g. a reused/migrated document with empty/stale storage) by backfilling a local copy from
    the manifest's still-present STAGED file. NO Graph, NO download. Safe: content-verified, only fills a
    MISSING file, never overwrites/changes canonical content. ``dry_run`` reports what would be repaired."""
    from app.db import documents
    from app.importers.sharepoint import (
        _has_resolvable_file,
        backfill_local_source,
        resolved_staged_path,
    )
    items = load_manifest_items(manifest_path)
    out = {"checked": 0, "missing_source": 0, "repaired": 0, "no_staged_file": 0, "details": []}
    for it in items:
        staged = resolved_staged_path(it)
        dids = _document_ids_for_items([it])
        did = dids[0] if dids else None
        if did is None:
            continue
        out["checked"] += 1
        with engine.connect() as conn:
            row = conn.execute(select(documents.c.storage_uri, documents.c.storage_path)
                               .where(documents.c.id == did)).mappings().first()
        if _has_resolvable_file(row):
            continue
        out["missing_source"] += 1
        if not staged:
            out["no_staged_file"] += 1
            out["details"].append({"document_id": did, "action": "no_staged_file",
                                   "web_url": it.get("web_url")})
            continue
        if dry_run:
            out["details"].append({"document_id": did, "action": "would_backfill", "staged": staged})
            continue
        if backfill_local_source(did, staged, destination_root=destination_root):
            out["repaired"] += 1
            out["details"].append({"document_id": did, "action": "backfilled", "staged": staged})
    return out


def manifest_ocr_problems(manifest_path):
    """READ-ONLY: the imported documents for a manifest that are in a failed/timed_out OCR state — their
    document_id, filename, status, attempts, and last_error (NO contents). Identifies exactly which docs
    need attention."""
    from pathlib import Path

    from app.db import documents, metadata
    from app.importers.sharepoint import _item_uri
    p = Path(manifest_path)
    if not p.exists():
        return []
    items = _usable_staged_records(
        _parse_manifest_records(p.read_text(encoding="utf-8", errors="replace")) or [])
    ds = metadata.tables.get("document_sources")
    doc_ocr = metadata.tables.get("document_ocr")
    if ds is None or doc_ocr is None:
        return []
    rows, seen = [], set()
    with engine.connect() as conn:
        for it in items:
            did = conn.execute(select(ds.c.document_id).where(
                ds.c.source_system == "SharePoint", ds.c.source_uri == _item_uri(it))
                .order_by(ds.c.id.desc())).scalar()
            if not did or did in seen:
                continue
            seen.add(did)
            r = conn.execute(select(doc_ocr.c.status, doc_ocr.c.attempts, doc_ocr.c.last_error,
                                    documents.c.original_name)
                             .select_from(doc_ocr.join(documents, documents.c.id == doc_ocr.c.document_id))
                             .where(doc_ocr.c.document_id == did)).mappings().first()
            if r and r["status"] in ("failed", "timed_out"):
                rows.append({"document_id": did, "name": r["original_name"], "status": r["status"],
                             "attempts": r["attempts"], "last_error": (r["last_error"] or "")[:300]})
    return rows


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


# --- durable per-drive delta checkpoint (@odata.deltaLink) for the CANONICAL SharePoint pipeline --------
# IMPORTANT: microsoft_drives.delta_link is owned by the legacy microsoft_document_sync job. The canonical
# downloader keeps its OWN dedicated checkpoint (canonical_delta_link) so a populated legacy delta_link can
# never make the canonical sync skip its initial baseline. The two checkpoints are fully independent; the
# legacy delta_link is never read or written here.

def load_canonical_delta_link(drive_id):
    """The durably-persisted CANONICAL @odata.deltaLink for a drive, or None (=> do the initial baseline).
    Reads canonical_delta_link ONLY — never the legacy microsoft_document_sync delta_link."""
    from app.db import metadata
    t = metadata.tables.get("microsoft_drives")
    if t is None or "canonical_delta_link" not in t.c:
        return None
    with engine.connect() as conn:
        return conn.execute(select(t.c.canonical_delta_link)
                            .where(t.c.microsoft_drive_id == str(drive_id))).scalar()


def save_canonical_delta_link(drive_id, delta_link, *, source_type="sharepoint"):
    """Persist the CANONICAL @odata.deltaLink (+ canonical_delta_synced_at). Upserts microsoft_drives so a
    not-yet-discovered drive is created; on an existing row it updates ONLY the canonical columns — the
    legacy delta_link / last_synced_at (owned by microsoft_document_sync) are left untouched."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db import metadata
    t = metadata.tables.get("microsoft_drives")
    if t is None or not delta_link or "canonical_delta_link" not in t.c:
        return
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(pg_insert(t).values(
            microsoft_drive_id=str(drive_id), source_type=source_type,
            canonical_delta_link=delta_link, canonical_delta_synced_at=now).on_conflict_do_update(
            index_elements=[t.c.microsoft_drive_id],
            set_={"canonical_delta_link": delta_link, "canonical_delta_synced_at": now,
                  "updated_at": now}))          # never touches delta_link / last_synced_at (legacy job's)


def _graph_fetch_from_session(session):
    """Build a ``fetch(url, timeout) -> json`` from the connector's authenticated Graph session (reuses the
    connector's own auth; no new auth path). A _ManagedGraphSession transparently re-authenticates on 401."""
    def fetch(url, timeout):
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    return fetch


class _GraphDownloadError(RuntimeError):
    """A download failure carrying the HTTP status + Graph error code so the runner can log it and decide
    hold-vs-manual-review. ``permanent`` marks a genuinely-unavailable item (404/410) — recorded for manual
    review, NOT a checkpoint-holding failure."""

    def __init__(self, message, *, status=None, graph_code=None, permanent=False):
        super().__init__(message)
        self.status = status
        self.graph_code = graph_code
        self.permanent = permanent


def _graph_error(resp):
    """Extract (code, message) from a Graph error body — never returns tokens/secrets."""
    try:
        err = (resp.json() or {}).get("error") or {}
        return err.get("code"), (err.get("message") or "")[:300]
    except Exception:  # noqa: BLE001 — non-JSON error body
        return None, ""


class _ManagedGraphSession:
    """Wraps the connector's authenticated Graph session and transparently RE-AUTHENTICATES once on a 401
    (access-token expiry) using the connector's own auth path (latest_account -> get_microsoft_access_token
    -> graph_session). This is what lets a multi-hour baseline survive token expiration WITHOUT blind
    retries — a single targeted re-auth per 401, then the request proceeds."""

    def __init__(self, mod):
        self._mod = mod
        self._session = _connector_session(mod)

    def rebuild(self):
        self._session = _connector_session(self._mod)

    def get(self, url, *, _reauth=True, **kw):
        resp = self._session.get(url, **kw)
        if getattr(resp, "status_code", None) == 401 and _reauth:
            log.warning("Microsoft Graph returned 401 (access token expired) — re-authenticating and retrying")
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            self.rebuild()
            resp = self._session.get(url, **kw)
        return resp


def sharepoint_delta_changes(drive_id, fetch, *, resume=True, persist=True, base_url=GRAPH_BASE_URL,
                             timeout=120, progress=None, max_pages=None):
    """Delta-CHECKPOINTED change feed for a drive (Microsoft Graph driveItem delta semantics).

    Resumes from the durably-persisted @odata.deltaLink when present (``resume``), else initial
    /root/delta; follows @odata.nextLink; captures the NEW @odata.deltaLink and (``persist``) stores it via
    save_canonical_delta_link. Classifies driveItems into ``changed`` (new/modified files — feed to the
    canonical pipeline) and ``deleted`` (tombstones — mark the source reference unavailable; NEVER delete a
    canonical document). Read-only w.r.t. content (no downloads). ``fetch(url, timeout)`` performs the
    authenticated GET (injected — testable, reuses the connector session). Returns
    ``{changed, deleted, delta_link, resumed, pages}``."""
    stored = load_canonical_delta_link(drive_id) if resume else None
    url = stored or initial_delta_url(drive_id, base_url=base_url)
    changed, deleted, delta_link, page = [], [], None, 0
    while url:
        page += 1
        if progress:
            progress({"phase": "request", "page": page, "resumed": bool(stored)})
        data = fetch(url, timeout)
        if not isinstance(data, dict):
            data = {}
        for it in data.get("value", []):
            if not isinstance(it, dict):
                continue
            if "deleted" in it:                              # tombstone (file or folder)
                deleted.append(it)
            elif "folder" in it and "file" not in it:
                continue                                     # a live folder is not a document
            else:
                changed.append(it)                           # new/modified file
        if progress:
            progress({"phase": "response", "page": page,
                      "changed": len(changed), "deleted": len(deleted)})
        url = data.get("@odata.nextLink")
        delta_link = data.get("@odata.deltaLink") or delta_link
        if max_pages and page >= max_pages:
            break
    if persist and delta_link:
        save_canonical_delta_link(drive_id, delta_link)
    return {"changed": changed, "deleted": deleted, "delta_link": delta_link,
            "resumed": bool(stored), "pages": page}


def sharepoint_delta_diagnostics(drive_ids=None):
    """READ-ONLY proof of BOTH per-drive checkpoints, separately (no Graph call):
      * source_sync_checkpoint — the legacy microsoft_document_sync @odata.deltaLink (delta_link),
      * canonical_checkpoint — the canonical SharePoint downloader's own @odata.deltaLink.
    A canonical sync resumes ONLY from its own checkpoint; a legacy checkpoint never affects it."""
    from app.db import metadata
    t = metadata.tables.get("microsoft_drives")
    rows = []
    if t is None:
        return rows
    has_canon = "canonical_delta_link" in t.c
    cols = [t.c.microsoft_drive_id, t.c.delta_link, t.c.last_synced_at]
    if has_canon:
        cols += [t.c.canonical_delta_link, t.c.canonical_delta_synced_at]
    with engine.connect() as conn:
        q = select(*cols)
        if drive_ids:
            q = q.where(t.c.microsoft_drive_id.in_([str(d) for d in drive_ids]))
        for r in conn.execute(q.order_by(t.c.microsoft_drive_id)).mappings():
            rows.append({
                "drive_id": r["microsoft_drive_id"],
                "source_sync_checkpoint": bool(r["delta_link"]),          # legacy microsoft_document_sync
                "source_sync_last_synced_at": (r["last_synced_at"].isoformat()
                                               if r["last_synced_at"] else None),
                "canonical_checkpoint": bool(r.get("canonical_delta_link")) if has_canon else False,
                "canonical_last_synced_at": (r["canonical_delta_synced_at"].isoformat()
                                             if has_canon and r.get("canonical_delta_synced_at") else None),
            })
    return rows


def _source_uri_for_external_id(item_id):
    """The stored SharePoint source_uri for an item id (source_external_id) — so a delta tombstone (which
    may carry no webUrl) resolves to the existing source reference and marks IT unavailable."""
    from app.db import metadata
    ds = metadata.tables.get("document_sources")
    if ds is None or not item_id:
        return None
    with engine.connect() as conn:
        return conn.execute(select(ds.c.source_uri).where(
            ds.c.source_system == "SharePoint", ds.c.source_external_id == str(item_id))
            .order_by(ds.c.id.desc())).scalar()


def _delta_item_record(drive_id, it, local_path):
    """A real staged item record (with local_path) for import_sharepoint_items — from a delta driveItem."""
    pr = it.get("parentReference") or {}
    return {"drive_id": drive_id, "item_id": it.get("id"), "name": it.get("name"),
            "web_url": it.get("webUrl"), "parent_path": pr.get("path"),
            "folder_path": pr.get("path"), "size": it.get("size"),
            "modified_at": it.get("lastModifiedDateTime"), "created_at": it.get("createdDateTime"),
            "site": pr.get("siteId"), "library": pr.get("driveId") or drive_id,
            "content_type": (it.get("file") or {}).get("mimeType"), "local_path": local_path}


def _delta_deletion_record(drive_id, it):
    """A deletion record for import_sharepoint_items — marks the SharePoint source reference unavailable
    (never deletes the canonical document). Resolves the stored source_uri by item id when the tombstone
    carries no webUrl."""
    item_id = it.get("id")
    pr = it.get("parentReference") or {}
    return {"drive_id": drive_id, "item_id": item_id, "name": it.get("name") or str(item_id),
            "web_url": it.get("webUrl") or _source_uri_for_external_id(item_id),
            "site": pr.get("siteId"), "library": pr.get("driveId") or drive_id, "deleted": True}


_DELTA_MAX_TEMP_PATH = 240


def _bounded_delta_filename(dest_dir, item_id, name):
    """Return a deterministic staged filename whose .part path stays bounded on Windows.

    The SharePoint/original filename remains unchanged in item metadata; this only
    bounds the local temporary/staging filename used by the delta downloader.
    """
    from pathlib import Path

    dest_dir = Path(dest_dir)
    item_id = str(item_id)
    name = str(name)
    prefix = f"{item_id}__"

    candidate = prefix + name
    temp_path = dest_dir / (candidate + ".part")
    if len(str(temp_path)) <= _DELTA_MAX_TEMP_PATH:
        return candidate

    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name

    fixed_len = len(str(dest_dir / (prefix + suffix + ".part")))
    available = max(1, _DELTA_MAX_TEMP_PATH - fixed_len)
    stem = stem[:available]

    return prefix + stem + suffix

def _transient_backoff_seconds(attempt, *, base=1, cap=30):
    """Exponential backoff (seconds) for a transient download retry, capped. ``attempt`` is 1-based."""
    return min(cap, base * (2 ** (max(1, attempt) - 1)))


# Bounded per-request timeouts for the delta content download. A single float is only a per-recv INACTIVITY
# timeout, so a fully idle/stalled socket read can block indefinitely — the ~4h production hang was a silent
# SSL read with no forward bound. An explicit (connect, read) TUPLE makes urllib3 arm a read-inactivity
# deadline on every recv (the header read in getresponse AND each streamed chunk), so an inactive socket
# raises ReadTimeout promptly and enters the existing transient-retry/backoff path.
_DOWNLOAD_CONNECT_TIMEOUT = 30                              # seconds to establish TCP + TLS
_DOWNLOAD_READ_TIMEOUT = 120                                # max seconds of socket INACTIVITY per recv
_DOWNLOAD_TIMEOUT = (_DOWNLOAD_CONNECT_TIMEOUT, _DOWNLOAD_READ_TIMEOUT)
# Cooperative, SAME-THREAD total-elapsed backstop for the STREAMING (body) phase only — checked between
# chunks, never via a background/daemon thread, so the calling thread always stays the sole owner of the
# .part/destination file (no staging race). Generous, to bound a slow-trickle body without killing a
# legitimate large download that keeps making steady progress.
_DOWNLOAD_MAX_STREAM_SECONDS = 1800
# Design B — response-HEADER acquisition bound. A per-recv read timeout cannot bound a slow-drip/idle header
# read (getresponse is one uninterruptible blocking call; any dribbled byte resets the inactivity timer). So
# the blocking session.get up to headers runs in a bounded DAEMON worker with a TOTAL wall-clock deadline;
# the worker performs ONLY session.get and NEVER touches the .part/destination file, DB, or checkpoints.
_DOWNLOAD_HEADER_DEADLINE = 120            # total wall-clock seconds allowed to obtain response headers
_MAX_STUCK_HEADER_WORKERS = 4              # hard cap on OUTSTANDING header workers (invariant: live workers
#                                           named "sp-delta-header" is always <= this; bounds any leak)


class _HeaderTimeout(Exception):
    """Bounded header acquisition exceeded its wall-clock deadline (or the header-worker pool is saturated).
    A slow-drip/idle response-HEADER read cannot be bounded by a per-recv read timeout, so this total
    deadline is what terminates it. Always treated as a TRANSIENT (non-permanent) download failure."""


_HEADER_SEMAPHORE = None


def _header_semaphore():
    """Process-wide BoundedSemaphore capping how many header-acquisition workers can be outstanding at once
    (a stuck worker holds its slot). This is the hard bound that prevents an unbounded thread leak across a
    large baseline. Lazily created."""
    global _HEADER_SEMAPHORE
    if _HEADER_SEMAPHORE is None:
        import threading
        _HEADER_SEMAPHORE = threading.BoundedSemaphore(_MAX_STUCK_HEADER_WORKERS)
    return _HEADER_SEMAPHORE


def _acquire_response(session, url, *, deadline, timeout, semaphore):
    """Obtain the HTTP response (headers only) in a bounded DAEMON worker that performs ONLY ``session.get``.

    Strict ownership: the worker never iterates the body, never opens/writes the ``.part`` file, never
    touches the destination or database — the calling (main) thread remains the sole owner of all staged-file
    and persistence work. Returns the response, or raises :class:`_HeaderTimeout` when headers do not arrive
    within ``deadline`` (the TOTAL wall-clock bound a per-recv read timeout cannot provide) or when the
    bounded worker pool is saturated. A worker's own connect/read error propagates unchanged (the caller
    handles it as transient).

    Lifecycle / no leak: a slot is held for the worker's whole lifetime and released only when it finishes,
    so at most ``_MAX_STUCK_HEADER_WORKERS`` workers can ever be outstanding. On deadline the main thread
    abandons the worker and hands off ownership so the worker closes the response when it finally returns
    (connection cleanup only — never a file). Workers are daemon threads, so a permanently stuck worker never
    blocks process exit."""
    import threading

    if not semaphore.acquire(blocking=False):
        raise _HeaderTimeout(f"header-worker pool saturated ({_MAX_STUCK_HEADER_WORKERS} outstanding)")

    state = {"resp": None, "exc": None, "owner": "worker"}   # 'worker' until handed to 'main'/'abandoned'
    lock = threading.Lock()
    done = threading.Event()

    def _work():
        resp = exc = None
        try:
            resp = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
        except BaseException as e:  # noqa: BLE001 — capture; main re-raises or we clean up
            exc = e
        with lock:
            state["resp"], state["exc"] = resp, exc
            abandoned = state["owner"] == "abandoned"
            if not abandoned:
                state["owner"] = "main"
        done.set()
        semaphore.release()
        if abandoned and resp is not None:              # main already gave up -> release the connection here
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_work, name="sp-delta-header", daemon=True).start()
    if not done.wait(timeout=deadline):
        with lock:
            if state["owner"] == "worker":              # still running -> abandon; the worker closes the resp
                state["owner"] = "abandoned"
                raise _HeaderTimeout(f"no response headers within {deadline}s")
            # else: the worker finished within the race window -> fall through and consume its result
    if state["exc"] is not None:
        raise state["exc"]
    return state["resp"]


def _download_delta_item(session, drive_id, it, staging_dir, *, max_throttle_waits=6,
                         max_transient_retries=5, sleep=None, header_deadline=None, header_semaphore=None):
    """Download ONE changed driveItem's content to a staged file (same-dir temp -> atomic replace).

    Robust against the bulk-download failure seen in the baseline:
      * RESUME: an already-downloaded staged file is reused ONLY when Graph declares a size AND the on-disk
        size matches it exactly. When the size is unknown we never blindly trust an existing staged file.
      * Always uses the AUTHENTICATED /drives/{id}/items/{id}/content endpoint (a fresh 302 redirect per
        request) — NEVER the pre-authorized @microsoft.graph.downloadUrl captured at enumeration, which
        expires ~1h later and caused every later download to fail at once.
      * Token expiry is handled by the injected _ManagedGraphSession (re-auth on 401).
      * 429 throttling honors Retry-After (bounded) instead of blindly hammering.
      * BOUNDED I/O: response-HEADER acquisition runs in a bounded daemon worker with a TOTAL wall-clock
        deadline (_DOWNLOAD_HEADER_DEADLINE) — this is what bounds a slow-drip/idle header read that a
        per-recv read timeout cannot. The worker performs ONLY session.get; this thread owns all file/DB
        work. The (connect, read) tuple (_DOWNLOAD_TIMEOUT) stays as socket-level defense in depth, plus a
        cooperative same-thread total-elapsed backstop over the streamed body (no thread owns the file).
      * TRANSIENT failures (HTTP 5xx, connect/read timeouts, connection errors, an interrupted stream, or a
        short/truncated response) are retried with bounded exponential backoff; when the budget is exhausted
        they raise a NON-permanent _GraphDownloadError so the caller HOLDS the checkpoint (change re-delivered).
      * 404/410 => a PERMANENT _GraphDownloadError (genuinely unavailable -> manual review, not a hold).
      * A short/truncated response is NEVER promoted to the final staged file (byte count verified against
        the Graph-declared size before the atomic replace).
    Returns the staged local path. Never logs tokens/secrets."""
    import os
    import time
    from pathlib import Path

    import requests

    from app.importers.taxdome_drive import sanitize_relative_path
    sleep = sleep or time.sleep
    header_deadline = header_deadline or _DOWNLOAD_HEADER_DEADLINE
    header_semaphore = header_semaphore or _header_semaphore()
    item_id = str(it.get("id"))
    name = sanitize_relative_path(it.get("name") or item_id).name
    dest_dir = Path(staging_dir) / "_delta"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _bounded_delta_filename(dest_dir, item_id, name)
    declared = it.get("size")
    # RESUME (M2): only reuse a staged file when the size is KNOWN and matches exactly. An unknown declared
    # size means we cannot verify the staged bytes, so we re-download rather than trust a possibly
    # partial/stale file.
    if declared is not None and dest.exists() and dest.stat().st_size == int(declared):
        return str(dest)
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"   # fresh, authenticated, never stale
    tmp = dest.with_suffix(dest.suffix + ".part")

    def _discard_partial():
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    throttle_waits = 0                                     # 429 Retry-After budget (unchanged behavior)
    transient_tries = 0                                    # 5xx / connection / read / short-read budget
    while True:
        try:
            # Header acquisition runs in a bounded worker with a TOTAL wall-clock deadline (Design B); the
            # (connect, read) tuple stays as socket-level defense in depth. The worker only performs
            # session.get — this thread owns every subsequent .part/destination/DB operation.
            resp = _acquire_response(session, url, deadline=header_deadline,
                                     timeout=_DOWNLOAD_TIMEOUT, semaphore=header_semaphore)
        except _HeaderTimeout as exc:                     # total header wall-clock deadline -> transient (HOLD)
            transient_tries += 1
            if transient_tries > max_transient_retries:
                raise _GraphDownloadError(
                    f"header acquisition exceeded {header_deadline}s after {transient_tries} attempt(s): {exc}",
                    status=None, graph_code="headerTimeout") from exc
            sleep(_transient_backoff_seconds(transient_tries))
            continue
        except requests.RequestException as exc:          # worker connect/read timeout or conn error -> transient
            transient_tries += 1
            if transient_tries > max_transient_retries:
                raise _GraphDownloadError(f"connection error after {transient_tries} attempt(s): {exc}",
                                          status=None, graph_code="connectionError") from exc
            sleep(_transient_backoff_seconds(transient_tries))
            continue
        status = getattr(resp, "status_code", 0)
        if status == 429:                                 # throttled -> honor Retry-After (bounded)
            retry_after = _retry_after_seconds(resp)
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            throttle_waits += 1
            if throttle_waits > max_throttle_waits:
                raise _GraphDownloadError(f"throttled: {throttle_waits} Retry-After waits exhausted",
                                          status=429, graph_code="tooManyRequests")
            sleep(retry_after)
            continue
        if status in (404, 410):                          # genuinely gone -> manual review (permanent)
            code, msg = _graph_error(resp)
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            raise _GraphDownloadError(f"item unavailable: HTTP {status} {code} {msg}",
                                      status=status, graph_code=code, permanent=True)
        if status >= 500:                                 # transient server error -> bounded retry, else HOLD
            code, msg = _graph_error(resp)
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            transient_tries += 1
            if transient_tries > max_transient_retries:
                raise _GraphDownloadError(
                    f"server error HTTP {status} after {transient_tries} attempt(s): {code} {msg}",
                    status=status, graph_code=code)       # NON-permanent -> checkpoint held
            sleep(_transient_backoff_seconds(transient_tries))
            continue
        if status >= 400:                                 # other 4xx (e.g. 403) -> systematic, NON-permanent
            code, msg = _graph_error(resp)
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            raise _GraphDownloadError(f"download failed: HTTP {status} {code} {msg}",
                                      status=status, graph_code=code)
        # 2xx: stream to a temp file; a mid-stream failure is transient (bounded retry, else HOLD).
        written = 0
        stream_deadline = time.monotonic() + _DOWNLOAD_MAX_STREAM_SECONDS
        try:
            with resp:
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        # Cooperative, same-thread total-elapsed backstop for a slow-trickle body: checked
                        # between chunks (no daemon thread), so this thread stays the sole file owner.
                        if time.monotonic() > stream_deadline:
                            raise requests.exceptions.ReadTimeout(
                                f"download body exceeded {_DOWNLOAD_MAX_STREAM_SECONDS}s total budget")
                        if chunk:
                            fh.write(chunk)
                            written += len(chunk)
        except requests.RequestException as exc:          # interrupted/timed-out stream -> transient
            _discard_partial()
            transient_tries += 1
            if transient_tries > max_transient_retries:
                raise _GraphDownloadError(f"stream interrupted after {transient_tries} attempt(s): {exc}",
                                          status=None, graph_code="streamInterrupted") from exc
            sleep(_transient_backoff_seconds(transient_tries))
            continue
        # Integrity: a short/truncated response must NEVER become the final staged file.
        if declared is not None and written != int(declared):
            _discard_partial()
            transient_tries += 1
            if transient_tries > max_transient_retries:
                raise _GraphDownloadError(
                    f"incomplete download: got {written} of {int(declared)} bytes after "
                    f"{transient_tries} attempt(s)", status=None, graph_code="incompleteDownload")
            sleep(_transient_backoff_seconds(transient_tries))
            continue
        os.replace(str(tmp), str(dest))                   # same directory -> same volume (atomic)
        return str(dest)


def _retry_after_seconds(resp, *, default=5, cap=60):
    try:
        return min(int(resp.headers.get("Retry-After", default) or default), cap)
    except (TypeError, ValueError):
        return default


def _staged_size(it, local_path):
    """Bytes for progress accounting: the driveItem's declared size, else the staged file size."""
    sz = it.get("size")
    if sz is not None:
        return int(sz)
    try:
        import os
        return os.path.getsize(local_path)
    except OSError:
        return 0


class _DeltaProgress:
    """Throttled progress reporter for --delta-sync: emits a one-line tally at least every ``every`` files
    OR ``interval`` seconds (whichever comes first). Cheap — plain counters + a throttled sink call, so it
    never materially slows ingestion and never touches checkpoint semantics. ``now``/``sink`` are injected
    for testing."""

    def __init__(self, *, every=100, interval=60, sink=None, now=None):
        import time
        self._every, self._interval = every, interval
        self._sink = sink or (lambda line: print(line, flush=True))
        self._now = now or time.monotonic
        self._start = self._now()
        self._last_emit, self._last_count, self._processed = self._start, 0, 0
        self.drive = None
        self.checkpoint = "NOT_ADVANCED (baseline running)"
        self.t = {"enumerated": 0, "changed": 0, "downloaded": 0, "download_failed": 0, "bytes": 0,
                  "reused_existing": 0, "canonical_created": 0, "canonical_reused": 0,
                  "ocr_completed": 0, "ocr_failed": 0, "ocr_unsupported": 0, "ocr_timed_out": 0}

    def set_drive(self, drive_id):
        self.drive, self.checkpoint = drive_id, "NOT_ADVANCED (baseline running)"

    def set_changed(self, n):
        self.t["changed"] = n

    def on_download(self, size_bytes):
        self.t["enumerated"] += 1
        self.t["downloaded"] += 1
        self.t["bytes"] += int(size_bytes or 0)
        self._processed += 1
        self._maybe_emit()

    def on_download_failed(self):
        self.t["enumerated"] += 1
        self.t["download_failed"] += 1
        self._processed += 1
        self._maybe_emit()

    def on_import(self, summary):
        self.t["canonical_created"] += summary.get("canonical_created", 0)
        self.t["canonical_reused"] += summary.get("reused_canonical", 0)
        self.t["reused_existing"] += summary.get("skipped", 0)

    def on_ocr(self, status):
        key = {"completed": "ocr_completed", "failed": "ocr_failed", "error": "ocr_failed",
               "unsupported": "ocr_unsupported", "timed_out": "ocr_timed_out"}.get(status)
        if key:
            self.t[key] += 1
        self._processed += 1
        self._maybe_emit()

    def set_checkpoint(self, state):
        self.checkpoint = state

    def _maybe_emit(self):
        if (self._processed - self._last_count) >= self._every or \
                (self._now() - self._last_emit) >= self._interval:
            self.emit()

    def emit(self):
        self._last_emit, self._last_count = self._now(), self._processed
        t = self.t
        self._sink(
            f"[delta-progress] drive={self.drive} elapsed={self._now() - self._start:.1f}s "
            f"enumerated={t['enumerated']} changed={t['changed']} downloaded={t['downloaded']} "
            f"download_failed={t['download_failed']} reused_existing={t['reused_existing']} "
            f"canonical_created={t['canonical_created']} canonical_reused={t['canonical_reused']} "
            f"ocr_completed={t['ocr_completed']} ocr_failed={t['ocr_failed']} "
            f"ocr_unsupported={t['ocr_unsupported']} ocr_timed_out={t['ocr_timed_out']} "
            f"bytes={t['bytes']} checkpoint={self.checkpoint}")


def run_sharepoint_delta_sync(drive_ids=None, *, session=None, fetch=None, download=None,
                              staging_root=None, destination_root=None, timeout=120, ocr=True,
                              actor_user_id=None, trigger_source="scheduled", progress=None,
                              report=None, report_every=100, report_interval=60, now=None):
    """Delta-CHECKPOINTED recurring canonical SharePoint sync.

    Per drive: resume from the durably-persisted @odata.deltaLink (or initial /root/delta on first sync),
    download ONLY new/changed files, feed changed + deleted items to import_sharepoint_items
    (non-authoritative — deletions mark the source reference unavailable, unseen items are NEVER marked
    missing, canonical documents are never deleted), then run OCR-on-need. Existing dedupe/idempotency is
    unchanged (import resolves by content hash and skips unchanged).

    Downloads use a re-authenticating session (survives token expiry) and the fresh authenticated /content
    endpoint (never a stale pre-authorized URL), honor 429 Retry-After, and RESUME by reusing already-staged
    files — so a large baseline survives multi-hour runs and can be safely re-run.

    Checkpoint safety: the deltaLink is advanced ONLY after the FULL Graph traversal completes AND every
    changed item was downloaded + imported, EXCEPT that genuinely-unavailable items (HTTP 404/410) are
    routed to the manual-review ``exceptions`` queue and do NOT hold the checkpoint. A TRANSIENT/systematic
    download failure (auth/throttle/5xx/connection) or an import failure HOLDS the checkpoint so those
    changes are re-delivered next sync. OCR failures never hold the checkpoint (the document is already
    canonical; OCR retry is separate). Bad documents / OCR failures / unsupported / corrupt scans are
    isolated and preserved as ``exceptions`` for cleanup."""
    from app.importers.sharepoint import import_sharepoint_items
    started = datetime.now(UTC)
    staging = staging_root or _staging_root()
    if fetch is None or download is None:
        if session is None:
            from app.connectors.microsoft365 import sharepoint_content as _mod
            session = _ManagedGraphSession(_mod)          # transparent re-auth on 401 (token expiry)
        fetch = fetch or _graph_fetch_from_session(session)
        download = download or (lambda d, it: _download_delta_item(session, d, it, staging))
    ids = list(drive_ids) if drive_ids is not None else _discovered_drive_ids()
    rep = _DeltaProgress(every=report_every, interval=report_interval, sink=report, now=now)
    out = {"status": "completed", "drives": [], "changed": 0, "deleted": 0, "imported": 0,
           "download_failures": 0, "permanent_failures": 0, "ocr_analyzed": 0, "ocr_failed": 0,
           "ocr_timed_out": 0, "checkpoints_advanced": 0, "exceptions": []}
    for did in ids:
        drv = {"drive_id": did, "advanced": False}
        rep.set_drive(did)
        try:                                              # full delta traversal (persist=False — advance later)
            res = sharepoint_delta_changes(did, fetch, resume=True, persist=False,
                                           timeout=timeout, progress=progress)
        except Exception as exc:  # noqa: BLE001 — traversal incomplete: HOLD the checkpoint
            out["exceptions"].append({"drive_id": did, "phase": "traversal", "error": str(exc)[:500]})
            out["status"] = "completed_with_errors"
            drv["held_reason"] = "delta traversal failed"
            rep.set_checkpoint("HELD (traversal failed)")
            rep.emit()
            out["drives"].append(drv)
            continue
        rep.set_changed(len(res["changed"]))
        records, dl_failures, permanent_failures = [], 0, 0
        for it in res["changed"]:                         # download changed items (isolated per item)
            try:
                lp = download(did, it)
                records.append(_delta_item_record(did, it, lp))
                rep.on_download(_staged_size(it, lp))     # progress: enumerated/downloaded/bytes (throttled)
            except Exception as exc:  # noqa: BLE001 — one bad item is non-blocking; keep going
                rep.on_download_failed()
                status = getattr(exc, "status", None)
                graph_code = getattr(exc, "graph_code", None)
                permanent = bool(getattr(exc, "permanent", False))
                # Rich, secret-free failure log: item id, filename, HTTP status, Graph code/message, type.
                log.warning("delta download failed: drive=%s item=%s name=%r http_status=%s graph_code=%s "
                            "permanent=%s exception=%s: %s", did, it.get("id"), it.get("name"), status,
                            graph_code, permanent, type(exc).__name__, str(exc)[:300])
                out["exceptions"].append({
                    "drive_id": did, "item_id": it.get("id"), "name": it.get("name"), "phase": "download",
                    "http_status": status, "graph_code": graph_code, "permanent": permanent,
                    "exception_type": type(exc).__name__, "error": str(exc)[:500],
                    "review": "manual_review" if permanent else "retry_next_sync"})
                if permanent:
                    permanent_failures += 1               # genuinely gone -> manual review; does NOT hold
                else:
                    dl_failures += 1                      # transient/systematic -> HOLD the checkpoint
        records.extend(_delta_deletion_record(did, it) for it in res["deleted"])
        try:
            summary = import_sharepoint_items(records, destination_root=destination_root,
                                              actor_user_id=actor_user_id, dry_run=False,
                                              authoritative=False)
        except Exception as exc:  # noqa: BLE001 — catastrophic import failure: HOLD the checkpoint
            out["exceptions"].append({"drive_id": did, "phase": "import", "error": str(exc)[:500]})
            out["status"] = "completed_with_errors"
            drv["held_reason"] = "import failed"
            rep.set_checkpoint("HELD (import failed)")
            rep.emit()
            out["drives"].append(drv)
            continue
        rep.on_import(summary)
        rep.emit()                                        # a line around the bulk import boundary
        import_item_failures = len(summary.get("errors", []))
        for e in summary.get("errors", []):
            out["exceptions"].append({"drive_id": did, "phase": "import_item", "error": e})
        out["changed"] += len(res["changed"])
        out["deleted"] += len(res["deleted"])
        out["imported"] += summary.get("canonical_created", 0) + summary.get("reused_canonical", 0)
        if ocr:                                           # OCR failures preserved; do NOT hold the checkpoint
            def _ocr_prog(ev, _rep=rep):
                _rep.on_ocr(ev.get("status"))            # progress: OCR completed/failed/unsupported/timed_out
                if progress:
                    progress(ev)
            # OCR runs on ALREADY-canonical documents, so per the function contract it must never abort the
            # run or hold the checkpoint. Any unexpected OCR-phase error is captured as an exception and the
            # drive still reaches its normal checkpoint decision below (which depends ONLY on download/import
            # outcomes). Download/import failures are handled separately above and DO still hold.
            try:
                counts = _ocr_documents(summary.get("affected_document_ids", []), progress=_ocr_prog)
                out["ocr_analyzed"] += counts["ocr_analyzed"]
                out["ocr_failed"] += counts["ocr_failed"]
                out["ocr_timed_out"] += counts["ocr_timed_out"]
            except Exception as exc:  # noqa: BLE001 — OCR is post-canonical: isolate, never block the checkpoint
                log.warning("delta OCR phase failed (non-blocking): drive=%s exception=%s: %s",
                            did, type(exc).__name__, str(exc)[:300])
                out["exceptions"].append({"drive_id": did, "phase": "ocr",
                                          "exception_type": type(exc).__name__, "error": str(exc)[:500]})
                out["status"] = "completed_with_errors"
        # Advance the checkpoint ONLY on a clean traversal + full download+import of every changed item.
        if dl_failures == 0 and import_item_failures == 0 and res["delta_link"]:
            save_canonical_delta_link(did, res["delta_link"])
            drv["advanced"] = True
            out["checkpoints_advanced"] += 1
            rep.set_checkpoint("ADVANCED")
        else:
            drv["held_reason"] = (f"{dl_failures} download + {import_item_failures} import failure(s) — "
                                  "checkpoint held; changes re-delivered next sync")
            out["status"] = "completed_with_errors"
            rep.set_checkpoint("HELD (download/import failures)")
        rep.emit()                                        # final per-drive line (with the checkpoint outcome)
        out["download_failures"] += dl_failures
        out["permanent_failures"] += permanent_failures
        drv.update({"resumed": res["resumed"], "changed": len(res["changed"]),
                    "deleted": len(res["deleted"]), "download_failures": dl_failures,
                    "permanent_failures": permanent_failures,
                    "import_item_failures": import_item_failures})
        out["drives"].append(drv)
    _record_run("SharePoint", {"status": out["status"], "items_examined": out["imported"],
                               "canonical_created": out["imported"], "ocr_analyzed": out["ocr_analyzed"]},
                started=started, actor_user_id=actor_user_id, trigger_source=trigger_source)
    return out


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
    """Build an authenticated Graph session using the connector's existing auth path.

    The production SharePoint connector acquires its delegated Graph token through
    ``_load_connected_account`` + ``_acquire_token`` and sends requests with
    ``_auth_header``. Reuse that exact path while exposing a requests.Session-like
    object for the delta downloader.
    """
    load_account = getattr(mod, "_load_connected_account", None)
    acquire_token = getattr(mod, "_acquire_token", None)
    auth_header = getattr(mod, "_auth_header", None)
    requests_mod = getattr(mod, "requests", None)

    if not (
        callable(load_account)
        and callable(acquire_token)
        and callable(auth_header)
        and requests_mod is not None
        and callable(getattr(requests_mod, "Session", None))
    ):
        raise RuntimeError(
            "SharePoint connector does not expose the expected authenticated "
            "Graph request path."
        )

    account = load_account()
    token = acquire_token(account)

    session = requests_mod.Session()
    session.headers.update(auth_header(token))
    return session


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
        items, source = from_file, "manifest_file"          # already usable (failed dropped, deduped)
    else:
        items, source = _usable_staged_records(_stager_items(result)), "run_return"
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
