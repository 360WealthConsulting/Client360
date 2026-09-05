# app/connectors/microsoft365/sharepoint_content.py
"""SharePoint content connector — the missing bridge between the live Microsoft Graph
metadata sync and the existing canonical SharePoint importer.

WHAT THIS IS
------------
``app/importers/sharepoint.py`` integrates *staged* SharePoint items (each with a
locally-downloaded ``local_path``) into the canonical document model. It does NOT talk
to Graph; it consumes a manifest JSON. This connector is the deployment-time producer of
that manifest: it enumerates configured SharePoint sites' document libraries (drives),
walks their folders/files over Microsoft Graph, downloads each file's content into a
staging directory using the EXISTING delegated read permissions, and emits the manifest
``import_sharepoint_items`` already accepts. It also writes a run/completion record so an
operator can tell, deterministically, whether a run is safe to follow with the importer's
``--purge-missing``.

Pipeline (unchanged importer, unchanged storage, unchanged auth)::

    connect once via /microsoft365/connect  (existing delegated OAuth)
    python -m app.connectors.microsoft365.sharepoint_content --sites <id,id> --staging <dir>
    python -m app.importers.sharepoint --manifest <dir>/manifest.json            # existing importer

BOUNDARIES (by design — see the audit)
--------------------------------------
* Reuses ``app.services.microsoft_identity.get_microsoft_access_token`` and the encrypted
  MSAL token cache. No new authentication, no app-only auth, no new app registration.
* Read-only: uses only the existing delegated scopes (Files.Read.All / Sites.Read.All).
  Never writes/uploads/creates/moves anything in SharePoint.
* Writes ONLY to the local staging directory (staged files + manifest.json + state +
  run record). It does not touch the database (beyond a read-only account lookup for the
  token), does not create canonical documents, does not change ``storage_provider``, and
  does not modify importer behavior. All DB work remains the importer's.
* No staging cleanup: renamed/removed SharePoint items may leave orphaned staged files on
  disk. This is deliberate — the connector never deletes staged content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import requests
from sqlalchemy import select

# --- reuse existing auth + Graph constants/helpers (no new auth, no duplication) ---------
from app.db import documents, engine, metadata, microsoft_accounts
from app.jobs.microsoft_document_sync import (
    GRAPH_BASE_URL,
    _identity_email,  # uploader-email extraction (ownership metadata)
)
from app.services.microsoft_identity import get_microsoft_access_token

logger = logging.getLogger("client360.connectors.sharepoint")

SOURCE_SYSTEM = "SharePoint"                       # must match app/importers/sharepoint.py
# Staging root is DEPLOYMENT configuration, never a machine-specific path hard-coded in source. It is
# resolved from CLIENT360_SHAREPOINT_STAGING_ROOT (or passed via --staging / staging_root). If none is
# configured, staging fails closed with a clear message rather than defaulting to any one machine's path.
# The attribute name is part of the connector's contract with app.services.microsoft_ingestion
# (_connector_staging_root reads mod.DEFAULT_STAGING_ROOT).
DEFAULT_STAGING_ROOT = os.getenv("CLIENT360_SHAREPOINT_STAGING_ROOT") or None

MANIFEST_FILENAME = "manifest.json"
RUN_RECORD_FILENAME = "run_record.json"
STATE_FILENAME = ".sharepoint_sync_state.json"

# Retry policy for transient Graph failures (429 / 5xx / network). Read-only GETs only.
# A 401 is never retried — it aborts the run immediately (see ReconnectRequired).
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 2
_REQUEST_TIMEOUT = 60

# Run outcomes.
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATUS_FAILED = "failed"
STATUS_DRY_RUN = "dry_run"


class ReconnectRequired(Exception):
    """Raised when Microsoft Graph rejects the token (HTTP 401).

    Deliberately NOT a subclass of ``RuntimeError`` so the per-item / per-drive / per-site
    ``except RuntimeError`` handlers do not swallow it: a 401 means the credential is
    globally invalid, so the run stops immediately rather than manufacturing a partial
    failure for every remaining item.
    """


@dataclass
class StagingSummary:
    dry_run: bool
    sites: int = 0
    drives: int = 0
    folders_walked: int = 0
    files_seen: int = 0
    files_downloaded: int = 0
    skipped_unchanged: int = 0
    bytes_downloaded: int = 0
    errors: list[str] = field(default_factory=list)
    status: str = "started"
    started_at: str | None = None
    finished_at: str | None = None
    manifest_path: str | None = None
    staging_root: str | None = None

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def purge_missing_safe(self) -> bool:
        """The importer's ``--purge-missing`` is safe to run against this manifest ONLY when the
        run completed a full enumeration with zero errors. Both conditions are required."""
        return self.status == STATUS_COMPLETED and self.error_count == 0

    def as_record(self) -> dict:
        """The completion / run record persisted to disk and returned to callers."""
        return {
            "source_system": SOURCE_SYSTEM,
            "status": self.status,
            "dry_run": self.dry_run,
            "purge_missing_safe": self.purge_missing_safe,
            "files_seen": self.files_seen,
            "files_downloaded": self.files_downloaded,
            "skipped_unchanged": self.skipped_unchanged,
            "bytes_downloaded": self.bytes_downloaded,
            "sites": self.sites,
            "drives": self.drives,
            "folders_walked": self.folders_walked,
            "error_count": self.error_count,
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "manifest_path": self.manifest_path,
            "staging_root": self.staging_root,
        }


# --- configuration -----------------------------------------------------------------------

def resolve_site_ids(site_ids=None) -> list[str]:
    """Explicit ``site_ids`` win; otherwise fall back to the SAME env the live sync uses
    (``MICROSOFT_SHAREPOINT_SITE_IDS``, comma-separated). Raises with a clear message if none."""
    if site_ids:
        ids = [str(s).strip() for s in site_ids if str(s).strip()]
    else:
        raw = os.getenv("MICROSOFT_SHAREPOINT_SITE_IDS", "")
        ids = [s.strip() for s in raw.split(",") if s.strip()]
    if not ids:
        raise RuntimeError(
            "No SharePoint site IDs configured. Pass --sites, or set "
            "MICROSOFT_SHAREPOINT_SITE_IDS (comma-separated Graph site IDs).")
    return ids


def _load_connected_account():
    """Read the single connected Microsoft 365 account (read-only). Mirrors the live sync's
    selection. No write, no new auth — the token/refresh is handled by microsoft_identity."""
    with engine.connect() as conn:
        account = conn.execute(
            select(microsoft_accounts).order_by(microsoft_accounts.c.updated_at.desc()).limit(1)
        ).mappings().one_or_none()
    if account is None:
        raise RuntimeError(
            "No Microsoft 365 account is connected. Run /microsoft365/connect once before staging.")
    return account


def _load_existing_sharepoint_fastpath() -> dict[str, dict]:
    """Return canonical SharePoint items safe to skip downloading.

    Eligibility is deliberately fail-closed:
      * stable source_external_id exists;
      * size + modified metadata exist;
      * source_hash exists;
      * duplicate rows agree on size + modified + hash;
      * at least one linked canonical document has a real local file.

    Anything incomplete, conflicting, changed, or missing local storage
    falls through to the normal Graph download path.
    """
    document_sources = metadata.tables["document_sources"]
    grouped: dict[str, list[dict]] = {}

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                document_sources.c.source_external_id,
                document_sources.c.source_hash,
                document_sources.c.metadata,
                documents.c.storage_uri,
            )
            .select_from(
                document_sources.join(
                    documents,
                    documents.c.id == document_sources.c.document_id,
                )
            )
            .where(
                document_sources.c.source_system == SOURCE_SYSTEM,
                document_sources.c.source_external_id.is_not(None),
                document_sources.c.source_external_id != "",
                documents.c.status != "deleted",
            )
        ).mappings()

        for row in rows:
            item_id = str(row["source_external_id"] or "").strip()
            if not item_id:
                continue

            source_metadata = row["metadata"] or {}

            if not isinstance(source_metadata, dict):
                grouped.setdefault(item_id, []).append({"valid": False})
                continue

            try:
                size = int(source_metadata.get("size"))
            except (TypeError, ValueError):
                grouped.setdefault(item_id, []).append({"valid": False})
                continue

            modified = source_metadata.get("modified")
            source_hash = str(row["source_hash"] or "").strip()

            if modified in (None, "") or not source_hash:
                grouped.setdefault(item_id, []).append({"valid": False})
                continue

            storage_uri = str(row["storage_uri"] or "").strip()

            grouped.setdefault(item_id, []).append(
                {
                    "valid": True,
                    "size": size,
                    "modified": str(modified),
                    "sha256": source_hash,
                    "local_ok": bool(
                        storage_uri and Path(storage_uri).is_file()
                    ),
                }
            )

    fastpath: dict[str, dict] = {}

    for item_id, candidates in grouped.items():
        if not candidates:
            continue

        if any(not c.get("valid") for c in candidates):
            continue

        signatures = {
            (c["size"], c["modified"], c["sha256"])
            for c in candidates
        }

        if len(signatures) != 1:
            continue

        if not any(c["local_ok"] for c in candidates):
            continue

        size, modified, sha256 = next(iter(signatures))

        fastpath[item_id] = {
            "size": size,
            "modified": modified,
            "sha256": sha256,
        }

    return fastpath


def _acquire_token(account) -> str:
    """Acquire a Graph token via the existing MSAL cache. Any failure here is a reconnect
    condition (there is no valid credential to start the run)."""
    try:
        return get_microsoft_access_token(account)
    except Exception as exc:  # RECONNECT_MESSAGE et al.
        raise ReconnectRequired(
            f"Microsoft 365 must be reconnected before staging: {exc}") from exc



class _GraphTokenSession:
    """Mutable bearer-token holder for long-running SharePoint traversals.

    The initial token and every refresh use the canonical MSAL cache path.
    A refresh reloads the account row first so any newly persisted MSAL
    cache state is used.  Callers that still pass a plain string retain
    the existing fail-closed 401 behavior.
    """

    def __init__(self, account):
        self._account = account
        self._token = _acquire_token(account)

    def current(self) -> str:
        return self._token

    def refresh(self) -> str:
        self._account = _load_connected_account()
        self._token = _acquire_token(self._account)
        return self._token


# --- Graph access with retry (auth token comes from the existing MSAL cache) --------------

def _token_value(token) -> str:
    if isinstance(token, _GraphTokenSession):
        return token.current()
    return str(token)


def _refresh_token_after_401(token, *, context: str) -> bool:
    """Refresh only managed long-run sessions. Plain tokens still fail closed."""
    if not isinstance(token, _GraphTokenSession):
        return False

    logger.warning(
        "Microsoft Graph returned 401 during %s; "
        "reacquiring bearer token through the canonical MSAL cache",
        context,
    )
    token.refresh()
    return True


def _auth_header(token) -> dict:
    return {
        "Authorization": f"Bearer {_token_value(token)}",
        "Accept": "application/json",
    }


def _sleep_for_retry(response, attempt: int) -> None:
    retry_after = None
    if response is not None:
        try:
            retry_after = float(response.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            retry_after = None
    delay = retry_after if retry_after is not None else _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    logger.warning("Graph transient failure; retry %d/%d in %.1fs", attempt, _MAX_ATTEMPTS, delay)
    time.sleep(delay)


def _graph_get_json(url: str, token, params=None) -> dict:
    """GET a Graph JSON resource.

    429/5xx/network failures use the existing bounded retry policy.
    For a managed long-running token session only, the first 401 causes
    one canonical MSAL refresh and an immediate retry.  A second 401
    still fails closed with ReconnectRequired.
    """
    last_exc = None
    attempt = 1
    auth_refreshed = False

    while attempt <= _MAX_ATTEMPTS:
        try:
            resp = requests.get(
                url,
                headers=_auth_header(token),
                params=params,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS:
                _sleep_for_retry(None, attempt)
                attempt += 1
                continue
            raise RuntimeError(
                f"Graph request failed after {attempt} attempts: {url} ({exc})"
            ) from exc

        if resp.status_code == 401:
            if (
                not auth_refreshed
                and _refresh_token_after_401(token, context="Graph request")
            ):
                auth_refreshed = True
                continue

            raise ReconnectRequired(
                "Microsoft Graph returned 401 (token rejected). Reconnect Microsoft 365 "
                "(/microsoft365/connect) and re-run staging."
            )

        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            if attempt < _MAX_ATTEMPTS:
                _sleep_for_retry(resp, attempt)
                attempt += 1
                continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                f"Graph error {resp.status_code} for {url}: {resp.text[:300]}"
            ) from exc

        return resp.json()

    raise RuntimeError(
        f"Graph request exhausted retries: {url} ({last_exc})"
    )


def _graph_list(url: str, token: str) -> list[dict]:
    """Enumerate a paged Graph collection (follows @odata.nextLink)."""
    items: list[dict] = []
    while url:
        payload = _graph_get_json(url, token)
        items.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
    return items


def _graph_download(drive_id: str, item_id: str, token, dest: Path) -> tuple[int, str]:
    """Stream an item's content to ``dest``, returning (bytes_written, sha256_hex).

    Uses GET /drives/{drive}/items/{item}/content.  For a managed
    long-running token session, one 401 triggers a canonical MSAL token
    refresh and retries the same item.  A second 401 still fails closed.
    """
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
    last_exc = None
    attempt = 1
    auth_refreshed = False

    while attempt <= _MAX_ATTEMPTS:
        try:
            with requests.get(
                url,
                headers=_auth_header(token),
                stream=True,
                allow_redirects=True,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:

                if resp.status_code == 401:
                    if (
                        not auth_refreshed
                        and _refresh_token_after_401(
                            token,
                            context=f"download item {item_id}",
                        )
                    ):
                        auth_refreshed = True
                        continue

                    raise ReconnectRequired(
                        "Microsoft Graph returned 401 (token rejected) during download. "
                        "Reconnect Microsoft 365 (/microsoft365/connect) and re-run staging."
                    )

                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    if attempt < _MAX_ATTEMPTS:
                        _sleep_for_retry(resp, attempt)
                        attempt += 1
                        continue

                resp.raise_for_status()

                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")

                sha = hashlib.sha256()
                size = 0

                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        sha.update(chunk)
                        size += len(chunk)

                tmp.replace(dest)
                return size, sha.hexdigest()

        except ReconnectRequired:
            raise

        except requests.RequestException as exc:
            last_exc = exc

            if attempt < _MAX_ATTEMPTS:
                _sleep_for_retry(None, attempt)
                attempt += 1
                continue

            raise RuntimeError(
                f"Download failed for item {item_id}: {exc}"
            ) from exc

    raise RuntimeError(
        f"Download exhausted retries for item {item_id}: {last_exc}"
    )


# --- enumeration + walk ------------------------------------------------------------------

def enumerate_site_drives(site_id: str, token: str) -> list[dict]:
    """Document libraries (drives) for a SharePoint site."""
    return _graph_list(f"{GRAPH_BASE_URL}/sites/{site_id}/drives", token)


def walk_drive(drive_id: str, token: str, summary: StagingSummary):
    """Yield every file item under a drive (depth-first). Folders are recursed; files yielded.
    ``parentReference.path`` gives a human folder path used for ownership hints."""
    stack = [f"{GRAPH_BASE_URL}/drives/{drive_id}/root/children"]
    visited: set[str] = set()
    while stack:
        url = stack.pop()
        children = _graph_list(url, token)
        summary.folders_walked += 1
        for item in children:
            item_id = str(item.get("id") or "")
            if not item_id or item_id in visited:
                continue
            visited.add(item_id)
            if item.get("folder"):
                stack.append(f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/children")
            elif item.get("file"):
                summary.files_seen += 1
                yield item


# --- staging + manifest ------------------------------------------------------------------

_SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", (name or "file").strip()).strip(". ")
    return cleaned or "file"


def _folder_path(item: dict) -> str:
    """Human folder path relative to the drive root, from parentReference.path
    (e.g. '/drive/root:/Clients/Robinson' -> 'Clients/Robinson')."""
    raw = str(item.get("parentReference", {}).get("path") or "")
    if "root:" in raw:
        raw = raw.split("root:", 1)[1]
    return raw.strip("/")


#: Segments that organise the practice, not a client. Proven from production metadata: under
#: ``Clients`` the library nests a SERVICE LINE, sometimes a sub-service, sometimes an activity
#: status, sometimes an entity class — and only then the client. Every one of these has been
#: observed sitting in the position a client folder would otherwise occupy.
_STRUCTURAL_SEGMENTS = frozenset({
    # service lines
    "tax preparation", "tax preparation(1)", "sales, litter & pp tax", "sales & litter tax",
    "sales and litter tax", "sales tax", "payroll", "bookkeeping", "bookkeeping(1)",
    "client services", "tax planning", "tax representation",
    # entity classes
    "individual", "individuals", "business", "businesses", "corporate", "partnership",
    "trust", "estate", "non-profit", "nonprofit",
    # activity status / workflow buckets
    "active", "inactive", "active clients", "inactive clients", "needs to be done",
    "archive", "archived", "general",
})

#: Work-product / document-category folders. These normally sit BELOW a client, where the walk
#: never reaches them. Finding one AT the client position means the client level is simply absent
#: (``Clients/Sales, Litter & PP Tax/Fixed Asset List/2018`` — 402 documents in production), so the
#: structure is not understood and the only safe answer is no hint. They are TERMINAL rather than
#: skippable for exactly that reason: skipping would walk on and return the year below them.
_NON_CLIENT_TERMINAL = frozenset({
    "fixed asset list", "federal", "state", "unemployment", "paystubs", "check stubs",
    "bank statements", "payroll reports", "notes", "reports", "correspondence",
})

#: The segment that opens the client area. Everything before it is firm/library chrome, so a
#: client can NEVER be read from above it — the firm root is excluded structurally, not by name.
_CLIENT_ROOT_SEGMENTS = frozenset({"clients", "active clients"})


def _segments(folder_path: str) -> list[str]:
    """Path segments, tolerant of Windows separators and Graph's ``/drives/<id>/root:/A/B`` form."""
    path = (folder_path or "").replace("\\", "/")
    if "root:" in path:
        path = path.split("root:", 1)[1]
    return [seg for seg in (p.strip() for p in path.split("/")) if seg]


def client_folder_hint(folder_path: str) -> str | None:
    """The CLIENT folder in a SharePoint path, or None when the structure is not recognised.

    Resolved later by the importer's ``resolve_folder`` (the same convention TaxDome uses). This
    only proposes a folder NAME; it never resolves an owner and never guesses.

    The library is organised as::

        360 Tax Solutions, LLC / Clients / Tax Preparation / Individual / Sebastian, Britt / 2022
        360 Tax Solutions, LLC / Clients / Payroll / Inactive / ERE Power LLC / Federal / 2018
        Documents 1 / FileHistory / 360 Tax / Clients / Bookkeeping / Raymonds Construction

    so the client is neither the first segment nor the deepest one. It is the first segment after
    ``Clients`` that is not practice structure. Reading the TOP segment — which is what this used
    to do — yields the FIRM ("360 Tax Solutions, LLC") on ~25k documents, and the firm is itself a
    canonical business record, so that would have anchored them all onto the firm's own entity.

    Fail-closed by construction:

    * no ``Clients`` root  ->  None. Backup trees, mailbox dumps and system folders never match.
    * only structural segments after it  ->  None.
    * a numeric or date-like candidate (``1179``, ``2022``)  ->  None; those are IDs and years.
    * the search starts AFTER ``Clients``, so no segment above it — the firm root included — can
      ever be returned. That is a property of the walk, not a denylist that must be maintained.
    """
    segments = _segments(folder_path)
    lowered = [s.lower() for s in segments]
    try:
        start = next(i for i, s in enumerate(lowered) if s in _CLIENT_ROOT_SEGMENTS) + 1
    except StopIteration:
        return None                                   # not the client area — no hint
    # strict=True: ``lowered`` is a 1:1 comprehension over ``segments``, so the two slices are
    # equal-length by construction. Asserting it keeps that invariant enforced — were ``lowered``
    # ever changed to filter or reshape, strict=False would silently walk the shorter list and
    # mis-pair a segment with the wrong lowered form, which is precisely how a client folder gets
    # matched against the wrong token.
    for segment, low in zip(segments[start:], lowered[start:], strict=True):
        if low in _STRUCTURAL_SEGMENTS:
            continue                                  # service line / entity class / status
        if low in _NON_CLIENT_TERMINAL:
            return None                               # category folder here => no client level
        if not any(ch.isalpha() for ch in segment):
            return None                               # numeric id or bare year -> unsafe
        return segment
    return None


#: Historical private name; the record builders and this module both call the public helper.
_client_folder_hint = client_folder_hint


_STAGE_MAX_TEMP_PATH = 240


def _bounded_staged_filename(
    parent: Path,
    item_id: str,
    name: str,
    *,
    max_temp_path: int = _STAGE_MAX_TEMP_PATH,
) -> str:
    """Build a stable staging filename whose download ``.part`` path
    remains below the conservative Windows path limit.

    Preserve the SharePoint item id and original extension.  When the
    original filename would make the temporary download path too long,
    truncate the stem and append a deterministic hash so different long
    SharePoint filenames cannot collapse to the same local name.
    """
    safe_id = _safe_name(item_id)
    safe_name = _safe_name(name)

    prefix = f"{safe_id}__"
    candidate = prefix + safe_name

    if len(str(parent / (candidate + ".part"))) <= max_temp_path:
        return candidate

    suffix = Path(safe_name).suffix
    stem = safe_name[:-len(suffix)] if suffix else safe_name

    digest = hashlib.sha256(
        safe_name.encode("utf-8", errors="replace")
    ).hexdigest()[:12]

    # Length available for the stem after accounting for:
    # parent + slash + item-id prefix + hyphen + hash + extension + .part
    fixed_length = (
        len(str(parent))
        + 1
        + len(prefix)
        + 1
        + len(digest)
        + len(suffix)
        + len(".part")
    )

    available = max_temp_path - fixed_length

    if available < 1:
        raise OSError(
            f"SharePoint staging parent path leaves no room for a bounded filename: {parent}"
        )

    return f"{prefix}{stem[:available]}-{digest}{suffix}"


def _staged_path(staging_root: Path, site_id: str, drive_id: str, item_id: str, name: str) -> Path:
    # item_id keeps the path unique + stable across runs (duplicate prevention).
    parent = (
        staging_root
        / _safe_name(site_id)
        / _safe_name(drive_id)
    )

    filename = _bounded_staged_filename(
        parent,
        item_id,
        name,
    )

    return parent / filename


def _load_state(staging_root: Path) -> dict:
    p = staging_root / STATE_FILENAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("Ignoring unreadable staging state at %s", p)
    return {}


def _save_state(staging_root: Path, state: dict) -> None:
    staging_root.mkdir(parents=True, exist_ok=True)
    (staging_root / STATE_FILENAME).write_text(json.dumps(state, indent=2), encoding="utf-8")


def _write_run_record(staging_root: Path, record: dict) -> Path:
    """Persist the completion/run record next to the manifest so an operator (or automation)
    can gate ``--purge-missing`` on ``status == completed AND error_count == 0``."""
    staging_root.mkdir(parents=True, exist_ok=True)
    path = staging_root / RUN_RECORD_FILENAME
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def _manifest_item(item: dict, *, site_id: str, drive_id: str, drive_name: str,
                   local_path: str | None, sha256: str | None, size: int | None) -> dict:
    """Exactly the shape ``app/importers/sharepoint.py`` consumes, plus the traceability fields
    requested (drive_id / site_id / sha256). Unknown keys are ignored by the importer."""
    item_id = str(item.get("id") or "")
    folder_path = _folder_path(item)
    return {
        # --- fields the importer reads ---
        "name": item.get("name"),
        "web_url": item.get("webUrl"),
        "local_path": local_path,
        "site": site_id,                 # -> tags.sharepoint_site + synthesized uri
        "library": drive_name,           # -> tags.sharepoint_library
        "item_id": item_id,              # -> tags.sharepoint_item_id + source_external_id
        "folder_path": folder_path,
        "author": _identity_email(item, "createdBy") or None,
        "created_at": item.get("createdDateTime"),
        "modified_at": item.get("lastModifiedDateTime"),
        "size": size if size is not None else item.get("size"),
        "content_type": item.get("file", {}).get("mimeType"),
        "client_folder": _client_folder_hint(folder_path),   # optional ownership hint
        # --- explicit traceability / provenance (importer ignores extras) ---
        "site_id": site_id,
        "drive_id": drive_id,
        "sha256": sha256,
        "created_by_email": _identity_email(item, "createdBy") or None,
        "modified_by_email": _identity_email(item, "lastModifiedBy") or None,
    }


# --- orchestration -----------------------------------------------------------------------

def stage_sharepoint_content(*, site_ids=None, staging_root=None, manifest_path=None,
                             dry_run=False) -> tuple[list[dict], dict]:
    """Enumerate configured sites, stage file content locally, and write the importer manifest
    plus a run/completion record.

    Returns ``(manifest_items, run_record)``. In dry-run nothing is downloaded and no files are
    written — only enumeration + a plan are logged, and the returned record has
    ``status='dry_run'`` and ``purge_missing_safe=False``.

    Raises ``ReconnectRequired`` immediately (after recording ``status='failed'``) if Graph
    returns 401 at any point, so a stale credential never produces a stream of partial
    failures. Raises ``RuntimeError`` for configuration problems (no site IDs, no account)."""
    summary = StagingSummary(dry_run=dry_run, started_at=datetime.now(UTC).isoformat())
    ids = resolve_site_ids(site_ids)
    resolved_root = staging_root or DEFAULT_STAGING_ROOT
    if not resolved_root:
        raise RuntimeError(
            "No SharePoint staging root configured. Pass --staging (or staging_root=), or set "
            "CLIENT360_SHAREPOINT_STAGING_ROOT.")
    staging = Path(resolved_root)
    manifest_file = Path(manifest_path) if manifest_path else staging / MANIFEST_FILENAME
    summary.staging_root = str(staging)
    summary.manifest_path = str(manifest_file)

    account = _load_connected_account()
    # Long full traversals may outlive one bearer token.  Keep a mutable
    # session so a Graph 401 can silently refresh through the canonical
    # encrypted MSAL cache without restarting the crawl.
    token = _GraphTokenSession(account)
    state = {} if dry_run else _load_state(staging)
    existing_fastpath = _load_existing_sharepoint_fastpath()
    manifest: list[dict] = []

    logger.info(
        "Staging SharePoint content: sites=%s staging=%s dry_run=%s canonical_fastpath=%d",
        ids,
        staging,
        dry_run,
        len(existing_fastpath),
    )

    try:
        for site_id in ids:
            try:
                drives = enumerate_site_drives(site_id, token)
            except RuntimeError as exc:
                summary.errors.append(f"site {site_id}: {exc}")
                logger.error("Site %s enumeration failed: %s", site_id, exc)
                continue
            summary.sites += 1
            for drive in drives:
                drive_id = str(drive.get("id") or "")
                drive_name = drive.get("name") or "Documents"
                if not drive_id:
                    continue
                summary.drives += 1
                try:
                    for item in walk_drive(drive_id, token, summary):
                        _stage_one(
                            item,
                            site_id=site_id,
                            drive_id=drive_id,
                            drive_name=drive_name,
                            staging=staging,
                            token=token,
                            state=state,
                            dry_run=dry_run,
                            manifest=manifest,
                            summary=summary,
                            existing_fastpath=existing_fastpath,
                        )
                except RuntimeError as exc:
                    summary.errors.append(f"drive {drive_name} ({drive_id}): {exc}")
                    logger.error("Drive %s walk failed: %s", drive_id, exc)
    except ReconnectRequired as exc:
        # Immediate, deterministic stop: record the failed run, then re-raise. No partial
        # per-item failures are manufactured for the remaining corpus.
        summary.status = STATUS_FAILED
        summary.errors.append(f"RECONNECT REQUIRED: {exc}")
        summary.finished_at = datetime.now(UTC).isoformat()
        logger.error("Aborting SharePoint staging: %s", exc)
        if not dry_run:
            _write_run_record(staging, summary.as_record())
        raise

    summary.finished_at = datetime.now(UTC).isoformat()
    summary.status = (STATUS_DRY_RUN if dry_run
                      else (STATUS_COMPLETED_WITH_ERRORS if summary.errors else STATUS_COMPLETED))

    if not dry_run:
        _save_state(staging, state)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        record_path = _write_run_record(staging, summary.as_record())
        logger.info("Wrote manifest: %s (%d items); run record: %s (status=%s, purge_missing_safe=%s)",
                    manifest_file, len(manifest), record_path, summary.status,
                    summary.purge_missing_safe)
    else:
        logger.info("DRY-RUN: would write %d manifest items to %s", len(manifest), manifest_file)

    return manifest, summary.as_record()


def _stage_one(
    item,
    *,
    site_id,
    drive_id,
    drive_name,
    staging,
    token,
    state,
    dry_run,
    manifest,
    summary,
    existing_fastpath=None,
):
    item_id = str(item.get("id") or "")
    name = item.get("name") or ""
    if not item_id or not name:
        return
    size_hint = item.get("size")
    modified = item.get("lastModifiedDateTime")
    key = f"{drive_id}:{item_id}"
    dest = _staged_path(staging, site_id, drive_id, item_id, name)

    # Duplicate prevention / incremental: skip re-download when the staged copy already matches
    # size + modified and still exists on disk. (No cleanup of superseded/orphaned files.)
    prior = state.get(key)
    if (prior and dest.exists() and prior.get("size") == size_hint
            and prior.get("modified") == modified):
        summary.skipped_unchanged += 1
        manifest.append(_manifest_item(
            item, site_id=site_id, drive_id=drive_id, drive_name=drive_name,
            local_path=prior.get("local_path", str(dest)),
            sha256=prior.get("sha256"), size=prior.get("size")))
        return

    canonical_prior = (existing_fastpath or {}).get(item_id)

    try:
        normalized_size_hint = (
            int(size_hint)
            if size_hint is not None
            else None
        )
    except (TypeError, ValueError):
        normalized_size_hint = None

    if (
        canonical_prior
        and normalized_size_hint == canonical_prior.get("size")
        and str(modified or "") == canonical_prior.get("modified")
    ):
        summary.skipped_unchanged += 1

        manifest.append(
            _manifest_item(
                item,
                site_id=site_id,
                drive_id=drive_id,
                drive_name=drive_name,
                local_path=None,
                sha256=canonical_prior.get("sha256"),
                size=normalized_size_hint,
            )
        )
        return

    if dry_run:
        summary.files_downloaded += 1     # would download
        if isinstance(size_hint, int):
            summary.bytes_downloaded += size_hint
        manifest.append(_manifest_item(
            item, site_id=site_id, drive_id=drive_id, drive_name=drive_name,
            local_path=str(dest), sha256=None, size=size_hint))
        return

    # _graph_download raises ReconnectRequired on 401 (bubbles up to abort the run); RuntimeError
    # for a genuinely bad single file (recorded here, run continues).
    try:
        size, sha = _graph_download(drive_id, item_id, token, dest)
    except RuntimeError as exc:
        summary.errors.append(f"{name} ({item_id}): {exc}")
        logger.error("Download failed for %s (%s): %s", name, item_id, exc)
        return

    summary.files_downloaded += 1
    summary.bytes_downloaded += size
    state[key] = {"size": size, "modified": modified, "sha256": sha, "local_path": str(dest)}
    manifest.append(_manifest_item(
        item, site_id=site_id, drive_id=drive_id, drive_name=drive_name,
        local_path=str(dest), sha256=sha, size=size))
    logger.debug("Staged %s -> %s (%d bytes)", name, dest, size)


# --- CLI ---------------------------------------------------------------------------------

def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m app.connectors.microsoft365.sharepoint_content",
        description="Stage SharePoint content and emit the manifest for app/importers/sharepoint.py")
    p.add_argument("--sites", default=None,
                   help="Comma-separated Graph site IDs (else MICROSOFT_SHAREPOINT_SITE_IDS).")
    p.add_argument("--staging", default=None, help="Staging root (else CLIENT360_SHAREPOINT_STAGING_ROOT).")
    p.add_argument("--manifest", default=None, help="Manifest output path (else <staging>/manifest.json).")
    p.add_argument("--dry-run", action="store_true", help="Enumerate + plan only; no downloads, no writes.")
    p.add_argument("--verbose", action="store_true", help="DEBUG logging.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    site_ids = [s for s in (args.sites or "").split(",") if s.strip()] or None
    try:
        _, record = stage_sharepoint_content(
            site_ids=site_ids, staging_root=args.staging, manifest_path=args.manifest,
            dry_run=args.dry_run)
    except ReconnectRequired as exc:
        logger.error("SharePoint staging aborted (reconnect required): %s", exc)
        print(f"RECONNECT REQUIRED: {exc}")
        return 3
    except RuntimeError as exc:
        logger.error("SharePoint staging aborted: %s", exc)
        print(f"ERROR: {exc}")
        return 2

    label = "DRY RUN — nothing downloaded/written" if record["dry_run"] else "SharePoint staging complete"
    print(f"{label}.")
    for k in ("status", "files_seen", "files_downloaded", "skipped_unchanged", "bytes_downloaded",
              "sites", "drives", "folders_walked", "error_count", "purge_missing_safe"):
        print(f"  {k}: {record[k]}")
    if record["errors"]:
        print(f"  errors ({record['error_count']}):")
        for e in record["errors"][:20]:
            print(f"    - {e}")
    return 1 if record["status"] == STATUS_COMPLETED_WITH_ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
