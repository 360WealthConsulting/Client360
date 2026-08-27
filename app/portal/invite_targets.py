"""Resolve WHO a portal invitation is for, from human-readable input — server-side, fail-closed.

Staff used to type ``person_id``, ``household_id`` and ``organization_id`` into the invite form.
That exposed internal keys as a normal workflow and made the browser the authority on which record
was being granted external access. This module is the server-side authority instead: staff search by
name / email / phone, pick a person, and every internal id is derived and re-validated here.

Nothing in this module trusts the browser. ``resolve_invite_target`` re-checks record scope on the
submitted person id on EVERY call, so a tampered or stale hidden field resolves to a refusal rather
than to a grant. Scoping for search is delegated to the existing principal-scoped
``universal_search`` rather than reimplemented, so staff can only ever find people they can service.

Access types are NOT reinterpreted here — see :data:`ACCESS_CHOICES` for the exact mapping onto the
grant semantics already implemented in ``app.portal.service._resolve_scope``.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db import engine, household_relationships, households, people

#: Staff-facing access options, each mapping onto an EXISTING ``portal_access_grants.access_type``.
#:
#: ``self`` — the grant carries ``person_id``; scope is that one person plus their household id.
#:   It is deliberately NOT in the household-expansion set, so no other member becomes reachable.
#:   This is the ordinary client case and the safe default.
#:
#: ``joint`` — the grant carries no ``person_id``; ``_resolve_scope`` puts the household into
#:   ``shared_household_ids``, which expands to EVERY person in that household. That expansion is
#:   additionally governed firm-wide by the ``portal.household_enabled`` gate.
#:
#: ``delegate`` is deliberately absent. The old dropdown offered it, but the expansion set in
#: ``_resolve_scope`` is ``{"joint", "trusted", "delegated"}`` — "delegate" is not a member. Such a
#: grant gets neither a person nor household expansion, so the account can see nothing. Remapping it
#: to "delegated" would silently widen a client's access to their whole household, which is an
#: authorization change, not a UI change. See :data:`AUTHORIZED_REPRESENTATIVE_NOTICE`.
ACCESS_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("self", "Client access",
     "This client only. They see their own record and nothing else in the household."),
    ("joint", "Household access",
     "Everyone in the household. Use for spouses or partners who share their financial picture."),
)

#: The only access types this staff form may create. Anything else fails closed server-side.
PERMITTED_ACCESS_TYPES = frozenset(code for code, _label, _help in ACCESS_CHOICES)

DEFAULT_ACCESS_TYPE = "self"

#: Shown where "authorized representative" would otherwise appear. The prerequisite — a represented
#: subject distinct from the account holder — is not representable in ``portal_access_grants``.
AUTHORIZED_REPRESENTATIVE_NOTICE = (
    "Authorized-representative access (a third party acting for a client, such as a POA or trustee) "
    "is not available from this form. The grant model cannot record who the representative acts "
    "for, so it cannot be issued safely here. Raise it with the portal owner."
)


class InviteTargetError(ValueError):
    """A person cannot be invited. The message is staff-facing and names no internal id."""


@dataclass(frozen=True)
class InviteTarget:
    """A validated invitation target. Every id on it was derived server-side, never submitted."""

    person_id: int
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str
    household_id: int
    household_name: str
    household_member_names: tuple[str, ...]

    @property
    def household_is_shared(self) -> bool:
        """Whether household access would actually reach anyone beyond this client."""
        return len(self.household_member_names) > 1


# --- search ---------------------------------------------------------------------------

#: Shortest term worth searching. Matches universal_search's own floor.
MIN_TERM = 2
#: Per-term ceiling used while intersecting. Higher than the returned limit so combining
#: "Michael" + "Shelton" is not starved by one common term filling its own page first.
_TERM_LIMIT = 200


def search_people(principal, query: str | None = None, *, first_name: str | None = None,
                  last_name: str | None = None, email: str | None = None,
                  phone: str | None = None, limit: int = 20) -> list[dict]:
    """People this principal may service, matching EVERY supplied term.

    Each term is run through the existing principal-scoped ``universal_search`` (which applies
    ``accessible_person_ids`` and the phone-query normalisation), and the per-term id sets are
    INTERSECTED. So "Michael" + "Shelton" narrows to people matching both, and adding a partial
    phone narrows further — without a second person-search implementation and without widening
    what staff can see: an intersection can only ever be smaller than each scoped set.

    ``query`` remains supported as a single combined term. Results are enriched with the fields
    needed to tell two people with the same name apart, because a name alone is not safe to pick
    from."""
    from app.services.universal_search import universal_search

    terms = [t.strip() for t in (query, first_name, last_name, email, phone) if t and t.strip()]
    terms = [t for t in terms if len(t) >= MIN_TERM]
    if not terms:
        return []

    person_ids: set[int] | None = None
    for term in terms:
        found = universal_search(principal, term, types=["person"], limit=_TERM_LIMIT)
        matched = {r["id"] for r in found.get("results", []) if r.get("kind") == "person"}
        person_ids = matched if person_ids is None else (person_ids & matched)
        if not person_ids:
            return []                      # one term excludes everyone — stop early
    person_ids = sorted(person_ids)[:limit]
    if not person_ids:
        return []

    with engine.connect() as connection:
        rows = connection.execute(
            select(people.c.id, people.c.first_name, people.c.last_name, people.c.full_name,
                   people.c.primary_email, people.c.primary_phone, people.c.city, people.c.state,
                   people.c.household_id, households.c.name.label("household_name"))
            .select_from(people.outerjoin(households, households.c.id == people.c.household_id))
            .where(people.c.id.in_(person_ids))
            .order_by(people.c.last_name, people.c.first_name, people.c.id)
        ).mappings().all()

    return [{
        "person_id": r["id"],
        "first_name": r["first_name"] or "",
        "last_name": r["last_name"] or "",
        "full_name": r["full_name"] or f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(),
        "email": r["primary_email"] or "",
        "phone": r["primary_phone"] or "",
        "location": ", ".join(p for p in (r["city"], r["state"]) if p),
        "household_name": r["household_name"] or "",
        "has_household": r["household_id"] is not None,
    } for r in rows]


# --- resolution -----------------------------------------------------------------------

def _primary_household(connection, person_row):
    """The household a grant should anchor on: the person's own household first, then the
    relationship marked primary. Never guessed from surname or address."""
    if person_row["household_id"] is not None:
        return person_row["household_id"]
    return connection.scalar(
        select(household_relationships.c.household_id)
        .where(household_relationships.c.person_id == person_row["id"])
        .order_by(household_relationships.c.is_primary_household.desc(),
                  household_relationships.c.is_primary.desc(),
                  household_relationships.c.id)
        .limit(1))


def resolve_invite_target(principal, person_id) -> InviteTarget:
    """Validate a submitted person selection and derive every internal id from the database.

    Raises :class:`InviteTargetError` when the person is missing, inactive, outside the principal's
    WRITE record scope, or has no household to anchor a grant on. The same refusal covers "no such
    person" and "outside your scope" so the form never discloses that a record exists."""
    from app.security.authorization import record_in_scope

    try:
        person_id = int(person_id)
    except (TypeError, ValueError):
        raise InviteTargetError("Select a client before sending an invitation.") from None

    # Scope BEFORE any read-back: a tampered hidden field must not confirm that a record exists.
    if not record_in_scope(principal, "person", person_id, write=True):
        raise InviteTargetError("That client is not available to you. Search and select again.")

    with engine.connect() as connection:
        person = connection.execute(
            select(people.c.id, people.c.first_name, people.c.last_name, people.c.full_name,
                   people.c.primary_email, people.c.primary_phone, people.c.household_id,
                   people.c.active)
            .where(people.c.id == person_id)).mappings().one_or_none()
        if not person or not person["active"]:
            raise InviteTargetError("That client is not available to you. Search and select again.")

        household_id = _primary_household(connection, person)
        if household_id is None:
            raise InviteTargetError(
                "This client is not in a household yet, and portal access is granted through a "
                "household. Add them to one first, then invite.")

        household_name = connection.scalar(
            select(households.c.name).where(households.c.id == household_id)) or ""
        member_names = tuple(n for n in connection.scalars(
            select(people.c.full_name).where(people.c.household_id == household_id,
                                             people.c.active.is_(True))
            .order_by(people.c.last_name, people.c.first_name)) if n)

    first = person["first_name"] or ""
    last = person["last_name"] or ""
    return InviteTarget(
        person_id=person["id"], first_name=first, last_name=last,
        full_name=person["full_name"] or f"{first} {last}".strip() or "Client",
        email=person["primary_email"] or "", phone=person["primary_phone"] or "",
        household_id=household_id, household_name=household_name,
        household_member_names=member_names)


def validate_access_type(access_type: str | None, target: InviteTarget) -> str:
    """Return the grant ``access_type`` to use, or raise if it is not legitimate for this target.

    Fail-closed: an unknown, absent, or removed value (``delegate``) is refused rather than quietly
    downgraded, so a tampered form cannot select a grant shape this workflow does not support."""
    code = (access_type or "").strip() or DEFAULT_ACCESS_TYPE
    if code not in PERMITTED_ACCESS_TYPES:
        raise InviteTargetError("Choose a valid portal access level.")
    if code == "joint" and not target.household_is_shared:
        raise InviteTargetError(
            "Household access needs more than one person in the household. Use client access, or "
            "add the other household members first.")
    return code
