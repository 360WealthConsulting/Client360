"""Client Portal internal diagnostics (Phase D.43) — INTERNAL-ONLY observability of portal health.

Composes the in-process counters (``stats``), the gate snapshot (``gate``), the visibility-registry
coverage, and the governance report into a single, low-cardinality report for the internal admin surface
(``observability.audit``). Exposes low-cardinality aggregates only — no client data, no identifiers, no
secrets. Must never be reachable from an external ``/portal`` route.
"""
from __future__ import annotations

from app.portal import stats, visibility
from app.portal.gate import gate_status, portal_enabled, production_ready
from app.portal.governance import validate_portal


def portal_diagnostics() -> dict:
    gov = validate_portal()
    return {
        "enabled": portal_enabled(),
        "production_ready": production_ready(),
        "gates": gate_status(),
        "stats": stats.portal_stats(),
        "visibility_coverage": visibility.coverage(),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
