"""Notification activation layer (F5.8 / Epic 5, ADR-017).

The **activation** entry point: a stateless tick handler that invokes an injected worker
callable **exactly once** and returns an activation-level outcome.

    activation occurred  ->  invoke the worker exactly once  ->  return activation-level outcome

It owns nothing notification-specific. It does **not** select notifications, evaluate retry
eligibility, read the ledger or delivery-attempt history, perform retry/due-time arithmetic,
construct claims, call the retry-decision layer, or touch any database. It holds no state
between activations and keeps no wall-clock — it only times the invocation and reports whether
the worker succeeded or failed.

The worker is a **generic injected callable** (``Callable[[], Any]``) returning an opaque
result — F5.8 is deliberately independent of any specific worker implementation, so the same
activation mechanism is reusable. The worker's return value is carried through **opaquely**;
F5.8 never reads or reinterprets it. Notification-, claim-, and dispatch-level failures and
metrics belong to the worker and the layers below it, not here.

Operational safety: this entry point may be invoked manually and in tests, but **no recurring
production driver may be enabled until the future F5.9 "Ready Notification Claim" feature is
complete** — today's worker claim does not enforce retry-delay timing, so a recurring
activation would re-attempt transiently-failed (still-pending) notifications immediately,
ignoring the retry delay. External deployment drivers (in-process loop, APScheduler, cron,
serverless timer, K8s CronJob, ...) are out of scope and unimplemented; enabling any of them is
gated on F5.9.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("client360.notifications.activation")


@dataclass(frozen=True)
class ActivationResult:
    """Immutable, activation-level outcome of one activation. Carries only activation facts;
    the worker's own result is nested **opaquely** (never reinterpreted or flattened)."""

    started: bool
    completed: bool
    cancelled: bool
    worker_invoked: bool
    worker_ok: bool
    runtime_ms: float
    error_class: str | None = None   # content-free classification of an activation-level failure
    worker_result: Any = None        # opaque nested worker result; F5.8 does not inspect it

    def to_dict(self) -> dict:
        wr = self.worker_result
        return {
            "started": self.started, "completed": self.completed, "cancelled": self.cancelled,
            "worker_invoked": self.worker_invoked, "worker_ok": self.worker_ok,
            "runtime_ms": self.runtime_ms, "error_class": self.error_class,
            # delegate to the worker's own representation if it has one; never re-derive it.
            "worker_result": (wr.to_dict() if hasattr(wr, "to_dict") else wr),
        }


def activate(*, worker: Callable[[], Any], stop: Callable[[], bool] | None = None) -> ActivationResult:
    """Invoke the injected ``worker`` callable exactly once and return an
    :class:`ActivationResult`.

    Stateless: holds nothing between calls, reads no database, keeps no wall-clock. ``worker``
    is a generic zero-argument callable returning an opaque result — F5.8 is independent of any
    specific worker implementation. ``stop`` is an optional cooperative-cancellation predicate
    checked **before** invocation: if it already requests a stop, the worker is not invoked and
    a cancelled outcome is returned. A worker exception is caught and represented as an
    activation-level failure (``worker_ok=False``) **without** inspecting notifications.
    """
    # driver cancellation / cooperative shutdown requested before we start: do not invoke.
    if stop is not None and stop():
        return ActivationResult(started=True, completed=True, cancelled=True,
                                worker_invoked=False, worker_ok=False, runtime_ms=0.0)

    start = time.monotonic()
    worker_ok = False
    error_class: str | None = None
    result: Any = None
    try:
        result = worker()          # invoke exactly once; opaque result
        worker_ok = True
    except Exception as exc:       # activation-level failure only — no notification inspection
        error_class = type(exc).__name__
        logger.warning("activation: worker invocation failed", extra={"error_class": error_class})
    runtime_ms = (time.monotonic() - start) * 1000.0

    return ActivationResult(
        started=True, completed=True, cancelled=False, worker_invoked=True,
        worker_ok=worker_ok, runtime_ms=runtime_ms, error_class=error_class, worker_result=result,
    )
