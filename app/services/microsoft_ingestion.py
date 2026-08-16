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
from datetime import UTC, datetime

from sqlalchemy import select

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


def _invoke_stager(fn, site_ids, dry_run):
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {}
    if "site_ids" in params:
        kwargs["site_ids"] = site_ids
    if "dry_run" in params:
        kwargs["dry_run"] = dry_run
    return fn(**kwargs) if kwargs else fn()


def _stager_items(result):
    import json
    from pathlib import Path
    if isinstance(result, tuple) and result and isinstance(result[0], list):
        return result[0]                                   # (manifest_items, run_record)
    if isinstance(result, list):
        return result
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", None)
    if isinstance(items, list):
        return items
    mp = result.get("manifest_path") if isinstance(result, dict) else getattr(result, "manifest_path", None)
    if mp and Path(mp).exists():
        return json.loads(Path(mp).read_text())
    return []


def resolve_sharepoint_stager(*, site_ids=None, dry_run=False, module=None):
    """Return a zero-arg ``stager`` callable that invokes the REAL deployment connector's SharePoint
    staging entrypoint and yields the manifest items. It does NOT hard-import a specific function name:
    on call it discovers the connector's public staging callable (adapting to the deployment's connector)
    and raises a clear error — listing the connector's actual public callables and the connector-CLI +
    --manifest contract — if none is found. ``module`` overrides the connector module (used by tests)."""
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
        return _stager_items(_invoke_stager(fn, site_ids, dry_run))
    return _do


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
