"""Stage C — guarded physical relocation of the Stage-B household-owned documents.

The Stage-B re-ownership moved proven joint personal returns from a person to their canonical household
(ownership only); their bytes still sit under the old ``Clients\\<person>`` path. Stage C relocates ONLY
those documents to their household destination, REUSING the existing repository relocation engine
(``RepositoryRelocationJob``) — the same safe conventions already in production: copy -> verify (SHA-256 +
size) -> repoint ``storage_uri`` only after the destination is verified, source bytes RETAINED (never
deleted), ``document_sources`` and ``documents.id`` preserved, one document per transaction, idempotent,
and fail-closed on drift / collision / missing source / destination conflict / count drift.

Stage C adds NO new relocation logic — it only SCOPES the frozen manifest to household-owned re-owned
documents (``household_id`` set, ``person_id`` / ``organization_id`` NULL) and delegates the guarded
copy/verify/repoint to ``RepositoryRelocationJob._apply``. It creates/duplicates no rows, changes no
ownership, creates no people/households, and (this phase) previews only.
"""
from __future__ import annotations

from app.services.migration.canonical_repair import RepairGuardError
from app.services.migration.relocation import (
    RepositoryRelocationJob,
    _norm,
    _source_system,
    _under,
)

_HOUSEHOLDS_AREA = "Households"


def _is_household_owned(d) -> bool:
    """The Stage-B re-owned scope: owned by a household, and not by a person or organization."""
    return bool(d.get("household_id")) and not d.get("person_id") and not d.get("organization_id")


def _plan(config):
    """Load + scope + classify the household-owned documents (read-only). Returns (rows, manifest, counts)."""
    job = RepositoryRelocationJob(config)
    docs, people_map, hh_map, org_map = job._load()
    dest_root = config.migration_dest_root
    dest_norm = _norm(str(dest_root))

    scoped = [d for d in docs if _is_household_owned(d)]
    rows: list[dict] = []
    exceptions: list[dict] = []
    manifest: dict[int, tuple[str, str, str]] = {}
    dest_seen: dict[str, int] = {}
    total_bytes = needs = already = missing = placeholders = 0

    for d in scoped:
        placed = job.naming.plan(d, people=people_map, households=hh_map, organizations=org_map)
        dest_full = placed.full(dest_root)
        dest_seen[_norm(dest_full)] = dest_seen.get(_norm(dest_full), 0) + 1
        src = d.get("storage_uri")
        info = job.storage.stat(src) if src else None
        if not src or info is None or not info.exists:
            state = "missing_source"
            missing += 1
            exceptions.append({"document_id": d["id"], "reason": f"source not found: {src or '(none)'}"})
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
                manifest[d["id"]] = (placed.area, src, dest_full)
        rows.append({
            "document_id": d["id"], "state": state, "area": placed.area, "entity": placed.entity,
            "category": placed.category, "year": placed.year, "filename": placed.filename,
            "current_storage_uri": src or "", "proposed_destination": dest_full,
            "size_bytes": d.get("size_bytes") or (info.size if info else 0),
            "source_system": _source_system(d.get("tags")),
        })

    collisions = sum(v - 1 for v in dest_seen.values() if v > 1)
    counts = {
        "destination_root": str(dest_root), "household_scope_total": len(scoped),
        "needs_relocation": needs, "already_in_repository": already, "missing_source": missing,
        "cloud_only_placeholders": placeholders, "destination_collisions": collisions,
        "relocatable_bytes": total_bytes, "relocatable_gb": round(total_bytes / (1024 ** 3), 2),
    }
    return rows, manifest, counts, exceptions


def preview(config=None) -> dict:
    """Read-only Stage C preview. No bytes copied, no storage_uri changed, no rows modified."""
    if config is None:
        from app.services.migration.config import MigrationConfig
        config = MigrationConfig.from_env()
    rows, manifest, counts, exceptions = _plan(config)
    return {"counts": counts, "rows": rows, "exceptions": exceptions, "manifest": manifest,
            "reownable_manifest_size": len(manifest)}


def apply(*, confirm=False, backup=None, expect=None, config=None) -> dict:
    """Guarded Stage C APPLY. Builds the household-scoped frozen manifest and delegates the copy/verify/
    repoint to the existing RepositoryRelocationJob (all its guards apply). Pins the exact count."""
    if config is None:
        from app.services.migration.config import MigrationConfig
        config = MigrationConfig.from_env()
    _rows, manifest, _counts, _exc = _plan(config)
    if expect is not None and len(manifest) != expect:
        raise RepairGuardError(
            f"count drift — approved {expect} but live {len(manifest)} household documents need "
            "relocation; aborted before any write.")
    if not manifest:
        # Nothing left to relocate (idempotent no-op) — the underlying engine requires a non-empty
        # frozen scope, so short-circuit rather than delegate. Confirm/backup are still validated.
        if not confirm:
            raise RepairGuardError("APPLY requires explicit confirm=True.")
        import os
        if not backup or not os.path.isfile(backup) or os.path.getsize(backup) == 0:
            raise RepairGuardError(f"APPLY requires a verified non-empty DB backup file (got: {backup!r}).")
        return {"counts": {"Clients": 0, "Households": 0, "Businesses": 0, "total": 0,
                           "rows_inserted": 0, "skipped_already_relocated": 0},
                "reconciliation": [], "notes": ["Nothing to relocate — all household documents already "
                                                "in the repository (idempotent no-op)."]}
    # Delegate to the production relocation engine with a Households-only frozen manifest + per-area expect.
    per_area_expect = {"Clients": 0, "Households": len(manifest), "Businesses": 0}
    outcome = RepositoryRelocationJob(config)._apply(
        approved=manifest, confirm=confirm, backup=backup, expect=per_area_expect)
    return {"counts": outcome.counts, "reconciliation": outcome.reconciliation, "notes": outcome.notes}
