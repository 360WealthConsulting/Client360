"""Neutral notification-claim contract (Epic 5, ADR-017).

A neutral shared-contract module owned by **neither** the F5.6 worker nor the F5.9 ready-claim
layer. It holds only the :class:`PendingNotificationClaim` value object so both layers can
depend on the claim contract without depending on each other — keeping the dependency graph
acyclic (F5.9 must not import the F5.6 worker module).

Depends on nothing app-specific (stdlib only), so it sits at the base of the dependency graph.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingNotificationClaim:
    """An immutable value object representing one unit of work claimed by the worker.

    The worker operates on a **claim**, not a bare integer, so the interface stays stable as
    future scalable claims extend this object (e.g. lease token, lease expiration, claim
    timestamp, queue partition, worker ownership, priority). None of those fields exist today
    — the single-instance implementation carries only the notification references.
    """

    notification_id: int
    notification_uid: str | None = None
    created_at: object = None  # notification created_at reference (informational only)
