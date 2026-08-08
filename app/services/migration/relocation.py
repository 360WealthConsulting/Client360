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

import os

from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.naming import RepositoryNaming
from app.services.migration.storage import LocalFilesystemStorage, StorageService


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


class RepositoryRelocationJob(MigrationJob):
    """Relocate existing canonical documents into the repository. Source-agnostic; read-only this phase."""

    source_system = "Repository Relocation"
    #: Fail-closed: apply/rollback are declared in the pipeline but disabled until the preview is approved.
    supported_modes = frozenset({Mode.PREVIEW, Mode.RECONCILE})

    def __init__(self, config=None, *, storage: StorageService | None = None, naming: RepositoryNaming | None = None):
        super().__init__(config)
        self.storage = storage or LocalFilesystemStorage()
        self.naming = naming or RepositoryNaming()

    # -- data access (read-only) ----------------------------------------------
    def _load(self):
        from sqlalchemy import select

        from app.db import engine, households, metadata, people
        documents = metadata.tables["documents"]
        orgs = metadata.tables.get("organizations")
        with engine.connect() as conn:
            people_map = {}
            for r in conn.execute(select(people.c.id, people.c.first_name, people.c.last_name,
                                         people.c.full_name)).mappings():
                last, first = (r["last_name"] or "").strip(), (r["first_name"] or "").strip()
                people_map[r["id"]] = f"{last}, {first}".strip(", ") or (r["full_name"] or f"Person {r['id']}")
            hh_map = {r["id"]: r["name"] for r in conn.execute(select(households.c.id, households.c.name)).mappings()}
            org_map = {}
            if orgs is not None and "name" in orgs.c.keys():
                org_map = {r["id"]: r["name"] for r in conn.execute(select(orgs.c.id, orgs.c.name)).mappings()}
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
