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
from app.db import engine, microsoft_accounts
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


def _acquire_token(account) -> str:
    """Acquire a Graph token via the existing MSAL cache. Any failure here is a reconnect
    condition (there is no valid credential to start the run)."""
    try:
        return get_microsoft_access_token(account)
    except Exception as exc:  # RECONNECT_MESSAGE et al.
        raise ReconnectRequired(
            f"Microsoft 365 must be reconnected before staging: {exc}") from exc


# --- Graph access with retry (auth token comes from the existing MSAL cache) --------------

def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


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


def _graph_get_json(url: str, token: str, params=None) -> dict:
    """GET a Graph JSON resource with retry on 429/5xx/network. A 401 aborts the run."""
    last_exc = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=_auth_header(token), params=params, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:  # network-level
            last_exc = exc
            if attempt < _MAX_ATTEMPTS:
                _sleep_for_retry(None, attempt); continue
            raise RuntimeError(f"Graph request failed after {attempt} attempts: {url} ({exc})") from exc
        if resp.status_code == 401:
            raise ReconnectRequired(
                "Microsoft Graph returned 401 (token rejected). Reconnect Microsoft 365 "
                "(/microsoft365/connect) and re-run staging.")
        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            if attempt < _MAX_ATTEMPTS:
                _sleep_for_retry(resp, attempt); continue
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Graph error {resp.status_code} for {url}: {resp.text[:300]}") from exc
        return resp.json()
    raise RuntimeError(f"Graph request exhausted retries: {url} ({last_exc})")


def _graph_list(url: str, token: str) -> list[dict]:
    """Enumerate a paged Graph collection (follows @odata.nextLink)."""
    items: list[dict] = []
    while url:
        payload = _graph_get_json(url, token)
        items.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
    return items


def _graph_download(drive_id: str, item_id: str, token: str, dest: Path) -> tuple[int, str]:
    """Stream an item's content to ``dest``, returning (bytes_written, sha256_hex).

    Uses GET /drives/{drive}/items/{item}/content — Graph 302-redirects to a short-lived
    pre-authorized download URL; ``requests`` strips Authorization on the cross-host hop.
    A 401 aborts the run (ReconnectRequired); transient failures retry."""
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
    last_exc = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with requests.get(url, headers=_auth_header(token), stream=True,
                              allow_redirects=True, timeout=_REQUEST_TIMEOUT) as resp:
                if resp.status_code == 401:
                    raise ReconnectRequired(
                        "Microsoft Graph returned 401 (token rejected) during download. "
                        "Reconnect Microsoft 365 (/microsoft365/connect) and re-run staging.")
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    if attempt < _MAX_ATTEMPTS:
                        _sleep_for_retry(resp, attempt); continue
                resp.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                sha = hashlib.sha256()
                size = 0
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        fh.write(chunk); sha.update(chunk); size += len(chunk)
                tmp.replace(dest)                       # atomic finalize
                return size, sha.hexdigest()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS:
                _sleep_for_retry(None, attempt); continue
            raise RuntimeError(f"Download failed for item {item_id}: {exc}") from exc
    raise RuntimeError(f"Download exhausted retries for item {item_id}: {last_exc}")


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


def _client_folder_hint(folder_path: str) -> str | None:
    """Best-effort ownership hint = top-level folder segment, resolved later by the importer's
    ``resolve_folder`` (same convention as TaxDome). Unresolved items import unlinked — never guessed."""
    top = folder_path.split("/", 1)[0] if folder_path else ""
    return top or None


def _staged_path(staging_root: Path, site_id: str, drive_id: str, item_id: str, name: str) -> Path:
    # item_id keeps the path unique + stable across runs (duplicate prevention).
    return (staging_root / _safe_name(site_id) / _safe_name(drive_id)
            / f"{_safe_name(item_id)}__{_safe_name(name)}")


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
    token = _acquire_token(account)                      # existing MSAL cache + silent refresh
    state = {} if dry_run else _load_state(staging)
    manifest: list[dict] = []

    logger.info("Staging SharePoint content: sites=%s staging=%s dry_run=%s", ids, staging, dry_run)

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
                        _stage_one(item, site_id=site_id, drive_id=drive_id, drive_name=drive_name,
                                   staging=staging, token=token, state=state, dry_run=dry_run,
                                   manifest=manifest, summary=summary)
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


def _stage_one(item, *, site_id, drive_id, drive_name, staging, token, state, dry_run,
               manifest, summary):
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
