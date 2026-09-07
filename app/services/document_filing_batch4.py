"""Document filing persistence BATCH 4 — the deterministic plan for the unconfirmed-client lane.

Batch 4 files 1,285 documents whose only recorded filing problem was
``no_client_confirmation_in_path``: the preview could not confirm from the source path that the
folder it was about to file into belongs to the document's owner, so it stopped. Everything else
about these rows is already clean — the scope is resolved, the category is a real service category
from the canonical SharePoint taxonomy, and the preview recorded NO conflicts at all.

WHY THE PREVIEW COULD NOT CONFIRM, AND WHY THAT IS NOT A LICENCE TO GUESS
--------------------------------------------------------------------------
The reason is not that the paths are ambiguous. It is that
:func:`document_filing_preview.client_context` compares the path against owner tokens built by
``person_name_tokens(first_name, last_name)`` for a person and against the organization's FULL name
including its legal suffix for a business. For every document in this batch one of exactly two
structural things is true:

* **the person's ``first_name`` and ``last_name`` are NULL** and their name lives in ``full_name``,
  so ``person_name_tokens`` returns the empty set and the guard ``if owner_tokens and ...`` can
  never fire — the comparison never happens at all; or
* **the path omits the organization's legal suffix** — ``Calhoun Construction`` for *Calhoun
  Construction LLC* — which the already-reviewed
  :func:`document_strict_safe_ownership_batch4.core_name_tokens` primitive exists to normalize.

Both are representation gaps in the comparison, not evidence of an unconfirmed client. Neither is
repaired by relaxing what counts as a match. This module re-derives the corroboration with the SAME
standard Batch 3 uses and nothing weaker, and the preview defect is reported separately rather than
worked around here.

WHAT IS ACCEPTED, EXHAUSTIVELY
--------------------------------
:func:`client_corroboration` from Batch 3, unchanged and imported rather than copied — so the two
batches cannot drift — plus three refusals that a population of 1,285 rows needs and Batch 3's
twelve did not:

``MIN_OWNER_TOKENS``       an owner name must carry at least two ASCII tokens. Batch 3's owners all
                           did; at this size a single-token name would silently become SURNAME-ONLY
                           matching, which is exactly what must never happen. Three different
                           Cundiffs are filed by this batch, and only the two-token requirement
                           keeps them apart.
``legal-suffix omission``  a path may DROP a legal suffix, which ``core_name_tokens`` normalizes. It
                           may not SUBSTITUTE a different one: ``Lilolu Properties Inc`` does not
                           corroborate *LILOLU PROPERTIES LLC*, because an LLC and an Inc are not
                           the same registered entity. Five rows are excluded for this.
``mutual agreement``       where several available sources carry a client folder, they must agree
                           with each other. Sets of name tokens that are neither equal nor nested
                           name different parties and cannot both be right.

No nickname table, no initials, no edit distance, no trade names, no abbreviation rules.

THE YEAR
--------
Unlike Batch 3, some of these rows have a tax year that the existing rules prove strongly, and the
preview already proposes a depth-3 destination for them. Those keep the year folder that Batch 1
established for exactly this case. Every other row files at depth 2 and NO year is chosen. The link
is enforced in both directions: a year node requires ``tax_year_confidence == 'strong'``, and a
strong year requires the year node. ``documents.tax_year`` is never written by any batch — a year
exists only as a folder.

WHAT IT WRITES
--------------
The folder rows this plan needs that do not exist yet, and ``documents.folder_id`` on exactly 1,285
documents. Fifteen nodes already exist from Batch 3 and are REUSED — never renamed, reparented,
reclassified or recreated. No other document column is touched.
"""
from __future__ import annotations

import csv
import json
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
    year_code,
)
from app.services.document_filing_batch3 import CORROBORATION_RULES, client_corroboration
from app.services.document_filing_preview import PROVENANCE_CATEGORIES
from app.services.document_strict_safe_ownership_batch2 import ascii_tokens
from app.services.document_strict_safe_ownership_batch4 import LEGAL_SUFFIX_TOKENS

__all__ = ["BATCH_NAME", "CANDIDATE_CSV_SHA256", "CANDIDATE_JSON_SHA256", "EXPECTED_DOCUMENTS",
           "EXPECTED_CLIENT_NODES", "EXPECTED_CATEGORY_NODES", "EXPECTED_YEAR_NODES",
           "EXPECTED_FOLDER_NODES", "EXPECTED_NEW_FOLDERS", "EXPECTED_REUSED_FOLDERS",
           "EXPECTED_PLAN_DIGEST", "EXPECTED_FOLDER_MANIFEST_DIGEST", "DESTINATION_DEPTHS",
           "FOLDER_CLASSIFICATION", "MIN_OWNER_TOKENS", "REQUIRED_REASON", "STRONG_YEAR",
           "PlanError", "build_plan", "corroborate", "substituted_legal_suffix",
           "CORROBORATION_RULES", "confirm_phrase", "rollback_phrase", "category_code",
           "client_code", "year_code", "folder_manifest_digest", "plan_digest", "sha256_of",
           "slugify", "verify_json_candidate", "FOLDER_FIELDS", "FOLDER_KINDS", "PLAN_FIELDS"]

BATCH_NAME = "DOCUMENT-FILING-BATCH4"

#: The reviewed artifacts. Byte-pinned.
CANDIDATE_CSV_SHA256 = "ec5157ffc0c6b96519392f49f02c77495d23d5e4cf5525ec531ec1020d33d06c"
CANDIDATE_JSON_SHA256 = "0636082af1aa73d9f1ac6d6db4b102140986c0bf85ddaee0cd80f15e6aa3c94f"

#: The reviewed census. Every one is enforced, not assumed.
EXPECTED_DOCUMENTS = 1285
EXPECTED_CLIENT_NODES = 111
EXPECTED_CATEGORY_NODES = 112
EXPECTED_YEAR_NODES = 149
EXPECTED_FOLDER_NODES = 372
EXPECTED_NEW_FOLDERS = 357
EXPECTED_REUSED_FOLDERS = 15

EXPECTED_PLAN_DIGEST = "8ff23437174d30b267d31d44bff832007fef62d3baea87a566fb2e1cd924e7b2"
EXPECTED_FOLDER_MANIFEST_DIGEST = (
    "bd1ac44d8c163c4ed2c5e400f4132657d45a92bdf3b3e491d07c3fbb1dbee9f3")

EXPECTED_CATEGORY_CENSUS = {"Payroll": 45, "Sales & Litter Tax": 265, "Tax Preparation": 975}
EXPECTED_SCOPE_CENSUS = {"organization": 706, "person": 579}
EXPECTED_DEPTH_CENSUS = {2: 794, 3: 491}

#: A destination is a CATEGORY node, or a YEAR node when — and only when — the year is strong.
DESTINATION_DEPTHS = (2, 3)

#: The single reason the reviewed lane permits.
REQUIRED_REASON = "no_client_confirmation_in_path"

#: The one tax-year confidence that earns a year folder. Anything else files at the category.
STRONG_YEAR = "strong"

#: The fewest ASCII tokens an owner name must have before a path can corroborate it. Two, because
#: one would make a shared surname sufficient.
MIN_OWNER_TOKENS = 2

#: Folder rows this batch creates carry a NULL classification, exactly as Batches 1-3's do.
FOLDER_CLASSIFICATION = None

SCOPE_TYPES = ("person", "household", "organization")

#: The candidate is a FILING-PREVIEW-shaped export, the same shape Batch 3 reads.
CANDIDATE_COLUMNS = ("document_id", "proposed_scope_type", "proposed_scope_id",
                     "proposed_scope_name", "filing_scope_state", "proposed_folder_segments",
                     "proposed_folder_path", "proposed_top_level_category", "proposed_tax_year",
                     "tax_year_confidence", "filing_status", "reasons", "conflicts", "evidence")


def _require(condition, message):
    if not condition:
        raise PlanError(f"ABORT: {message}")


def _trailing_suffixes(name) -> set[str]:
    """The legal-form tokens at the END of a name, which is the only place they carry that meaning.

    ``Katy & Co LLC`` yields ``{co, llc}``; ``Mignard Company`` yields ``{company}``. A token that
    is a legal form but sits mid-name is part of the identity and is not returned.
    """
    tokens = list(ascii_tokens(name))
    found: set[str] = set()
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        found.add(tokens.pop())
    return found


def substituted_legal_suffix(scope_name, segment) -> bool:
    """True when the PATH asserts a legal form the stored name does not have.

    Dropping a suffix is a formatting difference and ``core_name_tokens`` normalizes it. Replacing
    one is a claim about which registered entity this is, and nothing in Client360 proves that
    ``Lilolu Properties Inc`` and *LILOLU PROPERTIES LLC* are the same company. ``Mignard Company``
    for *Mignard Company LLC* is an omission, not a substitution: its suffix set is a SUBSET of the
    stored name's.
    """
    return bool(_trailing_suffixes(segment) - _trailing_suffixes(scope_name))


def corroborate(scope_type, scope_name, client_segments):
    """(rule, segment) when the path deterministically names the owner, else None.

    Batch 3's :func:`client_corroboration` decides the match; this adds the three refusals a
    population this size needs. Imported rather than reimplemented so the two batches cannot drift.
    """
    if len(ascii_tokens(scope_name)) < MIN_OWNER_TOKENS:
        return None
    named = [s for s in client_segments if (s or "").strip()]
    if not named:
        return None
    if any(substituted_legal_suffix(scope_name, s) for s in named):
        return None

    # Available client folders must agree with EACH OTHER. Token sets that are neither equal nor
    # nested name different parties; preferring whichever one happens to match would be choosing
    # the evidence that gives the answer we want.
    token_sets = {frozenset(ascii_tokens(s)) for s in named if ascii_tokens(s)}
    if any(a != b and not (a <= b or b <= a) for a in token_sets for b in token_sets):
        return None
    return client_corroboration(scope_type, scope_name, named)


def _json_field(row, key, document_id, default="[]"):
    try:
        return json.loads(row.get(key) or default)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"ABORT: document {document_id} has unreadable {key}") from exc


def _claim(registry: dict, code: str, node: dict) -> None:
    previous = registry.get(code)
    if previous is None:
        registry[code] = node
        return
    _require(previous == node,
             f"folder code {code} is claimed twice with different content: {previous} vs {node}")


def _census(documents, key) -> dict:
    counts: dict = {}
    for document in documents:
        counts[document[key]] = counts.get(document[key], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: str(kv[0])))


def build_plan(candidate_csv, *, expect_sha=CANDIDATE_CSV_SHA256,
               expect_documents=EXPECTED_DOCUMENTS, enforce_census=True) -> dict[str, Any]:
    """The whole deterministic Batch 4 plan: folder manifest, destinations, digests. Pure.

    Structurally re-proves every claim rather than trusting the artifact. The SHA proves the file is
    the reviewed one; it does not prove the reviewed one says what it was reported to say.
    """
    path = Path(candidate_csv)
    _require(path.is_file(), f"frozen candidate not found: {path}")
    digest = sha256_of(path)
    _require(digest == expect_sha, f"candidate SHA256 {digest} != approved {expect_sha}")

    previous_limit = csv.field_size_limit()
    csv.field_size_limit(1024 * 1024 * 64)
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in CANDIDATE_COLUMNS if c not in (reader.fieldnames or ())]
            _require(not missing, f"candidate is missing columns {missing}")
            rows = list(reader)
    finally:
        csv.field_size_limit(previous_limit)
    if expect_documents is not None:
        _require(len(rows) == expect_documents,
                 f"candidate has {len(rows)} rows, approved {expect_documents}")

    documents: list[dict] = []
    corroborations: dict[int, tuple] = {}
    clients: dict[str, dict] = {}
    categories: dict[str, dict] = {}
    years: dict[str, dict] = {}
    seen: set[int] = set()

    for row in rows:
        try:
            document_id = int(row["document_id"])
            scope_id = int(row["proposed_scope_id"])
        except (TypeError, ValueError) as exc:
            raise PlanError(f"ABORT: unreadable candidate row {row!r}") from exc
        _require(document_id not in seen, f"duplicate document_id {document_id}")
        seen.add(document_id)

        _require((row.get("filing_status") or "").strip() == "REVIEW_REQUIRED",
                 f"document {document_id} filing_status is {row.get('filing_status')!r}, "
                 "expected REVIEW_REQUIRED")
        _require((row.get("filing_scope_state") or "").strip().lower() == "resolved",
                 f"document {document_id} filing_scope_state is "
                 f"{row.get('filing_scope_state')!r}, not resolved")

        reasons = _json_field(row, "reasons", document_id)
        _require(reasons == [REQUIRED_REASON],
                 f"document {document_id} reasons are {reasons}, expected [{REQUIRED_REASON!r}]")

        # This lane's defining property, and the one Batch 3's lane did not have: the preview found
        # NOTHING in conflict. A single conflict of any kind means the row came from elsewhere.
        conflicts = _json_field(row, "conflicts", document_id)
        _require(conflicts == [],
                 f"document {document_id} carries conflicts {conflicts}; this lane has none")

        scope_type = (row.get("proposed_scope_type") or "").strip()
        _require(scope_type in SCOPE_TYPES,
                 f"document {document_id} scope type {scope_type!r} is not one of "
                 f"{list(SCOPE_TYPES)}")
        scope_name = row.get("proposed_scope_name") or ""
        category = row.get("proposed_top_level_category") or ""
        _require(scope_name.strip() != "", f"document {document_id} has a blank scope name")
        _require(category.strip() != "", f"document {document_id} has a blank category")

        # Checked against the taxonomy directly, never via a reason code: the preview returns this
        # lane's reason AFTER provenance_category_only, so a provenance category could not have
        # produced this reason — but that is a fact about ordering, not a gate, and gates are what
        # this module is made of.
        _require(category not in PROVENANCE_CATEGORIES,
                 f"document {document_id} category {category!r} is provenance-only, not a service "
                 "category")

        evidence = _json_field(row, "evidence", document_id, "{}")
        contexts = {k for k, _n in (evidence.get("client_contexts") or [])}
        _require("different_client" not in contexts,
                 f"document {document_id} has a different-client context")

        segments = [s.get("client_segment") or "" for s in (evidence.get("sources") or [])
                    if s.get("available") and s.get("taxonomy")]
        corroboration = corroborate(scope_type, scope_name, segments)
        _require(corroboration is not None,
                 f"document {document_id} has no source path that mechanically names its client "
                 f"(owner {scope_name!r}, path client folders {segments})")
        _require(corroboration[0] in CORROBORATION_RULES,
                 f"document {document_id} matched by an unknown rule {corroboration[0]!r}")
        _require(corroboration[0] != "organization_core" or scope_type == "organization",
                 f"document {document_id} used the organization rule on a {scope_type} scope")

        folder_segments = _json_field(row, "proposed_folder_segments", document_id)
        _require(isinstance(folder_segments, list)
                 and all(isinstance(s, str) for s in folder_segments),
                 f"document {document_id} proposed_folder_segments is not a list of strings")
        depth = len(folder_segments)
        _require(depth in DESTINATION_DEPTHS,
                 f"document {document_id} folder depth is {depth}, must be one of "
                 f"{list(DESTINATION_DEPTHS)}")
        _require(folder_segments[0] == scope_name,
                 f"document {document_id} first segment {folder_segments[0]!r} != scope name "
                 f"{scope_name!r}")
        _require(folder_segments[1] == category,
                 f"document {document_id} second segment {folder_segments[1]!r} != category "
                 f"{category!r}")
        _require((row.get("proposed_folder_path") or "") == "/".join(folder_segments),
                 f"document {document_id} proposed_folder_path does not match its segments")

        # The year, both ways round. A year folder demands a strong year, and a strong year demands
        # the year folder — so an uncertain year can never be manufactured, and a proven one can
        # never be silently discarded into a shared category folder.
        confidence = (row.get("tax_year_confidence") or "").strip()
        raw_year = (row.get("proposed_tax_year") or "").strip()
        strong = confidence == STRONG_YEAR
        _require((depth == 3) == strong,
                 f"document {document_id} has depth {depth} with tax_year_confidence "
                 f"{confidence!r}; a year folder requires {STRONG_YEAR!r} and nothing else")
        tax_year = None
        if strong:
            _require(raw_year.isdigit() and len(raw_year) == 4,
                     f"document {document_id} has a strong year that is not four digits: "
                     f"{raw_year!r}")
            tax_year = int(raw_year)
            _require(folder_segments[2] == raw_year,
                     f"document {document_id} third segment {folder_segments[2]!r} != tax year "
                     f"{raw_year!r}")

        # The destination is RECOMPUTED, never read from the artifact.
        parent = client_code(scope_type, scope_id)
        cat_code = category_code(scope_type, scope_id, category)
        destination = year_code(scope_type, scope_id, category, tax_year) if strong else cat_code
        _require(("--year-" in destination) == strong,
                 f"document {document_id} year node disagrees with its year confidence")
        _require(destination.count("--") == depth - 1,
                 f"document {document_id} destination is not a depth-{depth} node")

        _claim(clients, parent, {"code": parent, "name": scope_name, "kind": "client",
                                 "parent_code": None})
        _claim(categories, cat_code, {"code": cat_code, "name": category, "kind": "category",
                                      "parent_code": parent})
        if strong:
            _claim(years, destination, {"code": destination, "name": str(tax_year),
                                        "kind": "year", "parent_code": cat_code})
        documents.append({
            "document_id": document_id, "scope_type": scope_type, "scope_id": scope_id,
            "scope_name": scope_name, "category": category, "tax_year": tax_year,
            "depth": depth, "folder_code": destination,
            "folder_path": row.get("proposed_folder_path") or "",
        })
        corroborations[document_id] = corroboration

    folders = ([clients[k] for k in sorted(clients)]
               + [categories[k] for k in sorted(categories)]
               + [years[k] for k in sorted(years)])
    documents.sort(key=lambda d: d["document_id"])
    computed_plan = plan_digest([{k: d[k] for k in PLAN_FIELDS} for d in documents])
    computed_folders = folder_manifest_digest(folders)

    if enforce_census:
        _require(len(clients) == EXPECTED_CLIENT_NODES,
                 f"{len(clients)} client nodes, approved {EXPECTED_CLIENT_NODES}")
        _require(len(categories) == EXPECTED_CATEGORY_NODES,
                 f"{len(categories)} category nodes, approved {EXPECTED_CATEGORY_NODES}")
        _require(len(years) == EXPECTED_YEAR_NODES,
                 f"{len(years)} year nodes, approved {EXPECTED_YEAR_NODES}")
        _require(len(folders) == EXPECTED_FOLDER_NODES,
                 f"{len(folders)} folder nodes, approved {EXPECTED_FOLDER_NODES}")
        census = _census(documents, "category")
        _require(census == EXPECTED_CATEGORY_CENSUS,
                 f"category census {census} != approved {EXPECTED_CATEGORY_CENSUS}")
        scopes = _census(documents, "scope_type")
        _require(scopes == EXPECTED_SCOPE_CENSUS,
                 f"scope census {scopes} != approved {EXPECTED_SCOPE_CENSUS}")
        depths = _census(documents, "depth")
        _require(depths == EXPECTED_DEPTH_CENSUS,
                 f"depth census {depths} != approved {EXPECTED_DEPTH_CENSUS}")
        _require(computed_plan == EXPECTED_PLAN_DIGEST,
                 f"plan digest {computed_plan} != approved {EXPECTED_PLAN_DIGEST}")
        _require(computed_folders == EXPECTED_FOLDER_MANIFEST_DIGEST,
                 f"folder manifest digest {computed_folders} != approved "
                 f"{EXPECTED_FOLDER_MANIFEST_DIGEST}")

    return {
        "batch": BATCH_NAME,
        "candidate_csv_sha256": digest,
        "documents": documents,
        "corroborations": corroborations,
        "folders": folders,
        "expect_new_folders": EXPECTED_NEW_FOLDERS if enforce_census else None,
        "expect_reused_folders": EXPECTED_REUSED_FOLDERS if enforce_census else None,
        "census": {
            "documents": len(documents), "client_nodes": len(clients),
            "category_nodes": len(categories), "year_nodes": len(years),
            "folder_nodes": len(folders),
            "by_category": _census(documents, "category"),
            "by_scope_type": _census(documents, "scope_type"),
            "by_depth": _census(documents, "depth"),
        },
        "plan_digest": computed_plan,
        "folder_manifest_digest": computed_folders,
    }


def verify_json_candidate(json_path, *, expect_sha=CANDIDATE_JSON_SHA256) -> str:
    """Pin the reviewed JSON artifact's bytes as well. Returns its digest."""
    path = Path(json_path)
    _require(path.is_file(), f"json candidate not found: {path}")
    digest = sha256_of(path)
    _require(digest == expect_sha, f"json candidate SHA256 {digest} != approved {expect_sha}")
    return digest


def confirm_phrase(document_count) -> str:
    """``APPLY-DOCUMENT-FILING-BATCH4-1285``."""
    return f"APPLY-{BATCH_NAME}-{int(document_count)}"


def rollback_phrase(document_count) -> str:
    return f"ROLLBACK-{BATCH_NAME}-{int(document_count)}"
