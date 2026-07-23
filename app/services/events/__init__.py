"""Enterprise Domain Event Model (Phase D.34) — a governed, typed event model over the outbox.

D.34 standardizes a typed, versioned domain-event model so every major business action can emit typed
domain events that other modules consume asynchronously — without direct inter-module dependencies. It
**reuses the existing transactional outbox** (``app/platform/outbox.py`` + ``app/platform/events.py``)
as the internal event bus: delivery guarantees, idempotency, dead-letter, and envelope versioning
already exist. It adds no second event table (the architecture invariant) — a domain event is a
contract-validated envelope in ``outbox_events``. It never bypasses RBAC — capability checks stay at
the call site.

Public surface:
- ``events.publisher`` — the standardized publish API (validate against the typed contract → outbox).
- ``events.registry`` — contract + subscription discovery / versioning / lifecycle / coverage.
- ``events.governance`` — validate the event model.
- ``events.diagnostics`` / ``events.replay`` — read-only event-flow inspection + deterministic replay.
"""
from __future__ import annotations

from . import (  # noqa: F401
           contracts,
           diagnostics,
           governance,
           publisher,
           registry,
           replay,
           subscriptions,
)

__all__ = ["contracts", "diagnostics", "governance", "publisher", "registry", "replay",
           "subscriptions"]
