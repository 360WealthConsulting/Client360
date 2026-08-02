"""Client Workspace — Tax tab (tax operating center) coverage.

Composes the authoritative tax domain (engagements/returns/missing items/filing events) scoped to a
client (ADR-073), reuses exception/task services, and renders Drake-only concepts honestly as
not-connected. No new tax/document/workflow/ownership logic. Temp/test rows only.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    engine,
    filing_jurisdictions,
    people,
    tax_engagement_returns,
    tax_engagements,
    tax_filing_events,
    tax_firms,
    tax_missing_items,
    tax_offices,
    tax_return_types,
    tax_years,
)
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.tax_workspace import (
    NO_AUTHORITATIVE_SOURCE,
    REQUIRES_REVIEW,
    build_tax_workspace,
)

_TAG = "CWTAX"
_CAPS = frozenset({"tax.read", "record.read_all", "client.read"})


@pytest.fixture
def tax_client():
    tag = uuid.uuid4().hex[:6]
    created = {}
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Tax", last_name=f"{_TAG}{tag}", full_name=f"Tax {_TAG}{tag}",
            active=True).returning(people.c.id)).scalar_one()
        firm = c.execute(select(tax_firms.c.id).limit(1)).scalar() or c.execute(
            tax_firms.insert().values(code=f"F{tag}", name=f"Firm {tag}").returning(
                tax_firms.c.id)).scalar_one()
        office = c.execute(select(tax_offices.c.id).limit(1)).scalar() or c.execute(
            tax_offices.insert().values(tax_firm_id=firm, code=f"O{tag}", name=f"Office {tag}").returning(
                tax_offices.c.id)).scalar_one()
        yr = c.execute(select(tax_years.c.id).limit(1)).scalar() or c.execute(
            tax_years.insert().values(year=2024, starts_on=date(2024, 1, 1), ends_on=date(2024, 12, 31),
                                      status="open").returning(tax_years.c.id)).scalar_one()
        rt = c.execute(select(tax_return_types.c.id).limit(1)).scalar() or c.execute(
            tax_return_types.insert().values(code="1040", name="Individual",
                                             entity_type="individual").returning(
                tax_return_types.c.id)).scalar_one()
        jur = c.execute(select(filing_jurisdictions.c.id).limit(1)).scalar() or c.execute(
            filing_jurisdictions.insert().values(code="US", name="Federal", level="federal").returning(
                filing_jurisdictions.c.id)).scalar_one()
        eng = c.execute(tax_engagements.insert().values(
            tax_firm_id=firm, tax_office_id=office, tax_year_id=yr, person_id=pid,
            engagement_type="individual", status="active").returning(
            tax_engagements.c.id)).scalar_one()
        ret = c.execute(tax_engagement_returns.insert().values(
            tax_engagement_id=eng, return_type_id=rt, jurisdiction_id=jur, status="in_preparation",
            filing_status="not_filed").returning(tax_engagement_returns.c.id)).scalar_one()
        c.execute(insert(tax_missing_items).values(
            tax_engagement_return_id=ret, item_type="k1", title="Missing K-1 — Rental LLC",
            status="open", due_date=date(2025, 3, 20)))
        c.execute(insert(tax_filing_events).values(
            tax_engagement_return_id=ret, filing_status="submitted", provider_key="drake",
            submission_id="SUB123", message="e-file submitted", idempotency_key=f"idem-{tag}"))
    created.update(pid=pid, eng=eng, ret=ret)
    yield created
    # tax_filing_events are append-only (immutable trigger), so the return/engagement/person can't be
    # deleted while they exist. Best-effort cleanup per statement; immutable rows are left behind
    # harmlessly (unique per run, and all reads are person-scoped).
    def _try(stmt):
        try:
            with engine.begin() as c:
                c.execute(stmt)
        except Exception:
            pass
    _try(delete(tax_missing_items).where(tax_missing_items.c.tax_engagement_return_id == created["ret"]))
    _try(delete(tax_engagement_returns).where(tax_engagement_returns.c.id == created["ret"]))
    _try(delete(tax_engagements).where(tax_engagements.c.id == created["eng"]))
    _try(delete(people).where(people.c.id == created["pid"]))


def _principal(caps=_CAPS):
    return Principal(0, "adv@e.test", "Advisor", caps)


def _tw(tax_client):
    return build_tax_workspace(_principal(), person_id=tax_client["pid"], scope_ids=[tax_client["pid"]])


# --- scope + status summary + return history ---------------------------------

def test_tax_scope_and_status_summary(tax_client):
    tw = _tw(tax_client)
    assert tw["status_summary"]["status"] == "available"
    assert tw["status_summary"]["return_count"] == 1
    assert tw["status_summary"]["open_missing"] == 1
    assert tw["status_summary"]["filed"] is False


def test_return_history_lists_returns(tax_client):
    rh = _tw(tax_client)["return_history"]
    assert rh["status"] == "available" and len(rh["returns"]) == 1
    r = rh["returns"][0]
    assert r["return_id"] == tax_client["ret"] and r["year"] is not None and r["return_type"]


def test_out_of_scope_client_sees_no_tax_data():
    # A brand-new client with no engagements → no data (not an error).
    tag = uuid.uuid4().hex[:6]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"Empty {_TAG}{tag}", active=True)
                        .returning(people.c.id)).scalar_one()
    try:
        tw = build_tax_workspace(_principal(), person_id=pid, scope_ids=[pid])
        assert tw["status_summary"]["status"] == "no_data" and tw["return_history"]["returns"] == []
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == pid))


# --- missing items + K-1 + acknowledgements (authoritative) ------------------

def test_missing_items_and_k1(tax_client):
    tw = _tw(tax_client)
    assert tw["missing_and_exceptions"]["status"] == "available"
    assert any("K-1" in m["title"] for m in tw["missing_and_exceptions"]["missing_items"])
    assert tw["k1_tracking"]["status"] == "available" and len(tw["k1_tracking"]["k1_items"]) == 1


def test_acknowledgements_from_filing_events(tax_client):
    acks = _tw(tax_client)["acknowledgements"]
    assert acks["status"] == "available"
    assert any(e["submission_id"] == "SUB123" for e in acks["events"])


def test_filing_timeline_has_events(tax_client):
    tl = _tw(tax_client)["filing_timeline"]
    assert tl["status"] == "available" and len(tl["events"]) >= 1


# --- honest deferred states for Drake-only concepts --------------------------

def test_drake_only_sections_are_honest(tax_client):
    tw = _tw(tax_client)
    assert tw["estimated_payments"]["status"] == NO_AUTHORITATIVE_SOURCE
    assert tw["carryforwards_planning"]["status"] == REQUIRES_REVIEW
    # These sections carry an explanatory note, not fabricated records.
    assert tw["estimated_payments"].get("note")
    assert "documents" not in tw["estimated_payments"]  # no fake rows


def test_empty_client_deferred_sections_still_honest():
    tag = uuid.uuid4().hex[:6]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"E {_TAG}{tag}", active=True)
                        .returning(people.c.id)).scalar_one()
    try:
        tw = build_tax_workspace(_principal(), person_id=pid, scope_ids=[pid])
        assert tw["acknowledgements"]["status"] == "no_data"
        assert tw["estimated_payments"]["status"] == NO_AUTHORITATIVE_SOURCE
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == pid))


# --- integration through the workspace + capability gating -------------------

def test_tax_section_via_get_workspace(tax_client):
    ws = get_workspace(_principal(), person_id=tax_client["pid"])
    assert "tax" in ws["section_keys"]
    sec = ws["sections"]["tax"]
    assert sec["status_summary"]["return_count"] == 1
    # Dashboard-compat keys preserved
    assert "open_exceptions" in sec and "engagements" in sec


def test_tax_tab_hidden_without_capability(tax_client):
    ws = get_workspace(Principal(0, "x@e.test", "X", frozenset({"client.read", "record.read_all"})),
                       person_id=tax_client["pid"])
    assert "tax" not in ws["section_keys"]        # gated by tax.read


def test_tax_tab_renders(tax_client):
    from starlette.requests import Request

    from app.routes.client360 import client_workspace
    scope = {"type": "http", "method": "GET", "path": f"/client/{tax_client['pid']}", "headers": [],
             "query_string": b"", "state": {}}
    req = Request(scope)
    req.state.principal = _principal()
    req.state.request_id = "t"
    html = client_workspace(req, person_id=tax_client["pid"], tab="tax", principal=_principal()).body.decode()
    assert "Tax status" in html or "Return history" in html
    assert "Missing K-1 — Rental LLC" in html
