"""Drake client-id carry-over ownership — eligibility, firm-signal tuning, apply and rollback.

The rule this file pins: a Drake document may inherit the owner that every already-owned document
under its native client id agrees on, PROVIDED nothing contradicts it. The deployed proposal engine
does not have to agree — on Drake documents it usually cannot, because their filenames are ``1.PDF``
and ``F.PDF`` — but it may not disagree, and the document's own content may not name a competing
party.

Verified firm staff and firm/self entities are ignored FOR CONTRADICTION PURPOSES ONLY, because
every tax return names its preparer and the preparing firm, and counting those as evidence against
the client is the failure that once put 2,157 documents on one person. The tests below prove that
exclusion cannot leak: a firm identity is never an owner, and no NON-firm competing identity is ever
ignored.

Signals are injected rather than extracted, so the decision logic is tested without file I/O and
without production data. The two production regressions (121833, 121836) are reproduced as fixtures
from their recorded evidence, not by reading production.
"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import engine, metadata
from app.services import drake_sibling_ownership as dso

documents = metadata.tables["documents"]
document_sources = metadata.tables["document_sources"]

STAFF_A, STAFF_B = 1314, 7851          # verified firm staff (firm mail domain)
FIRM_ORG = 160                          # verified firm/self entity
CLIENT, OTHER_CLIENT = 4204, 3003       # ordinary owner-eligible people
CLIENT_ORG = 53                         # an owner-eligible client business


def _idx(*, owner_eligible=(CLIENT, OTHER_CLIENT), org_eligible=(CLIENT_ORG,), members=None):
    """A minimal match index with the shape the rule consumes. No database, no engine."""
    return {"staff": {STAFF_A, STAFF_B}, "firm_entities": {FIRM_ORG, 161},
            "owner_eligible": set(owner_eligible), "org_eligible": set(org_eligible),
            "pid": {CLIENT: {"name": "Lloyd Blankenship", "household_id": None},
                    OTHER_CLIENT: {"name": "Brittany Oberlin", "household_id": 112},
                    STAFF_B: {"name": "Michael Shelton", "household_id": None}},
            "members": members or {112: {OTHER_CLIENT, 7529}},
            "name": {}, "first_last": {}, "inst": set(), "email": {}, "phone": {}, "biz": {}}


def _signals(sig=None, households=(), orgs=(), folder=None):
    return {"sig": sig or {}, "households": set(households), "orgs": set(orgs),
            "folder": folder, "method": "test", "text_len": 0}


# ==================================================================================================
# Firm-signal exclusion — the tuning under test
# ==================================================================================================

def test_firm_staff_signal_is_ignored_as_a_contradiction():
    """1. A preparer's phone on a client's return is noise, not a competing claim."""
    sig = {STAFF_A: {"phone"}, CLIENT: {"name"}}
    strict, _ = dso.contradictions(("person", CLIENT), _signals(sig), _idx(), exclude_firm=False)
    tuned, excluded = dso.contradictions(("person", CLIENT), _signals(sig), _idx(), exclude_firm=True)
    assert "foreign_strong_identifier" in strict
    assert tuned == []
    assert excluded["staff"] == [STAFF_A]


def test_firm_self_entity_signal_is_ignored_as_a_contradiction():
    """2. The preparing firm's own name on the return is not an organization conflict."""
    sig = {CLIENT: {"name"}}
    strict, _ = dso.contradictions(("person", CLIENT), _signals(sig, orgs=[FIRM_ORG]), _idx(),
                                   exclude_firm=False)
    tuned, excluded = dso.contradictions(("person", CLIENT), _signals(sig, orgs=[FIRM_ORG]), _idx(),
                                         exclude_firm=True)
    assert "organization_person_conflict" in strict
    assert tuned == []
    assert excluded["entities"] == [FIRM_ORG]


def test_a_non_firm_foreign_strong_identifier_still_blocks():
    """3. The exclusion must not become 'ignore other people'. A real third party's phone blocks."""
    sig = {OTHER_CLIENT: {"phone"}, CLIENT: {"name"}}
    tuned, _ = dso.contradictions(("person", CLIENT), _signals(sig), _idx(), exclude_firm=True)
    assert "foreign_strong_identifier" in tuned


def test_a_non_firm_second_named_taxpayer_still_blocks():
    """4. A joint return naming a second real taxpayer is ambiguous, tuning or not."""
    sig = {CLIENT: {"name"}, OTHER_CLIENT: {"name"}}
    tuned, _ = dso.contradictions(("person", CLIENT), _signals(sig), _idx(), exclude_firm=True)
    assert "multiple_named_identities" in tuned


def test_a_non_firm_organization_still_blocks():
    sig = {CLIENT: {"name"}}
    tuned, _ = dso.contradictions(("person", CLIENT), _signals(sig, orgs=[CLIENT_ORG]), _idx(),
                                  exclude_firm=True)
    assert "organization_person_conflict" in tuned


def test_household_person_conflict_still_blocks():
    """5. A household signal against a person candidate survives the tuning."""
    sig = {CLIENT: {"name"}}
    tuned, _ = dso.contradictions(("person", CLIENT), _signals(sig, households=[112]), _idx(),
                                  exclude_firm=True)
    assert "household_person_conflict" in tuned


def test_frequent_occurrence_never_creates_a_firm_identity():
    """6. The exclusion set comes from the deployed index, never from how often a name appears.
    A person seen with every signal class, thousands of times over, is still not staff."""
    sig = {OTHER_CLIENT: {"name", "email", "phone"}, CLIENT: {"name"}}
    tuned, excluded = dso.contradictions(("person", CLIENT), _signals(sig), _idx(),
                                         exclude_firm=True)
    assert excluded["staff"] == []                      # nothing was excluded
    assert "foreign_strong_identifier" in tuned         # and the signal still counts


def test_firm_signals_are_never_positive_evidence():
    """7. Excluding a firm signal removes a CONTRADICTION; it can never support an owner. With only
    firm signals present the candidate still comes solely from the siblings."""
    sig = {STAFF_A: {"phone"}, STAFF_B: {"name"}}
    tuned, excluded = dso.contradictions(("person", CLIENT), _signals(sig, orgs=[FIRM_ORG]),
                                         _idx(), exclude_firm=True)
    assert tuned == []
    assert sorted(excluded["staff"]) == sorted([STAFF_A, STAFF_B])


# ==================================================================================================
# Owner eligibility — a firm identity is never an owner
# ==================================================================================================

def test_owner_that_is_firm_staff_is_refused():
    ok, why = dso.owner_is_eligible(("person", STAFF_B), _idx())
    assert not ok and why == dso.R_OWNER_IS_FIRM


def test_owner_that_is_a_firm_entity_is_refused():
    ok, why = dso.owner_is_eligible(("organization", FIRM_ORG), _idx())
    assert not ok and why == dso.R_OWNER_IS_FIRM


def test_owner_not_owner_eligible_is_refused():
    ok, why = dso.owner_is_eligible(("person", 999999), _idx())
    assert not ok and why == dso.R_OWNER_INELIGIBLE


def test_eligible_client_owner_is_accepted():
    assert dso.owner_is_eligible(("person", CLIENT), _idx())[0]
    assert dso.owner_is_eligible(("organization", CLIENT_ORG), _idx())[0]


def test_household_owner_needs_an_owner_eligible_member():
    assert dso.owner_is_eligible(("household", 112), _idx())[0]
    assert not dso.owner_is_eligible(("household", 999), _idx())[0]


def test_owner_of_prefers_person_then_household_then_organization():
    assert dso.owner_of((7, None, None)) == ("person", 7)
    assert dso.owner_of((None, 8, None)) == ("household", 8)
    assert dso.owner_of((None, None, 9)) == ("organization", 9)


# ==================================================================================================
# Production regressions, reproduced from recorded evidence (no production access)
# ==================================================================================================

def test_regression_121833_must_block():
    """121833 — a joint Oberlin return. Firm signals (1314 phone, 7851 name, org 160) clear, but
    Brittany Oberlin is a REAL second taxpayer and the engine held HIGH evidence for household 112.
    Must block under both policies, forever."""
    sig = {STAFF_A: {"phone"}, OTHER_CLIENT: {"name"}, 7529: {"email", "name", "phone"},
           STAFF_B: {"name"}}
    signals = _signals(sig, households=[112], orgs=[FIRM_ORG])
    idx = _idx(owner_eligible=(CLIENT, OTHER_CLIENT, 7529))
    strict, _ = dso.contradictions(("person", 7529), signals, idx, exclude_firm=False)
    tuned, _ = dso.contradictions(("person", 7529), signals, idx, exclude_firm=True)
    assert strict, "strict policy must block 121833"
    assert "household_person_conflict" in tuned, "tuned policy must STILL block 121833"


def test_regression_121836_clears_only_under_the_tuned_policy():
    """121836 — a Blankenship return whose only competing signals are the preparer's phone (1314),
    the preparer's name (7851) and the firm entity (160). Every one is firm; none is a third party."""
    sig = {STAFF_A: {"phone"}, CLIENT: {"name"}, STAFF_B: {"name"}}
    signals = _signals(sig, orgs=[FIRM_ORG])
    strict, _ = dso.contradictions(("person", CLIENT), signals, _idx(), exclude_firm=False)
    tuned, _ = dso.contradictions(("person", CLIENT), signals, _idx(), exclude_firm=True)
    assert set(strict) >= {"foreign_strong_identifier", "multiple_named_identities",
                           "organization_person_conflict"}
    assert tuned == []


# ==================================================================================================
# Database-backed eligibility — sibling evidence and lifecycle gates
# ==================================================================================================

@pytest.fixture
def drake_docs():
    """Temp Drake documents + refs, torn down by tag."""
    tag = uuid.uuid4().hex[:8].upper()
    made = []

    def make(*, client_id=tag, owner=None, status="active", archived=False, available=True,
             external_id=None, name=None):
        with engine.begin() as c:
            vals = {"original_name": name or f"{tag}.pdf",
                    "stored_name": f"drake:{tag}{uuid.uuid4().hex}", "storage_path": "/x",
                    "storage_provider": "Client360 Local", "storage_uri": f"/x/{tag}",
                    "size_bytes": 10, "sha256": uuid.uuid4().hex, "status": status,
                    "archived": archived}
            if owner:
                vals[f"{owner[0]}_id"] = owner[1]
            did = c.execute(documents.insert().values(**vals)
                            .returning(documents.c.id)).scalar_one()
            c.execute(document_sources.insert().values(
                document_id=did, source_system="Drake",
                source_uri=f"C:\\DRAKE\\{tag}\\{did}.pdf", source_path="x",
                source_external_id=external_id if external_id is not None else client_id,
                available=available, metadata={}))
        made.append(did)
        return did

    yield make
    with engine.begin() as c:
        if made:
            c.execute(delete(document_sources).where(document_sources.c.document_id.in_(made)))
            c.execute(delete(documents).where(documents.c.id.in_(made)))


def _sibs(did, client_id):
    with engine.connect() as c:
        return dso.sibling_owners(c, client_id, exclude_document_id=did)


def test_unanimous_siblings_yield_one_candidate(drake_docs):
    """8. The core carry-over: two owned siblings under one client id, both the same owner."""
    cid = uuid.uuid4().hex[:8].upper()
    drake_docs(client_id=cid, owner=("person", CLIENT))
    drake_docs(client_id=cid, owner=("person", CLIENT))
    target = drake_docs(client_id=cid)
    tuples, sibs = _sibs(target, cid)
    assert len(tuples) == 1
    assert dso.owner_of(next(iter(tuples))) == ("person", CLIENT)
    assert len(sibs) == 2


def test_conflicting_sibling_owners_are_not_collapsed(drake_docs):
    """9. Two owned siblings disagreeing is exactly the case that must never auto-resolve."""
    cid = uuid.uuid4().hex[:8].upper()
    drake_docs(client_id=cid, owner=("person", CLIENT))
    drake_docs(client_id=cid, owner=("person", OTHER_CLIENT))
    target = drake_docs(client_id=cid)
    tuples, _ = _sibs(target, cid)
    assert len(tuples) == 2


def test_no_sibling_yields_no_candidate(drake_docs):
    cid = uuid.uuid4().hex[:8].upper()
    target = drake_docs(client_id=cid)
    assert _sibs(target, cid)[0] == set()


def test_a_document_never_supplies_its_own_sibling_evidence(drake_docs):
    """10. Non-circularity: the target is excluded from its own sibling set even when owned. This
    is what makes a retrospective meaningful rather than self-fulfilling."""
    cid = uuid.uuid4().hex[:8].upper()
    target = drake_docs(client_id=cid, owner=("person", CLIENT))
    assert _sibs(target, cid)[0] == set()


def test_deleted_and_archived_siblings_are_not_evidence(drake_docs):
    cid = uuid.uuid4().hex[:8].upper()
    drake_docs(client_id=cid, owner=("person", CLIENT), status="deleted")
    drake_docs(client_id=cid, owner=("person", CLIENT), archived=True)
    target = drake_docs(client_id=cid)
    assert _sibs(target, cid)[0] == set()


def test_source_ref_lookup_reports_availability_and_client_id(drake_docs):
    cid = uuid.uuid4().hex[:8].upper()
    unavailable = drake_docs(client_id=cid, available=False)
    with engine.connect() as c:
        row = dso.drake_source(c, unavailable)
    assert row["available"] is False
    assert row["source_external_id"] == cid


def test_a_non_hex_client_id_is_not_a_client_id():
    assert dso.CLIENT_ID_RE.match("D14FE8D0")
    assert dso.CLIENT_ID_RE.match("d14fe8d0")
    assert not dso.CLIENT_ID_RE.match("SMITHJOHN")
    assert not dso.CLIENT_ID_RE.match("D14FE8D")
    assert not dso.CLIENT_ID_RE.match("D14FE8D01")


def test_blocking_contradiction_list_covers_every_competing_party_class():
    """11. A class silently dropped from this tuple would silently start auto-assigning."""
    for cls in ("foreign_strong_identifier", "multiple_strong_identities",
                "multiple_named_identities", "household_person_conflict",
                "organization_person_conflict", "folder_identity_conflict",
                "placeholder_candidate", "engine_proposes_different_owner"):
        assert cls in dso.BLOCKING_CONTRADICTIONS


# ==================================================================================================
# evaluate() end to end — the lifecycle and rule-7 gates
#
# Signals and the engine answer are injected so these exercise the DECISION, not file extraction.
# ==================================================================================================

@pytest.fixture
def scored(monkeypatch):
    """Run evaluate() with injected signals and a chosen engine verdict."""
    def _run(did, *, sig=None, households=(), orgs=(), engine_says=(None, None, None),
             idx=None, retrospective=False):
        monkeypatch.setattr(dso, "document_signals",
                            lambda *a, **k: _signals(sig, households, orgs))
        monkeypatch.setattr(dso, "engine_proposal", lambda *a, **k: engine_says)
        with engine.connect() as c:
            return dso.evaluate(c, did, idx or _idx(), retrospective=retrospective)
    return _run


def _pair(drake_docs, **target):
    """One owned sibling plus an unowned target under a shared client id."""
    cid = uuid.uuid4().hex[:8].upper()
    drake_docs(client_id=cid, owner=("person", CLIENT))
    return cid, drake_docs(client_id=cid, **target)


def test_evaluate_accepts_a_clean_carry_over(drake_docs, scored):
    """12. The whole rule, end to end: one agreeing sibling, no competing signal, engine silent."""
    _cid, target = _pair(drake_docs)
    v = scored(target, sig={CLIENT: {"name"}})
    assert v["eligible"] is True
    assert v["candidate"] == ("person", CLIENT)
    assert v["reasons"] == []


def test_evaluate_blocks_a_high_proposal_for_a_different_owner(drake_docs, scored):
    """13. RULE 7. The engine need not agree, but it may not disagree."""
    _cid, target = _pair(drake_docs)
    v = scored(target, sig={CLIENT: {"name"}},
               engine_says=("household", 112, "HIGH"))
    assert not v["eligible"]
    assert dso.R_CONTRADICTED in v["reasons"]
    assert "engine_proposes_different_owner" in v["contradictions_tuned"]


def test_evaluate_allows_a_high_proposal_for_the_same_owner(drake_docs, scored):
    """14. Agreement is welcome; it is simply not required."""
    _cid, target = _pair(drake_docs)
    v = scored(target, sig={CLIENT: {"name"}}, engine_says=("person", CLIENT, "HIGH"))
    assert v["eligible"] is True


def test_evaluate_ignores_a_non_high_proposal_for_a_different_owner(drake_docs, scored):
    """15. HOLD/MEDIUM is silence. Only HIGH disagreement blocks — that is what makes the rule
    usable on Drake documents, whose filenames the engine cannot read."""
    _cid, target = _pair(drake_docs)
    v = scored(target, sig={CLIENT: {"name"}}, engine_says=("person", OTHER_CLIENT, "MEDIUM"))
    assert v["eligible"] is True


def test_evaluate_blocks_an_already_owned_target(drake_docs, scored):
    _cid, target = _pair(drake_docs, owner=("person", OTHER_CLIENT))
    v = scored(target, sig={CLIENT: {"name"}})
    assert not v["eligible"] and dso.R_ALREADY_OWNED in v["reasons"]


def test_retrospective_mode_scores_an_owned_document(drake_docs, scored):
    """16. Only the all-NULL rule is skipped, and only in retrospective mode."""
    _cid, target = _pair(drake_docs, owner=("person", OTHER_CLIENT))
    v = scored(target, sig={CLIENT: {"name"}}, retrospective=True)
    assert v["eligible"] is True
    assert dso.R_ALREADY_OWNED not in v["reasons"]


@pytest.mark.parametrize("kw,reason", [
    ({"status": "deleted"}, dso.R_NOT_ACTIVE),
    ({"archived": True}, dso.R_NOT_ACTIVE),
    ({"available": False}, dso.R_NO_SOURCE),
    ({"external_id": "NOTHEXID"}, dso.R_NO_CLIENT_ID),
])
def test_evaluate_blocks_on_lifecycle_and_source_state(drake_docs, scored, kw, reason):
    """17. Deleted, archived, source gone, or a client id that is not a client id."""
    _cid, target = _pair(drake_docs, **kw)
    v = scored(target, sig={CLIENT: {"name"}})
    assert not v["eligible"] and reason in v["reasons"]


def test_evaluate_blocks_a_frozen_drake_document(scored, monkeypatch):
    """18. FROZEN_DRAKE_DOCUMENT_IDS are intentional permanent holds."""
    frozen = next(iter(dso.FROZEN_DRAKE_DOCUMENT_IDS))
    v = scored(frozen, sig={})
    assert not v["eligible"]
    assert dso.R_REJECTED in v["reasons"] or "not_found" in v["reasons"]


def test_evaluate_blocks_when_the_owner_is_firm_staff(drake_docs, scored):
    """19. Rule 14 outranks perfect sibling evidence: the firm's own principal is genuinely a tax
    client of the firm, and their return still may not be auto-assigned to them."""
    people = metadata.tables["people"]
    with engine.begin() as c:
        staff_pid = c.execute(people.insert().values(full_name="Test Firm Principal")
                              .returning(people.c.id)).scalar_one()
    try:
        cid = uuid.uuid4().hex[:8].upper()
        drake_docs(client_id=cid, owner=("person", staff_pid))
        target = drake_docs(client_id=cid)
        idx = _idx()
        idx["staff"] = {staff_pid}
        idx["pid"][staff_pid] = {"name": "Test Firm Principal", "household_id": None}
        v = scored(target, sig={}, idx=idx)
        assert not v["eligible"] and dso.R_OWNER_IS_FIRM in v["reasons"]
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == staff_pid))


def test_evaluate_blocks_when_siblings_disagree(drake_docs, scored):
    cid = uuid.uuid4().hex[:8].upper()
    drake_docs(client_id=cid, owner=("person", CLIENT))
    drake_docs(client_id=cid, owner=("person", OTHER_CLIENT))
    target = drake_docs(client_id=cid)
    v = scored(target, sig={})
    assert not v["eligible"] and dso.R_MULTI_SIBLING in v["reasons"]


def test_evaluate_blocks_with_no_sibling(drake_docs, scored):
    cid = uuid.uuid4().hex[:8].upper()
    target = drake_docs(client_id=cid)
    v = scored(target, sig={})
    assert not v["eligible"] and dso.R_NO_SIBLING in v["reasons"]


def test_unreadable_document_does_not_break_scoring(drake_docs, monkeypatch):
    """20. A missing or corrupt file is a fact about the file. It must not raise out of evaluate."""
    _cid, target = _pair(drake_docs)

    def boom(*a, **k):
        raise OSError("file is gone")

    monkeypatch.setattr(dso, "document_signals", boom)
    monkeypatch.setattr(dso, "engine_proposal", lambda *a, **k: (None, None, None))
    with engine.connect() as c:
        v = dso.evaluate(c, target, _idx())
    assert v["extract_method"] == "unreadable"


# ==================================================================================================
# The Keen inconsistency — recorded, deliberately NOT resolved
# ==================================================================================================

def test_keen_pair_shows_carry_over_mirrors_whichever_sibling_it_sees(drake_docs, scored):
    """21. Production documents 121779 / 121808 are the same client's returns, owned by a HOUSEHOLD
    in one year and by a PERSON in another. The two are each other's only sibling, so carry-over
    proposes the household for one and the person for the other, and is 'wrong' relative to the
    opposite human decision either way.

    This test pins the BEHAVIOUR — the rule mirrors the evidence it is given — and deliberately
    does NOT assert that either granularity is universally correct. That is a data-quality question
    about the source ownership, not a rule to encode."""
    cid = uuid.uuid4().hex[:8].upper()
    idx = _idx(members={112: {CLIENT, OTHER_CLIENT}})
    person_owned = drake_docs(client_id=cid, owner=("person", CLIENT))
    household_owned = drake_docs(client_id=cid, owner=("household", 112))

    from_person = scored(household_owned, sig={}, idx=idx, retrospective=True)
    from_household = scored(person_owned, sig={}, idx=idx, retrospective=True)
    assert from_person["candidate"] == ("person", CLIENT)
    assert from_household["candidate"] == ("household", 112)
    # Neither is asserted "correct": the point is that the two disagree because the sources do.
    assert from_person["candidate"] != from_household["candidate"]


# ==================================================================================================
# Manifest envelope and rollback — the approval gates, tested without touching production
# ==================================================================================================

def _manifest(tmp_path, rows, *, applied="NO"):
    import csv as _csv

    from scripts.apply_drake_sibling_ownership import sha256_of
    p = tmp_path / "m.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["document_id", "drake_client_id", "owner_type",
                                            "owner_id", "evidence_digest", "applied"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "applied": applied})
    return p, sha256_of(p)


def _rows(n=2, start=1):
    return [{"document_id": start + i, "drake_client_id": f"AAAAAAA{i}",
             "owner_type": "person", "owner_id": CLIENT, "evidence_digest": f"d{i}"}
            for i in range(n)]


def _census(person=2, household=0, organization=0):
    return {"person": person, "household": household, "organization": organization}


def test_manifest_sha_mismatch_aborts(tmp_path):
    """22. The manifest is immutable input. A changed file is a different approval."""
    from scripts.apply_drake_sibling_ownership import load_manifest
    p, _sha = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="SHA256"):
        load_manifest(p, expect_sha="0" * 64, expect_rows=2, expect_census=_census())


def test_manifest_row_count_mismatch_aborts(tmp_path):
    from scripts.apply_drake_sibling_ownership import load_manifest
    p, sha = _manifest(tmp_path, _rows(2))
    with pytest.raises(SystemExit, match="rows"):
        load_manifest(p, expect_sha=sha, expect_rows=3, expect_census=_census(3))


def test_manifest_census_mismatch_aborts(tmp_path):
    """23. The per-type census is part of what a human approved, not a derived convenience."""
    from scripts.apply_drake_sibling_ownership import load_manifest
    p, sha = _manifest(tmp_path, _rows(2))
    with pytest.raises(SystemExit, match="census"):
        load_manifest(p, expect_sha=sha, expect_rows=2,
                      expect_census=_census(person=1, household=1))


def test_manifest_duplicate_document_ids_abort(tmp_path):
    from scripts.apply_drake_sibling_ownership import load_manifest
    rows = _rows(1) + _rows(1)
    p, sha = _manifest(tmp_path, rows)
    with pytest.raises(SystemExit, match="duplicate"):
        load_manifest(p, expect_sha=sha, expect_rows=2, expect_census=_census())


def test_manifest_row_already_applied_aborts(tmp_path):
    from scripts.apply_drake_sibling_ownership import load_manifest
    p, sha = _manifest(tmp_path, _rows(), applied="YES")
    with pytest.raises(SystemExit, match="applied=NO"):
        load_manifest(p, expect_sha=sha, expect_rows=2, expect_census=_census())


def test_census_that_does_not_sum_to_row_count_aborts(tmp_path):
    """24. Catches an approval that is internally inconsistent before anything is read."""
    from scripts.apply_drake_sibling_ownership import load_manifest
    p, sha = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="sums to"):
        load_manifest(p, expect_sha=sha, expect_rows=2, expect_census=_census(person=5))


def test_confirm_phrase_binds_batch_id_and_row_count():
    """25. A confirmation typed for one batch cannot authorise a different one."""
    from scripts.apply_drake_sibling_ownership import confirm_phrase
    assert confirm_phrase("DRAKE-X", 11) == "APPLY-DRAKE-X-11"
    assert confirm_phrase("DRAKE-X", 12) != confirm_phrase("DRAKE-X", 11)


def test_rollback_refuses_when_ownership_drifted(tmp_path, drake_docs):
    """26. If a human re-owned a document after the apply, rollback must abort rather than
    overwrite that decision — and must restore nothing at all, not merely skip the drifted row."""
    from scripts.rollback_drake_sibling_ownership import run as rollback_run
    people = metadata.tables["people"]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name="Rollback Fixture")
                        .returning(people.c.id)).scalar_one()
        other = c.execute(people.insert().values(full_name="Later Human Decision")
                          .returning(people.c.id)).scalar_one()
    try:
        did = drake_docs(owner=("person", other))          # current owner: the LATER decision
        snap = tmp_path / "rollback_snapshot_drake_sibling_ownership.csv"
        snap.write_text(
            "document_id,original_name,prior_person_id,prior_household_id,prior_organization_id,"
            "drake_client_id,destination_owner_type,destination_owner_id,evidence_digest\n"
            f"{did},x.pdf,,,,AAAAAAAA,person,{pid},d0\n", encoding="utf-8")
        report = rollback_run(str(snap), apply_changes=False, log=lambda *_a: None)
        assert report["failures"], "drift must be detected"
        assert "drift" in report["failures"][0]["why"]
        assert report["restored"] == 0
        with engine.connect() as c:
            still = c.execute(select(documents.c.person_id)
                              .where(documents.c.id == did)).scalar()
        assert still == other, "the later human decision must survive untouched"
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id.in_([pid, other])))


def test_rollback_dry_run_restores_nothing(tmp_path, drake_docs):
    """27. Dry run is the default posture everywhere in this workflow."""
    from scripts.rollback_drake_sibling_ownership import run as rollback_run
    people = metadata.tables["people"]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name="Dry Run Fixture")
                        .returning(people.c.id)).scalar_one()
    try:
        did = drake_docs(owner=("person", pid))
        snap = tmp_path / "rollback_snapshot_drake_sibling_ownership.csv"
        snap.write_text(
            "document_id,original_name,prior_person_id,prior_household_id,prior_organization_id,"
            "drake_client_id,destination_owner_type,destination_owner_id,evidence_digest\n"
            f"{did},x.pdf,,,,AAAAAAAA,person,{pid},d0\n", encoding="utf-8")
        report = rollback_run(str(snap), apply_changes=False, log=lambda *_a: None)
        assert report["failures"] == []
        assert report["restored"] == 0 and report["committed"] is False
        with engine.connect() as c:
            assert c.execute(select(documents.c.person_id)
                             .where(documents.c.id == did)).scalar() == pid
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == pid))
