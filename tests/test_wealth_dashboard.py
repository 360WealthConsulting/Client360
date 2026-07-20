"""Tests for the advisor-facing Wealth dashboard (Phase A, PR-1).

A read-only firm-wide overview at `/wealth` that reuses the existing
`get_firm_portfolio_metrics()`. It is gated exactly like `/portfolio`:
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
    for label in ("Firm AUM", "Cash", "Largest household", "Accounts without a review"):
        assert label in body


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
