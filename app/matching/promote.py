"""Canonical-person promotion — the identity-pipeline "apply" step for single-source imports.

Background (Task 1B): the ``plan_matches`` matcher deduplicates contacts that appear in **two
or more** source systems, so a single-source import (e.g. Wealthbox alone) yields no groups and
no imported contact ever becomes a canonical Person. This step fills that gap **without changing
the matcher**: it promotes unlinked ``source_contacts`` into ``people`` using the current schema
and the existing ``person_source_links`` contract.

Per unlinked contact:
- **Link to an existing person** when exactly one existing ``people`` row matches by exact
  normalized email or phone (this also absorbs a later same-person import from another system).
- **Create a new person** when the contact matches no existing person and does not collide (by
  normalized email or phone) with another unlinked contact.
- **Leave unpromoted (ambiguous)** when >1 existing person matches, or another unlinked contact
  shares the normalized email/phone — these are exactly the cases that belong in Match Review, so
  the step never risks a false merge.

Read-mostly and idempotent: links use ``on_conflict_do_nothing`` on the
``(person_id, source_contact_id)`` unique constraint, so re-running promotes nothing already
linked. Content-free reporting (counts only).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db import engine, people, person_source_links, source_contacts


@dataclass
class PromotionReport:
    inspected: int = 0
    created: int = 0
    linked_existing: int = 0
    ambiguous: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _create_person(conn, record) -> int:
    values = {
        "first_name": record["first_name"],
        "middle_name": record["middle_name"],
        "last_name": record["last_name"],
        "full_name": record["full_name"],
        "primary_email": record["email"],
        "normalized_email": record["normalized_email"],
        "primary_phone": record["phone"],
        "normalized_phone": record["normalized_phone"],
        "address_line_1": record["address_line_1"],
        "address_line_2": record["address_line_2"],
        "city": record["city"],
        "state": record["state"],
        "postal_code": record["postal_code"],
        "active": True,
    }
    return conn.execute(people.insert().values(**values).returning(people.c.id)).scalar_one()


def _link(conn, person_id, source_contact_id, method, score):
    conn.execute(
        insert(person_source_links)
        .values(
            person_id=person_id,
            source_contact_id=source_contact_id,
            match_method=method,
            match_score=score,
            confirmed=True,
        )
        .on_conflict_do_nothing(constraint="uq_person_source_link")
    )


def _candidate_people(conn, normalized_email, normalized_phone):
    conditions = []
    if normalized_email:
        conditions.append(people.c.normalized_email == normalized_email)
    if normalized_phone:
        conditions.append(people.c.normalized_phone == normalized_phone)
    if not conditions:
        return []
    return conn.execute(select(people.c.id).where(or_(*conditions)).distinct()).scalars().all()


def list_ambiguous_unlinked(*, source_system: str | None = None, conn=None) -> list[dict]:
    """Unlinked source_contacts that :func:`promote_unlinked` deliberately leaves for a human:
    more than one candidate person (an exact email/phone match to several people), or a normalized
    email/phone shared with another unlinked contact. Each is returned with its candidate people so
    the Match Review "unresolved contacts" queue can offer link-to-existing / create-new. Content
    is staff-facing (names/emails), unlike the content-free promotion report."""

    def _do(c) -> list[dict]:
        already_linked = select(person_source_links.c.source_contact_id)
        query = select(source_contacts).where(source_contacts.c.id.notin_(already_linked))
        if source_system:
            query = query.where(source_contacts.c.source_system == source_system)
        rows = c.execute(query.order_by(source_contacts.c.full_name)).mappings().all()
        email_counts = Counter(r["normalized_email"] for r in rows if r["normalized_email"])
        phone_counts = Counter(r["normalized_phone"] for r in rows if r["normalized_phone"])

        results: list[dict] = []
        for record in rows:
            ne, np = record["normalized_email"], record["normalized_phone"]
            candidate_ids = _candidate_people(c, ne, np)
            shared = (ne and email_counts[ne] > 1) or (np and phone_counts[np] > 1)
            if len(candidate_ids) > 1 or (len(candidate_ids) == 0 and shared):
                candidates = []
                if candidate_ids:
                    candidates = [dict(p) for p in c.execute(
                        select(people.c.id, people.c.full_name, people.c.primary_email,
                               people.c.primary_phone).where(people.c.id.in_(candidate_ids))
                    ).mappings().all()]
                results.append({
                    "id": record["id"], "full_name": record["full_name"],
                    "source_system": record["source_system"], "email": record["email"],
                    "phone": record["phone"],
                    "reason": "multiple_candidates" if candidate_ids else "shared_contact_info",
                    "candidates": candidates,
                })
        return results

    if conn is not None:
        return _do(conn)
    with engine.connect() as connection:
        return _do(connection)


def resolve_link_to_person(source_contact_id: int, person_id: int, *, conn=None) -> None:
    """Human resolution: link an unresolved contact to an existing person (Match Review)."""
    def _do(c):
        _link(c, person_id, source_contact_id, "manual_review", 100)

    if conn is not None:
        return _do(conn)
    with engine.begin() as connection:
        return _do(connection)


def resolve_create_person(source_contact_id: int, *, conn=None) -> int:
    """Human resolution: create a new canonical person from an unresolved contact and link it."""
    def _do(c):
        record = c.execute(
            select(source_contacts).where(source_contacts.c.id == source_contact_id)
        ).mappings().one()
        person_id = _create_person(c, record)
        _link(c, person_id, source_contact_id, "manual_review", 100)
        return person_id

    if conn is not None:
        return _do(conn)
    with engine.begin() as connection:
        return _do(connection)


def promote_unlinked(*, source_system: str | None = None, conn=None) -> PromotionReport:
    """Promote unlinked ``source_contacts`` into canonical people. Optionally scope to one
    ``source_system``. Returns a content-free :class:`PromotionReport`."""

    def _do(c) -> PromotionReport:
        report = PromotionReport()
        already_linked = select(person_source_links.c.source_contact_id)
        query = select(source_contacts).where(source_contacts.c.id.notin_(already_linked))
        if source_system:
            query = query.where(source_contacts.c.source_system == source_system)
        rows = c.execute(query.order_by(source_contacts.c.id)).mappings().all()
        report.inspected = len(rows)

        # collision detection among the unlinked set (shared normalized email/phone)
        email_counts = Counter(r["normalized_email"] for r in rows if r["normalized_email"])
        phone_counts = Counter(r["normalized_phone"] for r in rows if r["normalized_phone"])

        for record in rows:
            ne, np = record["normalized_email"], record["normalized_phone"]
            candidates = _candidate_people(c, ne, np)
            if len(candidates) == 1:
                _link(c, candidates[0], record["id"], "auto_email_phone", 95)
                report.linked_existing += 1
            elif len(candidates) > 1:
                report.ambiguous += 1  # multiple existing people -> Match Review
            elif (ne and email_counts[ne] > 1) or (np and phone_counts[np] > 1):
                report.ambiguous += 1  # collides with another unlinked contact -> Match Review
            else:
                person_id = _create_person(c, record)
                _link(c, person_id, record["id"], "auto_promote", 100)
                report.created += 1
        return report

    if conn is not None:
        return _do(conn)
    with engine.begin() as connection:
        return _do(connection)
