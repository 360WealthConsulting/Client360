"""Knowledge pipeline (Phase 6A) — Canonical Document → OCR → Classify → Extract → Validate → Facts.

Orchestrates the Knowledge layer over the EXISTING canonical model: it reads a document's completed OCR
text (``document_ocr``), classifies it (``document_classification``), extracts structured facts
(``knowledge_extraction``), validates them, and stores them as versioned Knowledge Objects
(``document_facts``) kept SEPARATE from the OCR text. Extracted dates become Timeline events on the
document's owner (ADR-073). No AI application, no Intelligence-specific screens — the workspace tabs read
the classification + facts through this module's read helpers.

Idempotent: re-running re-classifies (upsert) and re-extracts; an unchanged fact set is a no-op, a
changed one supersedes the prior current facts (``is_current`` flips, ``version`` increments), and
Timeline events upsert on a stable external id. Audited.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import and_, or_, select

from app.db import document_classifications, document_facts, document_ocr, documents, engine
from app.services.document_classification import CLASSIFIER_VERSION, classify_document
from app.services.knowledge_extraction import EXTRACTOR_VERSION, extract_facts

# Facts whose value is a calendar date and should also become a Timeline event.
_DATE_FACT = "date"


def _candidates(conn, *, mode, document_ids, batch_size):
    j = documents.outerjoin(document_classifications,
                            document_classifications.c.document_id == documents.c.id)
    stmt = (select(documents.c.id, documents.c.original_name, documents.c.person_id,
                   documents.c.household_id, document_ocr.c.text)
            .select_from(j.join(document_ocr, document_ocr.c.document_id == documents.c.id))
            .where(documents.c.status != "deleted", document_ocr.c.status == "completed"))
    if document_ids is not None:
        stmt = stmt.where(documents.c.id.in_(tuple(document_ids) or (-1,)))
    elif mode != "reprocess":      # incremental/initial — not yet classified
        stmt = stmt.where(document_classifications.c.document_id.is_(None))
    return conn.execute(stmt.order_by(documents.c.id).limit(batch_size)).mappings().all()


def _new_summary(mode, dry_run):
    return {"mode": mode, "candidates": 0, "classified": 0, "facts_written": 0,
            "facts_superseded": 0, "timeline_events": 0, "skipped": 0, "errors": [],
            "dry_run": dry_run, "status": "started"}


def run_knowledge_pipeline(*, document_ids=None, mode="incremental", actor_user_id=None,
                           request_id=None, batch_size=200, dry_run=False) -> dict:
    """Run the Knowledge pipeline over classified-pending (or specified) documents. ``mode``:
    ``initial``/``incremental`` (documents with OCR but no classification) or ``reprocess`` (re-run).
    Returns a summary of counts."""
    summary = _new_summary(mode, dry_run)
    with engine.connect() as conn:
        cands = _candidates(conn, mode=mode, document_ids=document_ids, batch_size=batch_size)
    summary["candidates"] = len(cands)
    for row in cands:
        try:
            _process_one(row, summary, dry_run)
        except Exception as exc:      # noqa: BLE001 — record & continue
            summary["errors"].append(f"doc {row['id']}: {exc}")
    _audit(summary, actor_user_id, request_id, dry_run)
    summary["status"] = "dry_run" if dry_run else ("completed_with_errors" if summary["errors"]
                                                   else "completed")
    return summary


def _process_one(row, summary, dry_run):
    doc_id, name, text = row["id"], row["original_name"], row["text"]
    doc_type, confidence = classify_document(name, text)
    raw_facts = _validate(extract_facts(name=name, text=text, doc_type=doc_type))

    if dry_run:
        summary["classified"] += 1
        summary["facts_written"] += len(raw_facts)
        return

    now = datetime.now(UTC)
    with engine.begin() as conn:
        # 1) Classification — one current row per document (upsert).
        _upsert_classification(conn, doc_id, doc_type, confidence, now)
        summary["classified"] += 1
        # 2) Facts — versioned Knowledge Objects, superseding the prior set only when it changed.
        written, superseded = _store_facts(conn, doc_id, raw_facts, now)
        summary["facts_written"] += written
        summary["facts_superseded"] += superseded
    # 3) Timeline events from extracted dates (own transaction via the timeline service; idempotent).
    summary["timeline_events"] += _emit_timeline(row, doc_type, raw_facts)


def _upsert_classification(conn, doc_id, doc_type, confidence, now):
    existing = conn.execute(select(document_classifications.c.id).where(
        document_classifications.c.document_id == doc_id)).scalar()
    values = {"doc_type": doc_type, "confidence": confidence,
              "classifier_version": CLASSIFIER_VERSION, "classified_at": now, "updated_at": now}
    if existing is None:
        conn.execute(document_classifications.insert().values(document_id=doc_id, **values))
    else:
        conn.execute(document_classifications.update().where(
            document_classifications.c.id == existing).values(**values))


def _store_facts(conn, doc_id, facts, now):
    current = {(r["fact_type"], r["fact_value"]): r["version"] for r in conn.execute(
        select(document_facts.c.fact_type, document_facts.c.fact_value, document_facts.c.version)
        .where(document_facts.c.document_id == doc_id, document_facts.c.is_current.is_(True)))
        .mappings()}
    new = {(f["fact_type"], f["value"]): f["confidence"] for f in facts}
    if set(new) == set(current):
        return 0, 0                                    # idempotent — unchanged fact set, no version churn
    superseded = conn.execute(document_facts.update().where(and_(
        document_facts.c.document_id == doc_id, document_facts.c.is_current.is_(True)))
        .values(is_current=False)).rowcount or 0
    for (ftype, value), conf in new.items():
        conn.execute(document_facts.insert().values(
            document_id=doc_id, fact_type=ftype, fact_value=value, confidence=conf,
            extraction_engine="rules", extractor_version=EXTRACTOR_VERSION, extracted_at=now,
            version=(current.get((ftype, value), 0) or 0) + 1, is_current=True))
    return len(new), superseded


def _emit_timeline(row, doc_type, facts):
    person_id, household_id = row["person_id"], row["household_id"]
    if person_id is None and household_id is None:
        return 0                                       # unowned document → nothing to anchor a timeline to
    from app.services.timeline import add_timeline_event
    added = 0
    for f in facts:
        if f["fact_type"] != _DATE_FACT:
            continue
        when = _parse_date(f["value"])
        if when is None:
            continue
        add_timeline_event(
            source="knowledge", event_type="document_date",
            title=f"{(doc_type or 'Document').replace('_', ' ')} date: {f['value']}",
            person_id=person_id, household_id=household_id, event_time=when,
            external_id=f"knowledge-doc-{row['id']}-date-{f['value']}",
            event_metadata={"document_id": row["id"], "doc_type": doc_type})
        added += 1
    return added


def _parse_date(value):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
    return None


def _validate(facts):
    """Drop implausible extractions before they become Knowledge Objects (honest, no fabrication)."""
    out = []
    for f in facts:
        ft, val = f["fact_type"], (f["value"] or "").strip()
        if not val:
            continue
        if ft in ("tax_year", "document_year"):
            if not (val.isdigit() and 1990 <= int(val) <= date.today().year + 1):
                continue
        elif ft == "ssn_last4" and not (len(val) == 4 and val.isdigit()):
            continue
        elif ft == "ein" and not _valid_ein(val):
            continue
        out.append({**f, "value": val})
    return out


def _valid_ein(val):
    parts = val.split("-")
    return len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) == 7


def _audit(summary, actor_user_id, request_id, dry_run):
    if dry_run:
        return
    from app.security.audit import write_audit_event
    write_audit_event(
        action="document.knowledge_run", entity_type="document", entity_id=None,
        actor_user_id=actor_user_id, request_id=request_id or f"knowledge-{uuid.uuid4()}",
        metadata={k: summary[k] for k in ("mode", "candidates", "classified", "facts_written",
                                          "facts_superseded", "timeline_events")})


# --- read helpers (workspace) ------------------------------------------------

def classification_for_documents(document_ids) -> dict[int, dict]:
    ids = [i for i in document_ids if i]
    if not ids:
        return {}
    with engine.connect() as conn:
        return {r["document_id"]: dict(r) for r in conn.execute(select(
            document_classifications.c.document_id, document_classifications.c.doc_type,
            document_classifications.c.confidence, document_classifications.c.classifier_version,
            document_classifications.c.classified_at)
            .where(document_classifications.c.document_id.in_(ids))).mappings()}


def facts_for_documents(document_ids) -> dict[int, list[dict]]:
    ids = [i for i in document_ids if i]
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    with engine.connect() as conn:
        for r in conn.execute(select(
                document_facts.c.document_id, document_facts.c.fact_type, document_facts.c.fact_value,
                document_facts.c.confidence, document_facts.c.extraction_engine,
                document_facts.c.extracted_at, document_facts.c.version)
                .where(document_facts.c.document_id.in_(ids), document_facts.c.is_current.is_(True))
                .order_by(document_facts.c.fact_type)).mappings():
            out.setdefault(r["document_id"], []).append(dict(r))
    return out


def facts_for_document(document_id: int) -> list[dict]:
    return facts_for_documents([document_id]).get(document_id, [])


_COMPLIANCE_TYPES = ("irs_notice", "state_notice")


def _classified_for_scope(scope_ids, household_ids, *, extra_where, limit) -> list[dict]:
    """Classified documents owned by the given people/households (ADR-073), for Dashboard cards."""
    pids = [i for i in (scope_ids or []) if i]
    hids = [i for i in (household_ids or []) if i]
    if not pids and not hids:
        return []
    owner = []
    if pids:
        owner.append(documents.c.person_id.in_(pids))
    if hids:
        owner.append(documents.c.household_id.in_(hids))
    with engine.connect() as conn:
        rows = conn.execute(select(
            documents.c.id, documents.c.original_name, document_classifications.c.doc_type,
            document_classifications.c.confidence, document_classifications.c.classified_at)
            .select_from(documents.join(document_classifications,
                                        document_classifications.c.document_id == documents.c.id))
            .where(documents.c.status != "deleted", or_(*owner), extra_where)
            .order_by(document_classifications.c.classified_at.desc()).limit(limit)).mappings()
        return [dict(r) for r in rows]


def recently_classified(scope_ids, *, household_ids=None, limit=8) -> list[dict]:
    """Newly classified (identified) documents — for the Dashboard 'newly classified' card."""
    return _classified_for_scope(scope_ids, household_ids,
                                 extra_where=document_classifications.c.doc_type != "unknown",
                                 limit=limit)


def compliance_documents(scope_ids, *, household_ids=None, limit=8) -> list[dict]:
    """Documents whose type implies a compliance action (IRS / state notices) — Dashboard card."""
    return _classified_for_scope(scope_ids, household_ids,
                                 extra_where=document_classifications.c.doc_type.in_(_COMPLIANCE_TYPES),
                                 limit=limit)


def unidentified_documents(scope_ids, *, household_ids=None, limit=8) -> list[dict]:
    """Documents OCR'd but not identifiable (classified 'unknown') — a missing/needs-filing alert."""
    return _classified_for_scope(scope_ids, household_ids,
                                 extra_where=document_classifications.c.doc_type == "unknown",
                                 limit=limit)


_TAX_FACT_TYPES = ("tax_year", "filing_status", "return_type", "ein")


def tax_facts_for_scope(person_ids, household_id=None, *, limit=20) -> list[dict]:
    """Tax-relevant extracted facts (tax_year / filing_status / return_type / EIN) for documents owned
    by the scope — surfaced on the Tax tab as clearly-labeled, unverified 'from documents' hints."""
    pids = [i for i in (person_ids or []) if i]
    owner = []
    if pids:
        owner.append(documents.c.person_id.in_(pids))
    if household_id:
        owner.append(documents.c.household_id == household_id)
    if not owner:
        return []
    with engine.connect() as conn:
        rows = conn.execute(select(
            document_facts.c.fact_type, document_facts.c.fact_value, document_facts.c.confidence,
            documents.c.id.label("document_id"), documents.c.original_name)
            .select_from(document_facts.join(documents, documents.c.id == document_facts.c.document_id))
            .where(document_facts.c.is_current.is_(True),
                   document_facts.c.fact_type.in_(_TAX_FACT_TYPES),
                   documents.c.status != "deleted", or_(*owner))
            .order_by(document_facts.c.fact_type).limit(limit)).mappings()
        return [dict(r) for r in rows]


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m app.services.knowledge_pipeline",
                                description="Run the document Knowledge pipeline (classify + extract).")
    p.add_argument("--mode", choices=("initial", "incremental", "reprocess"), default="incremental")
    p.add_argument("--batch-size", type=int, default=200)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    s = run_knowledge_pipeline(mode=args.mode, batch_size=args.batch_size, dry_run=args.dry_run)
    for k in ("mode", "candidates", "classified", "facts_written", "facts_superseded",
              "timeline_events", "status"):
        print(f"  {k}: {s[k]}")
    if s["errors"]:
        print(f"  errors ({len(s['errors'])}):")
        for e in s["errors"][:20]:
            print(f"    - {e}")
    return 1 if s["status"] == "completed_with_errors" else 0


if __name__ == "__main__":
    raise SystemExit(main())
