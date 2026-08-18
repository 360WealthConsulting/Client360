"""Data-SAFE database rollback (Phase 0B).

A Client360 rollback downgrades the database to a prior release's Alembic revision. Alembic downgrades
are STRUCTURALLY reversible (CI proves the DDL runs) but are **not** data-preserving — several downgrades
`DELETE` from append-only tables (audit_events, exception_events) and every `drop_table`/`drop_column`
in a downgrade destroys the data it held. `scripts/check_migrations_reversible.sh` documents this
explicitly. So a downgrade can silently lose data.

This module makes rollback fail-closed: it takes a **verified backup** (pg_dump custom-format, validated
with `pg_restore --list`) BEFORE any downgrade, prints the backup location first, and requires explicit
confirmation. If the backup or its verification fails, the downgrade never runs. Forward migration
(`app.deploy.migrate`, upgrade-only) is unchanged.

Automatic restore-on-failed-downgrade is deliberately NOT implemented (see docs/DATABASE.md): restoring
means dropping and recreating the live database from the dump — a destructive, environment-sensitive
operation (e.g. encrypted token caches need the original key) that must be an operator decision. The
manual restore procedure is printed on failure and documented.
"""
from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.db import DATABASE_URL
from app.deploy.migrate import _alembic_config, connectivity_ok, current_revision
from app.safety import database_name

_MIN_BACKUP_BYTES = 512   # a valid pg_dump -Fc archive (schema alone) is far larger; catches empty/truncated


class RollbackError(RuntimeError):
    """A rollback was refused or failed."""


class BackupError(RollbackError):
    """The pre-rollback backup could not be created."""


class BackupVerificationError(RollbackError):
    """The pre-rollback backup could not be verified as restorable."""


class ConfirmationRequired(RollbackError):
    """Destructive rollback was not explicitly confirmed."""


# --- backup location ---------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_backup_path(target: str, *, backup_dir: str | None = None) -> Path:
    """Where the pre-rollback backup will be written. Operators should point CLIENT360_BACKUP_DIR at
    durable storage; it defaults to <repo>/backups."""
    base = Path(backup_dir or os.getenv("CLIENT360_BACKUP_DIR") or (_repo_root() / "backups"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_target = "".join(c if c.isalnum() or c in "-_" else "_" for c in (target or "unknown"))
    return base / f"rollback-{database_name(DATABASE_URL)}-{stamp}-to-{safe_target}.dump"


def _backup_mechanism() -> str:
    return "CLIENT360_BACKUP_CMD" if (os.getenv("CLIENT360_BACKUP_CMD") or "").strip() else "pg_dump"


# --- backup + verification (fail closed) ------------------------------------

def create_backup(path: Path) -> Path:
    """Create a verifiable backup at ``path`` (pg_dump custom-format by default; an operator-supplied
    CLIENT360_BACKUP_CMD receives the target path in CLIENT360_BACKUP_FILE and MUST write a
    pg_restore-compatible dump there). Raises BackupError on any failure — NO downgrade must follow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hook = (os.getenv("CLIENT360_BACKUP_CMD") or "").strip()
    try:
        if hook:
            env = {**os.environ, "CLIENT360_BACKUP_FILE": str(path)}
            subprocess.run(hook, shell=True, check=True, env=env)   # noqa: S602 — operator-supplied hook
        else:
            subprocess.run(
                ["pg_dump", "--format=custom", f"--dbname={DATABASE_URL}", "--file", str(path)],
                check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise BackupError(f"backup command failed ({_backup_mechanism()}): {exc}") from exc
    if not path.exists():
        raise BackupError(f"backup command produced no file at {path}")
    return path


def verify_backup(path: Path) -> None:
    """Verify the backup is present, non-trivial, and a restorable archive (`pg_restore --list`). Raises
    BackupVerificationError on any failure — NO downgrade must follow."""
    if not path.exists():
        raise BackupVerificationError(f"backup file is missing: {path}")
    size = path.stat().st_size
    if size < _MIN_BACKUP_BYTES:
        raise BackupVerificationError(f"backup file is suspiciously small ({size} bytes): {path}")
    try:
        subprocess.run(["pg_restore", "--list", str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise BackupVerificationError(
            f"backup at {path} is not a restorable pg_restore archive: {exc}") from exc


def _downgrade(target: str) -> None:
    """Run the Alembic downgrade. Isolated so tests can assert it is (not) reached."""
    from alembic import command
    command.downgrade(_alembic_config(), target)


# --- plan (no changes) -------------------------------------------------------

def plan(target: str, *, backup_dir: str | None = None) -> dict:
    ok, detail = connectivity_ok()
    current = current_revision() if ok else None
    return {
        "connectable": ok, "connect_error": detail,
        "database": database_name(DATABASE_URL),
        "current": current, "target": target,
        "backup_path": str(default_backup_path(target, backup_dir=backup_dir)),
        "backup_mechanism": _backup_mechanism(),
        "destructive": True,
    }


def _print_manual_restore(path: Path) -> None:
    print("\nTo restore this database from the backup (MANUAL, operator decision):")
    print("  dropdb <db> && createdb <db>   # or restore into a scratch DB first")
    print(f"  pg_restore --no-owner --dbname=<db> {path}")
    print(f"  # verify with: scripts/restore_rehearsal.sh {path} <scratch-db>")


# --- orchestrated rollback (gated) ------------------------------------------

def run(target: str, *, assume_yes: bool = False, confirm=None, backup_dir: str | None = None) -> dict:
    """Gate order: connectivity → CONFIRMATION → verified BACKUP → print location → downgrade → verify.
    Any refusal/failure before the downgrade leaves the database untouched.
    ``confirm`` is a no-arg callable returning True to proceed (used when ``assume_yes`` is False)."""
    if not (target or "").strip():
        raise RollbackError("a target revision is required")

    ok, detail = connectivity_ok()
    if not ok:
        raise RollbackError(f"database is not reachable: {detail}")
    current = current_revision()
    print(f"[rollback] database: {database_name(DATABASE_URL)}")
    print(f"[rollback] current : {current}")
    print(f"[rollback] target  : {target}")

    # --- confirmation gate (BEFORE any backup or change) ---
    if not assume_yes:
        if confirm is None or not confirm():
            raise ConfirmationRequired(
                "destructive rollback not confirmed (pass --yes or confirm interactively)")

    # --- verified backup (BEFORE any downgrade) ---
    path = default_backup_path(target, backup_dir=backup_dir)
    print(f"[rollback] creating pre-rollback backup ({_backup_mechanism()}) …")
    create_backup(path)          # raises BackupError -> no downgrade
    verify_backup(path)          # raises BackupVerificationError -> no downgrade
    # requirement: print the backup location/identifier BEFORE any downgrade begins
    print(f"[rollback] backup VERIFIED: {path} ({path.stat().st_size} bytes)")

    # --- downgrade (only reached with a verified backup) ---
    print(f"[rollback] downgrading {current} -> {target} …")
    try:
        _downgrade(target)
    except Exception as exc:  # noqa: BLE001
        print(f"[rollback] DOWNGRADE FAILED: {exc}")
        _print_manual_restore(path)
        raise RollbackError(f"downgrade failed after backup; database may be partially changed: {exc}") from exc

    verified = current_revision()
    print(f"[rollback] downgraded. current revision: {verified}")
    print(f"[rollback] backup retained at: {path}")
    print("[rollback] redeploy the matching application artifact if not already done.")
    return {"applied": True, "from": current, "to": target, "verified": verified, "backup_path": str(path)}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m app.deploy.rollback",
        description="Data-safe database rollback: verified backup, then Alembic downgrade to --to.")
    parser.add_argument("--to", required=True, metavar="REVISION",
                        help="target Alembic revision to downgrade to")
    parser.add_argument("--dry-run", "--plan", dest="dry_run", action="store_true",
                        help="report the plan and backup location; make NO changes")
    parser.add_argument("--yes", action="store_true",
                        help="skip the interactive confirmation (still takes a verified backup)")
    parser.add_argument("--backup-dir", default=None,
                        help="directory for the backup (default: CLIENT360_BACKUP_DIR or <repo>/backups)")
    args = parser.parse_args(argv)

    if args.dry_run:
        p = plan(args.to, backup_dir=args.backup_dir)
        print("== rollback plan (DRY RUN — no changes) ==")
        for k in ("database", "current", "target", "backup_mechanism", "backup_path", "connectable"):
            print(f"  {k:18} {p[k]}")
        print(f"  would run: alembic downgrade {args.to} (AFTER a verified backup)")
        return 0

    def _interactive_confirm() -> bool:
        prompt = (f"Downgrade '{plan(args.to)['database']}' to '{args.to}'? "
                  "Schema/data created after it will be DROPPED. Type 'yes': ")
        try:
            return input(prompt).strip() == "yes"
        except EOFError:
            return False

    try:
        run(args.to, assume_yes=args.yes, confirm=_interactive_confirm, backup_dir=args.backup_dir)
        return 0
    except ConfirmationRequired as exc:
        print(f"[rollback] REFUSED: {exc}")
        return 1
    except RollbackError as exc:
        print(f"[rollback] FAILED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
