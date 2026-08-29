"""360Plus displays assets under management to NO ONE.

Binding business rule: no user — administrator, advisor, tax staff, benefits staff, compliance,
a holder of record.read_all, or anyone with firm-wide scope — may see AUM anywhere in the product.

The rule is enforced at ONE boundary, ``app/services/portfolio.py::_CANONICAL_KEYS``: aggregate
totals are still computed internally (percentages, high-cash triage, reconciliation) but never enter
a returned contract, so no route, template, or API response can carry them. These tests assert the
SERVER-SIDE payloads, not the rendered HTML — hiding a tile is not the mechanism.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from app.db import accounts, engine, households, people
from app.security.models import Principal

REPO = Path(__file__).resolve().parent.parent

#: Keys that would constitute AUM exposure if they ever appeared in a user-facing payload.
FORBIDDEN_KEYS = ("aum", "total_aum", "firm_aum", "household_aum", "portfolio_aum", "book_aum",
                  "assets_under_management", "advisory_revenue_basis")

ALL_CAPS = frozenset({
    "client.read", "client.write", "record.read_all", "record.write_all", "analytics.view",
    "analytics.executive", "portfolio.firm_metrics", "vault.access.all", "vault.category.wealth",
})


def _principal(caps):
    return Principal(1, "u@e.test", "U", frozenset(caps))


# Every role shape named in the requirement, including the impossible-in-production case where a
# principal somehow holds EVERY capability at once.
ROLE_SHAPES = {
    "administrator": ALL_CAPS,
    "advisor": {"client.read", "client.write", "record.read_all", "analytics.view"},
    "tax_staff": {"client.read", "analytics.view"},
    "benefits_staff": {"client.read"},
    "compliance": {"client.read", "record.read_all", "vault.access.all"},
    "record_read_all_only": {"record.read_all"},
    "everything": ALL_CAPS,
}


def _leaks(payload, path="payload"):
    """Every forbidden key reachable anywhere in a nested structure, with its path."""
    found = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            kl = str(k).lower()
            if any(kl == f or kl.endswith("_" + f) or f in kl for f in ("aum", "advisory_revenue_basis")):
                found.append(f"{path}.{k}")
            found.extend(_leaks(v, f"{path}.{k}"))
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            found.extend(_leaks(v, f"{path}[{i}]"))
    return found


@pytest.fixture
def client_with_accounts():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Suppression HH {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Suppression Client {tag}",
                                              active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(accounts).values(
            person_id=pid, household_id=hid, custodian="Schwab", account_number=f"A-{tag}",
            account_name=f"Acct {tag}", status="open", total_value=1_234_567, cash_value=50_000))
    return {"person_id": pid, "household_id": hid}


# --- the boundary itself ---------------------------------------------------------------------

def test_the_canonical_portfolio_contract_declares_no_aum():
    from app.services.portfolio import _CANONICAL_KEYS
    assert not [k for k in _CANONICAL_KEYS if "aum" in k.lower()]


def test_person_and_household_portfolios_carry_no_aum(client_with_accounts):
    from app.services.portfolio import get_household_portfolio, get_person_portfolio
    for payload in (get_person_portfolio(client_with_accounts["person_id"]),
                    get_household_portfolio(client_with_accounts["household_id"])):
        assert _leaks(payload) == []


def test_the_internal_total_still_computes_for_reconciliation(client_with_accounts):
    """Requirement 7: internal computation is preserved behind a non-user-facing boundary."""
    from app.services.portfolio import _internal_total
    assert float(_internal_total([client_with_accounts["person_id"]])) == 1_234_567.0


def test_the_internal_total_is_not_exported_under_the_old_public_name():
    import app.services.portfolio as portfolio
    assert not hasattr(portfolio, "book_aum"), "book_aum was a user-facing export; it must stay private"


# --- A. firm-wide ------------------------------------------------------------------------------

@pytest.mark.parametrize("role", sorted(ROLE_SHAPES))
def test_no_role_sees_firm_aum_on_the_dashboard(role, client_with_accounts):
    from app.services.dashboard import get_dashboard_data
    assert _leaks(get_dashboard_data(_principal(ROLE_SHAPES[role]))) == []


def test_api_stats_exposes_no_aum(client_with_accounts):
    from starlette.requests import Request

    from app.routes.dashboard import stats
    req = Request({"type": "http", "method": "GET", "path": "/api/stats",
                   "headers": [], "query_string": b""})
    req.state.principal = _principal(ALL_CAPS)
    assert _leaks(stats(req)) == []


def test_firm_portfolio_metrics_and_wealth_dashboard_expose_no_aum(client_with_accounts):
    from app.services.portfolio import get_firm_portfolio_metrics, get_wealth_dashboard
    assert _leaks(get_firm_portfolio_metrics()) == []
    assert _leaks(get_wealth_dashboard()) == []


def test_the_largest_household_is_named_without_its_value(client_with_accounts):
    """A single-row disclosure of the biggest book is still a disclosure of that figure."""
    from app.services.portfolio import get_firm_portfolio_metrics
    lh = get_firm_portfolio_metrics()["largest_household"]
    if lh is not None:
        assert set(lh) == {"name"}


# --- B. executive analytics ----------------------------------------------------------------------

def test_the_analytics_registry_has_no_aum_metric():
    from app.services.analytics.metrics import _DEFS
    assert not [m for m in _DEFS if "aum" in m.key.lower()]


def test_executive_intelligence_registers_no_aum_widget():
    from app.services.executive_intelligence.registry import WIDGET_REGISTRY
    assert not [w for w in WIDGET_REGISTRY if "aum" in w.key.lower()]


def test_analytics_executive_still_authorizes_other_executive_metrics():
    """Only the AUM metric was removed — the capability must keep doing its job."""
    from app.services.analytics.metrics import _DEFS
    executive = [m for m in _DEFS if getattr(m, "executive", False)]
    assert len(executive) >= 3, "analytics.executive must still gate real executive metrics"


# --- C. financial operations -----------------------------------------------------------------------

def test_financial_operations_panels_expose_no_aum(client_with_accounts):
    from app.services.financial_operations import client_financial, household_financial
    p = _principal(ALL_CAPS)
    assert _leaks(client_financial(p, client_with_accounts["person_id"])) == []
    assert _leaks(household_financial(p, client_with_accounts["household_id"],
                                      [client_with_accounts["person_id"]])) == []


# --- D. per-household / per-person -----------------------------------------------------------------

@pytest.mark.parametrize("role", sorted(ROLE_SHAPES))
def test_no_role_sees_client_or_household_aum_in_client360(role, client_with_accounts):
    from app.services.client360.sections import financial as financial_section
    ctx = {"entity_type": "person", "entity_id": client_with_accounts["person_id"],
           "person_id": client_with_accounts["person_id"],
           "household_id": client_with_accounts["household_id"], "portfolio": {}}
    from app.services.portfolio import get_person_portfolio
    ctx["portfolio"] = get_person_portfolio(client_with_accounts["person_id"])
    assert _leaks(financial_section(_principal(ROLE_SHAPES[role]), ctx)) == []


def test_the_client_snapshot_exposes_no_aum(client_with_accounts):
    from app.services.advisor_workspace import get_client_snapshot
    assert _leaks(get_client_snapshot(client_with_accounts["person_id"],
                                      client_with_accounts["household_id"])) == []


def test_portfolio_search_returns_no_aum_and_offers_no_threshold_filter(client_with_accounts):
    import inspect

    from app.services.portfolio import search_portfolios
    assert "min_aum" not in inspect.signature(search_portfolios).parameters, \
        "a threshold filter is a binary-search oracle on the prohibited total"
    assert _leaks(search_portfolios()) == []


# --- non-AUM functionality must survive ------------------------------------------------------------

def test_non_aum_portfolio_functionality_still_works(client_with_accounts):
    from app.services.portfolio import get_person_portfolio
    p = get_person_portfolio(client_with_accounts["person_id"])
    assert p["accounts"], "account existence/detail must remain"
    assert p["accounts"][0]["custodian"] == "Schwab"
    for key in ("cash", "cash_percent", "allocation", "holdings", "beneficiary_count"):
        assert key in p


def test_vault_category_wealth_behaviour_is_unchanged():
    """It gates wealth DOCUMENTS, not AUM, and must keep working exactly as before."""
    from app.services.vault.service import CATEGORIES, can_access_category
    assert "wealth" in CATEGORIES
    assert can_access_category(_principal({"vault.category.wealth"}), "wealth") is True
    assert can_access_category(_principal({"vault.access.all"}), "wealth") is True
    assert can_access_category(_principal({"client.read"}), "wealth") is False


# --- repository guard: make reintroduction hard ------------------------------------------------------

_UI_AUM = re.compile(r"\b(AUM|aum)\b")


def test_no_template_renders_an_aum_value():
    """A template that prints AUM is user-facing exposure by definition."""
    offenders = []
    for path in (REPO / "app" / "templates").rglob("*.html"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "{{" in line and _UI_AUM.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:100]}")
    assert offenders == [], "templates must not render AUM:\n" + "\n".join(offenders)


def test_no_service_returns_a_forbidden_aum_key():
    """Guard against reintroducing an AUM key into any dict literal under app/."""
    offenders = []
    for path in (REPO / "app").rglob("*.py"):
        # Two INTERNAL, non-user-facing exemptions (requirement 7):
        #   * portfolio/calculations.py — aggregate_portfolio(), the internal computation whose
        #     total feeds percentages and reconciliation; never returned to a caller-facing contract.
        #   * portfolio_diagnostics.py — an unrouted developer diagnostic, not reachable by a user.
        if ("__pycache__" in str(path)
                or path.name == "portfolio_diagnostics.py"
                or path.as_posix().endswith("app/portfolio/calculations.py")):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            for key in FORBIDDEN_KEYS:
                if f'"{key}":' in line or f"'{key}':" in line:
                    offenders.append(f"{path.relative_to(REPO)}:{n}: {stripped[:100]}")
    assert offenders == [], "no user-facing dict may carry an AUM key:\n" + "\n".join(offenders)
