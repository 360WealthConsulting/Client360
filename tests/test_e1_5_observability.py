"""E1.5 — Observability & structured logging foundation acceptance tests.

Verifies the central logging configurator behaves correctly, is scoped to the
`client360` namespace (does not clobber the root logger), and is wired into
application startup. No application behavior is exercised or changed.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.observability.logging import (
    APP_LOGGER,
    JsonFormatter,
    configure_logging,
    resolve_format,
    resolve_level,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_resolve_level_and_format():
    assert resolve_level("debug") == logging.DEBUG
    assert resolve_level("INFO") == logging.INFO
    assert resolve_level("nonsense") == logging.INFO  # safe fallback
    assert resolve_format("json") == "json"
    assert resolve_format(None) == "plain"
    assert resolve_format("weird") == "plain"


def test_configure_is_idempotent_and_scoped():
    """configure_logging() targets the client360 logger and never accumulates handlers."""
    root_before = list(logging.getLogger().handlers)

    logger = configure_logging(force=True)
    assert logger.name == APP_LOGGER
    assert len(logger.handlers) == 1
    assert logger.propagate is False

    # Idempotent: a second (non-forced) call does not add handlers.
    configure_logging()
    assert len(logging.getLogger(APP_LOGGER).handlers) == 1

    # The root logger was not reconfigured (uvicorn/root untouched).
    assert list(logging.getLogger().handlers) == root_before


def test_level_and_format_applied():
    logger = configure_logging(level="WARNING", fmt="json", force=True)
    assert logger.level == logging.WARNING
    assert isinstance(logger.handlers[0].formatter, JsonFormatter)
    # Restore a sane default for any later tests.
    configure_logging(level="INFO", fmt="plain", force=True)


def test_json_formatter_emits_valid_json_with_extras():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="client360.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    record.account_id = 42  # simulate logger.info(..., extra={"account_id": 42})
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "client360.test"
    assert parsed["message"] == "hello world"
    assert parsed["account_id"] == 42


def test_startup_wires_configure_logging():
    """The application lifespan calls configure_logging (import is side-effect free)."""
    main_source = (REPO_ROOT / "app" / "main.py").read_text()
    assert "configure_logging()" in main_source
    assert "from app.observability import configure_logging" in main_source
    # Importing the app must not raise (routes still register).
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.router.routes}
    assert "/health" in paths and "/readiness" in paths


def test_observability_doc_present():
    assert (REPO_ROOT / "docs" / "OBSERVABILITY.md").is_file()
