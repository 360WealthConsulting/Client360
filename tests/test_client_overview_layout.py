"""The reorganized client Overview: five blocks, in one order, with nothing empty on show.

WHAT THIS FILE IS PROTECTING

The Overview used to be a flat grid of ten cards. On a healthy client most of them read "nothing
here", so the first screenful was mostly reassurance and the things that actually needed doing
were scattered across five separate boxes. The rebuilt page is:

  1. Client summary — who this person is and how to reach them
  2. Needs attention — ONE list: reviews, overdue tasks, missing tax items, compliance issues and
     unidentified documents, folded together
  3. Financial snapshot — investments and the tax position
  4. Recent documents — five, then a link to the tab that holds them all
  5. Recent activity — the same shape

These tests pin the order, the five-item ceilings, the fact that an empty block disappears rather
than rendering a large card saying nothing, and that no machine timestamp reaches the page.

NO REAL DATA. The workspace skeleton comes from a synthetic seeded person, and every row fed into
it is invented here.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.db import engine, people
from app.security.models import Principal
from app.services.client360.sections import OVERVIEW_LIST_LIMIT, _needs_attention
from app.services.client360.service import get_workspace
from app.templating import install_filters, install_globals_on_all_templates

STAFF = Principal(1, "staff@example.test", "Staff", frozenset({
    "client.read", "client.write", "tax.read", "record.read_all", "timeline.read",
    "documents.view", "tasks.read", "compliance.read", "communications.view",
}))
READER = Principal(2, "reader@example.test", "Reader", frozenset({
    "client.read", "record.read_all"}))

BLOCK = re.compile(r'<section class="c360-overview-block">\s*'
                   r'(?:<div class="c360-block-head">\s*)?<h2 class="section-title">([^<]+)</h2>')
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


@pytest.fixture(scope="module", autouse=True)
def _filters():
    install_globals_on_all_templates()


@pytest.fixture(scope="module")
def templates():
    t = Jinja2Templates(directory="app/templates")
    install_filters(t)
    return t


@pytest.fixture(scope="module")
def person_id() -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            full_name=f"Overview Subject {uuid.uuid4().hex[:10]}", active=True,
            primary_email=f"overview-{uuid.uuid4().hex[:8]}@example.test",
            primary_phone="(540) 555-0120", city="Roanoke", state="VA",
            birth_date=date(1972, 11, 3)).returning(people.c.id)).scalar_one()


def _request(path: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "session": {}, "app": None,
        "client": ("127.0.0.1", 1), "server": ("testserver", 80), "scheme": "http",
    })


def _render(templates, person_id, principal=STAFF, *, dashboard=None, profile=None):
    """Render the real Overview over a real workspace skeleton with a chosen dashboard payload."""
    ws = get_workspace(principal, person_id=person_id)
    if dashboard is not None:
        ws["sections"]["dashboard"] = dashboard
    ws["profile"] = profile
    return templates.get_template("client360/workspace.html").render(
        request=_request(f"/client/{person_id}"), ws=ws, principal=principal,
        active_tab="dashboard")


def _blocks(html) -> list[str]:
    return BLOCK.findall(html)


def _profile():
    """The shape ``profile_overview`` returns, with enough on file to show both halves."""
    return {
        "contact": {"phones": [{"label": "mobile", "display": "(540) 555-0120", "primary": True,
                                "sources": ["people"], "verified": False}],
                    "emails": [{"display": "overview@example.test", "primary": True,
                                "mailto": "mailto:overview@example.test", "sources": ["people"],
                                "verified": False}],
                    "address": {"lines": ["1 Example Way"], "city": "Roanoke", "state": "VA",
                                "postal_code": "24011", "source": "people"},
                    "preferred_contact_method": "email",
                    "household": {"id": 1, "name": "Example Household", "member_count": 2}},
        "identity": {"birth_date": date(1972, 11, 3), "birth_date_source": "people",
                     "ssn_last4": "1234", "ssn_on_file": True},
        "investments": {"custodians": [{"custodian": "Example Custodian", "subtotal": 1000,
                                        "accounts": [{"name": "Brokerage", "account_type": "taxable",
                                                      "status": "open", "value": 1000,
                                                      "value_available": True}]}],
                        "total_value": 1000, "account_count": 1},
        "tax": {"tax_year": 2025, "agi": 100000, "filing_status": "MFJ", "refund_amount": None,
                "balance_due": None, "federal": None, "state": None},
    }


def _empty_dashboard():
    return {"open_tasks": [], "recent_activity": [], "recent_documents": [],
            "documents_needing_review": [], "missing_tax_items": [], "tax_engagements": None,
            "upcoming_meetings": [], "planning_opportunities": [], "alerts": [],
            "newly_classified": [], "missing_document_alerts": [], "compliance_issues": [],
            "needs_attention": []}


# --- the order the page is read in --------------------------------------------------------------

def test_the_five_blocks_appear_in_the_agreed_order(templates, person_id):
    dash = _empty_dashboard()
    dash["needs_attention"] = [{"kind": "compliance", "label": "Compliance",
                                "title": "Missing engagement letter", "href": "/client/1"}]
    dash["recent_documents"] = [{"id": 1, "original_name": "Statement.pdf",
                                 "created_at": datetime(2026, 3, 4, tzinfo=UTC),
                                 "storage_provider": "local"}]
    dash["recent_activity"] = [{"title": "Call logged", "event_type": "call",
                                "occurred_at": datetime(2026, 3, 5, tzinfo=UTC)}]
    html = _render(templates, person_id, dashboard=dash, profile=_profile())
    assert _blocks(html) == ["Client summary", "Needs attention", "Financial snapshot",
                             "Recent documents", "Recent activity"]


def test_needs_attention_is_read_before_the_money(templates, person_id):
    """The only block that asks someone to act comes before the blocks that merely report."""
    dash = _empty_dashboard()
    dash["needs_attention"] = [{"kind": "task", "label": "Overdue task",
                                "title": "Chase 1099", "href": None}]
    html = _render(templates, person_id, dashboard=dash, profile=_profile())
    blocks = _blocks(html)
    assert blocks.index("Needs attention") < blocks.index("Financial snapshot")


# --- nothing empty on show ----------------------------------------------------------------------

def test_a_client_with_nothing_on_file_shows_only_the_summary(templates, person_id):
    profile = _profile()
    profile["investments"] = {"custodians": [], "total_value": 0, "account_count": 0}
    profile["tax"] = {"tax_year": None}
    html = _render(templates, person_id, dashboard=_empty_dashboard(), profile=profile)
    assert _blocks(html) == ["Client summary"]


def test_the_financial_block_is_hidden_when_there_is_no_money_and_no_return(templates, person_id):
    profile = _profile()
    profile["investments"] = {"custodians": [], "total_value": 0, "account_count": 0}
    profile["tax"] = {"tax_year": None}
    html = _render(templates, person_id, dashboard=_empty_dashboard(), profile=profile)
    assert "Financial snapshot" not in _blocks(html)


def test_needs_attention_disappears_when_there_is_nothing_to_attend_to(templates, person_id):
    html = _render(templates, person_id, dashboard=_empty_dashboard(), profile=_profile())
    assert "Needs attention" not in html


def test_the_summary_is_shown_even_when_every_field_is_blank(templates, person_id):
    """A client with nothing recorded still needs the card that says so."""
    profile = _profile()
    profile["contact"] = {"phones": [], "emails": [], "address": None,
                          "preferred_contact_method": None, "household": None}
    html = _render(templates, person_id, dashboard=_empty_dashboard(), profile=profile)
    assert "Client summary" in _blocks(html)
    assert html.count("Not on file") >= 3


# --- five, then the tab that holds the rest -----------------------------------------------------

def test_the_overview_limit_is_five():
    assert OVERVIEW_LIST_LIMIT == 5


def test_recent_documents_and_activity_stop_at_five(templates, person_id):
    dash = _empty_dashboard()
    dash["recent_documents"] = [
        {"id": i, "original_name": f"Doc {i}.pdf", "created_at": datetime(2026, 3, i + 1, tzinfo=UTC),
         "storage_provider": "local"} for i in range(1, 6)]
    dash["recent_activity"] = [
        {"title": f"Event {i}", "event_type": "note",
         "occurred_at": datetime(2026, 3, i + 1, tzinfo=UTC)} for i in range(1, 6)]
    html = _render(templates, person_id, dashboard=dash, profile=_profile())
    assert len(re.findall(r"Doc \d\.pdf", html)) == OVERVIEW_LIST_LIMIT
    assert len(re.findall(r"Event \d", html)) == OVERVIEW_LIST_LIMIT


def test_each_capped_block_links_to_the_tab_that_holds_them_all(templates, person_id):
    dash = _empty_dashboard()
    dash["recent_documents"] = [{"id": 1, "original_name": "One.pdf", "created_at": None,
                                 "storage_provider": "local"}]
    dash["recent_activity"] = [{"title": "One event", "event_type": "note", "occurred_at": None}]
    html = _render(templates, person_id, dashboard=dash, profile=_profile())
    assert f'href="/client/{person_id}?tab=documents">View all' in html
    assert f'href="/client/{person_id}?tab=timeline">View all' in html


# --- dates a person can read --------------------------------------------------------------------

def test_no_machine_timestamp_reaches_the_page(templates, person_id):
    """Serialized services hand templates ISO strings; the filter has to humanize those too."""
    dash = _empty_dashboard()
    dash["recent_documents"] = [{"id": 1, "original_name": "Serialized.pdf",
                                 "created_at": "2026-03-04T14:20:35-04:00",
                                 "storage_provider": "local"}]
    dash["recent_activity"] = [{"title": "Serialized event", "event_type": "note",
                                "occurred_at": "2026-03-05T09:00:00+00:00"}]
    html = _render(templates, person_id, dashboard=dash, profile=_profile())
    assert not ISO.search(html)
    assert "Mar 4, 2026" in html and "Mar 5, 2026" in html


def test_a_date_of_birth_is_written_out_in_words(templates, person_id):
    html = _render(templates, person_id, dashboard=_empty_dashboard(), profile=_profile())
    assert "Nov 3, 1972" in html
    assert "1972-11-03" not in html


# --- the Edit Profile action --------------------------------------------------------------------

def test_edit_profile_sits_beside_the_other_quick_actions(templates, person_id):
    html = _render(templates, person_id, dashboard=_empty_dashboard(), profile=_profile())
    assert "Edit Profile" in html
    assert f'/people/{person_id}/edit' in html
    assert "Add Note" in html          # still beside the action it was asked to join


def test_a_reader_without_write_is_not_offered_the_button(templates, person_id):
    html = _render(templates, person_id, principal=READER, dashboard=_empty_dashboard(),
                   profile=_profile())
    assert "Edit Profile" not in html
    assert f'/people/{person_id}/edit' not in html


# --- the folded Needs attention list ------------------------------------------------------------

def test_all_five_sources_fold_into_one_ordered_list():
    card = {
        "compliance_issues": [{"title": "Missing engagement letter"}],
        "missing_tax_items": [{"title": "No W-2 on file"}],
        "open_tasks": [{"title": "Chase 1099", "due_date": date(2020, 1, 1), "id": 7}],
        "documents_needing_review": [{"id": 3, "original_name": "Unreviewed.pdf"}],
        "missing_document_alerts": [{"title": "Unidentified upload"}],
    }
    items = _needs_attention(card)
    assert [i["kind"] for i in items] == [
        "compliance", "tax", "task", "review", "unidentified"]
    assert [i["label"] for i in items] == [
        "Compliance", "Tax", "Overdue task", "Needs review", "Unidentified"]


def test_a_task_with_no_due_date_is_open_work_not_overdue():
    items = _needs_attention({"open_tasks": [{"title": "Someday", "due_date": None, "id": 1}]})
    assert items == []


def test_the_folded_list_is_empty_when_every_source_is(templates):
    assert _needs_attention(_empty_dashboard()) == []


# --- responsive layout --------------------------------------------------------------------------

def test_the_overview_blocks_carry_their_own_stylesheet_rules():
    css = open("app/static/css/client360.css", encoding="utf-8").read()
    assert ".c360-overview-block" in css
    assert ".c360-block-head" in css


def test_the_profile_grid_collapses_to_one_column_on_a_narrow_screen():
    """The two-card rows must stack rather than scroll the page sideways on a phone."""
    css = open("app/static/css/client360.css", encoding="utf-8").read()
    narrow = [block for block in re.findall(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", css)
              if "max-width" in block and "c360-profile" in block]
    assert narrow, "no narrow-screen rule for the profile grid"
    assert any("1fr" in block for block in narrow)
