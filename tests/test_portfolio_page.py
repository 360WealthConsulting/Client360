"""Regression tests for the HTML portfolio page.

The portfolio nav used to open `/portfolio/search`, which returns raw JSON. These
tests pin the fix: a real HTML page at `/portfolio`, the JSON API preserved at
`/portfolio/search`, and navigation pointing at the HTML page.
"""
import pathlib

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


def test_portfolio_search_still_returns_json():
    from app.routes.portfolio import portfolio_search
    result = portfolio_search()
    assert isinstance(result, dict)
    assert isinstance(result.get("results"), list)


def test_navigation_points_to_html_portfolio_page():
    nav = pathlib.Path("app/templates/dashboard/index.html").read_text()
    assert 'href="/portfolio"' in nav
    assert 'href="/portfolio/search"' not in nav


def test_portfolio_page_links_to_client_detail():
    # The template links each row to the client/portfolio detail page.
    template = pathlib.Path("app/templates/portfolio/search.html").read_text()
    assert "/people/{{ r.id }}" in template
