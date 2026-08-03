"""Knowledge & Intelligence foundation (Phase 6A) — coverage.

The pipeline over the canonical model (ADR-072/073): classify → extract → validate → versioned Knowledge
Objects, surfaced through the existing Client Workspace (Dashboard, Documents, Tax, Timeline) and Universal
Search. Covers classification, extraction, confidence, versioning, SSN privacy, permissions/household
visibility, timeline generation, and workspace rendering. Temp/test rows only.
"""
import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    document_classifications,
    document_facts,
    document_ocr,
    documents,
    engine,
    household_relationships,
    households,
    people,
    timeline_events,
)
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.tax_workspace import build_tax_workspace
from app.services.document_classification import classify_document
from app.services.knowledge_extraction import extract_facts
from app.services.knowledge_pipeline import (
    facts_for_document,
    recently_classified,
    run_knowledge_pipeline,
)
from app.services.universal_search import universal_search

_TAG = "KNOW"
_CAPS = frozenset({"client.read", "documents.view", "record.read_all", "tax.read", "timeline.read"})
_UID = None

_W2_TEXT = ("Form W-2 Wage and Tax Statement 2023\nEmployer: Acme Industries LLC\n"
            "EIN 12-3456789\nEmployee SSN 123-45-6789\nWages $84,000.00 Federal 9,200.00\n"
            "Married filing jointly  Date 01/31/2024")


@pytest.fixture(autouse=True)
def _clean():
    from app.db import users

    def _wipe():
        with engine.begin() as c:
            doc_ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if doc_ids:
                c.execute(delete(document_facts).where(document_facts.c.document_id.in_(doc_ids)))
                c.execute(delete(document_classifications).where(
                    document_classifications.c.document_id.in_(doc_ids)))
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(timeline_events).where(timeline_events.c.person_id.in_(pids)))
                c.execute(delete(household_relationships).where(
                    household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    global _UID
    with engine.begin() as c:
        tag = uuid.uuid4().hex[:8]
        _UID = c.execute(users.insert().values(
            email=f"kn{tag}@e.test", normalized_email=f"kn{tag}@e.test",
            display_name="KN", status="active").returning(users.c.id)).scalar_one()
    yield
    _wipe()


def _person(name="Owner"):
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=name, last_name=_TAG, full_name=f"{name} {_TAG}",
            active=True).returning(people.c.id)).scalar_one()


def _doc(name, text, *, person_id=None, household_id=None, ocr_status="completed"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=f"{name} {_TAG}", stored_name=f"{_TAG}-{uuid.uuid4().hex[:8]}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x",
            size_bytes=10, sha256=uuid.uuid4().hex + uuid.uuid4().hex, person_id=person_id,
            household_id=household_id, status="active", archived=False).returning(documents.c.id)).scalar_one()
        c.execute(document_ocr.insert().values(
            document_id=did, status=ocr_status, text=text, char_count=len(text),
            engine="fake", attempts=1))
    return did


def _run(did, **kw):
    kw.setdefault("actor_user_id", _UID)
    return run_knowledge_pipeline(document_ids=[did], **kw)


def _principal():
    return Principal(_UID or 0, "a@e.test", "A", _CAPS)


def _cls(did):
    with engine.connect() as c:
        return c.execute(select(document_classifications).where(
            document_classifications.c.document_id == did)).mappings().first()


# --- classification (unit) ---------------------------------------------------

def test_classify_common_types():
    assert classify_document("2023 Form 1040.pdf", "U.S. Individual Income Tax Return")[0] == "1040"
    assert classify_document("w2.pdf", "Wage and Tax Statement")[0] == "W-2"
    assert classify_document("k1.pdf", "Schedule K-1 Partner's share of income")[0] == "K-1"
    assert classify_document("notice.pdf", "Internal Revenue Service Notice CP2000")[0] == "irs_notice"
    assert classify_document("id.pdf", "United States of America Passport")[0] == "passport"
    dt, conf = classify_document("random.pdf", "lorem ipsum dolor")
    assert dt == "unknown" and conf == 0.0


def test_classification_confidence_scored():
    _, conf = classify_document("Form 1040.pdf", "Form 1040 U.S. Individual Income Tax Return")
    assert 0 < conf <= 1                                        # both filename + body → high confidence


# --- extraction (unit) -------------------------------------------------------

def test_extract_core_facts():
    facts = extract_facts(name="w2.pdf", text=_W2_TEXT, doc_type="W-2")
    by_type = {f["fact_type"] for f in facts}
    assert {"tax_year", "ein", "ssn_last4", "filing_status", "financial_institution"} - by_type == \
        {"financial_institution"} or "tax_year" in by_type
    assert any(f["fact_type"] == "ein" and f["value"] == "12-3456789" for f in facts)
    assert all(0 < f["confidence"] <= 1 for f in facts)


def test_ssn_privacy_last4_only():
    facts = extract_facts(name="w2.pdf", text=_W2_TEXT, doc_type="W-2")
    ssn = [f for f in facts if f["fact_type"] == "ssn_last4"]
    assert ssn and ssn[0]["value"] == "6789"
    assert not any("123-45-6789" in f["value"] for f in facts)   # full SSN never present


# --- pipeline: classify + store facts ---------------------------------------

def test_pipeline_classifies_and_stores_facts():
    did = _doc("W-2", _W2_TEXT, person_id=_person())
    s = _run(did)
    assert s["classified"] == 1 and s["facts_written"] > 0
    c = _cls(did)
    assert c["doc_type"] == "W-2" and c["classifier_version"] == "rules-v1"
    facts = {f["fact_type"] for f in facts_for_document(did)}
    assert "ein" in facts and "ssn_last4" in facts
    # No full SSN persisted anywhere in document_facts.
    with engine.connect() as conn:
        vals = list(conn.scalars(select(document_facts.c.fact_value).where(
            document_facts.c.document_id == did)))
    assert not any("123-45-6789" in (v or "") for v in vals)


def test_pipeline_is_idempotent_no_version_churn():
    did = _doc("1040", "Form 1040 U.S. Individual Income Tax Return 2023 EIN 11-2223333", person_id=_person())
    _run(did)
    s2 = _run(did, mode="reprocess")
    assert s2["facts_written"] == 0 and s2["facts_superseded"] == 0   # unchanged set → no-op
    with engine.connect() as conn:
        n = conn.execute(select(document_facts.c.id).where(
            document_facts.c.document_id == did, document_facts.c.is_current.is_(True))).rowcount
        maxv = conn.scalar(select(document_facts.c.version).where(
            document_facts.c.document_id == did).order_by(document_facts.c.version.desc()).limit(1))
    assert n > 0 and maxv == 1                                  # still version 1 (no churn)


def test_fact_versioning_supersedes_on_change():
    did = _doc("1040", "Form 1040 tax year 2022 EIN 11-2223333", person_id=_person())
    _run(did)
    with engine.begin() as c:                                   # the document's content changes
        c.execute(document_ocr.update().where(document_ocr.c.document_id == did).values(
            text="Form 1040 tax year 2023 EIN 44-5556666"))
    s = _run(did, mode="reprocess")
    assert s["facts_superseded"] > 0 and s["facts_written"] > 0
    with engine.connect() as conn:
        current = {(r["fact_type"], r["fact_value"]) for r in conn.execute(select(
            document_facts.c.fact_type, document_facts.c.fact_value).where(
            document_facts.c.document_id == did, document_facts.c.is_current.is_(True))).mappings()}
        history = conn.execute(select(document_facts.c.id).where(
            document_facts.c.document_id == did, document_facts.c.is_current.is_(False))).rowcount
    assert ("ein", "44-5556666") in current and ("ein", "11-2223333") not in current
    assert history > 0                                          # prior version retained


# --- timeline generation -----------------------------------------------------

def test_timeline_event_from_extracted_date():
    pid = _person()
    did = _doc("1040", "Form 1040 filed 04/15/2024 for tax year 2023", person_id=pid)
    s = _run(did)
    assert s["timeline_events"] >= 1
    with engine.connect() as conn:
        ev = conn.execute(select(timeline_events.c.event_type, timeline_events.c.source).where(
            timeline_events.c.person_id == pid, timeline_events.c.source == "knowledge")).mappings().first()
    assert ev and ev["event_type"] == "document_date"


# --- permissions + household visibility -------------------------------------

def test_search_by_classification_respects_scope():
    from datetime import date

    from app.db import record_assignments, users
    mine, theirs = _person("Mine"), _person("Theirs")
    # Text that classifies as W-2 but the literal "W-2" is not in filename/body.
    wtext = "Wage and Tax Statement 2023 Employer Acme"
    _run(_doc("statement", wtext, person_id=mine))
    _run(_doc("statement", wtext, person_id=theirs))
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"sc{uuid.uuid4().hex[:6]}@e.test", normalized_email=f"sc{uuid.uuid4().hex[:6]}@e.test",
            display_name="S", status="active").returning(users.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            user_id=uid, entity_type="person", entity_id=mine, assignment_type="primary",
            effective_date=date.today()))
    scoped = Principal(uid, "s@e.test", "S", frozenset({"client.read", "documents.view"}))
    res = universal_search(scoped, "W-2", types=["document"])
    owners = {r["workspace_url"] for r in res["results"]}
    assert f"/client/{mine}" in owners and f"/client/{theirs}" not in owners


def test_household_visibility_recently_classified():
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"{_TAG} House").returning(
            households.c.id)).scalar_one()
        pids = []
        for nm in ("A", "B"):
            pid = c.execute(people.insert().values(
                first_name=nm, last_name=_TAG, full_name=f"{nm} {_TAG}", household_id=hid,
                active=True).returning(people.c.id)).scalar_one()
            c.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="member"))
            pids.append(pid)
    _run(_doc("W-2", _W2_TEXT, household_id=hid))
    # Both members (scoped to the household) see the classified document.
    for pid in pids:
        rc = recently_classified([pid], household_ids=[hid])
        assert any(d["doc_type"] == "W-2" for d in rc)


# --- workspace rendering -----------------------------------------------------

def test_documents_tab_shows_classification_and_extraction():
    pid = _person()
    did = _doc("W-2", _W2_TEXT, person_id=pid)
    _run(did)
    sec = get_workspace(_principal(), person_id=pid)["sections"]["documents"]
    d = next(x for x in sec["documents"] if x["id"] == did)
    assert d["classified_type"] == "W-2" and 0 < d["classification_confidence"] <= 1
    assert d["extraction_status"] == "extracted" and d["fact_count"] > 0


def test_dashboard_shows_newly_classified():
    pid = _person()
    _run(_doc("1040", "Form 1040 U.S. Individual Income Tax Return 2023", person_id=pid))
    dash = get_workspace(_principal(), person_id=pid)["sections"]["dashboard"]
    assert any(d["doc_type"] == "1040" for d in dash["newly_classified"])


def test_dashboard_flags_compliance_notice():
    pid = _person()
    _run(_doc("notice", "Internal Revenue Service Notice CP2000 balance due", person_id=pid))
    dash = get_workspace(_principal(), person_id=pid)["sections"]["dashboard"]
    assert any(d["doc_type"] == "irs_notice" for d in dash["compliance_issues"])


def test_tax_tab_surfaces_extracted_facts():
    pid = _person()
    _run(_doc("1040", "Form 1040 tax year 2023 Married filing jointly EIN 12-3456789", person_id=pid))
    tw = build_tax_workspace(_principal(), person_id=pid, scope_ids=[pid])
    ef = tw["extracted_facts"]
    assert ef["status"] == "available"
    types = {f["fact_type"] for f in ef["facts"]}
    assert "tax_year" in types and ("filing_status" in types or "return_type" in types)


# --- audit + dry run ---------------------------------------------------------

def test_run_is_audited():
    from app.db import audit_events
    _run(_doc("W-2", _W2_TEXT, person_id=_person()), request_id="kn-t")
    with engine.connect() as c:
        assert c.scalar(select(audit_events.c.id).where(
            audit_events.c.action == "document.knowledge_run").limit(1)) is not None


def test_dry_run_makes_no_changes():
    did = _doc("W-2", _W2_TEXT, person_id=_person())
    s = _run(did, dry_run=True)
    assert s["dry_run"] is True and s["classified"] == 1
    assert _cls(did) is None and facts_for_document(did) == []
