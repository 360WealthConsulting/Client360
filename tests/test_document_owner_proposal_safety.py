"""Owner-proposal safety: HIGH requires owner-positive identity.

Production evidence this pins down. One person (id 1314) held 2,157 HIGH proposals — 38.9% of every
HIGH proposal in the corpus, against 63 for the next-highest person. 1,707 of them rested on
``address/ZIP + phone`` with NO name match and a further 160 on phone alone, because ``_confidence``
returned HIGH for ``"phone" in sigs`` on any non-Drake document. The preparer/ERO guard existed but
was wired to ``tax_document=drake_source``, so it was inactive for 100% of SharePoint documents.
Separately, "Wells Fargo" and "Edward Jones" are rows in ``people`` and could be proposed as owners.

The rules asserted here:

* contact evidence alone never establishes ownership — HIGH needs the person's NAME plus a value
  that is theirs and nobody else's;
* a value shared across people (office line, town ZIP) is context, never corroboration;
* tax paperwork is identified from the filed category, not from the source system;
* HIGH requires POSITIVE OWNER ELIGIBILITY — the record must be provably a client (Drake
  taxpayer/spouse, household member, existing document owner, or CRM "Client-*") before it can be a
  confident owner. Absence of that proof caps the tier; it never asserts a counterparty.

No name list decides anything. An earlier revision blacklisted institution brands and it was wrong in
both directions: it erased person 5583, a real 1040-filing client named "Edward Jones", while missing
every institution with an ordinary-sounding name. ``looks_like_institution_name`` survives as reviewer
context only, and these tests pin that it cannot change a tier.

No test here references person 1314. The defence is structural, so the fix must hold for any person.
"""
from __future__ import annotations

import pytest

from app.services.document_owner_proposal import (
    _confidence,
    _mark_shared_values,
    analyze_identity,
    is_tax_document,
    looks_like_institution_name,
)

CLIENT_PID, OTHER_PID, STUB_PID = 11, 12, 13
CLIENT_PHONE, SHARED_PHONE = "5405551234", "5405559999"
CLIENT_ZIP = "24153"


def _idx():
    """A small canonical index: one real client, one client sharing the office line, one bare stub."""
    idx = {
        "email": {"jane@example.com": {CLIENT_PID}},
        "phone": {CLIENT_PHONE: {CLIENT_PID}, SHARED_PHONE: {CLIENT_PID, OTHER_PID}},
        "name": {"jane doe": [CLIENT_PID], "wells fargo": [STUB_PID]},
        "first_last": {("jane", "doe"): [CLIENT_PID]},
        "members": {}, "hh_name": {}, "biz": {}, "inst": set(),
        "pid": {
            CLIENT_PID: {"name": "Jane Doe", "email": "jane@example.com", "phone": CLIENT_PHONE,
                         "household_id": None, "zips": {CLIENT_ZIP}, "streets": {"12 elm st"}},
            OTHER_PID: {"name": "John Roe", "email": None, "phone": SHARED_PHONE,
                        "household_id": None, "zips": {CLIENT_ZIP}, "streets": set()},
            STUB_PID: {"name": "Wells Fargo", "email": None, "phone": None,
                       "household_id": None, "zips": set(), "streets": set()},
        },
    }
    _mark_shared_values(idx)
    # Set positively, exactly as _mark_owner_eligibility derives it from Drake / household / owned
    # documents / CRM "Client-*". STUB_PID is absent because nothing in the data says it is a client
    # — not because of what it is called.
    idx["owner_eligible"] = {CLIENT_PID, OTHER_PID}
    idx["staff"] = set()
    return idx


# ---------------------------------------------------------------- A/B: contact-only never HIGH

@pytest.mark.parametrize("sigs", [
    {"phone"},                    # A — phone only
    {"zip"},                      # ZIP only
    {"street"},                   # address only
    {"email"},                    # generic email only
    {"phone", "zip"},             # B — shared phone + ZIP, no name
    {"phone", "street", "zip"},   # every contact signal, still no name
])
def test_contact_evidence_without_a_name_is_never_high(sigs):
    assert _confidence(sigs, unique_name=True) != "HIGH"


def test_phone_plus_zip_without_name_is_the_exact_production_pattern():
    """1,707 of the 2,157 HIGH proposals looked exactly like this."""
    assert _confidence({"phone", "zip"}, unique_name=True, tax_document=False) == "MEDIUM"


# ---------------------------------------------------------------- C: shared values cannot corroborate

def test_shared_contact_block_cannot_produce_high():
    """A name plus a phone that belongs to several people is not owner-specific."""
    assert _confidence({"name", "phone"}, unique_name=True, shared={"phone"}) == "MEDIUM"


def test_unshared_contact_still_corroborates():
    assert _confidence({"name", "phone"}, unique_name=True, shared=frozenset(),
                       owner_eligible=True) == "HIGH"


def test_shared_values_are_detected_from_the_data_not_configured():
    idx = _idx()
    assert SHARED_PHONE in idx["shared"]["phone"]      # two people hold it
    assert CLIENT_PHONE not in idx["shared"]["phone"]  # one person holds it
    assert CLIENT_ZIP in idx["shared"]["zip"]          # a ZIP spans people


def test_zip_never_corroborates_even_when_unshared():
    """ZIP is excluded from owner-specific signals entirely, shared or not."""
    assert _confidence({"name", "zip"}, unique_name=True, shared=frozenset()) == "MEDIUM"


# ---------------------------------------------------------------- D: tax-document rules by category

@pytest.mark.parametrize("row,expected", [
    ({"category": "tax_document"}, True),
    ({"classification": "tax_return"}, True),
    ({"subcategory": "Tax Form"}, True),
    ({"category": "statement"}, False),
    ({"category": None, "classification": None, "subcategory": None}, False),
])
def test_tax_document_is_identified_from_filed_columns(row, expected):
    assert is_tax_document(row) is expected


def test_tax_guard_no_longer_depends_on_drake_alone():
    """A SharePoint tax document (no Drake source) must still get the guard."""
    assert is_tax_document({"category": "tax_document"}, drake_source=False) is True
    assert is_tax_document({"category": "statement"}, drake_source=True) is True   # Drake still counts


def test_name_only_on_a_tax_document_is_not_high():
    assert _confidence({"name"}, unique_name=True, tax_document=True, owner_eligible=True) == "LOW"


def test_tax_document_name_plus_unique_identifier_may_be_high():
    assert _confidence({"name", "email"}, unique_name=True, tax_document=True,
                       owner_eligible=True) == "HIGH"


def test_tax_document_name_plus_shared_identifier_is_not_high():
    assert _confidence({"name", "phone"}, unique_name=True, tax_document=True,
                       shared={"phone"}, owner_eligible=True) == "LOW"


# ---------------------------------------------------------------- E: legitimate matching survives

def test_exact_name_plus_unique_email_is_still_high():
    text = "Statement for Jane Doe, jane@example.com, account summary enclosed."
    out = analyze_identity(text, "statement.pdf", None, _idx())
    assert out["confidence"] == "HIGH"
    assert out["proposed_entity_id"] == CLIENT_PID


def test_exact_name_plus_unique_phone_is_still_high():
    text = f"Jane Doe  phone {CLIENT_PHONE[:3]}-{CLIENT_PHONE[3:6]}-{CLIENT_PHONE[6:]}"
    out = analyze_identity(text, "letter.pdf", None, _idx())
    assert out["confidence"] == "HIGH"
    assert out["proposed_entity_id"] == CLIENT_PID


def test_name_alone_off_a_tax_document_is_medium_not_high():
    out = analyze_identity("Correspondence regarding Jane Doe.", "note.pdf", None, _idx())
    assert out["confidence"] in ("MEDIUM", "AMBIGUOUS", "NO_MATCH")
    assert out["confidence"] != "HIGH"


# ---------------------------------------------------------------- F: eligibility, not names

def _eligible(idx, *pids):
    """Mark pids owner-eligible, as Drake/household/ownership/CRM evidence would."""
    idx["owner_eligible"] = set(pids)
    return idx


@pytest.mark.parametrize("name", [
    # Fictional institutions that appear in NO list anywhere. Under a blacklist these reached HIGH;
    # under positive eligibility they cannot, because nothing says they are clients.
    "Sterling Meridian Partners", "Halloway Whitfield Group", "Ashford Trust Advisors",
    "Blue Ridge National Bank", "Cascadia Credit Union", "Summit Mutual Insurance",
    "Wells Fargo", "Liberty University", "Roanoke College",
])
def test_unknown_organization_cannot_reach_high(name):
    """The generalisation the brand list could never make: unknown != named."""
    idx = _idx()
    key = name.lower()
    idx["name"][key] = [STUB_PID]
    idx["first_last"][(key.split()[0], key.split()[-1])] = [STUB_PID]
    idx["pid"][STUB_PID] = {"name": name, "email": "ap@stub.example.com", "phone": "5405558888",
                            "household_id": None, "zips": {CLIENT_ZIP}, "streets": {"9 oak ave"}}
    idx["email"]["ap@stub.example.com"] = {STUB_PID}
    idx["phone"]["5405558888"] = {STUB_PID}
    _mark_shared_values(idx)
    _eligible(idx, CLIENT_PID, OTHER_PID)          # STUB has contact details but no client evidence
    out = analyze_identity(
        f"{name} statement of account. Contact ap@stub.example.com or 540-555-8888.",
        "stmt.pdf", None, idx)
    assert out["confidence"] != "HIGH"


def test_edward_jones_shaped_client_can_reach_high():
    """DECISION 1: a real client is not suppressed because their name matches a brand.

    Person 5583 in production is named "Edward Jones", is a Wealthbox ``type=Person`` with
    first/last name populated, and files 1040s with the firm as taxpayer. The blacklist erased his
    name signal entirely. With eligibility proven from that Drake evidence, he behaves like any
    other client. No person id is special-cased anywhere.
    """
    idx = _idx()
    idx["name"]["edward jones"] = [STUB_PID]
    idx["first_last"][("edward", "jones")] = [STUB_PID]
    idx["pid"][STUB_PID] = {"name": "Edward Jones", "email": "ejones@example.com", "phone": "5405557777",
                            "household_id": None, "zips": {CLIENT_ZIP}, "streets": {"4 pine ct"}}
    idx["email"]["ejones@example.com"] = {STUB_PID}
    _mark_shared_values(idx)
    _eligible(idx, STUB_PID)                        # Drake taxpayer role on three 1040s
    out = analyze_identity("Statement for Edward Jones, ejones@example.com", "s.pdf", None, idx)
    assert out["proposed_entity_id"] == STUB_PID
    assert out["confidence"] == "HIGH"


def test_owner_eligibility_ignores_the_name_entirely():
    """Identical evidence, two names: the tier must not move."""
    def run(name):
        idx = _idx()
        key = name.lower()
        idx["name"][key] = [STUB_PID]
        idx["first_last"][(key.split()[0], key.split()[-1])] = [STUB_PID]
        idx["pid"][STUB_PID] = {"name": name, "email": "x@example.com", "phone": None,
                                "household_id": None, "zips": set(), "streets": set()}
        idx["email"]["x@example.com"] = {STUB_PID}
        _mark_shared_values(idx)
        _eligible(idx, STUB_PID)
        return analyze_identity(f"Statement for {name}, x@example.com", "s.pdf", None, idx)["confidence"]

    assert run("First National Bank") == run("Britt Sebastian")


def test_non_owner_eligible_candidate_never_reaches_high():
    """The core gate, stated directly."""
    for sigs in ({"name", "email"}, {"name", "phone"}, {"name", "street"}, {"name", "email", "phone"}):
        assert _confidence(sigs, unique_name=True, owner_eligible=False) != "HIGH"
        assert _confidence(sigs, unique_name=True, owner_eligible=True) == "HIGH"


def test_ineligible_candidate_is_capped_not_buried():
    """Not-yet-classified real clients must stay visible for review, not vanish."""
    assert _confidence({"name", "email"}, unique_name=True, owner_eligible=False) == "MEDIUM"
    assert _confidence({"name"}, unique_name=True, owner_eligible=False) == "MEDIUM"


def test_sparse_client_with_household_evidence_is_eligible():
    """FALSE-NEGATIVE guard: 1,103 production clients carry no contact details at all."""
    idx = _idx()
    idx["pid"][STUB_PID] = {"name": "Sparse Client", "email": None, "phone": None,
                            "household_id": 900, "zips": set(), "streets": set()}
    idx["name"]["sparse client"] = [STUB_PID]
    idx["first_last"][("sparse", "client")] = [STUB_PID]
    idx["members"][900] = {STUB_PID}
    _mark_shared_values(idx)
    _eligible(idx, STUB_PID)                        # household membership alone qualifies
    out = analyze_identity("Notice for Sparse Client", "n.pdf", None, idx)
    assert out["proposed_entity_id"] == STUB_PID
    assert out["confidence"] in ("MEDIUM", "HIGH")   # visible, not suppressed


# ---------------------------------------------------------------- G: the name check is advisory

def test_institution_name_check_does_not_gate_confidence():
    """DECISION 2: ``looks_like_institution_name`` must not appear in the confidence path."""
    import inspect

    from app.services import document_owner_proposal as m

    for fn in (m._confidence, m.analyze_identity):
        assert "looks_like_institution_name" not in inspect.getsource(fn)
    # And it decides nothing here either: an institution-named ELIGIBLE record still reaches HIGH.
    assert looks_like_institution_name("Edward Jones Bank") is True
    assert _confidence({"name", "email"}, unique_name=True, owner_eligible=True) == "HIGH"


@pytest.mark.parametrize("name", [
    # Every one of these is a REAL person in production that unanchored substring matching flagged
    # as an institution: "irs" inside Kirsten / Hairston, "bank" inside the five surnames.
    "Kirsten Filiberto", "Maria Regla Hairston", "Paul Banker", "Darin Eubank",
    "Todd Bankhead", "Clark Brockbank", "Gary Eubanks",
    "Jane Doe", "Britt Sebastian", "Mignard Company",
])
def test_real_client_names_are_not_flagged_as_institutions(name):
    assert looks_like_institution_name(name) is False


def test_keyword_matching_is_whole_word_not_substring():
    """The collision defect itself, pinned: a keyword must not match inside a longer word."""
    assert looks_like_institution_name("Bankhead") is False
    assert looks_like_institution_name("First National Bank") is True
    assert looks_like_institution_name("Kirsten") is False
    assert looks_like_institution_name("IRS") is True


@pytest.mark.parametrize("name", [
    "Kirsten Filiberto", "Todd Bankhead", "Gary Eubanks", "Maria Regla Hairston",
    "Paul Banker", "Darin Eubank", "Clark Brockbank",
])
def test_surname_collisions_still_reach_high_when_eligible(name):
    """The seven real clients must be able to own their documents normally."""
    idx = _idx()
    key = name.lower()
    idx["name"][key] = [STUB_PID]
    idx["first_last"][(key.split()[0], key.split()[-1])] = [STUB_PID]
    idx["pid"][STUB_PID] = {"name": name, "email": "real@example.com", "phone": None,
                            "household_id": None, "zips": set(), "streets": set()}
    idx["email"]["real@example.com"] = {STUB_PID}
    _mark_shared_values(idx)
    _eligible(idx, STUB_PID)
    out = analyze_identity(f"Tax notice for {name}, real@example.com", "n.pdf", None, idx)
    assert out["proposed_entity_id"] == STUB_PID
    assert out["confidence"] == "HIGH"


def test_wells_fargo_cannot_own_a_document():
    """Capped by eligibility, not erased by name — and that distinction is the point.

    The blacklist used to drop the name signal entirely, so this row was invisible. That same
    mechanism erased a real client (see ``test_edward_jones_shaped_client_can_reach_high``). Now the
    name matches like any other, the record is surfaced for a human at a low tier, and it cannot
    reach HIGH because nothing in the data shows it is a client we file documents for. Production
    agrees: Wells Fargo (person 116) owns 0 documents, has no Drake return, no household and no
    relationship entity.
    """
    out = analyze_identity("Wells Fargo checking statement, period ending June 30.",
                           "stmt.pdf", None, _idx())
    assert out["confidence"] != "HIGH"
    assert STUB_PID not in _idx()["owner_eligible"]


def test_preparer_contact_on_a_tax_document_does_not_become_owner():
    """Firm phone + firm ZIP on a return, no client name: the old engine returned HIGH."""
    text = ("Form 1040 U.S. Individual Income Tax Return. Paid preparer use only. "
            f"Office {SHARED_PHONE[:3]}-{SHARED_PHONE[3:6]}-{SHARED_PHONE[6:]}  Salem VA {CLIENT_ZIP}")
    out = analyze_identity(text, "return.pdf", None, _idx())
    assert out["confidence"] != "HIGH"


# ---------------------------------------------------------------- I/J/K: fail closed, Drake, non-tax

def test_ambiguous_shared_name_fails_closed():
    idx = _idx()
    idx["name"]["jane doe"] = [CLIENT_PID, OTHER_PID]
    idx["pid"][OTHER_PID]["name"] = "Jane Doe"
    out = analyze_identity("Jane Doe", "x.pdf", None, idx)
    assert out["confidence"] in ("AMBIGUOUS", "NO_MATCH", "LOW")
    assert out["confidence"] != "HIGH"


def test_drake_documents_keep_the_tax_guard():
    assert is_tax_document({}, drake_source=True) is True
    assert _confidence({"name"}, unique_name=True, tax_document=True, owner_eligible=True) == "LOW"


def test_non_tax_legitimate_matching_still_works():
    out = analyze_identity("Jane Doe 12 Elm St", "deed.pdf", None, _idx())
    assert out["proposed_entity_id"] == CLIENT_PID
    assert out["confidence"] == "HIGH"          # name + unshared street


# ---------------------------------------------------------------- L: no per-person special case

def test_mass_match_tripwire_flags_nameless_concentration():
    """The shape that produced 2,157 HIGH proposals for one person: many HIGH, none naming them."""
    from app.services.document_owner_proposal import mass_match_tripwire
    bad = [{"proposed_entity_type": "person", "proposed_entity_id": 99,
            "proposed_entity_name": "Someone", "confidence": "HIGH",
            "evidence": ["✓ phone ending 0123 matched", "• ZIP matched"]} for _ in range(80)]
    flagged = mass_match_tripwire(bad)
    assert len(flagged) == 1
    assert flagged[0]["entity_id"] == 99 and flagged[0]["high_proposals"] == 80


def test_mass_match_tripwire_does_not_punish_a_legitimate_bulk_client():
    """A client with many documents that DO name them trips nothing — it is not a cap."""
    from app.services.document_owner_proposal import mass_match_tripwire
    good = [{"proposed_entity_type": "person", "proposed_entity_id": 7,
             "proposed_entity_name": "Jane Doe", "confidence": "HIGH",
             "evidence": ["✓ exact name 'Jane Doe'", "✓ email jane@example.com matched"]}
            for _ in range(400)]
    assert mass_match_tripwire(good) == []


def test_mass_match_tripwire_holds_rather_than_acts():
    """It reports; it must not mutate, assign or drop anything."""
    from app.services.document_owner_proposal import mass_match_tripwire
    src = [{"proposed_entity_type": "person", "proposed_entity_id": 5, "confidence": "HIGH",
            "proposed_entity_name": "X", "evidence": ["✓ phone matched"]} for _ in range(60)]
    snapshot = [dict(p) for p in src]
    mass_match_tripwire(src)
    assert src == snapshot


def _executable_source(path):
    """Source with comments and string literals removed, so only CODE is inspected.

    The rationale for these rules is documented at length in the module — including the names and
    the person id that motivated them — and prose must not trip a guard aimed at runtime behaviour.
    What may never appear is an identity baked into an expression.
    """
    import io as _io
    import tokenize
    from pathlib import Path

    out = []
    with _io.StringIO(Path(path).read_text(encoding="utf-8")) as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


# ------------------------------------------------- H: signals the production audit REJECTED

def _db_eligibility(rows):
    """Insert people, build the real indexes against the DB, return (eligible, staff, ids)."""
    import uuid

    from app.db import engine
    from app.db import people as people_t
    from app.services.document_owner_proposal import build_match_indexes

    ids = []
    with engine.begin() as c:
        for kw in rows:
            ids.append(c.execute(people_t.insert().values(
                full_name=f"Sig {uuid.uuid4().hex[:6]}", active=True, **kw
            ).returning(people_t.c.id)).scalar_one())
    try:
        with engine.connect() as c:
            idx = build_match_indexes(c)
        return idx["owner_eligible"], idx["staff"], ids
    finally:
        with engine.begin() as c:
            c.execute(people_t.delete().where(people_t.c.id.in_(ids)))


def test_existing_document_ownership_is_not_eligibility_evidence():
    """Rejected as circular: 1 of 29,896 owned documents has an ownership-resolution audit event.

    A past linkage of unknown provenance must not authorise its own repetition. Production makes the
    danger concrete: person 4028 is a CRM *Prospect* who already owns 56 documents — under the old
    rule that alone would have made them owner-eligible and let name matches reach HIGH.
    """
    import hashlib
    import uuid

    from app.db import documents, engine
    from app.db import people as people_t
    from app.services.document_owner_proposal import build_match_indexes

    with engine.begin() as c:
        pid = c.execute(people_t.insert().values(
            full_name=f"Owns {uuid.uuid4().hex[:6]}", active=True
        ).returning(people_t.c.id)).scalar_one()
        did = c.execute(documents.insert().values(
            person_id=pid, original_name="x.txt", stored_name=f"own-{uuid.uuid4().hex}",
            storage_path="x", storage_uri="x", size_bytes=1,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active", archived=False
        ).returning(documents.c.id)).scalar_one()
    try:
        with engine.connect() as c:
            idx = build_match_indexes(c)
        assert pid not in idx["owner_eligible"], "owning a document must not confer eligibility"
    finally:
        with engine.begin() as c:
            c.execute(documents.delete().where(documents.c.id == did))
            c.execute(people_t.delete().where(people_t.c.id == pid))


def test_household_membership_alone_is_not_eligibility_evidence():
    """Rejected on evidence, not caution.

    Of the 122 production people this signal alone would admit, 113 sit in households containing NO
    member with any client evidence, and 5 are explicitly CRM prospects. A household groups related
    people; it does not assert that they are clients. Real client members carry Drake or CRM
    evidence anyway (456 of 596 do), so nothing legitimate depends on this rule.
    """
    import uuid

    from app.db import engine, households
    from app.db import people as people_t
    from app.services.document_owner_proposal import build_match_indexes

    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {uuid.uuid4().hex[:6]}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(people_t.insert().values(
            full_name=f"Member {uuid.uuid4().hex[:6]}", active=True, household_id=hid
        ).returning(people_t.c.id)).scalar_one()
    try:
        with engine.connect() as c:
            idx = build_match_indexes(c)
        assert pid not in idx["owner_eligible"], "household membership must not confer eligibility"
    finally:
        with engine.begin() as c:
            c.execute(people_t.delete().where(people_t.c.id == pid))
            c.execute(households.delete().where(households.c.id == hid))


def test_canonical_contact_type_client_confers_eligibility():
    """The one per-person column that does count — and the field the backfill will populate."""
    eligible, _staff, ids = _db_eligibility([{"contact_type": "Client - Tax ELP"},
                                             {"contact_type": "Prospect"},
                                             {"contact_type": None}])
    assert ids[0] in eligible
    assert ids[1] not in eligible, "a prospect is not an owner"
    assert ids[2] not in eligible, "an unclassified row is not an owner"


def test_staff_is_detected_from_the_canonical_email_too():
    """A staff identity whose firm address sits only on the canonical row was previously missed."""
    from app.db import engine, users

    with engine.connect() as c:
        domains = {e.split("@", 1)[1].lower()
                   for (e,) in c.execute(users.select().with_only_columns(users.c.email))
                   if e and "@" in e and "example" not in e.lower()}
    if not domains:
        import pytest as _pytest
        _pytest.skip("no firm domain resolvable from users in this database")
    firm = sorted(domains)[0]
    _eligible, staff, ids = _db_eligibility([
        {"contact_type": "Client", "primary_email": f"someone@{firm}"}])
    assert ids[0] in staff, "firm-domain identity must be staff"
    assert ids[0] not in _eligible, "staff are never owner-eligible, even marked Client"


def test_no_hard_coded_person_id_special_case():
    """The fix must be structural. An identity appearing in CODE would be a red flag."""
    code = _executable_source("app/services/document_owner_proposal.py")
    # The mass-match person that motivated the safety work is not special-cased...
    assert "1314" not in code
    assert "Mike Agree" not in code
    assert "michaelagee" not in code
    # ...and neither is the client the old blacklist wrongly suppressed. Person 5583 is admitted by
    # the same positive evidence every other client is admitted by, never by exception.
    assert "5583" not in code
    # And no brand blacklist may return: the name list that erased 5583 must stay gone.
    assert "_COUNTERPARTY_NAMES" not in code
    for brand in ("fargo", "schwab", "ameriprise", "thrivent"):
        assert brand not in code.lower(), f"{brand!r} must not appear as a coded name"
