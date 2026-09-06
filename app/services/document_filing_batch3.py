"""Document filing persistence — BATCH 3 plan layer. Deterministic, pure, and it never writes.

WHAT BATCH 3 IS
---------------
12 documents from the same lane Batch 2 filed: their filing evidence conflicts about ONE thing, the
tax year, and about nothing else. Owner resolved, category from a real SharePoint service line, no
different-client conflict, no category conflict. They are filed at the depth-2 CATEGORY destination
the frozen preview already computed for them, and no year folder is created or assigned — a
contradictory year is a reason not to choose one.

CLIENT CORROBORATION IS REQUIRED, AS IT WAS FOR BATCHES 1 AND 2
----------------------------------------------------------------
The first cut of this batch took 49 rows on the strength of ``reasons`` containing only
``conflicting_filing_evidence``. That was wrong. The preview returns that verdict BEFORE it checks
whether a source path confirms the client, so the ABSENCE of ``no_client_confirmation_in_path`` was
an artefact of early-return ordering, not evidence of confirmation — and none of the 49 in fact
carried path confirmation under the preview's own matcher.

So the destination client is corroborated here the way it is in Batches 1 and 2: the available client
filing path must MECHANICALLY name the stored owner. :func:`client_corroboration` is that gate, and
it accepts exactly two deterministic rules:

``exact_or_reordered``   the owner's name tokens are set-equal to, or a subset of, the path client
                         folder's tokens. This covers ``FOSTER, LANDON`` for LANDON FOSTER and
                         ``HARDING,NOAH`` for NOAH HARDING — reordering, case, punctuation and
                         separators are all absorbed by the shared ASCII tokenizer.
``organization_core``    for ORGANIZATION scopes only, the reviewed legal-suffix primitive
                         ``document_strict_safe_ownership_batch4.core_name_tokens`` matches, so
                         ``Harmony Day Support`` corroborates *Harmony Day Support Inc*.

Nothing else. No nickname table, no initials, no edit distance, no invented spacing rule. 7 rows match
directly, 5 match ``organization_core``, 33 would need one of the rules above and 4 have a path naming
no client at all; those 37 are excluded and belong in a human review lane:

* ``Reynolds, Ben`` for Benjamin Reynolds, ``LESIV,MATTHEW`` for Matt Lesiv, ``Lacayo, JP`` for Juan
  Lacayo — ``people.preferred_name`` is NULL for every one of them, so NO trusted Client360 data
  links the two forms;
* ``McCroskey, Brandy`` for Brandy Mc Croskey — a spacing variant inside the surname that no existing
  reviewed primitive normalizes;
* ``Murray & Sons`` for Murray & Sons Electrical, ``Santram Inc`` for SANTRAM CORPORATION — the path
  drops an identifying word, not merely a legal suffix;
* ``SHAREEF, REGINALD A & FAYE S`` for Malik Shareef — the path names DIFFERENT INDIVIDUALS who share
  a surname, which is exactly the confusion this gate exists to catch;
* ``Federal`` and ``Unemployment`` for MORGAN & MORGAN CONSTRUCTION INC — generic form folders
  carrying no client identity at all.

The gate is PURE: it re-derives the match from the candidate's own ``proposed_scope_name`` and the
``client_segment`` values in its recorded evidence, so it needs no database and cannot be satisfied
by a claim the artifact merely asserts.

WHAT IT WRITES
--------------
The folder rows this plan needs that do not exist yet, and ``documents.folder_id`` on exactly 12
documents. Any node an earlier batch already created is REUSED, never renamed, reparented,
reclassified or recreated. No other document column is touched, for the reasons the filing code
audit established.
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
)
from app.services.document_strict_safe_ownership_batch2 import ascii_tokens
from app.services.document_strict_safe_ownership_batch4 import core_name_tokens

__all__ = ["BATCH_NAME", "CANDIDATE_CSV_SHA256", "CANDIDATE_JSON_SHA256", "EXPECTED_DOCUMENTS",
           "EXPECTED_CLIENT_NODES", "EXPECTED_CATEGORY_NODES", "EXPECTED_FOLDER_NODES",
           "EXPECTED_NEW_FOLDERS", "EXPECTED_REUSED_FOLDERS", "EXPECTED_PLAN_DIGEST",
           "EXPECTED_FOLDER_MANIFEST_DIGEST", "DESTINATION_DEPTH", "FOLDER_CLASSIFICATION",
           "PlanError", "build_plan", "client_corroboration", "CORROBORATION_RULES",
           "confirm_phrase", "rollback_phrase", "category_code",
           "client_code", "folder_manifest_digest", "plan_digest", "sha256_of", "slugify",
           "FOLDER_FIELDS", "FOLDER_KINDS", "PLAN_FIELDS"]

BATCH_NAME = "DOCUMENT-FILING-BATCH3"

#: The reviewed artifacts. Byte-pinned.
CANDIDATE_CSV_SHA256 = "d4776117ae0cb4b78e3643be0f1de02d90d5c41c4fcd7454930390ffbcc38818"
CANDIDATE_JSON_SHA256 = "140bdcf82c3ce5f5075dd0009ad40c28eeadd12ba32deab578b09cf534a9efc1"

#: The reviewed census. Every one is enforced, not assumed.
EXPECTED_DOCUMENTS = 12
EXPECTED_CLIENT_NODES = 6
EXPECTED_CATEGORY_NODES = 6
EXPECTED_FOLDER_NODES = 12
EXPECTED_NEW_FOLDERS = 12
EXPECTED_REUSED_FOLDERS = 0

EXPECTED_PLAN_DIGEST = "8857aa6a996442865f38a4872c60abddd90e2ac7a7aec2269178cdd64b51145f"
EXPECTED_FOLDER_MANIFEST_DIGEST = \
    "95d130216ed4c9a45a9e8f80670168b88c0aeea7e94aebbe5cb2576b2f831b73"

EXPECTED_CATEGORY_CENSUS = {"Sales & Litter Tax": 3, "Tax Preparation": 9}
EXPECTED_SCOPE_CENSUS = {"organization": 5, "person": 7}

#: Every destination is a CATEGORY node. A year node would be a year this batch refuses to choose.
DESTINATION_DEPTH = 2

#: The single reason the reviewed lane permits.
REQUIRED_REASON = "conflicting_filing_evidence"

#: Folder rows this batch creates carry a NULL classification, exactly as Batch 1 and 2's do.
FOLDER_CLASSIFICATION = None

SCOPE_TYPES = ("person", "household", "organization")

#: The candidate is a FILING-PREVIEW-shaped export, not the flat Batch 2 candidate shape.
CANDIDATE_COLUMNS = ("document_id", "proposed_scope_type", "proposed_scope_id",
                     "proposed_scope_name", "filing_scope_state", "proposed_folder_segments",
                     "proposed_folder_path", "proposed_top_level_category", "proposed_tax_year",
                     "tax_year_confidence", "filing_status", "reasons", "conflicts", "evidence")


def _require(condition, message):
    if not condition:
        raise PlanError(f"ABORT: {message}")


#: The only corroboration rules this batch accepts. Named so a refusal message and an audit row can
#: say WHICH rule proved a destination, and so that adding a third is a visible code change.
CORROBORATION_RULES = ("exact_or_reordered", "organization_core")


def client_corroboration(scope_type, scope_name, client_segments):
    """(rule, matching segment) when a path client folder mechanically names the owner, else None.

    Pure and deterministic. ``exact_or_reordered`` absorbs ordering, case, punctuation and separator
    differences through the shared ASCII tokenizer, so ``FOSTER, LANDON`` proves LANDON FOSTER.
    ``organization_core`` additionally lets a business's legal suffix differ, via the reviewed
    :func:`core_name_tokens` primitive and its two-token minimum, so ``Harmony Day Support`` proves
    *Harmony Day Support Inc* while ``Santram Inc`` does NOT prove *SANTRAM CORPORATION*.

    A subset match means the path carries the owner's whole name plus extra words — a joint or
    suffixed folder. A path that DROPS a word of the owner's name proves nothing and returns None.
    """
    owner = set(ascii_tokens(scope_name))
    if not owner:
        return None
    for segment in client_segments:
        path = set(ascii_tokens(segment))
        if path and owner <= path:
            return "exact_or_reordered", segment
    if scope_type == "organization":
        owner_core = core_name_tokens(scope_name)
        if len(owner_core) >= 2:
            for segment in client_segments:
                if owner_core <= core_name_tokens(segment):
                    return "organization_core", segment
    return None


def _json_field(row, key, document_id, default="[]"):
    try:
        return json.loads(row.get(key) or default)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"ABORT: document {document_id} has unreadable {key}") from exc


def build_plan(candidate_csv, *, expect_sha=CANDIDATE_CSV_SHA256,
               expect_documents=EXPECTED_DOCUMENTS, enforce_census=True) -> dict[str, Any]:
    """The whole deterministic Batch 3 plan: folder manifest, destinations, digests. Pure.

    Structurally re-proves every claim rather than trusting the artifact. The SHA proves the file is
    the reviewed one; it does not prove the reviewed one says what it was reported to say, and the
    two failures are indistinguishable from outside.
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

        # The reviewed lane is exactly one reason. Any other reason means this row came from a
        # different population — a provenance-only category, or an unconfirmed client that the
        # preview DID record — and is not what was approved here.
        reasons = _json_field(row, "reasons", document_id)
        _require(reasons == [REQUIRED_REASON],
                 f"document {document_id} reasons are {reasons}, expected [{REQUIRED_REASON!r}]")

        # Every conflict must be about the tax year and nothing else.
        conflicts = _json_field(row, "conflicts", document_id)
        _require(bool(conflicts), f"document {document_id} carries no conflicts")
        _require(all("tax-year signals disagree" in c for c in conflicts),
                 f"document {document_id} has a non-tax-year conflict: {conflicts}")

        _require((row.get("tax_year_confidence") or "").strip() == "conflict",
                 f"document {document_id} tax_year_confidence is "
                 f"{row.get('tax_year_confidence')!r}, expected 'conflict'")
        _require((row.get("proposed_tax_year") or "").strip() == "",
                 f"document {document_id} carries a proposed_tax_year; this batch never files a "
                 "year")

        # The residual safety property this lane genuinely has: the path may fail to confirm the
        # client, but it must never name a DIFFERENT known one, and the category must not be
        # contested. Both are read from the preview's own recorded evidence.
        evidence = _json_field(row, "evidence", document_id, "{}")
        contexts = {k for k, _n in (evidence.get("client_contexts") or [])}
        _require("different_client" not in contexts,
                 f"document {document_id} has a different-client context")

        # The repaired safety basis. Note what is NOT used: the absence of
        # ``no_client_confirmation_in_path`` from ``reasons``. That absence is an artefact of
        # the preview's early return and proves nothing, so corroboration is re-derived here
        # from the recorded evidence rather than taken on trust.
        segments = [s.get("client_segment") or "" for s in (evidence.get("sources") or [])
                    if s.get("available") and s.get("taxonomy")]
        corroboration = client_corroboration(
            (row.get("proposed_scope_type") or "").strip(),
            row.get("proposed_scope_name") or "", segments)
        _require(corroboration is not None,
                 f"document {document_id} has no source path that mechanically names its "
                 f"client (owner {row.get('proposed_scope_name')!r}, path client folders "
                 f"{segments}); ownership alone is not corroboration")

        scope_type = (row.get("proposed_scope_type") or "").strip()
        _require(scope_type in SCOPE_TYPES,
                 f"document {document_id} scope type {scope_type!r} is not one of "
                 f"{list(SCOPE_TYPES)}")
        scope_name = row.get("proposed_scope_name") or ""
        category = row.get("proposed_top_level_category") or ""
        _require(scope_name.strip() != "", f"document {document_id} has a blank scope name")
        _require(category.strip() != "", f"document {document_id} has a blank category")

        segments = _json_field(row, "proposed_folder_segments", document_id)
        _require(isinstance(segments, list) and all(isinstance(s, str) for s in segments),
                 f"document {document_id} proposed_folder_segments is not a list of strings")
        _require(len(segments) == DESTINATION_DEPTH,
                 f"document {document_id} folder depth is {len(segments)}, must be "
                 f"{DESTINATION_DEPTH}")
        _require(segments[0] == scope_name,
                 f"document {document_id} first segment {segments[0]!r} != scope name "
                 f"{scope_name!r}")
        _require(segments[1] == category,
                 f"document {document_id} second segment {segments[1]!r} != category "
                 f"{category!r}")
        _require((row.get("proposed_folder_path") or "") == "/".join(segments),
                 f"document {document_id} proposed_folder_path does not match its segments")

        # The destination is RECOMPUTED, never read from the artifact.
        destination = category_code(scope_type, scope_id, category)
        _require("--year-" not in destination,
                 f"document {document_id} resolves to a YEAR destination")
        _require(destination.count("--") == DESTINATION_DEPTH - 1,
                 f"document {document_id} destination is not a depth-{DESTINATION_DEPTH} node")

        parent = client_code(scope_type, scope_id)
        _claim(clients, parent, {"code": parent, "name": scope_name, "kind": "client",
                                 "parent_code": None})
        _claim(categories, destination, {"code": destination, "name": category, "kind": "category",
                                         "parent_code": parent})
        documents.append({
            "document_id": document_id, "scope_type": scope_type, "scope_id": scope_id,
            "scope_name": scope_name, "category": category, "tax_year": None,
            "depth": DESTINATION_DEPTH, "folder_code": destination,
            "folder_path": row.get("proposed_folder_path") or "",
        })
        corroborations[document_id] = corroboration

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
        "corroborations": corroborations,
        "folders": folders,
        # How the plan's nodes must split between "already there" and "to create" is a REVIEWED fact
        # about production that the artifact cannot state, so it travels with the plan. None when
        # census enforcement is off, which is how a test drives the real path against a fixture.
        "expect_new_folders": EXPECTED_NEW_FOLDERS if enforce_census else None,
        "expect_reused_folders": EXPECTED_REUSED_FOLDERS if enforce_census else None,
        "census": {
            "documents": len(documents), "client_nodes": len(clients),
            "category_nodes": len(categories), "year_nodes": 0, "folder_nodes": len(folders),
            "by_category": _census(documents, "category"),
            "by_scope_type": _census(documents, "scope_type"),
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
    """``APPLY-DOCUMENT-FILING-BATCH3-12``."""
    return f"APPLY-{BATCH_NAME}-{int(document_count)}"


def rollback_phrase(document_count) -> str:
    return f"ROLLBACK-{BATCH_NAME}-{int(document_count)}"
