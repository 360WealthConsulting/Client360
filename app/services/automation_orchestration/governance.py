"""Automation Orchestration governance (Phase D.51) — read-only validation that the automation-orchestration
layer stays a COMPOSITION over the authoritative operational services, and never becomes a second workflow
engine, scheduler, rules engine, orchestration engine, event bus, or automation platform. Returns
``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow automation/workflow/event store).
  * No duplicate workflow execution / scheduler / event routing — the layer never calls a workflow /
    automation / trigger / event mutation (`launch_workflow`, `transition_workflow`, `complete_step`,
    `request_approval`, `decide_approval`, `reassign_approval`, `process_event`, `execute_automation_action`,
    `fire(`, `execute_action(`, `enqueue_run`, `execute_run`, `run_job`, `run_worker_cycle`, `publish(`,
    `publish_safe(`, `evaluate_sla(`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every automation + trigger + action + panel + dashboard is fully declared; every panel names an
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

_AUTHORITATIVE_READS = ("workflow_automation", "workflow_orchestration", "automation", "events",
                        "scheduling", "communications")

# Mutating/execution entry points this layer must NEVER call (would duplicate a workflow/automation engine).
_FORBIDDEN_CALLS = (
    "launch_workflow(", "transition_workflow(", "complete_step(", "request_approval(", "decide_approval(",
    "reassign_approval(", "process_event(", "execute_automation_action(", "evaluate_sla(", ".fire(",
    "execute_action(", "configure_trigger(", "enqueue_run(", "execute_run(", "run_job(",
    "run_worker_cycle(", "execute_dispatch(", "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_automation_orchestration() -> dict:
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

        # The composition must reference the authoritative reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative workflow engine must be composed (no second workflow engine).
        if "workflow_automation" not in composed and "workflow_orchestration" not in composed:
            findings.append({"type": "not_reusing_workflow_engine"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for a in registry.AUTOMATION_REGISTRY:
            if not a.owner or not a.workflow_owner or not a.trigger_source or not a.execution_owner \
                    or not a.scheduling_owner or not a.notification_owner or not a.deep_links:
                findings.append({"type": "automation_incomplete", "automation": a.key})
            if not a.runtime_gate:
                findings.append({"type": "automation_missing_gate", "automation": a.key})
        for t in registry.TRIGGER_REGISTRY:
            if not t.owner or not t.source or not t.execution_owner or not t.runtime_gate:
                findings.append({"type": "trigger_incomplete", "trigger": t.key})
        for ac in registry.ACTION_REGISTRY:
            if not ac.authoritative_owner or not ac.execution_service or not ac.permissions \
                    or not ac.runtime_gate:
                findings.append({"type": "action_incomplete", "action": ac.key})
        for d in registry.ORCHESTRATION_DASHBOARDS:
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
        for label, keys in (("automation", [a.key for a in registry.AUTOMATION_REGISTRY]),
                            ("trigger", [t.key for t in registry.TRIGGER_REGISTRY]),
                            ("action", [a.key for a in registry.ACTION_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.ORCHESTRATION_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
