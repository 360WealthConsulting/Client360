"""Strict-safe document tax-year resolution — the deterministic plan the guarded apply executes.

WHAT ``documents.tax_year`` MEANS
---------------------------------
It is the **resolved** tax year of a document: an adjudicated result produced by a validated
resolution process, persisted so the rest of the system can stop re-deriving it. It is authoritative
STATE, not another raw signal.

That distinction is the whole safety argument, so it is worth being blunt about the failure it
prevents. ``document_filing_preview._year_evidence`` grades a year ``strong`` when two independent
signals agree. The evidence behind this batch is the document's own OCR text. If the resolved year
were appended to that signal list, it would agree with the source-path year that helped select the
document, and the preview would report "two independent signals" for what is really one piece of
evidence counted twice. So :func:`document_filing_preview._resolve_year` treats a non-NULL
``documents.tax_year`` as the RESOLUTION and short-circuits — the raw signals are still recorded as
evidence, and any that disagree are surfaced, but nothing votes.

WHY tags['tax_year'] IS NOT THE PERSISTENCE MECHANISM
------------------------------------------------------
``tag`` is one of the three raw signals, and a recorded tag alone confers ``strong``. Writing the
resolved year into ``tags`` would manufacture exactly the circular evidence described above, and it
would also turn an inference into a displayed fact on a surface (the Documents tab) that has always
meant "a human or a source system recorded this". The column is the correct home: it already exists
(migration cf01), nothing writes it today, and ``client360/documents_screen.py`` already prefers it
over the tag when displaying a year.

WHAT MAKES A CANDIDATE STRICT-SAFE
-----------------------------------
Every clause below was measured before it was written down, in a read-only analysis over the whole
cohort. Two OCR passes established that the strict year rules read Form 8879 reliably and little
else, and that across 743 documents with a determinate year the source-path folder disagreed 4.44%
of the time. So the folder is NOT trusted to supply a year here: it is only required to AGREE with
the year the document states about itself. The evidence is the document's own content.

THE FROZEN CANDIDATE
---------------------
:data:`CANDIDATE_CSV_SHA256` pins the reviewed analysis artifact. The plan is rebuilt from that file
plus live database state, never from document ids alone, and a candidate that no longer satisfies
every clause is dropped rather than carried. :func:`plan_digest` hashes the whole plan including the
per-document content hash, so a corpus that has moved changes the digest and the apply refuses.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

#: Batch identity, encoded into both phrases so a phrase cannot be reused elsewhere.
BATCH_ID = "STRICT-SAFE-TAX-YEAR-1"

#: The reviewed analysis artifact this batch is authorized against.
CANDIDATE_CSV_SHA256 = "b7a06200acee2d21809ffe7e40977a077ce012be7b958ea52ffd744d0c166a8b"
EXPECTED_DOCUMENTS = 756

#: The only verdict a candidate row may carry.
REQUIRED_VERDICT = "VERIFIED_FOLDER_YEAR"

#: Extraction rules accepted as a document-level year statement. Deliberately the validated set —
#: a bare year, a revision stamp or an OMB number is not a tax-year statement.
ACCEPTED_RULES = frozenset({"calendar_year", "tax_year", "year_ending",
                            "fiscal_year_beginning", "for_tax_year"})

#: Provenance boundary the cohort was measured within.
REQUIRED_SOURCE_SYSTEM = "SharePoint"
REQUIRED_SERVICE_CODE = "tax_preparation"
REQUIRED_PATH_FRAGMENT = "/clients/tax preparation/"

PLAN_FIELDS = ("document_id", "tax_year", "folder_year", "extraction_rule", "form_family",
               "evidence_source", "sha256", "owner_scope", "service_code")


class TaxYearPlanError(RuntimeError):
    """A candidate row cannot be turned into a plan row. Never recovered from silently."""


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_frozen_candidates(candidate_csv, *, expect_sha=CANDIDATE_CSV_SHA256,
                           expect_documents=EXPECTED_DOCUMENTS) -> list[dict]:
    """The reviewed rows, with the artifact pinned by content hash before a byte is trusted."""
    path = Path(candidate_csv)
    actual = sha256_of(path)
    if expect_sha and actual != expect_sha:
        raise TaxYearPlanError(
            f"candidate csv sha256 {actual} != approved {expect_sha} — this is not the reviewed file")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [dict(r) for r in csv.DictReader(handle)]
    if expect_documents is not None and len(rows) != expect_documents:
        raise TaxYearPlanError(
            f"candidate csv holds {len(rows)} rows, approved for {expect_documents}")
    seen: set[int] = set()
    for row in rows:
        document_id = int(row["document_id"])
        if document_id in seen:
            raise TaxYearPlanError(f"duplicate document_id {document_id} in the candidate")
        seen.add(document_id)
        if row["verdict"] != REQUIRED_VERDICT:
            raise TaxYearPlanError(
                f"document {document_id} carries verdict {row['verdict']!r}, "
                f"only {REQUIRED_VERDICT} may be applied")
        if row["anomaly_flags"]:
            raise TaxYearPlanError(
                f"document {document_id} carries anomaly flags {row['anomaly_flags']!r}")
        rules = {r for r in (row["extraction_rule"] or "").split("|") if r}
        if not rules or not rules <= ACCEPTED_RULES:
            raise TaxYearPlanError(
                f"document {document_id} rules {sorted(rules)} are not all accepted year rules")
        if row["all_years"] and len({y for y in row["all_years"].split("|") if y}) != 1:
            raise TaxYearPlanError(f"document {document_id} extraction is not unambiguous")
        if int(row["extracted_year"]) != int(row["folder_year"]):
            raise TaxYearPlanError(
                f"document {document_id} extracted {row['extracted_year']} but folder says "
                f"{row['folder_year']} — a conflict may never be applied")
    return rows


_LIVE_SQL = """
    SELECT d.id                         AS document_id,
           d.tax_year                   AS current_tax_year,
           d.sha256                     AS sha256,
           d.status                     AS status,
           d.archived                   AS archived,
           d.deleted_at                 AS deleted_at,
           d.person_id, d.household_id, d.organization_id,
           d.storage_uri, d.storage_path,
           s.source_system              AS source_system,
           lower(coalesce(s.source_path, s.source_uri, '')) AS source_path
      FROM documents d
 LEFT JOIN LATERAL (
           SELECT source_system, source_path, source_uri
             FROM document_sources
            WHERE document_id = d.id
            ORDER BY id
            LIMIT 1) s ON true
     WHERE d.id = ANY(:ids)
     ORDER BY d.id
"""


def _owner_scope(row) -> str | None:
    if row["person_id"] is not None:
        return f"person:{row['person_id']}"
    if row["household_id"] is not None:
        return f"household:{row['household_id']}"
    if row["organization_id"] is not None:
        return f"organization:{row['organization_id']}"
    return None


def build_plan(conn, candidate_csv, *, expect_sha=CANDIDATE_CSV_SHA256,
               expect_documents=EXPECTED_DOCUMENTS, verify_file_hash=True) -> dict:
    """The plan as it stands RIGHT NOW. Read-only; issues SELECTs and reads files, writes nothing.

    ``verify_file_hash`` re-hashes each document's bytes on disk and requires them to equal
    ``documents.sha256``. That is what binds the OCR evidence — gathered hours earlier by a separate
    non-persistent pass — to the file this plan is about. Without it the plan would assert a year for
    bytes nobody has checked since extraction.
    """
    candidates = read_frozen_candidates(candidate_csv, expect_sha=expect_sha,
                                        expect_documents=expect_documents)
    by_id = {int(r["document_id"]): r for r in candidates}
    ids = sorted(by_id)
    live = {r["document_id"]: dict(r) for r in conn.execute(text(_LIVE_SQL), {"ids": ids}).mappings()}

    rows, dropped = [], []
    for document_id in ids:
        candidate = by_id[document_id]
        record = live.get(document_id)

        def drop(reason, document_id=document_id):
            dropped.append({"document_id": document_id, "reason": reason})

        if record is None:
            drop("document no longer exists")
            continue
        if record["current_tax_year"] is not None:
            drop(f"tax_year already set to {record['current_tax_year']}")
            continue
        if record["status"] == "deleted" or record["deleted_at"] is not None:
            drop("document is deleted")
            continue
        if record["archived"]:
            drop("document is archived")
            continue
        scope = _owner_scope(record)
        if scope is None:
            drop("owner is no longer resolved")
            continue
        if (record["source_system"] or "") != REQUIRED_SOURCE_SYSTEM:
            drop(f"source system is {record['source_system']!r}")
            continue
        if REQUIRED_PATH_FRAGMENT not in (record["source_path"] or ""):
            drop("source path is outside the approved provenance boundary")
            continue
        folder_year = int(candidate["folder_year"])
        if f"/{folder_year}" not in (record["source_path"] or ""):
            drop(f"source path no longer carries the approved year {folder_year}")
            continue
        if not record["sha256"]:
            drop("document has no content hash to bind the evidence to")
            continue
        if verify_file_hash:
            path = record["storage_uri"] or record["storage_path"]
            candidate_path = Path(path) if path else None
            if candidate_path is None or not candidate_path.exists():
                drop("source file is unreachable, so its evidence cannot be re-bound")
                continue
            if sha256_of(candidate_path) != record["sha256"]:
                drop("file bytes no longer match documents.sha256 — evidence is stale")
                continue
        rows.append({
            "document_id": document_id,
            "tax_year": int(candidate["extracted_year"]),
            "folder_year": folder_year,
            "extraction_rule": candidate["extraction_rule"],
            "form_family": candidate["form_family"],
            "evidence_source": candidate["evidence_source"],
            "sha256": record["sha256"],
            "owner_scope": scope,
            "service_code": REQUIRED_SERVICE_CODE,
        })

    rows.sort(key=lambda r: r["document_id"])
    return {"batch_id": BATCH_ID, "documents": rows, "dropped": dropped,
            "candidate_count": len(candidates)}


def plan_digest(plan) -> str:
    """Content hash over the plan. Sensitive by design: a changed year, rule or file hash moves it."""
    payload = [{k: row[k] for k in PLAN_FIELDS} for row in
               sorted(plan["documents"], key=lambda r: r["document_id"])]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def plan_census(plan) -> dict:
    from collections import Counter
    rows = plan["documents"]
    return {
        "documents": len(rows),
        "candidates": plan["candidate_count"],
        "dropped": len(plan["dropped"]),
        "by_year": dict(sorted(Counter(r["tax_year"] for r in rows).items())),
        "by_form_family": dict(sorted(Counter(r["form_family"] for r in rows).items())),
        "by_evidence_source": dict(sorted(Counter(r["evidence_source"] for r in rows).items())),
        "distinct_owners": len({r["owner_scope"] for r in rows}),
        "folder_year_equals_tax_year": all(r["tax_year"] == r["folder_year"] for r in rows),
    }


def confirm_phrase(document_count) -> str:
    return f"APPLY-{BATCH_ID}-{document_count}"


def rollback_phrase(document_count) -> str:
    return f"ROLLBACK-{BATCH_ID}-{document_count}"


__all__ = [
    "ACCEPTED_RULES",
    "BATCH_ID",
    "CANDIDATE_CSV_SHA256",
    "EXPECTED_DOCUMENTS",
    "PLAN_FIELDS",
    "TaxYearPlanError",
    "build_plan",
    "confirm_phrase",
    "plan_census",
    "plan_digest",
    "read_frozen_candidates",
    "rollback_phrase",
    "sha256_of",
]
