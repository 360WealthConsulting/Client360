"""Deferred-ownership lane — the guarantees that make parking a document safe.

Deferral moves an unowned document out of the actionable backlog without deleting it, archiving it,
hiding it from staff, or inventing an owner. Each test below pins one of those promises, because
every one of them is a way this feature could quietly become a data-loss bug instead of a queue
decision.

The scope decision under test alongside them: ``UNSUPPORTED`` is NOT deferrable in this first
implementation. Those documents are unresolved because nothing has read them yet (the production
OCR backend is not wired), which is not the same as their owner being unprovable.

Temp rows only, all tagged, all cleaned up.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.security.models import Principal
from app.services import document_deferral as dd
from app.services.document_platform.lifecycle import (
    active_documents_clause,
    deferred_ownership_clause,
    not_deferred_clause,
)
from app.services.document_platform.relationships import client_documents
from app.services.document_review_inbox import inbox_summary

_TAG = f"DEFER{uuid.uuid4().hex[:6]}"
_CAPS = frozenset({"client.read", "documents.view", "record.read_all", "record.write_all"})
STAFF = Principal(1, "staff@t", "Staff", _CAPS)


@pytest.fixture(autouse=True)
def _clean():
    facts = metadata.tables["document_facts"]
    ds = metadata.tables["document_sources"]

    def _wipe():
        with engine.begin() as c:
            ids = list(c.scalars(select(documents.c.id)
                                 .where(documents.c.original_name.like(f"%{_TAG}%"))))
            if ids:
                c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
                c.execute(delete(ds).where(ds.c.document_id.in_(ids)))
                c.execute(delete(documents).where(documents.c.id.in_(ids)))
            pids = list(c.scalars(select(people.c.id)
                                  .where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(people).where(people.c.id.in_(pids)))

    _wipe()
    yield
    _wipe()


def _doc(*, route="NO_MATCH", person_id=None, name=None, with_source=True,
         review_status="not_required") -> int:
    """One unowned document plus, unless suppressed, its current owner_proposal fact."""
    name = name or f"{_TAG} {uuid.uuid4().hex[:6]}.pdf"
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"defer:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local",
            storage_uri=f"/x/{name}", size_bytes=10, sha256=uuid.uuid4().hex * 2,
            person_id=person_id, status="active", archived=False,
            review_status=review_status, current_version=1,
            tags={"source_system": "SharePoint", "taxdome_folder": f"{_TAG} folder"}
        ).returning(documents.c.id)).scalar_one()
        if with_source:
            ds = metadata.tables["document_sources"]
            c.execute(ds.insert().values(
                document_id=did, source_system="SharePoint",
                source_uri=f"sp://{did}", source_external_id=f"EXT{did}",
                source_hash="f" * 64, available=True, metadata={}))
        if route is not None:
            facts = metadata.tables["document_facts"]
            c.execute(facts.insert().values(
                document_id=did, fact_type="owner_proposal",
                fact_value=json.dumps({"route": route}), confidence=0.0,
                extraction_engine="owner_proposal", extractor_version="test",
                version=1, is_current=True))
    return did


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.review_status, documents.c.tags,
                                documents.c.person_id, documents.c.household_id,
                                documents.c.organization_id, documents.c.status,
                                documents.c.archived, documents.c.sha256,
                                documents.c.storage_uri)
                         .where(documents.c.id == did)).mappings().one()


def _sources(did):
    ds = metadata.tables["document_sources"]
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(ds.c.source_system, ds.c.source_uri, ds.c.source_external_id, ds.c.source_hash)
            .where(ds.c.document_id == did)).mappings()]


# --- the fail-closed guard ----------------------------------------------------

def test_anchored_document_cannot_be_deferred():
    """The single most important guard: deferral must never touch an owned document."""
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Owner", last_name=_TAG, full_name=f"Owner {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc(person_id=pid)

    result = dd.defer_document(did)

    assert result["deferred"] is False
    assert result["outcome"] == "already_owned"
    assert _row(did)["review_status"] == "not_required"


def test_unsupported_route_is_not_deferrable_in_this_implementation():
    """Scope decision, pinned. UNSUPPORTED means 'not read yet', not 'owner unprovable'."""
    did = _doc(route="UNSUPPORTED")

    result = dd.defer_document(did)

    assert result["deferred"] is False
    assert result["outcome"] == "route_not_deferrable"
    assert result["route"] == "UNSUPPORTED"
    assert _row(did)["review_status"] == "not_required"
    assert "UNSUPPORTED" in dd.NON_DEFERRABLE_ROUTES
    assert "UNSUPPORTED" not in dd.ROUTE_REASON


@pytest.mark.parametrize("route", ["HIGH", "MEDIUM", "AMBIGUOUS"])
def test_routes_with_a_proposed_owner_are_not_deferrable(route):
    """These carry a candidate owner — they are the work a reviewer CAN finish today."""
    did = _doc(route=route)
    assert dd.defer_document(did)["outcome"] == "route_not_deferrable"


def test_relabelling_a_high_proposal_does_not_get_it_out_of_the_queue():
    """A caller cannot launder an ineligible route by supplying an eligible-looking reason.

    The refusal arrives as an OUTCOME rather than an exception: the document gate runs before the
    reason is considered, and a batch has to be able to skip a row and carry on. What matters is
    that nothing is written, which is what this asserts.
    """
    did = _doc(route="HIGH")
    result = dd.defer_document(did, reason=dd.REASON_NO_MATCH)
    assert result["deferred"] is False
    assert result["outcome"] == "route_not_deferrable"
    assert _row(did)["review_status"] == "not_required"


def test_a_reason_that_contradicts_an_eligible_route_raises():
    """On an eligible document, a reason that disagrees with the evidence is a caller error.

    Here the gate passes — the document really is deferrable — so the mismatch is not a row to skip
    but a bug in the call, and it raises rather than silently recording the wrong reason.
    """
    did = _doc(route="NO_MATCH")
    with pytest.raises(dd.DeferralError):
        dd.defer_document(did, reason=dd.REASON_NO_PROPOSAL)
    assert _row(did)["review_status"] == "not_required"


def test_an_unapproved_reason_is_refused():
    did = _doc()
    with pytest.raises(dd.DeferralError):
        dd.defer_document(did, reason="because_i_said_so")


@pytest.mark.parametrize("filename", ["Thumbs.db", "Outlook-5rp5rfei", "setup.exe"])
def test_a_system_artifact_is_not_deferrable(filename):
    """Deferral means "valid paperwork, owner not currently provable".

    A Thumbs.db has no owner because it is not a client document at all — it belongs in EXCLUDED.
    Production's candidate set contains exactly three such rows; without this gate they would
    dilute the lane and make its count mean two different things.
    """
    did = _doc(name=f"{_TAG} {filename}")
    result = dd.defer_document(did)
    assert result["deferred"] is False
    assert result["outcome"] == "not_a_client_document"
    assert _row(did)["review_status"] == "not_required"


# --- the two eligible routes --------------------------------------------------

def test_no_match_defers_with_reason_no_match():
    did = _doc(route="NO_MATCH")
    result = dd.defer_document(did, actor_user_id=7)
    assert result["deferred"] is True and result["reason"] == dd.REASON_NO_MATCH
    row = _row(did)
    assert row["review_status"] == dd.DEFERRED_REVIEW_STATUS
    assert row["tags"][dd.TAGS_KEY]["reason"] == dd.REASON_NO_MATCH
    assert row["tags"][dd.TAGS_KEY]["route"] == "NO_MATCH"
    assert row["tags"][dd.TAGS_KEY]["deferred_by_user_id"] == 7


def test_absent_proposal_defers_with_reason_no_proposal():
    did = _doc(route=None)                     # no owner_proposal fact at all
    result = dd.defer_document(did)
    assert result["deferred"] is True and result["reason"] == dd.REASON_NO_PROPOSAL


# --- ownership, provenance and content are untouched --------------------------

def test_deferral_preserves_ownership_nulls_and_every_source_reference():
    did = _doc()
    before_sources, before_row = _sources(did), _row(did)

    dd.defer_document(did)

    after_sources, after_row = _sources(did), _row(did)
    assert after_sources == before_sources            # source_external_id, uri, hash all intact
    assert after_row["person_id"] is None
    assert after_row["household_id"] is None
    assert after_row["organization_id"] is None
    assert after_row["sha256"] == before_row["sha256"]
    assert after_row["storage_uri"] == before_row["storage_uri"]
    assert after_row["status"] == "active" and after_row["archived"] is False
    # Pre-existing tags survive — deferral ADDS a key, it does not replace the object.
    assert after_row["tags"]["source_system"] == "SharePoint"
    assert after_row["tags"]["taxdome_folder"] == f"{_TAG} folder"


def test_deferral_does_not_disturb_the_owner_proposal_fact():
    did = _doc(route="NO_MATCH")
    facts = metadata.tables["document_facts"]
    with engine.connect() as c:
        before = [dict(r) for r in c.execute(
            select(facts.c.fact_value, facts.c.is_current, facts.c.version)
            .where(facts.c.document_id == did)).mappings()]
    dd.defer_document(did)
    with engine.connect() as c:
        after = [dict(r) for r in c.execute(
            select(facts.c.fact_value, facts.c.is_current, facts.c.version)
            .where(facts.c.document_id == did)).mappings()]
    assert after == before


# --- visibility ---------------------------------------------------------------

def test_deferred_document_stays_visible_to_staff():
    did = _doc()
    dd.defer_document(did)
    with engine.connect() as c:
        # The staff/library predicate is "not deleted" — deferral must not narrow it.
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, active_documents_clause())).scalar() == did
        # And it is findable in its own lane.
        assert did in [r["id"] for r in dd.list_deferred(conn=c)]
        assert did in [r["id"] for r in dd.list_deferred(reason=dd.REASON_NO_MATCH, conn=c)]


def test_deferred_document_is_absent_from_the_client_filing_tree():
    """It has no anchor, so no client read can reach it — before or after deferral."""
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Client", last_name=_TAG, full_name=f"Client {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc()
    dd.defer_document(did)

    filed = client_documents(STAFF, "person", pid)
    assert did not in [d["id"] for d in filed]


def test_deferred_and_not_deferred_clauses_are_complementary():
    did = _doc()
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, deferred_ownership_clause())).scalar() is None
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, not_deferred_clause())).scalar() == did
    dd.defer_document(did)
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, deferred_ownership_clause())).scalar() == did
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, not_deferred_clause())).scalar() is None


# --- the metric ---------------------------------------------------------------

def test_deferral_moves_the_document_out_of_the_actionable_backlog_into_its_own_count():
    did = _doc()
    before = inbox_summary()

    dd.defer_document(did)

    after = inbox_summary()
    assert after["unassigned_documents"] == before["unassigned_documents"] - 1
    assert after["deferred_ownership"] == before["deferred_ownership"] + 1
    # The pre-existing meaning is preserved for anyone reconciling an older report: the total
    # unowned population has NOT changed — only how it is split.
    assert after["unassigned_total"] == before["unassigned_total"]
    assert after["deferred_by_reason"].get(dd.REASON_NO_MATCH, 0) == \
        before["deferred_by_reason"].get(dd.REASON_NO_MATCH, 0) + 1


def test_the_deferred_lane_is_offered_to_staff():
    assert any(lane["key"] == "deferred_ownership" for lane in inbox_summary()["lanes"])


# --- idempotency and reversal -------------------------------------------------

def test_deferring_twice_is_a_no_op():
    did = _doc()
    first = dd.defer_document(did)
    second = dd.defer_document(did)
    assert first["deferred"] is True
    assert second["deferred"] is False and second["outcome"] == "already_deferred"


def test_promote_clears_the_lane_and_is_idempotent():
    did = _doc()
    dd.defer_document(did)

    first = dd.promote_document(did)
    second = dd.promote_document(did)

    assert first["promoted"] is True
    assert second["promoted"] is False and second["outcome"] == "not_deferred"
    row = _row(did)
    assert row["review_status"] == dd.SETTLED_REVIEW_STATUS
    assert dd.TAGS_KEY not in row["tags"]
    assert row["tags"]["source_system"] == "SharePoint"    # other tags survive the clear


def test_defer_promote_defer_round_trips():
    did = _doc()
    assert dd.defer_document(did)["deferred"] is True
    assert dd.promote_document(did)["promoted"] is True
    assert dd.defer_document(did)["deferred"] is True
    assert _row(did)["review_status"] == dd.DEFERRED_REVIEW_STATUS


# --- review_status is state, not spare space ----------------------------------
#
# Deferral WRITES review_status and promotion writes ``not_required`` BACK — it cannot restore a
# value it never recorded. So a row already carrying real review state must never enter the lane:
# deferring it would destroy that state, and promoting it would settle a review nobody performed.
# The guard is an allow-list, so it fails closed on states that do not exist yet.

@pytest.mark.parametrize("settled", ["not_required", ""])
def test_a_document_in_a_settled_review_state_can_be_deferred(settled):
    """The lane still accepts exactly what it was built for: rows with nothing to lose."""
    did = _doc(review_status=settled)

    assert dd.defer_document(did)["deferred"] is True
    assert _row(did)["review_status"] == dd.DEFERRED_REVIEW_STATUS


def test_an_existing_pending_review_is_not_deferred():
    """The one production row this defect would have destroyed (``pending`` ×1 of 121,806)."""
    did = _doc(review_status="pending")

    result = dd.defer_document(did)

    assert result["deferred"] is False
    assert result["outcome"] == "review_status_not_deferrable"
    assert _row(did)["review_status"] == "pending"


@pytest.mark.parametrize("state", ["pending", "in_review", "needs_review", "flagged", "hold",
                                   "rejected", "some_future_workflow_state"])
def test_a_non_default_review_status_is_refused_and_left_byte_for_byte(state):
    """Refusal must be a no-op, not a partial write — and unknown states refuse too (fail closed)."""
    did = _doc(review_status=state)
    before = _row(did)

    check = dd.defer_document(did)
    after = _row(did)

    assert check["deferred"] is False
    assert after["review_status"] == state
    assert dd.TAGS_KEY not in (after["tags"] or {})
    assert after["tags"] == before["tags"]                     # no partial tags write
    assert (after["person_id"], after["household_id"], after["organization_id"]) == (None, None, None)
    assert after["sha256"] == before["sha256"]                 # provenance untouched
    assert _sources(did)                                        # source references intact


def test_eligibility_reports_the_refusal_without_writing():
    did = _doc(review_status="pending")
    with engine.connect() as c:
        check = dd.eligibility(c, did)
    assert check["eligible"] is False
    assert check["reason_code"] == "review_status_not_deferrable"
    assert check["review_status"] == "pending"
    assert _row(did)["review_status"] == "pending"


def test_promotion_clears_the_sentinel_only():
    """Promotion is scoped to the lane: a row that never entered it is not settled by passing by."""
    deferred = _doc()
    dd.defer_document(deferred)
    untouched = _doc(review_status="pending")

    assert dd.promote_document(deferred)["promoted"] is True
    result = dd.promote_document(untouched)

    assert result["promoted"] is False and result["outcome"] == "not_deferred"
    assert _row(deferred)["review_status"] == dd.SETTLED_REVIEW_STATUS
    assert _row(untouched)["review_status"] == "pending"        # NOT reset to not_required


def test_a_refused_document_keeps_its_ownership_and_client_visibility():
    """Refusing to defer changes nothing a client or staff read can observe."""
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Client", last_name=_TAG, full_name=f"Client {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc(review_status="pending")

    dd.defer_document(did)

    with engine.connect() as c:
        # Still a live document for staff, and still NOT in the deferred lane.
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, active_documents_clause())).scalar() == did
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, deferred_ownership_clause())).scalar() is None
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, not_deferred_clause())).scalar() == did
        assert did not in [r["id"] for r in dd.list_deferred(conn=c)]
    # Unowned before, unowned after — no client read can reach it either way.
    assert did not in [d["id"] for d in client_documents(STAFF, "person", pid)]


def test_dry_run_writes_nothing():
    did = _doc()
    assert dd.defer_document(did, dry_run=True)["outcome"] == "would_defer"
    assert _row(did)["review_status"] == "not_required"


# --- promotion to FILED is atomic with the ownership write --------------------

def test_resolving_ownership_clears_the_deferral_in_the_same_write():
    """Assignment and deferral-clearing must not be separable — a document can never be both."""
    from app.services.households import resolve_document_ownership
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="New", last_name=_TAG, full_name=f"New {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc()
    dd.defer_document(did)
    assert _row(did)["review_status"] == dd.DEFERRED_REVIEW_STATUS

    result = resolve_document_ownership(did, person_id=pid, actor_user_id=1, request_id="t")

    assert result["assigned"] is True
    row = _row(did)
    assert row["person_id"] == pid
    assert row["review_status"] == "not_required"          # cleared atomically
    assert dd.TAGS_KEY not in row["tags"]
    assert row["tags"]["source_system"] == "SharePoint"


def test_resolving_ownership_leaves_an_unrelated_review_status_alone():
    """The CASE is narrow: only the deferral sentinel is cleared, not a real pending review."""
    from app.services.households import resolve_document_ownership
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Keep", last_name=_TAG, full_name=f"Keep {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc()
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET review_status='pending' WHERE id=:i"), {"i": did})

    resolve_document_ownership(did, person_id=pid, actor_user_id=1, request_id="t")

    assert _row(did)["review_status"] == "pending"


def test_a_deferred_document_that_gains_an_owner_cannot_stay_deferred():
    """The invariant the client-visibility story rests on: owned AND deferred is unreachable."""
    from app.services.households import resolve_document_ownership
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Inv", last_name=_TAG, full_name=f"Inv {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc()
    dd.defer_document(did)
    resolve_document_ownership(did, person_id=pid, actor_user_id=1, request_id="t")

    with engine.connect() as c:
        both = c.execute(select(documents.c.id).where(
            documents.c.id == did,
            documents.c.person_id.isnot(None),
            deferred_ownership_clause())).scalar()
    assert both is None
