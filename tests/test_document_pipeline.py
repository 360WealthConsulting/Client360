"""Document ingestion/extraction pipeline (owner-proposal edition).

Extraction + matching correctness (text PDF, Excel, HEIC/image, mixed-owner folder, content-overrides-
folder, lowercase names, source-contact email/phone, address corroboration, household, organization,
institution-not-owner, no-match, already-owned, permanent rejects) is covered in
tests/test_document_owner_proposal.py and reused unchanged. These tests cover the pipeline layer:
classification + year + routing, versioned proposal persistence (never ownership), the guarded future-
ingestion hook, failure isolation, and the read-only legacy batch.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db import (
    document_classifications,
    document_facts,
    document_ocr,
    documents,
    engine,
    people,
    person_source_links,
    source_contacts,
)
from app.services import document_pipeline as dp
from app.services.document_classification import classify_document
from app.services.document_sources import resolve_or_create_canonical

_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "abcdefghij")).capitalize()
# Alphabetic + capitalised so names built as f"First {_TAG}" are extractable by the content
# name matcher. A hex tag ("Jennifer a1b2c3d4") is not a name the extractor can see, so these
# fixtures used to reach HIGH on the email alone — the exact rule the safety patch removed.
_DOCS: list = []
_PEOPLE: list = []
_SC: list = []
_LINKS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(document_facts.delete().where(document_facts.c.document_id.in_(_DOCS)))
            c.execute(document_classifications.delete().where(
                document_classifications.c.document_id.in_(_DOCS)))
            c.execute(document_ocr.delete().where(document_ocr.c.document_id.in_(_DOCS)))
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _LINKS:
            c.execute(person_source_links.delete().where(person_source_links.c.id.in_(_LINKS)))
        if _SC:
            c.execute(source_contacts.delete().where(source_contacts.c.id.in_(_SC)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    for lst in (_DOCS, _PEOPLE, _SC, _LINKS):
        lst.clear()


def _person(full_name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _source_email(pid, email):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system="TaxDome", source_file="t.zip", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, email=email, raw_data={}).returning(source_contacts.c.id)).scalar_one()
        lid = c.execute(person_source_links.insert().values(
            person_id=pid, source_contact_id=sid, match_method="email", confirmed=True
        ).returning(person_source_links.c.id)).scalar_one()
    _SC.append(sid)
    _LINKS.append(lid)


def _doc(*, path=None, name="f.txt", person_id=None, tags=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"pl-{_TAG}-{uuid.uuid4().hex}", storage_path=str(path) if path else "x",
            storage_uri=str(path) if path else None, size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active", archived=False,
            tags=tags or {}).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        r = c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                      .where(documents.c.id == did)).first()
    return tuple(r)


# --- classification -------------------------------------------------------------------------------

def test_classifier_recognizes_1095a_and_8879():
    assert classify_document("Form1095a_2021.pdf", "Health Insurance Marketplace Statement")[0] == "1095-A"
    assert classify_document("8879.pdf", "IRS e-file Signature Authorization")[0] == "8879"


# --- analyze_document: classification + year + routing --------------------------------------------

def test_analyze_document_high_with_type_and_year(tmp_path):
    email = f"pipe-{_TAG}@mail.com"
    pid = _person(f"Zephyrina {_TAG}")
    _source_email(pid, email)
    f = tmp_path / "Form1095a_2021.pdf.txt"
    f.write_text(f"Form 1095-A Health Insurance Marketplace Statement\nDear Zephyrina {_TAG},\n"
                 f"contact {email}\n")
    did = _doc(path=f, name="Form1095a_2021.txt")
    r = dp.analyze_document(did)
    assert r["route"] == "HIGH" and r["proposed_entity_id"] == pid
    assert r["doc_type"] == "1095-A" and r["year"] == "2021"
    assert _owner(did) == (None, None, None)               # analysis writes nothing


def test_analyze_document_no_match_route(tmp_path):
    f = tmp_path / "Expenses.xlsx.txt"
    f.write_text("ADOBE ID CREATIVE CLD\nADOBE PHOTOGPHY PLAN\nLATER.COM\nTotal 42.00\n")
    did = _doc(path=f, name="Expenses.txt")
    r = dp.analyze_document(did)
    assert r["route"] == "NO_MATCH" and r["proposed_entity_id"] is None


def test_analyze_document_unsupported_route():
    did = _doc(path=None, name="archive.zip")             # no file / unsupported type -> no usable text
    r = dp.analyze_document(did)
    assert r["route"] == "UNSUPPORTED"


def test_analyze_document_skips_already_owned():
    did = _doc(name="x.txt", person_id=_person(f"Owner {_TAG}"))
    r = dp.analyze_document(did)
    assert r["eligible"] is False and r["route"] == "SKIPPED"


# --- persistence: versioned facts, never ownership ------------------------------------------------

def test_persist_proposal_writes_facts_not_ownership(tmp_path):
    email = f"persist-{_TAG}@mail.com"
    pid = _person(f"Thaddeus {_TAG}")
    _source_email(pid, email)
    f = tmp_path / "notice.txt"
    # Names the owner as well as carrying their unique email: an identifier alone is a lead, not an
    # owner, so a HIGH precondition must supply owner-positive identity.
    f.write_text(f"Please contact Thaddeus {_TAG} at {email} about your 2020 return\n")
    did = _doc(path=f, name="notice.txt")
    with engine.begin() as c:
        r = dp.analyze_document(did, conn=c)
        dp.persist_proposal(c, r)
    payload = dp.proposal_for_document(did)
    assert payload and payload["route"] == "HIGH" and payload["entity_id"] == pid
    with engine.connect() as c:
        cls = c.execute(select(document_classifications.c.doc_type)
                        .where(document_classifications.c.document_id == did)).scalar()
        facts = c.execute(select(document_facts.c.fact_type, document_facts.c.is_current)
                          .where(document_facts.c.document_id == did)).mappings().all()
    assert cls is not None
    assert [f for f in facts if f["fact_type"] == "owner_proposal" and f["is_current"]]
    assert _owner(did) == (None, None, None)               # NO ownership written


def test_persist_supersedes_prior_proposal(tmp_path):
    f = tmp_path / "n.txt"; f.write_text("no identity here\n")
    did = _doc(path=f, name="n.txt")
    with engine.begin() as c:
        for _ in range(2):
            dp.persist_proposal(c, dp.analyze_document(did, conn=c))
    with engine.connect() as c:
        rows = c.execute(select(document_facts.c.version, document_facts.c.is_current)
                         .where(document_facts.c.document_id == did,
                                document_facts.c.fact_type == "owner_proposal")).mappings().all()
    assert sum(1 for r in rows if r["is_current"]) == 1     # exactly one current
    assert max(r["version"] for r in rows) == 2             # version incremented


def test_no_full_ssn_in_persisted_payload(tmp_path):
    pid = _person(f"Jennifer {_TAG}")
    _source_email(pid, f"j-{_TAG}@mail.com")
    f = tmp_path / "w2.txt"
    f.write_text(f"SSN 123-45-6789 wage and tax statement for Jennifer {_TAG} j-{_TAG}@mail.com\n")
    did = _doc(path=f, name="w2.txt")
    with engine.begin() as c:
        dp.persist_proposal(c, dp.analyze_document(did, conn=c))
    import json
    with engine.connect() as c:
        raw = c.execute(select(document_facts.c.fact_value).where(
            document_facts.c.document_id == did,
            document_facts.c.fact_type == "owner_proposal")).scalar()
    assert "123-45-6789" not in (raw or "") and "123456789" not in (raw or "")
    json.loads(raw)                                          # valid JSON


# --- failure isolation ----------------------------------------------------------------------------

def test_analyze_and_persist_isolates_failure(monkeypatch, tmp_path):
    f = tmp_path / "n.txt"; f.write_text("hello\n")
    did = _doc(path=f, name="n.txt")
    monkeypatch.setattr(dp, "analyze_document", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with engine.begin() as c:
        out = dp.analyze_and_persist(did, conn=c)           # must not raise
    assert out is None
    assert _owner(did) == (None, None, None)                # no ownership despite failure


# --- future-ingestion hook ------------------------------------------------------------------------

def _sha():
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def test_ingestion_hook_generates_proposal(tmp_path):
    email = f"ingest-{_TAG}@mail.com"
    pid = _person(f"Ingestia {_TAG}")
    _source_email(pid, email)
    f = tmp_path / "arrival.txt"
    f.write_text(f"Statement for Ingestia {_TAG}, remit to {email}\n")
    res = resolve_or_create_canonical(
        sha256=_sha(), original_name="arrival.txt", stored_name=f"st-{_TAG}",
        storage_provider="local", storage_uri=str(f), storage_path=str(f), size_bytes=10,
        source_system="Upload")
    did = res["document_id"]; _DOCS.append(did)
    assert res["reused"] is False
    payload = dp.proposal_for_document(did)                  # auto-analyzed after ingestion
    assert payload and payload["route"] == "HIGH" and payload["entity_id"] == pid
    assert _owner(did) == (None, None, None)                # proposal only; no ownership guessed


def test_ingestion_succeeds_when_analysis_fails(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise RuntimeError("analysis exploded")
    monkeypatch.setattr(dp, "analyze_and_persist", _boom)
    f = tmp_path / "x.txt"; f.write_text("anything\n")
    res = resolve_or_create_canonical(                       # must still ingest despite the failure
        sha256=_sha(), original_name="x.txt", stored_name=f"bx-{_TAG}",
        storage_provider="local", storage_uri=str(f), storage_path=str(f), size_bytes=5,
        source_system="Upload")
    did = res["document_id"]; _DOCS.append(did)
    assert res["reused"] is False
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(documents.c.id == did)).scalar() == did


def test_ingestion_disabled_flag_skips_analysis(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "AUTO_ANALYZE_NEW_DOCUMENTS", False)
    f = tmp_path / "x.txt"; f.write_text("anything\n")
    res = resolve_or_create_canonical(
        sha256=_sha(), original_name="x.txt", stored_name=f"off-{_TAG}",
        storage_provider="local", storage_uri=str(f), storage_path=str(f), size_bytes=5,
        source_system="Upload")
    did = res["document_id"]; _DOCS.append(did)
    assert dp.proposal_for_document(did) is None             # not analyzed when disabled


# --- legacy read-only batch -----------------------------------------------------------------------

def test_batch_totals_and_buckets_and_no_mutation(tmp_path):
    email = f"batch-{_TAG}@mail.com"
    full_name = f"Batchy {_TAG}"
    pid = _person(full_name)
    _source_email(pid, email)
    hi = tmp_path / "hi.txt"; hi.write_text(f"Statement for {full_name}\nremit to {email}\n")
    no = tmp_path / "no.txt"; no.write_text("adobe creative cloud total 10\n")
    d_hi = _doc(path=hi, name="hi.txt")
    d_no = _doc(path=no, name="no.txt")
    out = dp.run_batch(include_details=True)
    assert out["total"] == sum(out["counts"].values())      # every analyzed doc bucketed exactly once
    routes = {d["document_id"]: d["route"] for d in out["details"]}
    assert routes.get(d_hi) == "HIGH" and routes.get(d_no) == "NO_MATCH"
    assert _owner(d_hi) == (None, None, None) and _owner(d_no) == (None, None, None)


def test_batch_excludes_permanent_rejects(monkeypatch, tmp_path):
    f = tmp_path / "r.txt"; f.write_text("hello\n")
    did = _doc(path=f, name="r.txt")
    monkeypatch.setattr(dp, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({did}))
    out = dp.run_batch(include_details=True)
    assert did not in {d["document_id"] for d in out["details"]}   # reject never analyzed/assignable


def test_batch_excludes_already_owned(tmp_path):
    owned = _doc(name="owned.txt", person_id=_person(f"Owned {_TAG}"))
    out = dp.run_batch(include_details=True)
    assert owned not in {d["document_id"] for d in out["details"]}


# --- OCR routing + stats --------------------------------------------------------------------------

def _ocr_cache(did, text):
    with engine.begin() as c:
        c.execute(document_ocr.insert().values(document_id=did, status="completed", text=text,
                                               char_count=len(text), engine="test"))


def test_analyze_uses_cached_ocr_text_and_routes_high(tmp_path):
    email = f"ocr-{_TAG}@mail.com"
    pid = _person(f"Ocrina {_TAG}")
    _source_email(pid, email)
    did = _doc(path=None, name="scan.jpg")                 # image with no native text
    _ocr_cache(did, f"Recipient Ocrina {_TAG}, remit to {email}")
    r = dp.analyze_document(did)
    assert r["route"] == "HIGH" and r["proposed_entity_id"] == pid
    assert r["extraction_method"] == "ocr_cache"


def test_batch_reports_ocr_stats(tmp_path):
    email = f"stat-{_TAG}@mail.com"
    full_name = f"Statina {_TAG}"
    pid = _person(full_name)
    _source_email(pid, email)
    did = _doc(path=None, name="scan2.jpg")
    _ocr_cache(did, f"Statement for {full_name}\nremit to {email}")
    out = dp.run_batch(include_details=True)
    assert set(out["stats"]) == {"ocr_extracted", "ocr_with_identity", "unsupported_remaining"}
    assert out["stats"]["ocr_extracted"] >= 1 and out["stats"]["ocr_with_identity"] >= 1
    assert {d["document_id"]: d["route"] for d in out["details"]}.get(did) == "HIGH"


def test_image_without_ocr_routes_unsupported():
    did = _doc(path=None, name="photo.heic")               # no file, no cache, batch native-only
    r = dp.analyze_document(did, ocr=False)
    assert r["route"] == "UNSUPPORTED"


# --- NEW_CLIENT_CANDIDATE (design only; disabled by default) --------------------------------------

def test_new_client_candidate_disabled_by_default(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text(f"please contact newperson-{_TAG}@nowhere.com about the account\n")
    did = _doc(path=f, name="n.txt")
    r = dp.analyze_document(did)
    assert r["route"] == "NO_MATCH"                         # unmatched email -> NO_MATCH while flag off


def test_new_client_candidate_when_enabled_creates_no_client(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "EMIT_NEW_CLIENT_CANDIDATE", True)
    with engine.connect() as c:
        before = len(c.execute(select(people.c.id)).all())
    f = tmp_path / "n.txt"
    f.write_text(f"please contact newperson2-{_TAG}@nowhere.com about the account\n")
    did = _doc(path=f, name="n.txt")
    r = dp.analyze_document(did)
    assert r["route"] == "NEW_CLIENT_CANDIDATE"
    with engine.connect() as c:
        after = len(c.execute(select(people.c.id)).all())
    assert after == before                                  # a document NEVER silently creates a client
    assert _owner(did) == (None, None, None)
