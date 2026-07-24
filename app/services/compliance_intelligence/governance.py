"""Compliance Intelligence governance (Phase D.47) — read-only validation that the supervisory layer stays a
COMPOSITION over the authoritative compliance/review/exception/audit/approval services and never becomes a
second compliance engine, approval engine, audit log, or workflow. Returns ``{ok, issue_count, findings}``
and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes the audit log
    — it only composes reads.
  * No module calls an approval/mutation entry point (submit_review / assign_reviewer / record_decision /
    write_audit_event / exception raise-resolve-acknowledge-escalate) — approvals stay with their owner.
  * The engine composes the AUTHORITATIVE sources (compliance.reviews, exception_engine, insurance_licensing,
    portfolio) — no second compliance/audit engine.
  * Every review type + exception type is fully declared in the registries; no duplicate ownership.
  * Supervisor-vs-advisor separation is enforced (``compliance.supervise`` gate present; the advisor task
    projection never returns supervisory items/exceptions).
  * Every gate is a governed runtime flag; no raw environment fallback.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "adapters/reviews.py", "adapters/exceptions.py", "adapters/licensing.py")

_AUTHORITATIVE_SOURCES = ("compliance.reviews", "exception_engine", "insurance_licensing", "portfolio")
# mutation / approval entry points that must NEVER be called from this read-only layer.
_FORBIDDEN_CALLS = ("submit_review(", "assign_reviewer(", "record_decision(", "write_audit_event(",
                    "raise_exception(", "resolve_exception(", "acknowledge_exception(",
                    "escalate_exception(", "set_status(")


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_compliance_intelligence() -> dict:
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
            for call in _FORBIDDEN_CALLS:
                if call in s:
                    findings.append({"type": "mutation_or_approval_call", "module": mod, "call": call})
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_read", "module": mod, "table": m})
            if re.search(r"Table\s*\(|define_\w+_tables\s*\(", s):
                findings.append({"type": "shadow_store_definition", "module": mod})
            if re.search(r"os\.getenv|os\.environ", s):
                findings.append({"type": "raw_env_fallback", "module": mod})

        # The engine composes the authoritative sources (no second compliance engine).
        composed = (_src("service.py") + _src("adapters/reviews.py") + _src("adapters/exceptions.py") +
                    _src("adapters/licensing.py"))
        if "compliance.reviews" not in composed and "compliance/reviews" not in composed:
            findings.append({"type": "not_reusing_compliance_reviews"})
        if not any(a in composed for a in _AUTHORITATIVE_SOURCES):
            findings.append({"type": "not_reusing_authoritative_sources"})

        # Supervisor-vs-advisor separation must be enforced.
        if "supervisor_authorized" not in _src("gate.py"):
            findings.append({"type": "supervisor_gate_missing"})
        svc = _src("service.py")
        if "supervisor_authorized" not in svc:
            findings.append({"type": "supervisor_gate_not_applied"})
        # The advisor task projection must only return the governed advisor recommendations (no supervisory
        # items/exceptions), so supervisory-only findings can never leak to an advisor.
        adv = svc.split("def advisor_compliance_tasks", 1)
        if len(adv) > 1:
            body = adv[1]
            if "SupervisoryItem" in body or "ComplianceException" in body or "review_items" in body:
                findings.append({"type": "advisor_projection_may_leak_supervisory_data"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in svc:
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for t in registry.SUPERVISORY_REGISTRY:
            if not t.owner or not t.governing_workflow or not t.deep_link or not t.runtime_gate:
                findings.append({"type": "review_type_incomplete", "type_key": t.key})
            if not t.required_evidence or not t.approval_authority or not t.retention_class:
                findings.append({"type": "review_type_missing_evidence_or_authority", "type_key": t.key})
            if t.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_review_lifecycle", "type_key": t.key})
        for t in registry.EXCEPTION_REGISTRY:
            if not t.owner or not t.governing_policy or not t.escalation:
                findings.append({"type": "exception_type_incomplete", "type_key": t.key})
            if t.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_exception_lifecycle", "type_key": t.key})
        skeys = [t.key for t in registry.SUPERVISORY_REGISTRY]
        xkeys = [t.key for t in registry.EXCEPTION_REGISTRY]
        if len(skeys) != len(set(skeys)) or len(xkeys) != len(set(xkeys)):
            findings.append({"type": "duplicate_registry_ownership"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
