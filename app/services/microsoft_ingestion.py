"""Continuous ingestion — Microsoft/SharePoint sync runner (Phase C of the change-detection milestone).

A thin, repeatable orchestration over the EXISTING pieces — no second extraction or matching engine:

    stage (connector, incremental delta/state)  ->  import_sharepoint_items (canonical dedupe + change
    detection: NEW ingests, CHANGED re-ingests a new canonical version, UNCHANGED is skipped without
    re-hash/OCR, DELETED marks the source reference unavailable and NEVER deletes the canonical document)
    ->  OCR-on-need analysis for ONLY the new documents  ->  durable run record (audit) for admin visibility.

The staging step (live Microsoft Graph enumeration + download) is the deployment connector
``app.connectors.microsoft365.sharepoint_content.stage_sharepoint_content`` — injected here as ``stager``
(or pre-staged ``items``) so this runner is testable and reuses the real connector at deploy time. Nothing
here creates a client, assigns ambiguous ownership, or re-OCRs unchanged documents. Failures are isolated:
a bad staged item is recorded and skipped by the importer, and a whole-run failure is recorded, not raised.
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
