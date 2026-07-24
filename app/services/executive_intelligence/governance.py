"""Executive Reporting governance (Phase D.48) — read-only validation that the executive-intelligence layer
stays a COMPOSITION over the authoritative operational services + the SINGLE Analytics Registry, and never
becomes a second analytics engine, data warehouse, BI platform, reporting database, or metrics system.
Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no reporting warehouse, no ETL, no copied operational data).
  * No second metrics registry — widget KPIs flow through ``analytics.metrics.compute_metric`` (the one
    registry); this layer defines no ``Metric``/``_DEFS``.
  * The engine composes the authoritative reads (analytics, work_queue, workflow, portfolio, opportunity,
    communications, runtime, recommendations).
  * Every dashboard + widget is fully declared in the registries; every widget names an authoritative owner +
    source; no duplicate ownership.
  * Every widget is explainable (explanation + source + deep link) — enforced by the model + compute layer.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "widgets.py")

_AUTHORITATIVE_READS = ("analytics.metrics", "work_queue", "workflow_automation", "portfolio",
                        "opportunity", "communications", "runtime", "recommendations")


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_executive_reporting() -> dict:
    findings = []
    try:
        for mod in _MODULES:
            s = _src(mod)
            for verb in (".insert()", ".insert(", ".update(", ".delete()", "sa.insert", "sa.update",
                         "sa.delete"):
                if verb in s:
                    findings.append({"type": "database_write", "module": mod, "op": verb})
            if re.search(r"publish_safe\s*\(|publisher\.publish|publish_event\s*\(", s):
                findings.append({"type": "outbox_publication", "module": mod})
            if re.search(r"write_audit_event\s*\(", s):
                findings.append({"type": "audit_write", "module": mod})
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_read", "module": mod, "table": m})
            if re.search(r"Table\s*\(|define_\w+_tables\s*\(", s):
                findings.append({"type": "shadow_store_definition", "module": mod})
            if re.search(r"os\.getenv|os\.environ", s):
                findings.append({"type": "raw_env_fallback", "module": mod})
            # No second metrics registry: this layer must not define its own Metric catalog.
            if re.search(r"^_DEFS\s*=|class\s+Metric\b", s, re.M):
                findings.append({"type": "second_metrics_registry", "module": mod})

        # The widget KPIs must flow through the single Analytics Registry (compute_metric).
        widgets_src = _src("widgets.py")
        if "compute_metric" not in widgets_src:
            findings.append({"type": "not_reusing_analytics_registry"})
        composed = _src("service.py") + widgets_src
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in widgets_src:
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for d in registry.DASHBOARD_REGISTRY:
            if not d.owner or not d.audience or not d.runtime_gate or not d.navigation:
                findings.append({"type": "dashboard_incomplete", "dashboard": d.key})
            if not d.required_capabilities or not d.governing_services:
                findings.append({"type": "dashboard_missing_caps_or_services", "dashboard": d.key})
            if d.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_dashboard_lifecycle", "dashboard": d.key})
            for wkey in d.widgets:
                if not registry.widget_registered(wkey):
                    findings.append({"type": "dashboard_widget_unregistered", "dashboard": d.key,
                                     "widget": wkey})
        for w in registry.WIDGET_REGISTRY:
            if not w.owner or not w.source or not w.deep_link or not w.explainability:
                findings.append({"type": "widget_incomplete", "widget": w.key})
            if not w.permission:
                findings.append({"type": "widget_without_permission", "widget": w.key})
            if w.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_widget_lifecycle", "widget": w.key})
        dkeys = [d.key for d in registry.DASHBOARD_REGISTRY]
        wkeys = [w.key for w in registry.WIDGET_REGISTRY]
        if len(dkeys) != len(set(dkeys)) or len(wkeys) != len(set(wkeys)):
            findings.append({"type": "duplicate_registry_ownership"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
