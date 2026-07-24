"""Security Operations governance (Phase D.54) — read-only validation that the security-operations layer
stays a COMPOSITION over the authoritative security owners, and never becomes a second IAM platform, identity
provider, RBAC engine, authentication system, authorization engine, MFA provider, audit-logging platform, or
SIEM. Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow identity / user / role / session / audit store).
  * No mutation / no auth action — the layer never calls an authentication / identity / session / RBAC /
    audit mutation (`authenticate_claims`, `create_session`, `revoke_session`, `create_user`, `invite_user`,
    `set_user_status`, `assign_role`, `compose_role`, `write_audit_event`, `bootstrap_administrator`,
    `resolve_principal`, `rotate`, `reset_password`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every identity class + security domain + panel + dashboard is fully declared; every panel names an
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

_AUTHORITATIVE_READS = ("security.service", "security.providers", "security.policies", "security.incidents",
                        "identity", "audit_export", "analytics")

# Mutating/auth entry points this layer must NEVER call (would duplicate an IAM/RBAC/audit engine).
_FORBIDDEN_CALLS = (
    "authenticate_claims(", "create_session(", "revoke_session(", "invite_user(", "set_user_status(",
    "assign_role(", "compose_role(", "add_team_membership(", "assign_record(", "write_audit_event(",
    "audit_denied(", "bootstrap_administrator(", "resolve_principal(", "reset_password(", "rotate_secret(",
    "register_policy(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_security_operations() -> dict:
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

        # The composition must reference the authoritative security reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative security domain + identity owner must be composed (no second IAM).
        if "security." not in composed or "identity" not in composed:
            findings.append({"type": "not_reusing_security_owner"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for i in registry.IDENTITY_REGISTRY:
            if not i.authoritative_owner or not i.authentication_owner or not i.authorization_owner \
                    or not i.deep_links:
                findings.append({"type": "identity_incomplete", "identity": i.key})
            if not i.runtime_gate:
                findings.append({"type": "identity_missing_gate", "identity": i.key})
        for s in registry.SECURITY_REGISTRY:
            if not s.category or not s.authoritative_owner or not s.provider_owner or not s.monitoring_owner \
                    or not s.runtime_gate or not s.deep_links:
                findings.append({"type": "security_domain_incomplete", "security_domain": s.key})
        for d in registry.SECURITY_DASHBOARDS:
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
        for label, keys in (("identity", [i.key for i in registry.IDENTITY_REGISTRY]),
                            ("security", [s.key for s in registry.SECURITY_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.SECURITY_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
