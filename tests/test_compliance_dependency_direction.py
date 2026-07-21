"""Dependency-direction guardrails for the D.5–D.8 compliance stack (Phase D.8A).

Consolidates the previously scattered one-off import checks into a single
characterization of the intended one-way dependency graph:

    Advisor Intelligence -> Rule Catalog -> Compliance Review -> Reviewer Authority

Each edge points downward only. These are static-import assertions (cheap, stable) that
fail loudly if a future change reverses a dependency or lets a lower layer reach back
up, or lets database/service modules import routes/templates.
"""
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"


def _src(rel):
    return (APP / rel).read_text()


# --- Advisor Intelligence must not know about compliance at all --------------

def test_advisor_intelligence_does_not_import_compliance():
    src = _src("services/advisor_intelligence.py")
    assert "services.compliance" not in src
    assert "import compliance" not in src


# --- Rule Catalog depends only upward on Advisor Intelligence ----------------

def test_rule_catalog_does_not_depend_on_review_or_authority():
    src = _src("services/compliance/rule_catalog.py")
    for forbidden in ("compliance.reviews", "compliance.reviewer_authority",
                      "compliance.authority_admin", "import reviews", "import authority_admin"):
        assert forbidden not in src


# --- Compliance services never reach up into routes/templates ----------------

@pytest.mark.parametrize("module", [
    "services/compliance/rule_catalog.py",
    "services/compliance/reviews.py",
    "services/compliance/reviewer_authority.py",
    "services/compliance/authority_admin.py",
    "services/compliance/_common.py",
])
def test_compliance_services_do_not_import_routes_or_templates(module):
    src = _src(module)
    assert "app.routes" not in src
    assert "Jinja2Templates" not in src
    assert "TemplateResponse" not in src


# --- Reviewer-authority administration must not execute AI rules -------------

def test_authority_admin_does_not_execute_advisor_intelligence():
    src = _src("services/compliance/authority_admin.py")
    # Authority administration records facts; it never produces or runs AI signals.
    assert "advisor_intelligence" not in src
    assert "get_client_signals" not in src


# --- Reviewer-authority LOOKUP stays a leaf (no review/catalog deps) ---------

def test_reviewer_authority_lookup_is_a_leaf():
    src = _src("services/compliance/reviewer_authority.py")
    for forbidden in ("compliance.reviews", "compliance.rule_catalog",
                      "compliance.authority_admin", "advisor_intelligence"):
        assert forbidden not in src


# --- Database modules must not import routes or templates --------------------

@pytest.mark.parametrize("module", ["db.py", "database/compliance_tables.py"])
def test_database_modules_do_not_import_routes_or_templates(module):
    src = _src(module)
    assert "app.routes" not in src
    assert "app.templates" not in src
    assert "Jinja2Templates" not in src
