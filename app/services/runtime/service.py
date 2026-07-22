"""Runtime engine service facade (Phase D.28) — overview metrics + audit re-export for routes.

Aggregates the engine's runtime state for the overview surface. The runtime engine owns evaluation
only; it references the D.27 metadata and never mutates it, and it never bypasses RBAC/scope (routes
gate every surface). It imports its own submodules and shared infrastructure — never a composition
layer.
"""
from __future__ import annotations

from . import engine as runtime_engine
from .common import audit_history  # re-exported for routes


def overview_metrics(principal=None) -> dict:
    m = runtime_engine.metrics(principal)
    readiness = runtime_engine.readiness()
    return {"snapshots": m["snapshots"], "latest_version": m["latest_version"],
            "cache_hit_ratio": m["cache_hit_ratio"], "cache_version": m["cache_version"],
            "evaluations": m["evaluations"], "hydrated": m["hydrated"],
            "validation_ok": readiness["validation_ok"], "issue_count": readiness["issue_count"]}


__all__ = ["overview_metrics", "audit_history"]
