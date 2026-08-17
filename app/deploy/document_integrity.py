"""Read-only document integrity verifier (audit V4).

Cross-checks the database's document metadata against the physical files on disk and reports:
total records checked, files found, missing files, SHA-256 matches, SHA-256 mismatches,
invalid/unresolvable storage references, duplicate physical references, and (opt-in) orphaned
physical files with no DB reference.

Designed for two operational uses: verifying the document move to ``D:\\360PlusData`` (run before and
after; every record must still resolve with a matching hash) and verifying a restored backup against
its database snapshot.

STRICTLY READ-ONLY. It only issues SELECTs and reads file bytes. It NEVER writes, moves, renames,
deletes, repairs, OCRs, mkdirs, or mutates any database row or file — including not calling the vault
``storage_root()``/``resolve_path()`` helpers, which would ``mkdir`` the root. Path resolution here is
a read-only reimplementation that reuses the vault key regex.

Usage:
    python -m app.deploy.document_integrity [--stores {all,documents,vault}] [--no-hash]
        [--roots PATH ...] [--scan-orphans] [--sample N] [--json] [--limit-list N]

Exit code 0 only when there are no missing files, no hash mismatches, no invalid references, and
(when --scan-orphans) no orphaned files — so it can gate a migration/restore step.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import select

from app.db import documents, engine, vault_documents
from app.services.vault.storage import (
    _KEY_RE,  # reuse the exact key-shape validation (no duplication)
)

_LOCAL_PROVIDERS = {None, "", "local", "file", "filesystem"}
_READ_CHUNK = 1024 * 1024


# --- read-only path resolution ----------------------------------------------

def _vault_root() -> Path:
    """Vault root WITHOUT creating it (unlike storage.storage_root, which mkdirs)."""
    return Path(os.getenv("VAULT_STORAGE_ROOT", "data/vault")).resolve()


def _documents_root() -> Path:
    """Person-documents root WITHOUT creating it (mirrors documents.DOCUMENT_ROOT = Path('documents'))."""
    return Path("documents").resolve()


def _as_path(reference: str | None) -> Path | None:
    if not reference:
        return None
    ref = reference.strip()
    if not ref:
        return None
    if ref.startswith("file://"):
        return Path(unquote(urlsplit(ref).path))
    return Path(ref)


def _resolve_documents_path(*, storage_uri, storage_path, roots) -> tuple[Path | None, str | None]:
    """Return (resolved_path_or_None, invalid_reason_or_None). Prefers storage_uri, then storage_path.
    A relative reference is tried as-is and under each provided root; the first existing match wins,
    else the first candidate is returned so a missing file is reported against a concrete path."""
    candidates: list[Path] = []
    for ref in (storage_uri, storage_path):
        p = _as_path(ref)
        if p is None:
            continue
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append(Path.cwd() / p)
            candidates.extend(root / p for root in roots)
    if not candidates:
        return None, "no storage_uri or storage_path"
    for c in candidates:
        try:
            if c.exists():
                return c, None
        except OSError:
            continue
    return candidates[0], None


def _resolve_vault_path(storage_key) -> tuple[Path | None, str | None]:
    if not storage_key or not isinstance(storage_key, str) or not _KEY_RE.match(storage_key):
        return None, f"invalid storage_key: {storage_key!r}"
    root = _vault_root()
    candidate = (root / storage_key).resolve()
    if root not in candidate.parents:
        return None, "storage_key escapes vault root"
    return candidate, None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:                      # read-only
        while chunk := fh.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# --- per-record evaluation ---------------------------------------------------

def _evaluate(*, kind, doc_id, expected_sha, resolved, invalid_reason, compute_hash) -> dict:
    rec = {"kind": kind, "id": doc_id, "path": str(resolved) if resolved else None}
    if invalid_reason is not None:
        rec["status"] = "invalid"
        rec["detail"] = invalid_reason
        return rec
    try:
        exists = resolved.exists() and resolved.is_file()
    except OSError as exc:
        rec["status"] = "invalid"
        rec["detail"] = f"path error: {exc}"
        return rec
    if not exists:
        rec["status"] = "missing"
        return rec
    if not compute_hash or not expected_sha:
        rec["status"] = "found"
        rec["hash"] = "skipped" if not compute_hash else "no_expected_hash"
        return rec
    actual = _sha256(resolved)
    if actual == expected_sha:
        rec["status"] = "found"
        rec["hash"] = "match"
    else:
        rec["status"] = "mismatch"
        rec["hash"] = "mismatch"
        rec["expected_sha256"] = expected_sha
        rec["actual_sha256"] = actual
    return rec


# --- core verification -------------------------------------------------------

def verify(*, stores=("documents", "vault"), roots=None, compute_hash=True, sample=None) -> dict:
    """Read-only integrity pass. Returns a structured report; performs no writes."""
    roots = [Path(r).resolve() for r in (roots or [])]
    records: list[dict] = []
    ref_index: dict[str, list] = {}     # resolved path str -> [(kind, id), ...] for duplicate detection
    skipped_remote = 0

    with engine.connect() as conn:      # connect(), never begin() — SELECT only
        if "documents" in stores:
            q = select(documents.c.id, documents.c.sha256, documents.c.storage_uri,
                       documents.c.storage_path, documents.c.storage_provider,
                       documents.c.status, documents.c.archived).order_by(documents.c.id)
            if sample:
                q = q.limit(sample)
            for row in conn.execute(q).mappings():
                if row["storage_provider"] not in _LOCAL_PROVIDERS:
                    skipped_remote += 1
                    continue                                   # remote-stored: not verifiable on local disk
                resolved, invalid = _resolve_documents_path(
                    storage_uri=row["storage_uri"], storage_path=row["storage_path"], roots=roots)
                rec = _evaluate(kind="documents", doc_id=row["id"], expected_sha=row["sha256"],
                                resolved=resolved, invalid_reason=invalid, compute_hash=compute_hash)
                records.append(rec)
                if rec["path"]:
                    ref_index.setdefault(rec["path"], []).append(("documents", row["id"]))

        if "vault" in stores:
            q = select(vault_documents.c.id, vault_documents.c.checksum_sha256,
                       vault_documents.c.storage_key, vault_documents.c.status,
                       vault_documents.c.archived_at).order_by(vault_documents.c.id)
            if sample:
                q = q.limit(sample)
            for row in conn.execute(q).mappings():
                resolved, invalid = _resolve_vault_path(row["storage_key"])
                rec = _evaluate(kind="vault", doc_id=row["id"], expected_sha=row["checksum_sha256"],
                                resolved=resolved, invalid_reason=invalid, compute_hash=compute_hash)
                records.append(rec)
                if rec["path"]:
                    ref_index.setdefault(rec["path"], []).append(("vault", row["id"]))

    duplicates = [{"path": p, "refs": refs} for p, refs in ref_index.items() if len(refs) > 1]

    report = {
        "database": _redacted_target(),
        "stores": list(stores),
        "hash_checked": compute_hash,
        "documents_checked": len(records),
        "files_found": sum(1 for r in records if r["status"] == "found"),
        "missing_files": [r for r in records if r["status"] == "missing"],
        "sha256_matches": sum(1 for r in records if r.get("hash") == "match"),
        "sha256_mismatches": [r for r in records if r["status"] == "mismatch"],
        "invalid_references": [r for r in records if r["status"] == "invalid"],
        "duplicate_references": duplicates,
        "skipped_remote_provider": skipped_remote,
        "hash_skipped": sum(1 for r in records if r.get("hash") in {"skipped", "no_expected_hash"}),
        "orphaned_files": None,          # populated only when scan requested
        "records": records,
    }
    return report


def scan_orphans(report_paths: set[str], roots) -> list[str]:
    """Return physical files under ``roots`` not referenced by any checked record. Read-only walk."""
    referenced = {str(Path(p).resolve()) for p in report_paths}
    orphans: list[str] = []
    for root in roots:
        root = Path(root).resolve()
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                fp = str((Path(dirpath) / name).resolve())
                if fp not in referenced:
                    orphans.append(fp)
    return sorted(orphans)


def _redacted_target() -> str:
    url = engine.url
    return f"{url.get_backend_name()}:{url.database}@{url.host or 'local'}"     # never renders the password


# --- CLI ---------------------------------------------------------------------

def _print_text(report, limit_list) -> None:
    print(f"Document integrity — database {report['database']}  stores={report['stores']}  "
          f"hash={'on' if report['hash_checked'] else 'off'}")
    print(f"  records checked ............ {report['documents_checked']}")
    print(f"  files found ................ {report['files_found']}")
    print(f"  missing files .............. {len(report['missing_files'])}")
    print(f"  sha256 matches ............. {report['sha256_matches']}")
    print(f"  sha256 mismatches .......... {len(report['sha256_mismatches'])}")
    print(f"  invalid/unresolvable refs .. {len(report['invalid_references'])}")
    print(f"  duplicate references ....... {len(report['duplicate_references'])}")
    print(f"  hash skipped (no expected) . {report['hash_skipped']}")
    print(f"  skipped remote provider .... {report['skipped_remote_provider']}")
    if report["orphaned_files"] is not None:
        print(f"  orphaned physical files .... {len(report['orphaned_files'])}")

    def _dump(title, items, fmt):
        if not items:
            return
        print(f"\n{title} ({len(items)}):")
        for it in items[:limit_list]:
            print("  ", fmt(it))
        if len(items) > limit_list:
            print(f"   … {len(items) - limit_list} more")

    _dump("MISSING", report["missing_files"], lambda r: f"{r['kind']}#{r['id']} -> {r['path']}")
    _dump("MISMATCH", report["sha256_mismatches"],
          lambda r: f"{r['kind']}#{r['id']} {r['path']} expected {r['expected_sha256'][:12]}… "
                    f"got {r['actual_sha256'][:12]}…")
    _dump("INVALID", report["invalid_references"], lambda r: f"{r['kind']}#{r['id']} — {r['detail']}")
    _dump("DUPLICATE", report["duplicate_references"],
          lambda d: f"{d['path']} <- {d['refs']}")
    if report["orphaned_files"]:
        _dump("ORPHANED", report["orphaned_files"], lambda p: p)


def _is_clean(report) -> bool:
    return (not report["missing_files"] and not report["sha256_mismatches"]
            and not report["invalid_references"] and not report["orphaned_files"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only document integrity verifier (V4).")
    parser.add_argument("--stores", choices=["all", "documents", "vault"], default="all")
    parser.add_argument("--no-hash", action="store_true", help="check existence only (fast)")
    parser.add_argument("--roots", nargs="*", default=[],
                        help="extra roots for relative-path resolution and orphan scanning")
    parser.add_argument("--scan-orphans", action="store_true",
                        help="also report physical files under the roots with no DB reference")
    parser.add_argument("--sample", type=int, default=None, help="check only the first N of each store")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit-list", type=int, default=25, help="max items to list per category")
    args = parser.parse_args(argv)

    stores = ("documents", "vault") if args.stores == "all" else (args.stores,)
    report = verify(stores=stores, roots=args.roots, compute_hash=not args.no_hash, sample=args.sample)

    if args.scan_orphans:
        roots = [Path(r) for r in args.roots] or [_vault_root(), _documents_root()]
        referenced = {r["path"] for r in report["records"] if r["path"]}
        report["orphaned_files"] = scan_orphans(referenced, roots)

    report.pop("records", None)          # keep the emitted report compact
    if args.json:
        import json
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_text(report, args.limit_list)

    return 0 if _is_clean(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
