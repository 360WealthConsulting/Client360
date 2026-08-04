from __future__ import annotations

import json
import re
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import MetaData, select, text

load_dotenv(r"C:\Client360\app\.env")

from app.db import engine

metadata = MetaData()
metadata.reflect(bind=engine)

people = metadata.tables["people"]
drake_identity = metadata.tables["drake_identity"]
source_contacts = metadata.tables["source_contacts"]


def clean(value):
    if value is None:
        return None
    value = str(value).replace("\x00", "").strip()
    return value or None


def normalize_name(value):
    value = clean(value)
    if not value:
        return None
    value = re.sub(r"[^a-z0-9 ]+", "", value.lower())
    return re.sub(r"\s+", " ", value).strip() or None


def normalize_email(value):
    value = clean(value)
    return value.lower() if value else None


def normalize_phone(value):
    digits = "".join(
        character
        for character in (clean(value) or "")
        if character.isdigit()
    )
    if len(digits) > 10:
        digits = digits[-10:]
    return digits or None


def score_candidate(identity, evidence, person):
    score = 0
    reasons = []

    identity_names = {
        normalize_name(identity.get("taxpayer_name")),
        normalize_name(identity.get("spouse_name")),
    }
    identity_names.discard(None)

    person_name = normalize_name(person.get("full_name"))

    if person_name and person_name in identity_names:
        score += 55
        reasons.append("exact_name")

    if evidence["emails"] and person.get("normalized_email"):
        if normalize_email(person["normalized_email"]) in evidence["emails"]:
            score += 40
            reasons.append("exact_email")

    if evidence["phones"] and person.get("normalized_phone"):
        if normalize_phone(person["normalized_phone"]) in evidence["phones"]:
            score += 35
            reasons.append("exact_phone")

    if evidence["cities"] and clean(person.get("city")):
        if person["city"].lower() in evidence["cities"]:
            score += 10
            reasons.append("same_city")

    if evidence["states"] and clean(person.get("state")):
        if person["state"].lower() in evidence["states"]:
            score += 5
            reasons.append("same_state")

    return score, reasons


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

    evidence_rows = conn.execute(text("""
        SELECT
            raw_data->>'identifier_hash' AS identifier_hash,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT lower(email)), NULL) AS emails,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT normalized_phone), NULL) AS phones,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT lower(city)), NULL) AS cities,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT lower(state)), NULL) AS states
        FROM source_contacts
        WHERE source_system = 'Drake'
          AND raw_data->>'identifier_hash' IS NOT NULL
        GROUP BY raw_data->>'identifier_hash'
    """)).mappings().all()

    evidence_by_hash = {
        row["identifier_hash"]: {
            "emails": set(row["emails"] or []),
            "phones": set(row["phones"] or []),
            "cities": set(row["cities"] or []),
            "states": set(row["states"] or []),
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
            {
                "emails": set(),
                "phones": set(),
                "cities": set(),
                "states": set(),
            },
        )

        candidates = []

        for person in person_rows:
            score, reasons = score_candidate(
                identity,
                evidence,
                person,
            )

            if score < 55:
                continue

            candidates.append({
                "person_id": person["id"],
                "score": score,
                "reasons": reasons,
            })

        candidates.sort(
            key=lambda candidate: (
                candidate["score"],
                -candidate["person_id"],
            ),
            reverse=True,
        )

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
