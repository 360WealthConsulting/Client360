"""Enterprise Change Management governance (Phase D.63) — read-only validation that the change / release /
configuration layer stays a COMPOSITION over the authoritative change-evidence owners, and never becomes a
second ITSM / change-management / deployment / CI-CD / Git / CMDB / feature-flag / release-approval / incident /
maintenance-scheduling platform. Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow change / release / deployment / configuration store).
  * No mutation — the layer never creates a branch, merges a PR, pushes a commit, tags a release, deploys,
    runs a migration, changes a flag, approves a change, schedules maintenance, acknowledges an incident, or
    executes rollback (`set_flag`, `upgrade`, `downgrade`, `merge`, `deploy`, `rollback`, `approve`,
    `schedule_maintenance`, `acknowledge`, `create_environment_profile`, `write_audit`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every registry key is unique; every configured entry names an authoritative owner; every panel names an
    authoritative owner + source + deep link; every derived value is labeled.
  * not_configured entries (live git / PR / CI status, deployment execution / status, rollback readiness,
    production verification, change calendar, post-change review) are reported honestly; no fabricated change
    request, deployment status, release approval, rollback readiness, configuration state, production
    verification, environment health, or change success.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("observability", "runtime", "policy", "continuous_integration",
                        "architecture_manifest", "security", "compliance_intelligence", "integration_hub")

# Mutating/execution entry points this layer must NEVER call (would duplicate a change / deploy / CI engine).
_FORBIDDEN_CALLS = (
    "set_flag(", "upgrade(", "downgrade(", "merge(", "deploy(", "rollback(", "approve(",
    "schedule_maintenance(", "acknowledge_incident(", "create_environment_profile(",
    "create_deployment_reference(", "create_connector(", "run_sync(", "create_document(",
    "write_audit(", "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_change_management() -> dict:
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
        if "_expected_head" not in composed or "app.routes" not in composed:
            findings.append({"type": "not_reusing_self_verification_owner"})

        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership + honest not_configured.
        registries = (
            ("change_domain", registry.CHANGE_DOMAIN_REGISTRY),
            ("release", registry.RELEASE_REGISTRY),
            ("configuration", registry.CONFIGURATION_REGISTRY),
            ("change_evidence", registry.CHANGE_EVIDENCE_REGISTRY),
        )
        for label, reg in registries:
            keys = [e.key for e in reg]
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})
            for e in reg:
                links = getattr(e, "deep_links", None) or getattr(e, "deep_link", None)
                if not e.owner or not e.capabilities or not links or not e.runtime_gate:
                    findings.append({"type": "entry_incomplete", "registry": label, "key": e.key})
                if e.config_status == registry.CONFIGURED and e.owner == registry.NOT_CONFIGURED:
                    findings.append({"type": "configured_entry_without_owner", "registry": label,
                                     "key": e.key})
                if e.config_status not in (registry.CONFIGURED, registry.NOT_CONFIGURED):
                    findings.append({"type": "invalid_config_status", "registry": label, "key": e.key})

        for d in registry.CHANGE_DASHBOARDS:
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
            # a value computed by the layer (change_management.compose) must be labeled derived.
            if p.source.startswith("change_management.compose") and not p.derived:
                findings.append({"type": "unlabeled_derived_summary", "panel": p.key})
        pk = [p.key for p in registry.PANEL_REGISTRY]
        if len(pk) != len(set(pk)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "panel"})
        dk = [d.key for d in registry.CHANGE_DASHBOARDS]
        if len(dk) != len(set(dk)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "dashboard"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
