"""Canonical row shape for the Drake ``CLIENT.CSV`` client export.

WHY THIS EXISTS
---------------
Both Drake client importers map values to columns with ``csv.DictReader``, which assigns values to
header names BY POSITION and silently tolerates a row that is short. The 2021 and 2022 exports emit
**one fewer field for every 1120S return**, so every value from the omission point onward lands one
column early. The value that belongs in ``Type`` lands in ``Paid``, and ``return_type`` — which the
importer reads from ``Type`` — is imported as NULL.

Measured against the real exports at authoring time:

===== ===========  ================  ================  ==========================
year   header cols  rows w/ 123       rows w/ 122       well-formed 1120S rows
===== ===========  ================  ================  ==========================
2021   123          691               **52**            **0**
2022   123          691               **57**            **0**
2023   123          855               0                 73
2024   123          735               0                 60
2025   123          609               0                 51
===== ===========  ================  ================  ==========================

52 + 57 = 109 — exactly the rows carrying ``return_type IS NULL`` in production. In 2021 and 2022
there is not one well-formed 1120S row: the short row IS the 1120S row shape in those two exports,
and Drake fixed it from 2023 onward. That is why every affected row recovers the same form.

WHAT ELSE THE SHIFT BREAKS
--------------------------
``return_type`` is the consequence that was noticed, not the whole defect. Every column the importer
reads from a header position at or after the omission received its neighbour's value — measured
across the 109 rows: ``agi`` wrong on 102, ``preparer_fee`` on 97, ``complete_date`` on 109, and the
six e-file columns on ~79 each. Columns before the omission — ``FS``, the taxpayer name fields,
``TP_DoB``, ``Prep``, and crucially ``TP_Social`` and ``SP_Social`` — are untouched, which is why
every affected row still carries the correct identifier hashes.

Repairing one column would leave the rest wrong while making the row look repaired. Normalizing the
RAW ROW before it is mapped to the header fixes all of them at once, from one rule.

THE RULE, AND WHY IT IS STRUCTURAL
----------------------------------
:func:`normalize_row` restores the single missing structural slot. It never repairs a column
individually and never consults a name, a year, or a taxpayer. A row is normalized only when ALL of
these hold:

1. it is short by exactly one field (``len(values) == len(header) - 1``);
2. the header carries ``Paid`` immediately followed by ``Type`` — the adjacency the shift exploits;
3. the value that landed in ``Paid`` is a RECOGNISED Drake return form;
4. the value that landed in ``Type`` is blank;
5. there is at least one empty field before the displaced block to restore into.

Anything else is left exactly as it is and reported — see FAIL CLOSED below.

WHERE THE MISSING FIELD GOES
----------------------------
The omitted field cannot be located exactly, and this module says so rather than pretending. In the
production rows the omission lies somewhere in header band 36-49 (``Receipt`` .. ``Misc5``): every
column in 37-48 is empty on all 109 rows, so an omitted empty field leaves no trace of itself.

That ambiguity is harmless, because **every choice inside a run of empty fields produces the identical
canonical row**. The rule therefore scans left from ``Paid`` over the displaced values, then over the
empty run behind them, and restores the slot at the START of that run. Inserting at any other index in
the same run yields a byte-identical result.

FAIL CLOSED
-----------
A short row that does not match the proven shape is NOT guessed at, NOT shifted, and NOT dropped. It
is mapped exactly as it is today and returned with :data:`UNRECOGNISED_SHORT_ROW` so the caller can
report it for review. Silently normalizing an arbitrary 122-field row would be the same class of
mistake as the defect being fixed.
"""
from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: The column whose value the shift displaces into :data:`PAID_COLUMN`.
TYPE_COLUMN = "Type"

#: The column immediately before ``Type``; on a short row it holds ``Type``'s value.
PAID_COLUMN = "Paid"

#: Return forms Drake emits in the ``Type`` column. A displaced token must be one of these before any
#: row is normalized — an unrecognised token means the shape is not the proven one.
RECOGNISED_RETURN_FORMS = frozenset({
    "1040", "1040NR", "1041", "1065", "1120", "1120S", "990",
})

#: Row shapes :func:`normalize_row` can report.
UNCHANGED = "unchanged"
NORMALIZED = "normalized_short_row"
UNRECOGNISED_SHORT_ROW = "unrecognised_short_row"

#: The Drake ``CLIENT.CSV`` header, identical in every export 2021-2025 (123 columns, all names
#: distinct, the last one unnamed). Schema metadata only — it carries no client data. The importers
#: read the header from the file they are importing; this constant exists so that the coordinated
#: migration can reconstruct a stored row's ORIGINAL value order from ``raw_data``, whose JSONB keys
#: are sorted and therefore lose it, and so tests can build real-shaped fixtures without the export.
CLIENT_EXPORT_HEADER: tuple[str, ...] = (
    "FS", "TP_Social", "TP_FirstName",
    "TP_LastName", "TP_DoB", "TP_DepAnother",
    "SP_Social", "SP_FirstName", "SP_LastName",
    "SP_DoB", "SP_DepAnother", "Address",
    "City", "State", "Zip",
    "Res_State", "Res_City", "County",
    "School_Dist", "TP_Day_Phone", "TP_Eve_Phone",
    "TP_Cell_Phone", "Email", "TP_Occupation",
    "TP_Blind", "TP_Pres", "TP_Deceased",
    "SP_Occupation", "SP_Blind", "SP_Pres",
    "SP_Deceased", "CareOf", "Firm",
    "Prep", "DE", "ERO",
    "Receipt", "Fee", "Est_Tax",
    "St_Est_Tax", "Over_Pmt", "St_Over_Pmt",
    "2210_Code", "Ly_Fed_Tax", "Ly_St_Tax",
    "Misc1", "Misc2", "Misc3",
    "Misc4", "Misc5", "AGI",
    "Prep_Fee", "Wh_Ral", "Paid",
    "Type", "First Came In - Date", "First Came In - Time",
    "First Came In - Preparer", "Interview - Date", "Interview - Time",
    "Interview - Preparer", "Interview - Minutes", "Prepare - Date",
    "Prepare - Time", "Prepare - Preparer", "Prepare - Minutes",
    "Review - Date", "Review - Time", "Review - Preparer",
    "Review - Minutes", "Approved - Date", "Approved - Time",
    "Approved - Preparer", "Approved - Minutes", "Copy - Date",
    "Copy - Time", "Copy - Preparer", "Copy - Minutes",
    "Contact - Date", "Contact - Time", "Contact - Preparer",
    "Pickup - Date", "Pickup - Time", "Pickup - Preparer",
    "Promised - Date", "Promised - Time", "Complete - Date",
    "ADMN-Prep1", "ADMN-Min1", "ADMN-Prep2",
    "ADMN-Min2", "ADMN-Prep3", "ADMN-Min3",
    "ADMN-Prep4", "ADMN-Min4", "ADMN-Prep5",
    "ADMN-Min5", "e-File Product #1", "e-File 1st Tran Date #1",
    "e-File Last Tran Date #1", "e-File ACK Date #1", "e-File ACK Code #1",
    "e-File Product #2", "e-File 1st Tran Date #2", "e-File Last Tran Date #2",
    "e-File ACK Date #2", "e-File ACK Code #2", "e-File Product #3",
    "e-File 1st Tran Date #3", "e-File Last Tran Date #3", "e-File ACK Date #3",
    "e-File ACK Code #3", "e-File Product #4", "e-File 1st Tran Date #4",
    "e-File Last Tran Date #4", "e-File ACK Date #4", "e-File ACK Code #4",
    "e-File Product #5", "e-File 1st Tran Date #5", "e-File Last Tran Date #5",
    "e-File ACK Date #5", "e-File ACK Code #5", "",
)


#: Drake's date formats, most specific first. ``%m%d%Y`` is what the export actually uses.
_DATE_FORMATS = ("%m%d%Y", "%Y-%m-%d", "%m/%d/%Y")


def clean_value(value: Any) -> str:
    """Strip NULs and surrounding whitespace, as the Drake importers always have."""
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def parse_date_value(value: Any):
    """Drake's date text as a ``date``, or ``None`` when it is blank or unparseable.

    Shared with the coordinated re-key migration so that a repaired row and a re-imported row derive
    identical values — a difference here would show up as a spurious diff on the next import.
    """
    value = clean_value(value)

    if not value:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def parse_decimal_value(value: Any):
    """Drake's money text as a float, or ``None``. Shared for the same reason as :func:`parse_date_value`."""
    value = clean_value(value).replace(",", "").replace("$", "")

    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class RowShape:
    """What :func:`normalize_row` did to one raw row, and why.

    ``values`` is always usable: unchanged for a well-formed row, restored for a normalized one, and
    the original values for a short row the rule refuses to touch.
    """

    status: str
    values: tuple[str, ...]
    detail: str = ""
    recovered_return_type: str | None = None

    @property
    def normalized(self) -> bool:
        return self.status == NORMALIZED

    @property
    def needs_review(self) -> bool:
        return self.status == UNRECOGNISED_SHORT_ROW


def _blank(value: Any) -> bool:
    return not str(value or "").strip()


def normalize_row(header, values) -> RowShape:
    """Restore the one missing structural slot in a known-malformed short row.

    Pure and database-free, so the rule can be exercised against real export shapes in tests. Returns
    the row unchanged unless every condition in the module docstring holds.
    """
    header = list(header)
    values = [("" if value is None else str(value)) for value in values]

    if len(values) == len(header):
        return RowShape(UNCHANGED, tuple(values))

    if len(values) != len(header) - 1:
        return RowShape(
            UNRECOGNISED_SHORT_ROW, tuple(values),
            f"field count {len(values)} against a {len(header)}-column header: "
            "not the single-omission shape",
        )

    if PAID_COLUMN not in header or TYPE_COLUMN not in header:
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        f"header carries no {PAID_COLUMN!r}/{TYPE_COLUMN!r} pair")

    paid_index = header.index(PAID_COLUMN)
    type_index = header.index(TYPE_COLUMN)

    if type_index != paid_index + 1:
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        f"{TYPE_COLUMN!r} does not immediately follow {PAID_COLUMN!r}")

    if type_index >= len(values):
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        "row ends before the Paid/Type pair")

    displaced = values[paid_index].strip().upper()

    if displaced not in RECOGNISED_RETURN_FORMS:
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        f"value in {PAID_COLUMN!r} is not a recognised return form")

    if not _blank(values[type_index]):
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        f"{TYPE_COLUMN!r} is not blank, so nothing is displaced")

    # Scan left over the displaced values, then over the empty run behind them. The omitted field sits
    # somewhere in that run; every position in it is empty, so restoring at the start of the run and
    # restoring anywhere else inside it produce the identical row.
    scan = paid_index - 1
    while scan >= 0 and not _blank(values[scan]):
        scan -= 1

    if scan < 0:
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        "no empty field before the displaced form token to restore into")

    run_end = scan
    run_start = scan
    while run_start > 0 and _blank(values[run_start - 1]):
        run_start -= 1

    restored = list(values)
    restored.insert(run_start, "")

    # Post-conditions. If any fails the rule did not do what it claims, so the row is left alone.
    if len(restored) != len(header):
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values), "restored row is the wrong length")

    if restored[type_index].strip().upper() != displaced:
        return RowShape(UNRECOGNISED_SHORT_ROW, tuple(values),
                        "restoring did not move the form token into Type")

    return RowShape(
        NORMALIZED, tuple(restored),
        f"restored one empty field into the empty run at header indices "
        f"{run_start}-{run_end}; every position in that run is equivalent",
        recovered_return_type=displaced,
    )


def to_mapping(header, values) -> dict[str | None, Any]:
    """Map values to header names exactly as ``csv.DictReader`` would.

    Reproduced rather than reused so that a normalized row and an untouched row travel the same path:
    missing trailing values become ``None`` (DictReader's ``restval``) and surplus values collect
    under the ``None`` key (its ``restkey``).
    """
    header = list(header)
    values = list(values)
    mapping: dict[str | None, Any] = dict(zip(header, values, strict=False))

    if len(values) < len(header):
        for name in header[len(values):]:
            mapping[name] = None
    elif len(values) > len(header):
        mapping[None] = values[len(header):]

    return mapping


@dataclass(frozen=True)
class ClientRow:
    """One export row: its position, its header mapping, and what normalization did to it."""

    row_number: int
    mapping: dict[str | None, Any]
    shape: RowShape


def iter_client_rows(handle) -> Iterator[ClientRow]:
    """Yield every data row of a Drake ``CLIENT.CSV``, normalized where the proven shape applies.

    The header is read from the file being imported, never assumed — :data:`CLIENT_EXPORT_HEADER` is
    for the migration and for tests. Normalization happens on the RAW ROW, before any mapping to
    column names, which is what lets one rule correct every displaced field at once.
    """
    reader = csv.reader(handle)
    header = next(reader, None)

    if header is None:
        return

    for row_number, values in enumerate(reader, start=1):
        shape = normalize_row(header, values)
        yield ClientRow(row_number, to_mapping(header, shape.values), shape)


def normalized_name(first: Any, last: Any) -> str:
    return " ".join(part.lower() for part in (clean_value(first), clean_value(last)) if part)


def parse_client_row(row, *, tax_year, source_row_number, source_updated_at, identifier_hash) -> dict:
    """One Drake ``CLIENT.CSV`` record -> the column values for ``drake_client_returns``.

    Pure, so the identity and shift rules can be exercised against real Drake column names without a
    database or the hashing secret — ``identifier_hash`` is supplied by the caller, which is what
    keeps ``MICROSOFT_TOKEN_KEY`` in the import script and out of this module and its tests.
    """
    return {
        "tax_year": tax_year,
        "source_row_number": source_row_number,
        "taxpayer_identifier_hash": identifier_hash(row.get("TP_Social")),
        "spouse_identifier_hash": identifier_hash(row.get("SP_Social")),
        "taxpayer_first_name": row.get("TP_FirstName") or None,
        "taxpayer_last_name": row.get("TP_LastName") or None,
        "taxpayer_normalized_name": normalized_name(row.get("TP_FirstName"), row.get("TP_LastName")),
        "taxpayer_dob": parse_date_value(row.get("TP_DoB")),
        "spouse_first_name": row.get("SP_FirstName") or None,
        "spouse_last_name": row.get("SP_LastName") or None,
        "spouse_normalized_name": normalized_name(row.get("SP_FirstName"), row.get("SP_LastName")),
        "spouse_dob": parse_date_value(row.get("SP_DoB")),
        "filing_status": row.get("FS") or None,
        "return_type": row.get("Type") or None,
        "preparer_code": row.get("Prep") or None,
        "agi": parse_decimal_value(row.get("AGI")),
        "preparer_fee": parse_decimal_value(row.get("Prep_Fee")),
        "prepare_date": parse_date_value(row.get("Prepare - Date")),
        "review_date": parse_date_value(row.get("Review - Date")),
        "approved_date": parse_date_value(row.get("Approved - Date")),
        "complete_date": parse_date_value(row.get("Complete - Date")),
        "federal_product": row.get("e-File Product #1") or None,
        "federal_ack_date": parse_date_value(row.get("e-File ACK Date #1")),
        "federal_ack_code": row.get("e-File ACK Code #1") or None,
        "state_product": row.get("e-File Product #2") or None,
        "state_ack_date": parse_date_value(row.get("e-File ACK Date #2")),
        "state_ack_code": row.get("e-File ACK Code #2") or None,
        "source_updated_at": source_updated_at,
        "raw_data": json.dumps(row, ensure_ascii=False),
    }


def read_client_rows(tax_year, client_file, *, identifier_hash, source_updated_at=None):
    """Parse one year's export. The WHOLE file is materialized on purpose.

    Identity collisions are only visible with the complete export in hand, so the batch — not the
    row — is the unit of import.

    Returns ``(rows, anomalies)``. An anomaly is a short row whose shape is NOT the proven one: it is
    parsed exactly as it always was and reported, never realigned on a guess.
    """
    if source_updated_at is None:
        source_updated_at = datetime.fromtimestamp(client_file.stat().st_mtime, tz=UTC)

    rows = []
    anomalies = []

    with client_file.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for client_row in iter_client_rows(handle):
            if client_row.shape.needs_review:
                anomalies.append({
                    "tax_year": tax_year,
                    "source_row_number": client_row.row_number,
                    "detail": client_row.shape.detail,
                })

            rows.append(parse_client_row(
                {clean_value(key): clean_value(value)
                 for key, value in client_row.mapping.items() if key is not None},
                tax_year=tax_year,
                source_row_number=client_row.row_number,
                source_updated_at=source_updated_at,
                identifier_hash=identifier_hash,
            ))

    return rows, anomalies
