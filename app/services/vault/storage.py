"""Secure local storage for the Client Vault (MVP).

Files live under ``VAULT_STORAGE_ROOT`` (default ``data/vault`` relative to the app root, i.e.
``C:\\Client360\\data\\vault`` on the office server). The browser never sees a filesystem path:
callers hand us a stream and get back an opaque, internally-generated ``storage_key``; downloads
are served by resolving that key back to a path *inside the root only*. Original filenames are kept
as metadata, never used to build a path (so a crafted "../.." filename cannot traverse). We validate
extension and size, and compute the SHA-256 while streaming (contents are written once, chunked).
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO

MAX_UPLOAD_BYTES = 50 * 1024 * 1024                       # 50 MB
ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "xlsx", "csv", "jpg", "jpeg", "png", "txt"})
_KEY_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{32}\.[0-9a-z]{1,8}$")   # shard/uuid.ext — the only shape we accept


class VaultStorageError(ValueError):
    """Raised for validation failures (bad extension, oversize, unsafe key)."""


def storage_root() -> Path:
    """The vault root, created if missing. Overridable with ``VAULT_STORAGE_ROOT``."""
    root = Path(os.getenv("VAULT_STORAGE_ROOT", "data/vault")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_extension(original_filename: str) -> str:
    """Return the lowercased, allow-listed extension of ``original_filename`` or raise."""
    ext = Path(original_filename or "").suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise VaultStorageError(
            f"Unsupported file type '.{ext or '?'}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}.")
    return ext


def _new_key(ext: str) -> str:
    token = uuid.uuid4().hex                              # 32 hex chars — no user input, no separators
    return f"{token[:2]}/{token}.{ext}"


def resolve_path(storage_key: str) -> Path:
    """Resolve an internal ``storage_key`` to an absolute path, guaranteed to be inside the root.

    Rejects any key that is not our exact ``shard/uuid.ext`` shape, and defensively re-checks that
    the resolved path stays within the root (blocks traversal even if the shape check were bypassed)."""
    if not isinstance(storage_key, str) or not _KEY_RE.match(storage_key):
        raise VaultStorageError("Invalid storage key.")
    root = storage_root()
    candidate = (root / storage_key).resolve()
    if root not in candidate.parents:
        raise VaultStorageError("Resolved path escapes the vault root.")
    return candidate


def save_stream(source: BinaryIO, *, original_filename: str) -> dict:
    """Validate + stream ``source`` into the vault. Returns
    ``{storage_key, checksum_sha256, file_size, extension}``. Enforces the size cap while writing
    (a stream longer than the cap is aborted and the partial file removed)."""
    ext = validate_extension(original_filename)
    storage_key = _new_key(ext)
    destination = resolve_path(storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("wb") as out:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise VaultStorageError(f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise VaultStorageError("Empty file.")
    except BaseException:
        destination.unlink(missing_ok=True)              # never leave a partial/oversize file behind
        raise

    return {"storage_key": storage_key, "checksum_sha256": digest.hexdigest(),
            "file_size": size, "extension": ext}
