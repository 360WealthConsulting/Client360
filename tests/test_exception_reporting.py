"""Release 0.9.10 / Sprint 5.5 — Exception dashboards & reporting (Phase 8) tests.

Reporting is built on ``exception_engine.list_exceptions(principal)``, so record-scope
authorization is applied *before* aggregation. Tests use a record-scoped principal
(assigned to exactly one fresh return via the intake case) so counts are deterministic
regardless of what other tests leave in the shared database.
"""
from starlette.requests import Request

import pytest

from app.security.models import Principal
from app.routes import exceptions as X
from app.services import exception_engine as ee
from app.services import exception_reporting as er


def _case():
    from tests.test_tax_intake import _case as intake_case
    u, p, h, portal, result = intake_case()
    return u, p, h, result["return_id"]


def _scoped(u, caps=("exception.read", "exception.write")):
    # `u` is assigned primary on its own return by the intake case → record-scoped.
    return Principal(u, f"u{u}@e.com", "U", frozenset(caps))


def _raise(code, *, u, r, p, h, principal=None, dedupe=None):
    return ee.raise_exception(code=code, actor_user_id=u, principal=principal, source="system",
                              tax_engagement_return_id=r, person_id=p, household_id=h,
                              dedupe_key=dedupe or f"{code}-{r}")


def _req(path="/exceptions/reporting"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


# --- authorization before aggregation ----------------------------------------

def test_authorization_filtering_precedes_aggregation():
    u, p, h, r = _case()
    ex = _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    mine = er.exception_report(_scoped(u))
    assert mine["summary"]["open"] >= 1
    assert mine["by_return"].get(f"return:{r}") == 1
    # a principal with no assignment to this return aggregates none of it.
    outsider = Principal(9_900_001, "x@e.com", "X", frozenset({"exception.read"}))
    theirs = er.exception_report(outsider)
    assert f"return:{r}" not in theirs["by_return"]
    assert all(f"person:{p}" != k for k in theirs["by_client"])


# --- aggregation accuracy (real data only) -----------------------------------

def test_summary_and_breakdowns_are_accurate():
    u, p, h, r = _case()
    for code in ("FILING_REJECTED", "CLIENT_EFILE_AUTH_MISSING", "CLIENT_UNRESPONSIVE",
                 "COMPLIANCE_SOD_VIOLATION"):
        _raise(code, u=u, r=r, p=p, h=h)
    rep = er.exception_report(_scoped(u))
    s = rep["summary"]
    assert s["open"] == 4
    assert s["blocker"] >= 1 and s["high"] >= 1
    assert s["compliance"] == 1 and rep["compliance_open"] == 1
    assert s["unassigned"] == 4  # none have an owner yet
    assert set(rep["by_category"]) == {"filing", "client", "compliance"}
    assert rep["aging"]["lt_1d"] == 4  # all just opened
    assert rep["escalation_distribution"] == {"0": 4}
    assert rep["by_client"][f"person:{p}"] == 4 and rep["by_return"][f"return:{r}"] == 4


def test_throughput_reopen_and_sla_from_real_events():
    u, p, h, r = _case()
    scoped = _scoped(u)
    ex = _raise("CLIENT_UNRESPONSIVE", u=u, r=r, p=p, h=h)
    ee.acknowledge(ex["id"], principal=scoped, actor_user_id=u)
    ee.begin_work(ex["id"], principal=scoped, actor_user_id=u)
    ee.resolve(ex["id"], "handled", principal=scoped, actor_user_id=u)
    rep = er.exception_report(scoped)
    assert rep["throughput"]["acknowledged_count"] >= 1 and rep["throughput"]["resolved_count"] >= 1
    assert rep["throughput"]["mean_time_to_acknowledge_seconds"] is not None
    assert rep["throughput"]["mean_time_to_resolve_seconds"] is not None
    assert rep["sla"]["resolved_with_sla"] >= 1 and rep["sla"]["sla_compliance_rate"] == 1.0  # resolved before due
    ee.reopen(ex["id"], principal=scoped, actor_user_id=u)
    rep2 = er.exception_report(scoped)
    assert rep2["reopen"]["reopened_exceptions"] >= 1 and rep2["reopen"]["reopen_rate"] > 0


def test_trend_is_derived_from_real_timestamps_not_fabricated():
    u, p, h, r = _case()
    for code in ("DOC_MISSING_OVERDUE", "CLIENT_UNRESPONSIVE"):
        _raise(code, u=u, r=r, p=p, h=h)
    rep = er.exception_report(_scoped(u))
    # every trend point is a real day; opened over the window equals what we raised.
    assert sum(pt["opened"] for pt in rep["trend"]) == rep["summary"]["open"] == 2
    assert all(pt["opened"] >= 0 and pt["resolved"] >= 0 for pt in rep["trend"])


# --- audiences ---------------------------------------------------------------

def test_default_audience_by_role_and_panel_selection():
    mgmt = Principal(1, "m@e.com", "M", frozenset({"exception.read", "record.write_all"}))
    comp = Principal(2, "c@e.com", "C", frozenset({"exception.read", "exception.compliance"}))
    ops = Principal(3, "o@e.com", "O", frozenset({"exception.read", "capacity.read"}))
    tax = Principal(4, "t@e.com", "T", frozenset({"exception.read", "tax.read"}))
    adv = Principal(5, "a@e.com", "A", frozenset({"exception.read"}))
    assert er.default_audience(mgmt) == "management"
    assert er.default_audience(comp) == "compliance"
    assert er.default_audience(ops) == "operations"
    assert er.default_audience(tax) == "tax"
    assert er.default_audience(adv) == "advisor"
    # explicit audience selects that panel set; invalid falls back to default.
    assert er.exception_report(comp, audience="compliance")["panels"] == er.AUDIENCE_PANELS["compliance"]
    assert er.exception_report(adv, audience="bogus")["audience"] == "advisor"


# --- dashboard embedding gate ------------------------------------------------

def test_dashboard_summary_is_capability_gated():
    assert er.dashboard_summary(None) is None
    nocap = Principal(9_900_002, "n@e.com", "N", frozenset())
    assert er.dashboard_summary(nocap) is None
    u, p, h, r = _case()
    _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    summary = er.dashboard_summary(_scoped(u), audience="operations")
    assert summary and summary["summary"]["open"] >= 1


# --- routes ------------------------------------------------------------------

def test_report_api_and_html_render():
    u, p, h, r = _case()
    _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    scoped = _scoped(u)
    api = X.api_report(principal=scoped)
    assert "summary" in api and api["summary"]["open"] >= 1
    # trend_days is clamped
    assert X.api_report(trend_days=1000, principal=scoped)["trend_days"] == 120
    html = X.reporting(_req(), principal=scoped)
    assert html.status_code == 200 and "text/html" in html.headers["content-type"]
    assert b"Exception dashboard" in html.body


def test_reporting_route_registered_before_id_route():
    # "/exceptions/reporting" and "/api/v1/exceptions/report" must precede the
    # {exception_id} int routes so the words are not captured as ids.
    paths = [getattr(r, "path", "") for r in X.router.routes]
    assert paths.index("/exceptions/reporting") < paths.index("/exceptions/{exception_id}")
    assert paths.index("/api/v1/exceptions/report") < paths.index("/api/v1/exceptions/{exception_id}")
