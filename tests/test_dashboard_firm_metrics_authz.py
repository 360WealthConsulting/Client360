"""Firm-wide AUM / portfolio metrics are privileged and gated SERVER-SIDE.

Root defect: firm-wide financial aggregates (firm AUM, total account value, cash waiting, largest
household/position) were returned to any dashboard viewer. They are now gated on the dedicated
``portfolio.firm_metrics`` capability — NOT on ``record.read_all`` — so an ordinary employee (even one who
can read all records) neither receives nor sees them, on the Firm Dashboard, ``/api/stats``, or ``/wealth``.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.db import capabilities, engine, role_capabilities, roles
from app.routes.dashboard import advisor_dashboard, stats
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.dashboard import FIRM_METRICS_CAPABILITY, get_dashboard_data

# AUM ("firm_aum"/"total_aum") is no longer in this set: it is exposed to NOBODY, capability or
# not, so it cannot be asserted as "present for the privileged principal". tests/test_no_aum_exposure.py
# pins its total absence. What remains here is the non-AUM firm triage this capability still gates.
_SENSITIVE = ("cash_waiting", "largest_household", "largest_position")
_OPERATIONAL = ("people", "households", "accounts", "open_tasks", "recent_activities")

# Privileged: holds the dedicated firm-metrics capability.
PRIV = Principal(1, "admin@firm.test", "Owner", frozenset({FIRM_METRICS_CAPABILITY, "record.read_all", "client.read"}))
# Two DISTINCT ordinary staff so this can never regress into a single-user ("Jessica-only") fix:
#  - an advisor who HAS record.read_all (broad record scope) but NOT firm metrics
#  - a coordinator with only client.read
ORDINARY = [
    Principal(2, "advisor@firm.test", "Advisor", frozenset({"record.read_all", "client.read"})),
    Principal(3, "coord@firm.test", "Coordinator", frozenset({"client.read"})),
]


def _req(principal, path="/"):
    req = Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})
    req.state.principal = principal
    return req


# --- service layer (the authoritative mechanism) --------------------------------------------------

def test_privileged_principal_receives_firm_metrics():
    data = get_dashboard_data(PRIV)
    for key in _SENSITIVE:
        assert key in data, key


@pytest.mark.parametrize("principal", ORDINARY)
def test_ordinary_employee_never_receives_firm_metrics(principal):
    data = get_dashboard_data(principal)
    for key in _SENSITIVE:
        assert key not in data, f"{key} leaked to {principal.email}"      # incl. record.read_all holder


def test_missing_principal_fails_safe():
    for data in (get_dashboard_data(None), get_dashboard_data()):
        assert not any(k in data for k in _SENSITIVE)


def test_ordinary_employee_still_gets_operational_dashboard():
    data = get_dashboard_data(ORDINARY[0])
    for key in _OPERATIONAL:
        assert key in data, key                                          # normal dashboard unaffected


def test_authorization_is_server_side_not_template():
    # The values are absent from the data the server produces — hiding in Jinja is not the mechanism.
    data = get_dashboard_data(ORDINARY[1])
    assert all(k not in data for k in _SENSITIVE)


# --- /api/stats JSON endpoint --------------------------------------------------------------------

@pytest.mark.parametrize("principal", ORDINARY)
def test_api_stats_does_not_leak_to_ordinary(principal):
    body = stats(_req(principal, "/api/stats"))
    for key in _SENSITIVE:
        assert key not in body, key


def test_api_stats_returns_firm_metrics_to_privileged():
    body = stats(_req(PRIV, "/api/stats"))
    assert all(key in body for key in _SENSITIVE)


def test_api_stats_no_principal_fails_safe():
    req = Request({"type": "http", "method": "GET", "path": "/api/stats", "headers": [], "query_string": b""})
    assert not any(k in stats(req) for k in _SENSITIVE)


# --- Firm Dashboard route context ----------------------------------------------------------------

@pytest.mark.parametrize("principal", ORDINARY)
def test_dashboard_route_context_omits_metrics_for_ordinary(principal):
    resp = advisor_dashboard(_req(principal))
    assert resp.context["show_firm_metrics"] is False
    assert all(k not in resp.context["dashboard"] for k in _SENSITIVE)


def test_dashboard_route_context_includes_metrics_for_privileged():
    resp = advisor_dashboard(_req(PRIV))
    assert resp.context["show_firm_metrics"] is True
    assert all(k in resp.context["dashboard"] for k in _SENSITIVE)


# --- template omits the section (no "$0"), driven by the server flag ------------------------------

def _render(show, dashboard):
    from app.routes.dashboard import templates
    return templates.get_template("dashboard/index.html").render(
        request=None, show_firm_metrics=show, dashboard=dashboard, exception_summary=None)


def test_template_omits_firm_section_when_unauthorized():
    html = _render(False, {"people": 5, "households": 3, "accounts": 9, "open_tasks": 2,
                           "recent_activities": []})
    assert "Firm AUM" not in html and "Total account value" not in html
    assert "Cash waiting" not in html and "Largest household" not in html
    assert "$0" not in html                                             # never a misleading zero


def test_template_shows_firm_section_when_authorized():
    html = _render(True, {"people": 5, "households": 3, "accounts": 9, "open_tasks": 2,
                          "recent_activities": [], "cash_waiting": 10,
                          "largest_household": None, "largest_position": None,
                          "missing_beneficiaries": 0, "accounts_without_reviews": 0})
    # The non-AUM firm triage renders; AUM never does, for anyone.
    assert "Cash waiting" in html
    assert "AUM" not in html


# --- /wealth (second surface) + capability gate --------------------------------------------------

def test_require_capability_gate_denies_without_firm_metrics():
    dep = require_capability(FIRM_METRICS_CAPABILITY)
    assert dep(principal=PRIV) is PRIV
    for principal in ORDINARY:
        with pytest.raises(HTTPException) as exc:
            dep(principal=principal)
        assert exc.value.status_code == 403


def test_wealth_route_gated_on_firm_metrics_not_client_read():
    src = Path("app/routes/wealth.py").read_text(encoding="utf-8")
    assert 'require_capability("portfolio.firm_metrics")' in src
    assert 'require_capability("client.read")' not in src


# --- migration policy: firm metrics granted to administrator only ---------------------------------

def test_capability_is_seeded_but_granted_to_no_role():
    """Migration b4f1a207c9d3 revoked it from EVERY role — 360Plus shows AUM to nobody. The
    capability row is retained (reversible, and it still gates the non-AUM firm triage)."""
    with engine.connect() as c:
        cid = c.execute(select(capabilities.c.id)
                        .where(capabilities.c.code == FIRM_METRICS_CAPABILITY)).scalar()
        assert cid is not None, "the capability row is retained, not dropped"
        holders = c.execute(select(role_capabilities.c.role_id).where(
            role_capabilities.c.capability_id == cid)).all()
        assert holders == [], "no role may hold portfolio.firm_metrics"


# --- no username/email-specific logic -------------------------------------------------------------

def test_no_username_specific_logic_in_changed_files():
    forbidden = ("michael", "jessica", "lauren", "sarah", "angel")
    for path in ("app/routes/dashboard.py", "app/services/dashboard.py", "app/routes/wealth.py",
                 "app/templates/dashboard/index.html"):
        body = Path(path).read_text(encoding="utf-8").lower()
        for name in forbidden:
            assert name not in body, f"{name} hard-coded in {path}"
