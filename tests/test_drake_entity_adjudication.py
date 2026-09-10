"""Human-approved adjudication of a non-natural Drake identity onto an existing entity.

WHAT THESE PIN

The pathway exists because Drake's client export does not carry a fiduciary EIN: on a 1041 the
export's taxpayer-identifier column holds the DECEDENT's SSN, and an estate with no decedent
recorded — or a living trust, which has no decedent at all — exports a blank. The unattended import
must keep quarantining those rows, and these tests assert it still does. The EIN reaches the system
only as evidence a human read, through :mod:`app.services.drake_entity_adjudication`.

The two regression fixtures are the real production shapes with synthetic identifiers: a decedent's
estate and a living trust, each a full-width 123-field export row with a blank taxpayer identifier.

Every row is seeded inside a transaction and rolled back. Nothing is committed.
"""

import ast
import hashlib
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.db import engine
from app.importers.drake_client_csv import CLIENT_EXPORT_HEADER, read_client_rows
from app.services import drake_entity_adjudication as adj
from app.services.drake_identifier import (
    IdentifierHashKeyMissing,
    identifier_digits,
    identifier_hash,
)
from app.services.drake_return_identity import (
    NO_TAXPAYER_IDENTIFIER,
    assign_identities,
    is_identified,
)
from app.services.link_trust import HUMAN_APPROVED
from app.services.relationships import create_named_entity

pytestmark = pytest.mark.skipif(
    inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)

HEADER = CLIENT_EXPORT_HEADER
_I = {name: index for index, name in enumerate(HEADER) if name}

#: Synthetic, never a production identifier. Shaped like an EIN because that is what is adjudicated.
ESTATE_EIN = "99-7000001"
TRUST_EIN = "99-7000002"


#: A deterministic, obviously-not-production hashing secret for this module's tests.
TEST_HASH_KEY = "adjudication-tests-not-a-production-key"


# --- fixtures -------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    """Give every test here its own hashing secret instead of inheriting the shell's.

    CI does not set ``MICROSOFT_TOKEN_KEY``, and these tests originally read whatever the developer
    had exported. That passed locally and failed in CI, which is precisely the class of hidden
    dependency an isolated run exists to catch. Setting it here makes the value part of the test.

    Tests that are ABOUT the absent or a specific secret override this with ``monkeypatch.delenv``
    or ``setenv``; the same function-scoped ``monkeypatch`` applies both, so the test's own call
    wins. Nothing about the product's fail-closed behaviour is relaxed — only the environment the
    tests run in is made explicit.
    """
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", TEST_HASH_KEY)


@pytest.fixture()
def conn():
    """A transaction that is always rolled back."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture()
def actor(conn):
    tag = uuid.uuid4().hex[:10]
    return conn.execute(text(
        "INSERT INTO users (email, normalized_email, display_name) "
        "VALUES (:e, :e, :d) RETURNING id"),
        {"e": f"adj-{tag}@example.invalid", "d": f"Adjudicator {tag}"}).scalar_one()


@pytest.fixture()
def trust_entity(conn):
    return create_named_entity(conn, "trust", f"adj-trust-{uuid.uuid4().hex[:8]}")


def drake_contact(conn, *, name, year, return_type="1041", role="taxpayer",
                  system="Drake", return_id=None):
    """One Drake source contact of the shape ``build_drake_contacts`` writes."""
    return_id = return_id or int(uuid.uuid4().int % 10_000_000)
    return conn.execute(text(
        "INSERT INTO source_contacts "
        "(source_system, source_file, source_record_id, source_hash, first_name, full_name, "
        " raw_data) "
        "VALUES (:sys, :file, :rec, :hash, :name, :name, CAST(:raw AS json)) RETURNING id"),
        {"sys": system, "file": f"Drake {year}", "rec": f"{year}:{return_id}:{role}",
         "hash": uuid.uuid4().hex, "name": name,
         "raw": json.dumps({"drake_return_id": return_id, "tax_year": year, "role": role,
                            "return_type": return_type, "identifier_hash": None})}).scalar_one()


def request_for(entity, contacts, actor_id, *, identifier=TRUST_EIN, identifier_type="ein",
                evidence=None, reason="verified from the filed return"):
    return adj.AdjudicationRequest(
        relationship_entity_id=entity,
        identifier=identifier,
        identifier_type=identifier_type,
        source_contact_ids=tuple(contacts),
        evidence=evidence if evidence is not None else (
            adj.Evidence(kind="document", reference="doc-1", detail="filed Form 1041"),),
        actor_user_id=actor_id,
        reason=reason,
    )


def refusal(conn, request):
    with pytest.raises(adj.AdjudicationRefused) as caught:
        adj.adjudicate_entity_identity(conn, request)
    return caught.value.code


def counts(conn, entity, hashed):
    return (
        conn.execute(text("SELECT count(*) FROM drake_business_identity "
                          "WHERE identifier_hash = :h"), {"h": hashed}).scalar(),
        conn.execute(text("SELECT count(*) FROM entity_source_links "
                          "WHERE relationship_entity_id = :e"), {"e": entity}).scalar(),
    )


# --- the happy path -------------------------------------------------------------------------------

def test_adjudicates_an_estate_onto_an_existing_trust_entity(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Estate", year=y) for y in (2022, 2023)]

    result = adj.adjudicate_entity_identity(
        conn, request_for(trust_entity, contacts, actor, identifier=ESTATE_EIN))

    assert result.relationship_entity_id == trust_entity
    assert result.subject_type == "estate_or_trust"
    assert result.first_year == 2022 and result.last_year == 2023
    assert result.return_count == 2
    assert result.return_types == ("1041",)
    assert result.identity_created is True
    assert set(result.source_links_created) == set(contacts)
    assert result.changed is True


def test_the_identity_row_carries_the_entity_and_human_approval(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    result = adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    row = conn.execute(text(
        "SELECT relationship_entity_id, subject_type, trust_level, confirmation_source, "
        "       evidence_method, confirmed_by_user_id, confirmed_at "
        "FROM drake_business_identity WHERE id = :i"),
        {"i": result.business_identity_id}).mappings().one()

    assert row["relationship_entity_id"] == trust_entity
    assert row["subject_type"] == "estate_or_trust"
    assert row["trust_level"] == HUMAN_APPROVED
    assert row["confirmation_source"] == "human"
    assert row["evidence_method"] == adj.EVIDENCE_METHOD
    assert row["confirmed_by_user_id"] == actor
    assert row["confirmed_at"] is not None


def test_every_named_source_record_is_linked_with_recorded_provenance(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=y) for y in (2022, 2023)]
    adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    rows = conn.execute(text(
        "SELECT source_contact_id, trust_level, confirmation_source, evidence_method, "
        "       confirmed_by_user_id, confirmed_at, confirmed "
        "FROM entity_source_links WHERE relationship_entity_id = :e ORDER BY source_contact_id"),
        {"e": trust_entity}).mappings().all()

    assert [r["source_contact_id"] for r in rows] == sorted(contacts)
    for row in rows:
        assert row["trust_level"] == HUMAN_APPROVED
        assert row["confirmation_source"] == "human"
        assert row["evidence_method"] == adj.EVIDENCE_METHOD
        assert row["confirmed_by_user_id"] == actor
        assert row["confirmed_at"] is not None
        assert row["confirmed"] is True


def test_the_audit_entry_answers_who_decided_what_on_which_evidence(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    evidence = (adj.Evidence(kind="document", reference="doc-7", detail="filed Form 1041"),
                adj.Evidence(kind="drake_client_index", reference="NAME_NDX/2022"))
    result = adj.adjudicate_entity_identity(
        conn, request_for(trust_entity, contacts, actor, evidence=evidence))

    entry = conn.execute(text(
        'SELECT action, actor_user_id, entity_type, entity_id, metadata '
        "FROM audit_events WHERE id = :i"), {"i": result.audit_event_id}).mappings().one()

    assert entry["action"] == adj.AUDIT_ACTION
    assert entry["actor_user_id"] == actor
    assert entry["entity_type"] == "relationship_entity"
    assert entry["entity_id"] == str(trust_entity)

    meta = entry["metadata"]
    assert meta["relationship_entity_id"] == trust_entity
    assert meta["identifier_type"] == "ein"
    assert meta["identifier_hash"] == result.identifier_hash
    assert meta["trust_level"] == HUMAN_APPROVED
    assert meta["human_adjudication"] is True
    assert meta["approved_by_user_id"] == actor
    assert meta["approved_at"]
    assert meta["source_contact_ids"] == sorted(contacts)
    assert [e["reference"] for e in meta["evidence"]] == ["doc-7", "NAME_NDX/2022"]
    assert meta["subject_type"] == "estate_or_trust"


def test_the_audit_entry_never_records_the_raw_identifier(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    result = adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    blob = conn.execute(text("SELECT metadata::text FROM audit_events WHERE id = :i"),
                        {"i": result.audit_event_id}).scalar()

    assert TRUST_EIN not in blob
    assert identifier_digits(TRUST_EIN) not in blob


# --- idempotency -----------------------------------------------------------------------------------

def test_running_the_same_adjudication_twice_creates_no_duplicates(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=y) for y in (2022, 2023)]
    request = request_for(trust_entity, contacts, actor)

    first = adj.adjudicate_entity_identity(conn, request)
    before = counts(conn, trust_entity, first.identifier_hash)
    second = adj.adjudicate_entity_identity(conn, request)

    assert counts(conn, trust_entity, first.identifier_hash) == before == (1, 2)
    assert second.business_identity_id == first.business_identity_id
    assert second.identity_created is False
    assert second.source_links_created == ()
    assert set(second.source_links_unchanged) == set(contacts)
    assert second.changed is False


def test_an_unadjudicated_identity_from_ingestion_is_claimed_not_duplicated(
        conn, actor, trust_entity):
    """Ingestion writes ``relationship_entity_id = NULL``. Adjudication fills it in, in place."""
    hashed = identifier_hash(TRUST_EIN)
    existing = conn.execute(text(
        "INSERT INTO drake_business_identity "
        "(identifier_hash, subject_type, relationship_entity_id, first_year, last_year, "
        " return_count, subject_name, return_types) "
        "VALUES (:h, 'estate_or_trust', NULL, 2022, 2022, 1, 'Synthetic Trust', "
        " ARRAY['1041']) RETURNING id"), {"h": hashed}).scalar_one()

    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    result = adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    assert result.business_identity_id == existing
    assert result.identity_created is False
    assert conn.execute(text(
        "SELECT count(*) FROM drake_business_identity WHERE identifier_hash = :h"),
        {"h": hashed}).scalar() == 1
    assert conn.execute(text(
        "SELECT relationship_entity_id FROM drake_business_identity WHERE id = :i"),
        {"i": existing}).scalar() == trust_entity


def test_a_re_run_adds_the_newly_named_source_record_only(conn, actor, trust_entity):
    first_contact = drake_contact(conn, name="Synthetic Trust", year=2022)
    result = adj.adjudicate_entity_identity(
        conn, request_for(trust_entity, [first_contact], actor))

    later = drake_contact(conn, name="Synthetic Trust", year=2023)
    second = adj.adjudicate_entity_identity(
        conn, request_for(trust_entity, [first_contact, later], actor))

    assert second.source_links_created == (later,)
    assert second.source_links_unchanged == (first_contact,)
    assert counts(conn, trust_entity, result.identifier_hash) == (1, 2)


# --- conflicts, all fail closed ----------------------------------------------------------------------

def test_an_identifier_already_adjudicated_to_another_entity_is_refused(conn, actor, trust_entity):
    other = create_named_entity(conn, "trust", f"adj-other-{uuid.uuid4().hex[:8]}")
    first = [drake_contact(conn, name="First Trust", year=2022)]
    adj.adjudicate_entity_identity(conn, request_for(other, first, actor))

    second = [drake_contact(conn, name="Second Trust", year=2022)]
    assert refusal(conn, request_for(trust_entity, second, actor)) == \
        adj.IDENTIFIER_BOUND_TO_OTHER_ENTITY


def test_an_identifier_that_denotes_a_natural_person_is_refused(conn, actor, trust_entity):
    hashed = identifier_hash(TRUST_EIN)
    conn.execute(text(
        "INSERT INTO drake_identity (identifier_hash, first_year, last_year, return_count, "
        "taxpayer_name) VALUES (:h, 2021, 2023, 3, 'A Natural Person')"), {"h": hashed})
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]

    assert refusal(conn, request_for(trust_entity, contacts, actor)) == \
        adj.IDENTIFIER_BOUND_TO_PERSON


def test_a_source_record_linked_to_another_entity_is_refused(conn, actor, trust_entity):
    other = create_named_entity(conn, "trust", f"adj-other-{uuid.uuid4().hex[:8]}")
    contact = drake_contact(conn, name="Contested Trust", year=2022)
    conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id) "
        "VALUES (:e, :c)"), {"e": other, "c": contact})

    assert refusal(conn, request_for(trust_entity, [contact], actor)) == \
        adj.SOURCE_RECORD_BOUND_ELSEWHERE


def test_an_unattributed_approval_is_refused(conn, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    assert refusal(conn, request_for(trust_entity, contacts, 0)) == adj.MISSING_ACTOR


def test_an_adjudication_with_no_evidence_is_refused(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    assert refusal(conn, request_for(trust_entity, contacts, actor, evidence=())) == \
        adj.MISSING_EVIDENCE


def test_an_adjudication_naming_no_source_records_is_refused(conn, actor, trust_entity):
    assert refusal(conn, request_for(trust_entity, [], actor)) == adj.MISSING_SOURCE_RECORDS


def test_an_inactive_entity_is_refused(conn, actor, trust_entity):
    conn.execute(text("UPDATE relationship_entities SET active = false WHERE id = :i"),
                 {"i": trust_entity})
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]

    assert refusal(conn, request_for(trust_entity, contacts, actor)) == adj.ENTITY_INACTIVE


def test_an_unknown_entity_is_refused_and_never_created(conn, actor):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    missing = conn.execute(text(
        "SELECT coalesce(max(id), 0) + 1000 FROM relationship_entities")).scalar()
    before = conn.execute(text("SELECT count(*) FROM relationship_entities")).scalar()

    assert refusal(conn, request_for(missing, contacts, actor)) == adj.ENTITY_NOT_FOUND
    assert conn.execute(text("SELECT count(*) FROM relationship_entities")).scalar() == before


def test_an_entity_of_the_wrong_type_is_refused(conn, actor):
    business = create_named_entity(conn, "business", f"adj-biz-{uuid.uuid4().hex[:8]} LLC")
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]

    assert refusal(conn, request_for(business, contacts, actor)) == adj.ENTITY_TYPE_MISMATCH


def test_person_returns_are_refused(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="A Person", year=2022, return_type="1040")]
    assert refusal(conn, request_for(trust_entity, contacts, actor)) == \
        adj.SUBJECT_IS_NATURAL_PERSON


def test_a_mixed_person_and_entity_history_is_held_for_review(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Mixed", year=2022, return_type="1041"),
                drake_contact(conn, name="Mixed", year=2022, return_type="1120S")]
    assert refusal(conn, request_for(trust_entity, contacts, actor)) == \
        adj.SUBJECT_REQUIRES_REVIEW


def test_an_ssn_identifier_type_is_refused(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    assert refusal(conn, request_for(trust_entity, contacts, actor, identifier_type="ssn")) == \
        adj.UNSUPPORTED_IDENTIFIER_TYPE


def test_an_identifier_with_no_digits_is_refused(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    assert refusal(conn, request_for(trust_entity, contacts, actor, identifier="  -- ")) == \
        adj.UNUSABLE_IDENTIFIER


def test_a_non_drake_source_record_is_refused(conn, actor, trust_entity):
    contact = drake_contact(conn, name="Synthetic Trust", year=2022, system="Wealthbox")
    assert refusal(conn, request_for(trust_entity, [contact], actor)) == \
        adj.SOURCE_RECORD_NOT_DRAKE


def test_a_source_record_that_does_not_exist_is_refused(conn, actor, trust_entity):
    missing = conn.execute(text(
        "SELECT coalesce(max(id), 0) + 1000 FROM source_contacts")).scalar()
    assert refusal(conn, request_for(trust_entity, [missing], actor)) == \
        adj.SOURCE_RECORD_NOT_FOUND


def test_a_refusal_writes_nothing_at_all(conn, actor, trust_entity):
    contacts = [drake_contact(conn, name="A Person", year=2022, return_type="1040")]
    identities = conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar()
    links = conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar()
    events = conn.execute(text("SELECT count(*) FROM audit_events")).scalar()

    refusal(conn, request_for(trust_entity, contacts, actor))

    assert conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar() == identities
    assert conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar() == links
    assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar() == events


# --- the hash is derived, never supplied, and never drifts -------------------------------------------

def test_the_request_has_no_hash_field_so_none_can_be_supplied():
    assert "identifier_hash" not in adj.AdjudicationRequest.__dataclass_fields__
    assert "hash" not in adj.AdjudicationRequest.__dataclass_fields__


def test_the_derived_hash_matches_the_historical_expression(monkeypatch):
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "a-known-key")
    expected = hashlib.sha256(b"a-known-key:997000002").hexdigest()

    assert identifier_hash(TRUST_EIN) == expected
    assert identifier_hash("997000002") == expected
    assert identifier_hash("99 7000002") == expected


def test_the_derived_hash_matches_the_retired_2025_expression(monkeypatch):
    """``scripts/import_drake_2025`` used to hash inline. Centralising it changed no value."""
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "a-known-key")

    def retired_2025(value, key="a-known-key"):
        cleaned = "" if value is None else value.replace("\x00", "").strip()
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if not digits:
            return None
        return hashlib.sha256(f"{key}:{digits}".encode()).hexdigest()

    for value in (TRUST_EIN, ESTATE_EIN, "  123-45-6789  ", "12\x003456789", "0",
                  "000000000", "", None, "no digits"):
        assert identifier_hash(value) == retired_2025(value), value


# --- the 2025 importer is centralised too ------------------------------------------------------------

# These read the module's source rather than importing it. That used to be mandatory, because the
# module ran its whole import at import time; it is now merely the narrowest way to assert a
# structural fact. The import-safety contract itself lives in
# ``tests/test_drake_2025_import_safety.py``, which imports the module for real.
_IMPORT_2025 = Path(__file__).resolve().parent.parent / "scripts" / "import_drake_2025.py"
_MISSING_SECRET_MESSAGE = "MICROSOFT_TOKEN_KEY is required for deterministic identifier hashing"


def _module_2025():
    return ast.parse(_IMPORT_2025.read_text(encoding="utf-8"))


def test_the_2025_importer_imports_the_one_hash_function():
    imported = {
        alias.name
        for node in ast.walk(_module_2025())
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.drake_identifier"
        for alias in node.names
    }
    assert "identifier_hash" in imported


def test_the_2025_importer_defines_no_hash_of_its_own():
    tree = _module_2025()
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "identifier_hash" not in defined

    modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    assert "hashlib" not in modules, "a second hashing implementation has reappeared"


def test_the_2025_importer_still_refuses_to_run_without_the_secret():
    """The fail-closed refusal survives, with its original message.

    Its POSITION moved — from module scope into ``main`` — when the import-time execution hazard was
    removed. What must not change is that the refusal exists and still reads the same, so an operator
    sees the message they always saw. That it fires ahead of every side effect is asserted
    behaviourally in ``tests/test_drake_2025_import_safety.py``.
    """
    tree = _module_2025()

    checks = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "hash_key"]
    assert checks, "the secret check is gone"

    raised = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
    messages = [arg.value for node in raised if isinstance(node.exc, ast.Call)
                for arg in node.exc.args if isinstance(arg, ast.Constant)]
    assert _MISSING_SECRET_MESSAGE in messages, "the original refusal message changed"


def test_the_all_years_importer_is_fail_closed_without_the_secret(monkeypatch, capsys):
    """Behavioural, not textual: the driver exits 2 and imports nothing."""
    from scripts import import_drake_all_years as driver

    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "")
    assert driver.main(["--year", "2022"]) == 2
    assert _MISSING_SECRET_MESSAGE.split(" is required")[0] in capsys.readouterr().err


def test_the_import_driver_uses_this_one_hash_function():
    from scripts import import_drake_all_years as driver

    assert driver.identifier_hash is identifier_hash


def test_an_identifier_with_no_digits_has_no_hash():
    assert identifier_hash("") is None
    assert identifier_hash(None) is None
    assert identifier_hash("no-digits-here") is None


def test_the_canonical_hash_refuses_when_the_secret_is_absent(monkeypatch):
    """Fail-closed at the source. No key means no identifier, never a hash of the empty key."""
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)

    with pytest.raises(IdentifierHashKeyMissing):
        identifier_hash(TRUST_EIN)


def test_a_value_with_no_digits_needs_no_secret(monkeypatch):
    """The early return happens before the key is read, so it holds even with no secret at all."""
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)

    assert identifier_hash("no-digits-here") is None


def test_adjudication_refuses_when_the_secret_is_absent(conn, actor, trust_entity, monkeypatch):
    """The service derives its own hash, so it inherits the same fail-closed behaviour."""
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)

    with pytest.raises(IdentifierHashKeyMissing):
        adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))


def test_the_adjudicated_hash_is_the_import_hash(conn, actor, trust_entity):
    """An adjudicated entity is findable by the same hash a future export would produce."""
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022)]
    result = adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    assert result.identifier_hash == identifier_hash(TRUST_EIN)


# --- no name or address fallback is introduced --------------------------------------------------------

def test_the_entity_is_named_explicitly_and_never_looked_up():
    """The caller must supply the entity id: there is no default and no resolver to fall back on."""
    field = adj.AdjudicationRequest.__dataclass_fields__["relationship_entity_id"]
    import dataclasses
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING

    resolvers = [name for name in dir(adj)
                 if callable(getattr(adj, name))
                 and re.search(r"(^|_)(match|find|search|resolve|lookup)", name, re.I)]
    assert resolvers == [], f"adjudication must not offer an entity resolver: {resolvers}"


def test_a_name_that_disagrees_with_the_entity_does_not_block_or_steer_anything(
        conn, actor, trust_entity):
    """The stored label comes from Drake; it is never a matching input."""
    contacts = [drake_contact(conn, name="A Completely Different Name", year=2022)]
    result = adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    assert result.relationship_entity_id == trust_entity
    assert result.subject_name == "A Completely Different Name"


def test_the_service_reads_no_address_evidence():
    source = Path(adj.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # skip the module docstring, which discusses addresses
    for column in ("address", "city", "postal_code", "zip"):
        assert column not in body.lower(), f"{column} must not influence adjudication"


# --- the unattended import still quarantines ------------------------------------------------------------

def _row(values):
    """A well-formed 123-field export row: every column empty except the ones named."""
    row = [""] * len(HEADER)
    for name, value in values.items():
        row[_I[name]] = value
    return row


def _write_export(tmp_path, rows) -> Path:
    path = tmp_path / "CLIENT.CSV"
    lines = [",".join(f'"{c}"' for c in HEADER)]
    lines += [",".join(f'"{c}"' for c in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


ESTATE_ROW = {"TP_FirstName": "Synthetic Decedent Estate", "Address": "1 Example Way",
              "City": "Salem", "State": "VA", "Zip": "24153", "Type": "1041", "AGI": "5997"}
TRUST_ROW = {"TP_FirstName": "Synthetic Living Trust", "Address": "2 Example Way",
             "City": "Salem", "State": "VA", "Zip": "24153", "Type": "1041", "AGI": "18349"}


@pytest.mark.parametrize("shape", [ESTATE_ROW, TRUST_ROW], ids=["estate", "living_trust"])
def test_a_blank_identifier_fiduciary_row_is_still_quarantined_unattended(tmp_path, shape):
    """Fixture A and B at the import boundary: full width, blank identifier, still refused."""
    path = _write_export(tmp_path, [_row(shape)])
    rows, anomalies = read_client_rows(2022, path, identifier_hash=identifier_hash)

    assert anomalies == []
    assert len(rows) == 1
    assert rows[0]["return_type"] == "1041"
    assert rows[0]["taxpayer_identifier_hash"] is None

    assigned = assign_identities(rows)
    assert assigned[0]["identity_status"] == NO_TAXPAYER_IDENTIFIER
    assert assigned[0]["return_identity_key"] is None
    assert is_identified(assigned[0]) is False


def test_adjudication_does_not_change_what_the_import_would_do(conn, actor, trust_entity, tmp_path):
    """Fixture A end to end: adjudicate, then re-parse the export. It still quarantines."""
    contacts = [drake_contact(conn, name="Synthetic Decedent Estate", year=2022)]
    adj.adjudicate_entity_identity(
        conn, request_for(trust_entity, contacts, actor, identifier=ESTATE_EIN))

    path = _write_export(tmp_path, [_row(ESTATE_ROW)])
    rows, _ = read_client_rows(2022, path, identifier_hash=identifier_hash)
    assigned = assign_identities(rows)

    assert assigned[0]["taxpayer_identifier_hash"] is None
    assert assigned[0]["identity_status"] == NO_TAXPAYER_IDENTIFIER


def test_the_quarantined_drake_return_row_is_left_exactly_as_drake_wrote_it(
        conn, actor, trust_entity):
    """Adjudication attributes a return; it never rewrites one."""
    row_id = conn.execute(text(
        "INSERT INTO drake_client_returns "
        "(tax_year, source_row_number, taxpayer_first_name, taxpayer_normalized_name, "
        " return_type, identity_status, source_updated_at, raw_data) "
        "VALUES (2022, 9001, 'Synthetic Living Trust', 'synthetic living trust', '1041', "
        " :status, now(), CAST(:raw AS jsonb)) RETURNING id"),
        {"status": NO_TAXPAYER_IDENTIFIER,
         "raw": '{"TP_Social": "", "Type": "1041", "TP_FirstName": "Synthetic Living Trust"}'}
    ).scalar_one()
    before = conn.execute(text(
        "SELECT taxpayer_identifier_hash, identity_status, return_identity_key, raw_data::text "
        "FROM drake_client_returns WHERE id = :i"), {"i": row_id}).one()

    contacts = [drake_contact(conn, name="Synthetic Living Trust", year=2022, return_id=row_id)]
    adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    after = conn.execute(text(
        "SELECT taxpayer_identifier_hash, identity_status, return_identity_key, raw_data::text "
        "FROM drake_client_returns WHERE id = :i"), {"i": row_id}).one()

    assert after == before
    assert after[0] is None
    assert after[1] == NO_TAXPAYER_IDENTIFIER


def test_the_attributed_returns_are_readable_without_touching_the_return_rows(
        conn, actor, trust_entity):
    """The supported answer to "which Drake returns does this entity own?"."""
    contacts = [drake_contact(conn, name="Synthetic Trust", year=2022, return_id=91001),
                drake_contact(conn, name="Synthetic Trust", year=2023, return_id=91002)]
    adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    assert adj.attributed_return_ids(conn, trust_entity) == (91001, 91002)


def test_attributed_returns_are_empty_for_an_unadjudicated_entity(conn, trust_entity):
    assert adj.attributed_return_ids(conn, trust_entity) == ()


def test_a_living_trust_adjudication_creates_no_person_identity(conn, actor, trust_entity):
    """Fixture B: no decedent exists, so nothing may appear in the person-side tables."""
    identities = conn.execute(text("SELECT count(*) FROM drake_identity")).scalar()
    people = conn.execute(text("SELECT count(*) FROM people")).scalar()
    links = conn.execute(text("SELECT count(*) FROM person_source_links")).scalar()

    contacts = [drake_contact(conn, name="Synthetic Living Trust", year=y) for y in (2022, 2023)]
    result = adj.adjudicate_entity_identity(conn, request_for(trust_entity, contacts, actor))

    assert conn.execute(text("SELECT count(*) FROM drake_identity")).scalar() == identities
    assert conn.execute(text("SELECT count(*) FROM people")).scalar() == people
    assert conn.execute(text("SELECT count(*) FROM person_source_links")).scalar() == links
    assert conn.execute(text(
        "SELECT decedent_identifier_hash FROM drake_business_identity WHERE id = :i"),
        {"i": result.business_identity_id}).scalar() is None
