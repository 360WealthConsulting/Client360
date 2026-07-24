"""Enterprise Risk Management governance (Phase D.58) — read-only validation that the risk-management layer
stays a COMPOSITION over the authoritative risk / control / assurance owners, and never becomes a second GRC
platform, risk register, compliance engine, exception system, audit platform, incident-management system,
control-testing application, policy engine, or approval engine. Returns ``{ok, issue_count, findings}`` and
NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow risk / control / finding / incident / evidence store).
  * No mutation — the layer never calls a risk / finding / exception / incident / review / approval / policy
    mutation (`raise_exception`, `acknowledge`, `escalate`, `resolve`, `assign`, `submit_review`,
    `record_decision`, `create_incident`, `create_finding`, `approve_exception`, `write_audit`,
    `write_audit_event`, …).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every risk domain + control family + assurance source + panel + dashboard is fully declared; every
    configured entry names an authoritative owner; every panel names an authoritative owner + source + deep
    link; every derived panel is labeled; no duplicate ownership.
  * No fabricated enterprise composite risk score — the derived posture panel is labeled derived and never
    claims certification.
  * not_configured entries (control testing, model/AI risk, privacy risk, financial authorization, change
    management) are reported honestly.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("compliance_intelligence", "exception_engine", "exception_reporting",
                        "security.incidents", "security_operations", "data_governance", "integration.sync",
                        "integration.service", "integration_hub", "business_continuity", "vendor_management",
                        "financial_operations", "document_intelligence", "automation_orchestration",
                        "insurance_licensing", "insurance_reporting")

# Mutating/execution entry points this layer must NEVER call (would duplicate a risk/GRC/incident engine).
_FORBIDDEN_CALLS = (
    "raise_exception(", "acknowledge(", "begin_work(", "place_waiting(", "escalate(", "resolve(",
    "assign(", "reopen(", "submit_review(", "assign_reviewer(", "record_decision(", "create_incident(",
    "set_incident_status(", "create_finding(", "approve_exception(", "record_expected(", "record_received(",
    "write_audit(", "write_audit_event(", "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_enterprise_risk() -> dict:
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

        # The composition must reference the authoritative risk reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative exception/compliance owner must be composed (no second exception/GRC engine).
        if "compliance_intelligence" not in composed and "exception" not in composed:
            findings.append({"type": "not_reusing_exception_owner"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership + honest not_configured.
        for r in registry.ENTERPRISE_RISK_REGISTRY:
            if not r.risk_category or not r.signal_owners or not r.capabilities or not r.deep_links \
                    or not r.runtime_gate:
                findings.append({"type": "risk_domain_incomplete", "domain": r.key})
            if r.config_status == registry.CONFIGURED and r.authoritative_owner == registry.NOT_CONFIGURED:
                findings.append({"type": "configured_domain_without_owner", "domain": r.key})
        for c in registry.CONTROL_REGISTRY:
            if not c.control_family or not c.control_objective or not c.capabilities or not c.deep_links \
                    or not c.runtime_gate:
                findings.append({"type": "control_incomplete", "control": c.key})
            if c.config_status == registry.CONFIGURED and c.authoritative_owner == registry.NOT_CONFIGURED:
                findings.append({"type": "configured_control_without_owner", "control": c.key})
            # control TESTING is not owned anywhere — every test_owner MUST be not_configured (honesty).
            if c.test_owner != registry.NOT_CONFIGURED:
                findings.append({"type": "fabricated_control_test_owner", "control": c.key})
        for a in registry.ASSURANCE_REGISTRY:
            if not a.assurance_owner or not a.evidence_source or not a.scope or not a.frequency \
                    or not a.deep_link or not a.runtime_gate:
                findings.append({"type": "assurance_incomplete", "assurance": a.key})
            if a.config_status == registry.CONFIGURED and a.assurance_owner == registry.NOT_CONFIGURED:
                findings.append({"type": "configured_assurance_without_owner", "assurance": a.key})
        for d in registry.RISK_DASHBOARDS:
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
            # no fabricated composite risk score — a *_score / posture panel must be labeled derived.
            if ("posture" in p.key or p.key.endswith("_score")) and not p.derived:
                findings.append({"type": "unlabeled_derived_score", "panel": p.key})
        for label, keys in (("risk", [r.key for r in registry.ENTERPRISE_RISK_REGISTRY]),
                            ("control", [c.key for c in registry.CONTROL_REGISTRY]),
                            ("assurance", [a.key for a in registry.ASSURANCE_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.RISK_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
