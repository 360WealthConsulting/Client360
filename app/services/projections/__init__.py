"""Enterprise Read Models & Projection Engine (Phase D.36).

D.36 consumes the D.34/D.35 domain events from the transactional outbox (the sole event bus + log) to
build fast, query-optimized READ MODELS. It changes NO business behavior: the domain services remain the
sole authoritative mutation layer and the outbox remains authoritative. Read models exist only for
querying/dashboards/analytics/timelines/reporting/search/AI — they hold no authoritative business logic
or state, contain only event-derived references/statuses, and are **disposable** (deletable and
rebuildable deterministically from events). Projection failures never affect business transactions.

Public surface:
- ``projections.engine`` — process / rebuild / reset / replay / validate / tick (deterministic runtime).
- ``projections.registry`` — projection discovery / versioning / lifecycle / dependency graph.
- ``projections.governance`` — validate the read-model registry.
- ``projections.diagnostics`` — health / lag / size / rebuild history (read-only).
"""
from __future__ import annotations

from . import diagnostics, engine, governance, registry  # noqa: F401

__all__ = ["diagnostics", "engine", "governance", "registry"]
