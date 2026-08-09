"""Repository Relocation Service — the permanent, source-agnostic storage-relocation engine.

Relocates EXISTING canonical documents' bytes into the Client360 repository (``D:\\Client360\\Content``)
WITHOUT creating, duplicating, or re-importing any row. It reads the ``documents`` table (any origin —
existing Client360 uploads, TaxDome, SharePoint, OneDrive, Wealthbox, future adapters), plans a
human-readable destination via the Naming service, and — in a later phase — copies + verifies (SHA-256) +
re-points ``storage_uri``. ``documents.id`` and every ``document_sources`` row are preserved; nothing is
ever duplicated and the legacy source is never deleted here.

THIS PHASE IS READ-ONLY: only ``preview`` and ``reconcile`` are enabled. ``apply`` and ``rollback`` are
declared but fail-closed (refused before any database access) until the preview is reviewed against
production — exactly the discipline used for the Enterprise Ingestion Platform.
"""
from __future__ import annotations

import csv
import hashlib
import os

from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.canonical_repair import RepairGuardError
from app.services.migration.naming import RepositoryNaming
from app.services.migration.storage import LocalFilesystemStorage, StorageService

#: Areas that have a canonical owner (person/household/organization). Firm/Unfiled is NOT relocated here.
_OWNED_AREAS = ("Clients", "Households", "Businesses")


def _norm(path: str | None) -> str:
    """Normalize a path for prefix/equality comparison (case + separators)."""
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _under(path: str, root: str) -> bool:
    """True if ``path`` is inside ``root`` (both already normalized)."""
    if not path or not root:
        return False
    return path == root or path.startswith(root + os.sep)


def _source_system(tags) -> str:
    return tags.get("source_system", "") if isinstance(tags, dict) else ""


def load_approved_relocation(path):
    """Load the FROZEN approved relocation scope from a relocation preview's reconciliation.csv (pass the
    run directory or the csv). Keeps ONLY owned areas (Clients/Households/Businesses) in the
    ``needs_relocation`` state — Firm/unfiled, already-in-repository, and missing_source are excluded.
    Returns {document_id: (area, approved_source_uri, approved_destination)} — freezing ids AND paths."""
    csvpath = path if str(path).endswith(".csv") else os.path.join(path, "reconciliation.csv")
    out: dict[int, tuple[str, str, str]] = {}
    with open(csvpath, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            did = (r.get("document_id") or "").strip()
            if (r.get("area") in _OWNED_AREAS and r.get("state") == "needs_relocation" and did.isdigit()):
                out[int(did)] = (r["area"], r.get("current_storage_uri") or "",
                                 r.get("proposed_destination") or "")
    return out


class RepositoryRelocationJob(MigrationJob):
    """Relocate existing canonical documents into the repository. Source-agnostic; read-only this phase."""

    source_system = "Repository Relocation"
    #: APPLY relocates owned documents only, frozen to an approved manifest (guarded). ROLLBACK stays
    #: disabled until built.
    supported_modes = frozenset({Mode.PREVIEW, Mode.RECONCILE, Mode.APPLY})

    def __init__(self, config=None, *, storage: StorageService | None = None, naming: RepositoryNaming | None = None):
        super().__init__(config)
        self.storage = storage or LocalFilesystemStorage()
        self.naming = naming or RepositoryNaming()

    # -- data access (read-only) ----------------------------------------------
    def _load(self):
        from sqlalchemy import select

        from app.db import engine, households, metadata, people
        documents = metadata.tables["documents"]
        # documents.organization_id is a FK to relationship_entities (the canonical entity registry for
        # businesses/trusts/etc.) — NOT a separate "organizations" table. Resolve org display names from
        # there so business/trust documents use their real canonical name, not a generic "Organization" id.
        rel = metadata.tables["relationship_entities"]
        with engine.connect() as conn:
            people_map = {}
            for r in conn.execute(select(people.c.id, people.c.first_name, people.c.last_name,
                                         people.c.full_name)).mappings():
                last, first = (r["last_name"] or "").strip(), (r["first_name"] or "").strip()
                people_map[r["id"]] = f"{last}, {first}".strip(", ") or (r["full_name"] or f"Person {r['id']}")
            hh_map = {r["id"]: r["name"] for r in conn.execute(select(households.c.id, households.c.name)).mappings()}
            org_map = {r["id"]: r["name"]
                       for r in conn.execute(select(rel.c.id, rel.c.name)).mappings() if r["name"]}
            cols = [documents.c.id, documents.c.person_id, documents.c.household_id, documents.c.organization_id,
                    documents.c.original_name, documents.c.classification, documents.c.category,
                    documents.c.effective_date, documents.c.storage_uri, documents.c.size_bytes,
                    documents.c.sha256, documents.c.tags, documents.c.status]
            docs = [dict(m) for m in
                    conn.execute(select(*cols).where(documents.c.status != "deleted")).mappings()]
        return docs, people_map, hh_map, org_map

    # -- preview (read-only) --------------------------------------------------
    def _preview(self, **_opts) -> Outcome:
        docs, people_map, hh_map, org_map = self._load()
        dest_root = self.config.migration_dest_root
        dest_norm = _norm(str(dest_root))

        rows: list[dict] = []
        exceptions: list[dict] = []
        by_area: dict[str, int] = {}
        dest_seen: dict[str, int] = {}
        total_bytes = already = needs = missing = placeholders = 0

        for d in docs:
            placed = self.naming.plan(d, people=people_map, households=hh_map, organizations=org_map)
            dest_full = placed.full(dest_root)
            dest_seen[_norm(dest_full)] = dest_seen.get(_norm(dest_full), 0) + 1
            by_area[placed.area] = by_area.get(placed.area, 0) + 1

            src = d.get("storage_uri")
            if not src:
                state = "missing_source"
                missing += 1
                exceptions.append({"document_id": d["id"], "reason": "no storage_uri on document"})
            else:
                info = self.storage.stat(src)
                if not info.exists:
                    state = "missing_source"
                    missing += 1
                    exceptions.append({"document_id": d["id"], "reason": f"source not found: {src}"})
                else:
                    total_bytes += info.size
                    if info.is_placeholder:
                        placeholders += 1
                        exceptions.append({"document_id": d["id"], "reason": "cloud-only placeholder (must hydrate)"})
                    if _under(_norm(src), dest_norm):
                        state = "already_in_repository"
                        already += 1
                    else:
                        state = "needs_relocation"
                        needs += 1

            rows.append({
                "document_id": d["id"], "source_system": _source_system(d.get("tags")),
                "state": state, "area": placed.area, "entity": placed.entity,
                "category": placed.category, "year": placed.year, "filename": placed.filename,
                "current_storage_uri": src or "", "proposed_destination": dest_full,
                "size_bytes": d.get("size_bytes") or 0,
            })

        collisions = sum(v - 1 for v in dest_seen.values() if v > 1)
        usage = dest_root if dest_root.exists() else dest_root.anchor or "."
        free, _total = self.storage.free_and_total(str(usage))
        counts = {
            "destination_root": str(dest_root), "dest_root_exists": dest_root.exists(),
            "documents_total": len(docs), "needs_relocation": needs,
            "already_in_repository": already, "missing_source": missing,
            "cloud_only_placeholders": placeholders, "destination_collisions": collisions,
            "relocatable_bytes": total_bytes, "relocatable_gb": round(total_bytes / (1024 ** 3), 2),
            "by_area": by_area,
            "dest_free_gb": round(free / (1024 ** 3), 2) if free is not None else None,
            "fits_with_10pct_margin": (free > total_bytes * 1.1) if free is not None else None,
        }
        notes = [
            "PREVIEW ONLY — no bytes copied, no storage_uri changed, no documents/document_sources rows "
            "modified, no import_jobs row opened.",
            "Source-agnostic: every documents row is planned by canonical fields alone, regardless of origin.",
            "Uniqueness is guaranteed by the canonical document id embedded in each filename "
            "(destination_collisions must be 0).",
            "cloud_only_placeholders>0 marks documents that must be hydrated before an apply can read them.",
            "APPLY and ROLLBACK are disabled this phase (fail-closed) until this preview is reviewed.",
        ]
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=rows, notes=notes)

    # -- reconcile (read-only) ------------------------------------------------
    def _reconcile(self, **_opts) -> Outcome:
        docs, _p, _h, _o = self._load()
        dest_norm = _norm(str(self.config.migration_dest_root))

        rows: list[dict] = []
        exceptions: list[dict] = []
        inside = outside = missing = size_ok = size_bad = 0

        for d in docs:
            src = d.get("storage_uri")
            if not src:
                missing += 1
                exceptions.append({"document_id": d["id"], "reason": "no storage_uri"})
                continue
            info = self.storage.stat(src)
            if not info.exists:
                missing += 1
                exceptions.append({"document_id": d["id"], "reason": f"source not found: {src}"})
                continue
            in_repo = _under(_norm(src), dest_norm)
            inside += 1 if in_repo else 0
            outside += 0 if in_repo else 1
            size_match = info.size == (d.get("size_bytes") or -1)
            if size_match:
                size_ok += 1
            else:
                size_bad += 1
                exceptions.append({"document_id": d["id"],
                                   "reason": f"size mismatch: db={d.get('size_bytes')} file={info.size}"})
            rows.append({"document_id": d["id"], "in_repository": in_repo,
                         "storage_uri": src, "size_expected": d.get("size_bytes") or 0,
                         "size_actual": info.size})

        complete = (outside == 0 and missing == 0 and size_bad == 0 and len(docs) > 0)
        counts = {
            "destination_root": str(self.config.migration_dest_root),
            "documents_total": len(docs), "in_repository": inside, "outside_repository": outside,
            "missing_source": missing, "size_verified": size_ok, "size_mismatch": size_bad,
            "relocation_complete": complete,
        }
        notes = [
            "RECONCILE (read-only): compares each document's storage_uri location + size against the DB.",
            "SHA-256 content verification is performed by the APPLY-phase reconcile (which reads bytes); "
            "this phase verifies placement + size only.",
            "Before any apply, in_repository is expected to be 0 (nothing relocated yet) — this is the "
            "baseline, not a failure.",
        ]
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=rows, notes=notes)

    # -- APPLY (guarded, idempotent, copy -> verify -> repoint; never deletes source) ------------------
    def _plan_apply(self, docs, people_map, hh_map, org_map, approved):
        """Classify the frozen approved set into pending / applied / drift against current state.
        Pure over the loaded snapshot — no writes, no byte reads."""
        dest_root = self.config.migration_dest_root
        dest_norm = _norm(str(dest_root))
        by_id = {d["id"]: d for d in docs}
        pending, applied, drift = [], [], []
        from collections import Counter
        applied_by_area: Counter = Counter()
        for did, (area, appr_src, appr_dest) in approved.items():
            d = by_id.get(did)
            if d is None:
                drift.append((did, "missing_document")); continue
            placed = self.naming.plan(d, people=people_map, households=hh_map, organizations=org_map)
            if placed.area not in _OWNED_AREAS:
                drift.append((did, "owner_removed")); continue          # canonical owner gone -> out of scope
            dest = placed.full(dest_root)
            if _norm(dest) != _norm(appr_dest):
                drift.append((did, "destination_drift")); continue      # planned dest changed since approval
            src = d.get("storage_uri")
            # idempotent: already relocated to dest (storage_uri repointed) -> applied
            if src and _norm(src) == _norm(dest) and _under(_norm(src), dest_norm):
                applied.append((did, area, src, dest)); applied_by_area[area] += 1; continue
            if appr_src and _norm(src) != _norm(appr_src):
                drift.append((did, "source_drift")); continue           # source path changed since approval
            pending.append((did, area, src, dest, d.get("sha256"), d.get("size_bytes")))
        return pending, applied, applied_by_area, drift

    def _apply(self, job_id=None, approved=None, confirm=False, backup=None, expect=None, **_opts) -> Outcome:
        if not confirm:
            raise RepairGuardError("APPLY requires explicit confirm=True.")
        if not backup or not os.path.isfile(backup) or os.path.getsize(backup) == 0:
            raise RepairGuardError(f"APPLY requires a verified non-empty DB backup file (got: {backup!r}).")
        if not approved:
            raise RepairGuardError("APPLY requires an --approved relocation manifest (frozen scope).")

        docs, people_map, hh_map, org_map = self._load()
        pending, applied, applied_by_area, drift = self._plan_apply(docs, people_map, hh_map, org_map, approved)

        # pre-write guard (read-only): missing source, cloud-only placeholder, or a destination that
        # already exists but is NOT this document's verified copy (would be overwritten) -> fail closed.
        blockers = []
        for did, _area, src, dest, sha, _size in pending:
            sinfo = self.storage.stat(src) if src else None
            if not src or sinfo is None or not sinfo.exists:
                blockers.append((did, "missing_source")); continue
            if sinfo.is_placeholder:
                blockers.append((did, "cloud_placeholder")); continue
            dinfo = self.storage.stat(dest)
            if dinfo.exists:
                try:
                    same = hashlib.sha256(self.storage.read(dest)).hexdigest() == sha
                except OSError:
                    same = False
                if not same:
                    blockers.append((did, "destination_collision"))

        if drift or blockers:
            raise RepairGuardError(
                f"aborted before any write — drift={len(drift)} blockers={len(blockers)} "
                f"(examples: {(drift + blockers)[:5]}). Frozen scope must match exactly.")

        from collections import Counter
        guard: Counter = Counter(applied_by_area)
        for _did, area, *_ in pending:
            guard[area] += 1
        guard_counts = {a: guard.get(a, 0) for a in _OWNED_AREAS}
        if expect is not None and guard_counts != expect:
            raise RepairGuardError(f"count drift — approved {expect} but live {guard_counts}; aborted before any write.")

        # ---- writes: copy -> verify -> repoint storage_uri, one document per transaction; source retained
        from sqlalchemy import update

        from app.db import engine, metadata
        documents = metadata.tables["documents"]
        rows: list[dict] = []
        moved = 0
        for did, area, src, dest, sha, size in pending:
            data = self.storage.read(src)                                # source bytes
            if hashlib.sha256(data).hexdigest() != sha or len(data) != (size or len(data)):
                raise RepairGuardError(f"source integrity failure for document {did} — aborted; "
                                       f"{moved} already relocated (verified) and left in place.")
            self.storage.write(dest, data)                              # atomic temp+rename
            v = self.storage.stat(dest)
            if not v.exists or v.size != len(data) or hashlib.sha256(self.storage.read(dest)).hexdigest() != sha:
                raise RepairGuardError(f"destination verification FAILED for document {did} — storage_uri "
                                       f"NOT repointed; source retained. {moved} prior moves verified.")
            rel = os.path.relpath(dest, str(self.config.migration_dest_root))
            with engine.begin() as conn:                               # repoint ONLY after verified copy
                conn.execute(update(documents).where(documents.c.id == did).values(
                    storage_uri=dest, storage_path=rel, storage_provider="Client360 Repository"))
            moved += 1
            rows.append({"document_id": did, "area": area, "old_storage_uri": src, "new_storage_uri": dest,
                         "sha256_verified": sha, "action": "copied_verified_and_repointed"})
        for did, area, src, dest in applied:
            rows.append({"document_id": did, "area": area, "old_storage_uri": src, "new_storage_uri": dest,
                         "sha256_verified": "", "action": "skipped_already_relocated"})

        counts = dict(guard_counts)
        counts["total"] = sum(guard_counts.values())
        counts["rows_inserted"] = moved                                # documents relocated + repointed
        counts["skipped_already_relocated"] = len(applied)
        notes = [
            "APPLY complete: owned documents copied to D:\\Client360\\Content, SHA-256 + size verified, then "
            "storage_uri repointed. Source bytes RETAINED (not deleted) as rollback; document_sources and "
            "all provenance preserved; Firm/unfiled and missing_source excluded.",
            "Rollback: restore the pre-apply DB backup (storage_uri) — source files were never removed.",
        ]
        return Outcome(counts=counts, exceptions=[], reconciliation=rows, notes=notes)
