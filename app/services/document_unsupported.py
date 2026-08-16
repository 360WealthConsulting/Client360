"""Phase 5 — READ-ONLY inventory of UNSUPPORTED documents + targeted re-analysis of exactly those.

`inventory()` reports every currently-UNSUPPORTED unassigned document (the pipeline route is UNSUPPORTED —
no usable extracted text) with the details needed to decide which extractors are worth adding, and
summarises counts by extension / failure reason / source system. It writes nothing and runs no OCR.

`reanalyze()` runs ONLY those exact documents back through the SAME pipeline with the OCR fallback enabled
(the permitted targeted re-OCR of these documents during remediation), plus the new docx/ics extractors,
and reports BEFORE vs AFTER bucket counts. It assigns no ownership, creates nothing, and moves/deletes no
files — the only side effect is populating the document_ocr TEXT cache for these targeted documents.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db import document_ocr, documents, engine
from app.services.document_high_validation import _unassigned_ids
from app.services.document_owner_proposal import _ext
from app.services.document_pipeline import ROUTES, analyze_document

# Extensions the pipeline HAS an extractor for (after Phase 5). If one of these still yields no text, the
# extractor ran but failed; anything else has no extractor for that type.
_KNOWN_EXT = {"xlsx", "xlsm", "docx", "ics", "pdf", "txt", "csv", "md", "log",
              "png", "jpg", "jpeg", "tif", "tiff", "heic", "heif"}


def _resolve_path(row):
    uri, sp = row.get("storage_uri"), row.get("storage_path")
    if uri and Path(str(uri)).is_absolute():
        return Path(str(uri))
    if sp:
        return Path(str(sp))
    return None


def _ocr_state(conn, did):
    row = conn.execute(select(document_ocr.c.status, document_ocr.c.text)
                       .where(document_ocr.c.document_id == did)
                       .order_by(document_ocr.c.id.desc()).limit(1)).mappings().first()
    if row is None:
        return {"attempted": False, "cache": False, "status": None}
    return {"attempted": True, "cache": bool((row["text"] or "").strip()), "status": row["status"]}


def _unsupported_ids(conn, *, limit=None, ocr=False):
    """Document ids whose pipeline route is UNSUPPORTED right now (no usable extracted text)."""
    ids = []
    for did in _unassigned_ids(conn, limit=limit):
        r = analyze_document(did, conn=conn, ocr=ocr)
        if r.get("eligible") and r.get("route") == "UNSUPPORTED":
            ids.append(did)
    return ids


def inventory(*, limit=None):
    """READ-ONLY. Returns {total, rows, by_extension, by_reason, by_source}. Runs no OCR (ocr=False)."""
    rows = []
    by_ext, by_reason, by_source = Counter(), Counter(), Counter()
    with engine.connect() as conn:
        for did in _unsupported_ids(conn, limit=limit, ocr=False):
            r = analyze_document(did, conn=conn, ocr=False)
            drow = conn.execute(select(documents.c.original_name, documents.c.content_type,
                                       documents.c.storage_uri, documents.c.storage_path, documents.c.tags)
                                .where(documents.c.id == did)).mappings().first() or {}
            ext = _ext(drow.get("original_name"))
            path = _resolve_path(drow)
            ocr_state = _ocr_state(conn, did)
            method = r.get("extraction_method")
            tags = drow.get("tags") or {}
            reason = (method if method in ("unsupported", "image_no_text", "pdf_no_text", "none")
                      else "no_usable_text")
            extractor_exists = ext in _KNOWN_EXT
            source_system = tags.get("source_system") or "—"
            rows.append({
                "document_id": did, "filename": drow.get("original_name"), "extension": ext,
                "content_type": drow.get("content_type"),
                "source_system": source_system, "source_path": tags.get("taxdome_folder"),
                "extraction_method": method, "failure_reason": reason,
                "file_exists": bool(path and path.exists()),
                "extractor_exists_but_failed": bool(extractor_exists and path and path.exists()),
                "ocr_attempted": ocr_state["attempted"], "ocr_cache_exists": ocr_state["cache"],
                "ocr_status": ocr_state["status"],
            })
            by_ext[ext or "(none)"] += 1
            by_reason[reason] += 1
            by_source[source_system] += 1
    return {"total": len(rows), "rows": rows,
            "by_extension": dict(by_ext.most_common()),
            "by_reason": dict(by_reason.most_common()),
            "by_source": dict(by_source.most_common())}


def reanalyze(*, doc_ids=None, limit=None):
    """Re-run ONLY the currently-UNSUPPORTED documents (or the given ids) with the OCR fallback enabled.
    Returns before/after bucket counts + newly_text / newly_identity / remaining. Assigns no ownership."""
    after_counts = dict.fromkeys((*ROUTES, "SKIPPED"), 0)
    newly_text = newly_identity = 0
    remaining = []
    with engine.connect() as conn:
        targets = list(doc_ids) if doc_ids else _unsupported_ids(conn, limit=limit, ocr=False)
        before = len(targets)
        for did in targets:
            try:
                r = analyze_document(did, conn=conn, ocr=True)      # targeted re-OCR permitted here
                route = r.get("route") or "SKIPPED"
            except Exception as exc:                                # noqa: BLE001
                route = "ERROR"
                r = {"document_id": did, "route": "ERROR", "error": str(exc)[:200], "extracted": {}}
            after_counts[route] = after_counts.get(route, 0) + 1
            if route not in ("UNSUPPORTED", "ERROR", "SKIPPED"):
                newly_text += 1
            ex = r.get("extracted") or {}
            if ex.get("emails") or ex.get("phones") or ex.get("names") or route in ("HIGH", "MEDIUM", "AMBIGUOUS"):
                newly_identity += 1
            if route == "UNSUPPORTED":
                remaining.append({"document_id": did, "reason": r.get("extraction_method")})
    return {"before_unsupported": before, "after_counts": after_counts,
            "newly_text": newly_text, "newly_identity": newly_identity,
            "remaining": remaining, "remaining_count": len(remaining)}
