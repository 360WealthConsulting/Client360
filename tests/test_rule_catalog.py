"""Compliance Rule Catalog tests (Phase D.6).

Covers the governance layer over the Advisor Intelligence registry: the
RuleDefinition model, the RuleCatalog service (registry consumption, uniqueness,
versioning, ownership, approval states, lifecycle, documentation references,
serialization, search/filter/sort), the read-only route + authorization, and the
one-way dependency direction (Advisor Intelligence never imports compliance).
"""
import json
from pathlib import Path

import pytest
from starlette.requests import Request

from app.security.models import Principal
from app.services.advisor_intelligence import list_registered_signals
from app.services.compliance.rule_catalog import (
    APPROVAL_STATES,
    RuleCatalog,
    RuleDefinition,
    compare_versions,
    is_valid_semver,
    parse_version,
)

CATALOG = RuleCatalog.from_registry()


# --- version helpers ---------------------------------------------------------

def test_semver_validation():
    assert is_valid_semver("1.0.0")
    assert is_valid_semver("2.10.3")
    assert not is_valid_semver("1.0")
    assert not is_valid_semver("1.0.0-beta")
    assert not is_valid_semver("")
    assert not is_valid_semver("a.b.c")


def test_version_parse_and_compare():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert compare_versions("1.0.0", "2.0.0") == -1
    assert compare_versions("2.0.0", "1.9.9") == 1
    assert compare_versions("1.1.0", "1.1.0") == 0
    # Semantic, not lexicographic: 1.10.0 > 1.9.0.
    assert compare_versions("1.10.0", "1.9.0") == 1
    with pytest.raises(ValueError):
        parse_version("nope")


# --- registry consumption + model -------------------------------------------

def test_catalog_covers_every_registered_rule():
    reg_keys = {r.key for r in list_registered_signals()}
    cat_ids = {r.rule_id for r in CATALOG.list_rules()}
    assert cat_ids == reg_keys
    assert len(CATALOG.list_rules()) == len(reg_keys)


def test_rule_definition_has_all_governance_fields():
    r = CATALOG.get_rule("annual_portfolio_review_recommendation")
    assert isinstance(r, RuleDefinition)
    for field in ("rule_id", "title", "description", "category", "governing_rule",
                  "version", "policy_gate", "owner_role", "owner_name", "approval_status",
                  "approved_date", "effective_date", "expiration_date", "source_documents",
                  "implementation_status", "superseded_by", "deprecated_reason"):
        assert hasattr(r, field)
    assert r.governing_rule == "RULE-PORTFOLIO-REVIEW-CADENCE"
    assert r.version == "1.0.0"
    assert r.implementation_status == "implemented"


def test_operational_rule_omits_governance_but_still_versioned():
    r = CATALOG.get_rule("client_review_overdue")
    assert r.category == "review"
    assert r.governing_rule is None
    assert r.owner_role is None
    assert r.owner_name is None
    assert r.approval_status == "pending_assignment"  # no recorded owner/approval
    assert r.version == "1.0.0"  # initial-version convention


# --- ownership (no fabrication) ---------------------------------------------

def test_owner_role_parsed_and_names_never_fabricated():
    # advisor_operations-owned rule.
    ops = CATALOG.get_rule("annual_portfolio_review_recommendation")
    assert ops.owner_role == "advisor_operations"
    assert ops.owner_name is None
    # compliance_reviewer (unassigned) -> role only, no individual.
    ins = CATALOG.get_rule("insurance_review_recommendation")
    assert ins.owner_role == "compliance_reviewer"
    assert ins.owner_name is None
    # No rule invents an individual name.
    assert all(r.owner_name is None for r in CATALOG.list_rules())


# --- approval states ---------------------------------------------------------

def test_approval_states_are_from_the_allowed_vocabulary():
    for r in CATALOG.list_rules():
        assert r.approval_status in APPROVAL_STATES


def test_registry_approval_is_mapped_into_governance_vocabulary():
    assert CATALOG.get_rule("annual_portfolio_review_recommendation").approval_status == "approved"
    # registry "pending_compliance_review" -> governance "pending_review"
    assert CATALOG.get_rule("insurance_review_recommendation").approval_status == "pending_review"
    assert CATALOG.get_rule("beneficiary_review_recommendation").approval_status == "pending_review"


# --- lifecycle + dates (informational; null unless recorded) -----------------

def test_lifecycle_and_dates_are_null_when_unrecorded():
    for r in CATALOG.list_rules():
        assert r.approved_date is None
        assert r.effective_date is None
        assert r.expiration_date is None
        assert r.superseded_by is None
        assert r.deprecated_reason is None


# --- documentation references (real files, references only) ------------------

def test_source_documents_reference_real_files():
    repo = Path(__file__).resolve().parent.parent
    for r in CATALOG.list_rules():
        assert r.source_documents  # every rule references at least the architecture doc
        for d in r.source_documents:
            assert {"type", "title", "ref"} <= set(d)
            assert (repo / d["ref"]).exists(), f"missing doc {d['ref']}"
    # Governed recommendation rules additionally reference the governance docs.
    rec = CATALOG.get_rule("beneficiary_review_recommendation")
    refs = {d["ref"] for d in rec.source_documents}
    assert "docs/V1_RISK_REGISTER.md" in refs
    assert "docs/PRODUCT_DECISIONS.md" in refs
    # Operational rules reference only the architecture doc.
    op = CATALOG.get_rule("overdue_open_task")
    assert {d["ref"] for d in op.source_documents} == {"docs/ADVISOR_WORKSPACE_ARCHITECTURE.md"}


# --- serialization -----------------------------------------------------------

def test_rule_definition_serialization_is_json_safe():
    for r in CATALOG.list_rules():
        d = r.to_dict()
        json.loads(json.dumps(d))
        assert d["rule_id"] == r.rule_id
        assert isinstance(d["source_documents"], list)


# --- uniqueness + version verification --------------------------------------

def test_uniqueness_and_version_verification_pass():
    CATALOG.validate_uniqueness()  # no raise
    CATALOG.verify_versions()  # no raise


# --- search / filter / sort --------------------------------------------------

def test_search_matches_id_title_description_and_governing_rule():
    by_gov = CATALOG.query(search="RULE-INSURANCE-REVIEW-CADENCE")
    assert by_gov and all(r.governing_rule == "RULE-INSURANCE-REVIEW-CADENCE" for r in by_gov)
    by_word = CATALOG.query(search="beneficiary")
    assert {r.rule_id for r in by_word} >= {"beneficiary_review_opportunity",
                                            "beneficiary_review_recommendation"}
    assert CATALOG.query(search="zzz-no-match") == []


def test_filter_by_category_gate_and_status():
    recs = CATALOG.query(category="recommendation")
    assert recs and all(r.category == "recommendation" for r in recs)
    licensed = CATALOG.query(policy_gate="license_required")
    assert [r.rule_id for r in licensed] == ["insurance_review_recommendation"]
    approved = CATALOG.query(approval_status="approved")
    assert approved and all(r.approval_status == "approved" for r in approved)


def test_sort_by_version_is_semantic_and_by_columns_is_stable():
    # All versions are 1.0.0 here, so tie-break falls to rule_id ascending.
    ids = [r.rule_id for r in CATALOG.query(sort="version")]
    assert ids == sorted(ids)
    desc = [r.rule_id for r in CATALOG.query(sort="rule_id", descending=True)]
    assert desc == sorted(desc, reverse=True)


# --- dependency direction ----------------------------------------------------

def test_advisor_intelligence_does_not_import_compliance():
    src = (Path(__file__).resolve().parent.parent
           / "app" / "services" / "advisor_intelligence.py").read_text()
    # No import statement pulling the compliance package into Advisor Intelligence.
    assert "import compliance" not in src
    assert "from app.services.compliance" not in src
    assert "services.compliance" not in src


# --- route + authorization + rendering --------------------------------------

def _req(path="/admin/rule-catalog"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [],
                    "query_string": b""})


def test_route_requires_audit_read_capability():
    from app.security.middleware import RULES
    cap = next((code for pat, code in RULES if pat.search("/admin/rule-catalog")), None)
    assert cap == "audit.read"


def test_rule_catalog_page_renders_read_only():
    from app.routes.admin import rule_catalog
    principal = Principal(1, "a@e.com", "Admin", frozenset({"audit.read"}))
    resp = rule_catalog(_req(), principal=principal)
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Rule Catalog" in body
    assert "RULE-PORTFOLIO-REVIEW-CADENCE" in body
    assert "Annual Portfolio Review Recommendation" in body  # derived title
    # Read-only: no editing / approval / workflow controls.
    for control in ("Approve<", "Reject", "Edit<", "Delete", "Run workflow", "method=\"post\""):
        assert control not in body


def test_rule_catalog_page_supports_filtering_via_query():
    from app.routes.admin import rule_catalog
    principal = Principal(1, "a@e.com", "Admin", frozenset({"audit.read"}))
    resp = rule_catalog(_req(), category="recommendation", principal=principal)
    body = resp.body.decode()
    assert "Annual Portfolio Review Recommendation" in body
    # An operational-only rule is filtered out.
    assert "Overdue Open Task" not in body
