"""Drake CLIENT.CSV: the 2021/2022 1120S short row, and everything the rule must NOT touch.

The 2021 and 2022 Drake exports emit one fewer field for every 1120S return. ``csv.DictReader`` maps
values to header names by position and tolerates a short row silently, so every value from the
omission point onward landed one column early: the form token that belongs in ``Type`` landed in
``Paid``, and ``return_type`` imported as NULL for all 109 of them — along with ``agi``,
``preparer_fee``, ``complete_date`` and the six e-file columns.

Every fixture here uses the REAL 123-column Drake header and the real displaced layout. No live
taxpayer data: the identifiers are obvious stand-ins and the figures are invented.

The tests are in two halves. The first proves the rule repairs the proven shape, in all the columns —
not just ``return_type``, which is the consequence that was noticed rather than the whole defect. The
second proves it refuses everything else, because silently realigning an unknown short row would be
the same class of mistake as the defect being fixed.
"""
from datetime import date

from app.importers.drake_client_csv import (
    CLIENT_EXPORT_HEADER,
    NORMALIZED,
    UNCHANGED,
    UNRECOGNISED_SHORT_ROW,
    normalize_row,
    parse_client_row,
    to_mapping,
)

HEADER = CLIENT_EXPORT_HEADER
_I = {name: index for index, name in enumerate(HEADER) if name}


def _row(values: dict[str, str]) -> list[str]:
    """A well-formed 123-field export row: every column empty except the ones named.

    Keyed by the real Drake column name, so a fixture reads like the export it stands for.
    """
    row = [""] * len(HEADER)
    for name, value in values.items():
        row[_I[name]] = value
    return row


def _shorten(row: list[str], *, drop_at: int) -> list[str]:
    """Exactly what the Drake export does to a 1120S row: emit one field fewer.

    Dropping an EMPTY field at ``drop_at`` shifts every later value one position early, which is the
    displacement observed in the real files. The trailing unnamed column falls off the end.
    """
    assert row[drop_at] == "", "the export omits an empty field; dropping a value would be a different bug"
    return row[:drop_at] + row[drop_at + 1:]


def _wellformed_1120s() -> list[str]:
    """A 1120S return as 2023-2025 emit it: 123 fields, the form token in ``Type``."""
    return _row({
        "TP_Social": "272000000", "TP_FirstName": "EXAMPLE HOLDINGS LLC",
        "Address": "1 Example Way", "City": "Salem", "State": "VA", "Zip": "24153",
        "Email": "office@example.invalid", "TP_Day_Phone": "5400000000",
        "Firm": "1", "Prep": "5", "DE": "4", "ERO": "Preparer", "Receipt": "650",
        "AGI": " 6294 ", "Prep_Fee": " 650 ", "Wh_Ral": " 0 ", "Paid": " 0 ", "Type": "1120S",
        "ADMN-Prep1": "106.5",
    })


def _malformed_1120s() -> list[str]:
    """The same return as 2021/2022 emit it: 122 fields, the form token displaced into ``Paid``."""
    return _shorten(_wellformed_1120s(), drop_at=_I["Misc4"])


# --- the proven shape is repaired, in every displaced column ---------------------------------------

def test_the_malformed_row_is_one_field_short_of_the_header():
    assert len(_malformed_1120s()) == len(HEADER) - 1


def test_without_normalization_the_form_token_lands_in_paid_and_type_is_blank():
    raw = to_mapping(HEADER, _malformed_1120s())
    assert raw["Paid"] == "1120S"
    assert raw["Type"] == ""


def test_the_short_row_normalizes_and_reports_the_recovered_form():
    shape = normalize_row(HEADER, _malformed_1120s())
    assert shape.status == NORMALIZED
    assert shape.normalized is True
    assert shape.recovered_return_type == "1120S"
    assert len(shape.values) == len(HEADER)


def test_normalization_restores_every_displaced_column_not_just_return_type():
    """``return_type`` is what was noticed; ``agi`` and the e-file columns were wrong too."""
    shape = normalize_row(HEADER, _malformed_1120s())
    mapped = to_mapping(HEADER, shape.values)

    assert mapped["Type"] == "1120S"
    assert mapped["Paid"] == " 0 "
    assert mapped["AGI"] == " 6294 "
    assert mapped["Prep_Fee"] == " 650 "
    assert mapped["Wh_Ral"] == " 0 "


def test_a_normalized_row_is_identical_to_the_well_formed_row_it_should_have_been():
    shape = normalize_row(HEADER, _malformed_1120s())
    assert list(shape.values) == _wellformed_1120s()


def test_restoring_anywhere_inside_the_empty_run_gives_the_identical_row():
    """The omitted field cannot be located exactly; every position in the empty run is equivalent."""
    from_misc1 = normalize_row(HEADER, _shorten(_wellformed_1120s(), drop_at=_I["Misc1"]))
    from_misc4 = normalize_row(HEADER, _shorten(_wellformed_1120s(), drop_at=_I["Misc4"]))
    assert from_misc1.values == from_misc4.values


def test_the_parsed_row_carries_the_recovered_return_type_and_agi():
    shape = normalize_row(HEADER, _malformed_1120s())
    mapped = {key: value for key, value in to_mapping(HEADER, shape.values).items() if key is not None}
    parsed = parse_client_row(
        {key: (value or "").strip() for key, value in mapped.items()},
        tax_year=2021, source_row_number=479, source_updated_at=None,
        identifier_hash=lambda value: f"hash:{value}",
    )

    assert parsed["return_type"] == "1120S"
    assert parsed["agi"] == 6294.0
    assert parsed["preparer_fee"] == 650.0


def test_the_e_file_columns_are_recovered_too():
    row = _wellformed_1120s()
    row[_I["e-File Product #1"]] = "1120S"
    row[_I["e-File ACK Date #1"]] = "04262022"
    row[_I["e-File ACK Code #1"]] = "A"
    row[_I["e-File Product #2"]] = "7004"

    mapped = to_mapping(HEADER, normalize_row(HEADER, _shorten(row, drop_at=_I["Misc4"])).values)

    assert mapped["e-File Product #1"] == "1120S"
    assert mapped["e-File ACK Date #1"] == "04262022"
    assert mapped["e-File ACK Code #1"] == "A"
    assert mapped["e-File Product #2"] == "7004"


def test_columns_before_the_omission_are_untouched():
    """``TP_Social`` is the identity the whole import hangs on; it sits before the displacement."""
    mapped = to_mapping(HEADER, normalize_row(HEADER, _malformed_1120s()).values)

    assert mapped["TP_Social"] == "272000000"
    assert mapped["TP_FirstName"] == "EXAMPLE HOLDINGS LLC"
    assert mapped["City"] == "Salem"
    assert mapped["Prep"] == "5"
    assert mapped["ERO"] == "Preparer"


def test_the_rule_accepts_any_recognised_form_not_just_1120s():
    """The rule is structural. 1120S is what production carries; the rule does not assume it."""
    row = _wellformed_1120s()
    row[_I["Type"]] = "1065"
    shape = normalize_row(HEADER, _shorten(row, drop_at=_I["Misc4"]))

    assert shape.status == NORMALIZED
    assert shape.recovered_return_type == "1065"


# --- everything else is left exactly as it is ------------------------------------------------------

def test_a_well_formed_row_passes_through_byte_identically():
    for form in ("1040", "1065", "1120", "1041", "990", "1120S"):
        row = _wellformed_1120s()
        row[_I["Type"]] = form
        shape = normalize_row(HEADER, row)

        assert shape.status == UNCHANGED
        assert list(shape.values) == row


def test_a_well_formed_1040_is_parsed_unchanged():
    row = _row({
        "TP_Social": "123000000", "TP_FirstName": "EXAMPLE", "TP_LastName": "TAXPAYER",
        "TP_DoB": "04031953", "FS": "2", "Type": "1040",
        "AGI": " 109076 ", "Prep_Fee": " 693 ", "Paid": " 0 ",
    })
    shape = normalize_row(HEADER, row)
    parsed = parse_client_row(
        {key: value for key, value in to_mapping(HEADER, shape.values).items() if key is not None},
        tax_year=2021, source_row_number=1, source_updated_at=None,
        identifier_hash=lambda value: f"hash:{value}",
    )

    assert shape.status == UNCHANGED
    assert parsed["return_type"] == "1040"
    assert parsed["filing_status"] == "2"
    assert parsed["taxpayer_dob"] == date(1953, 4, 3)
    assert parsed["agi"] == 109076.0


def test_a_short_row_whose_paid_is_not_a_return_form_fails_closed():
    """The whole point: an arbitrary 122-field row is NOT realigned."""
    row = _wellformed_1120s()
    row[_I["Type"]] = ""
    row[_I["Paid"]] = " 0 "
    shape = normalize_row(HEADER, _shorten(row, drop_at=_I["Misc4"]))

    assert shape.status == UNRECOGNISED_SHORT_ROW
    assert shape.needs_review is True
    assert "not a recognised return form" in shape.detail


def test_a_short_row_whose_type_slot_is_already_populated_fails_closed():
    """Nothing is displaced if the ``Type`` slot already holds a value, so nothing is restored."""
    row = _wellformed_1120s()
    row[_I["First Came In - Date"]] = "01012022"
    shape = normalize_row(HEADER, _shorten(row, drop_at=_I["Misc4"]))

    assert shape.status == UNRECOGNISED_SHORT_ROW
    assert "not blank" in shape.detail


def test_a_row_short_by_more_than_one_field_fails_closed():
    row = _malformed_1120s()[:-1]
    shape = normalize_row(HEADER, row)

    assert shape.status == UNRECOGNISED_SHORT_ROW
    assert "single-omission shape" in shape.detail


def test_a_row_longer_than_the_header_fails_closed():
    shape = normalize_row(HEADER, [*_wellformed_1120s(), "surplus"])

    assert shape.status == UNRECOGNISED_SHORT_ROW


def test_a_short_row_with_no_empty_field_to_restore_into_fails_closed():
    header = ("a", "b", "Paid", "Type", "tail")
    shape = normalize_row(header, ["x", "y", "1120S", ""])

    assert shape.status == UNRECOGNISED_SHORT_ROW
    assert "no empty field" in shape.detail


def test_a_header_without_the_paid_type_pair_fails_closed():
    header = ("a", "b", "c", "d")
    shape = normalize_row(header, ["", "", ""])

    assert shape.status == UNRECOGNISED_SHORT_ROW
    assert "no 'Paid'/'Type' pair" in shape.detail


def test_a_header_where_type_does_not_follow_paid_fails_closed():
    header = ("Paid", "elsewhere", "Type", "tail")
    shape = normalize_row(header, ["1120S", "", ""])

    assert shape.status == UNRECOGNISED_SHORT_ROW
    assert "does not immediately follow" in shape.detail


def test_an_unrecognised_short_row_keeps_its_original_values():
    """Refusing to normalize must not also mangle the row."""
    row = _wellformed_1120s()
    row[_I["Type"]] = ""
    row[_I["Paid"]] = "not-a-form"
    short = _shorten(row, drop_at=_I["Misc4"])
    shape = normalize_row(HEADER, short)

    assert list(shape.values) == short


def test_to_mapping_matches_dictreader_for_short_and_surplus_rows():
    """The mapping a normalized row travels through is the one DictReader would have produced."""
    import csv
    import io

    for values in (_wellformed_1120s(), _malformed_1120s(), [*_wellformed_1120s(), "surplus"]):
        text = io.StringIO()
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerow(values)
        text.seek(0)

        expected = next(iter(csv.DictReader(text)))
        assert to_mapping(HEADER, values) == dict(expected)
