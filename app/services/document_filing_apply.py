"""Document filing persistence — BATCH 1 plan layer. Deterministic, pure, and it never writes.

WHAT THIS BATCH DOES, AND THE ONE COLUMN IT TOUCHES
----------------------------------------------------
``document_folders`` is empty and ``documents.folder_id`` is NULL across the whole corpus. This batch
creates the folder hierarchy the frozen filing preview proposes and points exactly the reviewed
AUTO_FILE_SAFE documents at it. **Only ``documents.folder_id`` changes.** Category, classification,
subcategory, tags, display_name, ownership, review_status, lifecycle and every storage/provenance
field are left exactly as they are, and the code-audit that preceded this batch is why:

* ``documents.classification`` carries a CHECK constraint over a fixed 14-value domain vocabulary
  (``client``, ``tax``, ``legal`` …). The service categories here — "Tax Preparation", "Wealth
  Accounts" — are not members of it, so writing them would be rejected by Postgres outright.
* ``documents.category`` and ``subcategory`` already mean something else to the Documents screen
  (``_filed_category`` reads ``classification or category``; a set ``subcategory`` flips a document
  type from *derived* to *filed*). Writing them would silently restate an inference as a fact.
* ``tags`` is polymorphic — sometimes an object, sometimes an array — and a recorded ``tax_year``
  makes the tax-year engine report *recorded* instead of *inferred*, which changes what staff see.

So the destination lives in the folder tree, and nothing else is disturbed. ``folder_id`` is read by
no UI today (the library's folder filter is opt-in and no template renders folders), which is exactly
what makes it the safe first write.

THE PLAN IS DERIVED FROM A FROZEN FILE, NOT FROM THE DATABASE
--------------------------------------------------------------
The reviewed artifact is a CSV whose SHA256 is pinned. This module parses it, re-proves every
structural claim about it, and produces the folder manifest and per-document destinations. It reads
no database at all — the apply script is what compares this plan to live state.

TWO DIFFERENT HASHES, ON PURPOSE
--------------------------------
``FROZEN_CSV_SHA256`` pins the FILE'S BYTES: it is the reviewed artifact's identity, and a byte that
moved means the reviewed thing is not the thing in front of us.

:func:`plan_digest` and :func:`folder_manifest_digest` hash the PARSED CONTENT as canonical JSON. A
CSV re-saved with CRLF is byte-different but plan-identical, and the plan digest says so — which is
what makes the digest usable as a cross-platform gate while the SHA stays an exact artifact pin.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

#: Batch identity. Encoded into both confirmation phrases and every audit row.
BATCH_NAME = "DOCUMENT-FILING-BATCH1"

#: The reviewed preview artifact. Byte-pinned.
FROZEN_CSV_SHA256 = "b944fd1a92ab3c9516b75a17aac9d4b2aac04f616573dd228b12eef6c6daa4c2"

#: Structural expectations a human approved. Every one is enforced, not assumed.
EXPECTED_PREVIEW_ROWS = 73240
EXPECTED_AUTO_ROWS = 16304
EXPECTED_CLIENT_NODES = 845
EXPECTED_CATEGORY_NODES = 913
EXPECTED_YEAR_NODES = 1376
EXPECTED_FOLDER_NODES = 3134

#: The approved service-category census.
EXPECTED_CATEGORY_CENSUS = {
    "Tax Preparation": 11296,
    "Wealth Accounts": 2284,
    "Sales & Litter Tax": 1885,
    "Payroll": 572,
    "Bookkeeping": 267,
}

#: Depth census: a document is filed at the category level or, when the year is strongly supported,
#: one level deeper. Never at the client root.
EXPECTED_DEPTH_CENSUS = {2: 9361, 3: 6943}

AUTO_FILE_SAFE = "AUTO_FILE_SAFE"
RESOLVED = "resolved"

#: Scope types the filing tree understands, mirroring ``document_filing_preview.client_scope``.
SCOPE_TYPES = ("person", "household", "organization")

#: Folder rows are created with a NULL classification. The audit found no reader, no constraint and
#: no test for ``document_folders.classification``, and the only vocabulary in scope is the DOCUMENT
#: domain vocabulary, which a service category is not a member of. NULL is the honest value.
FOLDER_CLASSIFICATION = None

_YEAR_RE = re.compile(r"^\d{4}$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

#: Ordering of the folder manifest: clients, then categories, then years — parents before children,
#: which is also the insert order the apply must use.
FOLDER_KINDS = ("client", "category", "year")

FOLDER_FIELDS = ("code", "name", "kind", "parent_code")
PLAN_FIELDS = ("document_id", "scope_type", "scope_id", "scope_name", "category", "tax_year",
               "depth", "folder_code", "folder_path")


class PlanError(ValueError):
    """The frozen artifact is not the reviewed artifact, or does not say what it must say."""


# --- deterministic naming ------------------------------------------------------------------------

def slugify(value) -> str:
    """Lowercase ``a-z0-9`` with single ``-`` separators; ``unnamed`` when nothing survives.

    The exact rule the folder-code design was audited against. Kept as one function because the code
    is the only uniqueness guarantee in the folder table — ``document_folders`` has a unique index on
    ``code`` and nothing else, not even ``(parent_folder_id, name)``.
    """
    text = _SLUG_STRIP_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return text or "unnamed"


def client_code(scope_type, scope_id) -> str:
    return f"client-{slugify(scope_type)}-{int(scope_id)}"


def category_code(scope_type, scope_id, category) -> str:
    return f"{client_code(scope_type, scope_id)}--category-{slugify(category)}"


def year_code(scope_type, scope_id, category, year) -> str:
    return f"{category_code(scope_type, scope_id, category)}--year-{int(year):04d}"


# --- the frozen artifact -------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _require(condition, message):
    if not condition:
        raise PlanError(f"ABORT: {message}")


def read_frozen_rows(csv_path, *, expect_sha=FROZEN_CSV_SHA256,
                     expect_rows=EXPECTED_PREVIEW_ROWS) -> list[dict]:
    """Verify the artifact's bytes, then parse it as UTF-8. Nothing is accepted before the SHA."""
    path = Path(csv_path)
    _require(path.is_file(), f"frozen preview not found: {path}")
    digest = sha256_of(path)
    _require(digest == expect_sha,
             f"frozen preview SHA256 {digest} != approved {expect_sha}")

    # The evidence column carries large JSON blobs; the default field limit is too small for it.
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(1024 * 1024 * 64)
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    finally:
        csv.field_size_limit(previous_limit)
    if expect_rows is not None:
        _require(len(rows) == expect_rows,
                 f"frozen preview has {len(rows)} rows, approved {expect_rows}")
    return rows


def _parse_segments(raw, document_id):
    try:
        segments = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"ABORT: document {document_id} has unreadable "
                        f"proposed_folder_segments") from exc
    _require(isinstance(segments, list) and all(isinstance(s, str) for s in segments),
             f"document {document_id} proposed_folder_segments is not a list of strings")
    return segments


def _parse_conflicts(raw, document_id):
    if raw is None or str(raw).strip() == "":
        return []
    try:
        conflicts = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"ABORT: document {document_id} has unreadable conflicts") from exc
    _require(isinstance(conflicts, list),
             f"document {document_id} conflicts is not a list")
    return conflicts


def build_plan(csv_path, *, expect_sha=FROZEN_CSV_SHA256, expect_rows=EXPECTED_PREVIEW_ROWS,
               expect_auto_rows=EXPECTED_AUTO_ROWS, enforce_census=True) -> dict[str, Any]:
    """The whole deterministic plan: folder manifest, per-document destinations, digests.

    Pure — no database. Every structural claim the reviewer was shown is re-proved here rather than
    trusted, because the CSV is an artifact a human can edit and the SHA only proves it is the same
    artifact, not that the artifact is coherent.
    """
    rows = read_frozen_rows(csv_path, expect_sha=expect_sha, expect_rows=expect_rows)
    auto = [r for r in rows if (r.get("filing_status") or "").strip() == AUTO_FILE_SAFE]
    if expect_auto_rows is not None:
        _require(len(auto) == expect_auto_rows,
                 f"{len(auto)} AUTO_FILE_SAFE rows, approved {expect_auto_rows}")

    documents: list[dict] = []
    seen_ids: set[int] = set()
    clients: dict[str, dict] = {}
    categories: dict[str, dict] = {}
    years: dict[str, dict] = {}

    for row in auto:
        try:
            document_id = int(row["document_id"])
        except (TypeError, ValueError, KeyError) as exc:
            raise PlanError(f"ABORT: unreadable document_id in row {row!r}") from exc
        _require(document_id not in seen_ids, f"duplicate document_id {document_id}")
        seen_ids.add(document_id)

        _require((row.get("filing_scope_state") or "").strip().lower() == RESOLVED,
                 f"document {document_id} filing_scope_state is "
                 f"{row.get('filing_scope_state')!r}, not {RESOLVED!r}")
        _require(not _parse_conflicts(row.get("conflicts"), document_id),
                 f"document {document_id} carries conflicts")

        scope_type = (row.get("proposed_scope_type") or "").strip()
        _require(scope_type in SCOPE_TYPES,
                 f"document {document_id} scope type {scope_type!r} is not one of "
                 f"{list(SCOPE_TYPES)}")
        try:
            scope_id = int(row["proposed_scope_id"])
        except (TypeError, ValueError, KeyError) as exc:
            raise PlanError(f"ABORT: document {document_id} has no usable "
                            f"proposed_scope_id") from exc
        scope_name = (row.get("proposed_scope_name") or "")
        _require(scope_name.strip() != "", f"document {document_id} has a blank scope name")
        category = (row.get("proposed_top_level_category") or "")
        _require(category.strip() != "", f"document {document_id} has a blank category")

        segments = _parse_segments(row.get("proposed_folder_segments"), document_id)
        depth = len(segments)
        _require(depth in (2, 3),
                 f"document {document_id} folder depth is {depth}, must be 2 or 3")
        _require(segments[0] == scope_name,
                 f"document {document_id} first segment {segments[0]!r} != scope name "
                 f"{scope_name!r}")
        _require(segments[1] == category,
                 f"document {document_id} second segment {segments[1]!r} != category "
                 f"{category!r}")

        year = None
        if depth == 3:
            third = segments[2]
            _require(bool(_YEAR_RE.match(third)),
                     f"document {document_id} third segment {third!r} is not a four-digit year")
            _require(str(row.get("proposed_tax_year") or "").strip() == third,
                     f"document {document_id} year segment {third!r} != proposed_tax_year "
                     f"{row.get('proposed_tax_year')!r}")
            year = int(third)

        path_value = row.get("proposed_folder_path") or ""
        _require(path_value == "/".join(segments),
                 f"document {document_id} proposed_folder_path {path_value!r} does not match its "
                 "segments")

        c_code = client_code(scope_type, scope_id)
        cat_code = category_code(scope_type, scope_id, category)
        _claim(clients, c_code, {"code": c_code, "name": scope_name, "kind": "client",
                                 "parent_code": None})
        _claim(categories, cat_code, {"code": cat_code, "name": category, "kind": "category",
                                      "parent_code": c_code})
        if year is None:
            destination = cat_code
        else:
            y_code = year_code(scope_type, scope_id, category, year)
            _claim(years, y_code, {"code": y_code, "name": str(year), "kind": "year",
                                   "parent_code": cat_code})
            destination = y_code

        documents.append({
            "document_id": document_id, "scope_type": scope_type, "scope_id": scope_id,
            "scope_name": scope_name, "category": category, "tax_year": year, "depth": depth,
            "folder_code": destination, "folder_path": path_value,
        })

    folders = ([clients[k] for k in sorted(clients)]
               + [categories[k] for k in sorted(categories)]
               + [years[k] for k in sorted(years)])
    documents.sort(key=lambda d: d["document_id"])

    if enforce_census:
        _require(len(clients) == EXPECTED_CLIENT_NODES,
                 f"{len(clients)} client nodes, approved {EXPECTED_CLIENT_NODES}")
        _require(len(categories) == EXPECTED_CATEGORY_NODES,
                 f"{len(categories)} category nodes, approved {EXPECTED_CATEGORY_NODES}")
        _require(len(years) == EXPECTED_YEAR_NODES,
                 f"{len(years)} year nodes, approved {EXPECTED_YEAR_NODES}")
        _require(len(folders) == EXPECTED_FOLDER_NODES,
                 f"{len(folders)} folder nodes, approved {EXPECTED_FOLDER_NODES}")
        census = _category_census(documents)
        _require(census == EXPECTED_CATEGORY_CENSUS,
                 f"category census {census} != approved {EXPECTED_CATEGORY_CENSUS}")
        depths = _depth_census(documents)
        _require(depths == EXPECTED_DEPTH_CENSUS,
                 f"depth census {depths} != approved {EXPECTED_DEPTH_CENSUS}")

    return {
        "batch": BATCH_NAME,
        "frozen_csv_sha256": expect_sha,
        "documents": documents,
        "folders": folders,
        "census": {
            "preview_rows": len(rows),
            "auto_file_safe": len(documents),
            "client_nodes": len(clients),
            "category_nodes": len(categories),
            "year_nodes": len(years),
            "folder_nodes": len(folders),
            "by_category": _category_census(documents),
            "by_depth": _depth_census(documents),
        },
        "plan_digest": plan_digest(documents),
        "folder_manifest_digest": folder_manifest_digest(folders),
    }


def _claim(registry: dict, code: str, node: dict) -> None:
    """Register a folder node, refusing a code two different names both claim.

    The collision has to be caught HERE, as the node is first seen. A ``setdefault`` would keep the
    first name and silently drop the second, so the two-names-one-code case — two clients whose
    display names differ under the same scope id — would never be visible again. The unique index
    would not catch it either: it is one code, inserted once, with somebody else's name on it.
    """
    existing = registry.get(code)
    if existing is None:
        registry[code] = node
        return
    _require(existing["name"] == node["name"],
             f"folder code {code!r} is claimed by two different names: "
             f"{existing['name']!r} and {node['name']!r}")
    _require(existing["parent_code"] == node["parent_code"],
             f"folder code {code!r} is claimed under two different parents")


def _category_census(documents) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in documents:
        out[d["category"]] = out.get(d["category"], 0) + 1
    return dict(sorted(out.items()))


def _depth_census(documents) -> dict[int, int]:
    out: dict[int, int] = {}
    for d in documents:
        out[d["depth"]] = out.get(d["depth"], 0) + 1
    return dict(sorted(out.items()))


# --- digests -------------------------------------------------------------------------------------

def _canonical(payload) -> bytes:
    """UTF-8 bytes of canonical JSON: sorted keys, no whitespace, no ASCII escaping.

    Hashing this rather than a file means the digest is a statement about CONTENT. The same plan
    parsed from an LF file and from a CRLF file produces identical bytes here, so the digest is a
    usable gate on any platform while ``FROZEN_CSV_SHA256`` stays an exact artifact pin.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def plan_digest(documents) -> str:
    ordered = [{k: d[k] for k in PLAN_FIELDS}
               for d in sorted(documents, key=lambda d: d["document_id"])]
    return hashlib.sha256(_canonical(ordered)).hexdigest()


def folder_manifest_digest(folders) -> str:
    ordered = [{k: f[k] for k in FOLDER_FIELDS}
               for f in sorted(folders, key=lambda f: (FOLDER_KINDS.index(f["kind"]), f["code"]))]
    return hashlib.sha256(_canonical(ordered)).hexdigest()


# --- confirmation phrases --------------------------------------------------------------------------

def confirm_phrase(document_count) -> str:
    """``APPLY-DOCUMENT-FILING-BATCH1-16304`` — batch identity plus the reviewed row count."""
    return f"APPLY-{BATCH_NAME}-{int(document_count)}"


def rollback_phrase(document_count) -> str:
    return f"ROLLBACK-{BATCH_NAME}-{int(document_count)}"
