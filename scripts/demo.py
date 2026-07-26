#!/usr/bin/env python
"""Cross-platform Developer Demo Mode control (DEMO-ONLY) — Windows/macOS/Linux.

A pure-Python peer of ``scripts/demo.sh`` for hosts without bash (e.g. the office
Windows Server). It runs the SAME safety guard, migrations, seeder, and demo ASGI
app; it adds no features, endpoints, models, or architecture.

    python scripts/demo.py <command>

    verify   confirm the target is a safe *_demo database (never production)
    setup    create the demo DB (if missing), migrate to head, seed
    reset    DROP + recreate the demo DB, migrate, reseed (idempotent)
    run      run the demo server in the FOREGROUND (manage it with a Windows
             service / Task Scheduler for auto-start)
    smoke    run the demo smoke checks (safety, logins, visibility)

Every DB-touching command refuses unless the database name ends in ``_demo`` and
the environment is not production — identical to the bash script. The normal
Client360 (`client360`) database is never touched.

Configuration (environment variables, with the same defaults as demo.sh):
    DEMO_DB_NAME      default ``client360_demo``
    DATABASE_URL      default ``postgresql://localhost/<DEMO_DB_NAME>``
    CLIENT360_ENVIRONMENT  forced to ``development`` if unset (never production here)
    SESSION_SECRET    a fixed local demo value if unset (NOT for production)
    DEMO_HOST         default ``0.0.0.0`` (reachable from office workstations)
    DEMO_PORT         default ``8360``
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Make the `app` package importable regardless of the caller's working directory
# (scripts/demo.sh relies on `cd $REPO_ROOT`; this script does the equivalent).
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --- environment defaults (parity with scripts/demo.sh) ----------------------
os.environ.setdefault("CLIENT360_ENVIRONMENT", "development")
DEMO_DB_NAME = os.environ.setdefault("DEMO_DB_NAME", "client360_demo")
os.environ.setdefault("DATABASE_URL", f"postgresql://localhost/{DEMO_DB_NAME}")
os.environ.setdefault("SESSION_SECRET", "demo-session-secret-not-for-production")
DEMO_HOST = os.environ.setdefault("DEMO_HOST", "0.0.0.0")
DEMO_PORT = os.environ.setdefault("DEMO_PORT", "8360")


def _refuse_production() -> None:
    if os.environ.get("CLIENT360_ENVIRONMENT", "").lower() == "production":
        sys.exit("REFUSED: demo tooling must not run with CLIENT360_ENVIRONMENT=production.")


def _guard() -> str:
    """Return the demo DB name, or exit if it is unsafe to touch."""
    _refuse_production()
    from app.demo.safety import assert_demo_database
    try:
        return assert_demo_database()
    except Exception as exc:  # DemoSafetyError and friends carry a clear message
        sys.exit(f"REFUSED: {exc}")


def _run(cmd: list[str]) -> None:
    print("  $ " + " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        sys.exit(f"REFUSED: '{name}' not found on PATH. Install PostgreSQL client tools "
                 f"(they ship the {name} command) or create/drop the demo DB manually.")
    return path


def _db_exists(name: str) -> bool:
    # psql -lqt lists databases; match the exact name in the first column.
    psql = _require_tool("psql")
    out = subprocess.run([psql, "-lqt"], cwd=REPO_ROOT, capture_output=True, text=True)
    return any(line.split("|")[0].strip() == name for line in out.stdout.splitlines())


def _migrate_and_seed() -> None:
    print("Applying migrations to head...")
    _run([sys.executable, "-m", "alembic", "upgrade", "head"])
    print("Seeding demo data...")
    _run([sys.executable, "-m", "app.demo.seed"])


def cmd_verify() -> None:
    name = _guard()
    print(f"Environment : {os.environ['CLIENT360_ENVIRONMENT']}")
    print(f"Database URL: {os.environ['DATABASE_URL']}")
    print(f"SAFE: target database '{name}' ends in '_demo' and environment is not production.")


def cmd_setup() -> None:
    name = _guard()
    if not _db_exists(name):
        print(f"Creating database {name}...")
        _run([_require_tool("createdb"), name])
    _migrate_and_seed()
    print("Setup complete. Start with: python scripts/demo.py run")


def cmd_reset() -> None:
    name = _guard()
    print(f"Dropping and recreating {name}...")
    _run([_require_tool("dropdb"), "--if-exists", name])
    _run([_require_tool("createdb"), name])
    _migrate_and_seed()
    print("Reset complete.")


def cmd_run() -> None:
    _guard()
    print(f"Starting demo server on http://{DEMO_HOST}:{DEMO_PORT} ...")
    print(f"  Login: http://{DEMO_HOST}:{DEMO_PORT}/demo/login")
    _run([sys.executable, "-m", "uvicorn", "app.demo.demo_app:app",
          "--host", DEMO_HOST, "--port", DEMO_PORT])


def cmd_smoke() -> None:
    _guard()
    _run([sys.executable, "-m", "app.demo.smoke"])


_COMMANDS = {"verify": cmd_verify, "setup": cmd_setup, "reset": cmd_reset,
             "run": cmd_run, "smoke": cmd_smoke}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in _COMMANDS:
        sys.exit("usage: python scripts/demo.py {verify|setup|reset|run|smoke}")
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
