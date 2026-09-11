#!/usr/bin/env python3
"""Read-only full-corpus ownership PLAN — what the pipeline would decide, before it decides it.

Answers the question you have to answer before turning a pipeline loose on a client corpus: for every
document, which lane would claim it, what would it link to, and how many of those decisions would a
human have to make? It writes nothing. It is the same question ``document_pipeline_continuous`` asks
at runtime, asked ahead of time and written to a file instead of to the database.

READ-ONLY IS ENFORCED BY THE SERVER, NOT BY THIS FILE
-----------------------------------------------------
The connection sets ``default_transaction_read_only=on``, so PostgreSQL itself refuses any INSERT,
UPDATE, DELETE or DDL from this session. A bug here cannot write to production; it can only crash.
That is a stronger guarantee than "the code does not call insert()", and it is checked and printed at
startup so the operator sees it rather than trusting it.

IT DOES NOT COMPETE WITH A RUNNING OCR SWEEP
---------------------------------------------
* It never opens a document to extract text. Every judgement comes from the database — filename,
  folder path, source system, and whatever OCR text has ALREADY been extracted. A document whose OCR
  has not finished yet is reported as awaiting OCR rather than forced through an engine.
* It drops itself to below-normal process priority on Windows, so the OCR workers get the CPU whenever
  they want it.
* It takes no advisory lock, claims no document, and writes no queue row.

The one filesystem cost is a ``stat`` per document to inventory missing source files, which is
metadata only — no file is read.

NO CORPUS CAP
-------------
There is no ``--limit``. The walk ends when the corpus ends. ``--chunk-size`` paginates the walk and
exists so that a crash costs one chunk instead of the whole run: the checkpoint file records the last
document id completed, and ``--resume`` (the default) continues from it.

THE PLAN PREDICTS THE PIPELINE
------------------------------
Each lane calls the same function the pipeline calls, and the confidence-to-outcome mapping is
imported from ``document_pipeline_continuous.ownership`` rather than restated here, so the plan cannot
quietly disagree with what would actually happen:

* ``drake``    — ``drake_document_owner.propose_drake_document_owner``. AUTHORITATIVE: no
  corroboration is required or consulted.
* ``taxdome``  — ``taxdome_drive.resolve_folder`` on the document's account folder. AUTHORITATIVE:
  the folder IS the mapping; content evidence is not consulted.
* ``sharepoint`` — ``document_owner_proposal.analyze_identity`` over folder path, filename, names,
  households, organizations, addresses, emails, phones, account identifiers and cached OCR text.

Usage::

    python scripts/plan_document_ownership.py --out reports/ownership_plan.json
    python scripts/plan_document_ownership.py --restart          # ignore the checkpoint
    python scripts/plan_document_ownership.py --no-check-files   # skip the missing-file stat pass
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = REPO_ROOT / "reports" / "ownership_plan.json"
DEFAULT_CHECKPOINT = REPO_ROOT / "reports" / "ownership_plan.checkpoint.json"
DEFAULT_CHUNK = 2000

#: File types the OCR/extraction path can produce text for. Anything else is inventoried as
#: unsupported — it is not a failure, it is a document with no text in it to read.
_TEXT_EXT = {"pdf", "tif", "tiff", "png", "jpg", "jpeg", "heic", "heif", "xlsx", "xlsm", "xls",
             "docx", "txt", "csv", "md", "log", "ics", "eml"}


def _lower_priority() -> str:
    """Yield the CPU to whatever else is running. Never fails the run."""
    try:
        if os.name == "nt":
            import ctypes
            below_normal = 0x00004000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # argtypes/restype are REQUIRED here: GetCurrentProcess returns a pseudo-handle, and
            # ctypes' default c_int truncates it on 64-bit, so SetPriorityClass silently fails.
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass.restype = ctypes.c_int
            if kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal):
                return "below_normal"
            return f"unchanged (SetPriorityClass errno {ctypes.get_last_error()})"
        os.nice(10)
        return "nice+10"
    except Exception as exc:      # noqa: BLE001 — a priority hint is never worth a crash
        return f"unchanged ({exc.__class__.__name__})"


def _database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / "app" / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set and app/.env does not define one.")
    return url


def read_only_engine(url: str):
    """An engine whose every session is read-only at the SERVER. Writes fail, they do not slip through."""
    return sa.create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"},
                            pool_pre_ping=True)


def assert_read_only(conn) -> None:
    """Prove the guard rather than assert it in a comment.

    The probe deliberately attempts a write, which aborts the transaction — so it MUST be rolled back
    before the caller does anything else, or every later query dies with "current transaction is
    aborted". Proving the guard and leaving the connection usable are both part of the job."""
    setting = conn.execute(sa.text("SHOW default_transaction_read_only")).scalar()
    if str(setting).lower() not in ("on", "true"):
        raise SystemExit(
            f"REFUSED: the connection is not read-only (default_transaction_read_only={setting!r}).")
    try:
        conn.execute(sa.text("CREATE TEMP TABLE _plan_write_probe (x int)"))
    except Exception:
        conn.rollback()      # exactly what must happen — clear the aborted transaction and carry on
        return
    conn.rollback()
    raise SystemExit("REFUSED: a write succeeded on a connection that must be read-only.")


# --- per-chunk reads ------------------------------------------------------------------------------

def _documents_chunk(conn, after_id: int, chunk_size: int):
    return conn.execute(sa.text("""
        SELECT id, original_name, status, size_bytes, sha256, storage_uri, storage_path,
               person_id, household_id, organization_id, tags, category, classification,
               subcategory, ocr_status
          FROM documents
         WHERE id > :after
         ORDER BY id
         LIMIT :chunk
    """), {"after": after_id, "chunk": chunk_size}).mappings().all()


def _sources_for(conn, ids):
    rows = conn.execute(sa.text("""
        SELECT document_id, source_system FROM document_sources WHERE document_id = ANY(:ids)
    """), {"ids": list(ids)}).all()
    by_document: dict[int, set[str]] = {}
    for document_id, source_system in rows:
        by_document.setdefault(int(document_id), set()).add(source_system)
    return by_document


def _ocr_for(conn, ids):
    """Cached OCR text for a chunk, truncated exactly the way the pipeline truncates it.

    ``document_owner_proposal.extract_document_text`` caps what it hands the matcher at
    ``_MAX_TEXT_CHARS``. Reading the full column here would feed the matcher MORE text than the
    pipeline ever would, and a plan that scores on evidence the pipeline will not see is a plan that
    predicts matches it will not make. Truncating in SQL also keeps a chunk's memory bounded instead
    of proportional to the largest scanned document in it."""
    from app.services.document_owner_proposal import _MAX_TEXT_CHARS

    rows = conn.execute(sa.text("""
        SELECT document_id, status, COALESCE(char_count, 0) AS char_count,
               LEFT(text, :cap) AS text, last_error
          FROM document_ocr WHERE document_id = ANY(:ids)
    """), {"ids": list(ids), "cap": int(_MAX_TEXT_CHARS)}).mappings().all()
    return {int(row["document_id"]): dict(row) for row in rows}


# --- helpers --------------------------------------------------------------------------------------

def _ext(name):
    name = name or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _source_path(row):
    uri, path = row.get("storage_uri"), row.get("storage_path")
    if uri and Path(uri).is_absolute():
        return Path(uri)
    if path:
        return Path(path)
    return None


def _owned(row):
    return any(row.get(key) is not None for key in ("person_id", "household_id", "organization_id"))


def _owner_key(row):
    for entity_type, column in (("person", "person_id"), ("household", "household_id"),
                                ("organization", "organization_id")):
        if row.get(column) is not None:
            return (entity_type, int(row[column]))
    return None


def _conflicts(row, proposed):
    """An existing owner that disagrees with the lane's proposal. Same owner is agreement."""
    if proposed is None:
        return False
    existing = _owner_key(row)
    return existing is not None and existing != proposed


# --- the three lanes ------------------------------------------------------------------------------

class Planner:
    """Holds everything built once — the match indexes and the folder cache — and plans one document
    at a time against them."""

    def __init__(self, conn, *, check_files: bool = True):
        from app.services.document_owner_proposal import build_match_indexes
        from app.services.document_pipeline_continuous.ownership import (
            DRAKE_SOURCE,
            LANE_DRAKE,
            LANE_SHAREPOINT,
            LANE_TAXDOME,
            LINKABLE_CONFIDENCE,
            REVIEWABLE_CONFIDENCE,
            SHAREPOINT_SOURCE,
            TAXDOME_SOURCE,
        )
        self.conn = conn
        self.check_files = check_files
        self.idx = build_match_indexes(conn)
        self._folders: dict[str, tuple] = {}
        self.DRAKE_SOURCE, self.TAXDOME_SOURCE, self.SHAREPOINT_SOURCE = (
            DRAKE_SOURCE, TAXDOME_SOURCE, SHAREPOINT_SOURCE)
        self.LANE_DRAKE, self.LANE_TAXDOME, self.LANE_SHAREPOINT = (
            LANE_DRAKE, LANE_TAXDOME, LANE_SHAREPOINT)
        self.LINKABLE, self.REVIEWABLE = LINKABLE_CONFIDENCE, REVIEWABLE_CONFIDENCE

    # -- lane selection, by provenance, highest authority first --

    def lane_for(self, systems) -> str:
        if self.DRAKE_SOURCE in systems:
            return self.LANE_DRAKE
        if self.TAXDOME_SOURCE in systems:
            return self.LANE_TAXDOME
        return self.LANE_SHAREPOINT

    def _resolve_folder(self, folder):
        """Resolve one TaxDome folder, cached. ``resolve_folder`` scans every person, so calling it
        once per DOCUMENT instead of once per FOLDER is the difference between minutes and hours."""
        if folder not in self._folders:
            from app.importers import taxdome_drive
            try:
                self._folders[folder] = taxdome_drive.resolve_folder(self.conn, folder)
            except Exception:      # noqa: BLE001 — an unresolvable folder is data, not a crash
                self._folders[folder] = (None, None)
        return self._folders[folder]

    # -- per-document plan --

    def plan_document(self, row, systems, ocr) -> dict:
        document_id = int(row["id"])
        lane = self.lane_for(systems)
        verdict = {"document_id": document_id, "lane": lane, "confidence": None,
                   "proposed": None, "reason": None}

        if (row.get("status") or "") == "deleted":
            verdict["reason"] = "deleted"
            return verdict

        if lane == self.LANE_DRAKE:
            return self._drake(row, verdict)
        if lane == self.LANE_TAXDOME:
            return self._taxdome(row, verdict, systems, ocr)
        return self._sharepoint(row, verdict, systems, ocr)

    def _drake(self, row, verdict) -> dict:
        """Drake identity is authoritative. No corroboration is required or consulted."""
        from app.services.drake_document_owner import propose_drake_document_owner
        try:
            proposal = propose_drake_document_owner(int(row["id"]), conn=self.conn)
        except Exception as exc:      # noqa: BLE001
            verdict["reason"] = f"drake_error:{exc.__class__.__name__}"
            return verdict
        if proposal is None:
            # Not actually resolvable through Drake (already owned, or no Drake source row after all).
            verdict["reason"] = "drake_not_applicable"
            return verdict
        confidence = proposal.get("confidence")
        verdict["confidence"] = confidence
        entity_type = proposal.get("proposed_entity_type")
        entity_id = proposal.get("proposed_entity_id")
        if confidence == "HOLD":
            verdict["reason"] = proposal.get("drake_resolution") or "drake_identity_hold"
        elif confidence in self.LINKABLE and entity_type and entity_id:
            verdict["proposed"] = (entity_type, int(entity_id))
        elif confidence in self.REVIEWABLE:
            verdict["reason"] = "drake_ambiguous"
        else:
            verdict["reason"] = "drake_no_match"
        return verdict

    def _taxdome(self, row, verdict, systems, ocr) -> dict:
        """The TaxDome account/folder mapping is authoritative; content evidence is not consulted."""
        folder = (row.get("tags") or {}).get("taxdome_folder")
        if not folder:
            verdict["lane"] = self.LANE_TAXDOME
            result = self._sharepoint(row, verdict, systems, ocr)
            result["reason"] = result["reason"] or "taxdome_folder_tag_missing"
            return result
        household_id, person_id = self._resolve_folder(folder)
        if household_id is not None:
            verdict["confidence"] = "AUTHORITATIVE"
            verdict["proposed"] = ("household", int(household_id))
        elif person_id is not None:
            verdict["confidence"] = "AUTHORITATIVE"
            verdict["proposed"] = ("person", int(person_id))
        else:
            verdict["reason"] = "taxdome_folder_unresolved"
        return verdict

    def _sharepoint(self, row, verdict, systems, ocr) -> dict:
        """Evidence lane: folder path, filename, names, households, organizations, addresses, emails,
        phones, account identifiers, and OCR text when it is already available."""
        from app.services.document_owner_proposal import analyze_identity, is_tax_document
        text = (ocr or {}).get("text") or ""
        verdict["ocr_chars"] = len(text)
        verdict["ocr_status"] = (ocr or {}).get("status")
        folder = (row.get("tags") or {}).get("taxdome_folder")
        try:
            proposal = analyze_identity(
                text, row.get("original_name"), folder, self.idx,
                tax_document=is_tax_document(row, drake_source=self.DRAKE_SOURCE in systems))
        except Exception as exc:      # noqa: BLE001
            verdict["reason"] = f"evidence_error:{exc.__class__.__name__}"
            return verdict
        confidence = proposal.get("confidence")
        verdict["confidence"] = confidence
        entity_type = proposal.get("proposed_entity_type")
        entity_id = proposal.get("proposed_entity_id")
        if confidence in self.LINKABLE and entity_type and entity_id:
            verdict["proposed"] = (entity_type, int(entity_id))
        elif confidence in self.REVIEWABLE:
            verdict["reason"] = str(confidence).lower()
        elif confidence == "HOLD":
            verdict["reason"] = "identity_hold"
        else:
            verdict["reason"] = "no_match"
        return verdict


# --- inventory -------------------------------------------------------------------------------------

def _inventory(row, ocr, *, check_files: bool) -> list[str]:
    """Operational facts about a document that are true regardless of who owns it."""
    flags = []
    size = row.get("size_bytes")
    if size is not None and int(size) == 0:
        flags.append("zero_byte")
    extension = _ext(row.get("original_name"))
    status = (ocr or {}).get("status")
    if status == "timed_out":
        flags.append("timed_out")
    # Two different things, counted separately, because they need different answers: a file type that
    # carries no text is a permanent property of the document, while a row the extractor MARKED
    # unsupported is a judgement it made and can be asked about.
    if extension and extension not in _TEXT_EXT:
        flags.append("unsupported_file_type")
    if status == "unsupported":
        flags.append("unsupported_recorded_by_extractor")
    if status == "unsupported" or (extension and extension not in _TEXT_EXT):
        flags.append("unsupported")
    if status == "failed":
        flags.append("extraction_failed")
    if not ocr or status in (None, "pending", "processing"):
        flags.append("awaiting_ocr")
    if check_files:
        path = _source_path(row)
        if path is None:
            flags.append("no_source_path")
        else:
            try:
                if not path.exists():
                    flags.append("missing_source_file")
            except OSError:
                flags.append("source_unreachable")
    return flags


# --- the run ----------------------------------------------------------------------------------------

def _empty_totals() -> dict:
    return {
        "documents": 0,
        # Deleted documents are counted and then EXCLUDED from every lane total. Discovery skips
        # ``status = 'deleted'``, so the pipeline will never touch them; folding them into "unmatched"
        # would report tens of thousands of documents as outstanding work that nothing will ever do.
        "excluded_deleted": 0,
        "by_lane": Counter(),
        "high_confidence": Counter(),      # lane -> documents with an authoritative/HIGH proposal
        "ambiguous": Counter(),            # lane -> documents needing a human decision
        "conflicts": Counter(),            # lane -> proposal disagrees with the stored owner
        "unmatched": Counter(),            # lane -> no proposal and no ambiguity
        "already_owned_agreeing": Counter(),
        "reasons": Counter(),
        "inventory": Counter(),
        "confidence": Counter(),
        # File extensions across the corpus, and specifically among the documents counted as
        # unsupported — so "48,202 unsupported" can be read as "of what, exactly".
        "extensions": Counter(),
        "unsupported_extensions": Counter(),
        "ocr_status": Counter(),
        # The same outcomes split by whether OCR text was available yet. A plan taken while an OCR
        # sweep is still running understates the evidence lane, and this is how much by — measured,
        # not asserted.
        "outcome_by_text": Counter(),
    }


def _merge_counters(totals):
    return {key: (dict(value) if isinstance(value, Counter) else value)
            for key, value in totals.items()}


def _load_checkpoint(path: Path, resume: bool) -> dict | None:
    if not resume or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_checkpoint(path: Path, state: dict) -> None:
    """Checkpointing only — recovery, never a work limit. Best-effort; never fails the run."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def run(*, out_path: Path, checkpoint_path: Path, chunk_size: int, resume: bool,
        check_files: bool, database_url: str | None, progress_every: int) -> dict:
    priority = _lower_priority()
    url = _database_url(database_url)
    engine = read_only_engine(url)

    started = time.time()
    totals = _empty_totals()
    unique_owners: dict[str, set] = {"drake": set(), "taxdome": set(), "sharepoint": set()}
    after_id = 0

    checkpoint = _load_checkpoint(checkpoint_path, resume)
    if checkpoint:
        after_id = int(checkpoint.get("after_id") or 0)
        for key, value in (checkpoint.get("totals") or {}).items():
            if isinstance(value, dict):
                totals[key] = Counter(value)
            else:
                totals[key] = value
        for lane, owners in (checkpoint.get("unique_owners") or {}).items():
            unique_owners[lane] = {tuple(owner) for owner in owners}
        print(f"resuming after document id {after_id}")

    with engine.connect() as conn:
        assert_read_only(conn)
        print(f"read-only: enforced by the server   process priority: {priority}")
        corpus = conn.execute(sa.text("SELECT count(*), max(id) FROM documents")).first()
        print(f"corpus: {corpus[0]:,} documents (max id {corpus[1]})   chunk size {chunk_size:,} "
              f"(checkpointing only — there is no corpus cap)")

        planner = Planner(conn, check_files=check_files)
        print(f"match indexes built in {time.time() - started:.1f}s")

        while True:
            rows = _documents_chunk(conn, after_id, chunk_size)
            if not rows:
                break
            ids = [int(row["id"]) for row in rows]
            sources = _sources_for(conn, ids)
            ocr_rows = _ocr_for(conn, ids)

            for row in rows:
                document_id = int(row["id"])
                systems = sources.get(document_id, set())
                ocr = ocr_rows.get(document_id)

                # A deleted document is not work. Discovery's ``status IS DISTINCT FROM 'deleted'``
                # means the pipeline never sees it, so counting it anywhere but here would report
                # outstanding work that nothing will ever do.
                if (row.get("status") or "") == "deleted":
                    totals["excluded_deleted"] += 1
                    continue

                verdict = planner.plan_document(row, systems, ocr)
                lane = verdict["lane"]

                totals["documents"] += 1
                totals["by_lane"][lane] += 1
                if verdict.get("confidence"):
                    totals["confidence"][f"{lane}:{verdict['confidence']}"] += 1
                if verdict.get("reason"):
                    totals["reasons"][f"{lane}:{verdict['reason']}"] += 1
                extension = _ext(row.get("original_name")) or "(none)"
                totals["extensions"][extension] += 1
                totals["ocr_status"][str((ocr or {}).get("status"))] += 1
                flags = _inventory(row, ocr, check_files=check_files)
                for flag in flags:
                    totals["inventory"][flag] += 1
                if "unsupported" in flags:
                    totals["unsupported_extensions"][extension] += 1

                proposed = verdict.get("proposed")
                if proposed is not None:
                    if _owned(row):
                        if _conflicts(row, proposed):
                            outcome = "conflict"
                            totals["conflicts"][lane] += 1
                        else:
                            outcome = "already_owned"
                            totals["already_owned_agreeing"][lane] += 1
                    else:
                        outcome = "high_confidence"
                        totals["high_confidence"][lane] += 1
                        unique_owners[lane].add(tuple(proposed))
                elif verdict.get("reason") in ("medium", "ambiguous", "identity_hold",
                                               "drake_ambiguous", "drake_identity_hold",
                                               "frozen_identity_conflict",
                                               "taxdome_folder_unresolved"):
                    outcome = "ambiguous"
                    totals["ambiguous"][lane] += 1
                elif _owned(row):
                    outcome = "already_owned"
                    totals["already_owned_agreeing"][lane] += 1
                else:
                    outcome = "unmatched"
                    totals["unmatched"][lane] += 1

                has_text = bool((ocr or {}).get("text"))
                totals["outcome_by_text"][
                    f"{lane}:{outcome}:{'with_text' if has_text else 'no_text'}"] += 1

            after_id = ids[-1]
            _save_checkpoint(checkpoint_path, {
                "after_id": after_id, "totals": _merge_counters(totals),
                "unique_owners": {lane: [list(owner) for owner in owners]
                                  for lane, owners in unique_owners.items()},
                "updated_at": datetime.now(UTC).isoformat()})
            if progress_every and totals["documents"] % progress_every < chunk_size:
                rate = totals["documents"] / max(1e-6, time.time() - started)
                print(f"  {totals['documents']:,} planned   id<={after_id}   {rate:,.0f}/s")

    elapsed = time.time() - started
    plan = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": sa.engine.url.make_url(url).database,
        "read_only": True,
        "elapsed_seconds": round(elapsed, 1),
        "documents_planned": totals["documents"],
        "excluded_deleted": totals["excluded_deleted"],
        "by_lane": dict(totals["by_lane"]),
        "high_confidence_unmatched_documents": dict(totals["high_confidence"]),
        "high_confidence_unique_owners": {lane: len(owners) for lane, owners in unique_owners.items()},
        "ambiguous": dict(totals["ambiguous"]),
        "conflicts_with_existing_ownership": dict(totals["conflicts"]),
        "already_owned_agreeing": dict(totals["already_owned_agreeing"]),
        "unmatched": dict(totals["unmatched"]),
        "confidence_breakdown": dict(totals["confidence"]),
        "reasons": dict(totals["reasons"]),
        "inventory": dict(totals["inventory"]),
        "extensions": dict(totals["extensions"].most_common(40)),
        "unsupported_extensions": dict(totals["unsupported_extensions"].most_common(40)),
        "ocr_status": dict(totals["ocr_status"]),
        "outcome_by_text": dict(totals["outcome_by_text"]),
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        print(f"could not write {out_path}: {exc}", file=sys.stderr)
    return plan


def _print_summary(plan: dict) -> None:
    def total(section):
        return sum(plan.get(section, {}).values())

    print()
    print("=" * 78)
    print(f"OWNERSHIP PLAN — {plan['documents_planned']:,} live documents, read-only, "
          f"{plan['elapsed_seconds']}s")
    print(f"({plan.get('excluded_deleted', 0):,} deleted documents excluded — discovery skips them, "
          "so they are not outstanding work)")
    print("=" * 78)
    print(f"{'lane':<14}{'docs':>10}{'high-conf':>12}{'ambiguous':>12}{'conflict':>11}{'unmatched':>12}")
    for lane in ("drake", "taxdome", "sharepoint"):
        print(f"{lane:<14}{plan['by_lane'].get(lane, 0):>10,}"
              f"{plan['high_confidence_unmatched_documents'].get(lane, 0):>12,}"
              f"{plan['ambiguous'].get(lane, 0):>12,}"
              f"{plan['conflicts_with_existing_ownership'].get(lane, 0):>11,}"
              f"{plan['unmatched'].get(lane, 0):>12,}")
    print(f"{'TOTAL':<14}{plan['documents_planned']:>10,}"
          f"{total('high_confidence_unmatched_documents'):>12,}"
          f"{total('ambiguous'):>12,}{total('conflicts_with_existing_ownership'):>11,}"
          f"{total('unmatched'):>12,}")
    print()
    print("unique owners behind the high-confidence matches: "
          + ", ".join(f"{lane}={count:,}"
                      for lane, count in plan["high_confidence_unique_owners"].items()))
    print(f"already owned, plan agrees: {total('already_owned_agreeing'):,}")
    print()
    print("INVENTORY")
    for flag, count in sorted(plan["inventory"].items(), key=lambda kv: -kv[1]):
        print(f"  {flag:<36}{count:>10,}")
    print()
    print("OCR STATE (a plan taken mid-sweep: 'awaiting OCR' is not a failure)")
    for status, count in sorted(plan.get("ocr_status", {}).items(), key=lambda kv: -kv[1]):
        print(f"  {status:<36}{count:>10,}")
    print()
    print("TOP FILE TYPES COUNTED UNSUPPORTED")
    for extension, count in list(plan.get("unsupported_extensions", {}).items())[:12]:
        print(f"  .{extension:<35}{count:>10,}")

    by_text = plan.get("outcome_by_text", {})
    if by_text:
        print()
        print("EVIDENCE LANE: WHAT OCR TEXT IS WORTH (sharepoint lane only)")
        print(f"  {'outcome':<20}{'with OCR text':>16}{'no text yet':>14}{'match rate':>14}")
        for outcome in ("high_confidence", "ambiguous", "unmatched", "already_owned", "conflict"):
            with_text = by_text.get(f"sharepoint:{outcome}:with_text", 0)
            no_text = by_text.get(f"sharepoint:{outcome}:no_text", 0)
            print(f"  {outcome:<20}{with_text:>16,}{no_text:>14,}")
        total_with = sum(v for k, v in by_text.items()
                         if k.startswith("sharepoint:") and k.endswith(":with_text"))
        total_without = sum(v for k, v in by_text.items()
                            if k.startswith("sharepoint:") and k.endswith(":no_text"))
        hit_with = by_text.get("sharepoint:high_confidence:with_text", 0)
        hit_without = by_text.get("sharepoint:high_confidence:no_text", 0)
        if total_with and total_without:
            print(f"  {'HIGH-conf rate':<20}{hit_with / total_with:>15.2%}"
                  f"{hit_without / total_without:>14.2%}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/plan_document_ownership.py",
        description="Read-only full-corpus ownership plan. Writes nothing to the database.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK,
                        help="rows per chunk — checkpointing and recovery only, NOT a corpus cap")
    parser.add_argument("--restart", action="store_true", help="ignore any existing checkpoint")
    parser.add_argument("--no-check-files", action="store_true",
                        help="skip the per-document stat that finds missing source files")
    parser.add_argument("--database-url", default=None, help="override DATABASE_URL")
    parser.add_argument("--progress-every", type=int, default=10000)
    args = parser.parse_args(argv)

    plan = run(out_path=args.out, checkpoint_path=args.checkpoint, chunk_size=args.chunk_size,
               resume=not args.restart, check_files=not args.no_check_files,
               database_url=args.database_url, progress_every=args.progress_every)
    _print_summary(plan)
    print(f"\nplan written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
