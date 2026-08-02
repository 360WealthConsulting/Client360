"""Drake tax-domain integration (PR 3B) — coverage.

Imports Drake return-status records into the AUTHORITATIVE tax tables and confirms the Client Workspace
Tax tab renders them (status, acknowledgements, filing timeline, K-1, document links) — no separate
Drake app, no new tables. Idempotent + audited. Temp/test rows only.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    tax_engagement_returns,
    tax_engagements,
    tax_filing_events,
    tax_missing_items,
    tax_return_lifecycle_events,
)
from app.security.models import Principal
from app.services.client360.tax_workspace import build_tax_workspace
from app.services.drake_tax import import_drake_tax_returns

_TAG = "DRKTAX"
_CAPS = frozenset({"tax.read", "record.read_all", "client.read"})
_ACTOR = {"uid": None}


@pytest.fixture
def household():
    from app.db import users
    tag = uuid.uuid4().hex[:6]
    with engine.begin() as c:
        _ACTOR["uid"] = c.execute(users.insert().values(
            email=f"drk{tag}@e.test", normalized_email=f"drk{tag}@e.test",
            display_name=f"Drk {tag}", status="active").returning(users.c.id)).scalar_one()
        hid = c.execute(households.insert().values(name=f"{_TAG} White {tag}").returning(
            households.c.id)).scalar_one()
        pids = []
        # Fully tag-unique names: append-only lifecycle rows make full teardown best-effort, so these
        # people can outlive the test. Common names ("Michael"/"Debra"/"White") would leak into
        # name-matched search/suggestions in other suites; tag-unique tokens keep this self-contained.
        surname = f"Drk{tag}"
        for first in (f"Owner{tag}", f"Partner{tag}"):
            pid = c.execute(people.insert().values(
                first_name=first, last_name=surname, full_name=f"{first} {surname}",
                household_id=hid, active=True).returning(people.c.id)).scalar_one()
            c.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="member"))
            pids.append(pid)
        did = c.execute(documents.insert().values(
            original_name=f"2024 1040 {_TAG}{tag}.pdf", stored_name=f"drake:{tag}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/1040",
            size_bytes=3, sha256="a" * 64, household_id=hid, status="active", archived=False,
            tags={"source_system": "Drake"}).returning(documents.c.id)).scalar_one()
    yield {"hid": hid, "pids": pids, "did": did, "tag": tag}
    # tax_filing_events / lifecycle are append-only → best-effort cleanup.
    def _try(stmt):
        try:
            with engine.begin() as c:
                c.execute(stmt)
        except Exception:
            pass
    with engine.connect() as c:
        eng = list(c.scalars(select(tax_engagements.c.id).where(tax_engagements.c.household_id == hid)))
        rets = list(c.scalars(select(tax_engagement_returns.c.id).where(
            tax_engagement_returns.c.tax_engagement_id.in_(eng or [-1]))))
    for r in rets:
        _try(delete(tax_missing_items).where(tax_missing_items.c.tax_engagement_return_id == r))
        _try(delete(tax_engagement_returns).where(tax_engagement_returns.c.id == r))
    for e in eng:
        _try(delete(tax_engagements).where(tax_engagements.c.id == e))
    _try(delete(documents).where(documents.c.id == did))
    _try(delete(household_relationships).where(household_relationships.c.household_id == hid))
    _try(delete(people).where(people.c.household_id == hid))
    _try(delete(households).where(households.c.id == hid))


def _record(hh, **over):
    rec = {
        "household_id": hh["hid"], "tax_year": 2024, "return_type": "1040", "jurisdiction": "US",
        "status": "in_review", "federal_filing_status": "submitted",
        "prepared_at": date(2025, 3, 10), "filed_at": date(2025, 3, 14),
        "federal_accepted_at": date(2025, 3, 15),
        "federal_ack": {"status": "accepted", "submission_id": "SUB-2024-1040",
                        "message": "IRS accepted"},
        "missing_k1": ["Rental LLC"], "document_ids": [hh["did"]],
        "refund_amount": 1200, "amended": False,
    }
    rec.update(over)
    return rec


def _tw(hh):
    return build_tax_workspace(Principal(0, "a@e.test", "A", _CAPS),
                               person_id=hh["pids"][0], household_id=hh["hid"], scope_ids=hh["pids"])


# --- discovery / return import ----------------------------------------------

def test_import_creates_return(household):
    s = import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"], request_id="t")
    assert s["returns_imported"] == 1
    tw = _tw(household)
    assert tw["status_summary"]["status"] == "available"
    r = tw["return_history"]["returns"][0]
    assert r["year"] == 2024 and r["filed_at"] is not None


def test_acknowledgements_appear(household):
    import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"], request_id="t")
    acks = _tw(household)["acknowledgements"]
    assert acks["status"] == "available"
    assert any(e["submission_id"] == "SUB-2024-1040" for e in acks["events"])


def test_timeline_events(household):
    import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"], request_id="t")
    labels = " ".join(e["label"] for e in _tw(household)["filing_timeline"]["events"])
    assert "prepared" in labels and "filed" in labels and "federal_accepted" in labels


def test_k1_and_document_links(household):
    import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"], request_id="t")
    tw = _tw(household)
    assert any("Rental LLC" in m["title"] for m in tw["k1_tracking"]["k1_items"])
    assert any(household["did"] in [d["id"] for d in r["documents"]]
               for r in tw["return_history"]["returns"])


# --- status update + incremental / idempotent -------------------------------

def test_status_update_on_rerun(household):
    import_drake_tax_returns([_record(household, status="in_review")], actor_user_id=_ACTOR["uid"])
    import_drake_tax_returns([_record(household, status="completed")], actor_user_id=_ACTOR["uid"])
    r = _tw(household)["return_history"]["returns"][0]
    assert r["status"] == "completed"


def test_idempotent_no_duplicate_rows(household):
    import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"])
    s = import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"])
    assert s["returns_updated"] == 1 and s["returns_imported"] == 0
    with engine.connect() as c:
        eng = list(c.scalars(select(tax_engagements.c.id).where(tax_engagements.c.household_id == household["hid"])))
        rets = list(c.scalars(select(tax_engagement_returns.c.id).where(
            tax_engagement_returns.c.tax_engagement_id.in_(eng))))
        n_ack = len(list(c.scalars(select(tax_filing_events.c.id).where(
            tax_filing_events.c.tax_engagement_return_id.in_(rets)))))
        n_life = len(list(c.scalars(select(tax_return_lifecycle_events.c.id).where(
            tax_return_lifecycle_events.c.tax_engagement_return_id.in_(rets)))))
        n_k1 = len(list(c.scalars(select(tax_missing_items.c.id).where(
            tax_missing_items.c.tax_engagement_return_id.in_(rets)))))
    assert len(rets) == 1 and n_k1 == 1                     # no duplicate return / K-1
    assert n_ack == 1                                       # federal ack not re-inserted on rerun
    assert n_life == 3                                      # prepared, filed, federal_accepted (no dup on rerun)


# --- audit + scope + household visibility -----------------------------------

def test_import_is_audited(household):
    from app.db import audit_events
    import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"], request_id="t")
    with engine.connect() as c:
        assert c.scalar(select(audit_events.c.id).where(
            audit_events.c.action == "tax.drake_imported").limit(1)) is not None


def test_household_visibility_both_members(household):
    import_drake_tax_returns([_record(household)], actor_user_id=_ACTOR["uid"])
    # The person workspace for a member (scoped to the household) sees the return.
    tw = build_tax_workspace(Principal(0, "a@e.test", "A", _CAPS),
                             person_id=household["pids"][1], household_id=household["hid"],
                             scope_ids=household["pids"])
    assert tw["status_summary"]["return_count"] == 1


def test_dry_run_makes_no_changes(household):
    s = import_drake_tax_returns([_record(household)], dry_run=True)
    assert s["dry_run"] is True and s["returns_imported"] == 1
    assert _tw(household)["status_summary"]["status"] == "no_data"   # nothing written
