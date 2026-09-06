"""Phase A and Phase B plan layers. Pure, deterministic, and neither one touches a database.

TWO PHASES, TWO AUTHORIZATIONS, AND THE BINDING BETWEEN THEM
-------------------------------------------------------------
**Phase A** materializes folders. It may create and reuse ``document_folders`` rows and write its
own audit events. It may not touch a single document row.

**Phase B** files documents into folders that ALREADY EXIST. It may set ``documents.folder_id`` on
exactly the manifest-approved rows. It may not create a folder, not even one that is obviously
missing — a missing folder is an abort, because "create what's missing" is how a Phase B quietly
becomes a Phase A.

The two are bound by :data:`FOLDER_MANIFEST_DIGEST_FIELD`: a Phase B manifest records the digest of
the Phase A folder manifest it was planned against. Phase B re-derives that digest from the folders
it finds live and refuses to run if it differs. So Phase B cannot be pointed at a tree nobody
approved, and it cannot silently tolerate a tree that changed after approval.

Each manifest carries its phase tag inside the hashed payload and inside the confirmation phrase, so
handing a Phase A manifest to the Phase B script fails on the tag before anything is read — see
:func:`app.services.filing_manifest.require_phase`.

WHY THE PLAN LAYER IS PURE
---------------------------
Everything here is a function of the preview rows alone. The apply scripts are what compare a plan
to live state, under a lock, inside a transaction. Keeping the plan pure is what makes it hashable,
reproducible on any machine, and reviewable as an artifact rather than as a run.
"""
from __future__ import annotations

from app.services.canonical_filing import (
    AUTO_FILE_SAFE,
    CANONICAL_DEPTH,
    FOLDER_KINDS,
    SCOPE_TYPES,
    folder_nodes,
)
from app.services.filing_manifest import (
    PHASE_A,
    PHASE_B,
    ManifestError,
    batch_id,
    confirm_phrase,
    digest_of,
    require,
    rollback_phrase,
)

#: Fields of a Phase A folder node that are hashed. Display name included: a renamed folder is a
#: different reviewed artifact even though its identity is unchanged.
FOLDER_FIELDS = ("code", "kind", "name", "parent_code", "owner_scope_type", "owner_scope_id",
                 "service_code", "tax_year")

#: Fields of a Phase B assignment that are hashed.
ASSIGNMENT_FIELDS = ("document_id", "folder_code", "owner_scope_type", "owner_scope_id",
                     "service_code", "tax_year")

FOLDER_MANIFEST_DIGEST_FIELD = "folder_manifest_digest"


def _auto_rows(rows) -> list[dict]:
    auto = [r for r in rows if r["status"] == AUTO_FILE_SAFE]
    seen: set[int] = set()
    for row in auto:
        document_id = row["document_id"]
        require(document_id is not None, "an AUTO_FILE_SAFE row has no document_id")
        require(document_id not in seen, f"duplicate document_id {document_id}")
        seen.add(int(document_id))
        require(row["owner_scope_type"] in SCOPE_TYPES,
                f"document {document_id} scope type {row['owner_scope_type']!r} is not a scope type")
        require(row["owner_scope_id"] is not None,
                f"document {document_id} has no owner scope id")
        require(bool(row["service_code"]), f"document {document_id} has no service code")
        require(row["tax_year"] is not None, f"document {document_id} has no tax year")
        require(len(row["folder_segments"]) == CANONICAL_DEPTH,
                f"document {document_id} folder depth is {len(row['folder_segments'])}, "
                f"canonical depth is {CANONICAL_DEPTH}")
        require(row["folder_path"] == "/".join(row["folder_segments"]),
                f"document {document_id} folder path does not match its segments")
        require(bool(row["folder_code"]), f"document {document_id} has no folder code")
    return sorted(auto, key=lambda r: int(r["document_id"]))


def _folder_census(folders) -> dict:
    census: dict[str, int] = {kind: 0 for kind in FOLDER_KINDS}
    for node in folders:
        census[node["kind"]] += 1
    return census


def build_phase_a_manifest(rows) -> dict:
    """The folder manifest: every distinct canonical folder the AUTO_FILE_SAFE rows require.

    Folders only. The document ids are not in it and are not hashed into it, because Phase A must be
    reviewable and applicable without reference to which documents will later land there.
    """
    auto = _auto_rows(rows)
    folders = folder_nodes(rows)
    require(bool(folders) or not auto, "AUTO_FILE_SAFE rows exist but produced no folders")

    by_code = {node["code"]: node for node in folders}
    for node in folders:
        parent = node["parent_code"]
        if parent is not None:
            require(parent in by_code,
                    f"folder {node['code']!r} names a parent {parent!r} that is not in the manifest")
        if node["kind"] == "client":
            require(parent is None, f"client folder {node['code']!r} must have no parent")
        else:
            require(parent is not None, f"{node['kind']} folder {node['code']!r} needs a parent")
    # Parents must precede children in list order, so the apply may insert straight down the list.
    position = {node["code"]: i for i, node in enumerate(folders)}
    for node in folders:
        if node["parent_code"] is not None:
            require(position[node["parent_code"]] < position[node["code"]],
                    f"folder {node['code']!r} precedes its parent in the manifest")

    payload = [{k: node[k] for k in FOLDER_FIELDS} for node in folders]
    digest = digest_of(payload)
    return {
        "phase": PHASE_A,
        "folders": folders,
        "folder_count": len(folders),
        "census": _folder_census(folders),
        "destinations": sum(1 for n in folders if n["kind"] == "year"),
        "owners": len({(n["owner_scope_type"], n["owner_scope_id"]) for n in folders}),
        FOLDER_MANIFEST_DIGEST_FIELD: digest,
        "batch_id": batch_id(PHASE_A, digest),
        "confirm_phrase": confirm_phrase(PHASE_A, len(folders)),
        "rollback_phrase": rollback_phrase(PHASE_A, len(folders)),
    }


def build_phase_b_manifest(rows, phase_a_manifest) -> dict:
    """The filing manifest: which document goes into which ALREADY-APPROVED folder.

    :raises ManifestError: if handed something that is not a Phase A manifest, or if any assignment
        names a folder that Phase A does not create. The second check is what makes "Phase B cannot
        create folders" a property of the plan and not merely of the script.
    """
    require(isinstance(phase_a_manifest, dict), "phase A manifest is required to plan phase B")
    require(phase_a_manifest.get("phase") == PHASE_A,
            f"expected a {PHASE_A} manifest, got {phase_a_manifest.get('phase')!r}")
    approved = {node["code"] for node in phase_a_manifest["folders"]}

    # Naming quality gates PHASE B ONLY. The folder tree is already correct for a badly named
    # document — what must not happen is filing it under a name nobody can read. Phase A above
    # sees the full AUTO_FILE_SAFE population; these rows simply wait for naming.
    every_row = _auto_rows(rows)
    auto = [r for r in every_row if r.get("phase_b_naming_ok", True)]
    naming_hold = [r for r in every_row if not r.get("phase_b_naming_ok", True)]

    assignments = []
    for row in auto:
        require(row["folder_code"] in approved,
                f"document {row['document_id']} targets folder {row['folder_code']!r}, which the "
                "phase A manifest does not create — phase B may never create a folder")
        assignments.append({
            "document_id": int(row["document_id"]),
            "folder_code": row["folder_code"],
            "owner_scope_type": row["owner_scope_type"],
            "owner_scope_id": int(row["owner_scope_id"]),
            "service_code": row["service_code"],
            "tax_year": int(row["tax_year"]),
            "folder_path": row["folder_path"],
            "service_source": row["service_source"],
            "derivation_rule": (row["derivation"] or {}).get("derivation_rule"),
            "backing_document_count": (row["derivation"] or {}).get("backing_document_count"),
            "backing_digest": (row["derivation"] or {}).get("backing_digest"),
        })

    payload = [{k: a[k] for k in ASSIGNMENT_FIELDS} for a in assignments]
    digest = digest_of(payload)
    from collections import Counter
    return {
        "phase": PHASE_B,
        "assignments": assignments,
        "document_count": len(assignments),
        "destinations": len({a["folder_code"] for a in assignments}),
        "owners": len({(a["owner_scope_type"], a["owner_scope_id"]) for a in assignments}),
        "by_service": dict(sorted(Counter(a["service_code"] for a in assignments).items())),
        "by_service_source": dict(sorted(Counter(
            a["service_source"] for a in assignments).items())),
        "derived_assignments": sum(1 for a in assignments if a["derivation_rule"]),
        "naming_hold_count": len(naming_hold),
        "naming_hold_document_ids": sorted(int(r["document_id"]) for r in naming_hold),
        "by_display_name_quality": dict(sorted(Counter(
            r["display_name_quality"] for r in auto).items())),
        "assignment_digest": digest,
        FOLDER_MANIFEST_DIGEST_FIELD: phase_a_manifest[FOLDER_MANIFEST_DIGEST_FIELD],
        "batch_id": batch_id(PHASE_B, digest),
        "confirm_phrase": confirm_phrase(PHASE_B, len(assignments)),
        "rollback_phrase": rollback_phrase(PHASE_B, len(assignments)),
    }


def folder_manifest_digest_of(folders) -> str:
    """Digest of a folder set in manifest form — used by Phase B to re-prove the live tree."""
    ordered = sorted(folders, key=lambda n: (FOLDER_KINDS.index(n["kind"]), n["code"]))
    return digest_of([{k: n[k] for k in FOLDER_FIELDS} for n in ordered])


__all__ = [
    "ASSIGNMENT_FIELDS",
    "FOLDER_FIELDS",
    "FOLDER_MANIFEST_DIGEST_FIELD",
    "ManifestError",
    "build_phase_a_manifest",
    "build_phase_b_manifest",
    "folder_manifest_digest_of",
]
