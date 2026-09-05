"""Plan the backfill of recorded trust onto legacy Drake ``person_source_links``.

Phase 1 added the trust columns and deliberately back-filled nothing: every one of the 11,124 existing
links carries ``trust_level = NULL``, which reads as ``unknown_legacy``. This module decides which of
them can be given a RECORDED trust level from evidence that is already in the database, and — more
importantly — which cannot.

WHAT THE EVIDENCE ACTUALLY SUPPORTS (measured against production, read-only)

    3,607 Drake links, all ``confirmed = true``, none unconfirmed:

        identifier_verified      533   identifier-keyed method AND the hash is on a real return
        machine_exact_name     1,404   a NAME match; ``people`` holds 117 duplicate name-groups
        machine_contact          894   an email/phone match — identifies a mailbox, not a taxpayer
        canonical_repair         543   asserts provenance was matched, never records on what
        unknown_legacy           154
        machine_name_location     79
        human_approved             0

    Zero. Not "few" — ZERO. ``drake_identity_match_candidates`` holds 435 rows, every one still
    ``pending``, with no ``reviewed_by_user_id`` and no ``reviewed_at`` on any of them;
    ``match_review_decisions`` is empty; no event stream records a link confirmation. The identity
    review UI exists and has never been used. So ``confirmed = true`` cannot mean human approval on
    this data, and this module never reads it as such.

WHAT IS ELIGIBLE, AND WHY IT IS ONLY THIS

Two classes, both requiring evidence that is CHECKED at apply time rather than asserted:

  ``identifier_verified`` — the link's ``match_method`` names an identifier-keyed method AND the
      source contact's identifier hash actually appears as a taxpayer or spouse hash on a Drake
      return. Either half alone proves nothing: the method string says how the PERSON was chosen, the
      hash presence says the CONTACT came from Drake. Only together do they establish that this
      person was bound to this return's identifier.

  ``human_approved`` — an ``approved`` row in ``drake_identity_match_candidates`` for exactly this
      (identifier hash, person) pair, carrying both a reviewer and a timestamp. Today this yields
      nothing, and that is the correct result rather than a gap to be papered over.

Everything else is INELIGIBLE. ``canonical_repair`` is not promoted on the strength of its name — the
database does not record what evidence those repairs relied on, so the honest classification is the
one that says so. Name and contact matches stay candidates for review; they never become recorded
trust here.

NON-DESTRUCTIVE BY CONSTRUCTION

A link that already carries a recorded ``trust_level`` is never re-planned, whatever it says. This
backfill only ever moves a row from "nothing recorded" to "recorded", so re-running it cannot restate
an earlier decision, and a human correction made after the fact survives it.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import text

from app.services.link_trust import (
    HUMAN_APPROVED,
    IDENTIFIER_VERIFIED,
    SOURCE_HUMAN,
    SOURCE_MACHINE,
    derive_legacy_trust,
)

SOURCE_SYSTEM = "Drake"

#: The only trust levels this backfill will ever write. Deliberately the same set the resolver treats
#: as sufficient for tax-return visibility — writing anything weaker would put rows in the table that
#: look decided but grant nothing.
BACKFILLABLE = frozenset({IDENTIFIER_VERIFIED, HUMAN_APPROVED})

INELIGIBLE_ALREADY_RECORDED = "already_recorded"
INELIGIBLE_NO_IDENTIFIER_HASH = "no_identifier_hash_on_contact"
INELIGIBLE_HASH_NOT_ON_RETURN = "identifier_hash_absent_from_all_returns"
INELIGIBLE_WEAK_EVIDENCE = "evidence_below_recorded_trust"

def _links_sql(*, trust_column_present: bool):
    """Deployment-order tolerance, so a DRY RUN can classify an environment psl02 has not reached.

    Production is still at ``mcp01``: the trust columns do not exist there, which would make a plain
    ``SELECT l.trust_level`` fail and leave the pre-apply dry run — the one check that proves the tool
    agrees with the audited census — impossible to run where it matters most. Where the column is
    absent, every row is substituted with NULL, which is not a guess: "no trust has been recorded" is
    exactly what a database without the column means. Nothing else about the classification changes,
    and the APPLY path still writes only through columns that must exist.
    """
    trust = "l.trust_level" if trust_column_present else "NULL::text"
    return text(f"""
        SELECT l.id                              AS link_id,
               l.person_id                       AS person_id,
               l.source_contact_id               AS source_contact_id,
               l.match_method                    AS match_method,
               l.confirmed                       AS confirmed,
               {trust}                           AS trust_level,
               sc.raw_data->>'identifier_hash'   AS identifier_hash
        FROM person_source_links l
        JOIN source_contacts sc ON sc.id = l.source_contact_id
        JOIN people p           ON p.id = l.person_id
        WHERE sc.source_system = :source_system
        ORDER BY l.id
    """)


def _column_present(conn, table: str, column: str) -> bool:
    return conn.execute(text("""
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = :t AND column_name = :c
    """), {"t": table, "c": column}).scalar() is not None

#: Both sides matter: a spouse is bound to a joint return through their OWN identifier, so a hash that
#: only ever appears as ``spouse_identifier_hash`` is still genuine Drake identity evidence.
_RETURN_HASHES_SQL = text("""
    SELECT DISTINCT taxpayer_identifier_hash AS identifier_hash, 'taxpayer' AS side
      FROM drake_client_returns WHERE taxpayer_identifier_hash IS NOT NULL
    UNION
    SELECT DISTINCT spouse_identifier_hash, 'spouse'
      FROM drake_client_returns WHERE spouse_identifier_hash IS NOT NULL
""")

_APPROVALS_SQL = text("""
    SELECT identifier_hash, person_id, reviewed_by_user_id, reviewed_at
    FROM drake_identity_match_candidates
    WHERE status = 'approved'
      AND reviewed_by_user_id IS NOT NULL
      AND reviewed_at IS NOT NULL
""")


def _return_hash_sides(conn) -> dict[str, set[str]]:
    sides: dict[str, set[str]] = defaultdict(set)
    for identifier_hash, side in conn.execute(_RETURN_HASHES_SQL):
        sides[identifier_hash].add(side)
    return sides


def _proven_approvals(conn) -> dict[tuple[str, int], dict[str, Any]]:
    """Approvals that name BOTH a reviewer and a time. An unattributed approval is not one."""
    return {(r["identifier_hash"], r["person_id"]): dict(r)
            for r in conn.execute(_APPROVALS_SQL).mappings()}


def classify_link(row, *, return_hash_sides, approvals) -> dict[str, Any]:
    """Decide one link's fate. Pure given its two evidence indexes, so it is directly testable.

    Returns ``eligible`` plus the values that would be written, or the reason it is refused.
    """
    identifier_hash = row.get("identifier_hash")
    person_id = row.get("person_id")

    if (row.get("trust_level") or "").strip():
        return {"eligible": False, "reason": INELIGIBLE_ALREADY_RECORDED,
                "proposed_trust_level": None}

    approval = approvals.get((identifier_hash, person_id)) if identifier_hash else None
    if approval:
        return {
            "eligible": True,
            "proposed_trust_level": HUMAN_APPROVED,
            "proposed_confirmation_source": SOURCE_HUMAN,
            "confirmed_by_user_id": approval["reviewed_by_user_id"],
            "confirmed_at": approval["reviewed_at"],
            "reason": "approved in Drake identity review with reviewer and timestamp",
        }

    if derive_legacy_trust(row.get("match_method")) != IDENTIFIER_VERIFIED:
        return {"eligible": False, "reason": INELIGIBLE_WEAK_EVIDENCE,
                "proposed_trust_level": None}

    if not identifier_hash:
        return {"eligible": False, "reason": INELIGIBLE_NO_IDENTIFIER_HASH,
                "proposed_trust_level": None}

    sides = return_hash_sides.get(identifier_hash)
    if not sides:
        # The method claims the identifier, but no Drake return carries that hash. Unprovable, so it
        # stays unrecorded rather than being written on the strength of a method name alone.
        return {"eligible": False, "reason": INELIGIBLE_HASH_NOT_ON_RETURN,
                "proposed_trust_level": None}

    return {
        "eligible": True,
        "proposed_trust_level": IDENTIFIER_VERIFIED,
        "proposed_confirmation_source": SOURCE_MACHINE,
        "confirmed_by_user_id": None,
        "confirmed_at": None,
        "reason": f"identifier-keyed method; hash present on return ({'/'.join(sorted(sides))})",
    }


def build_plan(conn) -> dict[str, Any]:
    """The full backfill plan: eligible rows to write, plus every refusal and why.

    Refusals are returned rather than dropped. A plan that shows only what it will do hides the far
    more important number — how much of the linkage cannot be justified at all.
    """
    return_hash_sides = _return_hash_sides(conn)
    approvals = _proven_approvals(conn)
    trust_present = _column_present(conn, "person_source_links", "trust_level")
    links_sql = _links_sql(trust_column_present=trust_present)

    planned, refused = [], []
    for row in conn.execute(links_sql, {"source_system": SOURCE_SYSTEM}).mappings():
        verdict = classify_link(row, return_hash_sides=return_hash_sides, approvals=approvals)
        record = {
            "person_source_link_id": row["link_id"],
            "person_id": row["person_id"],
            "source_contact_id": row["source_contact_id"],
            "match_method": row["match_method"] or "",
            "confirmed": row["confirmed"],
            "reason": verdict["reason"],
        }
        if verdict["eligible"]:
            planned.append({
                **record,
                "trust_level": verdict["proposed_trust_level"],
                "confirmation_source": verdict["proposed_confirmation_source"],
                "evidence_method": row["match_method"] or "",
                "confirmed_by_user_id": verdict["confirmed_by_user_id"],
                "confirmed_at": verdict["confirmed_at"],
            })
        else:
            refused.append(record)

    census: dict[str, int] = defaultdict(int)
    for entry in planned:
        census[entry["trust_level"]] += 1
    refusal_census: dict[str, int] = defaultdict(int)
    for entry in refused:
        refusal_census[entry["reason"]] += 1

    return {
        "planned": planned,
        "refused": refused,
        "census": dict(census),
        "refusal_census": dict(refusal_census),
        "planned_rows": len(planned),
        "refused_rows": len(refused),
        # False means this environment predates psl02, so the plan is a DRY-RUN classification only —
        # the apply path cannot write here and will fail loudly on the missing columns if tried.
        "trust_columns_present": trust_present,
    }


_APPLY_SQL = text("""
    UPDATE person_source_links
       SET trust_level         = :trust_level,
           confirmation_source = :confirmation_source,
           evidence_method     = :evidence_method,
           confirmed_by_user_id = :confirmed_by_user_id,
           confirmed_at        = :confirmed_at
     WHERE id = :person_source_link_id
       AND trust_level IS NULL
""")


def apply_planned_row(conn, entry) -> bool:
    """Write ONE planned row. Returns whether it was written.

    The ``trust_level IS NULL`` predicate is inside the UPDATE, not checked beforehand, so a row that
    gained a recorded trust between planning and applying is skipped by the database itself rather
    than by a racing read.
    """
    if entry["trust_level"] not in BACKFILLABLE:
        raise ValueError(f"refusing to write trust_level {entry['trust_level']!r}")
    result = conn.execute(_APPLY_SQL, {
        "trust_level": entry["trust_level"],
        "confirmation_source": entry["confirmation_source"],
        "evidence_method": entry["evidence_method"],
        "confirmed_by_user_id": entry["confirmed_by_user_id"],
        "confirmed_at": entry["confirmed_at"],
        "person_source_link_id": entry["person_source_link_id"],
    })
    return result.rowcount == 1


def current_state(conn, link_ids) -> dict[int, dict[str, Any]]:
    """The trust columns as they stand now, for the rollback snapshot and for drift detection."""
    ids = [int(i) for i in link_ids]
    if not ids:
        return {}
    rows = conn.execute(text("""
        SELECT id, trust_level, confirmation_source, evidence_method,
               confirmed_by_user_id, confirmed_at
        FROM person_source_links WHERE id = ANY(:ids)
    """), {"ids": ids}).mappings()
    return {r["id"]: dict(r) for r in rows}
