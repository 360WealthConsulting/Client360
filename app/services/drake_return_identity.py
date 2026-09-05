"""Stable, content-derived identity for a Drake client return.

WHY THIS EXISTS
---------------
``drake_client_returns`` was upserted on ``(tax_year, source_row_number)``, where ``source_row_number``
is nothing but ``enumerate(csv.DictReader(...), start=1)`` — the row's POSITION in the export file. A
Drake re-export that inserts, deletes or re-sorts a single row shifts every row after it, so row *N*
becomes a DIFFERENT taxpayer and the upsert silently overwrites one client's return — AGI, filing
status, acknowledgements and both identifier hashes — with another's. Position is not identity.

WHAT DRAKE ACTUALLY GIVES US
----------------------------
The real exports under ``data/Drake/<year>/`` were inspected for a Drake-native client or return
identifier. ``CLIENT.CSV`` carries no such column in any year (2021-2025); its header is
``FS, TP_Social, TP_FirstName, …`` and the ONLY client key Drake exposes there is ``TP_Social`` — the
taxpayer's SSN/EIN. (``EF_DBF.CSV`` has a ``DCN``, but that identifies an e-file transmission, not the
client return row.) So the preferred evidence — a stable Drake-native identifier — does not exist, and
identity must be derived.

THE DERIVED IDENTITY
--------------------
``return_identity_key = SHA-256( tax_year | taxpayer_hash | spouse_hash | return_type | filing_status )``

Every input is ALREADY non-reversible or non-sensitive: the identifier hashes are the salted
``SHA-256(KEY : digits)`` values the import writes, never the SSN/EIN itself. This module therefore
needs no secret, and — importantly — the identical expression is computable in plain SQL, so the
migration can backfill without a key and without a raw identifier ever existing in the database.

Each component earns its place, measured against production (3,690 rows):

  ``tax_year``          a client files every year; the year separates those returns.
  ``taxpayer_hash``     Drake's own client key, in its non-reversible form.
  ``spouse_hash``       distinguishes a joint return from the same taxpayer's separate return, and is
                        what lets a spouse see a joint return through their OWN identifier.
  ``return_type``       one identifier can carry both a 1040 and a business return in one year;
                        6 taxpayer/year pairs in production do exactly that.
  ``filing_status``     REQUIRED, and not obvious. Preparers compute an MFJ and an MFS version of the
                        same couple's return to see which is better, producing two rows with the same
                        year, both hashes and ``1040``, differing only in ``FS`` (2 vs 3). Three such
                        pairs exist in production. Without ``filing_status`` they collide and one
                        silently overwrites the other.

FAIL CLOSED, NEVER COLLIDE
--------------------------
Two populations cannot be given a stable identity, and this module refuses to invent one for either.
They are QUARANTINED — assigned no identity key, marked with a status, and (by
``app.services.drake_return_resolution``) never resolved to a person:

  * ``unidentified_no_taxpayer_identifier`` — the row carries no usable taxpayer hash, so there is
    nothing to key on. 6 production rows, all 1041 estates/trusts filed without a ``TP_Social``
    (e.g. two different Stone estates in 2021). Keying them on "no identifier" would merge two
    unrelated estates into one return.
  * ``unidentified_ambiguous_collision`` — several rows in one export share a complete identity tuple.
    3 production rows (one taxpayer, 2021, three separate 1040s at FS=1 with different AGI — original
    plus amendments, or duplicate entry). The correct answer is not knowable from the export, so the
    import must not pick one. It flags all of them and imports none into an identity-keyed slot.

Measured against current production: 3,684 rows carry a taxpayer hash and yield 3,682 distinct keys;
3,681 rows are cleanly identified and 9 are quarantined (6 + 3 above).

``source_row_number`` is RETAINED as provenance — it says where in the export a row was found, which
is genuinely useful when reconciling against the source file — but it is never again an identity or
an upsert key.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

#: A salted SHA-256 as written by the Drake import. Anything else is malformed and fails closed.
_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")

IDENTIFIED = "identified"
NO_TAXPAYER_IDENTIFIER = "unidentified_no_taxpayer_identifier"
AMBIGUOUS_COLLISION = "unidentified_ambiguous_collision"

#: Every status that means "this row has no stable identity". Callers fail closed on these.
UNIDENTIFIED_STATUSES = frozenset({NO_TAXPAYER_IDENTIFIER, AMBIGUOUS_COLLISION})


def _clean(value: Any) -> str:
    """Normalize one component. Mirrors ``btrim``/``coalesce`` in the SQL backfill exactly."""
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def normalized_identifier(value: Any) -> str | None:
    """A Drake identifier hash, or ``None`` when it is absent or malformed.

    Fail-closed on shape: a truncated, upper-cased, whitespace-damaged or non-hex value is NOT
    silently accepted, because two different malformed values could otherwise normalize together.
    """
    cleaned = _clean(value).lower()
    if not cleaned or not _SHA256_HEX.match(cleaned):
        return None
    return cleaned


def identity_payload(
    tax_year: Any,
    taxpayer_identifier_hash: Any,
    spouse_identifier_hash: Any,
    return_type: Any,
    filing_status: Any,
) -> str | None:
    """The exact string that gets hashed, or ``None`` when no identity is possible.

    Kept separate from the hashing so a test can assert the composition itself, and so the SQL
    backfill has an unambiguous specification to mirror.
    """
    taxpayer = normalized_identifier(taxpayer_identifier_hash)
    if taxpayer is None:
        # No taxpayer identifier => nothing to key on. Never fall back to names or row position.
        return None

    year = _clean(tax_year)
    if not year.isdigit():
        return None

    spouse = normalized_identifier(spouse_identifier_hash) or ""

    return "|".join((
        str(int(year)),
        taxpayer,
        spouse,
        _clean(return_type).upper(),
        _clean(filing_status),
    ))


def compute_return_identity_key(
    tax_year: Any,
    taxpayer_identifier_hash: Any,
    spouse_identifier_hash: Any,
    return_type: Any,
    filing_status: Any,
) -> str | None:
    """Deterministic identity key for one Drake return row, or ``None`` if it cannot have one.

    No secret is involved: every input is already a hash or a non-sensitive field, which is what lets
    the migration reproduce this in SQL. A raw SSN/EIN is neither an input nor recoverable from the
    output.
    """
    payload = identity_payload(tax_year, taxpayer_identifier_hash, spouse_identifier_hash,
                              return_type, filing_status)
    if payload is None:
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assign_identities(rows) -> list[dict[str, Any]]:
    """Assign an identity + status to every row of ONE export batch, detecting collisions.

    Collision detection is necessarily batch-scoped: a duplicate tuple is only visible when the whole
    export is in hand. Returns one dict per input row, in input order, carrying ``return_identity_key``
    (``None`` when unidentified) and ``identity_status``.

    A row's outcome depends ONLY on its own content plus whether some OTHER row shares its exact
    identity tuple. It never depends on position, ordering, or how many unrelated rows the export
    holds — which is what makes reorder, insert and delete safe.
    """
    materialized = list(rows)
    keys: list[str | None] = []

    for row in materialized:
        keys.append(compute_return_identity_key(
            row.get("tax_year"),
            row.get("taxpayer_identifier_hash"),
            row.get("spouse_identifier_hash"),
            row.get("return_type"),
            row.get("filing_status"),
        ))

    occurrences: dict[str, int] = defaultdict(int)
    for key in keys:
        if key is not None:
            occurrences[key] += 1

    results = []
    for row, key in zip(materialized, keys, strict=True):
        if key is None:
            status, resolved_key = NO_TAXPAYER_IDENTIFIER, None
        elif occurrences[key] > 1:
            # Several rows claim one identity. Guessing which is "the" return would overwrite a real
            # return with another real return — precisely the failure this module exists to prevent.
            status, resolved_key = AMBIGUOUS_COLLISION, None
        else:
            status, resolved_key = IDENTIFIED, key
        results.append({
            **row,
            "return_identity_key": resolved_key,
            "identity_status": status,
        })
    return results


def is_identified(row) -> bool:
    """True only for a row that holds a stable identity. The single gate for callers."""
    return (row.get("identity_status") == IDENTIFIED
            and bool(row.get("return_identity_key")))
