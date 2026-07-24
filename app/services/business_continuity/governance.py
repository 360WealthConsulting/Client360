"""Business Continuity governance (Phase D.55) — read-only validation that the business-continuity layer
stays a COMPOSITION over the authoritative operational-resilience owners, and never becomes a second backup
platform, monitoring system, disaster-recovery engine, scheduler, notification system, or incident manager.
Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow backup / monitoring / incident / maintenance store).
  * No mutation / no backup or restore execution / no monitoring or incident or scheduler change — the layer
    never calls a backup / restore / monitoring / incident / maintenance / scheduler mutation (`run_backup`,
    `restore_backup`, `run_sync`, `run_due_scans`, `run_due_reviews`, `raise_alert`, `acknowledge_alert`,
    `resolve_alert`, `set_service_status`, `set_incident_status`, `set_finding_status`,
    `set_maintenance_status`, `create_incident`, `enqueue_run`, `execute_run`, `run_job`, `heartbeat`,
    `converge_worker`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every resilience domain + recovery asset + panel + dashboard is fully declared; every panel names an
    authoritative owner + source + deep link; no duplicate ownership.
  * Every panel is explainable (explanation + source + deep link) — enforced by the model + compute layer.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("observability", "runtime", "automation", "communications")

# Mutating/execution entry points this layer must NEVER call (would duplicate a backup/monitoring/DR engine).
_FORBIDDEN_CALLS = (
    "run_backup(", "restore_backup(", "run_sync(", "run_due_scans(", "run_due_reviews(", "raise_alert(",
    "acknowledge_alert(", "resolve_alert(", "set_service_status(", "set_incident_status(",
    "set_finding_status(", "set_maintenance_status(", "create_incident(", "enqueue_run(", "execute_run(",
    "run_job(", "heartbeat(", "converge_worker(", "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_business_continuity() -> dict:
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
            if re.search(r"^_DEFS\s*=|class\s+Metric\b", s, re.M):
                findings.append({"type": "second_metrics_registry", "module": mod})
            for call in _FORBIDDEN_CALLS:
                if call in s:
                    findings.append({"type": "duplicate_engine_call", "module": mod, "call": call})

        # The composition must reference the authoritative resilience reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative Observability domain must be composed (no second monitoring system).
        if "observability" not in composed:
            findings.append({"type": "not_reusing_observability_owner"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for r in registry.RESILIENCE_REGISTRY:
            if not r.authoritative_owner or not r.health_owner or not r.monitoring_owner or not r.deep_links:
                findings.append({"type": "resilience_incomplete", "resilience": r.key})
            if not r.runtime_gate:
                findings.append({"type": "resilience_missing_gate", "resilience": r.key})
        for a in registry.RECOVERY_REGISTRY:
            if not a.owner or not a.backup_owner or not a.restore_owner or not a.rpo or not a.rto \
                    or not a.runtime_gate:
                findings.append({"type": "recovery_incomplete", "recovery": a.key})
        for d in registry.CONTINUITY_DASHBOARDS:
            if not d.owner or not d.audience or not d.runtime_gate or not d.navigation or not d.panels:
                findings.append({"type": "dashboard_incomplete", "dashboard": d.key})
            if not d.required_capabilities or not d.governing_services:
                findings.append({"type": "dashboard_missing_caps_or_services", "dashboard": d.key})
            if d.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_dashboard_lifecycle", "dashboard": d.key})
            for pkey in d.panels:
                if not registry.panel_registered(pkey):
                    findings.append({"type": "dashboard_panel_unregistered", "dashboard": d.key,
                                     "panel": pkey})
        for p in registry.PANEL_REGISTRY:
            if not p.owner or not p.source or not p.deep_link or not p.explainability:
                findings.append({"type": "panel_incomplete", "panel": p.key})
            if not p.permission:
                findings.append({"type": "panel_without_permission", "panel": p.key})
            if p.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_panel_lifecycle", "panel": p.key})
        for label, keys in (("resilience", [r.key for r in registry.RESILIENCE_REGISTRY]),
                            ("recovery", [a.key for a in registry.RECOVERY_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.CONTINUITY_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
