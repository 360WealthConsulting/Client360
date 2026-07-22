"""In-process runtime cache (Phase D.28) — versioned, self-invalidating, instrumented.

A single-process cache for the hydrated effective configuration, edition/capability maps, feature
evaluations, and the current snapshot — so a request never re-resolves configuration. It carries a
monotonic ``version`` (bumped on every invalidation) for cache-versioning and stale detection,
per-entry TTL for automatic expiration, and hit/miss/eval counters for observability (D.26) and
analytics (D.15) — those counters run in the same process as the web app and metric compute
callables, so they are readable by an Analytics ``Metric``. Mirrors the module-global lazy-cache
house style (``app/routes/ops.py``) with explicit invalidation added.
"""
from __future__ import annotations

import threading
import time

_DEFAULT_TTL_SECONDS = 300


class RuntimeCache:
    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._lock = threading.RLock()
        self._store: dict[str, tuple[float, object]] = {}
        self._ttl = ttl_seconds
        self._version = 0
        self._hits = 0
        self._misses = 0
        self._evaluations = 0
        self._warmed_at: float | None = None

    # --- core ---------------------------------------------------------------

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if expires_at is not None and time.monotonic() > expires_at:
                self._store.pop(key, None)   # automatic expiration
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value, *, ttl: int | None = None):
        with self._lock:
            expiry = time.monotonic() + (ttl if ttl is not None else self._ttl)
            self._store[key] = (expiry, value)

    def invalidate(self):
        """Version-invalidate: bump the version and clear all entries (safe refresh)."""
        with self._lock:
            self._version += 1
            self._store.clear()

    def note_evaluation(self, n: int = 1):
        with self._lock:
            self._evaluations += n

    def mark_warmed(self):
        with self._lock:
            self._warmed_at = time.monotonic()

    # --- introspection ------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_ratio = (self._hits / total) if total else None
            return {"version": self._version, "size": len(self._store), "hits": self._hits,
                    "misses": self._misses, "hit_ratio": hit_ratio, "evaluations": self._evaluations,
                    "warmed": self._warmed_at is not None, "ttl_seconds": self._ttl}


# Module-global singleton (one runtime cache per process).
RUNTIME_CACHE = RuntimeCache()
