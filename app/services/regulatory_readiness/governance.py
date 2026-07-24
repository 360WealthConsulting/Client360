"""Enterprise Regulatory Examination Readiness governance (Phase D.59) — read-only validation that the
readiness layer stays a COMPOSITION over the authoritative regulatory / evidence / certification owners, and
never becomes a second compliance platform, examination-management system, audit platform, document
repository, records-management system, regulatory filing system, certification engine, evidence vault,
supervisory approval engine, or policy-management system. Returns ``{ok, issue_count, findings}`` and NEVER
raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow evidence / filing / examination / certification store).
  * No mutation — the layer never calls an evidence / review / approval / filing / retention / authority
    mutation (`record_decision`, `submit_review`, `assign_reviewer`, `activate`, `revoke`, `supersede`,
    `record_evidence`, `record_workflow_evidence`, `record_license`, `create_retention_assignment`,
    `execute_deletion`, `write_audit`, …).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every obligation / evidence / examination-request / certification / panel / dashboard key is unique; every
    configured item names an authoritative owner; every evidence item names a storage/evidence owner.
  * Every derived value is labeled; every blocked certification states why; reviewer authority is never
    inferred and business approval is never treated as regulatory certification (no certification is emitted
    with a fabricated `approved` status or a fabricated reviewer name / date).
  * not_configured filing / examination / export / obligation areas are reported honestly; no fabricated
    acknowledgement, readiness score, or absence-of-findings certification.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("compliance_intelligence", "compliance_rule_catalog", "compliance_reviews",
                        "exception_engine", "document_intelligence", "data_governance", "security_operations",
                        "business_continuity", "vendor_management", "financial_operations",
                        "insurance_licensing", "insurance_reporting", "observability.audit",
                        "continuous_integration", "reviewer_authority")

# Mutating/execution entry points this layer must NEVER call (would create a second compliance/evidence engine).
_FORBIDDEN_CALLS = (
    "record_decision(", "submit_review(", "assign_reviewer(", "activate(", "revoke(", "supersede(",
    "create_draft(", "record_evidence(", "record_workflow_evidence(", "record_license(", "update_license(",
    "record_ce(", "create_retention_assignment(", "review_deletion_request(", "execute_deletion(",
    "place_legal_hold(", "resolve(", "escalate(", "write_audit(", "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_regulatory_readiness() -> dict:
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
        if "compliance_intelligence" not in composed:
            findings.append({"type": "not_reusing_compliance_owner"})

        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Obligation registry: uniqueness + configured-owner + honest not_configured.
        ob_keys = [o.key for o in registry.REGULATORY_OBLIGATION_REGISTRY]
        if len(ob_keys) != len(set(ob_keys)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "obligation"})
        for o in registry.REGULATORY_OBLIGATION_REGISTRY:
            if not o.reg_domain or not o.capabilities or not o.deep_links or not o.runtime_gate:
                findings.append({"type": "obligation_incomplete", "obligation": o.key})
            if o.config_status == registry.CONFIGURED and o.authoritative_owner == registry.NOT_CONFIGURED:
                findings.append({"type": "configured_obligation_without_owner", "obligation": o.key})

        # Evidence registry: uniqueness + storage/evidence owner for configured entries.
        ev_keys = [e.key for e in registry.EVIDENCE_REGISTRY]
        if len(ev_keys) != len(set(ev_keys)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "evidence"})
        for e in registry.EVIDENCE_REGISTRY:
            if not e.evidence_class or not e.freshness or not e.capabilities or not e.deep_link:
                findings.append({"type": "evidence_incomplete", "evidence": e.key})
            if e.config_status == registry.CONFIGURED and (e.authoritative_owner == registry.NOT_CONFIGURED
                                                           or e.storage_owner == registry.NOT_CONFIGURED):
                findings.append({"type": "configured_evidence_without_owner", "evidence": e.key})

        # Examination-request registry: uniqueness.
        req_keys = [r.key for r in registry.EXAMINATION_REQUEST_REGISTRY]
        if len(req_keys) != len(set(req_keys)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "examination_request"})
        for r in registry.EXAMINATION_REQUEST_REGISTRY:
            if not r.category or not r.required_evidence or not r.capabilities or not r.deep_links:
                findings.append({"type": "examination_request_incomplete", "request": r.key})

        # Certification registry: uniqueness + reviewer authority never inferred + business approval not cert.
        cert_keys = [c.key for c in registry.CERTIFICATION_REGISTRY]
        if len(cert_keys) != len(set(cert_keys)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "certification"})
        for c in registry.CERTIFICATION_REGISTRY:
            if not c.scope or not c.accountable_reviewer_role or not c.evidence_owner or not c.deep_link:
                findings.append({"type": "certification_incomplete", "certification": c.key})
            # a fabricated "approved" status or a named reviewer without confirmed authority is forbidden.
            if c.status not in (registry.BLOCKED, registry.REVIEWER_NOT_CONFIRMED, registry.NOT_CONFIGURED):
                findings.append({"type": "fabricated_certification_status", "certification": c.key,
                                 "status": c.status})
            if c.named_reviewer != registry.REVIEWER_NOT_CONFIRMED and c.named_reviewer not in ("", None):
                # a named reviewer may only appear with an authoritatively confirmed authority record.
                findings.append({"type": "inferred_reviewer_authority", "certification": c.key})
            if c.review_date != registry.NOT_CONFIGURED:
                findings.append({"type": "fabricated_review_date", "certification": c.key})
            if c.status in (registry.BLOCKED, registry.REVIEWER_NOT_CONFIRMED) and not c.blocked_reason:
                findings.append({"type": "blocked_certification_without_reason", "certification": c.key})

        # Dashboards + panels.
        for d in registry.READINESS_DASHBOARDS:
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
            # a readiness / coverage / score panel DERIVED FROM THE LAYER'S OWN registries/compose (not a
            # direct authoritative read) must be labeled derived — never a fabricated readiness score.
            layer_derived = p.source.startswith("regulatory_readiness.")
            if layer_derived and ("readiness" in p.key or "coverage" in p.key or p.key.endswith("_score")) \
                    and not p.derived:
                findings.append({"type": "unlabeled_derived_readiness", "panel": p.key})
        pk = [p.key for p in registry.PANEL_REGISTRY]
        if len(pk) != len(set(pk)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "panel"})
        dk = [d.key for d in registry.READINESS_DASHBOARDS]
        if len(dk) != len(set(dk)):
            findings.append({"type": "duplicate_registry_ownership", "registry": "dashboard"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
