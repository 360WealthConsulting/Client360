"""Financial Operations governance (Phase D.57) — read-only validation that the financial-operations layer
stays a COMPOSITION over the authoritative financial owners, and never becomes a second accounting platform,
ERP, billing engine, commission engine, payroll system, bookkeeping platform, general ledger, or budgeting
application. Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow accounting / revenue / commission / payroll store).
  * No mutation / no accounting change — the layer never calls a commission / accounting / billing / payroll
    mutation (`record_expected`, `record_received`, `record_adjustment`, `write_off`, `generate_expected`,
    `import_statement`, `reconcile_line`, `reconcile_statement`, `create_invoice`, `post_journal_entry`,
    `run_payroll`, `pay_commission`, `process_payment`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every financial category + revenue type + panel + dashboard is fully declared; every panel names an
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

_AUTHORITATIVE_READS = ("insurance_reporting", "insurance_commissions", "portfolio", "analytics.metrics",
                        "analytics.trends", "analytics.sources", "executive_intelligence",
                        "practice_management", "vendor_management", "tax_domain")

# Mutating/execution entry points this layer must NEVER call (would duplicate an accounting/billing engine).
_FORBIDDEN_CALLS = (
    "record_expected(", "record_received(", "record_adjustment(", "write_off(", "generate_expected(",
    "import_statement(", "reconcile_line(", "reconcile_statement(", "create_invoice(", "post_journal_entry(",
    "post_journal(", "run_payroll(", "pay_commission(", "process_payment(", "calculate_tax(",
    "publish_safe(", "publish_event(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_financial_operations() -> dict:
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

        # The composition must reference the authoritative financial reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative commission ledger must be composed (no second commission engine).
        if "commission_report" not in composed:
            findings.append({"type": "not_reusing_commission_owner"})
        # The single Analytics Registry revenue metrics must be composed (no second revenue engine).
        if "analytics.metrics" not in composed:
            findings.append({"type": "not_reusing_revenue_owner"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for f in registry.FINANCIAL_REGISTRY:
            if not f.authoritative_owner or not f.reporting_owner or not f.calculation_owner \
                    or not f.deep_links:
                findings.append({"type": "financial_incomplete", "category": f.key})
            if not f.runtime_gate:
                findings.append({"type": "financial_missing_gate", "category": f.key})
        for r in registry.REVENUE_REGISTRY:
            if not r.category or not r.authoritative_owner or not r.reporting_owner \
                    or not r.recognition_owner or not r.runtime_gate:
                findings.append({"type": "revenue_incomplete", "revenue": r.key})
        for d in registry.FINANCIAL_DASHBOARDS:
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
        for label, keys in (("financial", [f.key for f in registry.FINANCIAL_REGISTRY]),
                            ("revenue", [r.key for r in registry.REVENUE_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.FINANCIAL_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
