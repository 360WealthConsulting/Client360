"""D7 Phase C — relocating a non-natural identity out of ``drake_identity``.

The population these tests model is real: 133 rows in production describe businesses, estates and
trusts while sitting in the natural-person table, and each one blocks the attribution of the
business it misdescribes. What the tests pin hardest is what Phase C REFUSES — the eight rows whose
person link exists nowhere else, the mixed-subject row, the identifiers a pending match candidate
still points at — because relocation is destructive and a wrong refusal costs nothing while a wrong
relocation erases evidence.

Every fixture is created inside a transaction that rolls back. Nothing here writes to a production
database.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db import engine
from app.services import drake_identity_phase_c as phase_c
from app.services import drake_machine_attribution as machine
from app.services.drake_identifier import identifier_hash
from app.services.drake_return_identity import compute_return_identity_key
from app.services.link_trust import IDENTIFIER_VERIFIED
from app.services.relationships import create_named_entity

pytestmark = pytest.mark.skipif(
    sa_inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)

TEST_HASH_KEY = "phase-c-tests-not-a-production-key"
BUSINESS_EIN = "99-8100001"
OTHER_EIN = "99-8100002"
PERSON_SSN = "999-01-0001"


@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", TEST_HASH_KEY)


@pytest.fixture()
def conn():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def make_return(conn, *, year, return_type, name, tp_hash, dob=None):
    """Mirrors the seeding helper in ``test_drake_machine_attribution``: ``source_updated_at`` is
    NOT NULL and ``ck_drake_client_returns_identity_coherent`` ties ``identified`` to a real key."""
    key = compute_return_identity_key(year, tp_hash, None, return_type, None)
    return conn.execute(text(
        "INSERT INTO drake_client_returns "
        "(tax_year, source_row_number, taxpayer_first_name, taxpayer_normalized_name, return_type, "
        " taxpayer_identifier_hash, taxpayer_dob, identity_status, return_identity_key, "
        " source_updated_at, raw_data) "
        "VALUES (:y, :row, :n, :nn, :t, :h, :dob, 'identified', :k, now(), "
        "        CAST('{}' AS jsonb)) RETURNING id"),
        {"y": year, "row": int(uuid.uuid4().int % 90000) + 9000, "n": name, "nn": name.lower(),
         "t": return_type, "h": tp_hash, "dob": dob, "k": key}).scalar_one()


def make_contact(conn, *, year, return_id, hash_value, name, return_type=None):
    raw = {"drake_return_id": return_id, "tax_year": year, "role": "taxpayer",
           "return_type": return_type, "identifier_hash": hash_value}
    return conn.execute(text(
        "INSERT INTO source_contacts "
        "(source_system, source_file, source_record_id, source_hash, first_name, full_name, raw_data) "
        "VALUES ('Drake', :f, :r, :h, :n, :n, CAST(:raw AS json)) RETURNING id"),
        {"f": f"Drake {year}", "r": f"{year}:{return_id}:taxpayer",
         "h": uuid.uuid4().hex, "n": name, "raw": json.dumps(raw)}).scalar_one()


def make_identity(conn, *, hash_value, name, person_id=None, first=2021, last=2022, count=2,
                  confidence=100):
    conn.execute(text(
        "INSERT INTO drake_identity (identifier_hash, primary_person_id, first_year, last_year, "
        " return_count, taxpayer_name, confidence) "
        "VALUES (:h, :p, :f, :l, :c, :n, :conf)"),
        {"h": hash_value, "p": person_id, "f": first, "l": last, "c": count, "n": name,
         "conf": confidence})
    return conn.execute(text(
        "SELECT identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at FROM drake_identity "
        "WHERE identifier_hash = :h"), {"h": hash_value}).mappings().one()


def make_person(conn, tag):
    return conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"Person {tag}"}).scalar_one()


@pytest.fixture()
def case(conn):
    """The 7379 shape: a business identifier mis-filed as a person identity, with 1120S returns."""
    business_hash = identifier_hash(BUSINESS_EIN)
    tag = uuid.uuid4().hex[:8]
    name = f"EXAMPLE WAREHOUSE {tag} LLC"

    returns, contacts = [], []
    for year in (2021, 2022):
        rid = make_return(conn, year=year, return_type="1120S", name=name, tp_hash=business_hash)
        returns.append(rid)
        # return_type deliberately NULL in raw_data: the production shape that makes
        # drake_subject_routing hold these for review while the filed returns type them plainly.
        contacts.append(make_contact(conn, year=year, return_id=rid, hash_value=business_hash,
                                     name=name, return_type=None))
    row = make_identity(conn, hash_value=business_hash, name=name)
    return {"hash": business_hash, "name": name, "returns": returns, "contacts": contacts,
            "row": dict(row), "tag": tag}


def request_for(case, **over):
    return phase_c.RelocationRequest(
        identifier_hash=over.get("hash", case["hash"]),
        frozen_row=over.get("frozen_row", case["row"]),
        expected_psl_ids=over.get("expected_psl_ids", ()),
    )


def link_person(conn, case, person_id, contacts=None):
    """Give the identity a person link plus the backing PSLs that make it cohort B."""
    conn.execute(text("UPDATE drake_identity SET primary_person_id = :p WHERE identifier_hash = :h"),
                 {"p": person_id, "h": case["hash"]})
    ids = [conn.execute(text(
        "INSERT INTO person_source_links (person_id, source_contact_id, match_method, confirmed) "
        "VALUES (:p, :c, 'exact_email+exact_phone', true) RETURNING id"),
        {"p": person_id, "c": c}).scalar_one() for c in (contacts or case["contacts"])]
    case["row"] = dict(conn.execute(text(
        "SELECT identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at FROM drake_identity "
        "WHERE identifier_hash = :h"), {"h": case["hash"]}).mappings().one())
    return ids


# --- cohort A: the mechanical majority ----------------------------------------------------------

def test_an_unlinked_identity_relocates(conn, case):
    result = phase_c.relocate_identity(conn, request_for(case))

    assert result.cohort == "A"
    assert result.subject_type == "business_entity"
    assert result.return_types == ("1120S",)
    assert result.first_year == 2021 and result.last_year == 2022 and result.return_count == 2
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 0
    dbi = conn.execute(text(
        "SELECT id, subject_type, relationship_entity_id, trust_level, confirmation_source, "
        "evidence_method, first_year, last_year, return_count, subject_name, return_types "
        "FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).mappings().one()
    assert dbi["id"] == result.business_identity_id
    assert dbi["subject_type"] == "business_entity"
    assert list(dbi["return_types"]) == ["1120S"]


def test_the_relocated_row_is_typed_but_unattributed(conn, case):
    """Phase C decides WHAT the identifier is. It must not decide whose it is."""
    phase_c.relocate_identity(conn, request_for(case))

    dbi = conn.execute(text(
        "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
        "FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).mappings().one()
    assert dict(dbi) == phase_c.UNATTRIBUTED


def test_it_writes_no_entity_source_links(conn, case):
    before = conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar()
    phase_c.relocate_identity(conn, request_for(case))
    assert conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar() == before


def test_it_classifies_from_filed_returns_not_from_source_contacts(conn, case):
    """Every fixture contact carries return_type NULL, exactly as the 109 production rows do."""
    raw = conn.execute(text("SELECT raw_data FROM source_contacts WHERE id = :i"),
                       {"i": case["contacts"][0]}).scalar()
    raw = json.loads(raw) if isinstance(raw, str) else raw
    assert raw["return_type"] is None

    result = phase_c.relocate_identity(conn, request_for(case))
    assert result.subject_type == "business_entity"      # typed anyway, from drake_client_returns


# --- the audit record ---------------------------------------------------------------------------

def test_one_unattended_audit_entry_carries_the_whole_removed_row(conn, case):
    person = make_person(conn, case["tag"])
    psl = link_person(conn, case, person)
    before = dict(conn.execute(text(
        "SELECT identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at FROM drake_identity "
        "WHERE identifier_hash = :h"), {"h": case["hash"]}).mappings().one())

    result = phase_c.relocate_identity(conn, request_for(case, expected_psl_ids=tuple(psl)))

    row = conn.execute(text(
        "SELECT action, entity_type, entity_id, actor_user_id, metadata FROM audit_events "
        "WHERE id = :i"), {"i": result.audit_event_id}).mappings().one()
    assert row["action"] == phase_c.AUDIT_ACTION
    assert row["actor_user_id"] is None
    assert row["entity_type"] == "drake_business_identity"
    assert int(row["entity_id"]) == result.business_identity_id
    meta = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
    removed = meta["removed_drake_identity"]
    for column in phase_c.IDENTITY_COLUMNS:
        # created_at round-trips through JSON as ISO-8601, so the 'T' separator is normalised
        # before comparing; every other column compares as-is.
        assert str(removed[column]).replace("T", " ").startswith(str(before[column])[:19])
    assert meta["former_primary_person_id"] == person
    assert meta["former_confidence"] == before["confidence"]
    assert meta["cohort"] == "B"
    assert sorted(meta["drake_return_ids"]) == sorted(case["returns"])
    assert sorted(meta["source_contact_ids"]) == sorted(case["contacts"])


def test_exactly_one_audit_entry_per_relocation(conn, case):
    before = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                          {"a": phase_c.AUDIT_ACTION}).scalar()
    phase_c.relocate_identity(conn, request_for(case))
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": phase_c.AUDIT_ACTION}).scalar() == before + 1


# --- cohort B: allowed only because person_source_links preserves the association ----------------

def test_a_linked_identity_relocates_when_psl_evidence_backs_it(conn, case):
    person = make_person(conn, case["tag"])
    psl = link_person(conn, case, person)

    result = phase_c.relocate_identity(conn, request_for(case, expected_psl_ids=tuple(psl)))

    assert result.cohort == "B"
    assert result.removed_row["primary_person_id"] == person
    # the PSLs are evidence, never a target: untouched, byte for byte
    rows = conn.execute(text(
        "SELECT id, person_id, source_contact_id, match_method, match_score, confirmed "
        "FROM person_source_links WHERE id = ANY(:i) ORDER BY id"), {"i": psl}).mappings().all()
    assert [r["id"] for r in rows] == sorted(psl)
    assert all(r["person_id"] == person and r["confirmed"] for r in rows)


def test_a_person_link_with_no_backing_psl_is_refused(conn, case):
    """Cohort C. The link exists here and nowhere else; relocating would erase it."""
    person = make_person(conn, case["tag"])
    conn.execute(text("UPDATE drake_identity SET primary_person_id = :p WHERE identifier_hash = :h"),
                 {"p": person, "h": case["hash"]})
    frozen = dict(conn.execute(text(
        "SELECT identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at FROM drake_identity "
        "WHERE identifier_hash = :h"), {"h": case["hash"]}).mappings().one())

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case, frozen_row=frozen))

    assert excinfo.value.code == phase_c.PERSON_LINK_WITHOUT_PSL
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 1
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 0


def test_a_psl_belonging_to_a_different_person_does_not_count_as_backing(conn, case):
    """The backing must record THIS person's association, not merely exist."""
    person, other = make_person(conn, case["tag"]), make_person(conn, case["tag"] + "x")
    conn.execute(text("UPDATE drake_identity SET primary_person_id = :p WHERE identifier_hash = :h"),
                 {"p": person, "h": case["hash"]})
    for contact in case["contacts"]:
        conn.execute(text(
            "INSERT INTO person_source_links (person_id, source_contact_id, match_method, confirmed) "
            "VALUES (:p, :c, 'exact_email+exact_phone', true)"), {"p": other, "c": contact})
    frozen = dict(conn.execute(text(
        "SELECT identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at FROM drake_identity "
        "WHERE identifier_hash = :h"), {"h": case["hash"]}).mappings().one())

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case, frozen_row=frozen))
    assert excinfo.value.code == phase_c.PERSON_LINK_WITHOUT_PSL


def test_drifted_backing_psls_are_refused_not_downgraded(conn, case):
    """A B row whose evidence moved must be refused, never quietly treated as cohort C."""
    person = make_person(conn, case["tag"])
    psl = link_person(conn, case, person)

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(
            case, expected_psl_ids=(psl[0], max(psl) + 9999)))
    assert excinfo.value.code == phase_c.ROW_DRIFTED
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 1


# --- refusals -----------------------------------------------------------------------------------

def test_a_mixed_subject_identifier_is_refused(conn, case):
    """Cohort D: 1040 and 1120S on one identifier is a real ambiguity, not a typing bug."""
    make_return(conn, year=2023, return_type="1040", name=case["name"], tp_hash=case["hash"],
                dob="1970-01-01")

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case))
    assert excinfo.value.code in (phase_c.MIXED_SUBJECT, phase_c.REQUIRES_REVIEW)
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 1


def test_a_natural_person_identity_is_refused(conn):
    """The 1,643 rows that genuinely belong where they are must never move."""
    person_hash = identifier_hash(PERSON_SSN)
    tag = uuid.uuid4().hex[:8]
    for year in (2021, 2022):
        rid = make_return(conn, year=year, return_type="1040", name=f"PERSON {tag}",
                          tp_hash=person_hash, dob="1970-01-01")
        make_contact(conn, year=year, return_id=rid, hash_value=person_hash, name=f"PERSON {tag}",
                     return_type="1040")
    row = dict(make_identity(conn, hash_value=person_hash, name=f"PERSON {tag}"))

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, phase_c.RelocationRequest(
            identifier_hash=person_hash, frozen_row=row))
    assert excinfo.value.code == phase_c.SUBJECT_IS_NATURAL_PERSON
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": person_hash}).scalar() == 1


def test_an_identifier_with_no_returns_is_refused(conn, case):
    conn.execute(text("DELETE FROM drake_client_returns WHERE taxpayer_identifier_hash = :h"),
                 {"h": case["hash"]})
    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case))
    assert excinfo.value.code == phase_c.NOT_TYPEABLE


def test_a_drifted_row_is_refused(conn, case):
    conn.execute(text("UPDATE drake_identity SET confidence = 55 WHERE identifier_hash = :h"),
                 {"h": case["hash"]})
    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case))
    assert excinfo.value.code == phase_c.ROW_DRIFTED
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 1


def test_a_pending_match_candidate_blocks_relocation(conn, case):
    """Fail closed: approving it afterwards is impossible, so it must not be stranded."""
    person = make_person(conn, case["tag"])
    conn.execute(text(
        "INSERT INTO drake_identity_match_candidates "
        "(identifier_hash, person_id, score, reasons, rank, status) "
        "VALUES (:h, :p, 70, CAST('[]' AS jsonb), 1, 'pending')"),
        {"h": case["hash"], "p": person})

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case))
    assert excinfo.value.code == phase_c.UNEXPECTED_DEPENDENCY
    assert conn.execute(text(
        "SELECT count(*) FROM drake_identity_match_candidates WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 1        # untouched


def test_a_conflicting_business_identity_is_refused(conn, case):
    conn.execute(text(
        "INSERT INTO drake_business_identity (identifier_hash, subject_type, first_year, last_year, "
        " return_count, subject_name, return_types) "
        "VALUES (:h, 'business_entity', 2021, 2022, 2, :n, ARRAY['1120S'])"),
        {"h": case["hash"], "n": case["name"]})

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case))
    assert excinfo.value.code == phase_c.CONFLICTING_DBI
    assert conn.execute(text("SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 1


# --- idempotency --------------------------------------------------------------------------------

def test_a_second_invocation_refuses_and_writes_nothing(conn, case):
    """Not idempotent by repetition — idempotent by refusal."""
    first = phase_c.relocate_identity(conn, request_for(case))
    counts = {
        "dbi": conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar(),
        "identity": conn.execute(text("SELECT count(*) FROM drake_identity")).scalar(),
        "audit": conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                              {"a": phase_c.AUDIT_ACTION}).scalar(),
    }

    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, request_for(case))

    assert excinfo.value.code == phase_c.ALREADY_RELOCATED
    assert conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar() == counts["dbi"]
    assert conn.execute(text("SELECT count(*) FROM drake_identity")).scalar() == counts["identity"]
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": phase_c.AUDIT_ACTION}).scalar() == counts["audit"]
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 1
    assert first.business_identity_id > 0


def test_a_missing_identifier_is_refused(conn, case):
    with pytest.raises(phase_c.RelocationRefused) as excinfo:
        phase_c.relocate_identity(conn, phase_c.RelocationRequest(
            identifier_hash=identifier_hash(OTHER_EIN), frozen_row=case["row"]))
    assert excinfo.value.code == phase_c.ROW_NOT_FOUND


# --- what it must never touch -------------------------------------------------------------------

FORBIDDEN = ("people", "person_source_links", "relationship_entities", "drake_client_returns",
             "source_contacts", "documents", "entity_source_links",
             "drake_identity_match_candidates")


def test_no_forbidden_table_is_written(conn, case):
    person = make_person(conn, case["tag"])
    psl = link_person(conn, case, person)
    before = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in FORBIDDEN}
    fingerprint = conn.execute(text(
        "SELECT md5(string_agg(id::text||'|'||person_id::text||'|'||source_contact_id::text, "
        "'~|~' ORDER BY id)) FROM person_source_links")).scalar()

    phase_c.relocate_identity(conn, request_for(case, expected_psl_ids=tuple(psl)))

    for table, count in before.items():
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == count, table
    assert conn.execute(text(
        "SELECT md5(string_agg(id::text||'|'||person_id::text||'|'||source_contact_id::text, "
        "'~|~' ORDER BY id)) FROM person_source_links")).scalar() == fingerprint


def test_the_service_contains_no_write_to_a_forbidden_table():
    """Read the source: a future edit must not quietly add one."""
    from pathlib import Path
    source = Path(phase_c.__file__).read_text(encoding="utf-8")
    for table in FORBIDDEN:
        for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
            assert f"{verb} {table}" not in source, f"{verb} {table}"
    assert "DELETE FROM drake_identity" in source          # the one delete it does perform
    assert "INSERT INTO drake_business_identity" in source


# --- the hand-off to PR #279 --------------------------------------------------------------------

def test_a_relocated_identity_can_then_be_attributed(conn, case):
    """The whole point: relocation must leave attribution possible, and fully evidenced."""
    entity = create_named_entity(conn, "business", case["name"])
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"canonical_creation_reason": "verified_structured_business_provenance",
                                   "source_contact_ids": case["contacts"]}), "e": entity})

    phase_c.relocate_identity(conn, request_for(case))
    result = machine.attribute_entity_by_provenance(conn, machine.AttributionRequest(
        relationship_entity_id=entity, identifier=BUSINESS_EIN, identifier_type="ein",
        source_contact_ids=tuple(case["contacts"])))

    dbi = conn.execute(text(
        "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
        "FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).mappings().one()
    assert dbi["relationship_entity_id"] == entity
    assert dbi["trust_level"] == IDENTIFIER_VERIFIED
    assert dbi["confirmation_source"] == machine.MACHINE
    assert dbi["evidence_method"] == machine.EVIDENCE_METHOD
    assert set(result.source_links_created) == set(case["contacts"])
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 1          # updated in place, never duplicated


def test_without_relocation_the_attribution_is_still_refused(conn, case):
    """The blocker this lane exists to clear, pinned so it cannot be forgotten."""
    entity = create_named_entity(conn, "business", case["name"])
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"source_contact_ids": case["contacts"]}), "e": entity})

    with pytest.raises(machine.AttributionRefused) as excinfo:
        machine.attribute_entity_by_provenance(conn, machine.AttributionRequest(
            relationship_entity_id=entity, identifier=BUSINESS_EIN, identifier_type="ein",
            source_contact_ids=tuple(case["contacts"])))
    assert excinfo.value.code == machine.IDENTIFIER_BOUND_TO_PERSON


# --- the plan digest ----------------------------------------------------------------------------

def test_the_plan_digest_is_content_sensitive(conn, case):
    rows = [{"identifier_hash": case["hash"], "frozen_row": case["row"], "expected_psl_ids": []}]
    baseline = phase_c.plan_digest(rows)
    assert baseline == phase_c.plan_digest(list(rows))

    moved = [{"identifier_hash": case["hash"],
              "frozen_row": {**case["row"], "confidence": 55}, "expected_psl_ids": []}]
    assert phase_c.plan_digest(moved) != baseline
