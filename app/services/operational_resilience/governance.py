"""Enterprise Operational Resilience governance (Phase D.60) — read-only validation that the resilience layer
stays a COMPOSITION over the authoritative operational-resilience owners, and never becomes a second
incident-management platform, ticketing system, monitoring platform, help desk, disaster-recovery platform,
change-management platform, CMDB, scheduler, or alerting engine. Returns ``{ok, issue_count, findings}`` and
NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow incident / alert / monitoring / maintenance store).
  * No mutation — the layer never calls an incident / alert / maintenance / recovery / monitoring mutation
    (`open_incident`, `set_incident_status`, `raise_alert`, `acknowledge_alert`, `resolve_alert`,
    `create_maintenance_window`, `set_maintenance_status`, `create_service`, `set_service_status`,
    `create_finding`, `write_audit`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every registry key is unique; every configured entry names an authoritative owner; every panel names an
    authoritative owner + source + deep link; every derived value is labeled.
  * not_configured entries (backup, restore, disaster recovery, recovery testing, failover, vendor incidents)
    are reported honestly; no fabricated operational status.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("observability.catalog", "observability.health", "observability.incidents",
                        "observability.alerts", "security.incidents", "integration.service",
                        "integration.sync", "vendor_management", "automation_orchestration",
                        "business_continuity", "integration_hub")

# Mutating/execution entry points this layer must NEVER call (would duplicate an incident/monitoring engine).
_FORBIDDEN_CALLS = (
    "open_incident(", "set_incident_status(", "create_finding(", "set_finding_status(", "raise_alert(",
    "acknowledge_alert(", "resolve_alert(", "create_rule(", "create_suppression(",
    "create_maintenance_window(", "set_maintenance_status(", "create_service(", "set_service_status(",
    "add_dependency(", "capture_runtime_snapshot(", "write_audit(", "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_operational_resilience() -> dict:
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
            if re.search(r"write_audit(_event)?\s*\(", s):
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

        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        if "observability" not in composed:
            findings.append({"type": "not_reusing_observability_owner"})

        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership + honest not_configured.
        registries = (
            ("operational_service", registry.OPERATIONAL_SERVICE_REGISTRY),
            ("incident_category", registry.INCIDENT_CATEGORY_REGISTRY),
            ("continuity_capability", registry.CONTINUITY_CAPABILITY_REGISTRY),
            ("recovery_objective", registry.RECOVERY_OBJECTIVE_REGISTRY),
            ("operational_dependency", registry.OPERATIONAL_DEPENDENCY_REGISTRY),
        )
        for label, reg in registries:
            keys = [e.key for e in reg]
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})
            for e in reg:
                if not e.owner or not e.capabilities or not e.deep_links or not e.runtime_gate:
                    findings.append({"type": "entry_incomplete", "registry": label, "key": e.key})
                if e.config_status == registry.CONFIGURED and e.owner == registry.NOT_CONFIGURED:
                    findings.append({"type": "configured_entry_without_owner", "registry": label,
                                     "key": e.key})

        for d in registry.RESILIENCE_DASHBOARDS:
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
            if not p.owner or not p.source or not p.deep_link or not p.explainability or not p.permission:
                findings.append({"type": "panel_incomplete", "panel": p.key})
            if p.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_panel_lifecycle", "panel": p.key})
            # a status / posture panel derived from the layer must be labeled derived (no fabricated status).
            layer_derived = p.source.startswith("operational_resilience.")
            if layer_derived and ("status" in p.key or "posture" in p.key or "readiness" in p.key) \
                    and not p.derived:
                findings.append({"type": "unlabeled_derived_status", "panel": p.key})
        pk = [p.key for p in registry.PANEL_REGISTRY]
        if len(pk) != len(set(pk)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "panel"})
        dk = [d.key for d in registry.RESILIENCE_DASHBOARDS]
        if len(dk) != len(set(dk)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "dashboard"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
