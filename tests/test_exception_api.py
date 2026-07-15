"""Release 0.9.10 / Sprint 5.5 — API & staff console (Phase 6) tests.

The harness has no httpx, so routes are exercised by calling their functions
directly with an explicit Principal (bypassing the DI capability gate, which is
tested separately) and a hand-built Starlette Request for HTML renders. The engine
services still enforce capability and record scope on every call.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.security.models import Principal
from app.routes import exceptions as X
from app.services import exception_engine as ee

FULL = frozenset({"exception.read", "exception.write", "exception.resolve", "exception.compliance", "record.read_all"})


def _case():
    from tests.test_tax_intake import _case as intake_case
    user_id, person_id, household_id, portal, result = intake_case()
    return user_id, person_id, household_id, result["return_id"]


def _req(path="/exceptions", query=b""):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": query})


def _raise(u, r, p, h, *, code="FILING_REJECTED", principal, dedupe=None):
    return ee.raise_exception(code=code, actor_user_id=u, principal=principal,
                              tax_engagement_return_id=r, person_id=p, household_id=h,
                              dedupe_key=dedupe or f"{code}-{r}")


# --- HTML pages --------------------------------------------------------------

def test_console_and_detail_return_200_html():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    console = X.console(_req(), principal=admin)
    assert console.status_code == 200 and "text/html" in console.headers["content-type"]
    assert b"Exception console" in console.body
    detail = X.console_detail(ex["id"], _req(), principal=admin)
    assert detail.status_code == 200 and "text/html" in detail.headers["content-type"]
    assert b"Event timeline" in detail.body and b"/people/" in detail.body  # client link


def test_console_empty_and_invalid_filter_states():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    empty = X.console(_req(), principal=admin, status="cancelled", return_id="99999999")
    assert empty.status_code == 200 and b"No exceptions match" in empty.body
    invalid = X.console(_req(), principal=admin, domain="microsoft")
    assert invalid.status_code == 400 and b"not available" in invalid.body


# --- JSON API ----------------------------------------------------------------

def test_json_list_detail_events_and_metrics():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    listing = X.api_list(principal=admin)
    assert any(row["id"] == ex["id"] for row in listing["results"])
    detail = X.api_detail(ex["id"], principal=admin)
    assert detail["id"] == ex["id"] and detail["events"][0]["event_type"] == "opened"
    events = X.api_events(ex["id"], principal=admin)["events"]
    assert [e["event_type"] for e in events] == ["opened"]
    assert X.api_metrics(principal=admin)["open"] >= 1


def test_create_and_every_mutation_endpoint():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    created = X.api_create(X.CreateException(code="CLIENT_UNRESPONSIVE", tax_engagement_return_id=r,
                                             person_id=p, household_id=h, dedupe_key=f"m-{r}"), principal=admin)
    eid = created["id"]
    assert X.api_acknowledge(eid, principal=admin)["status"] == "acknowledged"
    assert X.api_start(eid, principal=admin)["status"] == "in_progress"
    assert X.api_waiting(eid, principal=admin)["status"] == "waiting"
    assert X.api_start(eid, principal=admin)["status"] == "in_progress"
    assert X.api_escalate(eid, principal=admin)["status"] == "escalated"
    assert X.api_comment(eid, X.CommentBody(body="looking into it"), principal=admin)["id"] == eid
    assert X.api_resolve(eid, X.ResolveBody(resolution_code="handled"), principal=admin)["status"] == "resolved"
    assert X.api_reopen(eid, principal=admin)["status"] == "reopened"
    assert X.api_cancel(eid, principal=admin)["status"] == "cancelled"


def test_assign_reassign_remove_endpoints():
    from app.db import engine, teams
    from sqlalchemy import select
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    with engine.connect() as c:
        team_id = c.scalar(select(teams.c.id).where(teams.c.code == "operations"))
    aid = X.api_assign(ex["id"], X.AssignBody(assignment_role="primary", user_id=u), principal=admin)["assignment_id"]
    new_id = X.api_reassign(aid, X.ReassignBody(team_id=team_id), principal=admin)["assignment_id"]
    assert X.api_remove(new_id, principal=admin)["status"] == "removed"


# --- authorization -----------------------------------------------------------

def test_capability_dependency_rejects_missing_capability():
    dep = X.require_capability("exception.read")
    with pytest.raises(HTTPException) as exc:
        dep(Principal(1, "x@e.com", "X", frozenset()))  # no capability
    assert exc.value.status_code == 403


def test_out_of_scope_detail_returns_404():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    outsider = Principal(9_800_001, "x@e.com", "X", frozenset({"exception.read", "exception.write"}))
    with pytest.raises(HTTPException) as g:
        X.api_detail(ex["id"], principal=outsider)
    assert g.value.status_code == 404  # hide existence
    with pytest.raises(HTTPException) as u2:
        X.console_detail(ex["id"], _req(), principal=outsider)
    assert u2.value.status_code == 404


def test_record_scope_filters_list():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    outsider = Principal(9_800_002, "x@e.com", "X", frozenset({"exception.read"}))
    assert all(row["id"] != ex["id"] for row in X.api_list(principal=outsider)["results"])


def test_blocker_and_compliance_resolution_segregation():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    # non-resolver has write + scope (read_all) but not exception.resolve / .compliance
    non_resolver = Principal(u, "n@e.com", "N", frozenset({"exception.read", "exception.write", "record.read_all"}))
    blocker = _raise(u, r, p, h, principal=admin, code="FILING_REJECTED", dedupe=f"blk-{r}")
    X.api_acknowledge(blocker["id"], principal=admin); X.api_start(blocker["id"], principal=admin)
    with pytest.raises(HTTPException) as e1:
        X.api_resolve(blocker["id"], X.ResolveBody(resolution_code="x"), principal=non_resolver)
    assert e1.value.status_code == 403
    comp = _raise(u, r, p, h, principal=admin, code="COMPLIANCE_SOD_VIOLATION", dedupe=f"cmp-{r}")
    X.api_acknowledge(comp["id"], principal=admin); X.api_start(comp["id"], principal=admin)
    with pytest.raises(HTTPException) as e2:
        X.api_resolve(comp["id"], X.ResolveBody(resolution_code="x"),
                      principal=Principal(u, "c@e.com", "C", frozenset({"exception.read", "exception.write", "exception.resolve", "record.read_all"})))
    assert e2.value.status_code == 403  # needs exception.compliance


def test_readonly_principal_cannot_mutate():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    readonly = Principal(u, "ro@e.com", "RO", frozenset({"exception.read", "record.read_all"}))
    with pytest.raises(HTTPException) as e:
        X.api_acknowledge(ex["id"], principal=readonly)
    assert e.value.status_code == 403


# --- conflict handling -------------------------------------------------------

def test_stale_action_and_invalid_transition_conflicts():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    ex = _raise(u, r, p, h, principal=admin)
    with pytest.raises(HTTPException) as stale:
        X.api_acknowledge(ex["id"], X.ActionBody(expected_status="in_progress"), principal=admin)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        X.api_resolve(ex["id"], X.ResolveBody(resolution_code="x"), principal=admin)  # open → resolved illegal
    assert invalid.value.status_code == 409


# --- filters / parity / navigation ------------------------------------------

def test_filter_combinations_and_event_ordering():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    _raise(u, r, p, h, principal=admin, code="FILING_REJECTED", dedupe=f"blk-{r}")     # blocker/filing
    _raise(u, r, p, h, principal=admin, code="CLIENT_UNRESPONSIVE", dedupe=f"cli-{r}")  # medium/client
    blk = X.api_list(principal=admin, severity="blocker", category="filing")["results"]
    assert blk and all(x["severity"] == "blocker" and x["category"] == "filing" for x in blk)
    cli = X.api_list(principal=admin, category="client")["results"]
    assert cli and all(x["category"] == "client" for x in cli)


def test_api_ui_result_parity_and_no_raw_json_in_nav():
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    for code in ("FILING_REJECTED", "CLIENT_UNRESPONSIVE", "COMPLIANCE_SOD_VIOLATION"):
        _raise(u, r, p, h, principal=admin, code=code, dedupe=f"{code}-{r}")
    api_ids = {row["id"] for row in X.api_list(principal=admin)["results"]}
    html = X.console(_req(), principal=admin).body.decode()
    ui_ids = {int(m) for m in re.findall(r'/exceptions/(\d+)"', html)}
    assert api_ids == ui_ids  # API/UI parity
    # nav links to the HTML console, never the raw JSON API
    assert 'href="/exceptions"' in html and 'href="/api/v1/exceptions"' not in html


def test_navigation_link_is_capability_gated():
    base = pathlib.Path("app/templates/base.html").read_text()
    assert "principal.can('exception.read')" in base and 'href="/exceptions"' in base
    # rendered for a capable principal, the nav link is present
    u, p, h, r = _case()
    admin = Principal(u, "a@e.com", "A", FULL)
    html = X.console(_req(), principal=admin).body.decode()
    assert '<a href="/exceptions">Exceptions</a>' in html
