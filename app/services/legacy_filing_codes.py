"""Pure primitives shared by the legacy filing batches. No apply path, no database, no policy.

WHY THIS MODULE EXISTS
-----------------------
Batch 1's plan layer (``document_filing_apply``) is retired: its POLICY allowed depth-2 destinations
and combined folder creation with document mutation under one authorization. But batch 2, which is
merged and applied in production, imports ten neutral helpers from it — slug rules, folder codes,
content digests and the field tuples those digests hash over.

Retiring a policy must not break code that merely borrowed its arithmetic. So the helpers live here,
where nothing about them is a filing decision, and ``document_filing_apply`` re-exports them for the
merged callers while keeping only its *apply* entry points fail-closed.

THESE FUNCTIONS ARE FROZEN, NOT MAINTAINED
-------------------------------------------
``EXPECTED_PLAN_DIGEST`` and ``EXPECTED_FOLDER_MANIFEST_DIGEST`` in
:mod:`app.services.document_filing_batch2` are pinned against the exact bytes these produce, and
16,854 production documents are filed in folders whose codes :func:`client_code` and
:func:`category_code` generated. Changing any of them — the slug rule, the key order, the separator,
the JSON encoding — silently invalidates a pinned digest or orphans a live folder reference. They
are moved here VERBATIM and must stay that way.

The canonical architecture does not use them. Canonical folder identity is structural
(``owner_scope_type``, ``owner_scope_id``, ``service_code``, ``tax_year``) and its codes are
``cf-`` prefixed — see :mod:`app.services.canonical_filing`. Nothing new should import this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_YEAR_RE = re.compile(r"^\d{4}$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

#: Ordering of the folder manifest: clients, then categories, then years — parents before children,
#: which is also the insert order the legacy apply used.
FOLDER_KINDS = ("client", "category", "year")

FOLDER_FIELDS = ("code", "name", "kind", "parent_code")
PLAN_FIELDS = ("document_id", "scope_type", "scope_id", "scope_name", "category", "tax_year",
               "depth", "folder_code", "folder_path")


class PlanError(ValueError):
    """The frozen artifact is not the reviewed artifact, or does not say what it must say."""


# --- deterministic naming ------------------------------------------------------------------------

def slugify(value) -> str:
    """Lowercase ``a-z0-9`` with single ``-`` separators; ``unnamed`` when nothing survives.

    The exact rule the legacy folder-code design was audited against. Kept as one function because
    the code is the only uniqueness guarantee in the legacy folder tree — ``document_folders`` had a
    unique index on ``code`` and nothing else, not even ``(parent_folder_id, name)``.
    """
    text = _SLUG_STRIP_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return text or "unnamed"


def client_code(scope_type, scope_id) -> str:
    return f"client-{slugify(scope_type)}-{int(scope_id)}"


def category_code(scope_type, scope_id, category) -> str:
    return f"{client_code(scope_type, scope_id)}--category-{slugify(category)}"


def year_code(scope_type, scope_id, category, year) -> str:
    return f"{category_code(scope_type, scope_id, category)}--year-{int(year):04d}"


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(Path(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --- digests -------------------------------------------------------------------------------------

def _canonical(payload) -> bytes:
    """UTF-8 bytes of canonical JSON: sorted keys, no whitespace, no ASCII escaping."""
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


# --- reading a legacy code back ------------------------------------------------------------------

_CLIENT_RE = re.compile(r"^client-(?P<scope>[a-z]+)-(?P<id>\d+)$")
_CATEGORY_RE = re.compile(r"^client-(?P<scope>[a-z]+)-(?P<id>\d+)--category-(?P<cat>[a-z0-9-]+)$")
_YEAR_RE_CODE = re.compile(
    r"^client-(?P<scope>[a-z]+)-(?P<id>\d+)--category-(?P<cat>[a-z0-9-]+)--year-(?P<year>\d{4})$")


def parse_legacy_code(code):
    """Read a legacy folder code back into its parts, or ``None`` when it is not one.

    Reconciliation needs to know what an existing folder MEANS, and the only thing a legacy folder
    carries is its code — it has no identity columns. Parsing is lossy in one direction on purpose:
    the category slug is a slug, so ``sales-litter-tax`` cannot be turned back into the exact label
    ``Sales & Litter Tax`` by string surgery. The caller maps the slug through the canonical
    vocabulary rather than guessing.
    """
    text = str(code or "")
    for kind, pattern in (("year", _YEAR_RE_CODE), ("category", _CATEGORY_RE),
                          ("client", _CLIENT_RE)):
        match = pattern.match(text)
        if match:
            parts = match.groupdict()
            return {
                "kind": kind,
                "scope_type": parts["scope"],
                "scope_id": int(parts["id"]),
                "category_slug": parts.get("cat"),
                "tax_year": int(parts["year"]) if parts.get("year") else None,
            }
    return None
