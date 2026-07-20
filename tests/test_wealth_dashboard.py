"""Tests for the advisor-facing Wealth dashboard at `/wealth`.

A lean book-triage view fed by `get_wealth_dashboard()` (which reuses
`get_firm_portfolio_metrics()`): a compact Firm AUM / Firm Cash summary strip and
the three Advisor attention worklists. Gated exactly like `/portfolio`:
`client.read` (middleware RULE + route dependency) plus `record.read_all`
(FIRM_WIDE_COLLECTION). No new capability, schema, or business policy.
"""
import re

from starlette.requests import Request

from app.security.models import Principal


def _request(path="/wealth", query=b""):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "headers": [], "query_string": query,
    })


def _capability_for(path):
    from app.security.middleware import RULES
    return next((code for pattern, code in RULES if pattern.search(path)), None)


def _nav_groups(principal):
    """Render base.html and return {group-label: group-html-block}."""
    from app.templating import templates
    html = templates.env.get_template("base.html").render(
        request=_request(), principal=principal
    )
    groups = {}
    for block in html.split('<div class="nav-group">')[1:]:
        label = re.search(r'<div class="label">([^<]+)</div>', block)
        if label:
            groups[label.group(1).strip()] = block
    return groups


def _admin():
    return Principal(1, "admin@example.com", "Admin", frozenset({"record.read_all", "client.read"}))


def test_wealth_dashboard_renders_html_for_authorized_admin():
    from app.routes.wealth import wealth_dashboard
    response = wealth_dashboard(_request(), principal=_admin())
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.body.decode()
    assert "Wealth dashboard" in body
    # Compact firm strip + the attention worklists remain.
    for label in ("Firm AUM", "Firm Cash", "Advisor attention",
                  "Missing beneficiaries", "High cash", "Accounts needing review"):
        assert label in body
    # Attention tiles deep-link into the matching portfolio worklists.
    assert 'href="/portfolio?missing_beneficiary=true"' in body
    assert 'href="/portfolio?high_cash=true"' in body
    # Removed noise: the Firm-overview count grid, the recent-activity changelog,
    # and the quick-action nav (section labels that never appear in the sidebar).
    for gone in ("Firm overview", "Recent activity", "Latest imports",
                 "New households", "Recently updated accounts", "Quick actions"):
        assert gone not in body


def test_wealth_path_requires_client_read_capability():
    # Middleware RULE gates /wealth on client.read, matching /portfolio.
    assert _capability_for("/wealth") == "client.read"


def test_wealth_is_a_firm_wide_collection():
    # FIRM_WIDE_COLLECTION additionally enforces record.read_all on /wealth.
    from app.security.middleware import FIRM_WIDE_COLLECTION
    assert FIRM_WIDE_COLLECTION.match("/wealth")


def test_wealth_group_shows_dashboard_and_portfolio_for_admin():
    groups = _nav_groups(_admin())
    assert "Wealth" in groups
    assert 'href="/wealth"' in groups["Wealth"]
    assert 'href="/portfolio"' in groups["Wealth"]


def test_unauthorized_user_sees_neither_wealth_section_nor_dashboard():
    # Advisor: has client.read but NOT record.read_all -> firm_client is false.
    advisor = Principal(2, "advisor@example.com", "Advisor", frozenset({"client.read"}))
    groups = _nav_groups(advisor)
    assert "Wealth" not in groups
    from app.templating import templates
    nav = templates.env.get_template("base.html").render(
        request=_request(), principal=advisor
    )
    assert 'href="/wealth"' not in nav


def test_get_wealth_dashboard_reuses_metrics_and_counts_high_cash():
    # get_wealth_dashboard reuses get_firm_portfolio_metrics and adds a high-cash
    # count. Assert the reused keys plus that a high-cash account is counted.
    import uuid
    from decimal import Decimal

    from sqlalchemy import delete

    from app.db import accounts, engine, households
    from app.services.portfolio import get_wealth_dashboard

    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"Dash HH {tag}").returning(households.c.id)).scalar_one()
        # 60% cash -> counts toward high_cash.
        acct = c.execute(accounts.insert().values(
            household_id=hid, custodian="Schwab", account_number=f"DASH-{tag}",
            status="open", total_value=Decimal("100000"), cash_value=Decimal("60000"),
        ).returning(accounts.c.id)).scalar_one()
    try:
        d = get_wealth_dashboard()
        for key in ("firm_aum", "cash_waiting", "missing_beneficiaries", "accounts_without_reviews"):
            assert key in d
        assert d["high_cash_count"] >= 1  # our 60%-cash account
        # Removed reads are gone from the payload.
        for gone in ("household_count", "account_count", "recent_imports",
                     "new_households", "recently_updated_accounts"):
            assert gone not in d
    finally:
        with engine.begin() as c:
            c.execute(delete(accounts).where(accounts.c.id == acct))
            c.execute(delete(households).where(households.c.id == hid))
