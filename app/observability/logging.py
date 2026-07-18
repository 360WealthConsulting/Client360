"""Environment-aware structured logging for Client360 (E1.5).

Before E1.5 the application used ad-hoc ``logging.getLogger("client360.*")``
calls with no central configuration, so log level and format were Python
defaults (WARNING, unformatted, to stderr). This module provides one idempotent
``configure_logging()`` that gives the ``client360`` logger a consistent handler,
a level from ``LOG_LEVEL``, and either human-readable or JSON output via
``LOG_FORMAT``.

Scope discipline: it configures the ``client360`` namespace only. It deliberately
does NOT reconfigure the root logger or uvicorn's loggers, so it never alters
request handling, responses, or any application behavior — only how the app's own
log lines are rendered. It is safe to call more than once (idempotent).
"""
from __future__ import annotations

import json
import logging
import os
import sys

APP_LOGGER = "client360"
DEFAULT_LEVEL = "INFO"
DEFAULT_FORMAT = "plain"
_PLAIN_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Standard LogRecord attributes, so JSON output can surface any `extra=` fields.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__
) | {"message", "asctime", "taskName"}

_configured = False


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object (structured logs)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Surface structured context passed via logger.x(..., extra={...}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def resolve_level(level: str | None = None) -> int:
    """Resolve a level name (arg > LOG_LEVEL env > INFO) to a logging constant."""
    name = (level or os.getenv("LOG_LEVEL") or DEFAULT_LEVEL).upper()
    resolved = logging.getLevelName(name)
    return resolved if isinstance(resolved, int) else logging.INFO


def resolve_format(fmt: str | None = None) -> str:
    """Resolve the output format (arg > LOG_FORMAT env > plain)."""
    value = (fmt or os.getenv("LOG_FORMAT") or DEFAULT_FORMAT).lower()
    return "json" if value == "json" else "plain"


def configure_logging(
    *, level: str | None = None, fmt: str | None = None, force: bool = False
) -> logging.Logger:
    """Configure the ``client360`` logger. Idempotent unless ``force=True``.

    Returns the configured application logger.
    """
    global _configured
    logger = logging.getLogger(APP_LOGGER)
    if _configured and not force:
        return logger

    chosen_format = resolve_format(fmt)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if chosen_format == "json" else logging.Formatter(_PLAIN_FORMAT)
    )

    logger.handlers[:] = [handler]  # replace (idempotent — never accumulates)
    logger.setLevel(resolve_level(level))
    logger.propagate = False  # do not double-emit through the root logger

    _configured = True
    logger.info(
        "logging configured",
        extra={"log_format": chosen_format, "log_level": logging.getLevelName(logger.level)},
    )
    return logger
