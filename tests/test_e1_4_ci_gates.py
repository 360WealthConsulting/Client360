"""E1.4 — CI & quality-gate contract tests.

Pins the CI quality-gate contract so a gate cannot be silently dropped. Parses
the workflow (no execution) and asserts the expected gate commands are present.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _run_commands() -> str:
    workflow = yaml.safe_load(CI.read_text())
    steps = workflow["jobs"]["build"]["steps"]
    return "\n".join(step.get("run", "") for step in steps)


def test_ci_workflow_is_valid_yaml_with_build_job():
    workflow = yaml.safe_load(CI.read_text())
    assert "build" in workflow["jobs"]
    assert workflow["jobs"]["build"]["steps"]


def test_required_quality_gates_present():
    commands = _run_commands()
    required = [
        "pip check",                              # dependency consistency
        "scripts/check_secrets.py",               # secret hygiene (E1.4)
        "scripts/ruff_gate.py",                   # lint gate
        "compileall app tests migrations",        # compile
        "lint-imports",                           # import boundaries (E1.1 -> E1.4)
        "scripts/check_migration_heads.sh",       # single head
        "scripts/check_migrations_reversible.sh", # reversibility
        "scripts/check_schema_consistency.py",    # schema consistency (E1.3 -> E1.4)
        "pytest",                                 # tests
    ]
    for needle in required:
        assert needle in commands, f"CI is missing a required gate: {needle}"


def test_ci_uses_disposable_ci_database():
    workflow = yaml.safe_load(CI.read_text())
    env = workflow["jobs"]["build"].get("env", {})
    assert "client360_ci" in env.get("DATABASE_URL", ""), "CI must use a disposable DB"


def test_e1_4_scripts_present():
    assert (REPO_ROOT / "scripts" / "check_secrets.py").is_file()
    assert (REPO_ROOT / "scripts" / "check_schema_consistency.py").is_file()
    assert (REPO_ROOT / "docs" / "CI.md").is_file()
