"""Document filing persistence — BATCH 2 plan layer. Deterministic, pure, and it never writes.

WHAT BATCH 2 IS, AND WHY IT IS A SEPARATE BATCH
-----------------------------------------------
Batch 1 filed the 16,304 AUTO_FILE_SAFE documents. Batch 2 is the 550 documents the reviewed filing
preview held back for ONE reason only: their tax-year evidence contradicts itself. Nothing else about
them is uncertain — the owner is resolved, the service category came from a real SharePoint service
line, and an available source path names the owner or a member of their household.

A contradictory year is a reason not to choose a year. It is not a reason not to file, and the
reviewed rules already say so in three places:

* the preview places a year segment in a path only when ``tax_year_confidence == "strong"``;
* 9,361 of the 16,304 documents Batch 1 already filed sit at depth 2 for exactly that reason, applied
  and verified in production;
* for all 550 of these rows the frozen preview itself emitted a **two-segment** destination with an
  EMPTY ``proposed_tax_year``.

So this batch invents nothing. It files each document at the depth-2 category destination its own
reviewed row already specifies, and :func:`build_plan` refuses any row that arrives with a year, a
third segment, or a destination that is not a category node.

WHAT IT WRITES, AND THE ONE COLUMN IT TOUCHES
----------------------------------------------
Four folder rows that do not exist yet, and ``documents.folder_id`` on exactly 550 documents. Every
other folder in the tree is REUSED, not recreated: 377 of the 381 nodes this plan needs were created
by Batch 1 and are production state. Category, classification, subcategory, tags, display_name,
ownership, review_status, lifecycle and storage are untouched, for the reasons the filing code audit
established — ``documents.classification`` is CHECK-constrained to a domain vocabulary a service
category is not in, ``category``/``subcategory`` already mean something else to the Documents screen,
and a written ``tags.tax_year`` would turn an inference into a displayed fact. Which, for these 550
documents in particular, would be asserting a year the evidence disagrees about.

REUSE RATHER THAN A SECOND COPY
--------------------------------
The naming, code and digest primitives are imported from :mod:`app.services.document_filing_apply`,
the Batch 1 module that is deployed and verified. One slug rule and one digest definition across both
batches is the point: a second copy could drift, and a drifted code would either collide with a
Batch 1 folder or silently create a duplicate tree beside it.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.services.document_filing_apply import (
    FOLDER_FIELDS,
    FOLDER_KINDS,
    PLAN_FIELDS,
    PlanError,
    category_code,
    client_code,
    folder_manifest_digest,
    plan_digest,
    sha256_of,
    slugify,
)

__all__ = ["BATCH_NAME", "CANDIDATE_CSV_SHA256", "EXPECTED_DOCUMENTS", "EXPECTED_NEW_FOLDERS",
           "EXPECTED_REUSED_FOLDERS", "EXPECTED_PLAN_DIGEST", "EXPECTED_FOLDER_MANIFEST_DIGEST",
           "DESTINATION_DEPTH", "PlanError", "build_plan", "confirm_phrase", "rollback_phrase",
           "category_code", "client_code", "folder_manifest_digest", "plan_digest", "sha256_of",
           "slugify", "FOLDER_FIELDS", "FOLDER_KINDS", "PLAN_FIELDS"]

#: Batch identity. Encoded into both confirmation phrases and every audit row.
BATCH_NAME = "DOCUMENT-FILING-BATCH2"

#: The reviewed candidate artifact. Byte-pinned; this is the CSV, not the JSON.
CANDIDATE_CSV_SHA256 = "09615e3b95a79c900e07809c6699eaf49e205679c43e5363928fb210d64456d9"

#: The reviewed census. Every one is enforced, not assumed.
EXPECTED_DOCUMENTS = 550
EXPECTED_CLIENT_NODES = 190
EXPECTED_CATEGORY_NODES = 191
EXPECTED_FOLDER_NODES = 381
EXPECTED_NEW_FOLDERS = 4
EXPECTED_REUSED_FOLDERS = 377

EXPECTED_PLAN_DIGEST = "b2a5636ad2d4f200e10bf80b4e93cd29ce66731c99a4766174d3b0cc0b704e8e"
EXPECTED_FOLDER_MANIFEST_DIGEST = ("fea97522cbbef8ae85bafc821977f4fe5d601499816ea1fed39b571ba69b0d91")

#: Every Batch 2 destination is a CATEGORY node. A year node would be a year this batch refuses to
#: choose, so depth 3 is not "unexpected" here — it is forbidden.
DESTINATION_DEPTH = 2

#: The reviewed category and scope censuses.
EXPECTED_CATEGORY_CENSUS = {"Payroll": 4, "Sales & Litter Tax": 54, "Tax Preparation": 459,
                            "Wealth Accounts": 33}
EXPECTED_SCOPE_CENSUS = {"household": 80, "organization": 67, "person": 403}

#: Folder rows this batch creates carry a NULL classification, exactly as Batch 1's do.
FOLDER_CLASSIFICATION = None

CANDIDATE_COLUMNS = ("document_id", "scope_type", "scope_id", "scope_name", "category",
                     "folder_code", "folder_path", "source", "category_source",
                     "tax_year_confidence", "tax_year_evidence", "conflicts", "inclusion_reason")

SCOPE_TYPES = ("person", "household", "organization")


def _require(condition, message):
    if not condition:
        raise PlanError(f"ABORT: {message}")


def build_plan(candidate_csv, *, expect_sha=CANDIDATE_CSV_SHA256,
               expect_documents=EXPECTED_DOCUMENTS, enforce_census=True) -> dict[str, Any]:
    """The whole deterministic Batch 2 plan: folder manifest, destinations, digests. Pure.

    Re-proves every structural claim rather than trusting the artifact. The SHA proves the file is
    the reviewed one; it does not prove the reviewed one is coherent, and the two failures look
    identical from the outside.
    """
    path = Path(candidate_csv)
    _require(path.is_file(), f"frozen candidate not found: {path}")
    digest = sha256_of(path)
    _require(digest == expect_sha,
             f"candidate SHA256 {digest} != approved {expect_sha}")

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in CANDIDATE_COLUMNS if c not in (reader.fieldnames or ())]
        _require(not missing, f"candidate is missing columns {missing}")
        rows = list(reader)
    if expect_documents is not None:
        _require(len(rows) == expect_documents,
                 f"candidate has {len(rows)} rows, approved {expect_documents}")

    documents: list[dict] = []
    clients: dict[str, dict] = {}
    categories: dict[str, dict] = {}
    seen: set[int] = set()

    for row in rows:
        try:
            document_id = int(row["document_id"])
            scope_id = int(row["scope_id"])
        except (TypeError, ValueError) as exc:
            raise PlanError(f"ABORT: unreadable candidate row {row!r}") from exc
        _require(document_id not in seen, f"duplicate document_id {document_id}")
        seen.add(document_id)

        scope_type = (row["scope_type"] or "").strip()
        _require(scope_type in SCOPE_TYPES,
                 f"document {document_id} scope type {scope_type!r} is not one of "
                 f"{list(SCOPE_TYPES)}")
        scope_name = row["scope_name"] or ""
        category = row["category"] or ""
        _require(scope_name.strip() != "", f"document {document_id} has a blank scope name")
        _require(category.strip() != "", f"document {document_id} has a blank category")

        # The destination must be the CATEGORY node this scope+category determines, and nothing
        # else. Recomputing it rather than trusting the column is what makes a hand-edited
        # destination — a year folder, another client's folder — impossible to smuggle in.
        expected_code = category_code(scope_type, scope_id, category)
        _require(row["folder_code"] == expected_code,
                 f"document {document_id} folder_code {row['folder_code']!r} != the deterministic "
                 f"{expected_code!r}")
        _require("--year-" not in row["folder_code"],
                 f"document {document_id} names a YEAR destination; batch 2 never chooses a year")
        _require(row["folder_code"].count("--") == DESTINATION_DEPTH - 1,
                 f"document {document_id} destination is not a depth-{DESTINATION_DEPTH} node")

        segments = [s for s in (row["folder_path"] or "").split("/") if s]
        _require(len(segments) == DESTINATION_DEPTH,
                 f"document {document_id} folder_path has {len(segments)} segments, must have "
                 f"{DESTINATION_DEPTH}")
        _require(segments[0] == scope_name,
                 f"document {document_id} path client {segments[0]!r} != scope name "
                 f"{scope_name!r}")
        _require(segments[1] == category,
                 f"document {document_id} path category {segments[1]!r} != category {category!r}")

        # The whole reason this batch exists: the year is contradictory, so there must not be one.
        _require((row.get("tax_year_confidence") or "").strip() == "conflict",
                 f"document {document_id} tax_year_confidence is "
                 f"{row.get('tax_year_confidence')!r}, expected 'conflict'")

        c_code = client_code(scope_type, scope_id)
        _claim(clients, c_code, {"code": c_code, "name": scope_name, "kind": "client",
                                 "parent_code": None})
        _claim(categories, expected_code, {"code": expected_code, "name": category,
                                           "kind": "category", "parent_code": c_code})
        documents.append({
            "document_id": document_id, "scope_type": scope_type, "scope_id": scope_id,
            "scope_name": scope_name, "category": category, "tax_year": None,
            "depth": DESTINATION_DEPTH, "folder_code": expected_code,
            "folder_path": row["folder_path"],
        })

    folders = [clients[k] for k in sorted(clients)] + [categories[k] for k in sorted(categories)]
    documents.sort(key=lambda d: d["document_id"])
    computed_plan = plan_digest([{k: d[k] for k in PLAN_FIELDS} for d in documents])
    computed_folders = folder_manifest_digest(folders)

    if enforce_census:
        _require(len(clients) == EXPECTED_CLIENT_NODES,
                 f"{len(clients)} client nodes, approved {EXPECTED_CLIENT_NODES}")
        _require(len(categories) == EXPECTED_CATEGORY_NODES,
                 f"{len(categories)} category nodes, approved {EXPECTED_CATEGORY_NODES}")
        _require(len(folders) == EXPECTED_FOLDER_NODES,
                 f"{len(folders)} folder nodes, approved {EXPECTED_FOLDER_NODES}")
        census = _census(documents, "category")
        _require(census == EXPECTED_CATEGORY_CENSUS,
                 f"category census {census} != approved {EXPECTED_CATEGORY_CENSUS}")
        scopes = _census(documents, "scope_type")
        _require(scopes == EXPECTED_SCOPE_CENSUS,
                 f"scope census {scopes} != approved {EXPECTED_SCOPE_CENSUS}")
        _require(computed_plan == EXPECTED_PLAN_DIGEST,
                 f"plan digest {computed_plan} != approved {EXPECTED_PLAN_DIGEST}")
        _require(computed_folders == EXPECTED_FOLDER_MANIFEST_DIGEST,
                 f"folder manifest digest {computed_folders} != approved "
                 f"{EXPECTED_FOLDER_MANIFEST_DIGEST}")

    return {
        "batch": BATCH_NAME,
        "candidate_csv_sha256": digest,
        "documents": documents,
        "folders": folders,
        # How the plan's nodes must split between "already there" and "to create" is a REVIEWED
        # fact about production, not something the artifact can state, so it travels with the plan
        # and is enforced by the apply's state check. None when census enforcement is off, which
        # is how a test drives the real code path against a fixture of a different shape.
        "expect_new_folders": EXPECTED_NEW_FOLDERS if enforce_census else None,
        "expect_reused_folders": EXPECTED_REUSED_FOLDERS if enforce_census else None,
        "census": {
            "documents": len(documents),
            "client_nodes": len(clients),
            "category_nodes": len(categories),
            "year_nodes": 0,
            "folder_nodes": len(folders),
            "by_category": _census(documents, "category"),
            "by_scope_type": _census(documents, "scope_type"),
        },
        "plan_digest": computed_plan,
        "folder_manifest_digest": computed_folders,
    }


def _claim(registry: dict, code: str, node: dict) -> None:
    """Register a folder node, refusing a code two different names or parents both claim."""
    existing = registry.get(code)
    if existing is None:
        registry[code] = node
        return
    _require(existing["name"] == node["name"],
             f"folder code {code!r} is claimed by two different names: "
             f"{existing['name']!r} and {node['name']!r}")
    _require(existing["parent_code"] == node["parent_code"],
             f"folder code {code!r} is claimed under two different parents")


def _census(documents, key) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in documents:
        out[d[key]] = out.get(d[key], 0) + 1
    return dict(sorted(out.items()))


def confirm_phrase(document_count) -> str:
    """``APPLY-DOCUMENT-FILING-BATCH2-550``."""
    return f"APPLY-{BATCH_NAME}-{int(document_count)}"


def rollback_phrase(document_count) -> str:
    return f"ROLLBACK-{BATCH_NAME}-{int(document_count)}"
