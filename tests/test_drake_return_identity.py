"""Drake return identity — the export can move, the identity cannot.

``drake_client_returns`` was upserted on ``(tax_year, source_row_number)``: the row's POSITION in the
Drake export. A re-export that inserted, deleted or re-sorted one row shifted every row after it, so
row *N* became a different taxpayer and the upsert overwrote one client's return — AGI, filing status,
acknowledgements, both identifier hashes — with another client's.

Every test here asserts either that a return keeps its identity when the FILE changes around it, or
that an identity which cannot be established is refused rather than guessed at.

Temp/test rows only, all tagged and torn down.
"""
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.db import engine, metadata
from app.importers.drake_returns import upsert_return_rows
from app.services.drake_return_identity import (
    AMBIGUOUS_COLLISION,
    IDENTIFIED,
    NO_TAXPAYER_IDENTIFIER,
    assign_identities,
    compute_return_identity_key,
    identity_payload,
    is_identified,
    normalized_identifier,
)

drake_client_returns = metadata.tables["drake_client_returns"]

_TAG = "DRKID"


def _hash(label: str) -> str:
    """A well-formed stand-in for the salted SSN/EIN hash: 64 lowercase hex, like the real thing."""
    return hashlib.sha256(label.encode()).hexdigest()


def _row(**kw):
    """One parsed export row. Only the identity-bearing fields matter to these tests."""
    row = {
        "tax_year": 2024,
        "source_row_number": 1,
        "taxpayer_identifier_hash": None,
        "spouse_identifier_hash": None,
        "taxpayer_first_name": "Ada",
        "taxpayer_last_name": "Lovelace",
        "taxpayer_normalized_name": "ada lovelace",
        "taxpayer_dob": None,
        "spouse_first_name": None,
        "spouse_last_name": None,
        "spouse_normalized_name": None,
        "spouse_dob": None,
        "filing_status": "1",
        "return_type": "1040",
        "preparer_code": None,
        "agi": None,
        "preparer_fee": None,
        "prepare_date": None,
        "review_date": None,
        "approved_date": None,
        "complete_date": None,
        "federal_product": None,
        "federal_ack_date": None,
        "federal_ack_code": None,
        "state_product": None,
        "state_ack_date": None,
        "state_ack_code": None,
        "source_updated_at": datetime.now(UTC),
        "raw_data": "{}",
    }
    row.update(kw)
    return row


# --- the identity itself --------------------------------------------------------------------------

def test_identity_is_derived_only_from_content_not_position():
    a = _row(taxpayer_identifier_hash=_hash("a"), source_row_number=1)
    b = _row(taxpayer_identifier_hash=_hash("a"), source_row_number=997)
    assert compute_return_identity_key(**_identity_args(a)) \
        == compute_return_identity_key(**_identity_args(b))


def _identity_args(row):
    return {
        "tax_year": row["tax_year"],
        "taxpayer_identifier_hash": row["taxpayer_identifier_hash"],
        "spouse_identifier_hash": row["spouse_identifier_hash"],
        "return_type": row["return_type"],
        "filing_status": row["filing_status"],
    }


def test_filing_status_separates_the_mfj_mfs_what_if_pair():
    """Production holds three couples with an MFJ and an MFS row identical in every other field.

    Without ``filing_status`` in the key they collide and one silently overwrites the other.
    """
    mfj = _identity_args(_row(taxpayer_identifier_hash=_hash("tp"),
                              spouse_identifier_hash=_hash("sp"), filing_status="2"))
    mfs = _identity_args(_row(taxpayer_identifier_hash=_hash("tp"),
                              spouse_identifier_hash=_hash("sp"), filing_status="3"))
    assert compute_return_identity_key(**mfj) != compute_return_identity_key(**mfs)


def test_return_type_separates_a_1040_from_a_business_return_on_one_identifier():
    personal = _identity_args(_row(taxpayer_identifier_hash=_hash("x"), return_type="1040"))
    business = _identity_args(_row(taxpayer_identifier_hash=_hash("x"), return_type="1120S"))
    assert compute_return_identity_key(**personal) != compute_return_identity_key(**business)


def test_payload_never_contains_a_raw_identifier():
    """The key is built from HASHES. A raw SSN must be neither an input nor recoverable."""
    payload = identity_payload(2024, _hash("tp"), None, "1040", "1")
    assert "123456789" not in payload
    assert payload == f"2024|{_hash('tp')}||1040|1"


# --- 7. blank identifier / 8. malformed identifier ------------------------------------------------

@pytest.mark.parametrize("blank", [None, "", "   ", "\x00"])
def test_blank_taxpayer_identifier_gets_no_identity(blank):
    assert compute_return_identity_key(2024, blank, None, "1040", "1") is None


@pytest.mark.parametrize("malformed", [
    "not-a-hash",
    "abc123",                       # too short
    _hash("x")[:63],                # one char short
    _hash("x") + "0",               # one char long
    _hash("x").replace("a", "z"),   # non-hex character
    "  " + _hash("x") + "  extra",  # trailing junk
])
def test_malformed_taxpayer_identifier_fails_closed(malformed):
    assert normalized_identifier(malformed) is None
    assert compute_return_identity_key(2024, malformed, None, "1040", "1") is None


def test_two_different_malformed_identifiers_do_not_collide_into_one_identity():
    """Fail-closed must not become fail-together: neither may resolve, and neither may share a key."""
    rows = [_row(taxpayer_identifier_hash="garbage-one", source_row_number=1),
            _row(taxpayer_identifier_hash="garbage-two", source_row_number=2)]
    assigned = assign_identities(rows)
    assert [r["identity_status"] for r in assigned] == [NO_TAXPAYER_IDENTIFIER] * 2
    assert all(r["return_identity_key"] is None for r in assigned)


def test_malformed_spouse_identifier_is_ignored_not_fatal():
    """A damaged SPOUSE hash must not destroy the taxpayer's identity — it degrades to no spouse."""
    with_junk = compute_return_identity_key(2024, _hash("tp"), "junk", "1040", "2")
    without = compute_return_identity_key(2024, _hash("tp"), None, "1040", "2")
    assert with_junk == without is not None


# --- 5. joint return ------------------------------------------------------------------------------

def test_joint_return_identity_is_stable_and_distinct_from_the_separate_return():
    joint = compute_return_identity_key(2024, _hash("tp"), _hash("sp"), "1040", "2")
    separate = compute_return_identity_key(2024, _hash("tp"), None, "1040", "1")
    assert joint and separate and joint != separate
    assert joint == compute_return_identity_key(2024, _hash("tp"), _hash("sp"), "1040", "2")


# --- 6. business return ---------------------------------------------------------------------------

@pytest.mark.parametrize("return_type", ["1120", "1120S", "1065", "1041", "990"])
def test_business_return_gets_a_stable_identity_of_its_own(return_type):
    """A business return is not resolvable to a PERSON, but it must still be stably IMPORTABLE."""
    key = compute_return_identity_key(2024, _hash("ein"), None, return_type, "")
    assert key == compute_return_identity_key(2024, _hash("ein"), None, return_type, "")
    assert key != compute_return_identity_key(2024, _hash("ein"), None, "1040", "")


def test_estate_return_without_a_taxpayer_identifier_is_quarantined_not_merged():
    """Production holds two DIFFERENT Stone estates in 2021, both filed with no TP_Social.

    Keying them on "no identifier" would merge two unrelated estates into one return.
    """
    rows = [_row(taxpayer_identifier_hash=None, return_type="1041", filing_status=None,
                 taxpayer_first_name="ROBERT GORDON STONE ESTATE", source_row_number=410),
            _row(taxpayer_identifier_hash=None, return_type="1041", filing_status=None,
                 taxpayer_first_name="PATRICIA ANN STONE ESTATE", source_row_number=703)]
    assigned = assign_identities(rows)
    assert all(r["identity_status"] == NO_TAXPAYER_IDENTIFIER for r in assigned)
    assert not any(is_identified(r) for r in assigned)


# --- collision detection --------------------------------------------------------------------------

def test_several_rows_claiming_one_identity_are_all_quarantined():
    """Production: one taxpayer, 2021, three separate 1040s at FS=1 with different AGI.

    Which is "the" return is not knowable from the export, so NONE is chosen.
    """
    rows = [_row(taxpayer_identifier_hash=_hash("clara"), source_row_number=n, agi=agi)
            for n, agi in ((252, 78222), (253, 10523), (504, 46681))]
    assigned = assign_identities(rows)
    assert [r["identity_status"] for r in assigned] == [AMBIGUOUS_COLLISION] * 3
    assert all(r["return_identity_key"] is None for r in assigned)


def test_a_collision_does_not_contaminate_unrelated_rows():
    rows = [_row(taxpayer_identifier_hash=_hash("dup"), source_row_number=1),
            _row(taxpayer_identifier_hash=_hash("dup"), source_row_number=2),
            _row(taxpayer_identifier_hash=_hash("clean"), source_row_number=3)]
    assigned = assign_identities(rows)
    assert assigned[2]["identity_status"] == IDENTIFIED
    assert assigned[2]["return_identity_key"] is not None


# ==================================================================================================
# Database-backed: the upsert itself. These are the regressions the positional key could not survive.
# ==================================================================================================

@pytest.fixture
def export():
    """A five-row export whose returns are all cleanly identifiable, plus teardown by identity."""
    tag = uuid.uuid4().hex[:8]
    year = 2100 + (int(tag, 16) % 800)      # far from any real Drake year
    rows = [
        _row(tax_year=year, source_row_number=1, taxpayer_identifier_hash=_hash(tag + "a")),
        _row(tax_year=year, source_row_number=2, taxpayer_identifier_hash=_hash(tag + "b")),
        _row(tax_year=year, source_row_number=3, taxpayer_identifier_hash=_hash(tag + "c"),
             spouse_identifier_hash=_hash(tag + "d"), filing_status="2"),
        _row(tax_year=year, source_row_number=4, taxpayer_identifier_hash=_hash(tag + "e"),
             return_type="1120S", filing_status=""),
        _row(tax_year=year, source_row_number=5, taxpayer_identifier_hash=_hash(tag + "f")),
    ]
    yield {"tag": tag, "year": year, "rows": rows}
    with engine.begin() as c:
        c.execute(delete(drake_client_returns).where(drake_client_returns.c.tax_year == year))


def _stored(year):
    """``{return_identity_key: (source_row_number, agi)}`` for one tax year."""
    with engine.connect() as c:
        return {r.return_identity_key: (r.source_row_number, r.agi) for r in c.execute(
            select(drake_client_returns.c.return_identity_key,
                   drake_client_returns.c.source_row_number,
                   drake_client_returns.c.agi)
            .where(drake_client_returns.c.tax_year == year))}


def _import(rows):
    with engine.begin() as c:
        return upsert_return_rows(c, rows)


# --- 4. duplicate import --------------------------------------------------------------------------

def test_importing_the_identical_export_twice_creates_no_duplicates(export):
    first = _import(export["rows"])
    assert first["inserted"] == 5 and first["updated"] == 0

    second = _import(export["rows"])
    assert second["inserted"] == 0 and second["updated"] == 5

    assert len(_stored(export["year"])) == 5


# --- 1. reordered export --------------------------------------------------------------------------

def test_reordering_the_export_changes_nothing(export):
    _import(export["rows"])
    before = _stored(export["year"])

    reversed_rows = []
    for new_position, row in enumerate(reversed(export["rows"]), start=1):
        reversed_rows.append({**row, "source_row_number": new_position})
    summary = _import(reversed_rows)

    after = _stored(export["year"])
    assert summary["inserted"] == 0, "a reorder must never create a return"
    assert set(before) == set(after), "identities must survive a reorder"
    assert len(after) == 5


# --- 2. inserted row ------------------------------------------------------------------------------

def test_inserting_a_row_touches_only_that_return(export):
    _import(export["rows"])
    before = _stored(export["year"])

    # A new return arrives at the TOP, pushing every existing row down one position.
    newcomer = _row(tax_year=export["year"], source_row_number=1,
                    taxpayer_identifier_hash=_hash(export["tag"] + "new"))
    shifted = [newcomer] + [{**row, "source_row_number": n}
                            for n, row in enumerate(export["rows"], start=2)]
    summary = _import(shifted)

    after = _stored(export["year"])
    assert summary["inserted"] == 1, "only the genuinely new return may be created"
    assert summary["updated"] == 5
    assert set(before) <= set(after), "no pre-existing identity may disappear"
    assert len(after) == 6


def test_inserting_a_row_does_not_overwrite_the_neighbour_it_displaced(export):
    """The original defect, stated directly: row 2's AGI must not land on row 3's return."""
    _import(export["rows"])
    marked = [{**row, "agi": 1000 + n} for n, row in enumerate(export["rows"], start=1)]
    _import(marked)
    before = _stored(export["year"])

    newcomer = _row(tax_year=export["year"], source_row_number=1, agi=99999,
                    taxpayer_identifier_hash=_hash(export["tag"] + "new"))
    _import([newcomer] + [{**row, "source_row_number": n}
                          for n, row in enumerate(marked, start=2)])

    after = _stored(export["year"])
    for key, (_position, agi) in before.items():
        assert after[key][1] == agi, "a displaced return kept someone else's figures"


# --- 3. deleted row / delete-and-re-add ------------------------------------------------------------

def test_deleting_a_row_cannot_mutate_another_clients_return(export):
    marked = [{**row, "agi": 1000 + n} for n, row in enumerate(export["rows"], start=1)]
    _import(marked)
    before = _stored(export["year"])

    # The second return is withdrawn; everything after it moves up a position.
    survivors = [{**row, "source_row_number": n}
                 for n, row in enumerate(marked[:1] + marked[2:], start=1)]
    _import(survivors)

    after = _stored(export["year"])
    for row in survivors:
        key = compute_return_identity_key(**_identity_args(row))
        assert after[key][1] == before[key][1], "a surviving return took the deleted row's figures"


def test_deleting_and_re_adding_a_row_restores_the_same_identity(export):
    _import(export["rows"])
    before = _stored(export["year"])

    _import([{**row, "source_row_number": n}
             for n, row in enumerate(export["rows"][:-1], start=1)])
    _import(export["rows"])          # the withdrawn return comes back

    after = _stored(export["year"])
    assert set(before) == set(after)
    assert len(after) == 5, "re-adding a return must not create a second copy of it"


# --- quarantine at the database boundary -----------------------------------------------------------

def test_quarantined_rows_are_reported_and_never_written(export):
    rows = export["rows"] + [
        _row(tax_year=export["year"], source_row_number=6, taxpayer_identifier_hash=None),
        _row(tax_year=export["year"], source_row_number=7, taxpayer_identifier_hash="malformed"),
    ]
    summary = _import(rows)

    assert summary["quarantined"] == 2
    assert summary["quarantined_no_taxpayer_identifier"] == 2
    assert len(_stored(export["year"])) == 5, "a row with no identity must not reach the table"


def test_a_quarantined_row_never_overwrites_a_previously_imported_return(export):
    marked = [{**row, "agi": 4242} for row in export["rows"]]
    _import(marked)
    before = _stored(export["year"])

    # A later, damaged export re-presents one return with its identifier destroyed and a wrong AGI.
    damaged = {**export["rows"][0], "taxpayer_identifier_hash": "", "agi": 1}
    _import([damaged])

    assert _stored(export["year"]) == before
