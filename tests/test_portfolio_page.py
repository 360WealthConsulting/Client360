"""Regression tests for the HTML portfolio page.

The portfolio nav used to open `/portfolio/search`, which returns raw JSON. These
tests pin the fix: a real HTML page at `/portfolio`, the JSON API preserved at
`/portfolio/search`, and navigation pointing at the HTML page.
"""
import pathlib
import re

from starlette.requests import Request

from app.security.models import Principal


def _request(path="/portfolio", query=b""):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "headers": [], "query_string": query,
    })


def test_portfolio_html_page_returns_200_and_html():
    from app.routes.portfolio import portfolio_page
    principal = Principal(1, "demo@example.com", "Demo", frozenset({"client.read"}))
    response = portfolio_page(_request(), principal=principal)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.body.decode()
    # readable page with the required columns, not raw JSON
    assert "Portfolio search" in body
    for column in ("Client", "AUM", "Cash"):
        assert column in body
    # Simplified filters: keep search + the two worklist filters; the advanced
    # min-AUM / registration inputs were removed to reduce noise.
    assert 'name="q"' in body
    assert 'name="high_cash"' in body
    assert 'name="missing_beneficiary"' in body
    assert 'name="min_aum"' not in body
    assert 'name="registration"' not in body
    # Design-system alignment (Phase C.1 PR-1): shared page-head, section title,
    # data table with right-aligned numeric columns, rowlink action.
    assert 'class="page-head"' in body          # ui.page_head macro
    assert 'class="section-title"' in body
    assert 'class="data"' in body               # table.data, not a bare table
    assert 'class="num"' in body                # right-aligned AUM/Cash
    assert "rowlink" in body
    # Legacy styling borrowed from the Work / Tax modules is gone.
    assert 'class="filters"' not in body
    assert 'class="panel"' not in body
    # Terminology: "AUM", not "assets under management".
    assert "assets under management" not in body


def test_portfolio_search_still_returns_json():
    from app.routes.portfolio import portfolio_search
    result = portfolio_search()
    assert isinstance(result, dict)
    assert isinstance(result.get("results"), list)


def test_navigation_points_to_html_portfolio_page():
    # Navigation now lives in the application shell (base.html). It must point at
    # the HTML page (/portfolio), never the raw-JSON API (/portfolio/search).
    from app.templating import templates
    # Portfolio is a firm-wide collection screen: requires client.read + record.read_all
    principal = Principal(1, "a@example.com", "A", frozenset({"record.read_all", "client.read"}))
    nav = templates.env.get_template("base.html").render(request=_request(), principal=principal)
    assert 'href="/portfolio"' in nav
    assert 'href="/portfolio/search"' not in nav


def test_portfolio_page_links_to_client_detail():
    # The template links each row to the client/portfolio detail page.
    template = pathlib.Path("app/templates/portfolio/search.html").read_text()
    assert "/people/{{ r.id }}" in template


# --- Sidebar section: Portfolio lives under a dedicated "Wealth" group -------
#
# Portfolio moved out of the "Clients" group into its own top-level "Wealth"
# section. The move preserves the capability gate (firm_client = client.read AND
# record.read_all), so the section is admin-only exactly as the link was before.

def _nav_groups(principal, path="/portfolio"):
    """Render base.html and return {group-label: group-html-block} for each
    *visible* nav group. Each group is `<div class="nav-group"><div class="label">
    LABEL</div> ... </div>`; splitting on the group delimiter bounds each block to
    its own items (up to the next group), so membership can be asserted per group.
    """
    from app.templating import templates
    html = templates.env.get_template("base.html").render(
        request=_request(path=path), principal=principal
    )
    groups = {}
    for block in html.split('<div class="nav-group">')[1:]:
        label = re.search(r'<div class="label">([^<]+)</div>', block)
        if label:
            groups[label.group(1).strip()] = block
    return groups


def _admin():
    # Firm-wide reader: satisfies both client.read and record.read_all.
    return Principal(1, "admin@example.com", "Admin", frozenset({"record.read_all", "client.read"}))


def test_wealth_section_appears_for_authorized_admin():
    groups = _nav_groups(_admin())
    assert "Wealth" in groups


def test_portfolio_appears_beneath_wealth():
    groups = _nav_groups(_admin())
    assert "Wealth" in groups
    assert 'href="/portfolio"' in groups["Wealth"]
    assert "Portfolio" in groups["Wealth"]


def test_portfolio_no_longer_beneath_clients():
    groups = _nav_groups(_admin())
    assert "Clients" in groups
    assert 'href="/portfolio"' not in groups["Clients"]
    # The other Clients items are untouched by the move.
    for href in ('href="/households"', 'href="/people"', 'href="/relationships/search"'):
        assert href in groups["Clients"]


def test_unauthorized_user_does_not_see_wealth_section():
    # Advisor persona: has client.read but NOT record.read_all -> firm_client is
    # false, so neither the Wealth heading nor the Portfolio link is rendered.
    advisor = Principal(2, "advisor@example.com", "Advisor", frozenset({"client.read"}))
    groups = _nav_groups(advisor)
    assert "Wealth" not in groups
    from app.templating import templates
    nav = templates.env.get_template("base.html").render(
        request=_request(), principal=advisor
    )
    assert 'href="/portfolio"' not in nav


def test_portfolio_active_state_covers_nested_urls():
    # match="/portfolio" -> path.startswith() keeps the item active on nested URLs.
    nested = _nav_groups(_admin(), path="/portfolio/search")
    assert 'href="/portfolio"' in nested["Wealth"]
    assert 'aria-current="page"' in nested["Wealth"]
