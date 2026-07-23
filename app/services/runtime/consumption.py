"""Standardized runtime consumption API (Phase D.30) — the single behavioral-decision entry point.

Application behavior consumes the D.28 Runtime Configuration Engine ONLY through this API (no feature
bypasses it). Every entry is **behavior-preserving**: if the runtime engine has a definition for the
feature/config key, its evaluation is used (a *runtime decision*); otherwise the caller's ``default``
— the legacy behavior — is returned (a *legacy fallback*). Decisions are counted in-process for
adoption analytics/observability. This layer never evaluates configuration itself (it delegates to the
engine, the sole evaluator) and never edits metadata, and it never bypasses RBAC — capability checks
remain at the call site / route.

Non-request callers (scheduler, automation, detectors) obtain a context via ``runtime_context()``;
request handlers pass the per-request ``RuntimeContext`` already attached by the middleware.
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {"runtime_decisions": 0, "legacy_fallbacks": 0, "config_lookups": 0, "feature_lookups": 0}


def _note(kind: str):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + 1


def runtime_context(*, organization_id=None, user_id=None, principal_roles=None):
    """Build (or reuse the cached-snapshot-backed) runtime context for a non-request caller. Cheap —
    reads the cached snapshot; never raises."""
    try:
        from . import engine as runtime_engine
        return runtime_engine.context_for(None, organization_id=organization_id, user_id=user_id,
                                           principal_roles=principal_roles)
    except Exception:
        from .context import EMPTY_CONTEXT
        return EMPTY_CONTEXT


def feature_enabled(code: str, *, context=None, default: bool = False, organization_id=None,
                    user_id=None, principal_roles=None) -> bool:
    """Standard behavioral decision. Returns the runtime evaluation when the feature is defined, else
    the legacy ``default``. Never raises — always returns a bool."""
    _note("feature_lookups")
    try:
        ctx = context if context is not None else runtime_context(
            organization_id=organization_id, user_id=user_id, principal_roles=principal_roles)
        if ctx.feature_defined(code):
            _note("runtime_decisions")
            return ctx.feature_enabled(code, default)
    except Exception:
        pass
    _note("legacy_fallbacks")
    return bool(default)


def config_value(key: str, *, context=None, default=None, organization_id=None, user_id=None):
    """Standard configuration read. Returns the effective runtime value when set, else the legacy
    ``default``. Never raises."""
    _note("config_lookups")
    try:
        ctx = context if context is not None else runtime_context(
            organization_id=organization_id, user_id=user_id)
        val = ctx.config(key, None)
        if val is not None:
            _note("runtime_decisions")
            return val
    except Exception:
        pass
    _note("legacy_fallbacks")
    return default


def edition(*, context=None, organization_id=None):
    ctx = context if context is not None else runtime_context(organization_id=organization_id)
    return ctx.edition()


def license_code(*, context=None):
    ctx = context if context is not None else runtime_context()
    return ctx.license()


def capabilities(*, context=None, organization_id=None) -> frozenset:
    ctx = context if context is not None else runtime_context(organization_id=organization_id)
    return ctx.capabilities()


def adoption_stats() -> dict:
    """In-process runtime-consumption counters (feature/config lookups, runtime decisions vs legacy
    fallbacks, adoption ratio). Readable by an Analytics metric (same process)."""
    with _lock:
        s = dict(_STATS)
    total = s["runtime_decisions"] + s["legacy_fallbacks"]
    s["total_lookups"] = total
    s["runtime_adoption_pct"] = (round((s["runtime_decisions"] / total) * 100, 1) if total else None)
    return s
