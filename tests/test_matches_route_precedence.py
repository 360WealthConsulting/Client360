"""Route-precedence for /matches/* (identity-review vs. match-group).

`identity_review_router` serves the static `GET /matches/unresolved` and `matches_router`
serves the dynamic `GET /matches/{group_number}`. With an unconstrained `{group_number}`,
the dynamic route matched `/matches/unresolved` first and 422'd on int-coercion, hiding the
identity-review screen. Constraining it to `{group_number:int}` makes the static route win
for non-numeric segments while numeric ids still reach the match-group handler. This asserts
resolution at the routing layer (no HTTP client / DB needed)."""
from starlette.routing import Match

from app.main import app


def _winning_endpoint(path, method="GET"):
    """The endpoint the router would dispatch to for `path` — the first FULL match, in order."""
    scope = {"type": "http", "method": method, "path": path}
    for route in app.router.routes:
        if hasattr(route, "matches"):
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return route.endpoint
    return None


def test_unresolved_reaches_identity_review_handler():
    endpoint = _winning_endpoint("/matches/unresolved")
    assert endpoint is not None
    assert endpoint.__module__ == "app.routes.identity_review"
    assert endpoint.__name__ == "unresolved_contacts"


def test_numeric_group_reaches_match_group_handler():
    endpoint = _winning_endpoint("/matches/7")
    assert endpoint is not None
    assert endpoint.__module__ == "app.routes.matches"
    assert endpoint.__name__ == "match_group_page"


def test_non_numeric_group_does_not_match_the_int_route():
    # The dynamic match-group route must NOT capture a non-numeric segment any more.
    scope = {"type": "http", "method": "GET", "path": "/matches/unresolved"}
    for route in app.router.routes:
        if getattr(route, "endpoint", None) and route.endpoint.__module__ == "app.routes.matches" \
                and route.endpoint.__name__ == "match_group_page":
            match, _ = route.matches(scope)
            assert match != Match.FULL   # int-constrained route rejects "unresolved"
            break
