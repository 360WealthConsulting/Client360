"""Sprint 5.4 — deterministic document matching, authorization, ambiguity,
missing-information, and duplicate-handling tests."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import (documents, engine, households, people, tax_checklist_items,
    tax_document_links, tax_document_review_events, tax_engagement_returns,
    tax_engagements, tax_missing_items, users)
from app.security.models import Principal
from app.services.tax_domain import create_engagement
from app.services.tax_document_intelligence import (
    AUTO_MATCH_THRESHOLD, AMBIGUITY_FLOOR, NoopAIClassifier, Signal, compute_missing,
    decide, email_exact_signals, ingest_document, portal_request_signals,
    review_action, review_queue, score_signals,
)


def _req():
    return SimpleNamespace(state=SimpleNamespace(request_id="rc-" + uuid.uuid4().hex),
                           client=SimpleNamespace(host="127.0.0.1"), headers={})


def _user(label="u"):
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"{label}-{s}@e.com", normalized_email=f"{label}-{s}@e.com",
            display_name=label, auth_subject=f"{label}-{s}", status="active").returning(users.c.id)).scalar_one()


def _engagement(email=None):
    actor = _user("prep")
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        h = c.execute(households.insert().values(name=f"H {s}").returning(households.c.id)).scalar_one()
        p = c.execute(people.insert().values(household_id=h, full_name=f"Client {s}", active=True,
            normalized_email=email).returning(people.c.id)).scalar_one()
    result = create_engagement({"tax_year": 2026, "return_type": "1040", "filing_status": "single",
        "person_id": p, "household_id": h, "assignee_user_id": actor}, actor_user_id=actor, request_id=f"e-{s}")
    return actor, p, h, result["return_id"]


def _document(person_id, sha=None):
    tag = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(documents.insert().values(person_id=person_id, sha256=sha or tag[:64],
            original_name="doc.pdf", stored_name=f"{tag}.pdf", storage_path=f"/x/{tag}.pdf",
            size_bytes=1024).returning(documents.c.id)).scalar_one()


# --- Pure engine (no I/O) ---------------------------------------------------

def test_engine_single_deterministic_candidate_auto_matches():
    d = decide([Signal("portal_request", person_id=1, return_id=10, checklist_item_id=5)])
    assert d.outcome == "accept" and d.candidate.confidence >= AUTO_MATCH_THRESHOLD


def test_engine_ambiguous_candidates_route_to_review():
    d = decide([Signal("email_exact", person_id=1, return_id=10),
                Signal("email_exact", person_id=1, return_id=11)])
    assert d.outcome == "review" and "ambiguous" in d.reason


def test_engine_no_evidence_is_unmatched():
    assert decide([]).outcome == "unmatched"


def test_engine_ignores_unknown_fuzzy_signals():
    # A fuzzy/hint signal type must never contribute to the score.
    assert score_signals([Signal("hint", person_id=1, return_id=10)]) == []
    assert decide([Signal("hint", person_id=1, return_id=10)]).outcome == "unmatched"


def test_no_substring_matching_symbol_exists_in_service():
    import app.services.tax_document_intelligence as svc
    import inspect
    source = inspect.getsource(svc)
    # Ownership must never be established by substring/containment or SQL LIKE.
    assert " in search_text" not in source and "LIKE '%" not in source and ".contains(" not in source


# --- Deterministic provenance + missing-information -------------------------

def test_portal_provenance_auto_matches_and_resolves_checklist():
    actor, person, hh, rid = _engagement()
    with engine.connect() as c:
        item = c.execute(select(tax_checklist_items.c.id).where(
            tax_checklist_items.c.tax_engagement_return_id == rid,
            tax_checklist_items.c.required.is_(True))).first()
    assert item is not None
    doc = _document(person)
    with engine.connect() as c:
        signals = portal_request_signals(c, item.id)
    result = ingest_document(doc, signals)
    assert result["outcome"] == "accepted"
    with engine.connect() as c:
        ci = c.execute(select(tax_checklist_items).where(tax_checklist_items.c.id == item.id)).mappings().one()
        assert ci["status"] == "received" and ci["document_id"] == doc
        # the missing-item for this checklist item is resolved
        open_missing = c.scalar(select(tax_missing_items.c.id).where(
            tax_missing_items.c.checklist_item_id == item.id, tax_missing_items.c.status == "open"))
        assert open_missing is None


def test_email_identity_with_multiple_returns_is_ambiguous():
    email = f"multi-{uuid.uuid4().hex[:8]}@e.com"
    actor, person, hh, rid1 = _engagement(email=email)
    # second return for the same person
    from app.services.tax_domain import create_engagement
    rid2 = create_engagement({"tax_year": 2025, "return_type": "1040", "filing_status": "single",
        "person_id": person, "household_id": hh, "assignee_user_id": actor}, actor_user_id=actor, request_id="e2")["return_id"]
    doc = _document(person)
    with engine.connect() as c:
        signals = email_exact_signals(c, email)
    assert len(signals) == 2  # two candidate returns
    result = ingest_document(doc, signals)
    assert result["outcome"] == "proposed"  # ambiguous -> review, NOT auto-assigned


def test_missing_information_engine_opens_and_reasons():
    actor, person, hh, rid = _engagement()
    out = compute_missing(rid)
    assert out["missing_count"] >= 1
    assert all("reason" in m for m in out["missing"])


def test_duplicate_document_routes_to_review_not_silent_merge():
    actor, person, hh, rid = _engagement()
    with engine.connect() as c:
        item = c.execute(select(tax_checklist_items.c.id).where(
            tax_checklist_items.c.tax_engagement_return_id == rid, tax_checklist_items.c.required.is_(True))).first()
    shared = uuid.uuid4().hex
    doc1 = _document(person, sha=shared)
    with engine.connect() as c:
        sigs = portal_request_signals(c, item.id)
    ingest_document(doc1, sigs)  # accepted
    doc2 = _document(person, sha=shared)  # same hash
    result = ingest_document(doc2, sigs)
    assert result["outcome"] == "proposed"  # duplicate -> review, not silently accepted


# --- Authorization on reviewer actions --------------------------------------

def test_reviewer_cannot_accept_document_out_of_scope():
    actor, person, hh, rid = _engagement()
    doc = _document(person)
    # create a proposed link directly
    with engine.begin() as c:
        link_id = c.execute(tax_document_links.insert().values(document_id=doc, tax_engagement_return_id=rid,
            status="proposed", confidence=0.6, match_source="email_exact").returning(tax_document_links.c.id)).scalar_one()
    outsider = Principal(_user("out"), "x@e.com", "Out", frozenset({"tax.document.review"}))
    with pytest.raises(PermissionError):
        review_action(link_id, "accept", principal=outsider, request=_req())


def test_authorized_reviewer_accepts_and_writes_append_only_event():
    actor, person, hh, rid = _engagement()
    doc = _document(person)
    with engine.begin() as c:
        link_id = c.execute(tax_document_links.insert().values(document_id=doc, tax_engagement_return_id=rid,
            status="proposed", confidence=0.6, match_source="email_exact").returning(tax_document_links.c.id)).scalar_one()
    firm = Principal(_user("firm"), "f@e.com", "F", frozenset({"tax.document.review", "record.read_all"}))
    review_action(link_id, "accept", principal=firm, request=_req())
    with engine.connect() as c:
        assert c.scalar(select(tax_document_links.c.status).where(tax_document_links.c.id == link_id)) == "accepted"
        events = c.scalar(select(tax_document_review_events.c.id).where(
            tax_document_review_events.c.tax_document_link_id == link_id))
        assert events is not None


def test_review_event_ledger_is_append_only():
    actor, person, hh, rid = _engagement()
    doc = _document(person)
    with engine.begin() as c:
        link_id = c.execute(tax_document_links.insert().values(document_id=doc, tax_engagement_return_id=rid,
            status="proposed", confidence=0.6, match_source="email_exact").returning(tax_document_links.c.id)).scalar_one()
        ev = c.execute(tax_document_review_events.insert().values(tax_document_link_id=link_id, action="accept").returning(tax_document_review_events.c.id)).scalar_one()
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(tax_document_review_events.update().where(tax_document_review_events.c.id == ev).values(reason="tamper"))


def test_review_queue_is_scoped():
    actor, person, hh, rid = _engagement()
    doc = _document(person)
    with engine.begin() as c:
        c.execute(tax_document_links.insert().values(document_id=doc, tax_engagement_return_id=rid,
            status="proposed", confidence=0.6, match_source="email_exact"))
    outsider = Principal(_user("out"), "x@e.com", "Out", frozenset({"tax.read"}))
    assert review_queue(outsider)["count"] == 0  # not authorized for this return


# --- AI port is inert -------------------------------------------------------

def test_ai_classifier_port_is_inert():
    assert NoopAIClassifier().classify({"any": "document"}) is None
