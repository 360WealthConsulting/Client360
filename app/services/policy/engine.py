"""The Runtime Policy Engine (Phase D.32) — deterministic, explainable decision execution.

``evaluate(code, …)`` is the single entry point for a centralized business decision. It resolves the
policy definition, obtains the immutable D.28 ``RuntimeContext`` (the runtime engine remains the sole
evaluator — the engine only *composes* runtime evaluations into a business decision, it never resolves
configuration itself), evaluates the decision deterministically, composes any policy dependencies, and
returns a fully-explained :class:`PolicyResult`. Results are cached in-process keyed on the runtime
snapshot + subject, so repeated identical decisions are free (policy cache hits). The engine never
raises into a caller — a failed decision falls back to the behavior-preserving default and is counted
(policy failures) for observability. It never bypasses RBAC/scope: capability enforcement stays at the
call site; a policy only reports which capabilities its decision references.
"""
from __future__ import annotations

import threading
import time
import weakref

from app.services.runtime import consumption

from .definitions import _UNSET, POLICY_DEFINITIONS
from .result import PolicyResult

_lock = threading.RLock()
_STATS = {"evaluations": 0, "cache_hits": 0, "cache_misses": 0, "failures": 0,
          "composed_evaluations": 0, "total_latency_us": 0}
# The decision cache is scoped to a single immutable RuntimeContext object (per request / per loop),
# keyed by that object's id — each context is an immutable snapshot of the runtime state, so a decision
# evaluated against it is stable. Runtime *features* are evaluated live per call (not bound to a
# snapshot version), so caching across contexts would mask a live change; scoping to one context object
# keeps runtime authority reflected immediately (a new call builds a new context → fresh evaluation).
# A weakref finalizer drops a context's bucket when it is garbage-collected, so an id is never reused
# stale. Cache hits therefore accrue within a request (repeated decisions + policy composition).
_CACHE: dict[int, dict] = {}
_CACHE_CAP = 4096


def _note(kind: str, n: int = 1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + n


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def _bucket_for(ctx):
    """The per-context decision-cache bucket (created + weak-finalized on first use); None if the
    context is not weakly referenceable."""
    k = id(ctx)
    with _lock:
        bucket = _CACHE.get(k)
        if bucket is None:
            if len(_CACHE) >= _CACHE_CAP:
                _CACHE.clear()
            bucket = _CACHE[k] = {}
            try:
                weakref.finalize(ctx, _drop_bucket, k)
            except TypeError:
                _CACHE.pop(k, None)
                return None
    return bucket


def _drop_bucket(k: int):
    with _lock:
        _CACHE.pop(k, None)


def evaluate(code: str, *, context=None, subject=None, default=_UNSET, principal=None) -> PolicyResult:
    """Evaluate a centralized business decision → :class:`PolicyResult`. Consumes the runtime context
    (built from the cached snapshot when not supplied); deterministic; never raises."""
    started = time.perf_counter_ns()
    _note("evaluations")
    ctx = context if context is not None else consumption.runtime_context()

    definition = POLICY_DEFINITIONS.get(code)
    if definition is None:
        _note("failures")
        dflt = None if default is _UNSET else default
        return PolicyResult(decision=dflt, explanation=f"unknown policy {code!r}", policy_id=code,
                            runtime_snapshot_id=getattr(ctx, "snapshot_id", None),
                            evaluated_at=_now_iso())

    bucket = _bucket_for(ctx)
    subkey = (code, repr(subject))
    if bucket is not None:
        with _lock:
            hit = bucket.get(subkey)
        if hit is not None:
            _note("cache_hits")
            _note("total_latency_us", max(0, (time.perf_counter_ns() - started) // 1000))
            return PolicyResult(decision=hit.decision, explanation=hit.explanation, policy_id=hit.policy_id,
                                runtime_snapshot_id=hit.runtime_snapshot_id,
                                evaluated_features=hit.evaluated_features,
                                evaluated_capabilities=hit.evaluated_capabilities,
                                evaluated_at=hit.evaluated_at, cached=True, dependencies=hit.dependencies)
    _note("cache_misses")

    try:
        raw = definition.decide(ctx, subject, default)
        decision = raw.decision
        explanation = raw.explanation
        features = tuple(raw.evaluated_features)
        deps: list[str] = []
        # --- policy composition: AND boolean dependencies; record + merge their evaluations ---------
        for dep_code in definition.depends_on:
            dep = evaluate(dep_code, context=ctx)
            _note("composed_evaluations")
            deps.append(dep_code)
            features = features + tuple(dep.evaluated_features)
            if isinstance(decision, bool):
                if not bool(dep.decision):
                    decision = False
                    explanation = f"{explanation}; blocked by dependency {dep_code!r}"
        result = PolicyResult(
            decision=decision, explanation=explanation, policy_id=code,
            runtime_snapshot_id=getattr(ctx, "snapshot_id", None),
            evaluated_features=features,
            evaluated_capabilities=tuple(definition.required_capabilities),
            evaluated_at=_now_iso(), cached=False, dependencies=tuple(deps))
    except Exception as exc:   # never raise into a caller — fall back to the behavior-preserving default
        _note("failures")
        dflt = definition.default_decision if default is _UNSET else default
        if not isinstance(dflt, (bool, str, int, float, type(None))):
            dflt = None
        return PolicyResult(decision=dflt, explanation=f"policy evaluation error: {exc}", policy_id=code,
                            runtime_snapshot_id=getattr(ctx, "snapshot_id", None),
                            evaluated_capabilities=tuple(definition.required_capabilities),
                            evaluated_at=_now_iso())

    if bucket is not None:
        with _lock:
            bucket[subkey] = result
    _note("total_latency_us", max(0, (time.perf_counter_ns() - started) // 1000))
    return result


def explain(code: str, *, subject=None, context=None) -> dict:
    """Diagnostics: evaluate a policy and return its full explanation as a dict (never raises)."""
    return evaluate(code, subject=subject, context=context).to_dict()


def evaluation_stats() -> dict:
    """In-process policy-execution counters (evaluations, cache hits/misses, failures, latency).
    Readable by an Analytics metric / observability in the same process. Routine successful
    evaluations are counted here, never individually logged."""
    with _lock:
        s = dict(_STATS)
    evals = s["evaluations"]
    s["cache_hit_ratio"] = round(s["cache_hits"] / evals, 4) if evals else None
    s["avg_latency_us"] = round(s["total_latency_us"] / evals, 2) if evals else None
    s["failure_rate"] = round(s["failures"] / evals, 4) if evals else None
    return s


def reset_stats():
    """Reset the in-process counters + decision cache (test/diagnostic use)."""
    with _lock:
        for k in list(_STATS):
            _STATS[k] = 0
        _CACHE.clear()
