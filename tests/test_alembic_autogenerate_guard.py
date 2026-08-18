"""Guard: `alembic revision --autogenerate` is DISABLED and fails closed (Phase 0A).

Client360 migrations are hand-authored. Autogenerate would compare the live DB against the partial,
drifting `app/database/schema.py` and emit destructive DROP/NOT-NULL operations, so `migrations/env.py`
rejects `--autogenerate` before any revision file is created. These tests fail if that regresses — either
by silently succeeding, by creating a revision file, or by pointing Alembic back at schema.py's metadata.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.database import schema

_REPO = Path(__file__).resolve().parents[1]
_ENV_PY = _REPO / "migrations" / "env.py"
_VERSIONS = _REPO / "migrations" / "versions"


def _alembic(*args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO, env={**os.environ}, capture_output=True, text=True)


def _revision_files():
    return {p.name for p in _VERSIONS.glob("*.py")}


# --- autogenerate is rejected and creates no revision ------------------------

def test_autogenerate_is_rejected_and_creates_no_revision_file():
    before = _revision_files()
    result = _alembic("revision", "--autogenerate", "-m", "guard_probe_should_be_rejected")
    after = _revision_files()

    assert result.returncode != 0, "autogenerate should FAIL, not succeed"
    combined = result.stdout + result.stderr
    assert "DISABLED" in combined and "hand-authored" in combined, combined
    assert before == after, f"autogenerate created revision file(s): {after - before}"
    assert not list(_VERSIONS.glob("*guard_probe_should_be_rejected*")), "a probe revision was written"


# --- normal migration operations remain unchanged ---------------------------

def test_normal_alembic_operations_still_work():
    assert _alembic("heads").returncode == 0
    assert _alembic("current").returncode == 0
    assert _alembic("upgrade", "head").returncode == 0            # no-op, must succeed

    # downgrade dispatches through the SAME env path as upgrade; downgrading to the current head is a
    # safe no-op that proves the command runs (full base<->head reversibility is in test_migration_framework).
    head_rev = _alembic("heads").stdout.split()[0]
    assert head_rev, "could not resolve current head"
    assert _alembic("downgrade", head_rev).returncode == 0


# --- stale schema.py can never become the target again ----------------------

def test_env_does_not_use_stale_schema_metadata_as_target():
    src = _ENV_PY.read_text(encoding="utf-8")
    assert "_reject_autogenerate_if_requested" in src, "env.py no longer rejects autogenerate"
    assert "target_metadata=None" in src, "env.py should configure no target metadata"
    assert "target_metadata = metadata" not in src, "env.py points target_metadata at stale schema.py — trap is back"
    assert "schema.metadata" not in src, "env.py references schema.py metadata as an Alembic target"


def test_schema_py_is_only_partial_and_must_not_be_the_target():
    """Canary: schema.py declares far fewer tables than the live DB, which is exactly why it must never
    drive Alembic. If these converge, revisit the design deliberately."""
    from sqlalchemy import inspect

    from app.db import engine
    with engine.connect() as conn:
        live = set(inspect(conn).get_table_names()) - {"alembic_version"}
    assert len(live - set(schema.metadata.tables)) > 50
