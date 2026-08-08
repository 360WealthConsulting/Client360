"""MigrationConfig.from_env() must load the deployed app/.env.

Regression guard: a standalone migration CLI calls ``from_env()`` before anything imports ``app.db``
(which is what loads ``.env`` when the app runs as a service). Without this, env-overridable paths such as
``CLIENT360_MIGRATION_DEST_ROOT`` silently fall back to their code defaults (e.g. the legacy
``D:\\Client360Data`` instead of the deployed ``D:\\Client360\\Content``).
"""
import os
from pathlib import Path

from app.services.migration import config as cfgmod


def test_from_env_loads_the_app_dotenv(monkeypatch):
    seen = {}
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda p=None, *a, **k: seen.update(path=p))
    cfgmod.MigrationConfig.from_env()
    assert seen.get("path") == cfgmod._APP_ENV_PATH
    assert str(cfgmod._APP_ENV_PATH).replace("\\", "/").endswith("app/.env")


def test_from_env_honors_dotenv_dest_root(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    envfile.write_text("CLIENT360_MIGRATION_DEST_ROOT=D:\\Client360\\Content\n", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_APP_ENV_PATH", envfile)
    monkeypatch.delenv("CLIENT360_MIGRATION_DEST_ROOT", raising=False)
    saved = os.environ.get("CLIENT360_MIGRATION_DEST_ROOT")
    try:
        cfg = cfgmod.MigrationConfig.from_env()
        assert str(cfg.migration_dest_root) == "D:\\Client360\\Content"   # from .env, not the code default
    finally:
        os.environ.pop("CLIENT360_MIGRATION_DEST_ROOT", None)
        if saved is not None:
            os.environ["CLIENT360_MIGRATION_DEST_ROOT"] = saved


def test_default_dest_root_unchanged_when_env_absent(tmp_path, monkeypatch):
    empty = tmp_path / ".env"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "_APP_ENV_PATH", empty)
    monkeypatch.delenv("CLIENT360_MIGRATION_DEST_ROOT", raising=False)
    saved = os.environ.get("CLIENT360_MIGRATION_DEST_ROOT")
    try:
        cfg = cfgmod.MigrationConfig.from_env()
        assert str(cfg.migration_dest_root) == str(Path("D:\\Client360Data"))   # code default preserved
    finally:
        if saved is not None:
            os.environ["CLIENT360_MIGRATION_DEST_ROOT"] = saved
