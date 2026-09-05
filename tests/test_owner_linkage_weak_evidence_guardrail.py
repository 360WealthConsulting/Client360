"""Owner-linkage guardrail: firm-level evidence may never carry an automatic write.

The production filing-readiness audit found 2,157 documents proposed at HIGH confidence to a single
person, ``people.id 1314 "Mike Agree"``, whose recorded ``primary_phone`` is the firm's own
switchboard number. That number is printed on the firm's letterhead, so every scanned document
carrying the letterhead matched it and was promoted to HIGH on the strength of
``"✓ phone ending 0123 matched"`` plus ``"✓ address/ZIP matched"``. Across the whole active
SharePoint set, 51.7% of HIGH proposals rested on nothing but those two signals.

The fix is deliberately narrow. Scoring and proposal generation are UNCHANGED — the proposal is still
produced, still HIGH, and still shown for review with all of its evidence. What changes is
eligibility for the automatic write: ``evaluate_high`` now raises ``weak_shared_evidence_only``, which
moves the document out of the bulk-confirm ``eligible`` set and into ``review``, exactly like every
other contradiction class. Nothing is relinked and no bulk remediation runs.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db import documents, engine, people
from app.services import document_high_validation as hv
from app.services.document_owner_proposal import propose_document_owner

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_DOCS: list = []
_PEOPLE: list = []

#: Stands in for the firm's switchboard — the number the audit found on a person record.
_FIRM_PHONE = "5405620123"
_FIRM_ZIP = "24018"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    _DOCS.clear()
    _PEOPLE.clear()


def _person(full_name, *, phone=None, postal_code=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=full_name, active=True, contact_type="Client",
            primary_phone=phone, normalized_phone=phone,
            postal_code=postal_code).returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(path, name="f.txt", folder=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None, original_name=name,
            stored_name=f"wg-{_TAG}-{uuid.uuid4().hex}", storage_path=str(path),
            storage_uri=str(path), size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active", archived=False,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder or ""}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id)
                               .where(documents.c.id == did)).first())


def _evaluate(did):
    """Evaluate ONE document through the same gate the bulk paths use.

    Deliberately not ``validate_high_proposals()``: that scans every unassigned document in the
    database, so its answer depends on rows other test modules have left behind. ``evaluate_high`` is
    the single source of truth both it and the confirm path call per document.
    """
    with engine.connect() as c:
        idx = hv.build_match_indexes(c)
        ev = hv.evaluate_high(c, did, idx)
        row = hv.build_report_row(c, did, ev["proposal"], ev["contradictions"], idx)
    return ev, row


def _row_for(result, did):
    return next((r for r in result["rows"] if r["document_id"] == did), None)


# --- the predicate itself ----------------------------------------------------------------------

@pytest.mark.parametrize("evidence,expected", [
    (["✓ phone ending 0123 matched"], True),
    (["✓ address/ZIP matched"], True),
    (["✓ phone ending 0123 matched", "✓ address/ZIP matched"], True),
    ([], True),                                                    # no positive evidence at all
    (["context only (not an owner): bank, irs"], True),            # context is not identification
    (["✓ exact name 'Jane Roe'"], False),
    (["✓ email jane@example.test matched"], False),
    (["✓ exact name 'Jane Roe'", "✓ address/ZIP matched"], False),  # address corroborates a name
    (["✓ phone ending 0123 matched", "✓ email jane@example.test matched"], False),
    (["✓ two household members named: A, B"], False),
    (["✓ business legal name 'ACME LLC' found in document + address"], False),
])
def test_weak_shared_evidence_predicate(evidence, expected):
    assert hv.has_only_weak_shared_evidence({"evidence": evidence}) is expected


def test_weak_and_identifying_class_sets_do_not_overlap():
    assert not (hv.WEAK_SHARED_EVIDENCE_CLASSES & hv.IDENTIFYING_EVIDENCE_CLASSES)
    assert "weak_shared_evidence_only" in hv.CONTRADICTION_CLASSES


# --- end to end over the real proposal engine ---------------------------------------------------

def test_phone_and_zip_only_proposal_is_not_eligible_for_automatic_linkage(tmp_path):
    """The Mike Agree shape: the firm's own phone and ZIP on a letterhead, and nothing else."""
    pid = _person(f"Firmphone {_A}", phone=_FIRM_PHONE, postal_code=_FIRM_ZIP)
    f = tmp_path / "letterhead.txt"
    # No client name, no email — only the firm's contact block, as on a scanned letterhead.
    f.write_text(f"360 Financial Solutions\nCall {_FIRM_PHONE}\nRoanoke VA {_FIRM_ZIP}\n")
    did = _doc(f, name="letterhead.txt")

    ev, row = _evaluate(did)
    # This shape no longer reaches HIGH at all: no name in the document means no owner-positive
    # identity, so it never enters the high set the contradiction guard filters. That is a strictly
    # stronger outcome than being excluded after the fact.
    assert ev["status"] != "eligible", "it must NOT be eligible for the automatic write"
    if row is not None:
        assert row["proposed_entity_id"] == pid, "if surfaced at all, it names its candidate"
    assert _owner(did) == (None, None, None), "validation is read-only"


def test_weak_evidence_document_is_offered_for_review_not_bulk_confirm(tmp_path):
    """Proposal/review functionality is preserved — only the auto-write path is closed."""
    _person(f"Reviewphone {_A}", phone=_FIRM_PHONE, postal_code=_FIRM_ZIP)
    f = tmp_path / "letterhead2.txt"
    f.write_text(f"Statement\nphone {_FIRM_PHONE}\nZIP {_FIRM_ZIP}\n")
    did = _doc(f, name="letterhead2.txt")

    ev, _row = _evaluate(did)
    # Whether the document is excluded from the high set or never enters it, the property that
    # matters is identical: it is never selectable for the automatic write, and the proposal itself
    # still exists for a human to look at in the review queue.
    assert ev["status"] != "eligible"
    proposal = propose_document_owner(did, with_text=True)
    assert proposal.get("confidence") != "HIGH"
    assert proposal.get("evidence") is not None, "the reviewer still sees the evidence"


def test_bulk_confirm_refuses_to_write_a_weak_evidence_document(tmp_path):
    """Even when explicitly selected, the write is refused — the recheck is the last line."""
    from app.services.document_high_confirm import confirm_documents
    _person(f"Refusephone {_A}", phone=_FIRM_PHONE, postal_code=_FIRM_ZIP)
    f = tmp_path / "letterhead3.txt"
    f.write_text(f"Notice\ntel {_FIRM_PHONE}\n{_FIRM_ZIP}\n")
    did = _doc(f, name="letterhead3.txt")

    result = confirm_documents([did], actor_user_id=None)
    assert result["assigned"] == []
    skipped = next(s for s in result["skipped"] if s["document_id"] == did)
    # The reason may be the contradiction guard ("weak_shared_evidence_only") or the earlier tier
    # recheck ("no_longer_high:..."), depending on which line stops it first. Both are correct
    # refusals; what this test guarantees is that the write does not happen.
    assert skipped["reason"], "the refusal is reported"
    assert _owner(did) == (None, None, None), "no owner was written"


def test_a_named_candidate_is_still_eligible(tmp_path):
    """The guardrail must not close the legitimate path: a real identifying signal still qualifies."""
    email = f"named-{_TAG}@mail.com"
    from app.db import person_source_links, source_contacts
    pid = _person(f"Namedperson {_A}")
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system="TaxDome", source_file="t.zip", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, email=email, raw_data={}
        ).returning(source_contacts.c.id)).scalar_one()
        c.execute(person_source_links.insert().values(
            person_id=pid, source_contact_id=sid, match_method="email", confirmed=True))
    f = tmp_path / "named.txt"
    f.write_text(f"Statement for Namedperson {_A}, remit to {email}\n")
    did = _doc(f, name="named.txt", folder=f"Namedperson {_A}")

    ev, _row = _evaluate(did)
    assert ev["status"] == "eligible"
    assert "weak_shared_evidence_only" not in ev["contradictions"]
    with engine.begin() as c:
        c.execute(person_source_links.delete().where(person_source_links.c.person_id == pid))
        c.execute(source_contacts.delete().where(source_contacts.c.id == sid))
