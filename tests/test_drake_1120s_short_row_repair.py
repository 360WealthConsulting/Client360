"""The 2021/2022 1120S short row, end to end: re-key the stored rows, then re-import them.

The importer fix alone is not enough and the migration alone is not enough, because ``return_type``
is an input to ``return_identity_key`` and that key is the returns upsert's ``ON CONFLICT`` target:

* correcting ``return_type`` without the key -> the next import matches the old key and writes NULL
  back over the correction;
* correcting the key without the importer -> the next import computes the old key, matches nothing,
  and INSERTS a duplicate return.

So the test that matters is the pair: migrate, then re-import the SAME source and prove nothing was
inserted and nothing duplicated — twice. Everything else here guards a specific way the fix could go
wrong: a legitimate multi-form history collapsing, an identifier that must stay ambiguous being
quietly resolved, and the two production cases the fix was traced from.

Temp/test rows only, tagged and torn down. No live taxpayer data: identifier hashes are obvious
stand-ins and every figure is invented.
"""
import csv
import hashlib
import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import engine, metadata
from app.importers.drake_client_csv import (
    CLIENT_EXPORT_HEADER,
    normalize_row,
    read_client_rows,
    to_mapping,
)
from app.importers.drake_returns import upsert_return_rows
from app.services.drake_return_identity import compute_return_identity_key
from app.services.drake_return_subject import Observation, classify
from app.services.drake_subject_routing import route_identifier

drake_client_returns = metadata.tables["drake_client_returns"]

HEADER = CLIENT_EXPORT_HEADER
_I = {name: index for index, name in enumerate(HEADER) if name}

_TAG = "DRK1120S"

#: The production migration's "nothing outside the cohort is in the wrong state" guard, scoped away
#: for the tests that are not exercising it. ``client360_test`` accumulates rows from other suites, so
#: a global NULL-return_type count is not a meaningful assertion here.
_NO_STRAY_ROWS = "SELECT count(*) FROM drake_client_returns WHERE FALSE AND :ids IS NOT NULL"
_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / \
    "drake03_1120s_short_row_rekey.py"


def _load_migration():
    """Import the migration by path — ``migrations/versions`` is not a package."""
    spec = importlib.util.spec_from_file_location("drake03_under_test", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _hash(label: str) -> str:
    """A well-formed stand-in for the salted SSN/EIN hash: 64 lowercase hex, like the real thing."""
    return hashlib.sha256(f"{_TAG}:{label}".encode()).hexdigest()


def _identifier_hash(value):
    value = (value or "").strip()
    return _hash(value) if value else None


def _row(values: dict[str, str]) -> list[str]:
    row = [""] * len(HEADER)
    for name, value in values.items():
        row[_I[name]] = value
    return row


def _shorten(row: list[str]) -> list[str]:
    """One field fewer, exactly as the 2021/2022 export emits a 1120S return."""
    drop = _I["Misc4"]
    assert row[drop] == ""
    return row[:drop] + row[drop + 1:]


def _business_1120s(social: str, name: str, *, agi: str = " 6294 ") -> list[str]:
    return _row({
        "TP_Social": social, "TP_FirstName": name,
        "City": "Salem", "State": "VA", "Zip": "24153",
        "Prep": "5", "AGI": agi, "Prep_Fee": " 650 ", "Wh_Ral": " 0 ", "Paid": " 0 ",
        "Type": "1120S", "e-File Product #1": "1120S", "e-File Product #2": "7004",
    })


def _personal_1040(social: str, first: str, last: str, *, dob: str = "04031953") -> list[str]:
    return _row({
        "TP_Social": social, "TP_FirstName": first, "TP_LastName": last, "TP_DoB": dob,
        "FS": "2", "AGI": " 109076 ", "Prep_Fee": " 693 ", "Paid": " 0 ", "Type": "1040",
        "e-File Product #1": "1040",
    })


def _write_export(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row)
    return path


def _parsed_as_the_old_importer_did(values, *, tax_year, row_number, when):
    """What the pre-fix importer produced: DictReader mapping straight onto the short row."""
    from app.importers.drake_client_csv import clean_value, parse_client_row

    mapping = {clean_value(key): clean_value(value)
               for key, value in to_mapping(HEADER, values).items() if key is not None}
    return parse_client_row(mapping, tax_year=tax_year, source_row_number=row_number,
                            source_updated_at=when, identifier_hash=_identifier_hash)


@pytest.fixture
def cleanup():
    """Remove every row this module's tagged identifiers own, whatever the test did."""
    hashes: list[str] = []
    yield hashes

    if hashes:
        with engine.begin() as connection:
            connection.execute(delete(drake_client_returns).where(
                drake_client_returns.c.taxpayer_identifier_hash.in_(hashes)))


def _fetch(connection, taxpayer_hash):
    return connection.execute(
        select(drake_client_returns)
        .where(drake_client_returns.c.taxpayer_identifier_hash == taxpayer_hash)
        .order_by(drake_client_returns.c.tax_year)
    ).mappings().all()


# --- the pair: migrate, then re-import ------------------------------------------------------------

def test_migration_then_reimport_inserts_nothing_and_duplicates_nothing(tmp_path, cleanup):
    """The whole point of doing both halves in one release."""
    social = "272000001"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    values = _business_1120s(social, "EXAMPLE SHORTROW LLC")
    short = _shorten(values)
    export = _write_export(tmp_path / "CLIENT.CSV", [short])

    # BEFORE: the row as the pre-fix importer stored it — return_type NULL, key computed from ''.
    stale = _parsed_as_the_old_importer_did(short, tax_year=2021, row_number=1, when=when)
    assert stale["return_type"] is None

    with engine.begin() as connection:
        upsert_return_rows(connection, [stale])

    with engine.connect() as connection:
        before = _fetch(connection, taxpayer)

    assert len(before) == 1
    assert before[0]["return_type"] is None
    old_key = before[0]["return_identity_key"]
    row_id = before[0]["id"]

    # A. the migration, driven with this test's cohort rather than the frozen production one.
    with engine.begin() as connection:
        rows = migration._load(connection, {row_id: old_key})
        migrated = migration._apply(
            connection, rows,
            values_for=migration._repaired_values,
            expected_return_type="1120S",
            other_null_check="SELECT count(*) FROM drake_client_returns"
                             " WHERE return_type IS NULL AND NOT (id = ANY(:ids))"
                             f" AND taxpayer_identifier_hash = '{taxpayer}'",
        )

    assert migrated == 1

    # B. the corrected row.
    with engine.connect() as connection:
        after = _fetch(connection, taxpayer)

    assert len(after) == 1
    assert after[0]["id"] == row_id
    assert after[0]["return_type"] == "1120S"
    assert after[0]["agi"] == pytest.approx(6294.0)
    assert after[0]["preparer_fee"] == pytest.approx(650.0)
    assert after[0]["federal_product"] == "1120S"
    assert after[0]["state_product"] == "7004"
    assert after[0]["return_identity_key"] != old_key
    assert after[0]["return_identity_key"] == compute_return_identity_key(
        2021, taxpayer, None, "1120S", None)

    # C. the corrected importer against the SAME source file.
    fresh, anomalies = read_client_rows(2021, export, identifier_hash=_identifier_hash,
                                        source_updated_at=when)
    assert anomalies == []
    assert fresh[0]["return_type"] == "1120S"

    with engine.begin() as connection:
        summary = upsert_return_rows(connection, fresh)

    # D. it resolved the existing row through the NEW key.
    assert summary["inserted"] == 0
    assert summary["updated"] == 1

    with engine.connect() as connection:
        reimported = _fetch(connection, taxpayer)

    assert len(reimported) == 1
    assert reimported[0]["id"] == row_id

    # And again — idempotent.
    with engine.begin() as connection:
        second = upsert_return_rows(connection, fresh)

    assert second["inserted"] == 0
    assert second["updated"] == 1

    with engine.connect() as connection:
        twice = _fetch(connection, taxpayer)

    assert len(twice) == 1
    assert twice[0]["id"] == row_id
    assert twice[0]["return_type"] == "1120S"
    assert twice[0]["return_identity_key"] == reimported[0]["return_identity_key"]


def test_reimport_without_the_migration_would_have_duplicated_the_row(tmp_path, cleanup):
    """The failure this release exists to prevent, demonstrated rather than asserted in prose."""
    social = "272000002"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    short = _shorten(_business_1120s(social, "EXAMPLE UNMIGRATED LLC"))
    export = _write_export(tmp_path / "CLIENT.CSV", [short])

    with engine.begin() as connection:
        upsert_return_rows(connection, [
            _parsed_as_the_old_importer_did(short, tax_year=2021, row_number=1, when=when)])

    # No migration. The corrected importer computes a key nothing carries.
    fresh, _ = read_client_rows(2021, export, identifier_hash=_identifier_hash,
                                source_updated_at=when)

    with engine.begin() as connection:
        summary = upsert_return_rows(connection, fresh)

    assert summary["inserted"] == 1

    with engine.connect() as connection:
        rows = _fetch(connection, taxpayer)

    assert len(rows) == 2, "the un-migrated row and its corrected twin — exactly the duplication"


def test_drake03_descends_from_the_previous_head_and_is_the_only_one():
    """The re-key must sit on top of ``dbi01``, and must not fork the graph."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))

    assert set(scripts.get_heads()) == {"drake03"}
    assert scripts.get_revision("drake03").down_revision == "dbi01"


# --- the migration's guards ------------------------------------------------------------------------

def test_downgrade_restores_the_stored_row_exactly(tmp_path, cleanup):
    social = "272000003"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    short = _shorten(_business_1120s(social, "EXAMPLE REVERSIBLE LLC"))

    with engine.begin() as connection:
        upsert_return_rows(connection, [
            _parsed_as_the_old_importer_did(short, tax_year=2021, row_number=1, when=when)])

    with engine.connect() as connection:
        original = dict(_fetch(connection, taxpayer)[0])

    with engine.begin() as connection:
        rows = migration._load(connection, {original["id"]: original["return_identity_key"]})
        migration._apply(
            connection, rows, values_for=migration._repaired_values,
            expected_return_type="1120S",
            other_null_check=_NO_STRAY_ROWS,
        )

    # Reverse it exactly the way the downgrade does: re-derive from the untouched raw payload.
    with engine.begin() as connection:
        current = _fetch(connection, taxpayer)[0]
        restored = migration._original_values(current["raw_data"])
        connection.execute(text("""
            UPDATE drake_client_returns
               SET return_type = :return_type, return_identity_key = :new_key,
                   agi = :agi, preparer_fee = :preparer_fee,
                   prepare_date = :prepare_date, review_date = :review_date,
                   approved_date = :approved_date, complete_date = :complete_date,
                   federal_product = :federal_product, federal_ack_date = :federal_ack_date,
                   federal_ack_code = :federal_ack_code, state_product = :state_product,
                   state_ack_date = :state_ack_date, state_ack_code = :state_ack_code
             WHERE id = :id
        """), {"id": current["id"], "new_key": original["return_identity_key"], **restored})

    with engine.connect() as connection:
        final = dict(_fetch(connection, taxpayer)[0])

    assert final == original, "downgrade must restore every column, not just return_type"


def test_the_migration_refuses_a_row_that_has_drifted(cleanup):
    social = "272000004"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)

    short = _shorten(_business_1120s(social, "EXAMPLE DRIFTED LLC"))

    with engine.begin() as connection:
        upsert_return_rows(connection, [
            _parsed_as_the_old_importer_did(short, tax_year=2021, row_number=1,
                                            when=datetime.now(UTC))])

    with engine.connect() as connection:
        row = _fetch(connection, taxpayer)[0]

    with pytest.raises(RuntimeError, match="frozen identity key"), engine.begin() as connection:
        migration._load(connection, {row["id"]: _hash("some other key")})


def test_the_migration_refuses_a_cohort_that_is_only_partly_present(cleanup):
    """Some of the cohort but not all of it is drift, and stops everything."""
    social = "272000014"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)

    short = _shorten(_business_1120s(social, "EXAMPLE PARTIAL LLC"))

    with engine.begin() as connection:
        upsert_return_rows(connection, [
            _parsed_as_the_old_importer_did(short, tax_year=2021, row_number=1,
                                            when=datetime.now(UTC))])

    with engine.connect() as connection:
        present = _fetch(connection, taxpayer)[0]

    absent = -(abs(hash(uuid.uuid4().hex)) % 1_000_000) - 1
    cohort = {present["id"]: present["return_identity_key"], absent: _hash("absent")}

    with pytest.raises(RuntimeError, match="refusing to migrate"), engine.begin() as connection:
        migration._load(connection, cohort)


def test_the_migration_is_a_no_op_where_the_cohort_was_never_imported():
    """A fresh development database, CI and a restore rehearsal all upgrade through this revision.

    None of them has ever imported Drake, so there is nothing to repair — and refusing to migrate
    would make the revision unrunnable everywhere except production.
    """
    absent = {-(abs(hash(uuid.uuid4().hex)) % 1_000_000) - 1: _hash("absent"),
              -(abs(hash(uuid.uuid4().hex)) % 1_000_000) - 2: _hash("also absent")}

    with engine.begin() as connection:
        assert migration._load(connection, absent) == []


def test_the_migration_touches_only_the_columns_after_the_displacement(cleanup):
    social = "272000005"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)

    short = _shorten(_business_1120s(social, "EXAMPLE SCOPED LLC"))

    with engine.begin() as connection:
        upsert_return_rows(connection, [
            _parsed_as_the_old_importer_did(short, tax_year=2021, row_number=7,
                                            when=datetime.now(UTC))])

    with engine.connect() as connection:
        before = dict(_fetch(connection, taxpayer)[0])

    with engine.begin() as connection:
        rows = migration._load(connection, {before["id"]: before["return_identity_key"]})
        migration._apply(connection, rows, values_for=migration._repaired_values,
                         expected_return_type="1120S",
                         other_null_check=_NO_STRAY_ROWS)

    with engine.connect() as connection:
        after = dict(_fetch(connection, taxpayer)[0])

    untouched = ("id", "tax_year", "source_row_number", "taxpayer_identifier_hash",
                 "spouse_identifier_hash", "taxpayer_first_name", "taxpayer_last_name",
                 "taxpayer_normalized_name", "taxpayer_dob", "spouse_first_name",
                 "spouse_last_name", "spouse_normalized_name", "spouse_dob", "filing_status",
                 "preparer_code", "source_updated_at", "raw_data", "identity_status")

    for column in untouched:
        assert after[column] == before[column], f"{column} must not change"

    assert after["return_type"] != before["return_type"]
    assert after["raw_data"] == before["raw_data"], "the raw payload is the audit trail"


def test_downgrade_refuses_once_a_re_import_has_refreshed_raw_data():
    """The migration is lossless only while ``raw_data`` is still the pre-fix payload.

    A Drake source re-import legitimately refreshes it: the corrected importer writes the NORMALIZED
    mapping. After that the historical values cannot be reconstructed from the live row, and both
    directions must refuse rather than synthesize them — the verified pre-migration backup is then the
    rollback mechanism. This asserts the refusal, so it cannot be relaxed by accident.
    """
    values = _shorten(_business_1120s("272000015", "EXAMPLE REFRESHED LLC"))

    original = {key: value for key, value in to_mapping(HEADER, values).items() if key is not None}
    refreshed = {key: value for key, value in
                 to_mapping(HEADER, normalize_row(HEADER, values).values).items() if key is not None}

    # While raw_data is the original payload, both directions work.
    assert migration._original_values(json.dumps(original))["return_type"] is None
    assert migration._repaired_values(json.dumps(original))["return_type"] == "1120S"

    # Once it has been refreshed by a re-import, downgrade's own precondition fails...
    assert migration._original_values(json.dumps(refreshed))["return_type"] == "1120S", \
        "downgrade requires a NULL return_type here; a non-NULL value is what makes it refuse"

    # ...and re-running the upgrade refuses too, rather than shifting an already-correct row again.
    with pytest.raises(RuntimeError, match="does not match the proven short-row shape"):
        migration._repaired_values(json.dumps(refreshed))


# --- mixed years, and a legitimate multi-form history ----------------------------------------------

def test_one_identifier_keeps_a_row_per_year_across_malformed_and_valid_exports(tmp_path, cleanup):
    social = "272000006"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    values = _business_1120s(social, "EXAMPLE MIXED YEARS LLC")
    exports = {
        2021: _write_export(tmp_path / "2021.csv", [_shorten(values)]),
        2022: _write_export(tmp_path / "2022.csv", [_shorten(values)]),
        2023: _write_export(tmp_path / "2023.csv", [values]),
    }

    for year, export in exports.items():
        rows, anomalies = read_client_rows(year, export, identifier_hash=_identifier_hash,
                                           source_updated_at=when)
        assert anomalies == []
        with engine.begin() as connection:
            upsert_return_rows(connection, rows)

    with engine.connect() as connection:
        stored = _fetch(connection, taxpayer)

    assert [row["tax_year"] for row in stored] == [2021, 2022, 2023]
    assert {row["return_type"] for row in stored} == {"1120S"}
    assert len({row["return_identity_key"] for row in stored}) == 3, "one row per year, distinct"

    observations = [Observation(return_type=row["return_type"], tax_year=row["tax_year"])
                    for row in stored]
    assert classify(observations).outcome == "single_subject"


def test_a_legitimate_election_change_is_not_collapsed_or_rejected(tmp_path, cleanup):
    """A company that filed 1065 and later 1120S has a real multi-form history."""
    social = "272000007"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    partnership = _business_1120s(social, "EXAMPLE ELECTION LLC")
    partnership[_I["Type"]] = "1065"
    scorp = _business_1120s(social, "EXAMPLE ELECTION LLC")

    plan = {
        2021: (partnership, False),
        2022: (_shorten(scorp), True),
        2023: (scorp, False),
    }

    for year, (values, short) in plan.items():
        export = _write_export(tmp_path / f"{year}.csv", [values])
        rows, anomalies = read_client_rows(year, export, identifier_hash=_identifier_hash,
                                           source_updated_at=when)
        assert anomalies == [], f"{year} should parse cleanly whether or not it is short"
        assert rows[0]["return_type"] == ("1120S" if short or year == 2023 else "1065")
        with engine.begin() as connection:
            upsert_return_rows(connection, rows)

    with engine.connect() as connection:
        stored = _fetch(connection, taxpayer)

    assert len(stored) == 3, "the 1065 year and the 1120S years are separate returns"
    assert {row["return_type"] for row in stored} == {"1065", "1120S"}


# --- the two production cases the fix was traced from ----------------------------------------------

def test_case_7379_shape_classifies_as_a_business_entity_after_normalization(tmp_path):
    """A person's own 1040 identifier, and a SEPARATE business identifier whose 1120S rows are short.

    Before the fix the business identifier has no recognised return type at all and the classifier
    correctly refuses to guess. After it, the ordinary Phase B business route applies. Nothing here
    creates a DBI or an ESL — that is a later, separately authorized operation.
    """
    person_social, business_social = "272000008", "272000009"
    when = datetime.now(UTC)

    export = _write_export(tmp_path / "CLIENT.CSV", [
        _personal_1040(person_social, "EXAMPLE", "TAXPAYER"),
        _shorten(_business_1120s(business_social, "EXAMPLE WINE WAREHOUSE LLC")),
        _shorten(_business_1120s(business_social, "EXAMPLE WINE WAREHOUSE LLC")),
    ])

    rows, anomalies = read_client_rows(2021, export, identifier_hash=_identifier_hash,
                                       source_updated_at=when)
    assert anomalies == []

    person = [row for row in rows if row["taxpayer_identifier_hash"] == _hash(person_social)]
    business = [row for row in rows if row["taxpayer_identifier_hash"] == _hash(business_social)]

    before = classify([Observation(return_type=None, tax_year=2021) for _ in business])
    assert before.outcome == "unknown"
    assert before.requires_review is True

    after = classify([Observation(return_type=row["return_type"], tax_year=row["tax_year"])
                      for row in business])
    assert after.outcome == "single_subject"
    assert after.requires_review is False
    assert [subject.subject_type for subject in after.subjects] == ["business_entity"]

    route = route_identifier(_hash(business_social),
                             [Observation(return_type=row["return_type"], tax_year=row["tax_year"])
                              for row in business])
    assert route.destination == "route_business_identity"
    assert route.subject_type == "business_entity"

    # The person's own identifier is a natural person and is not disturbed.
    natural = classify([Observation(return_type=row["return_type"], tax_year=row["tax_year"],
                                    has_dob=row["taxpayer_dob"] is not None) for row in person])
    assert natural.outcome == "single_subject"
    assert [subject.subject_type for subject in natural.subjects] == ["natural_person"]


def test_case_220_shape_becomes_conflicting_and_is_not_auto_resolved(tmp_path):
    """One identifier that is BOTH a normalized 1120S taxpayer and a 1040 spouse.

    Before the fix the 1120S row is invisible and the classifier confidently answers
    ``natural_person``. After it the answer is ``conflicting_subjects`` and review — less confident
    and more correct. Normalization must surface this, never resolve it.
    """
    shared_social = "272000010"
    when = datetime.now(UTC)

    joint = _personal_1040("272000011", "EXAMPLE", "SPOUSEHOLDER", dob="03081968")
    joint[_I["SP_Social"]] = shared_social
    joint[_I["SP_FirstName"]] = "EXAMPLE"
    joint[_I["SP_LastName"]] = "SPOUSE"
    joint[_I["SP_DoB"]] = "03081968"

    export = _write_export(tmp_path / "CLIENT.CSV", [
        joint,
        _shorten(_business_1120s(shared_social, "EXAMPLE SHABBY LLC")),
    ])

    rows, anomalies = read_client_rows(2021, export, identifier_hash=_identifier_hash,
                                       source_updated_at=when)
    assert anomalies == []

    shared = _hash(shared_social)
    as_taxpayer = [row for row in rows if row["taxpayer_identifier_hash"] == shared]
    as_spouse = [row for row in rows if row["spouse_identifier_hash"] == shared]

    assert as_taxpayer and as_spouse, "the fixture must exercise both roles of one identifier"

    before = classify(
        [Observation(return_type=None, tax_year=2021) for _ in as_taxpayer]
        + [Observation(return_type=row["return_type"], tax_year=row["tax_year"], has_dob=True)
           for row in as_spouse]
    )
    assert before.outcome == "single_subject"
    assert before.requires_review is False, "today the defect makes this confidently wrong"

    after = classify(
        [Observation(return_type=row["return_type"], tax_year=row["tax_year"])
         for row in as_taxpayer]
        + [Observation(return_type=row["return_type"], tax_year=row["tax_year"], has_dob=True)
           for row in as_spouse]
    )
    assert after.outcome == "conflicting_subjects"
    assert after.requires_review is True
    assert after.subjects == (), "no subject is proposed for a contradiction"

    assert route_identifier(shared, [
        Observation(return_type=row["return_type"], tax_year=row["tax_year"])
        for row in as_taxpayer
    ] + [
        Observation(return_type=row["return_type"], tax_year=row["tax_year"], has_dob=True)
        for row in as_spouse
    ]).destination == "route_review"


# --- unrelated data is unaffected -------------------------------------------------------------------

def test_well_formed_years_import_exactly_as_before(tmp_path, cleanup):
    """2023-2025 carry no short rows; the reader must be a pass-through for them."""
    social = "272000012"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    export = _write_export(tmp_path / "CLIENT.CSV", [
        _personal_1040(social, "EXAMPLE", "UNAFFECTED"),
    ])

    rows, anomalies = read_client_rows(2024, export, identifier_hash=_identifier_hash,
                                       source_updated_at=when)

    assert anomalies == []
    assert rows[0]["return_type"] == "1040"
    assert rows[0]["agi"] == pytest.approx(109076.0)
    assert rows[0]["filing_status"] == "2"

    with engine.begin() as connection:
        first = upsert_return_rows(connection, rows)
        second = upsert_return_rows(connection, rows)

    assert first["inserted"] == 1
    assert second["inserted"] == 0

    with engine.connect() as connection:
        assert len(_fetch(connection, taxpayer)) == 1


def test_an_unrecognised_short_row_is_reported_and_imported_unchanged(tmp_path, cleanup):
    """Fail closed: not normalized, not dropped, and visible to a human."""
    social = "272000013"
    taxpayer = _hash(social)
    cleanup.append(taxpayer)
    when = datetime.now(UTC)

    odd = _business_1120s(social, "EXAMPLE UNKNOWN SHAPE LLC")
    odd[_I["Type"]] = ""
    odd[_I["Paid"]] = "not-a-form"
    export = _write_export(tmp_path / "CLIENT.CSV", [_shorten(odd)])

    rows, anomalies = read_client_rows(2021, export, identifier_hash=_identifier_hash,
                                       source_updated_at=when)

    assert len(anomalies) == 1
    assert anomalies[0]["source_row_number"] == 1
    assert "not a recognised return form" in anomalies[0]["detail"]
    assert rows[0]["return_type"] is None, "left exactly as it is, not guessed at"

    with engine.begin() as connection:
        upsert_return_rows(connection, rows)

    with engine.connect() as connection:
        assert len(_fetch(connection, taxpayer)) == 1
