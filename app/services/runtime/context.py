"""Immutable per-request runtime context (Phase D.28).

Every request receives one immutable ``RuntimeContext`` — the effective configuration, the active
features, the resolved edition + license, and the current snapshot id — computed once and reused for
the whole request (no repeated configuration resolution). The context is a frozen dataclass; it is
built from the cached current snapshot plus a per-principal feature evaluation, so per-request cost is
in-memory only. Building a context never mutates metadata and never raises into the request path.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuntimeContext:
    snapshot_id: int | None
    snapshot_uid: str | None
    snapshot_version: int | None
    edition_code: str | None
    license_code: str | None
    effective_config: dict = field(default_factory=dict)
    active_features: dict = field(default_factory=dict)
    edition_capabilities: frozenset = field(default_factory=frozenset)
    resolved: bool = True

    # --- the standardized runtime consumption API (Phase D.30) ---------------

    def config(self, key: str, default=None):
        """Resolve an effective configuration value (default when unset)."""
        entry = (self.effective_config or {}).get(key)
        return entry.get("value") if entry else default

    def feature_enabled(self, code: str, default: bool = False) -> bool:
        """Whether a feature is enabled in this context; ``default`` when the feature is undefined."""
        entry = (self.active_features or {}).get(code)
        if entry is None:
            return bool(default)
        return bool(entry.get("enabled"))

    def feature_defined(self, code: str) -> bool:
        return code in (self.active_features or {})

    def edition(self) -> str | None:
        return self.edition_code

    def license(self) -> str | None:
        return self.license_code

    def capabilities(self) -> frozenset:
        """The set of capability codes the resolved edition includes (edition-gating view; RBAC
        remains the sole access authority)."""
        return self.edition_capabilities or frozenset()

    def to_dict(self) -> dict:
        return {"snapshot_id": self.snapshot_id, "snapshot_uid": self.snapshot_uid,
                "snapshot_version": self.snapshot_version, "edition_code": self.edition_code,
                "license_code": self.license_code, "active_features": self.active_features,
                "effective_config": self.effective_config,
                "edition_capabilities": sorted(self.edition_capabilities or ()), "resolved": self.resolved}


EMPTY_CONTEXT = RuntimeContext(snapshot_id=None, snapshot_uid=None, snapshot_version=None,
                               edition_code=None, license_code=None, resolved=False)
