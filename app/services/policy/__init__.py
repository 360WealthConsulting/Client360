"""Runtime Policy Engine (Phase D.32) — centralized business-decision services.

D.32 centralizes application BUSINESS DECISIONS (eligibility / routing / gating / visibility) behind a
single, declarative policy engine so no caller implements custom decision logic. Every policy
**consumes the D.28 ``RuntimeContext``** and never bypasses it — the runtime engine remains the sole
evaluator (policy decision functions delegate to ``app/services/runtime/consumption.py``, which
delegates to the engine); D.29 coordination remains the sole synchronization mechanism; D.27 remains
the sole metadata owner. Policies never bypass RBAC/capabilities/record-scope/audit — the capability
and scope checks stay at the call site; a policy only centralizes the *business* decision and returns
a structured, explainable :class:`~app.services.policy.result.PolicyResult`.

Public surface:
- ``policy.evaluate(code, *, context=…, subject=…)`` — evaluate a decision → ``PolicyResult``.
- ``policy.registry`` — policy discovery / versioning / lifecycle / dependency graph.
- ``policy.governance`` — validate the policy registry + runtime definitions.
"""
from __future__ import annotations

from .engine import evaluate, evaluation_stats, explain, reset_stats
from .result import PolicyResult

__all__ = ["PolicyResult", "evaluate", "evaluation_stats", "explain", "reset_stats"]
