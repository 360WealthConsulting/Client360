from app.security.middleware import FIRM_WIDE_COLLECTION, RULES


def capability_for(path):
    return next((code for pattern, code in RULES if pattern.search(path)), None)


def test_integrated_relationship_routes_require_client_capability():
    assert capability_for("/relationships/search") == "client.read"
    assert capability_for("/api/relationships/search") == "client.read"
    assert capability_for("/relationship-entities/12") == "client.read"


def test_integrated_portfolio_routes_require_client_capability():
    assert capability_for("/portfolio/search") == "client.read"
    assert capability_for("/portfolio/import/schwab") == "client.read"


def test_global_intelligence_surfaces_require_firm_wide_record_scope():
    for path in (
        "/relationships/search",
        "/api/relationships/search",
        "/relationship-entities/12",
        "/portfolio/search",
        "/portfolio/import/schwab",
    ):
        assert FIRM_WIDE_COLLECTION.match(path), path
