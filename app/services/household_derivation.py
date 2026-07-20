"""Household auto-derivation engine (Sprint 2 — built to the policy boundary).

Groups un-householded people into households using an **injected policy** — a callable
``person -> grouping key`` (return ``None`` to skip). The mechanism is complete and tested; the
*grouping rule* and whether to auto-apply are **business decisions**, so nothing is derived by
default:

- :func:`no_derivation` is the default policy and groups nothing.
- :func:`group_by_normalized_address` is a *candidate* policy, provided but **not** enabled. Using
  address as the household signal (and auto-apply vs. review) requires firm approval.

``derive_households`` defaults to ``dry_run=True`` so a policy can be evaluated (counts only)
without writing. Only groups of two or more people form a household.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from sqlalchemy import insert, select

from app.db import engine, household_relationships, households, people


@dataclass
class DerivationReport:
    inspected: int = 0
    groups: int = 0
    households_created: int = 0
    members_assigned: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def no_derivation(person) -> str | None:
    """Default policy: never groups. Household derivation stays off until the firm approves a rule."""
    return None


def group_by_normalized_address(person) -> str | None:
    """CANDIDATE policy (not the default — awaiting a business decision). Groups people who share a
    normalized mailing address (line 1 + postal code). The firm must approve address as the
    household signal, and auto-apply vs. review, before this is used."""
    line1 = (person.get("address_line_1") or "").strip().lower()
    postal = (person.get("postal_code") or "").strip().lower()
    key = "|".join(part for part in (line1, postal) if part)
    return key or None


def _household_name(members) -> str:
    last_names = {(m.get("last_name") or "").strip() for m in members}
    last_names.discard("")
    if len(last_names) == 1:
        return f"{next(iter(last_names))} Household"
    lead = members[0].get("full_name") or f"Person {members[0]['id']}"
    return f"{lead} Household"


def derive_households(policy=no_derivation, *, dry_run: bool = True, conn=None) -> DerivationReport:
    """Group un-householded active people (``household_id IS NULL``) into households using
    ``policy``. Returns a :class:`DerivationReport`. With ``dry_run`` (default) nothing is written —
    the report shows what *would* happen, so a candidate policy can be evaluated safely."""

    def _do(c) -> DerivationReport:
        report = DerivationReport()
        rows = c.execute(
            select(people).where(people.c.household_id.is_(None), people.c.active.is_(True))
        ).mappings().all()
        report.inspected = len(rows)

        groups: dict[str, list] = defaultdict(list)
        for row in rows:
            key = policy(row)
            if key:
                groups[key].append(row)

        for members in groups.values():
            if len(members) < 2:
                continue
            report.groups += 1
            if dry_run:
                report.members_assigned += len(members)
                continue
            household_id = c.execute(
                households.insert().values(name=_household_name(members)).returning(households.c.id)
            ).scalar_one()
            report.households_created += 1
            for member in members:
                c.execute(people.update().where(people.c.id == member["id"]).values(
                    household_id=household_id))
                c.execute(insert(household_relationships).values(
                    household_id=household_id, person_id=member["id"], relationship_type="member"))
                report.members_assigned += 1
        return report

    if conn is not None:
        return _do(conn)
    with engine.begin() as connection:
        return _do(connection)
