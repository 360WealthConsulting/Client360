"""Advisor AI Assist governance (Phase D.42) — read-only validation that the assistant stays a grounded,
read-only, human-reviewed surface with no mutation, no outbox, no shadow logic. Returns a structured
report and NEVER raises into normal assistant use.
"""
from __future__ import annotations

import pathlib
import re

from .contracts import REQUIRED_OUTPUT_FIELDS
from .prompts import PROMPTS, REQUIRED_CONSTRAINTS
from .refusal import REGULATED
from .registry import ASSISTANTS

# Every module in the package must be free of mutation / outbox / audit / rm_ / secrets.
_MODULES = ("assistant.py", "context.py", "provider.py", "diagnostics.py", "grounding.py",
            "registry.py", "contracts.py", "common.py")
# The scope-guarded D.38–D.41 summaries the context service must consume (no raw domain fan-out).
_SUMMARY_IMPORTS = ("workspace.summaries", "work_queue.summary", "client360", "activity_timeline")


def _src(name):
    try:
        return (pathlib.Path(__file__).with_name(name)).read_text()
    except OSError:
        return ""


def validate_ai_assist(principal=None) -> dict:
    findings = []
    try:
        # every capability is registered with input + output contracts + a versioned prompt.
        for key, a in ASSISTANTS.items():
            if not a.input_contract or not a.output_contract:
                findings.append({"type": "capability_without_contract", "capability": key})
            if not a.prompt_version:
                findings.append({"type": "capability_without_prompt_version", "capability": key})
            if a.lifecycle not in ("active", "experimental", "deprecated", "retired"):
                findings.append({"type": "invalid_lifecycle", "capability": key})
            if key not in PROMPTS:
                findings.append({"type": "capability_without_prompt", "capability": key})

        # every prompt contains the read-only + grounding constraints.
        for key, p in PROMPTS.items():
            tpl = (p.get("template") or "").lower()
            for c in REQUIRED_CONSTRAINTS:
                if c.lower() not in tpl:
                    findings.append({"type": "prompt_missing_constraint", "capability": key, "constraint": c})

        # the response contract requires citations + limitations + human review.
        for f in ("citations", "limitations", "human_review"):
            if f not in REQUIRED_OUTPUT_FIELDS:
                findings.append({"type": "output_contract_missing_field", "field": f})

        # no mutation / outbox / audit-write / rm_ / obvious secret in any module (match CALLS, not doc
        # mentions — the invariant is enforced by what the code *does*, not what a docstring says).
        ctx_src = _src("context.py")
        for mod in _MODULES:
            s = _src(mod)
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_table_read", "module": mod, "table": m})
            for verb in ("_table.insert(", "_table.delete(", ".insert().", "table.update(",
                         "sa.insert(", "sa.update(", "sa.delete("):
                if verb in s:
                    findings.append({"type": "database_write", "module": mod, "op": verb})
            if re.search(r"publish_safe\s*\(|publish_event\s*\(|publisher\.publish", s):
                findings.append({"type": "outbox_publication", "module": mod})
            if re.search(r"write_audit_event\s*\(", s):
                findings.append({"type": "audit_write_in_assist", "module": mod})
            if re.search(r"(api_key|secret|password|token)\s*=\s*[\"']", s):
                findings.append({"type": "possible_secret_in_source", "module": mod})

        # context service consumes the D.38–D.41 summaries (no direct domain-table fan-out where a summary exists).
        if not any(sm in ctx_src for sm in _SUMMARY_IMPORTS):
            findings.append({"type": "context_not_reusing_summaries"})

        # provider configuration is governed (runtime feature gate present; no raw env fallback).
        if "feature_enabled" not in _src("assistant.py"):
            findings.append({"type": "provider_gate_not_governed"})
        if re.search(r"os\.getenv|os\.environ", _src("provider.py") + _src("assistant.py")):
            findings.append({"type": "raw_env_fallback"})

        # regulated categories are refused/constrained.
        for needed in ("trade_recommendation", "tax_conclusion", "compliance_approval",
                       "suitability_determination", "autonomous_action"):
            if needed not in REGULATED:
                findings.append({"type": "regulated_category_not_refused", "category": needed})

        # human-review labelling + no autonomous action endpoint (the routes are read-only; POST/query is a read).
        if "HUMAN_REVIEW_LABEL" not in _src("contracts.py"):
            findings.append({"type": "human_review_label_missing"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
