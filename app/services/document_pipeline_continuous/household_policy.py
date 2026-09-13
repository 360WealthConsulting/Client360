"""When may a folder owned by a PERSON be mapped to their HOUSEHOLD?

A TaxDome folder whose documents are filed partly to a person and partly to a household is
ambiguous on its face. It is usually one client recorded at two levels — and occasionally two
different clients sharing a folder, which is the case that must never be mapped automatically.

Telling those apart by structure alone is not possible, so Phase 1 does not try. It maps to the
household only when an independent authority says the two people are one tax household: Drake, which
records a married-filing-jointly return naming both of them. Every other shape becomes one aggregated
folder-level review, where a human answers it once.

THE SIX CONDITIONS, all required
--------------------------------
1. Exactly one active person owns documents in the folder.
2. That person belongs to exactly one active household.
3. Drake confirms married-filing-jointly between that person and the other household member.
4. The folder's existing owners are limited to that person, their spouse and that household.
5. No organization, unrelated owner or competing identity appears.
6. The household relationship is active and internally consistent.

WHY DRAKE CODE 2 AND NOT CODE 3
-------------------------------
``drake_client_returns.filing_status`` holds Drake's raw ``FS`` field, a numeric code. Code 2 carries
a spouse on 1,961 of 1,961 returns and code 3 on 82 of 82, so both are married — but 3 is married
filing SEPARATELY, which is precisely a couple who are not one filing unit. Only code 2 qualifies.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

#: Drake's ``FS`` code for married filing jointly. Code 3 (MFS) is deliberately excluded.
MFJ_FILING_STATUS = "2"


class PolicyResult:
    """The verdict, and the condition that decided it — so a review can say why it exists."""

    __slots__ = ("mapped", "entity_type", "entity_id", "condition", "detail")

    def __init__(self, mapped, *, entity_type=None, entity_id=None, condition, detail=""):
        self.mapped = mapped
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.condition = condition
        self.detail = detail

    def __repr__(self):                                    # pragma: no cover - diagnostics only
        return (f"PolicyResult(mapped={self.mapped}, entity={self.entity_type}:{self.entity_id}, "
                f"condition={self.condition!r})")

    @property
    def as_evidence(self) -> str:
        return f"household policy: {self.condition}" + (f" ({self.detail})" if self.detail else "")


def _active_people(conn, person_ids):
    if not person_ids:
        return []
    return conn.execute(text("""
        SELECT id, household_id, active FROM people WHERE id = ANY(:ids)
    """), {"ids": sorted(int(p) for p in person_ids)}).mappings().all()


def _household_members(conn, household_id):
    """Active members of a household, by the relationship table AND the denormalised column.

    Both are consulted because condition 6 is about internal consistency: if the two disagree about
    who is in the household, that is exactly the state a human should look at, not one an automated
    writer should pick a side in.
    """
    rel = {r["person_id"] for r in conn.execute(text("""
        SELECT hr.person_id FROM household_relationships hr
          JOIN people p ON p.id = hr.person_id
         WHERE hr.household_id = :h AND p.active
    """), {"h": int(household_id)}).mappings()}
    denorm = {r["id"] for r in conn.execute(text("""
        SELECT id FROM people WHERE household_id = :h AND active
    """), {"h": int(household_id)}).mappings()}
    return rel, denorm


def _drake_confirms_mfj(conn, person_id, spouse_person_id) -> tuple[bool, str]:
    """Does Drake hold a joint return naming both of these people?

    The join runs person -> drake_identity -> return -> spouse hash -> drake_identity -> person, so
    it is the identity table that decides who a hash belongs to, never a name comparison. A name
    match is how two different families with the same surname become one household by accident.
    """
    row = conn.execute(text("""
        SELECT count(*) AS n
          FROM drake_client_returns r
          JOIN drake_identity me     ON me.identifier_hash = r.taxpayer_identifier_hash
          JOIN drake_identity spouse ON spouse.identifier_hash = r.spouse_identifier_hash
         WHERE r.filing_status = :mfj
           AND (
                (me.primary_person_id = :a AND spouse.primary_person_id = :b)
             OR (me.primary_person_id = :b AND spouse.primary_person_id = :a)
           )
    """), {"mfj": MFJ_FILING_STATUS, "a": int(person_id), "b": int(spouse_person_id)}).mappings().first()
    n = int(row["n"]) if row else 0
    return (n > 0), (f"{n} joint return(s) name both" if n else "no joint return names both")


def household_mapping_allowed(conn, *, person_ids, household_ids, organization_ids) -> PolicyResult:
    """Apply the six conditions. Returns a mapped result only when every one of them holds."""
    persons = {int(p) for p in person_ids if p}
    households = {int(h) for h in household_ids if h}
    orgs = {int(o) for o in organization_ids if o}

    # 5 — no organization, and no competing household.
    if orgs:
        return PolicyResult(False, condition="organization_owner_present",
                            detail=f"{len(orgs)} organization owner(s)")
    if len(households) != 1:
        return PolicyResult(False, condition="not_exactly_one_household",
                            detail=f"{len(households)} households")
    household_id = next(iter(households))

    # 1 — exactly one active person.
    people = _active_people(conn, persons)
    inactive = [p["id"] for p in people if not p["active"]]
    if inactive:
        return PolicyResult(False, condition="inactive_person_owner",
                            detail=f"{len(inactive)} inactive person owner(s)")
    if len(people) != 1:
        return PolicyResult(False, condition="not_exactly_one_person",
                            detail=f"{len(people)} person owners")
    person = people[0]

    # 2 — that person belongs to exactly one active household, and it is this one.
    rel_members, denorm_members = _household_members(conn, household_id)
    if person["household_id"] != household_id or person["id"] not in rel_members:
        return PolicyResult(False, condition="person_not_in_this_household",
                            detail="owner is not a member of the folder's household")
    other_households = conn.execute(text("""
        SELECT count(DISTINCT hr.household_id) FROM household_relationships hr
         WHERE hr.person_id = :p
    """), {"p": person["id"]}).scalar()
    if int(other_households or 0) != 1:
        return PolicyResult(False, condition="person_in_multiple_households",
                            detail=f"{other_households} household memberships")

    # 6 — the household's two views of its own membership must agree.
    if rel_members != denorm_members:
        return PolicyResult(False, condition="household_membership_inconsistent",
                            detail="relationship table and people.household_id disagree")

    # 3 — Drake confirms MFJ with the OTHER member. A household of one cannot be joint.
    others = sorted(rel_members - {person["id"]})
    if len(others) != 1:
        return PolicyResult(False, condition="household_is_not_a_couple",
                            detail=f"{len(rel_members)} active members")
    confirmed, why = _drake_confirms_mfj(conn, person["id"], others[0])
    if not confirmed:
        return PolicyResult(False, condition="no_drake_mfj_confirmation", detail=why)

    # 4 — owners seen in the folder are limited to that person, their spouse and the household.
    #     (persons is already {person}; the spouse is allowed but need not appear.)
    stray = persons - {person["id"], others[0]}
    if stray:
        return PolicyResult(False, condition="unrelated_person_owner",
                            detail=f"{len(stray)} owner(s) outside the couple")

    return PolicyResult(True, entity_type="household", entity_id=household_id,
                        condition="mfj_household_confirmed", detail=why)
