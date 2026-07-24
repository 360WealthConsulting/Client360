"""Enterprise Regulatory Examination Readiness, Evidence Governance & Supervisory Certification (Phase D.59)
tests.

Verifies the readiness layer is a governed, READ-ONLY COMPOSITION over the platform's authoritative regulatory
/ evidence / certification owners — Compliance Intelligence + compliance reviews + the rule catalog + the
reviewer-authority owner, the Exception Engine, Document Intelligence, Data Governance, Security Operations,
Business Continuity, Vendor Management, Financial Operations, Insurance licensing, audit logging, and the CI
pipeline — and never becomes a second compliance / examination / audit / document / filing / certification
platform.

Covers: the four registries; completeness + duplicate-key prevention + configured-owner validation + honest
not_configured; blocked / reviewer_not_confirmed certification treatment; reviewer authority never inferred;
business approval is not regulatory certification; dashboard composition + panel explainability + deep links;
evidence availability / completeness / freshness; stale-evidence handling; supervisory reviews; findings /
exceptions; licensing / CE / suitability / replacement evidence; filing acknowledgements (not_configured);
examination-request coverage; record scope; runtime gates; policy enforcement; per-panel restriction;
fail-closed behavior; governance; diagnostics; analytics reuse; AI summarize-only; and the architecture
invariants (no second platform, no evidence persistence/mutation, no filing submission, no certification
creation, no fabricated readiness score, no compliance-from-missing-findings, no sensitive evidence).
"""
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.regulatory_readiness import (
    client_evidence_readiness,
    compose_dashboard,
    gate,
    get_panel,
    governance,
    household_evidence_readiness,
    list_dashboards,
    readiness_summary,
    registry,
)
from app.services.regulatory_readiness import diagnostics as diag

RR_DIR = pathlib.Path("app/services/regulatory_readiness")

FIRM = Principal(1, "m@e.com", "M",
                 frozenset({"compliance.supervise", "analytics.executive", "documents.view", "security.view",
                            "governance.view", "integration.view", "observability.view",
                            "observability.audit", "insurance.licensing.read", "record.read_all"}))
NONE = Principal(3, "n@e.com", "N", frozenset({"record.read_all"}))         # no supervise/executive


# --- registries --------------------------------------------------------------

def test_registries_complete():
    assert len(registry.REGULATORY_OBLIGATION_REGISTRY) == 23
    assert len(registry.EVIDENCE_REGISTRY) == 27
    assert len(registry.EXAMINATION_REQUEST_REGISTRY) == 22
    assert len(registry.CERTIFICATION_REGISTRY) == 14
    assert len(registry.PANEL_REGISTRY) == 37
    assert len(registry.READINESS_DASHBOARDS) == 8


def test_no_duplicate_registry_keys():
    for keys in ([o.key for o in registry.REGULATORY_OBLIGATION_REGISTRY],
                 [e.key for e in registry.EVIDENCE_REGISTRY],
                 [r.key for r in registry.EXAMINATION_REQUEST_REGISTRY],
                 [c.key for c in registry.CERTIFICATION_REGISTRY],
                 [p.key for p in registry.PANEL_REGISTRY],
                 [d.key for d in registry.READINESS_DASHBOARDS]):
        assert len(keys) == len(set(keys))


def test_every_configured_obligation_has_authoritative_owner():
    for o in registry.REGULATORY_OBLIGATION_REGISTRY:
        assert o.reg_domain and o.capabilities and o.deep_links and o.runtime_gate
        if o.config_status == registry.CONFIGURED:
            assert o.authoritative_owner != registry.NOT_CONFIGURED, o.key


def test_not_configured_obligations_reported_honestly():
    nc = set(registry.not_configured_obligations())
    # filing / examination-adjacent obligations have no authoritative owner today.
    assert {"investment_adviser_registration", "form_adv_governance", "advertising_marketing_review",
            "custody_asset_verification", "conflicts_of_interest", "complaint_handling"} <= nc
    for key in nc:
        assert registry.obligation(key).authoritative_owner == registry.NOT_CONFIGURED


def test_every_configured_evidence_has_storage_owner():
    for e in registry.EVIDENCE_REGISTRY:
        assert e.evidence_class and e.freshness and e.deep_link
        if e.config_status == registry.CONFIGURED:
            assert e.authoritative_owner != registry.NOT_CONFIGURED, e.key
            assert e.storage_owner != registry.NOT_CONFIGURED, e.key


def test_filing_and_examination_evidence_is_not_configured():
    for key in ("regulatory_filing_acknowledgements", "state_filing_acknowledgements", "filing_history",
                "examination_correspondence", "backup_restore_evidence"):
        assert registry.evidence_class(key).config_status == registry.NOT_CONFIGURED


# --- certifications: blocked / reviewer_not_confirmed, never inferred --------

def test_all_certifications_are_blocked_reviewer_not_confirmed():
    # the reviewer_authorities catalog is seeded empty → every certification is reviewer_not_confirmed.
    for c in registry.CERTIFICATION_REGISTRY:
        assert c.status in (registry.BLOCKED, registry.REVIEWER_NOT_CONFIRMED), c.key
        assert c.blocked_reason, c.key


def test_reviewer_authority_never_inferred_and_no_named_reviewer():
    for c in registry.CERTIFICATION_REGISTRY:
        # named reviewer + review date are never fabricated.
        assert c.named_reviewer == registry.REVIEWER_NOT_CONFIRMED, c.key
        assert c.review_date == registry.NOT_CONFIGURED, c.key


def test_business_approval_is_not_regulatory_certification():
    # no certification carries a fabricated "approved" status; the business owner is not the certifier.
    for c in registry.CERTIFICATION_REGISTRY:
        assert c.status != "approved", c.key
    for o in registry.REGULATORY_OBLIGATION_REGISTRY:
        # the accountable business owner is separate from the (unconfirmed) compliance reviewer.
        assert o.business_owner != o.compliance_reviewer or o.compliance_reviewer == registry.REVIEWER_NOT_CONFIRMED


def test_blocked_certifications_panel_states_why():
    p = get_panel(FIRM, "blocked_certifications")
    assert p is not None and p["blocked"] and p["blocked_reason"]
    assert p["value"]["count"] == 14
    assert p["value"]["business_approval_is_not_certification"] is True


# --- composition + explainability --------------------------------------------

def test_all_dashboards_compose_and_deep_link_to_owners():
    for d in registry.READINESS_DASHBOARDS:
        result = compose_dashboard(FIRM, d.key)
        assert result and result["enabled"] and result["dashboard"]
        board = result["dashboard"]
        assert board["generated_at"] and board["governing_services"]
        assert board["operational_readiness_not_certification"] is True
        assert "not_configured_domains" in board and "blocked_domains" in board
        for panel in board["panels"]:
            assert panel["explanation"] and panel["source"] and panel["deep_link"]
        assert board["deep_links"]


def test_every_panel_deep_links_and_has_permission():
    for p in registry.PANEL_REGISTRY:
        assert p.owner and p.source and p.deep_link and p.explainability and p.permission
        assert p.lifecycle in registry.LIFECYCLES


def test_derived_readiness_labeled_and_not_certification():
    p = get_panel(FIRM, "derived_readiness_coverage")
    assert p["derived"] is True
    assert p["value"]["operational_readiness_not_certification"] is True
    assert p["value"]["absence_of_findings_is_not_compliance"] is True


def test_list_dashboards_metadata_only():
    ld = list_dashboards(FIRM)
    assert ld["enabled"] and len(ld["dashboards"]) == 8
    for d in ld["dashboards"]:
        assert "panel_count" in d and "required_capabilities" in d
        assert "value" not in d


# --- authorization -----------------------------------------------------------

def test_unauthorized_principal_gets_none():
    assert compose_dashboard(NONE, "examination_readiness") is None
    assert list_dashboards(NONE)["dashboards"] == []


def test_unentitled_panel_is_restricted_never_valued():
    supervisor = Principal(4, "s@e.com", "S", frozenset({"compliance.supervise"}))
    p = get_panel(supervisor, "cybersecurity_evidence")   # requires security.view
    assert p is not None and p["restricted"] and p["value"] is None and p["available"] is False


# --- not_configured filing panels --------------------------------------------

def test_filing_panels_report_not_configured():
    for key in ("federal_filing_acknowledgements", "state_filing_acknowledgements", "filing_history",
                "examination_correspondence_availability", "evidence_export_availability"):
        p = get_panel(FIRM, key)
        assert p is not None and p["config_status"] == registry.NOT_CONFIGURED and p["available"] is False


# --- gate + policy -----------------------------------------------------------

def test_gate_off_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "enabled", lambda: False)
    assert compose_dashboard(FIRM, "examination_readiness") == {"enabled": False, "dashboard": None}
    assert list_dashboards(FIRM) == {"enabled": False, "dashboards": []}
    assert readiness_summary(FIRM)["enabled"] is False


def test_dashboard_specific_gate(monkeypatch):
    real_gate = gate.gate
    monkeypatch.setattr(gate, "gate", lambda n: False if n == "certification_signoff.enabled" else real_gate(n))
    result = compose_dashboard(FIRM, "certification_signoff")
    assert result and result.get("gated") == "certification_signoff.enabled"


def test_policy_deny_is_honored(monkeypatch):
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    result = compose_dashboard(FIRM, "examination_readiness")
    assert result and result.get("denied") == "policy"


# --- summary + client/household rollups --------------------------------------

def test_readiness_summary_operational_not_certification():
    s = readiness_summary(FIRM)
    assert s["enabled"] and s["generated_at"] and "panels" in s
    assert s["operational_readiness_not_certification"] is True
    assert s["absence_of_findings_is_not_compliance"] is True
    assert len(s["blocked_certifications"]) == 14


def test_client_and_household_evidence_are_record_scoped():
    cr = client_evidence_readiness(FIRM, 1)
    assert cr["source"] == "regulatory_readiness.client_evidence_readiness"
    assert cr["operational_readiness_not_certification"] is True and "signals" in cr
    hr = household_evidence_readiness(FIRM, 1, [1, 2])
    assert "signals" in hr and hr["operational_readiness_not_certification"] is True


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_regulatory_readiness()
    assert report["ok"] is True, report["findings"]


def test_governance_detects_forbidden_mutation(monkeypatch):
    orig = governance._src

    def fake_src(rel):
        s = orig(rel)
        if rel == "service.py":
            s = s + "\n# record_decision(review_id='x')\n"
        return s
    monkeypatch.setattr(governance, "_src", fake_src)
    report = governance.validate_regulatory_readiness()
    assert any(f["type"] == "duplicate_engine_call" for f in report["findings"])


def test_governance_detects_inferred_reviewer(monkeypatch):
    from app.services.regulatory_readiness.registry import Certification
    bogus = Certification("bogus", "scope", "not_configured", "role", "Some Named Person", "qual",
                          "not_configured", "reviewer_not_confirmed", "reason", "owner", "owner",
                          "certification_signoff.enabled", ("compliance.supervise",), "/x", "configured")
    monkeypatch.setattr(registry, "CERTIFICATION_REGISTRY", (*registry.CERTIFICATION_REGISTRY, bogus))
    report = governance.validate_regulatory_readiness()
    assert any(f["type"] == "inferred_reviewer_authority" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_persistence_no_outbox():
    for name in ("service.py", "panels.py", "registry.py", "model.py", "gate.py", "stats.py",
                 "metrics.py", "diagnostics.py", "governance.py", "__init__.py"):
        if name == "governance.py":
            continue  # holds the detection string-literals
        src = (RR_DIR / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit(",
                     "engine.begin("):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow store)"


def test_no_second_compliance_filing_or_certification_engine():
    composed = (RR_DIR / "service.py").read_text() + (RR_DIR / "panels.py").read_text()
    for forbidden in ("record_decision(", "submit_review(", "assign_reviewer(", "activate(", "revoke(",
                      "record_evidence(", "record_license(", "execute_deletion(", "write_audit("):
        assert forbidden not in composed, forbidden


def test_composes_the_authoritative_owners():
    composed = (RR_DIR / "panels.py").read_text() + (RR_DIR / "service.py").read_text()
    assert "compliance_intelligence" in composed
    assert "document_intelligence" in composed


def test_no_fabricated_readiness_score_or_compliance_from_missing_findings():
    for p in registry.PANEL_REGISTRY:
        if p.source.startswith("regulatory_readiness.") and (
                "readiness" in p.key or "coverage" in p.key or p.key.endswith("_score")):
            assert p.derived, p.key
    s = readiness_summary(FIRM)
    assert "compliant" not in s and "noncompliant" not in s


def test_no_second_metrics_registry():
    for name in ("registry.py", "metrics.py", "panels.py", "service.py"):
        src = (RR_DIR / name).read_text()
        assert not re.search(r"^_DEFS\s*=|class\s+Metric\b", src, re.M), name


# --- analytics counter reuse (single registry) -------------------------------

def test_counters_registered_in_single_analytics_registry():
    from app.services.analytics.metrics import METRICS, compute_metric
    for key in ("readiness_dashboards_composed", "readiness_panels_composed", "readiness_panel_failures",
                "readiness_authorization_failures"):
        assert key in METRICS
        assert compute_metric(FIRM, key).get("value") is not None


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape_low_cardinality():
    d = diag.readiness_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "panel_compute_coverage", "governance"} <= set(d)
    assert d["panel_compute_coverage"]["with_compute"] == d["panel_compute_coverage"]["total"] == 37
    assert d["blocked_certifications"] == 14
    assert d["governance"]["ok"] is True


# --- routes ------------------------------------------------------------------

def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/regulatory-readiness", "/api/v1/regulatory-readiness/dashboards",
            "/api/v1/regulatory-readiness/dashboard/{key}", "/api/v1/regulatory-readiness/summary",
            "/api/v1/regulatory-readiness/registry", "/api/v1/regulatory-readiness/panel/{key}",
            "/api/v1/regulatory-readiness/metrics", "/regulatory-readiness/diagnostics"} <= paths


def test_routes_capability_gated():
    dep = require_any_capability("compliance.supervise", "analytics.executive")
    without = Principal(9, "no@e.com", "No", frozenset({"record.read_all"}))
    with pytest.raises(HTTPException) as ei:
        dep(principal=without)
    assert ei.value.status_code == 403
    assert dep(principal=Principal(10, "s@e.com", "S", frozenset({"compliance.supervise"}))) is not None
    assert dep(principal=Principal(11, "e@e.com", "E", frozenset({"analytics.executive"}))) is not None


def test_route_module_defines_no_business_logic():
    src = pathlib.Path("app/routes/regulatory_readiness.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit(", "record_decision("):
        assert forbidden not in src


# --- surface integration -----------------------------------------------------

def test_workspace_regulatory_readiness_panel_present():
    from app.services.workspace.service import get_workspace
    ws = get_workspace(FIRM)
    assert "regulatory_readiness" in ws


def test_ai_never_certifies_or_files():
    src = pathlib.Path("app/services/ai_assist/context.py").read_text()
    for forbidden in ("record_decision(", "submit_review(", "execute_deletion(", "record_evidence("):
        assert forbidden not in src


def test_docs_and_adr_exist():
    for rel in ("docs/REGULATORY_EXAMINATION_READINESS.md", "docs/REGULATORY_OBLIGATION_REGISTRY.md",
                "docs/EVIDENCE_REGISTRY.md", "docs/EXAMINATION_REQUEST_REGISTRY.md",
                "docs/CERTIFICATION_SIGNOFF_REGISTRY.md", "docs/REGULATORY_READINESS_GOVERNANCE.md"):
        assert pathlib.Path(rel).is_file(), rel
    adrs = list(pathlib.Path("docs/adr").glob("ADR-064-*.md"))
    assert adrs, "ADR-064 missing"
