"""Release 0.9.10 / Sprint 5.5 — Portal "Action Needed" (Phase 7) tests.

Client-facing surface for client-visible tax exceptions. Routes are exercised by
calling their functions directly with an explicit ``PortalPrincipal`` (the portal
auth gate is the middleware, tested elsewhere) and a hand-built Starlette Request
for HTML renders. The canonical Exception Engine projection enforces the
client-visible allowlist and portal scope on every call.
"""
import pathlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.portal import service as svc
from app.portal.service import PortalPrincipal
from app.routes import portal as P
from app.services import exception_engine as ee

# Fields the portal is ever allowed to expose for an action item.
ALLOWED_FIELDS = frozenset({"id", "title", "explanation", "priority", "status",
    "resolved", "due_date", "tax_year", "return_label", "action_label", "action_url"})
# Internal fields/terms that must never leak to a portal account.
FORBIDDEN_FIELDS = frozenset({"code", "severity", "category", "owner_user_id",
    "owner_team_id", "escalation_level", "dedupe_key", "sla_state", "description",
    "events", "resolution_code", "resolution_notes", "exception_type_id"})

CLIENT_CODES = ["DOC_MISSING_OVERDUE", "CLIENT_ENGAGEMENT_UNSIGNED",
                "CLIENT_EFILE_AUTH_MISSING", "CLIENT_UNRESPONSIVE", "CLIENT_INFO_INCONSISTENT"]
INTERNAL_CODES = ["COMPLIANCE_SOD_VIOLATION", "WORKFLOW_STUCK", "OPS_JOB_FAILURE",
                  "FILING_REJECTED", "DOC_AMBIGUOUS_MATCH"]


def _case():
    from tests.test_tax_intake import _case as intake_case
    user_id, person_id, household_id, portal, result = intake_case()
    return user_id, person_id, household_id, portal, result["return_id"]


def _raise(code, *, u, r, p, h):
    # principal=None → system caller (bypasses capability/scope), like the detectors.
    return ee.raise_exception(code=code, actor_user_id=u, source="system",
                              tax_engagement_return_id=r, person_id=p, household_id=h,
                              dedupe_key=f"{code}-{r}")


def _req(path="/portal/action-needed"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


# --- filtering / exclusion ---------------------------------------------------

def test_only_client_visible_exceptions_are_exposed():
    u, p, h, portal, r = _case()
    for code in CLIENT_CODES + INTERNAL_CODES:
        _raise(code, u=u, r=r, p=p, h=h)
    items = svc.client_action_needed(portal)
    # every client-visible code surfaces...
    assert len(items) == len(CLIENT_CODES)
    # ...and no internal exception leaks through the allowlist.
    titles = {i["title"] for i in items}
    assert "Upload a requested document" in titles
    assert all(i["title"] in {ee._CLIENT_PRESENTATION[c]["title"] for c in CLIENT_CODES} for i in items)


def test_internal_exceptions_are_never_actionable_by_clients():
    u, p, h, portal, r = _case()
    internal = _raise("COMPLIANCE_SOD_VIOLATION", u=u, r=r, p=p, h=h)
    # not in the list, and not fetchable by id (hidden as not-found → client cannot act).
    assert all(i["id"] != internal["id"] for i in svc.client_action_needed(portal))
    with pytest.raises(ee.ExceptionNotFoundError):
        svc.client_action_detail(portal, internal["id"])


# --- portal-safe projection --------------------------------------------------

def test_projection_exposes_only_client_safe_fields():
    u, p, h, portal, r = _case()
    _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    item = svc.client_action_needed(portal)[0]
    assert set(item) <= ALLOWED_FIELDS
    assert not (set(item) & FORBIDDEN_FIELDS)
    assert item["tax_year"] == 2026 and item["return_label"].startswith("2026 ")


def test_action_links_point_at_existing_portal_actions():
    u, p, h, portal, r = _case()
    _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    _raise("CLIENT_INFO_INCONSISTENT", u=u, r=r, p=p, h=h)
    by_title = {i["title"]: i for i in svc.client_action_needed(portal)}
    assert by_title["Upload a requested document"]["action_url"] == "/portal/requests"
    assert by_title["Confirm a few details"]["action_url"] == "/portal/messages"


def test_no_event_history_or_staff_detail_leaks():
    u, p, h, portal, r = _case()
    ex = _raise("CLIENT_UNRESPONSIVE", u=u, r=r, p=p, h=h)
    detail = svc.client_action_detail(portal, ex["id"])
    assert "events" not in detail and set(detail) <= ALLOWED_FIELDS
    # there is no portal endpoint that returns an exception event ledger.
    assert not any(getattr(fn, "__name__", "") == "api_portal_exception_events"
                   for fn in P.router.routes)


# --- scope / isolation -------------------------------------------------------

def test_out_of_scope_exception_is_hidden_and_returns_404():
    u, p, h, portal, r = _case()
    ex = _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    # a portal account for an unrelated person/household
    _, p2, h2, other, _ = _case()
    assert all(i["id"] != ex["id"] for i in svc.client_action_needed(other))
    with pytest.raises(HTTPException) as exc:
        P.api_portal_exception(ex["id"], principal=other)
    assert exc.value.status_code == 404
    # the rightful owner still sees it
    assert P.api_portal_exception(ex["id"], principal=portal)["id"] == ex["id"]


def test_client_supplied_id_is_scope_validated():
    u, p, h, portal, r = _case()
    with pytest.raises(HTTPException) as exc:
        P.api_portal_exception(999_999_999, principal=portal)
    assert exc.value.status_code == 404


# --- resolution reflection ---------------------------------------------------

def test_resolved_exception_drops_from_active_view():
    u, p, h, portal, r = _case()
    ex = _raise("DOC_MISSING_OVERDUE", u=u, r=r, p=p, h=h)
    assert any(i["id"] == ex["id"] for i in svc.client_action_needed(portal))
    # resolution happens through the engine (real underlying action / detector), not the portal.
    ee.begin_work(ex["id"], principal=None, actor_user_id=u)
    ee.resolve(ex["id"], "auto_source_cleared", principal=None, actor_user_id=u)
    assert all(i["id"] != ex["id"] for i in svc.client_action_needed(portal))  # removed from active
    completed = svc.client_action_detail(portal, ex["id"])  # include_resolved → completion
    assert completed["resolved"] is True and completed["status"] == "Completed"


def test_client_cannot_directly_resolve_exceptions_via_portal():
    # No portal route mutates exceptions; the only portal exception routes are GET reads.
    exc_routes = [route for route in P.router.routes
                  if "portal/exceptions" in getattr(route, "path", "")]
    assert exc_routes  # the read routes exist
    assert all(route.methods <= {"GET", "HEAD"} for route in exc_routes)


# --- API / HTML surface ------------------------------------------------------

def test_api_list_and_html_render_and_parity():
    u, p, h, portal, r = _case()
    for code in CLIENT_CODES:
        _raise(code, u=u, r=r, p=p, h=h)
    api = P.api_portal_exceptions(principal=portal)["action_items"]
    dash = svc.dashboard(portal)["action_items"]
    assert len(api) == len(dash) == len(CLIENT_CODES)  # dashboard/detail parity
    html = P.portal_action_needed(_req(), principal=portal)
    assert html.status_code == 200 and "text/html" in html.headers["content-type"]
    body = html.body.decode()
    assert "Action Needed" in body and "Upload a requested document" in body
    assert "width=device-width" in body  # responsive/mobile viewport (inherited shell)
    # no internal terminology in the rendered client page
    for term in ("dedupe", "escalation", "exception.write", "COMPLIANCE_", "DOC_MISSING_OVERDUE"):
        assert term not in body


def test_empty_state_render():
    u, p, h, portal, r = _case()
    html = P.portal_action_needed(_req(), principal=portal)
    assert html.status_code == 200
    assert "all caught up" in html.body.decode()


def test_navigation_link_present():
    base = pathlib.Path("app/templates/portal/base.html").read_text()
    assert 'href="/portal/action-needed"' in base
    u, p, h, portal, r = _case()
    body = P.portal_action_needed(_req(), principal=portal).body.decode()
    assert 'href="/portal/action-needed"' in body


def test_client_visible_policy_is_single_source_of_truth():
    from app.services import exception_sla
    # the SLA notifier's client-facing set is exactly the portal allowlist.
    assert exception_sla.CLIENT_FACING_CODES is ee.CLIENT_VISIBLE_CODES
    assert set(ee._CLIENT_PRESENTATION) == set(ee.CLIENT_VISIBLE_CODES)
