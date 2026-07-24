"""Practice Management governance (Phase D.49) — read-only validation that the practice-management layer
stays a COMPOSITION over the authoritative operational services, and never becomes a second workflow engine,
scheduler, staffing/assignment engine, work queue, capacity/planning engine, metrics registry, or
persistence store. Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no planning warehouse, no shadow schedule/roster/assignment store).
  * No second workflow engine (composes ``workflow_automation.workflow_metrics`` — never launches/advances a
    workflow), no second scheduler (composes ``scheduling`` reads — never books), no duplicate assignment
    (composes work-queue/assignment READS — never calls ``assign_work``/``reassign``), no second capacity
    engine (composes ``operations.capacity`` — the authoritative owner).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``; utilization numbers come from
    the authoritative owners.
  * Every capacity model + resource + panel + dashboard is fully declared; every panel names an
    authoritative owner + source + deep link; no duplicate ownership.
  * Every panel is explainable (explanation + source + deep link) — enforced by the model + compute layer.
  * No raw environment gating; no mutation.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("operations.capacity", "work_queue", "workflow_automation", "tax_domain",
                        "opportunity", "analytics")

# Mutating/authoritative-owner entry points this layer must NEVER call (would duplicate an engine).
_FORBIDDEN_CALLS = (
    "assign_work(", "reassign_approval(", "reassign_work(", "launch_workflow(", "advance_workflow(",
    "decide_approval(", "request_approval(", "create_capacity_plan(", "update_capacity_plan(",
    "book_meeting(", "schedule_meeting(", "create_meeting(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_practice_management() -> dict:
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

        # The composition must reference the authoritative reads (capacity/work-queue/workflow/tax/etc.).
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative capacity owner must be composed (no second capacity engine).
        if "operations.capacity" not in composed:
            findings.append({"type": "not_reusing_capacity_owner"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for cm in registry.CAPACITY_REGISTRY:
            if not cm.owner or not cm.governing_workflow or not cm.workload_source or \
                    not cm.utilization_method or not cm.planning_horizon or not cm.deep_links:
                findings.append({"type": "capacity_model_incomplete", "model": cm.key})
            if not cm.runtime_gate:
                findings.append({"type": "capacity_model_missing_gate", "model": cm.key})
        for rm in registry.RESOURCE_REGISTRY:
            if not rm.owner or not rm.capabilities or not rm.workload_source or not rm.assignment_source \
                    or not rm.scheduling_source or not rm.utilization_source or not rm.availability_source:
                findings.append({"type": "resource_incomplete", "resource": rm.key})
        for d in registry.PRACTICE_DASHBOARDS:
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
        for label, keys in (("capacity", [c.key for c in registry.CAPACITY_REGISTRY]),
                            ("resource", [r.key for r in registry.RESOURCE_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.PRACTICE_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
