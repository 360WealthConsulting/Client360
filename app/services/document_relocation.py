"""Guarded canonical document-storage relocation (PLAN -> COPY -> VERIFY -> DB REPOINT).
WHAT THIS DOES
    Moves the *storage location* of existing ``documents`` rows onto the canonical data root
    (``CLIENT360_DATA_ROOT``, e.g. ``D:\\360PlusData``) by COPYING bytes to the canonical
    destination, VERIFYING them, and then repointing the row's storage columns.
WHAT IT NEVER DOES
    It never deletes, moves, renames, truncates or overwrites a source file - the legacy location
    stays intact as the rollback. It never creates or removes a ``documents`` row, never touches
    ownership, provenance, OCR, classifications or facts, and never merges or deduplicates rows.
    Document-row merge/deduplication is the separate document_merge_execute concern.
ARCHITECTURE REUSED (nothing new invented)
    * ``app.services.storage_paths``            - the ONE canonical root resolution
    * ``app.services.migration.storage``        - StorageService / LocalFilesystemStorage, the only
                                                  path to the filesystem. It exposes stat/exists/
                                                  read/write and NO delete or rename, which is what
                                                  makes source destruction unreachable from here.
    * ``app.services.migration.relocation``     - _norm/_under case-insensitive Windows path
                                                  comparison, already used by the repository job
    * ``app.deploy.document_integrity``         - chunked _sha256 and reference->path resolution
    * ``app.security.audit``                    - the existing hash-chained audit
PROVENANCE IS AUTHORITY, PATHNAME IS A HINT
    A pathname alone never decides a destination. ``document_sources.source_system`` decides which
    canonical tree a row belongs to; the path only narrows it. Where the two disagree the row is
    BLOCKED, and where provenance is absent or ambiguous it is REVIEW_REQUIRED. Nothing is
    relocated automatically on a guess.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import text

from app.db import engine
from app.deploy.document_integrity import _as_path
from app.security.audit import write_audit_event
from app.services.migration.storage import LocalFilesystemStorage
from app.services.storage_paths import data_root, document_root, repository_root

#: Row classifications. Only SAFE is ever executable.
SAFE = "SAFE"
REVIEW = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"
ALREADY_CANONICAL = "ALREADY_CANONICAL"
EMPTY_URI = "EMPTY_URI"

EXECUTABLE_CLASSIFICATIONS = frozenset({SAFE})
NON_EXECUTABLE_CLASSIFICATIONS = frozenset({REVIEW, BLOCKED, ALREADY_CANONICAL, EMPTY_URI})

AUDIT_ACTION = "document.storage.relocated"
AUDIT_ENTITY = "document_storage_location"

DEFAULT_BATCH_SIZE = 200
_READ_CHUNK = 1024 * 1024

#: The three canonical document sources, and the per-source env var that still wins (storage_paths).
_SOURCES = (("TaxDome", "CLIENT360_TAXDOME_DOCUMENT_ROOT"),
            ("SharePoint", "CLIENT360_SHAREPOINT_DOCUMENT_ROOT"),
            ("Drake", "CLIENT360_DRAKE_DOCUMENT_ROOT"))

#: source_system values that map onto a canonical document tree. Anything else is not a canonical
#: local-copy source and cannot decide a destination on its own.
_PROVENANCE_TO_SOURCE = {
    "sharepoint": "SharePoint",
    "taxdome": "TaxDome",
    "taxdome drive": "TaxDome",
    "taxdome drive sync": "TaxDome",
    "drake": "Drake",
}

#: Legacy roots recognised by PATHNAME. Compared with os.path.normcase, so C:\...\Data\... and
#: C:\...\data\... are one root on Windows (requirement A/B).
_LEGACY_DOC_ROOT = r"C:\Client360\Data\Documents"
_LEGACY_D_CONTENT = r"D:\Client360\Content"


class RelocationError(RuntimeError):
    """A refusal. Raised BEFORE any filesystem or database mutation."""


class StalePlanError(RelocationError):
    """The row moved since the plan was generated. The plan row is refused, never adapted."""


def _now():
    return datetime.now(UTC)


def _components(path) -> list[str]:
    """Split a Windows-or-POSIX path into components, preserving their original case."""
    return [c for c in str(path).replace("\\", "/").split("/") if c not in ("", ".")]


def _norm(path) -> str:
    """Case-insensitive, separator-insensitive form for COMPARISON only.

    migration.relocation._norm uses os.path.normcase, which folds case on Windows and is a NO-OP on
    POSIX. These are Windows paths by definition, and the planner must classify them identically
    wherever it runs (and be testable off-Windows), so the fold is explicit here rather than
    inherited from the host platform."""
    return "\\".join(c.casefold() for c in _components(path)) if path else ""


def _under(path_norm, root_norm) -> bool:
    """True when the normalised ``path_norm`` sits at or under the normalised ``root_norm``."""
    if not path_norm or not root_norm:
        return False
    return path_norm == root_norm or path_norm.startswith(root_norm + "\\")


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


# --- root resolution -----------------------------------------------------------------------------

def _host_path(root: str, anchor: str) -> str:
    r"""Render a storage_paths root for the HOST filesystem, preserving its anchor.

    storage_paths builds Windows-shaped roots (backslash-joined) and its _join strips leading
    separators - harmless for ``D:\360PlusData``, which is absolute without one, but it turns a
    POSIX absolute base into a RELATIVE path. That would make every destination relative to the
    process CWD. On Windows this is a no-op; off Windows it restores the leading separator and uses
    the host separator so the destination is always absolute and always a real directory tree."""
    parts = [c for c in root.replace(chr(92), "/").split("/") if c not in ("", ".")]
    joined = os.sep.join(parts)
    if anchor.startswith(("/", chr(92))) and not joined.startswith(("/", chr(92))):
        return os.sep + joined
    return joined


def canonical_roots() -> dict[str, str]:
    """The canonical destination root per source, straight from storage_paths. No new architecture."""
    anchor = data_root() or ""
    return {source: _host_path(document_root(source, env), anchor)
            for source, env in _SOURCES}


def _recognised_roots() -> list[tuple[str, str, str]]:
    """(kind, label, normalised_root) for every root the planner recognises, longest first."""
    roots: list[tuple[str, str, str]] = []
    for source, _env in _SOURCES:
        roots.append(("canonical", source, _norm(canonical_roots()[source])))
    for source, _env in _SOURCES:
        roots.append(("legacy_documents", source, _norm(os.path.join(_LEGACY_DOC_ROOT, source))))
    roots.append(("legacy_d_content", "Content", _norm(_LEGACY_D_CONTENT)))
    roots.append(("repository", "Repository", _norm(repository_root())))
    base = data_root()
    if base:
        roots.append(("canonical_base", "DataRoot", _norm(base)))
    # Longest root first so C:\...\Documents\TaxDome wins over a shorter enclosing root.
    return sorted(roots, key=lambda r: len(r[2]), reverse=True)


def classify_root(reference: str | None) -> tuple[str, str, str] | None:
    """Which recognised root a storage reference lives under, or None. Pure string work."""
    p = _as_path(reference)
    if p is None:
        return None
    norm = _norm(str(p))
    for kind, label, root in _recognised_roots():
        if _under(norm, root):
            return (kind, label, root)
    return None


# --- provenance ----------------------------------------------------------------------------------

def _provenance_source(source_systems) -> tuple[str | None, list[str]]:
    """Map document_sources.source_system values onto ONE canonical source, or None.

    Returns (source_or_None, reasons). Several distinct canonical sources on one row is ambiguity,
    never a majority vote: identical content legitimately arrives from more than one system and
    picking a winner would be a guess."""
    mapped = {_PROVENANCE_TO_SOURCE[s.strip().lower()] for s in source_systems
              if s and s.strip().lower() in _PROVENANCE_TO_SOURCE}
    if not mapped:
        return None, (["provenance_absent"] if not source_systems
                      else ["provenance_not_a_canonical_source"])
    if len(mapped) > 1:
        return None, ["provenance_ambiguous_multiple_sources"]
    return next(iter(mapped)), []


def _destination_for(source, relative) -> str:
    root = canonical_roots()[source]
    return os.path.join(root, relative) if relative else root


def _relative_under(reference, root_norm) -> str:
    r"""The sub-path of ``reference`` below a recognised root, with its ORIGINAL casing preserved.

    Matching is case-insensitive but the destination keeps the real folder names, so relocating
    C:\...\taxdome\2024\Return.pdf does not rewrite "Return.pdf" in a folded case."""
    parts = _components(_as_path(reference))
    depth = len(_components(root_norm))
    return os.path.join(*parts[depth:]) if len(parts) > depth else ""


# --- per-row classification ----------------------------------------------------------------------

def classify_row(row, source_systems) -> dict:
    """Decide one document row's relocation. Pure function - issues no query and touches no disk.

    Rule order matters and is deliberate: an empty reference, then already-canonical, then a
    pathname-derived candidate, then provenance as the authority over that candidate."""
    reasons: list[str] = []
    reference = row.get("storage_uri") or row.get("storage_path")
    prov_source, prov_reasons = _provenance_source(source_systems)
    reasons.extend(prov_reasons)

    if not (reference or "").strip():
        return {"classification": EMPTY_URI, "current_root": None, "destination": None,
                "provenance_source": prov_source,
                "reason_codes": sorted({*reasons, "empty_storage_uri"})}

    found = classify_root(reference)
    if found is None:
        return {"classification": REVIEW, "current_root": None, "destination": None,
                "provenance_source": prov_source,
                "reason_codes": sorted({*reasons, "unrecognised_storage_root"})}

    kind, label, root_norm = found
    if kind in ("canonical", "canonical_base"):
        return {"classification": ALREADY_CANONICAL, "current_root": label, "destination": None,
                "provenance_source": prov_source,
                "reason_codes": sorted({*reasons, "already_under_canonical_root"})}

    if kind == "repository":
        # A curated Repository item is not a per-source canonical copy; moving it would change what
        # the migration framework owns.
        return {"classification": REVIEW, "current_root": "Repository", "destination": None,
                "provenance_source": prov_source,
                "reason_codes": sorted({*reasons, "curated_repository_item"})}

    relative = _relative_under(reference, root_norm)

    if kind == "legacy_documents":
        # The pathname proposes a source; provenance must not contradict it.
        if prov_source is None:
            return {"classification": REVIEW, "current_root": f"legacy:{label}",
                    "destination": None, "provenance_source": None,
                    "reason_codes": sorted({*reasons, "provenance_cannot_confirm_path_source"})}
        if prov_source != label:
            return {"classification": BLOCKED, "current_root": f"legacy:{label}",
                    "destination": None, "provenance_source": prov_source,
                    "reason_codes": sorted({*reasons, "provenance_conflicts_with_path_source"})}
        return {"classification": SAFE, "current_root": f"legacy:{label}",
                "destination": _destination_for(label, relative),
                "provenance_source": prov_source,
                "reason_codes": sorted({*reasons, "legacy_documents_root"})}

    if kind == "legacy_d_content":
        # D:\Client360\Content carries NO source in its pathname. Provenance alone may decide, and
        # only when it is unambiguous - never inferred from the pathname.
        if prov_source is None:
            return {"classification": REVIEW, "current_root": "legacy:Content",
                    "destination": None, "provenance_source": None,
                    "reason_codes": sorted({*reasons, "d_content_requires_provenance"})}
        return {"classification": SAFE, "current_root": "legacy:Content",
                "destination": _destination_for(prov_source, relative),
                "provenance_source": prov_source,
                "reason_codes": sorted({*reasons, "d_content_resolved_by_provenance"})}

    return {"classification": REVIEW, "current_root": label, "destination": None,
            "provenance_source": prov_source,
            "reason_codes": sorted({*reasons, "unhandled_root_kind"})}


# --- storage_path: a SOURCE-RELATIVE pointer, not a second absolute one -------------------------
# Production rows demonstrate the two columns are not duplicates:
#     storage_uri  C:\Client360\data\Documents\TaxDome\Aaron Casper\...\2023\file.pdf
#     storage_path Aaron Casper/Client uploaded documents/2023/file.pdf
# Relocation changes the physical ROOT. A relative storage_path is still correct afterwards and is
# therefore left exactly as it is - slashes, casing and all. It is only written when leaving it
# would strand it: when it duplicated the absolute legacy URI.

def _storage_path_decision(storage_path, storage_uri, relative, destination):
    """Decide what happens to documents.storage_path. Returns (new_value_or_None, reasons, block).

    ``new_value_or_None`` is None when the column must NOT be written. ``block`` is a reason code
    when the existing value cannot be reconciled, in which case the row is REVIEW_REQUIRED rather
    than having a canonical-relative path invented for it."""
    sp = (storage_path or "").strip()
    if not sp:
        return None, ["storage_path_empty_left_unchanged"], None
    if _is_absolute(sp):
        # This row genuinely carries an absolute pointer. Left alone it would keep addressing the
        # legacy file after the URI moves, so it moves with it - preserving ITS absolute semantics.
        if _norm(sp) == _norm(storage_uri or ""):
            return destination, ["storage_path_absolute_repointed_with_uri"], None
        return None, [], "storage_path_absolute_and_differs_from_storage_uri"
    if _norm(sp) == _norm(relative):
        # The normal case: a valid source-relative path that the root change does not affect.
        return None, ["storage_path_relative_preserved"], None
    return None, [], "storage_path_relative_does_not_match_storage_uri"


# --- PLAN: database metadata only. Touches no file and writes nothing --------------------------

def row_fingerprint(row, plan_row) -> str:
    """Everything that must not move between plan and apply for THIS row."""
    return hashlib.sha256(_canonical_json({
        "document_id": row["id"],
        "storage_provider": row["storage_provider"],
        "storage_uri": row["storage_uri"],
        "storage_path": row["storage_path"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "provenance_source": plan_row["provenance_source"],
        "classification": plan_row["classification"],
        "destination": plan_row["destination"],
        "new_storage_path": plan_row.get("new_storage_path"),
    }).encode()).hexdigest()


def _disambiguate(destination, taken, document_id, sha) -> tuple[str, list[str]]:
    """Two rows may legitimately hold identical bytes. Same destination + same sha is allowed to
    share; a DIFFERENT sha never overwrites - it gets a deterministic, non-destructive path."""
    key = _norm(destination)
    prior = taken.get(key)
    if prior is None:
        taken[key] = (document_id, sha)
        return destination, []
    prior_id, prior_sha = prior
    if sha and prior_sha and sha == prior_sha:
        return destination, ["destination_shared_identical_content"]
    stem, ext = os.path.splitext(destination)
    unique = f"{stem}__doc{document_id}{ext}"
    taken[_norm(unique)] = (document_id, sha)
    return unique, ["destination_collision_disambiguated"]


def plan(*, limit=None, conn=None) -> dict:
    """Deterministic relocation plan built from DB metadata ONLY.

    It reads documents + document_sources and classifies every row. It does NOT stat, hash, crawl
    or open a single file, and it writes nothing - to the database or the filesystem."""
    close = conn is None
    conn = conn or engine.connect()
    try:
        sql = ("SELECT id, storage_provider, storage_uri, storage_path, sha256, size_bytes,"
               " status, stored_name, original_name FROM documents ORDER BY id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = [dict(r) for r in conn.execute(text(sql)).mappings()]
        ids = [r["id"] for r in rows]
        prov: dict[int, list[str]] = defaultdict(list)
        if ids:
            for s in conn.execute(text(
                "SELECT document_id, source_system FROM document_sources "
                "WHERE document_id = ANY(:ids) ORDER BY document_id, source_system"),
                    {"ids": ids}).mappings():
                prov[s["document_id"]].append(s["source_system"])
    finally:
        if close:
            conn.close()

    planned, taken = [], {}
    by_class: dict[str, int] = defaultdict(int)
    by_current: dict[str, int] = defaultdict(int)
    by_destination_root: dict[str, int] = defaultdict(int)
    collisions, missing_metadata, conflicting_hashes = 0, [], []
    seen_dest_sha: dict[str, set] = defaultdict(set)
    safe_bytes = 0

    for row in rows:
        decided = classify_row(row, prov.get(row["id"], []))
        reasons = list(decided["reason_codes"])
        destination = decided["destination"]
        classification = decided["classification"]

        new_storage_path = None
        if classification == SAFE:
            if not row["sha256"] or not row["size_bytes"]:
                classification = REVIEW
                reasons.append("missing_sha_or_size")
                missing_metadata.append(row["id"])
                destination = None
            else:
                destination, extra = _disambiguate(destination, taken, row["id"], row["sha256"])
                reasons.extend(extra)
                if "destination_collision_disambiguated" in extra:
                    collisions += 1
                found_root = classify_root(row["storage_uri"] or row["storage_path"])
                relative = _relative_under(row["storage_uri"] or row["storage_path"],
                                           found_root[2]) if found_root else ""
                new_storage_path, sp_reasons, sp_block = _storage_path_decision(
                    row["storage_path"], row["storage_uri"], relative, destination)
                reasons.extend(sp_reasons)
                if sp_block:
                    classification = REVIEW
                    reasons.append(sp_block)
                    destination, new_storage_path = None, None
                else:
                    seen = seen_dest_sha[_norm(destination)]
                    seen.add(row["sha256"])
                    if len(seen) > 1:
                        conflicting_hashes.append(row["id"])
                    safe_bytes += int(row["size_bytes"] or 0)

        entry = {
            "document_id": row["id"],
            "storage_provider": row["storage_provider"],
            "storage_uri": row["storage_uri"],
            "storage_path": row["storage_path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "status": row["status"],
            "provenance_source_systems": sorted(set(prov.get(row["id"], []))),
            "provenance_source": decided["provenance_source"],
            "current_root": decided["current_root"],
            "classification": classification,
            "destination": destination,
            # None means: do NOT write documents.storage_path for this row.
            "new_storage_path": new_storage_path,
            "reason_codes": sorted(set(reasons)),
        }
        entry["fingerprint"] = row_fingerprint(row, entry)
        planned.append(entry)
        by_class[classification] += 1
        by_current[entry["current_root"] or "(none)"] += 1
        if destination:
            by_destination_root[_destination_root_label(destination)] += 1

    body = [p for p in planned if p["classification"] == SAFE]
    summary = {
        "rows_examined": len(rows),
        "SAFE": by_class[SAFE],
        "REVIEW_REQUIRED": by_class[REVIEW],
        "BLOCKED": by_class[BLOCKED],
        "ALREADY_CANONICAL": by_class[ALREADY_CANONICAL],
        "EMPTY_URI": by_class[EMPTY_URI],
        "counts_per_current_root": dict(sorted(by_current.items())),
        "counts_per_proposed_destination_root": dict(sorted(by_destination_root.items())),
        "safe_bytes_total": safe_bytes,
        "destination_collisions": collisions,
        "conflicting_hashes": sorted(conflicting_hashes),
        "missing_required_metadata": sorted(missing_metadata),
    }
    doc = {
        "plan_version": 1,
        "read_only": True,
        "wrote_anything": False,
        "hashed_any_file": False,
        "data_root": data_root(),
        "canonical_roots": canonical_roots(),
        "summary": summary,
        "expected_safe_rows": len(body),
        "expected_safe_bytes": safe_bytes,
        "rows": planned,
    }
    doc["plan_fingerprint"] = hashlib.sha256(_canonical_json(
        [[p["document_id"], p["fingerprint"]] for p in planned]).encode()).hexdigest()
    return doc


def _destination_root_label(destination) -> str:
    norm = _norm(destination)
    for source in canonical_roots():
        if _under(norm, _norm(canonical_roots()[source])):
            return source
    return "(other)"


# --- the ONLY filesystem surface ---------------------------------------------------------------
# Every physical operation this module can perform is a method here. There is deliberately no
# delete, no move, no rename-source and no truncate: source destruction is not merely forbidden by
# policy, it is unreachable because no code path exists to express it. The underlying
# StorageService (migration.storage) likewise exposes only stat/exists/read/write.

class RelocationStorage:
    """Copy-only filesystem access: stat, hash, mkdir-parents, and atomic copy-into-place."""

    def __init__(self, backend=None):
        self._backend = backend or LocalFilesystemStorage()
        self.operations: list[tuple[str, str]] = []          # instrumentation for the tests

    def stat(self, path):
        self.operations.append(("stat", path))
        return self._backend.stat(path)

    def exists(self, path) -> bool:
        self.operations.append(("exists", path))
        return self._backend.exists(path)

    def sha256(self, path) -> str:
        """Chunked read-only hash - the same technique as deploy.document_integrity._sha256."""
        self.operations.append(("hash", path))
        digest = hashlib.sha256()
        with open(path, "rb") as fh:                          # mode 'rb': read-only by construction
            while chunk := fh.read(_READ_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()

    def makedirs(self, path) -> None:
        self.operations.append(("makedirs", path))
        os.makedirs(path, exist_ok=True)

    def copy_into_place(self, source, destination) -> None:
        """Copy source -> destination via a temp file in the DESTINATION directory, fsynced, then
        os.replace() into final position, so a crash never leaves a partial canonical file.

        Reads the SOURCE only; the source handle is opened 'rb' and is never modified."""
        self.operations.append(("copy", f"{source} -> {destination}"))
        parent = os.path.dirname(destination)
        if parent:
            self.makedirs(parent)
        tmp = f"{destination}.{uuid.uuid4().hex[:8]}.part"
        with open(source, "rb") as src, open(tmp, "wb") as dst:
            while chunk := src.read(_READ_CHUNK):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, destination)                          # atomic within the same volume


def _is_absolute(path: str) -> bool:
    r"""Absolute on either platform: a POSIX/UNC leading separator or a Windows ``X:\`` drive."""
    if not path:
        return False
    return (path.startswith(("/", chr(92))) or os.path.isabs(path)
            or (len(path) > 2 and path[1] == ":" and path[2] in (chr(92), "/")))


def _resolve_source(entry) -> str | None:
    p = _as_path(entry.get("storage_uri") or entry.get("storage_path"))
    return str(p) if p else None


# --- PHASE 1: verify. Reads only; issues no database or filesystem MUTATION --------------------

def _prepare_row(conn, entry, storage) -> dict:
    """Re-read the row, confirm it still matches the approved plan, and inspect both sides.

    Every refusal below happens before a byte is written anywhere."""
    live = conn.execute(text(
        "SELECT id, storage_provider, storage_uri, storage_path, sha256, size_bytes, status"
        " FROM documents WHERE id = :i"), {"i": entry["document_id"]}).mappings().first()
    if live is None:
        raise StalePlanError(f"document {entry['document_id']} no longer exists")
    for field in ("storage_provider", "storage_uri", "storage_path", "sha256", "size_bytes"):
        if live[field] != entry[field]:
            raise StalePlanError(
                f"document {entry['document_id']}: {field} changed since the plan was generated "
                f"({entry[field]!r} -> {live[field]!r})")
    if entry["classification"] not in EXECUTABLE_CLASSIFICATIONS:
        raise RelocationError(
            f"document {entry['document_id']}: classification {entry['classification']} is not "
            f"executable")

    source = _resolve_source(entry)
    destination = entry["destination"]
    if not source or not destination:
        raise RelocationError(f"document {entry['document_id']}: no source or destination")
    if not _is_absolute(destination):
        # A relative destination would be resolved against the process CWD - never write there.
        raise RelocationError(
            f"document {entry['document_id']}: destination {destination!r} is not absolute; "
            f"check CLIENT360_DATA_ROOT")

    st = storage.stat(source)
    if not st.exists:
        raise RelocationError(f"document {entry['document_id']}: source missing at {source}")
    if getattr(st, "is_placeholder", False):
        raise RelocationError(
            f"document {entry['document_id']}: source is a cloud placeholder; hydrate it first")
    if entry["size_bytes"] and st.size != entry["size_bytes"]:
        raise RelocationError(
            f"document {entry['document_id']}: source size {st.size} != expected "
            f"{entry['size_bytes']}")
    source_sha = storage.sha256(source)
    if entry["sha256"] and source_sha != entry["sha256"]:
        raise RelocationError(
            f"document {entry['document_id']}: source sha256 does not match documents.sha256")

    dest_state, dest_sha = "absent", None
    if storage.exists(destination):
        dest_sha = storage.sha256(destination)
        if dest_sha == source_sha:
            dest_state = "identical"
        else:
            raise RelocationError(
                f"document {entry['document_id']}: destination {destination} already exists with "
                f"DIFFERENT content - refusing to overwrite")

    return {
        "document_id": entry["document_id"],
        "run_source": source,
        "destination": destination,
        "expected_sha256": entry["sha256"],
        "expected_size": entry["size_bytes"],
        "source_sha256": source_sha,
        "source_size": st.size,
        "destination_state": dest_state,
        "destination_sha256_before": dest_sha,
        "original_storage_provider": entry["storage_provider"],
        "original_storage_uri": entry["storage_uri"],
        "original_storage_path": entry["storage_path"],
        "provenance_source": entry["provenance_source"],
        "provenance_source_systems": entry["provenance_source_systems"],
        "new_storage_path": entry["new_storage_path"],
        "fingerprint": entry["fingerprint"],
    }


# --- PHASE 2: copy + verify bytes, then repoint the row ----------------------------------------

#: The ONLY documents columns this module may ever write. Everything else - ownership, sha256,
#: classifications, OCR, facts, relationships, provenance, Drake identity - is untouched.
#: storage_provider is listed but is NEVER written: relocation does not change the provider, the
#: bytes stay local. storage_path is written ONLY when the plan decided it must move (see
#: _storage_path_decision); a valid source-relative path is left exactly as it is.
WRITABLE_COLUMNS = ("storage_uri", "storage_path", "updated_at")
ALWAYS_WRITTEN_COLUMNS = ("storage_uri", "updated_at")
CONDITIONALLY_WRITTEN_COLUMNS = ("storage_path",)


def _copy_and_verify(prepared, storage) -> dict:
    """Copy the bytes and verify the destination BEFORE the caller is allowed to repoint the row."""
    if prepared["destination_state"] != "identical":
        storage.copy_into_place(prepared["run_source"], prepared["destination"])

    st = storage.stat(prepared["destination"])
    if not st.exists:
        raise RelocationError(f"document {prepared['document_id']}: destination missing after copy")
    if st.size != prepared["source_size"]:
        raise RelocationError(
            f"document {prepared['document_id']}: destination size {st.size} != source "
            f"{prepared['source_size']}")
    dest_sha = storage.sha256(prepared["destination"])
    if dest_sha != prepared["source_sha256"]:
        raise RelocationError(
            f"document {prepared['document_id']}: destination sha256 does not match the source")
    if prepared["expected_sha256"] and dest_sha != prepared["expected_sha256"]:
        raise RelocationError(
            f"document {prepared['document_id']}: destination sha256 does not match "
            f"documents.sha256")
    return {"destination_sha256": dest_sha, "destination_size": st.size,
            "copy_performed": prepared["destination_state"] != "identical",
            "reused_existing_destination": prepared["destination_state"] == "identical",
            "verified": True}


def _repoint(conn, prepared, verification, run_id, plan_fingerprint, actor_user_id,
             request_id) -> dict:
    """Update ONLY the storage-location columns. No row is created; nothing else is modified."""
    now = _now()
    params = {"uri": prepared["destination"], "now": now, "i": prepared["document_id"]}
    sets = ["storage_uri = :uri", "updated_at = :now"]
    if prepared["new_storage_path"] is not None:
        sets.insert(1, "storage_path = :path")
        params["path"] = prepared["new_storage_path"]
    conn.execute(text(f"UPDATE documents SET {', '.join(sets)} WHERE id = :i"), params)

    evidence = {
        "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "document_id": prepared["document_id"],
        "original_storage_provider": prepared["original_storage_provider"],
        "original_storage_uri": prepared["original_storage_uri"],
        "original_storage_path": prepared["original_storage_path"],
        "new_storage_provider": prepared["original_storage_provider"],   # never written
        "new_storage_uri": prepared["destination"],
        "new_storage_path": (prepared["new_storage_path"] if prepared["new_storage_path"] is not None
                             else prepared["original_storage_path"]),
        "storage_path_written": prepared["new_storage_path"] is not None,
        "expected_sha256": prepared["expected_sha256"],
        "expected_size": prepared["expected_size"],
        "provenance_source": prepared["provenance_source"],
        "provenance_source_systems": prepared["provenance_source_systems"],
        "copy_verification": verification,
        "db_update": ("storage_uri repointed"
                      + (", storage_path repointed" if prepared["new_storage_path"] is not None
                         else ", storage_path left unchanged (valid source-relative path)")
                      + "; no other column written"),
        "fingerprint": prepared["fingerprint"],
        "timestamp": now.isoformat(),
    }
    # Existing hash-chained audit, on the CALLER's connection so it commits with the repoint.
    # Paths and digests only - no OCR text, fact content or document body ever enters this.
    write_audit_event(action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
                      entity_id=str(prepared["document_id"]),
                      actor_user_id=actor_user_id, request_id=request_id, outcome="success",
                      metadata=evidence, conn=conn)
    return evidence


def _check_expectations(plan_doc, *, expected_safe_rows, expected_safe_bytes,
                        expected_plan_fingerprint) -> None:
    """All three guards must be supplied and match. Checked before any copy or write."""
    if expected_safe_rows is None or expected_safe_bytes is None:
        raise RelocationError(
            "apply requires --expected-safe-rows and --expected-safe-bytes from the plan you "
            "reviewed, so a corpus that moved cannot be written blind")
    if expected_plan_fingerprint is None:
        raise RelocationError("apply requires --expected-plan-fingerprint")
    actual = (plan_doc["expected_safe_rows"], plan_doc["expected_safe_bytes"],
              plan_doc["plan_fingerprint"])
    wanted = (expected_safe_rows, expected_safe_bytes, expected_plan_fingerprint)
    if actual != wanted:
        raise RelocationError(
            f"expectation mismatch - refusing to write. expected rows={wanted[0]} bytes={wanted[1]} "
            f"fingerprint={str(wanted[2])[:16]}...; plan has rows={actual[0]} bytes={actual[1]} "
            f"fingerprint={actual[2][:16]}... Re-review the plan before applying")


# --- the run -------------------------------------------------------------------------------------

def apply(*, plan_doc=None, apply_writes=False, batch_size=DEFAULT_BATCH_SIZE,
          expected_safe_rows=None, expected_safe_bytes=None, expected_plan_fingerprint=None,
          actor_user_id=None, request_id=None, storage=None, limit=None) -> dict:
    """Relocate SAFE rows. DRY-RUN unless ``apply_writes`` is True.

    A dry run issues NO database mutation and NO filesystem mutation: it re-reads each row, checks
    it still matches the plan, and stats/hashes the source and any existing destination - all
    read-only - then stops before the first copy. Nothing is written and rolled back.

    BATCHES COMMIT INDEPENDENTLY. A later failure is reported as ``partial_apply`` with the
    committed batch numbers; it is never presented as if nothing happened.

    A filesystem failure can never alter the database: the copy is verified first, and the repoint
    happens after. If the copy succeeds but the DB update fails, the row keeps its ORIGINAL
    location (still valid, source untouched) and an extra verified canonical file may remain - a
    rerun detects it as ``identical`` and reuses it idempotently. Copied bytes are never deleted
    to compensate."""
    run_id = f"dmr-{uuid.uuid4().hex[:12]}"
    request_id = request_id or run_id
    storage = storage or RelocationStorage()
    plan_doc = plan_doc or plan(limit=limit)
    if apply_writes:
        _check_expectations(plan_doc, expected_safe_rows=expected_safe_rows,
                            expected_safe_bytes=expected_safe_bytes,
                            expected_plan_fingerprint=expected_plan_fingerprint)

    safe_rows = [r for r in plan_doc["rows"] if r["classification"] == SAFE]
    batches = [safe_rows[i:i + batch_size] for i in range(0, len(safe_rows), batch_size)]
    prepared_all, relocated, refused, batch_reports = [], [], [], []
    committed_batches, failure = [], None

    for n, batch in enumerate(batches, start=1):
        done, errors, prepared_batch = [], [], []
        try:
            with engine.begin() as conn:
                for entry in batch:
                    try:
                        prepared = _prepare_row(conn, entry, storage)     # reads only
                    except RelocationError as exc:
                        errors.append({"document_id": entry["document_id"],
                                       "refused": type(exc).__name__, "detail": str(exc)})
                        continue
                    prepared_batch.append(prepared)
                    if apply_writes:
                        try:
                            verification = _copy_and_verify(prepared, storage)
                        except RelocationError as exc:
                            errors.append({"document_id": entry["document_id"],
                                           "refused": type(exc).__name__, "detail": str(exc)})
                            continue
                        done.append(_repoint(conn, prepared, verification, run_id,
                                             plan_doc["plan_fingerprint"], actor_user_id,
                                             request_id))
        except Exception as exc:                       # noqa: BLE001 - reported, then stopped
            failure = {"batch": n, "rows_in_batch": len(batch), "error": type(exc).__name__,
                       "detail": str(exc)}
            batch_reports.append({"batch": n, "rows": len(batch), "relocated": 0,
                                  "refused": len(errors), "committed": False,
                                  "error": f"{type(exc).__name__}: {exc}"})
            break
        if apply_writes:
            committed_batches.append(n)
        prepared_all.extend(prepared_batch)
        relocated.extend(done)
        refused.extend(errors)
        batch_reports.append({"batch": n, "rows": len(batch), "relocated": len(done),
                              "refused": len(errors), "committed": bool(apply_writes)})

    fs_mutations = [op for op, _ in storage.operations if op in ("copy", "makedirs")]
    return {
        "run_id": run_id,
        "dry_run": not apply_writes,
        "wrote_anything": bool(apply_writes and relocated),
        "filesystem_mutations": len(fs_mutations),
        "partial_apply": bool(failure and committed_batches),
        "failed_batch": failure,
        "committed_batches": committed_batches,
        "batch_size": batch_size,
        "batches": batch_reports,
        "rows_planned": len(safe_rows),
        "rows_verified": len(prepared_all),
        "rows_relocated": len(relocated),
        "rows_refused": len(refused),
        "bytes_relocated": sum(p["source_size"] for p in prepared_all) if apply_writes else 0,
        "bytes_would_relocate": sum(p["source_size"] for p in prepared_all),
        "source_files_deleted": 0,      # structurally impossible: RelocationStorage has no delete
        "relocated": relocated,
        "verified": prepared_all,
        "refused": refused,
        "plan_fingerprint": plan_doc["plan_fingerprint"],
        "plan_totals": {"expected_safe_rows": plan_doc["expected_safe_rows"],
                        "expected_safe_bytes": plan_doc["expected_safe_bytes"]},
    }
