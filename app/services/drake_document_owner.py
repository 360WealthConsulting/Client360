"""Read-only Drake-specific document owner resolution.

Uses existing Drake taxpayer/spouse identifier hashes derived from SSNs.

This module proposes owners only. It never creates people, households,
source links, ownership assignments, OCR jobs, or file moves.
"""

from __future__ import annotations

import re
from typing import Any

_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")

# Historical identity conflicts intentionally frozen.
# These documents must remain manual-review HOLD even if future
# Drake imports make a seemingly deterministic candidate available.
FROZEN_DRAKE_DOCUMENT_IDS = frozenset({
    121627,
    121628,
})


def _norm(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        str(value or "").lower(),
    ).strip()


def _tokens(value: Any) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(str(value or ""))
        if len(token) >= 2
    }


def document_year(filename: str | None) -> int | None:
    match = _YEAR_RE.search(str(filename or ""))
    return int(match.group(1)) if match else None


def _token_present(
    name_token: str | None,
    document_tokens: set[str],
) -> bool:
    token = _norm(name_token)

    if not token:
        return True

    if token in document_tokens:
        return True

    # Drake filenames sometimes truncate names:
    # Teresa -> TERE, Jennie -> JENNI, etc.
    if len(token) >= 3:
        for document_token in document_tokens:
            if len(document_token) < 3:
                continue

            if (
                token.startswith(document_token)
                or document_token.startswith(token)
            ):
                return True

    return False


def is_personal_return_type(value: Any) -> bool:
    """Return True only for Drake individual-income-tax return types.

    The SSN-based person/household resolver must fail closed for business,
    estate, trust, partnership, and corporate returns.
    """

    normalized = _norm(value).replace(" ", "")

    if not normalized:
        return False

    return (
        normalized.startswith("1040")
        or normalized in {
            "individual",
            "individualreturn",
        }
    )


def candidate_return_matches_filename(
    filename: str,
    row: dict[str, Any],
) -> bool:
    """Conservatively bind a personal Drake return to its filename."""

    document_tokens = _tokens(filename)

    taxpayer_first = row.get("taxpayer_first_name")
    taxpayer_last = row.get("taxpayer_last_name")

    spouse_first = row.get("spouse_first_name")
    spouse_last = (
        row.get("spouse_last_name")
        or taxpayer_last
    )

    if not (taxpayer_first or taxpayer_last):
        return False

    if (
        taxpayer_first
        and not _token_present(
            taxpayer_first,
            document_tokens,
        )
    ):
        return False

    if (
        taxpayer_last
        and not _token_present(
            taxpayer_last,
            document_tokens,
        )
    ):
        return False

    if spouse_first:
        if not _token_present(
            spouse_first,
            document_tokens,
        ):
            return False

        if (
            spouse_last
            and not _token_present(
                spouse_last,
                document_tokens,
            )
        ):
            return False

    return True


def classify_resolved_return(
    row: dict[str, Any],
    identity_by_hash: dict[str, dict[str, Any]],
    person_by_id: dict[int, dict[str, Any]],
    household_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Pure ownership classification from Drake SSN-derived identities."""

    taxpayer_hash = row.get(
        "taxpayer_identifier_hash"
    )

    spouse_hash = row.get(
        "spouse_identifier_hash"
    )

    taxpayer_identity = (
        identity_by_hash.get(taxpayer_hash)
        if taxpayer_hash
        else None
    )

    spouse_identity = (
        identity_by_hash.get(spouse_hash)
        if spouse_hash
        else None
    )

    taxpayer_person_id = (
        taxpayer_identity or {}
    ).get("primary_person_id")

    spouse_person_id = (
        spouse_identity or {}
    ).get("primary_person_id")

    taxpayer_person = (
        person_by_id.get(taxpayer_person_id)
        if taxpayer_person_id
        else None
    )

    spouse_person = (
        person_by_id.get(spouse_person_id)
        if spouse_person_id
        else None
    )

    # Joint return.
    if spouse_hash:
        if (
            not taxpayer_person_id
            or not spouse_person_id
        ):
            return {
                "status": "hold",
                "reason": (
                    "joint_identity_not_fully_canonical"
                ),
                "taxpayer_person_id": taxpayer_person_id,
                "spouse_person_id": spouse_person_id,
            }

        taxpayer_household = (
            taxpayer_person or {}
        ).get("household_id")

        spouse_household = (
            spouse_person or {}
        ).get("household_id")

        if (
            taxpayer_household
            and spouse_household
            and taxpayer_household
            == spouse_household
        ):
            household = household_by_id.get(
                taxpayer_household,
                {},
            )

            return {
                "status": "resolved",
                "entity_type": "household",
                "entity_id": taxpayer_household,
                "entity_name": household.get("name"),
                "taxpayer_person_id": taxpayer_person_id,
                "spouse_person_id": spouse_person_id,
                "reason": (
                    "drake_ssn_hash_joint_household"
                ),
            }

        if (
            taxpayer_household
            or spouse_household
        ):
            return {
                "status": "hold",
                "reason": (
                    "joint_people_household_conflict"
                ),
                "taxpayer_person_id": taxpayer_person_id,
                "spouse_person_id": spouse_person_id,
            }

        # Resolver proposes only. Household creation belongs
        # to guarded remediation/write workflow.
        return {
            "status": "hold",
            "reason": "joint_people_need_household",
            "taxpayer_person_id": taxpayer_person_id,
            "spouse_person_id": spouse_person_id,
        }

    # Single taxpayer.
    if taxpayer_person_id:
        taxpayer_household = (
            taxpayer_person or {}
        ).get("household_id")

        if taxpayer_household:
            household = household_by_id.get(
                taxpayer_household,
                {},
            )

            return {
                "status": "resolved",
                "entity_type": "household",
                "entity_id": taxpayer_household,
                "entity_name": household.get("name"),
                "taxpayer_person_id": taxpayer_person_id,
                "reason": (
                    "drake_ssn_hash_taxpayer_household"
                ),
            }

        return {
            "status": "resolved",
            "entity_type": "person",
            "entity_id": taxpayer_person_id,
            "entity_name": (
                taxpayer_person or {}
            ).get("full_name"),
            "taxpayer_person_id": taxpayer_person_id,
            "reason": (
                "drake_ssn_hash_taxpayer_person"
            ),
        }

    return {
        "status": "hold",
        "reason": "taxpayer_identity_not_canonical",
    }


def propose_drake_document_owner(
    document_id: int,
    *,
    conn,
) -> dict[str, Any] | None:
    """Return deterministic Drake proposal, HOLD, or None.

    None means the document is not Drake-sourced and the
    generic proposal engine may proceed.

    HOLD means Drake structured identity evidence applies,
    but cannot be safely assigned. Generic scoring must not
    override the stronger Drake identity evidence.
    """

    if document_id in FROZEN_DRAKE_DOCUMENT_IDS:
        return {
            "proposed_entity_type": None,
            "proposed_entity_id": None,
            "proposed_entity_name": None,
            "confidence": "HOLD",
            "evidence": [
                (
                    "Drake document is explicitly frozen "
                    "because of a historical identity conflict"
                )
            ],
            "competing": [],
            "drake_resolution": "frozen_identity_conflict",
        }

    from sqlalchemy import select

    from app.db import (
        documents,
        households,
        metadata,
        people,
    )

    document_sources = metadata.tables[
        "document_sources"
    ]

    drake_client_returns = metadata.tables[
        "drake_client_returns"
    ]

    drake_identity = metadata.tables[
        "drake_identity"
    ]

    document = conn.execute(
        select(
            documents.c.id,
            documents.c.original_name,
            documents.c.person_id,
            documents.c.household_id,
            documents.c.organization_id,
        ).where(
            documents.c.id == document_id
        )
    ).mappings().first()

    if document is None:
        return None

    if any(
        (
            document["person_id"] is not None,
            document["household_id"] is not None,
            document["organization_id"] is not None,
        )
    ):
        return None

    source = conn.execute(
        select(
            document_sources.c.id,
            document_sources.c.source_external_id,
        )
        .where(
            document_sources.c.document_id
            == document_id
        )
        .where(
            document_sources.c.source_system
            == "Drake"
        )
        .limit(1)
    ).mappings().first()

    if source is None:
        return None

    year = document_year(
        document["original_name"]
    )

    if year is None:
        return {
            "proposed_entity_type": None,
            "proposed_entity_id": None,
            "proposed_entity_name": None,
            "confidence": "HOLD",
            "evidence": [
                (
                    "Drake source detected but tax year "
                    "could not be determined"
                )
            ],
            "competing": [],
            "drake_resolution": "year_missing",
        }

    return_rows = [
        dict(row)
        for row in conn.execute(
            select(
                drake_client_returns.c.id,
                drake_client_returns.c.tax_year,
                drake_client_returns.c.taxpayer_identifier_hash,
                drake_client_returns.c.spouse_identifier_hash,
                drake_client_returns.c.taxpayer_first_name,
                drake_client_returns.c.taxpayer_last_name,
                drake_client_returns.c.spouse_first_name,
                drake_client_returns.c.spouse_last_name,
                drake_client_returns.c.filing_status,
                drake_client_returns.c.return_type,
            ).where(
                drake_client_returns.c.tax_year
                == year
            )
        ).mappings()
    ]

    personal_rows = [
        row
        for row in return_rows
        if is_personal_return_type(
            row.get("return_type")
        )
    ]

    matched_returns = [
        row
        for row in personal_rows
        if candidate_return_matches_filename(
            document["original_name"],
            row,
        )
    ]

    if len(matched_returns) != 1:
        return {
            "proposed_entity_type": None,
            "proposed_entity_id": None,
            "proposed_entity_name": None,
            "confidence": "HOLD",
            "evidence": [
                (
                    "Drake structured identity binding "
                    f"found {len(matched_returns)} "
                    f"candidate returns for {year}"
                )
            ],
            "competing": [],
            "drake_resolution": (
                "return_binding_not_unique"
            ),
        }

    matched_return = matched_returns[0]

    hashes = {
        value
        for value in (
            matched_return.get(
                "taxpayer_identifier_hash"
            ),
            matched_return.get(
                "spouse_identifier_hash"
            ),
        )
        if value
    }

    identity_rows = {}

    if hashes:
        identity_rows = {
            row["identifier_hash"]: dict(row)
            for row in conn.execute(
                select(
                    drake_identity.c.identifier_hash,
                    drake_identity.c.primary_person_id,
                    drake_identity.c.confidence,
                ).where(
                    drake_identity.c.identifier_hash.in_(
                        hashes
                    )
                )
            ).mappings()
        }

    person_ids = {
        row.get("primary_person_id")
        for row in identity_rows.values()
        if row.get("primary_person_id")
    }

    person_rows = {}

    if person_ids:
        person_rows = {
            row["id"]: dict(row)
            for row in conn.execute(
                select(
                    people.c.id,
                    people.c.full_name,
                    people.c.household_id,
                    people.c.active,
                ).where(
                    people.c.id.in_(person_ids)
                )
            ).mappings()
        }

    household_ids = {
        row.get("household_id")
        for row in person_rows.values()
        if row.get("household_id")
    }

    household_rows = {}

    if household_ids:
        household_rows = {
            row["id"]: dict(row)
            for row in conn.execute(
                select(
                    households.c.id,
                    households.c.name,
                ).where(
                    households.c.id.in_(
                        household_ids
                    )
                )
            ).mappings()
        }

    classified = classify_resolved_return(
        matched_return,
        identity_rows,
        person_rows,
        household_rows,
    )

    if classified["status"] == "resolved":
        return {
            "proposed_entity_type": (
                classified["entity_type"]
            ),
            "proposed_entity_id": (
                classified["entity_id"]
            ),
            "proposed_entity_name": (
                classified.get("entity_name")
            ),
            "confidence": "HIGH",
            "evidence": [
                (
                    "Drake taxpayer/spouse identity "
                    "resolved from protected "
                    "SSN-derived identifier hash"
                ),
                f"Drake tax year {year}",
                (
                    "resolution="
                    f"{classified['reason']}"
                ),
            ],
            "competing": [],
            "drake_resolution": (
                classified["reason"]
            ),
            "drake_return_id": (
                matched_return["id"]
            ),
        }

    return {
        "proposed_entity_type": None,
        "proposed_entity_id": None,
        "proposed_entity_name": None,
        "confidence": "HOLD",
        "evidence": [
            (
                "Drake SSN-derived identity evidence "
                "exists but is not safely assignable"
            ),
            (
                "resolution="
                f"{classified['reason']}"
            ),
        ],
        "competing": [],
        "drake_resolution": (
            classified["reason"]
        ),
        "drake_return_id": (
            matched_return["id"]
        ),
    }
