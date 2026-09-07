"""Phase A and Phase B plan layers. Pure, deterministic, and neither one touches a database.

TWO PHASES, TWO AUTHORIZATIONS, AND THE BINDING BETWEEN THEM
-------------------------------------------------------------
**Phase A** materializes folders. It may create and reuse ``document_folders`` rows and write its
own audit events. It may not touch a single document row.

**Phase B** files documents into folders that ALREADY EXIST. It may set ``documents.folder_id`` on
exactly the manifest-approved rows. It may not create a folder, not even one that is obviously
missing — a missing folder is an abort, because "create what's missing" is how a Phase B quietly
becomes a Phase A. It also may not re-file a document that already has a folder, so a Phase B
manifest names only documents that are UNFILED when it is planned — see
:func:`build_phase_b_manifest`.

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

#: Digest of the folders THIS Phase B actually touches — its destinations plus every ancestor.
TARGET_SUBTREE_DIGEST_FIELD = "target_subtree_digest"


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


def build_phase_b_manifest(rows, phase_a_manifest, *, filed_document_ids=()) -> dict:
    """The filing manifest: which document goes into which ALREADY-APPROVED folder.

    ``filed_document_ids`` is the set of documents that ALREADY have a ``documents.folder_id``.
    Pass the live set; the caller reads it, this stays pure.

    WHY THE PLANNER MUST KNOW WHAT IS ALREADY FILED
    ------------------------------------------------
    :mod:`scripts.apply_canonical_filing` files a document with ``UPDATE documents SET folder_id
    = :folder_id WHERE id = :id AND folder_id IS NULL`` and aborts the whole batch on any row it
    finds already filed. That is the correct safeguard and it is not relaxed here. But the planner
    used to emit every AUTO_FILE_SAFE row that passed the naming gate, whichever folder it was
    already sitting in — so once ANY documents had been filed (by a previous Phase B, or by one of
    the strict-safe-ownership batches), the manifest contained rows the apply was guaranteed to
    reject, and a batch that was entirely valid for its remaining 3,801 documents aborted on the
    7,422 that were already home.

    A manifest is an authorization to act. Listing a document that cannot be acted on overstates
    what is being approved and makes the apply's own guard the thing that discovers it. Eligibility
    belongs in the plan, and the apply's guard stays where it is to catch DRIFT — a document filed
    between planning and applying — which is a different fact and still an abort.

    THE THREE BUCKETS PARTITION AUTO_FILE_SAFE EXACTLY
    ---------------------------------------------------
    ``already_filed`` is checked BEFORE ``naming_hold``, so the buckets never overlap and
    ``auto_file_safe_count == already_filed_count + naming_hold_count + document_count`` always
    holds. Precedence is that way round because being filed is a fact about live state that no
    amount of renaming changes: a filed document is not waiting for a better name, it is done.

    :raises ManifestError: if handed something that is not a Phase A manifest, or if any assignment
        names a folder that Phase A does not create. The second check is what makes "Phase B cannot
        create folders" a property of the plan and not merely of the script.
    """
    require(isinstance(phase_a_manifest, dict), "phase A manifest is required to plan phase B")
    require(phase_a_manifest.get("phase") == PHASE_A,
            f"expected a {PHASE_A} manifest, got {phase_a_manifest.get('phase')!r}")
    approved = {node["code"] for node in phase_a_manifest["folders"]}
    filed = {int(document_id) for document_id in filed_document_ids}

    # Naming quality gates PHASE B ONLY. The folder tree is already correct for a badly named
    # document — what must not happen is filing it under a name nobody can read. Phase A above
    # sees the full AUTO_FILE_SAFE population; these rows simply wait for naming.
    every_row = _auto_rows(rows)
    already_filed, naming_hold, auto = [], [], []
    for row in every_row:
        if int(row["document_id"]) in filed:
            already_filed.append(row)
        elif not row.get("phase_b_naming_ok", True):
            naming_hold.append(row)
        else:
            auto.append(row)
    require(len(already_filed) + len(naming_hold) + len(auto) == len(every_row),
            "the phase B buckets do not partition the AUTO_FILE_SAFE population")

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
    # The folders this batch will actually touch. Phase A's full digest stays below as the binding
    # to the approved tree; this is what the apply can re-prove against a scoped read of live state.
    subtree = target_subtree_nodes(phase_a_manifest,
                                   sorted({a["folder_code"] for a in assignments}))
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
        "auto_file_safe_count": len(every_row),
        "already_filed_count": len(already_filed),
        "already_filed_document_ids": sorted(int(r["document_id"]) for r in already_filed),
        "naming_hold_count": len(naming_hold),
        "naming_hold_document_ids": sorted(int(r["document_id"]) for r in naming_hold),
        # Every AUTO_FILE_SAFE document lands in exactly one bucket, and the four numbers are
        # published together so a reviewer can add them up without re-deriving anything.
        "reconciliation": {
            "auto_file_safe": len(every_row),
            "already_filed": len(already_filed),
            "naming_hold": len(naming_hold),
            "planned": len(assignments),
            "reconciles": len(every_row) == (
                len(already_filed) + len(naming_hold) + len(assignments)),
        },
        "by_display_name_quality": dict(sorted(Counter(
            r["display_name_quality"] for r in auto).items())),
        "assignment_digest": digest,
        FOLDER_MANIFEST_DIGEST_FIELD: phase_a_manifest[FOLDER_MANIFEST_DIGEST_FIELD],
        TARGET_SUBTREE_DIGEST_FIELD: target_subtree_digest_of(
            subtree, phase_a_manifest[FOLDER_MANIFEST_DIGEST_FIELD]),
        "target_subtree_folder_count": len(subtree),
        "target_subtree_census": _folder_census(subtree),
        "batch_id": batch_id(PHASE_B, digest),
        "confirm_phrase": confirm_phrase(PHASE_B, len(assignments)),
        "rollback_phrase": rollback_phrase(PHASE_B, len(assignments)),
    }


def folder_manifest_digest_of(folders) -> str:
    """Digest of a folder set in manifest form — the whole approved Phase A tree."""
    ordered = sorted(folders, key=lambda n: (FOLDER_KINDS.index(n["kind"]), n["code"]))
    return digest_of([{k: n[k] for k in FOLDER_FIELDS} for n in ordered])


def target_subtree_nodes(phase_a_manifest, folder_codes) -> list[dict]:
    """The Phase A folders a Phase B touches: its destinations plus every ancestor, nothing else.

    Derived from the APPROVED Phase A manifest, never from live state, so the plan states what the
    apply must find rather than discovering it there.
    """
    by_code = {node["code"]: node for node in phase_a_manifest["folders"]}
    wanted: dict[str, dict] = {}
    for code in folder_codes:
        cursor = code
        # Bounded by the canonical depth: client > service > year and nothing deeper.
        for _ in range(len(FOLDER_KINDS) + 1):
            if cursor is None or cursor in wanted:
                break
            node = by_code.get(cursor)
            require(node is not None,
                    f"folder {cursor!r} is required by a phase B target but the phase A manifest "
                    "does not create it")
            wanted[cursor] = node
            cursor = node["parent_code"]
        else:  # pragma: no cover - a chain deeper than the canonical depth cannot be built
            raise ManifestError(f"folder {code!r} has a parent chain deeper than the canonical tree")
    return sorted(wanted.values(), key=lambda n: (FOLDER_KINDS.index(n["kind"]), n["code"]))


def target_subtree_digest_of(folders, folder_manifest_digest) -> str:
    """Digest of a Phase B's target subtree, BOUND to the Phase A manifest it came from.

    WHY THIS IS NOT ``folder_manifest_digest_of``
    ----------------------------------------------
    Phase B's folder gate reads only its target folders and their ancestors, deliberately: hashing
    every canonical folder would make one batch's gate depend on folders belonging to another, so an
    unrelated Phase A elsewhere in the tree would abort a filing run that is perfectly valid. But
    that scoped hash used to be compared against ``folder_manifest_digest``, which covers the ENTIRE
    Phase A manifest. Those two are equal only when the batch happens to target every folder Phase A
    created — which the pre-eligibility Phase B did, and which is why the mismatch stayed invisible
    until the plan was correctly narrowed to 3,787 documents across 852 destinations. A proper
    subset can never hash to the whole.

    The Phase A digest is not dropped; it is hashed INTO this one. So the binding is enforced rather
    than merely recorded: a manifest whose ``folder_manifest_digest`` was edited produces a
    different target-subtree digest, and the apply — which recomputes this from the live subtree and
    the manifest's own recorded Phase A digest — aborts.
    """
    ordered = sorted(folders, key=lambda n: (FOLDER_KINDS.index(n["kind"]), n["code"]))
    return digest_of({
        FOLDER_MANIFEST_DIGEST_FIELD: folder_manifest_digest,
        "folders": [{k: n[k] for k in FOLDER_FIELDS} for n in ordered],
    })


__all__ = [
    "ASSIGNMENT_FIELDS",
    "FOLDER_FIELDS",
    "FOLDER_MANIFEST_DIGEST_FIELD",
    "TARGET_SUBTREE_DIGEST_FIELD",
    "ManifestError",
    "build_phase_a_manifest",
    "build_phase_b_manifest",
    "folder_manifest_digest_of",
    "target_subtree_digest_of",
    "target_subtree_nodes",
]
