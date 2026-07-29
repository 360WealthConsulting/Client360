"""Safe deployment migration — reports current→target and upgrades to head. Non-destructive.

Reuses Alembic + ``app.db``. It ONLY runs ``upgrade head`` — it never resets, recreates, or
downgrades a database. It validates config first, confirms connectivity, reports the revision it
will move from/to, optionally runs a pre-migration backup hook, upgrades, and verifies the result.
"""
from __future__ import annotations

import os
import subprocess

from sqlalchemy import text

from app.db import DATABASE_URL, engine


def _alembic_config():
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    return cfg


def target_head() -> str | None:
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    return heads[0] if len(heads) == 1 else None


def current_revision() -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def connectivity_ok() -> tuple[bool, str | None]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def backup_configured() -> bool:
    return bool((os.getenv("CLIENT360_BACKUP_CMD") or "").strip())


def _run_backup() -> None:
    cmd = (os.getenv("CLIENT360_BACKUP_CMD") or "").strip()
    if not cmd:
        raise RuntimeError("A pre-migration backup was requested but CLIENT360_BACKUP_CMD is not set.")
    print(f"[migrate] running pre-migration backup: {cmd}")
    subprocess.run(cmd, shell=True, check=True)      # noqa: S602 — operator-supplied deploy hook


def plan() -> dict:
    ok, detail = connectivity_ok()
    current = current_revision() if ok else None
    target = target_head()
    return {"connectable": ok, "connect_error": detail, "current": current, "target": target,
            "up_to_date": bool(current and target and current == target),
            "backup_configured": backup_configured()}


def run(*, backup: bool = False) -> dict:
    """Validate → connect → report → (optional backup) → upgrade head → verify. Raises on failure."""
    from app.deploy.config_check import validate_config
    cfg = validate_config()
    if not cfg["ok"]:
        raise RuntimeError("Configuration validation failed — fix the FATAL items before migrating.")

    ok, detail = connectivity_ok()
    if not ok:
        raise RuntimeError(f"Database is not reachable: {detail}")

    p = plan()
    print(f"[migrate] current revision: {p['current']}")
    print(f"[migrate] target head:      {p['target']}")
    if p["target"] is None:
        raise RuntimeError("Could not resolve a single migration head (merge the heads first).")
    if p["up_to_date"]:
        print("[migrate] database already at head — nothing to do.")
        return {**p, "applied": False}

    if backup:
        _run_backup()
    elif not backup_configured():
        print("[migrate] NOTE: no backup hook configured (CLIENT360_BACKUP_CMD). Continuing (upgrade "
              "is non-destructive), but a DB snapshot before schema changes is strongly recommended.")

    from alembic import command
    command.upgrade(_alembic_config(), "head")       # upgrade ONLY — never downgrade/reset

    verified = current_revision()
    if verified != p["target"]:
        raise RuntimeError(f"Post-migration head {verified} does not match target {p['target']}.")
    print(f"[migrate] upgraded and verified at head: {verified}")
    return {**plan(), "applied": True}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Apply Client360 database migrations to head (safe).")
    parser.add_argument("--backup", action="store_true",
                        help="run the configured CLIENT360_BACKUP_CMD before migrating")
    parser.add_argument("--plan", action="store_true", help="report current/target only; do not apply")
    args = parser.parse_args(argv)
    if args.plan:
        p = plan()
        print(p)
        return 0
    try:
        run(backup=args.backup)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[migrate] FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
