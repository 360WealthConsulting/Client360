"""E1.1 — Repository & Application Scaffolding acceptance tests.

These tests are database-independent. They prove that the incremental,
in-place scaffolding introduced under ADR-013 did not break protected
functionality and that the required scaffold artifacts exist and are
secret-free.

Protected scope (ADR-013): FastAPI app, SQLAlchemy models, importers,
matching/merge/match-review, search, existing routes, existing startup.
"""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_app_imports_and_is_fastapi():
    """The application entry point imports cleanly and exposes a FastAPI app.

    Importing app.main is side-effect-free: startup work runs in the lifespan.
    """
    from fastapi import FastAPI

    main = importlib.import_module("app.main")
    assert isinstance(main.app, FastAPI)


def test_existing_routes_preserved():
    """Existing user-visible routes remain registered (route preservation)."""
    main = importlib.import_module("app.main")
    paths = {getattr(r, "path", "") for r in main.app.router.routes}
    # A healthy, populated route table (baseline observed: 280 routes).
    assert len(paths) > 200
    for fragment in ("/people", "/households", "/matches", "/source", "/portfolio"):
        assert any(fragment in p for p in paths), f"missing route surface: {fragment}"
    assert any("search" in p for p in paths), "missing search route surface"


@pytest.mark.parametrize(
    "module",
    [
        "app.models.person",
        "app.models.household",
        "app.models.client",
        "app.models.source_link",
        "app.matching.matcher",
        "app.matching.plan_matches",
        "app.matching.apply_safe_matches",
        "app.matching.audit_matches",
        "app.matching.verify_merge_plan",
        "app.importers.schwab",
        "app.importers.assetmark",
        "app.importers.wealthbox",
        "app.routes.search",
    ],
)
def test_protected_modules_import(module):
    """Protected domain/ingestion/matching/search modules still import."""
    assert importlib.import_module(module) is not None


def test_config_validation_runs():
    """Startup configuration validation is callable and non-fatal in dev."""
    config = importlib.import_module("app.config")
    # Returns a list of warnings (possibly empty) and does not raise in dev.
    assert isinstance(config.configuration_warnings(), list)
    assert config.validate_startup_configuration() is None


def test_scaffold_directories_exist():
    """Approved additive top-level folders exist (ADR-013: no backend/ or frontend/)."""
    for name in ("config", "shared", "infrastructure"):
        assert (REPO_ROOT / name).is_dir(), f"missing scaffold dir: {name}"
    # ADR-013 explicitly does NOT introduce these:
    assert not (REPO_ROOT / "backend").exists(), "backend/ must not be created (ADR-013)"
    assert not (REPO_ROOT / "frontend").exists(), "frontend/ must not be created (ADR-013)"


def test_example_env_is_secret_free():
    """config/.env.example must contain no real secrets — placeholders only."""
    example = (REPO_ROOT / "config" / ".env.example").read_text()
    secret_keys = ("SESSION_SECRET", "MICROSOFT_TOKEN_KEY")
    for line in example.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in secret_keys:
            # Secret-bearing keys must be empty in the committed example.
            assert value == "", f"{key} must be empty in the committed example"


def test_app_env_not_tracked():
    """The real app/.env (which may hold secrets) must never be tracked by git."""
    result = subprocess.run(
        ["git", "ls-files", "app/.env"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", "app/.env must be gitignored, not tracked"


def test_module_map_and_adr_present():
    """E1.1 documentation artifacts exist."""
    assert (REPO_ROOT / "docs" / "architecture" / "MODULE_MAP.md").is_file()
    assert (
        REPO_ROOT / "docs" / "architecture" / "adr" / "ADR-013-repository-reconciliation.md"
    ).is_file()
