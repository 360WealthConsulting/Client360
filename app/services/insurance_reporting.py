"""Insurance operational reporting (Release 0.10.0, Phase 2, non-regulated).

Pipeline counts only — cases and policies by status, and outstanding requirements
across the principal's scoped cases. Authorization-filtered before aggregation
(reuses the scoped list services). This is operational management reporting; it
contains NO compliance metrics (no suitability/replacement/1035/licensing/CE rates
or determinations) — those remain behind the AD-5 gate.
"""
from __future__ import annotations

from collections import Counter

from app.services import insurance as ins


def pipeline_report(principal):
    """Operational pipeline snapshot within the principal's record scope."""
    cases = ins.list_cases(principal)          # scope-filtered
    policies = ins.list_policies(principal)    # scope-filtered

    open_requirements = 0
    for case in cases:
        open_requirements += len(
            ins.list_requirements(principal, case_id=case["id"], open_only=True))

    return {
        "case_count": len(cases),
        "cases_by_status": dict(Counter(c["status"] for c in cases)),
        "policy_count": len(policies),
        "policies_by_status": dict(Counter(p["status"] for p in policies)),
        "open_requirements": open_requirements,
    }
