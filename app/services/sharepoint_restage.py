"""Targeted SharePoint re-stage recovery for OCR failures whose local source file went missing.

Scope (narrow by construction): the EXACT baseline batch of ``documents`` (scoped by
``uploaded_by`` + a half-open ``created_at`` window — the same discipline as
``scripts.migration.reconcile_documents``) that additionally have an OCR row in
``status='failed'`` whose ``last_error`` reports the source file was not found. That
``last_error`` marker (:data:`SOURCE_NOT_FOUND_MARKER`) is present in BOTH the in-process
path (``document_ocr`` → ``ocr_backend._extract`` → ``FileNotFoundError('OCR source file not
found for document ...')``) and the isolated path (``ocr_isolation`` wraps it as
``RuntimeError("OCR failed at stage 'extract': FileNotFoundError: OCR source file not
found...")``), so it selects the recoverable population and nothing else.

What this does — and, deliberately, does NOT do:

* It RE-STAGES the missing local file from SharePoint using the EXISTING hardened connector
  (:mod:`app.connectors.microsoft365.sharepoint_content` — same auth, same read-only Graph
  download, no new client) and then hands the staged file to the EXISTING, SHA-verified
  :func:`app.importers.sharepoint.backfill_local_source`. That importer is the ONLY writer:
  it updates ``documents.storage_uri`` / ``documents.storage_path`` (the two fields OCR
  resolves through, see ``document_ocr._local_path``) and nothing else.
* It NEVER touches ownership (``person_id`` / ``household_id`` / ``organization_id``),
  NEVER inserts/updates/deletes ``document_sources`` rows, NEVER re-runs OCR, and NEVER
  overwrites an existing valid local file (``backfill_local_source`` is a no-op when the
  document already resolves).
* The staged file is verified against the source reference's ``source_hash`` BEFORE it is
  considered usable, and ``backfill_local_source`` independently re-verifies the staged
  content against the canonical ``documents.sha256`` — a two-sided check that the recovered
  bytes are exactly this document's content.

Preview (``apply=False``) performs ZERO network calls and ZERO DB/file writes: it only reads
the scoped population, resolves each document's best source reference deterministically, and
classifies what apply WOULD do. Apply is idempotent and resumable — an already-recovered
document re-classifies as ``already_present`` on a re-run (its file now resolves), and a
correctly-staged file on disk is reused instead of re-downloaded.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, select

logger = logging.getLogger("client360.sharepoint_restage")

SOURCE_SYSTEM = "SharePoint"

#: Substring present in ``document_ocr.last_error`` for BOTH the in-process and the isolated
#: source-file-not-found failure. This is the discriminator that scopes the recoverable
#: population — a failure caused ONLY by a missing local file, not a corrupt/unsupported one.
SOURCE_NOT_FOUND_MARKER = "OCR source file not found"

# --- outcome categories (also the manifest bucket keys) ----------------------------------
CATEGORY_ALREADY_PRESENT = "already_present"      # the document already resolves to a local file — no-op
CATEGORY_RECOVERED = "recovered"                  # staged + hash-verified + backfilled (apply only)
CATEGORY_HASH_MISMATCH = "hash_mismatch"          # downloaded bytes != source_hash (or != canonical sha256)
CATEGORY_SOURCE_UNAVAILABLE = "source_unavailable"  # no available ref, or its drive/library cannot be resolved
CATEGORY_AMBIGUOUS_MULTI_REF = "ambiguous_multi_ref"  # >1 equally-ranked available ref with differing content
CATEGORY_DOWNLOAD_FAILED = "download_failed"      # Graph download errored after retries (apply only)
CATEGORY_PLANNED = "planned"                      # preview only: apply WOULD attempt a download here

#: Every terminal bucket a per-document record can land in (candidates is the total, not a bucket).
_BUCKETS = (
    CATEGORY_ALREADY_PRESENT, CATEGORY_RECOVERED, CATEGORY_HASH_MISMATCH,
    CATEGORY_SOURCE_UNAVAILABLE, CATEGORY_AMBIGUOUS_MULTI_REF, CATEGORY_DOWNLOAD_FAILED,
    CATEGORY_PLANNED,
)


# --- data shapes -------------------------------------------------------------------------

@dataclass(frozen=True)
class DocRecord:
    """One document's recovery outcome (content-free: ids, hashes, provenance — never file bytes)."""

    document_id: int
    category: str
    item_id: str | None = None
    site: str | None = None
    library: str | None = None
    source_uri: str | None = None
    source_hash: str | None = None
    staged_path: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id, "category": self.category, "item_id": self.item_id,
            "site": self.site, "library": self.library, "source_uri": self.source_uri,
            "source_hash": self.source_hash, "staged_path": self.staged_path, "note": self.note,
        }


@dataclass
class RestageReport:
    mode: str                                   # "preview" | "apply"
    scope: dict
    counts: dict = field(default_factory=dict)
    records: list[DocRecord] = field(default_factory=list)
    staging_root: str | None = None

    def add(self, record: DocRecord) -> None:
        self.records.append(record)
        self.counts[record.category] = self.counts.get(record.category, 0) + 1

    def as_manifest(self) -> dict:
        return {
            "mode": self.mode, "scope": self.scope, "staging_root": self.staging_root,
            "candidates": len(self.records),
            "counts": {k: self.counts.get(k, 0) for k in _BUCKETS},
            "records": [r.to_dict() for r in self.records],
        }

    def write_manifest(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.as_manifest(), indent=2, sort_keys=True), encoding="utf-8")
        return p

    def summary_line(self) -> str:
        c = self.counts
        return (f"mode={self.mode} candidates={len(self.records)} "
                + " ".join(f"{k}={c.get(k, 0)}" for k in _BUCKETS if c.get(k, 0)))


# --- read-only selection + deterministic reference choice --------------------------------

def _engine():
    from app.db import engine
    return engine


def _table(name: str):
    """The reflected table from the shared app metadata (reflected once at import; no per-call reflection)."""
    from app.db import engine, metadata
    t = metadata.tables.get(name)
    if t is None:
        md = MetaData()
        md.reflect(bind=engine, only=[name])
        t = md.tables[name]
    return t


def select_candidates(conn, *, uploaded_by: str, created_from: datetime, created_to: datetime,
                      marker: str = SOURCE_NOT_FOUND_MARKER) -> list[dict]:
    """The EXACT recoverable population: baseline documents (uploaded_by + half-open created_at window)
    whose OCR row is ``failed`` with a source-file-not-found ``last_error``. Read-only."""
    docs = _table("documents")
    ocr = _table("document_ocr")
    stmt = (
        select(docs.c.id, docs.c.sha256, docs.c.original_name,
               docs.c.storage_uri, docs.c.storage_path, ocr.c.last_error)
        .select_from(docs.join(ocr, ocr.c.document_id == docs.c.id))
        .where(docs.c.uploaded_by == uploaded_by,
               docs.c.status != "deleted",
               docs.c.created_at >= created_from,
               docs.c.created_at < created_to,
               ocr.c.status == "failed",
               ocr.c.last_error.ilike(f"%{marker}%"))
        .order_by(docs.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def load_refs(conn, document_id: int) -> list[dict]:
    """All SharePoint source references for a document, in a STABLE order (deterministic selection)."""
    ds = _table("document_sources")
    stmt = (
        select(ds.c.source_external_id, ds.c.source_uri, ds.c.source_path,
               ds.c.source_hash, ds.c.available, ds.c.metadata)
        .where(ds.c.document_id == document_id, ds.c.source_system == SOURCE_SYSTEM)
        .order_by(ds.c.source_external_id, ds.c.source_uri)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def choose_ref(refs: list[dict], doc_sha256: str | None) -> tuple[dict | None, bool, bool]:
    """Deterministically pick the single safest source reference to re-stage.

    Returns ``(chosen, ambiguous, any_available)``. Ranking:
      1. only ``available`` refs are eligible (never re-stage a ref the last sync marked gone);
      2. prefer refs whose ``source_hash`` equals the canonical ``documents.sha256`` (an exact
         content match is the most likely to pass verification);
      3. within the chosen tier, order by ``(source_external_id, source_uri)`` for stability.

    A multi-ref document is ``ambiguous`` — and left for an operator — ONLY when the top tier
    still holds more than one ref with DIFFERING ``source_hash`` (we cannot know which content is
    canonical). Multiple refs that share one hash are the same bytes in two places: safe, so the
    lexicographically-first is chosen and the document is NOT flagged ambiguous.
    """
    available = [r for r in refs if r.get("available")]
    if not available:
        return None, False, False
    exact = [r for r in available if doc_sha256 and r.get("source_hash") == doc_sha256]
    tier = exact or available
    tier = sorted(tier, key=lambda r: (str(r.get("source_external_id") or ""), str(r.get("source_uri") or "")))
    if len(tier) > 1 and len({r.get("source_hash") for r in tier}) > 1:
        return None, True, True
    return tier[0], False, True


# --- connector adapter (real Graph by default; injectable for offline tests) --------------

def _looks_like_drive_id(value) -> bool:
    """True if ``value`` is a Graph DRIVE ID rather than a human library name.

    Historical SharePoint source refs stored the drive id directly in ``metadata.library`` (and a
    ``metadata.site`` that is not a usable composite site id). A Graph drive id is an opaque token
    that begins with the ``b!`` sentinel and carries no path separator, so it can never collide with
    a real library display name — making this a safe, unambiguous discriminator."""
    return isinstance(value, str) and value.startswith("b!") and "/" not in value


class RestageConnector:
    """Thin adapter over the EXISTING hardened SharePoint connector. Resolves a source ref's
    (site, library) to a Graph drive id and downloads an item's content to a staged path. No new
    auth, no new client — it holds one token acquired via the connector's own MSAL-cache helpers."""

    def __init__(self, token: str, *, drives_fn=None, download_fn=None) -> None:
        from app.connectors.microsoft365 import sharepoint_content as spc
        self._token = token
        self._drives_fn = drives_fn or spc.enumerate_site_drives
        self._download_fn = download_fn or spc._graph_download
        self._drive_cache: dict[str, dict[str, str]] = {}

    @classmethod
    def connect(cls) -> RestageConnector:
        """Build the default connector: read the single connected account and acquire one read-only
        Graph token via the existing MSAL cache (raises ``ReconnectRequired`` if unusable)."""
        from app.connectors.microsoft365 import sharepoint_content as spc
        account = spc._load_connected_account()
        return cls(spc._acquire_token(account))

    def resolve_drive(self, site_id, library) -> str | None:
        """Resolve the Graph drive id for a source ref's ``(site, library)``.

        Two shapes exist in the historical data:

        * HISTORICAL refs stored the drive id itself in ``metadata.library`` (a ``b!…`` token). It is
          used verbatim — enumerating site drives or matching a display name would never succeed, and
          the download (``GET /drives/{drive}/items/{item}/content``) needs only the drive id.
        * NEWER refs carry a real library DISPLAY NAME, resolved against the site's drives (cached per
          site) by matching the drive ``name``.

        Blank/invalid metadata that can be resolved neither way returns ``None`` (the caller fails
        closed and classifies the document ``source_unavailable`` — never a blind download)."""
        if _looks_like_drive_id(library):
            return library                       # historical: the drive id is already in hand
        if not (site_id and library):
            return None                          # fail closed: cannot resolve by name without both
        if site_id not in self._drive_cache:
            drives = self._drives_fn(site_id, self._token)
            self._drive_cache[site_id] = {str(d.get("name")): str(d.get("id"))
                                          for d in drives if d.get("id")}
        return self._drive_cache[site_id].get(library)

    def download(self, drive_id: str, item_id: str, dest: Path) -> tuple[int, str]:
        """Stream item content to ``dest``; returns ``(bytes, sha256_hex)``."""
        return self._download_fn(drive_id, item_id, self._token, dest)


# --- the recovery engine -----------------------------------------------------------------

def run_restage(*, uploaded_by: str, created_from: datetime, created_to: datetime,
                marker: str = SOURCE_NOT_FOUND_MARKER, apply: bool = False,
                staging_root: str | Path | None = None, connector: RestageConnector | None = None,
                backfill_fn=None, limit: int | None = None) -> RestageReport:
    """Run the re-stage recovery. ``apply=False`` (default) is a zero-side-effect preview: no
    network, no DB or file writes. ``apply=True`` downloads, verifies against ``source_hash``, and
    backfills via the existing SHA-verified importer (the only writer; storage_uri/path only).
    """
    from app.connectors.microsoft365 import sharepoint_content as spc
    from app.importers.sharepoint import backfill_local_source

    backfill_fn = backfill_fn or backfill_local_source
    scope = {"uploaded_by": uploaded_by, "created_from": created_from.isoformat(),
             "created_to": created_to.isoformat(), "marker": marker}
    report = RestageReport(mode="apply" if apply else "preview", scope=scope)

    staging: Path | None = None
    if apply:
        resolved_root = staging_root or spc.DEFAULT_STAGING_ROOT
        if not resolved_root:
            raise RuntimeError(
                "No SharePoint staging root configured. Pass staging_root= or set "
                "CLIENT360_SHAREPOINT_STAGING_ROOT (fail-closed; never defaults to a machine path).")
        staging = Path(resolved_root)
        report.staging_root = str(staging)

    engine = _engine()
    with engine.connect() as conn:
        candidates = select_candidates(conn, uploaded_by=uploaded_by, created_from=created_from,
                                       created_to=created_to, marker=marker)
        if limit is not None:
            candidates = candidates[:limit]
        # Load every document's refs up front (read-only) so the later apply loop holds no long txn.
        ref_map = {doc["id"]: load_refs(conn, doc["id"]) for doc in candidates}

    for doc in candidates:
        record = _process(doc, ref_map.get(doc["id"], []), apply=apply, staging=staging,
                          connector_ref=[connector], backfill_fn=backfill_fn)
        report.add(record)

    report.counts.setdefault("candidates", len(candidates))
    return report


def _process(doc: dict, refs: list[dict], *, apply: bool, staging: Path | None,
             connector_ref: list, backfill_fn) -> DocRecord:
    """Classify (and, in apply mode, recover) a single document. ``connector_ref`` is a 1-element
    list holding the connector so it can be lazily built and reused across documents."""
    from app.connectors.microsoft365 import sharepoint_content as spc
    from app.importers.sharepoint import _content_sha256, _has_resolvable_file

    doc_id = doc["id"]

    # 1. Already resolvable? Never overwrite a good local file (also makes apply idempotent —
    #    a document recovered on an earlier run resolves here on the next run).
    if _has_resolvable_file(doc):
        return DocRecord(doc_id, CATEGORY_ALREADY_PRESENT)

    # 2. Deterministic, safe reference choice.
    chosen, ambiguous, any_available = choose_ref(refs, doc.get("sha256"))
    if ambiguous:
        return DocRecord(doc_id, CATEGORY_AMBIGUOUS_MULTI_REF,
                         note="multiple available refs with differing source_hash")
    if chosen is None:
        note = "no source references" if not refs else "no available source reference"
        return DocRecord(doc_id, CATEGORY_SOURCE_UNAVAILABLE, note=note)

    meta = chosen.get("metadata") or {}
    site, library = meta.get("site"), meta.get("library")
    item_id = chosen.get("source_external_id")
    source_hash = chosen.get("source_hash")
    base = dict(item_id=item_id, site=site, library=library,
                source_uri=chosen.get("source_uri"), source_hash=source_hash)
    # ``library`` is the drive source (a ``b!…`` drive id for historical refs, else a display name);
    # ``item_id`` identifies the file. Both are always required. A ``site`` is required ONLY for the
    # display-name resolution path — a historical drive-id library needs no (usable) site.
    if not (library and item_id):
        return DocRecord(doc_id, CATEGORY_SOURCE_UNAVAILABLE,
                         note="chosen ref missing library/item_id", **base)
    if not _looks_like_drive_id(library) and not site:
        return DocRecord(doc_id, CATEGORY_SOURCE_UNAVAILABLE,
                         note="chosen ref missing site for library-name resolution", **base)

    # 3. Preview stops here — it never touches the network or disk.
    if not apply:
        return DocRecord(doc_id, CATEGORY_PLANNED, **base)

    # 4. Apply: resolve the drive, (re)stage the file, verify, and backfill.
    if connector_ref[0] is None:
        connector_ref[0] = RestageConnector.connect()
    connector = connector_ref[0]

    drive_id = connector.resolve_drive(site, library)
    if not drive_id:
        return DocRecord(doc_id, CATEGORY_SOURCE_UNAVAILABLE,
                         note=f"library '{library}' not found under site", **base)

    dest = spc._staged_path(staging, site, drive_id, item_id, doc.get("original_name") or f"{item_id}")
    # Resumable: reuse a correctly-staged file from a prior run instead of re-downloading.
    if dest.is_file() and source_hash and _content_sha256(dest) == source_hash:
        staged_sha = source_hash
    else:
        try:
            _size, staged_sha = connector.download(drive_id, item_id, dest)
        except spc.ReconnectRequired:
            raise  # a rejected credential is global — abort the whole run, never mask it per-doc
        except Exception as exc:  # noqa: BLE001 — a per-item download failure is recorded and skipped
            return DocRecord(doc_id, CATEGORY_DOWNLOAD_FAILED, note=str(exc)[:300], **base)

    # 5. Verify the staged bytes against the reference's source_hash BEFORE marking usable.
    if source_hash and staged_sha != source_hash:
        return DocRecord(doc_id, CATEGORY_HASH_MISMATCH,
                         note="staged content != source_hash", staged_path=str(dest), **base)

    # 6. The ONLY state change: hand the staged file to the existing SHA-verified backfill. It
    #    re-verifies against documents.sha256, is a no-op if already resolvable, never overwrites,
    #    and updates ONLY storage_uri/storage_path.
    if backfill_fn(doc_id, str(dest)):
        return DocRecord(doc_id, CATEGORY_RECOVERED, staged_path=str(dest), **base)

    # backfill returned False: either the row became resolvable concurrently, or the staged content
    # did not match the canonical sha256. Distinguish with a fresh read.
    if _has_resolvable_file(_current_storage(doc_id)):
        return DocRecord(doc_id, CATEGORY_ALREADY_PRESENT, staged_path=str(dest), **base)
    return DocRecord(doc_id, CATEGORY_HASH_MISMATCH,
                     note="staged content != canonical sha256", staged_path=str(dest), **base)


def _current_storage(document_id: int) -> dict:
    """Fresh read of a document's storage pointers (used to disambiguate a backfill no-op)."""
    docs = _table("documents")
    with _engine().connect() as conn:
        row = conn.execute(select(docs.c.storage_uri, docs.c.storage_path)
                           .where(docs.c.id == document_id)).mappings().first()
    return dict(row) if row else {}
