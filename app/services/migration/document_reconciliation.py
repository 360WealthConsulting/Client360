"""Post-OCR document reconciliation — READ-ONLY reconcile job (TaxDome exit).

Reconciles the migrated SharePoint document population after the baseline OCR run: proves the OCR final
states reconcile EXACTLY to an explicit expected population, then surfaces ownership, canonical-duplicate,
source-reference-integrity, searchability, and TaxDome-exit exceptions into one finite operator-review
queue.

Reuses the migration framework verbatim — a :class:`MigrationJob` running in ``RECONCILE`` mode (never a
WRITE mode), so no ``import_jobs`` row is opened and NOTHING is written. It only issues SELECTs and emits
the standard four artifacts (manifest.json / reconciliation.csv / exceptions.csv / summary.txt) under the
run directory. It never mutates documents, document_ocr, document_sources, ownership, source_contacts, or
migration state.

Scope: documents carrying a ``document_sources`` reference for the SharePoint source system that are NOT
soft-deleted — matching the OCR candidate scope, which excludes ``documents.status = 'deleted'``. The
EXPECTED population is caller-supplied and explicit (e.g. 17448 for the current recovery), never a system
constant; a mismatch produces a FAILED invariant result (``invariant_ok=False``), not a silent success.

Schema semantics used (verified, not assumed):
  * ``document_sources.document_id`` is a NOT-NULL enforced FK — a "dangling" ref is impossible, so an
    orphan source reference is one whose document is soft-deleted/archived (unusable canonically).
  * ``document_sources`` has UNIQUE(document_id, source_system, source_uri) — so a duplicate source
    IDENTIFIER means the same (source_system, external_id) or (source_system, source_uri) mapped to more
    than one DISTINCT canonical document.
  * Universal search reads ``document_ocr.text`` directly for ``status='completed'`` (no separate document
    search index), so "search_missing" = completed OCR with empty/absent text — there is no index drift.
  * ``password_required`` is split from ``unsupported`` via the Phase-1D ``last_error`` prefix convention
    (:data:`app.services.ocr_exceptions.ENCRYPTED_PDF_ERROR_CODE`), NOT a distinct status value.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.db import document_ocr, documents, engine, metadata
from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.ocr_exceptions import ENCRYPTED_PDF_ERROR_CODE

# document_sources / source_contacts are reflected but not module-level attributes of app.db.
document_sources = metadata.tables["document_sources"]
source_contacts = metadata.tables["source_contacts"]

# 'password_required:' — the machine-detectable last_error prefix written for encrypted PDFs (Phase 1D).
_PW_PREFIX = f"{ENCRYPTED_PDF_ERROR_CODE}:"

# Categories recorded in exceptions.csv for completeness but EXPECTED / non-actionable — they must NOT
# inflate the operator-review total. Genuine 'unsupported' (docx/xlsx/etc.) is handled natively by design
# and needs no remediation.
_INFORMATIONAL_CATEGORIES = frozenset({"unsupported"})

# Mutually exclusive OCR final buckets. Every scoped document lands in EXACTLY one (status is single-valued
# per document via uq_document_ocr_document; password_required is a sub-branch of unsupported).
_OCR_BUCKETS = ("completed", "failed", "timed_out", "unsupported", "password_required", "skipped", "pending")

_PENDING_STATUSES = frozenset({"pending", "processing"})
_PASSTHROUGH_STATUSES = frozenset({"completed", "failed", "timed_out", "skipped"})


def _bucket_for(ocr_status, last_error) -> str:
    """Map one document's OCR row to exactly one final bucket. No document is counted twice."""
    if ocr_status is None or ocr_status in _PENDING_STATUSES:
        return "pending"                                   # no OCR row yet, or mid-flight → unprocessed
    if ocr_status in _PASSTHROUGH_STATUSES:
        return ocr_status
    if ocr_status == "unsupported":
        return "password_required" if (last_error or "").startswith(_PW_PREFIX) else "unsupported"
    return "pending"                                       # defensive: unknown status (CHECK should prevent)


def _exc(category, **fields) -> dict:
    """One finite-queue exception row: a stable machine-readable ``category`` plus enough identifiers and
    evidence for an operator to investigate without another database-discovery step."""
    return {"category": category, **fields}


def _rec(section, metric, value) -> dict:
    return {"section": section, "metric": metric, "value": value}


class DocumentReconciliationJob(MigrationJob):
    """Read-only post-OCR document reconciliation. RECONCILE mode only — the framework guarantees no
    ``import_jobs`` row and no writes for non-WRITE modes; every query below is a plain SELECT."""

    source_system = "document_reconciliation"
    supported_modes = frozenset({Mode.RECONCILE})

    def _reconcile(self, *, sharepoint_source: str = "SharePoint", taxdome_source: str = "TaxDome",
                   expected_population: int | None = None, owner_proposal_limit: int = 0,
                   **_opts) -> Outcome:
        counts: dict = {}
        exceptions: list[dict] = []
        reconciliation: list[dict] = []
        notes: list[str] = [
            "READ-ONLY reconcile: no documents / document_ocr / document_sources / ownership / "
            "source_contacts / migration state were modified.",
            f"Scope: documents with a document_sources ref for source_system='{sharepoint_source}' and "
            "documents.status != 'deleted' (matches the OCR candidate scope).",
        ]

        with engine.connect() as conn:      # plain connection — SELECT only, never committed
            # -- scoped population + one OCR row per document -----------------------------------------
            sp_doc_ids = select(document_sources.c.document_id).where(
                document_sources.c.source_system == sharepoint_source)
            scoped_stmt = (
                select(
                    documents.c.id, documents.c.original_name, documents.c.sha256,
                    documents.c.person_id, documents.c.household_id, documents.c.organization_id,
                    documents.c.storage_provider,
                    document_ocr.c.status.label("ocr_status"), document_ocr.c.last_error,
                    document_ocr.c.char_count,
                    and_(document_ocr.c.text.isnot(None),
                         func.length(func.btrim(document_ocr.c.text)) > 0).label("has_text"))
                .select_from(documents.outerjoin(
                    document_ocr, document_ocr.c.document_id == documents.c.id))
                .where(documents.c.id.in_(sp_doc_ids), documents.c.status != "deleted")
                .order_by(documents.c.id))       # stable order → deterministic sample + exception ordering
            rows = conn.execute(scoped_stmt).mappings().all()
            actual = len(rows)

            # -- 1. OCR final-state reconciliation ---------------------------------------------------
            buckets = dict.fromkeys(_OCR_BUCKETS, 0)
            for r in rows:
                buckets[_bucket_for(r["ocr_status"], r["last_error"])] += 1
                st = r["ocr_status"]
                name = r["original_name"]
                if st == "failed":
                    exceptions.append(_exc("OCR_failed", document_id=r["id"], original_name=name,
                                           detail=(r["last_error"] or "")[:300]))
                elif st == "timed_out":
                    exceptions.append(_exc("OCR_timed_out", document_id=r["id"], original_name=name,
                                           detail=(r["last_error"] or "")[:300]))
                elif st == "unsupported" and (r["last_error"] or "").startswith(_PW_PREFIX):
                    exceptions.append(_exc("password_required", document_id=r["id"], original_name=name,
                                           detail=(r["last_error"] or "")[:300]))
                elif st == "unsupported":
                    exceptions.append(_exc("unsupported", document_id=r["id"], original_name=name,
                                           detail=(r["last_error"] or "")[:120]))
                if st == "completed" and not r["has_text"]:
                    exceptions.append(_exc("search_missing", document_id=r["id"], original_name=name,
                                           detail="OCR completed but stored text is empty/absent"))

            bucket_total = sum(buckets.values())
            counts.update({f"ocr_{k}": v for k, v in buckets.items()})
            counts["scoped_population"] = actual
            counts["ocr_bucket_total"] = bucket_total
            counts["expected_population"] = expected_population
            for k, v in buckets.items():
                reconciliation.append(_rec("ocr", k, v))

            # Invariant A: the buckets must reconcile to the scoped population (no double counting / gaps).
            no_double_count = bucket_total == actual
            # Invariant B: the scoped population must equal the explicit expected count (if supplied).
            if expected_population is None:
                pop_ok = True
                counts["population_difference"] = None
                notes.append("expected_population not supplied — population invariant NOT checked.")
            else:
                diff = actual - int(expected_population)
                counts["population_difference"] = diff
                pop_ok = diff == 0
                if not pop_ok:
                    exceptions.append(_exc("population_mismatch", document_id="",
                                           detail=f"expected={expected_population} actual={actual} "
                                                  f"difference={diff}"))
            invariant_ok = no_double_count and pop_ok
            counts["invariant_ok"] = invariant_ok
            counts["reconciliation_status"] = (
                "PASS" if invariant_ok else
                ("FAILED_BUCKET_DOUBLE_COUNT" if not no_double_count else "FAILED_POPULATION_MISMATCH"))

            # -- 2. Ownership (cheap SQL classifications over the scoped set) --------------------------
            owner_missing = 0
            multiple_owner = 0
            for r in rows:
                owned = [f for f in ("person_id", "household_id", "organization_id") if r[f] is not None]
                if not owned:
                    owner_missing += 1
                    exceptions.append(_exc("owner_missing", document_id=r["id"],
                                           original_name=r["original_name"],
                                           detail="no person/household/organization owner set"))
                elif len(owned) > 1:
                    multiple_owner += 1
                    exceptions.append(_exc("multiple_owner_fields", document_id=r["id"],
                                           original_name=r["original_name"],
                                           detail="owner fields set: " + ",".join(owned)))
            counts["owner_missing"] = owner_missing
            counts["multiple_owner_fields"] = multiple_owner

            # -- 2b. Proposal-based ownership (BOUNDED + opt-in; reuses document_owner_proposal) -------
            # Default off (limit 0): the deep pass reads file bytes per document, so it is bounded and
            # explicit to avoid competing with a running OCR job for disk. It NEVER writes and NEVER
            # triggers live OCR (ocr=False → cached/native text only).
            analyzed = ambiguous = low_conf = wrong_owner = high_auto = 0
            if owner_proposal_limit and owner_proposal_limit > 0:
                from app.services.document_owner_proposal import (
                    build_match_indexes,
                    propose_document_owner,
                )
                idx = build_match_indexes(conn)
                for r in rows[:owner_proposal_limit]:
                    try:
                        prop = propose_document_owner(r["id"], conn=conn, idx=idx, ocr=False)
                    except Exception:  # noqa: BLE001 — a proposal failure must not fail the read-only report
                        continue
                    analyzed += 1
                    conf = (prop or {}).get("confidence")
                    assigned = [f for f in ("person_id", "household_id", "organization_id")
                                if r[f] is not None]
                    if not assigned:
                        if conf == "AMBIGUOUS":
                            ambiguous += 1
                            exceptions.append(_exc("owner_ambiguous", document_id=r["id"],
                                                   original_name=r["original_name"],
                                                   detail=f"competing={len(prop.get('competing') or [])}"))
                        elif conf == "MEDIUM":
                            low_conf += 1
                            exceptions.append(_exc("owner_low_confidence", document_id=r["id"],
                                                   original_name=r["original_name"],
                                                   detail=f"proposed={prop.get('proposed_name')}"))
                        elif conf == "HIGH":
                            high_auto += 1                 # auto-linkable via existing link_documents apply
                    elif conf == "HIGH" and prop.get("proposed_entity_id") is not None:
                        # Assigned, but a HIGH-confidence proposal disagrees with the current owner.
                        if (prop.get("proposed_entity_type") == "person"
                                and prop.get("proposed_entity_id") != r["person_id"]):
                            wrong_owner += 1
                            exceptions.append(_exc("possible_wrong_owner", document_id=r["id"],
                                                   original_name=r["original_name"],
                                                   detail=f"assigned_person={r['person_id']} "
                                                          f"proposed_person={prop.get('proposed_entity_id')}"))
            counts.update({"owner_proposal_analyzed": analyzed, "owner_ambiguous": ambiguous,
                           "owner_low_confidence": low_conf, "possible_wrong_owner": wrong_owner,
                           "owner_high_confidence_auto_linkable": high_auto})

            # -- 3. Canonical duplicates (by SHA-256, over the scoped active set) ----------------------
            by_hash: dict[str, list[dict]] = {}
            for r in rows:
                if r["sha256"]:
                    by_hash.setdefault(r["sha256"], []).append(r)
            dup_same_owner = dup_cross_owner = 0
            for sha, members in by_hash.items():
                if len(members) < 2:
                    continue
                owner_keys = {(m["person_id"], m["household_id"], m["organization_id"]) for m in members}
                ids = [m["id"] for m in members]
                if len(owner_keys) == 1:
                    dup_same_owner += 1
                    exceptions.append(_exc("duplicate_canonical", document_id=ids[0],
                                           detail=f"sha256={sha[:16]}… members={ids}"))
                else:
                    dup_cross_owner += 1
                    exceptions.append(_exc("cross_owner_duplicate", document_id=ids[0],
                                           detail=f"sha256={sha[:16]}… members={ids} (differing owners)"))
            counts["duplicate_canonical_groups"] = dup_same_owner
            counts["cross_owner_duplicate_groups"] = dup_cross_owner

            # Legitimate reuse: many SharePoint refs → one canonical. Reported as INFO, never an exception.
            multi_ref = conn.execute(
                select(func.count()).select_from(
                    select(document_sources.c.document_id)
                    .where(document_sources.c.source_system == sharepoint_source)
                    .group_by(document_sources.c.document_id)
                    .having(func.count() > 1).subquery())).scalar() or 0
            counts["documents_with_multiple_sharepoint_refs"] = int(multi_ref)

            # -- 4. Source-reference integrity --------------------------------------------------------
            # orphan_source: a source ref whose document is soft-deleted/archived (unusable canonically).
            orphan_rows = conn.execute(
                select(document_sources.c.id, document_sources.c.document_id,
                       document_sources.c.source_system, document_sources.c.source_uri,
                       documents.c.status, documents.c.archived)
                .select_from(document_sources.join(
                    documents, documents.c.id == document_sources.c.document_id))
                .where(or_(documents.c.status == "deleted",
                           documents.c.deleted_at.isnot(None),
                           documents.c.archived.is_(True)))
                .order_by(document_sources.c.id)).mappings().all()
            for o in orphan_rows:
                exceptions.append(_exc("orphan_source", source_id=o["id"], document_id=o["document_id"],
                                       source_system=o["source_system"], source_uri=o["source_uri"],
                                       detail=f"document status={o['status']} archived={o['archived']}"))
            counts["orphan_source"] = len(orphan_rows)

            # missing_source: a document that looks SharePoint-provisioned but has no SharePoint source ref.
            missing_rows = conn.execute(
                select(documents.c.id, documents.c.original_name, documents.c.storage_provider)
                .where(documents.c.storage_provider.ilike(f"%{sharepoint_source}%"),
                       documents.c.status != "deleted",
                       documents.c.id.notin_(sp_doc_ids))
                .order_by(documents.c.id)).mappings().all()
            for m in missing_rows:
                exceptions.append(_exc("missing_source", document_id=m["id"],
                                       original_name=m["original_name"],
                                       detail=f"storage_provider={m['storage_provider']} but no "
                                              f"{sharepoint_source} document_sources ref"))
            counts["missing_source"] = len(missing_rows)

            # duplicate_source: same (source_system, external_id) or (source_system, source_uri) mapped to
            # MORE THAN ONE distinct canonical document (the UNIQUE constraint already forbids exact dupes).
            dup_src = 0
            for keycol in (document_sources.c.source_external_id, document_sources.c.source_uri):
                grp = (select(document_sources.c.source_system, keycol.label("k"),
                              func.count(func.distinct(document_sources.c.document_id)).label("n"))
                       .where(keycol.isnot(None))
                       .group_by(document_sources.c.source_system, keycol)
                       .having(func.count(func.distinct(document_sources.c.document_id)) > 1)
                       .order_by(document_sources.c.source_system, keycol))
                for g in conn.execute(grp).mappings().all():
                    dup_src += 1
                    exceptions.append(_exc("duplicate_source", source_system=g["source_system"],
                                           detail=f"key={str(g['k'])[:120]} maps to {g['n']} documents"))
            counts["duplicate_source"] = dup_src

            # source counts by source_system (coverage) — SharePoint / Drake / TaxDome and any others.
            for g in conn.execute(
                    select(document_sources.c.source_system,
                           func.count(func.distinct(document_sources.c.document_id)).label("n"))
                    .group_by(document_sources.c.source_system)
                    .order_by(document_sources.c.source_system)).mappings().all():
                counts[f"source_docs_{g['source_system']}"] = int(g["n"])
                reconciliation.append(_rec("source_coverage", g["source_system"], int(g["n"])))

            # -- 5. Searchability (derived directly from document_ocr.text; no separate index) --------
            searchable = sum(1 for r in rows if r["ocr_status"] == "completed" and r["has_text"])
            search_missing = buckets["completed"] - searchable
            counts["search_searchable"] = searchable
            counts["search_missing"] = search_missing
            notes.append("Searchability derived from document_ocr.text (universal_search reads it "
                         "directly for status='completed'); there is no separate document search index.")
            notes.append("OCR 'skipped' is a defined status the runner does NOT persist — a cached/reused "
                         "document keeps status='completed' — so the 'skipped' bucket is normally 0 and "
                         "cached documents count as completed.")

            # -- 6. TaxDome exit ----------------------------------------------------------------------
            has_td = select(document_sources.c.document_id).where(
                document_sources.c.source_system == taxdome_source)
            has_non_td = select(document_sources.c.document_id).where(
                document_sources.c.source_system != taxdome_source)
            td_only = conn.execute(
                select(documents.c.id, documents.c.original_name,
                       documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                .where(documents.c.status != "deleted",
                       documents.c.id.in_(has_td), documents.c.id.notin_(has_non_td))
                .order_by(documents.c.id)).mappings().all()
            td_owner_unresolved = 0
            for t in td_only:
                exceptions.append(_exc("taxdome_only_candidate", document_id=t["id"],
                                       original_name=t["original_name"],
                                       detail="only source reference is TaxDome (candidate; verify before "
                                              "declaring TaxDome-only)"))
                if t["person_id"] is None and t["household_id"] is None and t["organization_id"] is None:
                    td_owner_unresolved += 1
                    exceptions.append(_exc("taxdome_owner_unresolved", document_id=t["id"],
                                           original_name=t["original_name"],
                                           detail="TaxDome-derived document with no canonical owner"))
            counts["taxdome_only_candidate"] = len(td_only)
            counts["taxdome_owner_unresolved"] = td_owner_unresolved
            # Client-level TaxDome representation is NOT re-derived here (people has no source_contact FK);
            # canonical_population owns that determination. Report an informational source_contacts count.
            td_contacts = conn.execute(
                select(func.count()).select_from(source_contacts)
                .where(source_contacts.c.source_system == taxdome_source)).scalar() or 0
            counts["taxdome_source_contacts_total"] = int(td_contacts)
            notes.append("Client-level 'in TaxDome but not canonical' is NOT asserted here — people has no "
                         "source_contact FK; defer to canonical_population for that determination.")

        # -- headline totals -------------------------------------------------------------------------
        ownership_exceptions = owner_missing + multiple_owner + ambiguous + low_conf + wrong_owner
        duplicate_exceptions = dup_same_owner + dup_cross_owner
        source_exceptions = counts["orphan_source"] + counts["missing_source"] + counts["duplicate_source"]
        taxdome_exceptions = counts["taxdome_only_candidate"] + counts["taxdome_owner_unresolved"]
        counts["ownership_exceptions"] = ownership_exceptions
        counts["duplicate_exceptions"] = duplicate_exceptions
        counts["source_integrity_exceptions"] = source_exceptions
        counts["searchability_exceptions"] = search_missing
        counts["taxdome_exit_exceptions"] = taxdome_exceptions
        # Actionable operator-review total EXCLUDES expected/non-actionable categories (e.g. genuine
        # 'unsupported'), which are still written to exceptions.csv and counted separately.
        counts["informational_exceptions"] = sum(
            1 for e in exceptions if e["category"] in _INFORMATIONAL_CATEGORIES)
        counts["total_operator_review_exceptions"] = sum(
            1 for e in exceptions if e["category"] not in _INFORMATIONAL_CATEGORIES)
        counts["total_exception_rows"] = len(exceptions)

        reconciliation.insert(0, _rec("population", "expected", expected_population))
        reconciliation.insert(1, _rec("population", "actual", actual))
        reconciliation.insert(2, _rec("population", "difference", counts["population_difference"]))
        reconciliation.insert(3, _rec("population", "invariant_ok", counts["invariant_ok"]))

        if not counts["invariant_ok"]:
            notes.insert(0, f"INVARIANT FAILED: {counts['reconciliation_status']} — this reconciliation "
                            "is NOT a pass; resolve the population/bucket discrepancy before cutover.")
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=reconciliation, notes=notes)


def reconcile_documents(*, sharepoint_source: str = "SharePoint", taxdome_source: str = "TaxDome",
                        expected_population: int | None = None, owner_proposal_limit: int = 0,
                        config=None):
    """Convenience entry: run the read-only reconcile and return the :class:`MigrationResult`
    (artifacts are written under the run directory). Never writes to Client360."""
    job = DocumentReconciliationJob(config)
    return job.run(Mode.RECONCILE, sharepoint_source=sharepoint_source, taxdome_source=taxdome_source,
                   expected_population=expected_population, owner_proposal_limit=owner_proposal_limit)
