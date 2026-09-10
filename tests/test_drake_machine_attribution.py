"""Unattended entity attribution: allowed only where stored provenance already corroborates it.

WHAT THESE PIN

Two writers now bind a Drake identity to an entity, and they must stay apart. Human adjudication
records ``human_approved`` and demands a named approver, for evidence that reached the system only
because a person read it. This writer records ``identifier_verified`` / ``machine`` and demands no
approver, for cases the data already asserts. Neither can produce the other's trust level.

The provenance rule is the load-bearing one. This module must never become a name matcher, so the
tests below prove that an entity sharing only a name is refused, that provenance pointing at a
different taxpayer is refused, and that no name, address or contact point is read at all.

The success fixture mirrors D7 case 7379: a person with a real 1040 history who also carries a
separate business identifier on two clean 1120S returns, against an existing entity whose stored
provenance cites those exact source contacts.

Rows are seeded inside a transaction and rolled back. Nothing is committed, and no test touches
production.
"""

import ast
import inspect
import json
import re
import textwrap
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db import engine
from app.services import drake_machine_attribution as machine
from app.services.drake_identifier import identifier_hash
from app.services.drake_return_identity import compute_return_identity_key
from app.services.link_trust import HUMAN_APPROVED, IDENTIFIER_VERIFIED
from app.services.relationships import create_named_entity

pytestmark = pytest.mark.skipif(
    sa_inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)

TEST_HASH_KEY = "machine-attribution-tests-not-a-production-key"
BUSINESS_EIN = "99-8000001"
PERSON_SSN = "999-00-0001"


@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    """A deterministic secret of the module's own, never the shell's. CI sets none."""
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", TEST_HASH_KEY)


@pytest.fixture()
def conn():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def make_return(conn, *, year, return_type, name, tp_hash=None, sp_hash=None, dob=None):
    """A seeded return. ``ck_drake_client_returns_identity_coherent`` ties ``identified`` to a
    non-null identity key, so the key is derived with the production function rather than faked."""
    key = compute_return_identity_key(year, tp_hash, sp_hash, return_type, None)
    return conn.execute(text(
        "INSERT INTO drake_client_returns "
        "(tax_year, source_row_number, taxpayer_first_name, taxpayer_normalized_name, return_type, "
        " taxpayer_identifier_hash, spouse_identifier_hash, taxpayer_dob, identity_status, "
        " return_identity_key, source_updated_at, raw_data) "
        "VALUES (:y, :row, :n, :nn, :t, :tp, :sp, :dob, :status, :key, now(), "
        "        CAST('{}' AS jsonb)) RETURNING id"),
        {"y": year, "row": int(uuid.uuid4().int % 90000) + 9000, "n": name, "nn": name.lower(),
         "t": return_type, "tp": tp_hash, "sp": sp_hash, "dob": dob, "key": key,
         "status": "identified" if key else "unidentified_no_taxpayer_identifier"}).scalar_one()


def make_contact(conn, *, year, return_id, hash_value, name, system="Drake", role="taxpayer"):
    return conn.execute(text(
        "INSERT INTO source_contacts "
        "(source_system, source_file, source_record_id, source_hash, first_name, full_name, raw_data) "
        "VALUES (:sys, :f, :r, :h, :n, :n, CAST(:raw AS json)) RETURNING id"),
        {"sys": system, "f": f"Drake {year}", "r": f"{year}:{return_id}:{role}",
         "h": uuid.uuid4().hex, "n": name,
         "raw": json.dumps({"drake_return_id": return_id, "tax_year": year, "role": role,
                            "return_type": None, "identifier_hash": hash_value})}).scalar_one()


def set_details(conn, entity_id, details):
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps(details), "e": entity_id})


@pytest.fixture()
def case(conn):
    """The 7379 shape: a real person, a separate business identifier, a corroborated entity."""
    business_hash = identifier_hash(BUSINESS_EIN)
    person_hash = identifier_hash(PERSON_SSN)
    tag = uuid.uuid4().hex[:8]
    name = f"EXAMPLE WINE WAREHOUSE {tag} LLC"

    returns, contacts = [], []
    for year in (2021, 2022):
        rid = make_return(conn, year=year, return_type="1120S", name=name, tp_hash=business_hash)
        returns.append(rid)
        contacts.append(make_contact(conn, year=year, return_id=rid,
                                     hash_value=business_hash, name=name))

    # The natural-person side of the same collapsed person row. Never touched by this writer.
    person_returns = [make_return(conn, year=y, return_type="1040", name=f"PERSON {tag}",
                                  tp_hash=person_hash, dob="1970-01-01")
                      for y in (2021, 2022, 2023)]
    person = conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"Person {tag}"}).scalar_one()
    psl = [conn.execute(text(
        "INSERT INTO person_source_links (person_id, source_contact_id, match_method, confirmed) "
        "VALUES (:p, :c, 'exact_email+exact_phone', true) RETURNING id"),
        {"p": person, "c": c}).scalar_one() for c in contacts]

    entity = create_named_entity(conn, "business", name)
    set_details(conn, entity, {"canonical_creation_reason": "verified_structured_business_provenance",
                               "source_systems": ["Drake", "SharePoint"],
                               "source_contact_ids": contacts})

    return {"entity": entity, "contacts": contacts, "returns": returns, "hash": business_hash,
            "person": person, "person_hash": person_hash, "person_returns": person_returns,
            "psl": psl, "name": name}


def request_for(case, **over):
    return machine.AttributionRequest(
        relationship_entity_id=over.get("entity", case["entity"]),
        identifier=over.get("identifier", BUSINESS_EIN),
        identifier_type=over.get("identifier_type", "ein"),
        source_contact_ids=tuple(over.get("contacts", case["contacts"])),
        reason="D7 machine attribution")


def refusal(conn, request):
    with pytest.raises(machine.AttributionRefused) as caught:
        machine.attribute_entity_by_provenance(conn, request)
    return caught.value.code


# --- the 7379 fixture succeeds ------------------------------------------------------------------

def test_a_provenance_corroborated_business_case_is_attributed(conn, case):
    result = machine.attribute_entity_by_provenance(conn, request_for(case))

    assert result.relationship_entity_id == case["entity"]
    assert result.subject_type == "business_entity"
    assert result.first_year == 2021 and result.last_year == 2022
    assert result.return_count == 2
    assert result.return_types == ("1120S",)
    assert result.identity_created is True
    assert set(result.source_links_created) == set(case["contacts"])
    assert result.provenance_form == "details.source_contact_ids"
    assert result.changed is True


def test_the_written_rows_carry_machine_trust_and_no_approver(conn, case):
    result = machine.attribute_entity_by_provenance(conn, request_for(case))

    dbi = conn.execute(text(
        "SELECT relationship_entity_id, subject_type, trust_level, confirmation_source, "
        "       evidence_method, confirmed_by_user_id, confirmed_at "
        "FROM drake_business_identity WHERE id = :i"),
        {"i": result.business_identity_id}).mappings().one()
    assert dbi["relationship_entity_id"] == case["entity"]
    assert dbi["trust_level"] == IDENTIFIER_VERIFIED
    assert dbi["confirmation_source"] == "machine"
    assert dbi["evidence_method"] == machine.EVIDENCE_METHOD
    assert dbi["confirmed_by_user_id"] is None
    assert dbi["confirmed_at"] is None

    for row in conn.execute(text(
            "SELECT trust_level, confirmation_source, evidence_method, match_method, "
            "       confirmed_by_user_id, confirmed, match_score "
            "FROM entity_source_links WHERE relationship_entity_id = :e"),
            {"e": case["entity"]}).mappings():
        assert row["trust_level"] == IDENTIFIER_VERIFIED
        assert row["confirmation_source"] == "machine"
        assert row["evidence_method"] == machine.EVIDENCE_METHOD
        assert row["match_method"] == machine.EVIDENCE_METHOD
        assert row["confirmed_by_user_id"] is None
        assert row["confirmed"] is True


def test_it_never_records_human_approval(conn, case):
    machine.attribute_entity_by_provenance(conn, request_for(case))

    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE relationship_entity_id = :e "
        "AND trust_level = :t"), {"e": case["entity"], "t": HUMAN_APPROVED}).scalar() == 0
    assert "trust_level" not in machine.AttributionRequest.__dataclass_fields__
    assert "actor_user_id" not in machine.AttributionRequest.__dataclass_fields__


def test_the_audit_entry_is_unattended_and_names_the_provenance(conn, case):
    result = machine.attribute_entity_by_provenance(conn, request_for(case))

    entry = conn.execute(text(
        "SELECT action, actor_user_id, entity_type, entity_id, metadata FROM audit_events "
        "WHERE id = :i"), {"i": result.audit_event_id}).mappings().one()
    assert entry["action"] == machine.AUDIT_ACTION
    assert entry["actor_user_id"] is None
    assert entry["entity_id"] == str(case["entity"])

    meta = entry["metadata"]
    assert meta["trust_level"] == IDENTIFIER_VERIFIED
    assert meta["confirmation_source"] == "machine"
    assert meta["human_adjudication"] is False
    assert meta["unattended"] is True
    assert meta["provenance_form"] == "details.source_contact_ids"
    assert meta["source_contact_ids"] == sorted(case["contacts"])
    assert meta["identifier_hash"] == result.identifier_hash


def test_the_audit_entry_never_records_the_raw_identifier(conn, case):
    result = machine.attribute_entity_by_provenance(conn, request_for(case))
    blob = conn.execute(text("SELECT metadata::text FROM audit_events WHERE id = :i"),
                        {"i": result.audit_event_id}).scalar()
    assert BUSINESS_EIN not in blob
    assert "998000001" not in blob


# --- what it must not touch ---------------------------------------------------------------------

def test_the_person_side_is_untouched(conn, case):
    people = conn.execute(text("SELECT count(*) FROM people")).scalar()
    psl = conn.execute(text("SELECT count(*) FROM person_source_links")).scalar()
    before = conn.execute(text(
        "SELECT id, person_id, source_contact_id, match_method FROM person_source_links "
        "WHERE id = ANY(:i) ORDER BY id"), {"i": case["psl"]}).fetchall()

    machine.attribute_entity_by_provenance(conn, request_for(case))

    assert conn.execute(text("SELECT count(*) FROM people")).scalar() == people
    assert conn.execute(text("SELECT count(*) FROM person_source_links")).scalar() == psl
    assert conn.execute(text(
        "SELECT id, person_id, source_contact_id, match_method FROM person_source_links "
        "WHERE id = ANY(:i) ORDER BY id"), {"i": case["psl"]}).fetchall() == before


def test_the_drake_returns_are_untouched(conn, case):
    ids = case["returns"] + case["person_returns"]
    before = conn.execute(text(
        "SELECT id, identity_status, return_identity_key, taxpayer_identifier_hash, raw_data::text "
        "FROM drake_client_returns WHERE id = ANY(:i) ORDER BY id"), {"i": ids}).fetchall()

    machine.attribute_entity_by_provenance(conn, request_for(case))

    assert conn.execute(text(
        "SELECT id, identity_status, return_identity_key, taxpayer_identifier_hash, raw_data::text "
        "FROM drake_client_returns WHERE id = ANY(:i) ORDER BY id"), {"i": ids}).fetchall() == before


def test_no_entity_is_created(conn, case):
    before = conn.execute(text("SELECT count(*) FROM relationship_entities")).scalar()
    machine.attribute_entity_by_provenance(conn, request_for(case))
    assert conn.execute(text("SELECT count(*) FROM relationship_entities")).scalar() == before


def test_the_natural_person_identity_is_untouched(conn, case):
    identities = conn.execute(text("SELECT count(*) FROM drake_identity")).scalar()
    machine.attribute_entity_by_provenance(conn, request_for(case))
    assert conn.execute(text("SELECT count(*) FROM drake_identity")).scalar() == identities
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["person_hash"]}).scalar() == 0


# --- idempotency ----------------------------------------------------------------------------------

def test_running_twice_creates_no_duplicate_rows(conn, case):
    first = machine.attribute_entity_by_provenance(conn, request_for(case))
    second = machine.attribute_entity_by_provenance(conn, request_for(case))

    assert second.business_identity_id == first.business_identity_id
    assert second.identity_created is False
    assert second.source_links_created == ()
    assert set(second.source_links_unchanged) == set(case["contacts"])
    assert second.changed is False
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 1
    assert conn.execute(text(
        "SELECT count(*) FROM entity_source_links WHERE relationship_entity_id = :e"),
        {"e": case["entity"]}).scalar() == 2


def test_a_re_run_never_downgrades_a_human_approved_trust_level(conn, case):
    """If a human later adjudicated the same identifier, the machine path must not restate it."""
    machine.attribute_entity_by_provenance(conn, request_for(case))
    # The schema refuses an unattributed human approval, so the simulated adjudication supplies an
    # approver and a timestamp exactly as the human service would.
    actor = conn.execute(text(
        "INSERT INTO users (email, normalized_email, display_name, status) "
        "VALUES (:e, :e, 'Later Approver', 'active') RETURNING id"),
        {"e": f"later-{uuid.uuid4().hex[:8]}@example.invalid"}).scalar_one()
    conn.execute(text(
        "UPDATE drake_business_identity SET trust_level = :t, confirmation_source = 'human', "
        "confirmed_by_user_id = :u, confirmed_at = now() WHERE identifier_hash = :h"),
        {"t": HUMAN_APPROVED, "u": actor, "h": case["hash"]})
    conn.execute(text(
        "UPDATE entity_source_links SET trust_level = :t, confirmation_source = 'human', "
        "confirmed_by_user_id = :u, confirmed_at = now() WHERE relationship_entity_id = :e"),
        {"t": HUMAN_APPROVED, "u": actor, "e": case["entity"]})

    machine.attribute_entity_by_provenance(conn, request_for(case))

    assert conn.execute(text(
        "SELECT trust_level FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == HUMAN_APPROVED
    assert set(conn.execute(text(
        "SELECT DISTINCT trust_level FROM entity_source_links WHERE relationship_entity_id = :e"),
        {"e": case["entity"]}).scalars()) == {HUMAN_APPROVED}


# --- attributing a row that D7 Phase C already relocated -----------------------------------------
# Phase C moves a mis-filed identity out of drake_identity into this table as a TYPED BUT
# UNATTRIBUTED row: entity NULL, trust NULL -- the same shape drake_subject_routing._DBI_UPSERT
# produces at ingestion. These pin what attributing such a row does.

def _approver(conn):
    return conn.execute(text(
        "INSERT INTO users (email, normalized_email, display_name, status) "
        "VALUES (:e, :e, 'Approver', 'active') RETURNING id"),
        {"e": f"coalesce-{uuid.uuid4().hex[:8]}@example.invalid"}).scalar_one()


def _relocated_row(conn, case, **over):
    """A pre-existing unattributed DBI row for the case's identifier, as Phase C would leave it.

    ``ck_dbi_human_approval_attributed`` is checked on INSERT, so a simulated human approval must
    carry its approver in the same statement rather than being patched in afterwards.
    """
    values = {"h": case["hash"], "n": f"relocated {case['hash'][:8]}",
              "trust": None, "source": None, "method": None, "by": None, "at": None}
    values.update(over)
    if values["trust"] == HUMAN_APPROVED and values["by"] is None:
        values["by"] = _approver(conn)
    return conn.execute(text(
        "INSERT INTO drake_business_identity ("
        "  identifier_hash, subject_type, relationship_entity_id, first_year, last_year, "
        "  return_count, subject_name, return_types, trust_level, confirmation_source, "
        "  evidence_method, confirmed_by_user_id, confirmed_at) "
        "VALUES (:h, 'business_entity', NULL, 2021, 2022, 2, :n, ARRAY['1120S'], "
        "        :trust, :source, :method, :by, "
        "        CASE WHEN :by IS NULL THEN NULL ELSE now() END) RETURNING id"),
        values).scalar_one()


def test_attributing_a_relocated_row_fills_its_null_trust(conn, case):
    """The Phase C hand-off: an unattributed row must come out carrying this service's evidence."""
    existing = _relocated_row(conn, case)
    before = conn.execute(text(
        "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
        "FROM drake_business_identity WHERE id = :i"), {"i": existing}).mappings().one()
    assert before["relationship_entity_id"] is None
    assert (before["trust_level"], before["confirmation_source"],
            before["evidence_method"]) == (None, None, None)

    result = machine.attribute_entity_by_provenance(conn, request_for(case))

    assert result.business_identity_id == existing      # updated in place, not duplicated
    assert result.identity_created is False
    after = conn.execute(text(
        "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
        "FROM drake_business_identity WHERE id = :i"), {"i": existing}).mappings().one()
    assert after["relationship_entity_id"] == case["entity"]
    assert after["trust_level"] == IDENTIFIER_VERIFIED
    assert after["confirmation_source"] == machine.MACHINE
    assert after["evidence_method"] == machine.EVIDENCE_METHOD
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 1


def test_attributing_a_relocated_row_still_creates_its_links_and_audit(conn, case):
    """Phase C writes no links, so the attribution must still produce all of them."""
    _relocated_row(conn, case)
    audits = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                          {"a": machine.AUDIT_ACTION}).scalar()

    result = machine.attribute_entity_by_provenance(conn, request_for(case))

    assert set(result.source_links_created) == set(case["contacts"])
    assert conn.execute(text(
        "SELECT count(*) FROM entity_source_links WHERE relationship_entity_id = :e"),
        {"e": case["entity"]}).scalar() == 2
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": machine.AUDIT_ACTION}).scalar() == audits + 1
    assert result.changed is True


@pytest.mark.parametrize("held", ["trust_level", "confirmation_source", "evidence_method"])
def test_coalesce_is_per_column_and_never_overwrites_a_set_value(conn, case, held):
    """A partially populated row keeps every value it has and gains only the ones it lacks."""
    sentinel = {"trust_level": HUMAN_APPROVED, "confirmation_source": "human",
                "evidence_method": "human_adjudicated_entity_identifier"}[held]
    existing = _relocated_row(conn, case, **{
        {"trust_level": "trust", "confirmation_source": "source",
         "evidence_method": "method"}[held]: sentinel})

    machine.attribute_entity_by_provenance(conn, request_for(case))

    row = conn.execute(text(
        "SELECT trust_level, confirmation_source, evidence_method "
        "FROM drake_business_identity WHERE id = :i"), {"i": existing}).mappings().one()
    assert row[held] == sentinel, "an existing value must survive"
    filled = {"trust_level": IDENTIFIER_VERIFIED, "confirmation_source": machine.MACHINE,
              "evidence_method": machine.EVIDENCE_METHOD}
    for column, expected in filled.items():
        if column != held:
            assert row[column] == expected, f"{column} was NULL and should have been filled"


def test_the_upsert_coalesces_rather_than_assigning_the_trust_columns():
    """Read the statement itself: assignment would silently reintroduce the downgrade risk."""
    sql = machine._IDENTITY_UPSERT
    for column in ("trust_level", "confirmation_source", "evidence_method"):
        assert f"COALESCE(drake_business_identity.{column}, EXCLUDED.{column})" in sql
        assert f"{column}            = EXCLUDED.{column}" not in sql
        assert f"{column}    = EXCLUDED.{column}" not in sql


def test_each_call_writes_one_audit_entry(conn, case):
    """Stated, not hidden: rows are idempotent, the audit trail records every invocation."""
    before = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                          {"a": machine.AUDIT_ACTION}).scalar()
    machine.attribute_entity_by_provenance(conn, request_for(case))
    machine.attribute_entity_by_provenance(conn, request_for(case))
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": machine.AUDIT_ACTION}).scalar() == before + 2


# --- the provenance rule is the whole point -------------------------------------------------------

def test_a_name_only_entity_is_refused(conn, case):
    """The D7 defect: 18 candidates that rested on a shared name and nothing else.

    This twin carries details, so it is not merely empty — but none of them is a stored reference to
    a source record, which is the only thing that authorises an unattended bind.
    """
    twin = create_named_entity(conn, "business", case["name"])
    set_details(conn, twin, {"origin": "manual_entry", "notes": "same name as the real entity"})
    assert refusal(conn, request_for(case, entity=twin)) == machine.PROVENANCE_NAME_ONLY


def test_an_entity_whose_provenance_points_elsewhere_is_refused(conn, case):
    other = make_contact(conn, year=2022, return_id=999001,
                         hash_value=identifier_hash("99-8000999"), name="SOMEONE ELSE LLC")
    set_details(conn, case["entity"], {"origin": "canonical_repair",
                                       "source_contact_ids": [other]})
    assert refusal(conn, request_for(case)) == machine.PROVENANCE_MISMATCH


def test_an_entity_with_empty_details_is_refused(conn, case):
    """``details`` is NOT NULL and defaults to ``{}``, which is the shape a bare entity carries."""
    set_details(conn, case["entity"], {})
    assert refusal(conn, request_for(case)) == machine.PROVENANCE_MISSING


@pytest.mark.parametrize("details_key", ["source_record_ids", "drake_return_ids",
                                         "identifier_hash", "canonical_repair"])
def test_every_accepted_provenance_form_works(conn, case, details_key):
    shapes = {
        "source_record_ids": {"origin": "canonical_repair", "source_record_ids": [
            f"{y}:{r}:taxpayer" for y, r in zip((2021, 2022), case["returns"], strict=True)]},
        "drake_return_ids": {"created_from": "drake", "drake_return_ids": case["returns"]},
        "identifier_hash": {"origin": "drake", "identifier_hash": case["hash"]},
        "canonical_repair": {"canonical_repair": {"method": "x",
                                                  "source_contact_ids": case["contacts"]}},
    }
    set_details(conn, case["entity"], shapes[details_key])
    result = machine.attribute_entity_by_provenance(conn, request_for(case))
    assert result.provenance_form.endswith(details_key if details_key != "canonical_repair"
                                           else "canonical_repair.source_contact_ids")


def _executable_source(fn) -> str:
    """One function's statements with its docstring removed — prose is not behaviour."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def test_the_deciding_code_reads_no_name_or_address_evidence():
    """Precise about where it matters: the functions that DECIDE must not touch a name.

    A name still reaches the module as a stored label — ``subject_name`` is NOT NULL — so banning the
    word module-wide would assert nothing useful. What must hold is that nothing which chooses an
    entity, accepts a contact or validates provenance ever consults one.
    """
    deciding = "\n".join(_executable_source(fn) for fn in (
        machine._check_provenance, machine._load_contacts, machine._check_entity_type,
        machine._check_identifier_is_free, machine._check_contacts_are_free))
    for banned in ("full_name", "address", "city", "postal_code", "normalized_name",
                   "similar", "fuzzy", "levenshtein", "ilike", "soundex"):
        assert banned not in deciding.lower(), f"{banned} must not influence attribution"


def test_the_module_has_no_similarity_logic_anywhere():
    source = Path(machine.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[2].lower()
    for banned in ("similar", "fuzzy", "levenshtein", "soundex", "ilike", "difflib"):
        assert banned not in body, f"{banned} has no place in unattended attribution"


def test_the_module_offers_no_entity_resolver():
    resolvers = [n for n in dir(machine) if callable(getattr(machine, n))
                 and re.search(r"(^|_)(match|find|search|resolve|lookup)", n, re.I)]
    assert resolvers == [], f"attribution must not offer an entity resolver: {resolvers}"


# --- refusals, all fail closed ---------------------------------------------------------------------

def test_an_unknown_entity_is_refused(conn, case):
    missing = conn.execute(text(
        "SELECT coalesce(max(id), 0) + 1000 FROM relationship_entities")).scalar()
    assert refusal(conn, request_for(case, entity=missing)) == machine.ENTITY_NOT_FOUND


def test_an_inactive_entity_is_refused(conn, case):
    conn.execute(text("UPDATE relationship_entities SET active = false WHERE id = :e"),
                 {"e": case["entity"]})
    assert refusal(conn, request_for(case)) == machine.ENTITY_INACTIVE


def test_an_incompatible_entity_type_is_refused(conn, case):
    trust = create_named_entity(conn, "trust", case["name"])
    set_details(conn, trust, {"source_contact_ids": case["contacts"]})
    assert refusal(conn, request_for(case, entity=trust)) == machine.ENTITY_TYPE_MISMATCH


def test_a_natural_person_identifier_is_refused(conn, case):
    contacts = [make_contact(conn, year=y, return_id=r, hash_value=case["person_hash"],
                             name="A PERSON")
                for y, r in zip((2021, 2022, 2023), case["person_returns"], strict=True)]
    set_details(conn, case["entity"], {"source_contact_ids": contacts})
    assert refusal(conn, request_for(case, identifier=PERSON_SSN, contacts=contacts)) == \
        machine.SUBJECT_IS_NATURAL_PERSON


def test_a_mixed_subject_history_is_refused(conn, case):
    make_return(conn, year=2023, return_type="1040", name=case["name"], tp_hash=case["hash"],
                dob="1980-05-05")
    assert refusal(conn, request_for(case)) == machine.SUBJECT_REQUIRES_REVIEW


def test_a_person_then_estate_history_is_refused(conn, case):
    estate_hash = identifier_hash("99-8000777")
    r1 = make_return(conn, year=2021, return_type="1040", name="DECEDENT",
                     tp_hash=estate_hash, dob="1940-02-02")
    r2 = make_return(conn, year=2023, return_type="1041", name="DECEDENT ESTATE",
                     tp_hash=estate_hash)
    contacts = [make_contact(conn, year=2021, return_id=r1, hash_value=estate_hash, name="D"),
                make_contact(conn, year=2023, return_id=r2, hash_value=estate_hash, name="D")]
    set_details(conn, case["entity"], {"source_contact_ids": contacts})
    assert refusal(conn, request_for(case, identifier="99-8000777", contacts=contacts)) == \
        machine.SUBJECT_REQUIRES_REVIEW


def test_an_ssn_identifier_type_is_refused(conn, case):
    assert refusal(conn, request_for(case, identifier_type="ssn")) == \
        machine.UNSUPPORTED_IDENTIFIER_TYPE


def test_an_identifier_with_no_digits_is_refused(conn, case):
    assert refusal(conn, request_for(case, identifier=" -- ")) == machine.UNUSABLE_IDENTIFIER


def test_an_identifier_that_denotes_a_person_is_refused(conn, case):
    conn.execute(text(
        "INSERT INTO drake_identity (identifier_hash, first_year, last_year, return_count, "
        "taxpayer_name) VALUES (:h, 2021, 2022, 2, 'A Person')"), {"h": case["hash"]})
    assert refusal(conn, request_for(case)) == machine.IDENTIFIER_BOUND_TO_PERSON


def test_an_identifier_owned_by_another_entity_is_refused(conn, case):
    other = create_named_entity(conn, "business", f"other-{uuid.uuid4().hex[:6]}")
    conn.execute(text(
        "INSERT INTO drake_business_identity (identifier_hash, subject_type, "
        "relationship_entity_id, first_year, last_year, return_count, subject_name, return_types) "
        "VALUES (:h, 'business_entity', :e, 2021, 2022, 2, 'other', ARRAY['1120S'])"),
        {"h": case["hash"], "e": other})
    assert refusal(conn, request_for(case)) == machine.IDENTIFIER_BOUND_TO_OTHER_ENTITY


def test_an_identifier_already_on_the_target_entity_is_not_a_conflict(conn, case):
    machine.attribute_entity_by_provenance(conn, request_for(case))
    again = machine.attribute_entity_by_provenance(conn, request_for(case))
    assert again.identity_created is False


def test_a_non_drake_source_contact_is_refused(conn, case):
    foreign = make_contact(conn, year=2023, return_id=999002, hash_value=case["hash"],
                           name=case["name"], system="Wealthbox")
    assert refusal(conn, request_for(case, contacts=[*case["contacts"], foreign])) == \
        machine.SOURCE_RECORD_NOT_DRAKE


def test_a_contact_carrying_a_different_identifier_is_refused(conn, case):
    other = make_contact(conn, year=2022, return_id=999003,
                         hash_value=identifier_hash("99-8000888"), name="OTHER LLC")
    assert refusal(conn, request_for(case, contacts=[*case["contacts"], other])) == \
        machine.SOURCE_RECORD_WRONG_IDENTIFIER


def test_naming_only_some_of_the_identifiers_contacts_is_refused(conn, case):
    assert refusal(conn, request_for(case, contacts=case["contacts"][:1])) == \
        machine.SOURCE_RECORDS_INCOMPLETE


def test_a_contact_already_linked_to_another_entity_is_refused(conn, case):
    other = create_named_entity(conn, "business", f"other-{uuid.uuid4().hex[:6]}")
    conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id) "
        "VALUES (:e, :c)"), {"e": other, "c": case["contacts"][0]})
    assert refusal(conn, request_for(case)) == machine.SOURCE_RECORD_BOUND_ELSEWHERE


def test_a_missing_source_contact_is_refused(conn, case):
    missing = conn.execute(text(
        "SELECT coalesce(max(id), 0) + 1000 FROM source_contacts")).scalar()
    assert refusal(conn, request_for(case, contacts=[*case["contacts"], missing])) == \
        machine.SOURCE_RECORD_NOT_FOUND


def test_naming_no_source_contacts_is_refused(conn, case):
    assert refusal(conn, request_for(case, contacts=[])) == machine.MISSING_SOURCE_RECORDS


def test_an_identifier_with_no_returns_is_refused(conn, case):
    orphan = identifier_hash("99-8000666")
    contact = make_contact(conn, year=2022, return_id=999004, hash_value=orphan, name="ORPHAN LLC")
    set_details(conn, case["entity"], {"source_contact_ids": [contact]})
    assert refusal(conn, request_for(case, identifier="99-8000666", contacts=[contact])) == \
        machine.NO_RETURN_EVIDENCE


def test_every_refusal_writes_nothing(conn, case):
    twin = create_named_entity(conn, "business", case["name"])
    dbi = conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar()
    esl = conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar()
    events = conn.execute(text("SELECT count(*) FROM audit_events")).scalar()

    refusal(conn, request_for(case, entity=twin))

    assert conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar() == dbi
    assert conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar() == esl
    assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar() == events


# --- the two writers stay apart --------------------------------------------------------------------

def test_the_human_service_still_demands_an_approver(conn, case):
    """Adding the machine path must not weaken adjudication."""
    from app.services import drake_entity_adjudication as human

    request = human.AdjudicationRequest(
        relationship_entity_id=case["entity"], identifier=BUSINESS_EIN, identifier_type="ein",
        source_contact_ids=tuple(case["contacts"]),
        evidence=(human.Evidence(kind="document", reference="x"),), actor_user_id=0)
    with pytest.raises(human.AdjudicationRefused) as caught:
        human.adjudicate_entity_identity(conn, request)
    assert caught.value.code == human.MISSING_ACTOR


def test_the_two_writers_use_different_trust_vocabularies():
    from app.services import drake_entity_adjudication as human

    assert machine.EVIDENCE_METHOD != human.EVIDENCE_METHOD
    assert machine.AUDIT_ACTION != human.AUDIT_ACTION
    assert machine.MACHINE == "machine" and human.HUMAN == "human"


def test_both_writers_derive_the_hash_from_the_one_helper():
    from app.services import drake_entity_adjudication as human

    assert machine.__dict__["derive_identifier_hash"] is identifier_hash
    assert human.__dict__["derive_identifier_hash"] is identifier_hash
    assert "identifier_hash" not in machine.AttributionRequest.__dataclass_fields__
