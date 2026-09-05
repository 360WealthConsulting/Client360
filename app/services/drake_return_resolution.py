"""Drake return -> Client360 person resolution, gated on RECORDED TRUST and STABLE IDENTITY.

This is the parked design from ``c6ec6e1`` (``fix/drake-return-identity-hash``) adapted to the two
safety foundations added in this phase. That commit is NOT merged; this module supersedes it and the
two staff read surfaces are NOT yet wired to it — wiring is the merge step, deliberately deferred.

WHAT THE PARKED DESIGN GOT RIGHT, AND KEEPS
-------------------------------------------
Both staff surfaces that show Drake returns joined ``drake_client_returns`` to ``people`` on
``lower(trim(first_name)) + lower(trim(last_name))`` at request time. Replayed against production that
puts **817 of 3,690 returns on two or more unrelated people simultaneously** (738 on two, 42 on three,
33 on four, 4 on five), because ``people`` holds 117 duplicate name-groups, the largest with 42
records — so opening either surface could disclose another client's AGI, filing status and
acknowledgements. Resolution through the identifier hash, with every rule failing closed, is the right
answer and is preserved here in full.

WHAT IT GOT WRONG, AND WHAT THIS FIXES
--------------------------------------
The parked resolver's trust anchor is ``person_source_links.confirmed``. Its commit message describes
the result as resolving "by identifier hash, never by name". **That claim does not survive contact
with the data.** ``confirmed`` is set to ``TRUE`` unconditionally by automated code
(``app/matching/promote.py::_link``; ``scripts/link_drake_to_people.py``), all 3,607 Drake links carry
it, and 41.1% of them rest on name-derived evidence — 1,404 ``unique_exact_name`` and 79
``exact_name_city_state``. Reading ``confirmed`` moves the name match from request time to import
time; it does not remove it. This module therefore does NOT claim to exclude name-derived trust by
construction. It makes the choice EXPLICIT, at the call site, and says what each policy actually costs.

THE RULES — every one fails closed
----------------------------------
  * A return reaches a person only through a hash the Drake row itself carries. Names, phone, email,
    city/state, filenames and document ownership are never consulted HERE. (Whether the underlying
    link was itself established by a name is exactly what ``policy`` governs.)
  * A hash resolves only when EXACTLY ONE trusted person holds it. Two or more and the return is
    withheld from everyone rather than tie-broken.
  * A hash with no trusted person stays unresolved. There is no name fallback of any kind.
  * A return with no STABLE IDENTITY never resolves. A row that is quarantined by
    ``drake_return_identity`` — no taxpayer identifier, or a tuple claimed by several rows — is a row
    whose very identity is in question, so it may not put figures in front of staff.
  * A spouse sees a joint return because ``spouse_identifier_hash`` is their own identifier hash, not
    because a name matched.
  * Business, fiduciary and exempt returns (1120 / 1120S / 1065 / 1041 / 990, and any unrecognised or
    blank type) never reach a natural person. ``is_personal_return_type`` — already used by the
    document owner resolver — remains the single gate. Production holds 35 EIN hashes carrying a
    confirmed person link, which this refuses to honour.

MEASURED COST OF EACH POLICY (current production, read-only replay)
-------------------------------------------------------------------
    STRICT (recorded trust only)          0 of 3,690 returns resolve
    LEGACY_DERIVED (identifier + human)   646 returns resolve
    the parked commit's own behaviour   2,797 returns resolve, 41.1% of the linkage name-derived

STRICT resolves nothing today because nothing has been back-filled — no production row carries a
recorded ``trust_level``, by design (see ``migrations/versions/psl02_link_trust_provenance.py``).
That is not a bug in this module; it is the honest starting position, and it is why ``policy`` has no
default. The caller must choose, in code, and the choice is visible in review.

Read-only. Nothing here writes, and the 3,690 ``drake_client_returns`` staging rows are untouched.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from app.services.drake_document_owner import is_personal_return_type
from app.services.drake_return_identity import IDENTIFIED
from app.services.link_trust import is_trusted_for_tax_return_visibility

#: Only links whose trust was RECORDED deliberately may grant visibility. Resolves nothing until
#: trust is back-filled or newly written — the safe destination, and the correct default to build for.
POLICY_STRICT = "strict"

#: Additionally honour a trust level DERIVED from a legacy ``match_method``, but only where that
#: derivation lands on ``identifier_verified`` or ``human_approved``. Name, name+location and contact
#: matches are still refused. This is the migration path, not the destination.
POLICY_LEGACY_DERIVED = "legacy_derived"

POLICIES = (POLICY_STRICT, POLICY_LEGACY_DERIVED)

_REQUIRED_TABLES = ("public.drake_client_returns", "public.source_contacts",
                    "public.person_source_links")

#: Columns every caller renders. Explicit so a schema addition cannot silently widen a read surface.
_RETURN_COLUMNS = """
            d.id,
            d.tax_year,
            d.return_type,
            d.agi,
            d.preparer_code,
            d.filing_status,
            d.prepare_date,
            d.review_date,
            d.approved_date,
            d.complete_date,
            d.federal_product,
            d.federal_ack_date,
            d.federal_ack_code,
            d.state_product,
            d.state_ack_date,
            d.state_ack_code,
            d.taxpayer_first_name,
            d.taxpayer_last_name,
            d.spouse_first_name,
            d.spouse_last_name,
            d.taxpayer_identifier_hash,
            d.spouse_identifier_hash,
            d.return_identity_key,
            d.identity_status
"""

#: Candidate links only. The trust DECISION is made in Python by ``app.services.link_trust`` so that
#: one module remains the single definition of what "trusted" means — duplicating the method lists in
#: SQL is exactly how two definitions drift apart.
_CANDIDATE_LINKS_SQL = text("""
    SELECT DISTINCT
           sc.raw_data->>'identifier_hash' AS identifier_hash,
           l.person_id                     AS person_id,
           l.match_method                  AS match_method,
           l.trust_level                   AS trust_level,
           l.confirmation_source           AS confirmation_source
    FROM person_source_links l
    JOIN source_contacts sc ON sc.id = l.source_contact_id
    JOIN people p           ON p.id = l.person_id
    WHERE sc.source_system = 'Drake'
      AND l.confirmed
      AND sc.raw_data->>'identifier_hash' IS NOT NULL
""")

_RETURNS_BY_HASH_SQL = text(f"""
    SELECT
{_RETURN_COLUMNS}
    FROM drake_client_returns d
    WHERE d.identity_status = :identified
      AND (d.taxpayer_identifier_hash = ANY(:hashes)
           OR d.spouse_identifier_hash = ANY(:hashes))
    ORDER BY d.tax_year DESC, d.id DESC
""")


def _relation_present(conn, relation: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:relation)"),
                        {"relation": relation}).scalar() is not None


def _column_present(conn, table: str, column: str) -> bool:
    return conn.execute(text("""
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = :t AND column_name = :c
    """), {"t": table, "c": column}).scalar() is not None


def _schema_present(conn) -> bool:
    """Deployment-order tolerance, extended to this phase's columns.

    The Drake tables, the identity columns and the trust columns are provisioned by three different
    migrations, and a release can reach an environment before any of them has run. Degrade to "no
    Drake returns" rather than break a workspace — and, unlike the previous implementation, treat a
    MISSING IDENTITY OR TRUST COLUMN as a reason to resolve nothing rather than as a reason to fall
    back to weaker rules. Falling back is how a safety gate quietly stops applying.
    """
    for relation in _REQUIRED_TABLES:
        if not _relation_present(conn, relation):
            return False
    if not _column_present(conn, "drake_client_returns", "identity_status"):
        return False
    return _column_present(conn, "person_source_links", "trust_level")


def trusted_hash_owners(conn, *, policy: str) -> tuple[dict[str, int], set[str]]:
    """``({hash: person_id} for singly-held hashes, {contested hashes})`` under ``policy``.

    Returned as two values on purpose: a caller auditing this path needs to see the hashes that were
    WITHHELD for ambiguity, not just the ones that resolved. A contested hash appears in neither the
    resolved map nor any read surface.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; expected one of {POLICIES}")

    accept_derived = policy == POLICY_LEGACY_DERIVED

    claimants: dict[str, set[int]] = defaultdict(set)
    for row in conn.execute(_CANDIDATE_LINKS_SQL).mappings():
        if is_trusted_for_tax_return_visibility(row, accept_derived_legacy=accept_derived):
            claimants[row["identifier_hash"]].add(row["person_id"])

    sole = {h: next(iter(people)) for h, people in claimants.items() if len(people) == 1}
    contested = {h for h, people in claimants.items() if len(people) > 1}
    return sole, contested


def resolved_drake_returns(conn, person_ids, *, policy: str = POLICY_STRICT) -> list[dict]:
    """Drake returns that deterministically belong to ``person_ids`` under ``policy``.

    Each row carries the Drake columns plus ``resolved_person_id`` and ``drake_role``
    (``taxpayer`` / ``spouse``) so a caller can show WHY a return is here. Anything ambiguous,
    unresolved, unidentified or non-personal is simply absent — callers must not second-guess that.

    ``policy`` defaults to :data:`POLICY_STRICT`, which today resolves nothing. That is deliberate:
    the failing-safe direction is the default, and honouring legacy name-derived linkage has to be
    asked for by name.
    """
    ids = {int(person_id) for person_id in person_ids if person_id}
    if not ids:
        return []

    if not _schema_present(conn):
        return []

    sole, _contested = trusted_hash_owners(conn, policy=policy)

    # Only the hashes held by the people we were asked about.
    wanted = {h: pid for h, pid in sole.items() if pid in ids}
    if not wanted:
        return []

    rows = conn.execute(_RETURNS_BY_HASH_SQL,
                        {"identified": IDENTIFIED, "hashes": list(wanted)}).mappings().all()

    results = []
    for row in rows:
        # Business/fiduciary/exempt returns are dropped HERE rather than in SQL so that
        # ``is_personal_return_type`` stays the one definition of what may reach a natural person.
        if not is_personal_return_type(row["return_type"]):
            continue

        # Which of the return's OWN hashes did the trusted person hold? A return can legitimately
        # match on both when one person holds both identifiers; taxpayer is reported in that case.
        taxpayer_owner = wanted.get(row["taxpayer_identifier_hash"])
        spouse_owner = wanted.get(row["spouse_identifier_hash"])

        for person_id, role in ((taxpayer_owner, "taxpayer"), (spouse_owner, "spouse")):
            if person_id is None:
                continue
            results.append({**dict(row), "resolved_person_id": person_id, "drake_role": role})
            break

    return results


def resolved_drake_returns_for_person(conn, person_id, *, policy: str = POLICY_STRICT) -> list[dict]:
    """Single-person convenience wrapper over :func:`resolved_drake_returns`."""
    return resolved_drake_returns(conn, [person_id], policy=policy)
