"""Workflow SLA & escalation policy — formal specification (F4.6 / Epic 4, ADR-016).

A single, **pure** (DB-free), documented source of truth for SLA deadline evaluation
and escalation-policy decisions. The engine's ``evaluate_sla`` consumes these
functions, so the policy is defined once and cannot drift.

SLA processing **observes** workflow state (a step's deadline) but **never drives
execution** (ADR-016): it decides *whether* and *how* to escalate; it never transitions
or completes a workflow. Decisions are deterministic pure functions of ``(sla_due_at,
now)``, so evaluation is reproducible and idempotent at the policy layer.

The escalation policy is an **extension point** (`set_escalation_policy`): the default
mirrors the engine's existing behavior — a single level-1 ``sla_breach`` when a step's
deadline has passed — so behavior is unchanged.
"""
from __future__ import annotations

from collections.abc import Callable

ESCALATION_TYPE_SLA_BREACH = "sla_breach"
DEFAULT_ESCALATION_LEVEL = 1


def is_overdue(sla_due_at, now) -> bool:
    """Deterministic deadline check: a step is overdue when it has a due time in the past."""
    return sla_due_at is not None and sla_due_at < now


def _default_policy(sla_due_at, now) -> dict | None:
    """Default escalation policy: one level-1 ``sla_breach`` when overdue (else none)."""
    if is_overdue(sla_due_at, now):
        return {"escalation_type": ESCALATION_TYPE_SLA_BREACH, "level": DEFAULT_ESCALATION_LEVEL}
    return None


# Escalation policy (extension point). A policy is
# Callable[[sla_due_at, now], dict | None] returning {"escalation_type", "level"} or None.
_policy: Callable[..., dict | None] = _default_policy


def set_escalation_policy(policy: Callable[..., dict | None]) -> None:
    """Override the escalation policy (extension point; e.g. multi-level escalation)."""
    global _policy
    _policy = policy


def reset_escalation_policy() -> None:
    """Restore the default (level-1 sla_breach) escalation policy."""
    global _policy
    _policy = _default_policy


def evaluate_escalation(sla_due_at, now) -> dict | None:
    """Deterministic escalation decision for a step deadline.

    Returns an escalation spec ``{"escalation_type", "level"}`` when the deadline is
    breached, else ``None``. Pure and side-effect-free.
    """
    return _policy(sla_due_at, now)
