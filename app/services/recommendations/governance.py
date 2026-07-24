"""Operational Intelligence governance (Phase D.46) — read-only validation that the recommendation layer
stays a COMPOSITION over the authoritative recommendation sources and never becomes a second recommendation,
workflow, opportunity, CRM, analytics, reporting, or AI engine. Returns ``{ok, issue_count, findings}`` and
NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads.
  * No ML / predictive / model dependency (deterministic rule-based only); no probabilistic scoring.
  * The engine composes the AUTHORITATIVE recommendation sources (advisor_intelligence + the domain
    observation sets + work_queue + engagement) — it does not re-implement a recommendation/workflow/
    opportunity engine.
  * Every recommendation type is fully declared in the registry; no duplicate ownership.
  * Every emitted recommendation is explainable (why + evidence + deep link) — enforced by the model +
    engine; governance asserts the enforcement is present.
  * Every gate is a governed runtime flag; no raw environment fallback.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "adapters/signals.py", "adapters/observations.py", "adapters/composed.py")

# The authoritative recommendation sources the engine must compose (proves no second engine).
_AUTHORITATIVE_SOURCES = ("advisor_intelligence", "opportunity.intelligence", "bizdev.intelligence",
                          "analytics.intelligence", "work_queue", "engagement")
_ML_DEPS = ("sklearn", "tensorflow", "torch", "xgboost", "numpy.random", "predict_proba")


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_recommendations() -> dict:
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
            for dep in _ML_DEPS:
                if dep in s:
                    findings.append({"type": "ml_dependency", "module": mod, "dep": dep})

        # The engine composes the authoritative recommendation sources (no second engine).
        composed = (_src("service.py") + _src("adapters/signals.py") + _src("adapters/observations.py") +
                    _src("adapters/composed.py"))
        if "advisor_intelligence" not in composed:
            findings.append({"type": "not_reusing_advisor_intelligence"})
        if not any(a in composed for a in _AUTHORITATIVE_SOURCES):
            findings.append({"type": "not_reusing_authoritative_sources"})

        # Explainability enforcement must be present (model.is_explainable + engine drop).
        if "is_explainable" not in _src("model.py"):
            findings.append({"type": "explainability_not_enforced_in_model"})
        if "is_explainable" not in _src("service.py"):
            findings.append({"type": "explainability_not_enforced_in_engine"})

        # Registry completeness + single ownership.
        for t in registry.REGISTRY:
            if not t.owner_service or not t.source_services or not t.deep_link_target:
                findings.append({"type": "type_incomplete", "type_key": t.key})
            if not t.explanation_template or not t.evidence_kind:
                findings.append({"type": "type_without_explanation_or_evidence", "type_key": t.key})
            if not t.workflow_owner:
                findings.append({"type": "type_without_workflow_owner", "type_key": t.key})
            if t.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_lifecycle", "type_key": t.key})
        keys = [t.key for t in registry.REGISTRY]
        if len(keys) != len(set(keys)):
            findings.append({"type": "duplicate_recommendation_ownership"})

        # No unsupported confidence values (deterministic rule-based only — signals set 1.0 / source-supplied;
        # the model + adapters never compute a probabilistic score).
        if re.search(r"random|predict_proba|probability\s*\*", _src("service.py") + _src("adapters/signals.py")):
            findings.append({"type": "non_deterministic_confidence"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
