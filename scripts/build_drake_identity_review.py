"""Build the Drake identity review queue: candidates for a HUMAN to confirm, never a decision.

Scoring semantics are NOT defined here. They come from ``app.services.drake_linkage_evidence``, the
same evaluator the automatic linker uses, because the two had drifted:

* this script pooled ``taxpayer_name`` and ``spouse_name`` into a single set, so a person carrying
  the SPOUSE's name scored an exact-name hit against a TAXPAYER identity;
* it paid 40/35 points for a return-level ``Email`` / ``TP_*`` phone regardless of whose they were,
  which is how a joint return's household mailbox came to identify a taxpayer;
* it ordered ties by the LOWEST ``person_id``, turning a coin-flip into an identity decision.

Candidates are now produced per ROLE, and their score is the evaluator's evidence-derived confidence.
An AUTO_LINK-grade candidate is still only written here as a candidate: this script proposes, the
review queue disposes.
"""
from __future__ import annotations

import json

from dotenv import load_dotenv
from sqlalchemy import MetaData, select, text

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine  # noqa: E402
from app.services.drake_linkage_evidence import (  # noqa: E402
    AMBIGUOUS,
    NO_MATCH,
    ROLES,
    TAXPAYER,
    Roster,
    RosterPerson,
    build_identity_evidence,
    clean,
    evaluate,
    join_name,
)
from app.services.drake_return_subject import Observation as SubjectObservation  # noqa: E402

metadata = MetaData()
metadata.reflect(bind=engine)

people = metadata.tables["people"]
drake_identity = metadata.tables["drake_identity"]
source_contacts = metadata.tables["source_contacts"]


def build_roster(person_rows):
    """Canonical people only. Drake's own contacts are excluded so a link cannot vouch for itself."""
    roster = []
    for row in person_rows:
        roster.append(RosterPerson(
            person_id=row["id"],
            full_name=clean(row.get("full_name"))
            or join_name(row.get("first_name"), row.get("last_name")),
            dob=row.get("birth_date"),
            emails=frozenset({(clean(row.get("normalized_email")) or "").lower()} - {""}),
            phones=frozenset({clean(row.get("normalized_phone"))} - {None}),
            city=clean(row.get("city")),
            state=clean(row.get("state")),
        ))
    return Roster(roster)


def candidates_for_identity(identity, evidence, roster):
    """Every role-correct candidate for one identity, with the evaluator's own confidence.

    An identity holds a taxpayer name, a spouse name, or (for 64 of the 1,802) both, when the same
    person filed in different roles across years. Each role is evaluated SEPARATELY and the role is
    recorded on the candidate, so the queue can never again present a spouse as a taxpayer match.
    """
    proposals = {}
    for role in ROLES:
        role_name = identity.get("taxpayer_name") if role == TAXPAYER else identity.get("spouse_name")
        if not clean(role_name):
            continue

        decision = evaluate(build_identity_evidence(
            identity["identifier_hash"], role,
            taxpayer_name=identity.get("taxpayer_name"),
            spouse_name=identity.get("spouse_name"),
            emails=evidence["emails"], phones=evidence["phones"],
            city=next(iter(evidence["cities"]), None),
            state=next(iter(evidence["states"]), None),
            has_spouse=bool(clean(identity.get("spouse_name"))),
            return_observations=evidence.get("return_observations"),
        ), roster)

        if decision.outcome in (NO_MATCH, AMBIGUOUS):
            # Ambiguity is an ANSWER, not a gap to be filled with a best guess.
            continue

        for person_id in decision.candidates:
            reasons = {"role": role, "outcome": decision.outcome,
                       "trust_level": decision.trust_level, "method": decision.method,
                       "evidence": list(decision.reasons)}
            existing = proposals.get(person_id)
            if existing is None or decision.confidence > existing["score"]:
                proposals[person_id] = {"person_id": person_id, "score": decision.confidence,
                                        "reasons": reasons}
    return list(proposals.values())



with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS drake_identity_match_candidates (
            id BIGSERIAL PRIMARY KEY,
            identifier_hash TEXT NOT NULL,
            person_id BIGINT NOT NULL,
            score INTEGER NOT NULL,
            reasons JSONB NOT NULL,
            rank INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_at TIMESTAMPTZ,
            reviewed_by_user_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (identifier_hash, person_id)
        )
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS
        ix_drake_identity_match_candidates_status
        ON drake_identity_match_candidates (status, score DESC)
    """))

    unresolved = conn.execute(
        select(drake_identity).where(
            drake_identity.c.primary_person_id.is_(None)
        )
    ).mappings().all()

    person_rows = conn.execute(select(people)).mappings().all()
    roster = build_roster(person_rows)

    evidence_rows = conn.execute(text("""
        SELECT
            raw_data->>'identifier_hash' AS identifier_hash,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT lower(email)), NULL) AS emails,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT normalized_phone), NULL) AS phones,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT lower(city)), NULL) AS cities,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT lower(state)), NULL) AS states,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT raw_data->>'return_type'), NULL) AS return_types,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT (raw_data->>'tax_year')::integer), NULL) AS tax_years
        FROM source_contacts
        WHERE source_system = 'Drake'
          AND raw_data->>'identifier_hash' IS NOT NULL
        GROUP BY raw_data->>'identifier_hash'
    """)).mappings().all()

    evidence_by_hash = {
        row["identifier_hash"]: {
            "emails": list(row["emails"] or []),
            "phones": list(row["phones"] or []),
            "cities": list(row["cities"] or []),
            "states": list(row["states"] or []),
            # D7 Phase B: the returns behind this identifier, so the evaluator can refuse to
            # propose a person for a business, estate or trust identifier.
            "return_observations": [
                SubjectObservation(return_type=t, tax_year=y)
                for t in (row["return_types"] or [])
                for y in (row["tax_years"] or [None])
            ],
        }
        for row in evidence_rows
    }

    conn.execute(text("""
        DELETE FROM drake_identity_match_candidates
        WHERE status = 'pending'
    """))

    candidate_count = 0
    identities_with_candidates = 0

    for identity in unresolved:
        evidence = evidence_by_hash.get(
            identity["identifier_hash"],
            {"emails": [], "phones": [], "cities": [], "states": [],
             "return_observations": []},
        )

        candidates = candidates_for_identity(identity, evidence, roster)

        # Ordered by evidence strength only. The previous ``-person_id`` tie-break turned a genuine
        # tie into a decision; equal-scoring candidates now stay equal and are all shown, so the
        # reviewer sees the ambiguity instead of inheriting an arbitrary winner.
        candidates.sort(key=lambda candidate: candidate["score"], reverse=True)

        top_candidates = candidates[:5]

        if top_candidates:
            identities_with_candidates += 1

        for rank, candidate in enumerate(
            top_candidates,
            start=1,
        ):
            conn.execute(text("""
                INSERT INTO drake_identity_match_candidates (
                    identifier_hash,
                    person_id,
                    score,
                    reasons,
                    rank,
                    status,
                    updated_at
                )
                VALUES (
                    :identifier_hash,
                    :person_id,
                    :score,
                    CAST(:reasons AS JSONB),
                    :rank,
                    'pending',
                    now()
                )
                ON CONFLICT (identifier_hash, person_id)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    reasons = EXCLUDED.reasons,
                    rank = EXCLUDED.rank,
                    status = 'pending',
                    updated_at = now()
            """), {
                "identifier_hash": identity["identifier_hash"],
                "person_id": candidate["person_id"],
                "score": candidate["score"],
                "reasons": json.dumps(candidate["reasons"]),
                "rank": rank,
            })

            candidate_count += 1

    summary = conn.execute(text("""
        SELECT
            COUNT(DISTINCT identifier_hash) AS identities_in_queue,
            COUNT(*) AS candidate_rows,
            COUNT(*) FILTER (WHERE score >= 95) AS candidates_95_plus,
            COUNT(*) FILTER (WHERE score >= 80) AS candidates_80_plus
        FROM drake_identity_match_candidates
        WHERE status = 'pending'
    """)).mappings().one()

print()
print("DRAKE IDENTITY REVIEW QUEUE BUILT")
print("=" * 50)
print(f"Unresolved identities scanned:       {len(unresolved):,}")
print(f"Identities with candidates:          {identities_with_candidates:,}")
print(f"Candidate rows created:              {candidate_count:,}")
print(f"Candidates scoring 95+:              {summary['candidates_95_plus']:,}")
print(f"Candidates scoring 80+:              {summary['candidates_80_plus']:,}")
print()
print("No identities were automatically linked.")
print("This only built the grouped review queue.")
