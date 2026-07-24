"""Client Portal governance (Phase D.43) — read-only validation that the portal remains a governed
external composition + delegated-action surface and never becomes a second system. Returns a structured
report and NEVER raises into normal use.

Invariants enforced:
  * No second CRM/identity/document/messaging/scheduler/task/workflow/policy engine or event bus — portal
    modules must not publish to the outbox, evaluate policy, or run domain logic; mutations delegate.
  * Every externally-served field is declared in the visibility registry; no ``internal_only`` /
    ``prohibited`` field (advisor notes, assignments, compliance reasoning, AI briefs, work queue, audit,
    revenue, net worth) is ever externally visible.
  * All external portal capabilities are gated with production-safe defaults, and external production
    access is blocked until the compliance sign-off gate is set.
  * The financial surface masks account numbers and the diagnostics surface exposes no client data.
"""
from __future__ import annotations

import pathlib
import re

from app.portal import gate, visibility

# Portal modules that must not publish events, evaluate policy, or seed a parallel identity/RBAC stack.
# governance.py itself is excluded from the source scan: it holds the detection patterns as string
# literals, so scanning it would self-match (it enforces the invariant by *checking*, not by *doing*).
_MODULES = ("visibility.py", "consent.py", "financial.py", "identity_local.py", "diagnostics.py",
            "gate.py", "stats.py")

# The internal-only / prohibited registry keys that must never be externally visible.
_FORBIDDEN_KEYS = ("internal.advisor_notes", "internal.assignments", "internal.advisor_work",
                   "internal.work_queue", "internal.compliance_reasoning", "internal.suitability_findings",
                   "internal.audit_history", "internal.policy_explanations", "internal.ai_assist_brief",
                   "internal.opportunity_revenue", "internal.relationship_graph", "internal.net_worth")


def _src(name):
    try:
        return (pathlib.Path(__file__).with_name(name)).read_text()
    except OSError:
        return ""


def validate_portal() -> dict:
    findings = []
    try:
        # 1. No portal module publishes to the outbox / event bus (single bus is the internal outbox).
        for mod in _MODULES:
            s = _src(mod)
            if re.search(r"publish_safe\s*\(|publisher\.publish|publish_event\s*\(", s):
                findings.append({"type": "outbox_publication", "module": mod})
            # No second policy/runtime decision engine embedded in the portal.
            if re.search(r"class\s+\w*Policy\w*Engine|def\s+evaluate_policy\b", s):
                findings.append({"type": "shadow_policy_engine", "module": mod})
            # No raw environment fallback — gating is via the governed runtime.
            if re.search(r"os\.getenv|os\.environ", s):
                findings.append({"type": "raw_env_fallback", "module": mod})
            # No new RBAC capability seeded — the portal is grant-based.
            if re.search(r"CAPABILITY|capabilities\.insert|seed_capabilit", s):
                findings.append({"type": "parallel_rbac_capability", "module": mod})

        # 2. Every externally-served field is declared in the registry and none is forbidden.
        for f in visibility.external_fields():
            if f.external_visibility in visibility.FORBIDDEN_STATES:
                findings.append({"type": "forbidden_field_externally_visible", "field": f.key})
        for key in _FORBIDDEN_KEYS:
            fld = visibility.field(key)
            if fld is None:
                findings.append({"type": "forbidden_field_not_declared", "field": key})
            elif fld.external_visibility not in visibility.FORBIDDEN_STATES:
                findings.append({"type": "forbidden_field_misclassified", "field": key})
            elif visibility.is_externally_visible(key):
                findings.append({"type": "forbidden_field_externally_visible", "field": key})

        # 3. Account numbers are masked wherever a financial field is served.
        acct = visibility.field("financial.account_number")
        if acct is not None and acct.masking_rule != visibility.MASK_ACCOUNT:
            findings.append({"type": "account_number_not_masked"})
        # The masking helper must never emit a full number.
        if "1234567890" in visibility.mask_account_number("1234567890"):
            findings.append({"type": "account_masking_ineffective"})

        # 4. All portal gates default OFF and production access is blocked until compliance sign-off.
        for name, default in gate.GATES.items():
            if name == "portal.mfa_required":
                if default is not True:
                    findings.append({"type": "mfa_not_required_by_default"})
            elif default is not False:
                findings.append({"type": "gate_not_off_by_default", "gate": name})
        # With no runtime override, the portal must not be production-ready (compliance gate blocks).
        if gate.production_ready():
            findings.append({"type": "production_ready_without_signoff"})

        # 5. Financial surface fails closed on the feature gate + never mutates portfolio.
        fin = _src("financial.py")
        if "portal.financial_summary_enabled" not in fin:
            findings.append({"type": "financial_not_gated"})
        for verb in (".insert()", ".update(", ".delete()", "accounts.insert", "accounts.update"):
            if verb in fin:
                findings.append({"type": "financial_mutates_portfolio", "op": verb})

        # 6. Consent writes reference the audit ledger; diagnostics exposes no ids/tokens.
        if "write_audit_event" not in _src("consent.py"):
            findings.append({"type": "consent_not_audited"})
        diag = _src("diagnostics.py")
        if re.search(r"account_id|person_id|token|email", diag):
            findings.append({"type": "diagnostics_may_leak_identifiers"})

        # 7. Local identity provider only registers when not production-signed-off.
        idl = _src("identity_local.py")
        if "production_signed_off" not in idl:
            findings.append({"type": "local_provider_not_production_guarded"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
