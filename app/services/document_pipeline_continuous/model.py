"""Shared vocabulary and table bindings for the continuous document pipeline.

Everything in this package speaks the stage/state/outcome vocabulary defined here, and reaches the
five ``document_pipeline_*`` tables through :func:`tables`. The bind is TOLERANT: an environment that
has not applied the ``docpipe01`` migration yet imports this package cleanly and every entry point
refuses with :class:`PipelineNotInstalled` instead of raising ``KeyError`` at import time. That matters
because ``app.db`` reflects the whole schema at import: an eager ``metadata.tables[...]`` here would
stop the entire application from starting on a host that is one migration behind.
"""
from __future__ import annotations

from sqlalchemy import Table

from app.db import engine, metadata

# --- vocabulary --------------------------------------------------------------------------------

#: Pipeline stages, in the order a document travels them. ``discovered`` is the entry state (a task
#: exists but no work has begun); ``done`` is terminal.
STAGE_DISCOVERED = "discovered"
STAGE_EXTRACT = "extract"
STAGE_OCR = "ocr"
STAGE_CLASSIFY = "classify"
STAGE_OWNERSHIP = "ownership"
STAGE_DONE = "done"

STAGES = (STAGE_DISCOVERED, STAGE_EXTRACT, STAGE_OCR, STAGE_CLASSIFY, STAGE_OWNERSHIP, STAGE_DONE)

#: The default successor of each stage. The extract stage overrides this to SKIP ``ocr`` when the
#: document already yielded embedded text, which is the single largest saving in the whole pipeline.
NEXT_STAGE = {
    STAGE_DISCOVERED: STAGE_EXTRACT,
    STAGE_EXTRACT: STAGE_OCR,
    STAGE_OCR: STAGE_CLASSIFY,
    STAGE_CLASSIFY: STAGE_OWNERSHIP,
    STAGE_OWNERSHIP: STAGE_DONE,
}

STATE_QUEUED = "queued"
STATE_LEASED = "leased"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_BLOCKED = "blocked"
STATE_REVIEW = "review"

STATES = (STATE_QUEUED, STATE_LEASED, STATE_SUCCEEDED, STATE_FAILED, STATE_BLOCKED, STATE_REVIEW)

#: A task in one of these states is finished; discovery re-queues it only when the content changes.
TERMINAL_STATES = (STATE_SUCCEEDED, STATE_BLOCKED, STATE_REVIEW)

OUTCOME_LINKED = "linked"
OUTCOME_REVIEW = "review"
OUTCOME_UNRESOLVED = "unresolved"
OUTCOME_ALREADY_OWNED = "already_owned"
OUTCOME_BLOCKED = "blocked"

OUTCOMES = (OUTCOME_LINKED, OUTCOME_REVIEW, OUTCOME_UNRESOLVED, OUTCOME_ALREADY_OWNED,
            OUTCOME_BLOCKED)

#: Ownership lanes, in the order :mod:`.ownership` consults them. Drake and TaxDome are AUTHORITATIVE:
#: when either applies, its verdict stands and the evidence lane is never consulted for that document.
LANE_DRAKE = "drake"
LANE_TAXDOME = "taxdome"
LANE_SHAREPOINT = "sharepoint"

LANES = (LANE_DRAKE, LANE_TAXDOME, LANE_SHAREPOINT)

#: Error classes. A ``transient`` failure is retried with backoff; a ``permanent`` one leaves the
#: queue immediately for the blocker queue, because retrying an encrypted PDF five times is five ways
#: of learning the same thing.
ERROR_TRANSIENT = "transient"
ERROR_PERMANENT = "permanent"

#: The five tables this package owns. Nothing here writes to any other table directly — ownership
#: goes through ``households.resolve_document_ownership``, OCR state through ``document_ocr``, and
#: classification/proposals through ``document_pipeline``.
TABLE_NAMES = ("document_pipeline_tasks", "document_pipeline_blockers",
               "document_pipeline_ownership_reviews", "document_pipeline_workers",
               "document_pipeline_checkpoints")

#: The single discovery checkpoint row seeded by the migration.
DISCOVERY_CHECKPOINT = "discovery"


class PipelineError(RuntimeError):
    """Base class for continuous-pipeline failures."""


class PipelineNotInstalled(PipelineError):
    """The ``docpipe01`` migration has not been applied to the connected database."""


class PipelineTransientError(PipelineError):
    """A failure worth retrying — a locked file, a busy engine, a dropped connection."""


class PipelinePermanentError(PipelineError):
    """A failure retrying cannot fix. Carries the blocker ``reason_code`` it will be filed under."""

    def __init__(self, reason_code: str, message: str = ""):
        super().__init__(message or reason_code)
        self.reason_code = reason_code


# --- table bindings ----------------------------------------------------------------------------

_cache: dict[str, Table] = {}


def _bind(name: str) -> Table | None:
    """Reflect one pipeline table, tolerating its absence. Cached per process."""
    if name in _cache:
        return _cache[name]
    table = metadata.tables.get(name)
    if table is None:
        try:
            table = Table(name, metadata, autoload_with=engine)
        except Exception:      # noqa: BLE001 — absent (migration not applied) or unreachable
            return None
    _cache[name] = table
    return table


def installed() -> bool:
    """True when every pipeline table exists in the connected database."""
    return all(_bind(name) is not None for name in TABLE_NAMES)


def tables() -> dict[str, Table]:
    """The five pipeline tables, keyed by their short name (``tasks``, ``blockers``, ``reviews``,
    ``workers``, ``checkpoints``). Raises :class:`PipelineNotInstalled` if any is missing, so a
    half-migrated database fails with a sentence an operator can act on rather than a ``KeyError``."""
    bound = {name: _bind(name) for name in TABLE_NAMES}
    missing = sorted(name for name, table in bound.items() if table is None)
    if missing:
        raise PipelineNotInstalled(
            "the continuous document pipeline is not installed in this database: "
            f"missing {', '.join(missing)}. Apply the docpipe01 migration "
            "(`alembic upgrade head`) before starting the pipeline.")
    return {
        "tasks": bound["document_pipeline_tasks"],
        "blockers": bound["document_pipeline_blockers"],
        "reviews": bound["document_pipeline_ownership_reviews"],
        "workers": bound["document_pipeline_workers"],
        "checkpoints": bound["document_pipeline_checkpoints"],
    }


def reset_binding_cache() -> None:
    """Drop the cached bindings. Used by tests that create or drop the tables mid-process."""
    _cache.clear()
