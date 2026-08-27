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
#: Per-term ceiling used while combining. Higher than the returned limit so one common term
#: cannot crowd out the candidates a more selective term found.
_TERM_LIMIT = 200
#: The identifying fields staff can type, in the order they appear on the form.
_FIELDS = ("first_name", "last_name", "email", "phone")

#: Secondary ordering weight per (field, quality). Only used to break ties INSIDE a tier — the
#: tier in :func:`_tier` is what implements the required precedence.
_WEIGHTS = {
    ("email", "exact"): 100, ("phone", "exact"): 80,
    ("last_name", "exact"): 34, ("first_name", "exact"): 30,
    ("email", "partial"): 12, ("phone", "partial"): 10,
    ("last_name", "partial"): 5, ("first_name", "partial"): 4,
    ("query", "partial"): 3,
}


def _norm_email(value):
    return (value or "").strip().lower()


def _email_is_exact(entered, row) -> bool:
    entered = _norm_email(entered)
    return bool(entered) and entered in {_norm_email(row["primary_email"]),
                                         _norm_email(row["normalized_email"])}


def _phone_is_exact(entered, row) -> bool:
    """Exact against the stored digits, tolerating punctuation and a leading US country code —
    the same normalisation the query itself goes through."""
    from app.services.universal_search import _phone_query_variants
    stored = (row["normalized_phone"] or "").strip()
    return bool(stored) and stored in set(_phone_query_variants(entered or ""))


def _name_is_exact(entered, stored) -> bool:
    return bool((entered or "").strip()) and \
        (entered or "").strip().lower() == (stored or "").strip().lower()


def _match_profile(row, terms, matched_by_field) -> dict:
    """How this person matched each ENTERED field: "exact", "partial", or None.

    "partial" means the canonical search matched the term but the stored value is not equal to it
    — a substring of an email, part of a phone, a name that differs in case or completeness."""
    profile = {}
    for field in _FIELDS:
        entered = terms.get(field)
        if not entered or row["id"] not in matched_by_field.get(field, ()):
            profile[field] = None
            continue
        if field == "email":
            exact = _email_is_exact(entered, row)
        elif field == "phone":
            exact = _phone_is_exact(entered, row)
        else:
            exact = _name_is_exact(entered, row[field])
        profile[field] = "exact" if exact else "partial"
    # A combined single-term query has no field identity; it only ever counts as a weak match.
    profile["query"] = ("partial" if terms.get("query")
                        and row["id"] in matched_by_field.get("query", ()) else None)
    return profile


def _tier(profile) -> int:
    """Match strength, lowest first. This is the required precedence, expressed directly."""
    exact = {f for f, q in profile.items() if q == "exact"}
    partial = {f for f, q in profile.items() if q == "partial"}
    matched = exact | partial
    if "email" in exact:
        return 1
    if "phone" in exact:
        return 2
    if "first_name" in exact and "last_name" in exact:
        return 3
    if "last_name" in exact and len(matched) > 1:
        return 4
    if "first_name" in exact and len(matched) > 1:
        return 5
    if "email" in partial:
        return 6
    if "phone" in partial:
        return 7
    return 8                                    # first-name-only / last-name-only


def _sort_key(row, profile):
    matched = [f for f, q in profile.items() if q]
    weight = sum(_WEIGHTS.get((f, profile[f]), 0) for f in matched)
    # Within a tier, more matching fields wins, then the heavier match, then a stable name order.
    return (_tier(profile), -len(matched), -weight,
            (row["last_name"] or "").lower(), (row["first_name"] or "").lower(), row["id"])


def search_people(principal, query: str | None = None, *, first_name: str | None = None,
                  last_name: str | None = None, email: str | None = None,
                  phone: str | None = None, limit: int = 20) -> list[dict]:
    """People this principal may service, matching ANY supplied identifier, best match first.

    The four fields are alternate ways to FIND someone, not four filters that must all hold. A
    client whose stored email is out of date must still be findable by name and phone, so the
    per-field result sets are UNIONED and then RANKED by match strength (see :func:`_tier`).
    An earlier version intersected them, which meant one stale value — a changed email — hid the
    person completely.

    Every term is still run through the existing principal-scoped ``universal_search`` (which
    applies ``accessible_person_ids`` and the phone-query normalisation), so a union of scoped sets
    is itself scoped: nothing here can surface a person the principal may not service.

    Returning a candidate is NOT selecting one. The caller must still click a specific result
    before any person id is established; nothing is auto-selected on a partial match."""
    from app.services.universal_search import universal_search

    terms = {"query": query, "first_name": first_name, "last_name": last_name,
             "email": email, "phone": phone}
    terms = {k: v.strip() for k, v in terms.items()
             if v and v.strip() and len(v.strip()) >= MIN_TERM}
    if not terms:
        return []

    matched_by_field: dict[str, set[int]] = {}
    candidates: set[int] = set()
    for field, term in terms.items():
        found = universal_search(principal, term, types=["person"], limit=_TERM_LIMIT)
        ids = {r["id"] for r in found.get("results", []) if r.get("kind") == "person"}
        matched_by_field[field] = ids
        candidates |= ids                       # UNION: any identifier is enough to be a candidate
    if not candidates:
        return []

    with engine.connect() as connection:
        rows = connection.execute(
            select(people.c.id, people.c.first_name, people.c.last_name, people.c.full_name,
                   people.c.primary_email, people.c.normalized_email, people.c.primary_phone,
                   people.c.normalized_phone, people.c.city, people.c.state,
                   people.c.household_id, households.c.name.label("household_name"))
            .select_from(people.outerjoin(households, households.c.id == people.c.household_id))
            .where(people.c.id.in_(candidates))
        ).mappings().all()

    ranked = sorted(rows, key=lambda r: _sort_key(r, _match_profile(r, terms, matched_by_field)))
    return [{
        # Always the CANONICAL stored values, never what staff typed: staff must be able to see
        # that the record's email or phone differs from the one they entered.
        "person_id": r["id"],
        "first_name": r["first_name"] or "",
        "last_name": r["last_name"] or "",
        "full_name": r["full_name"] or f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(),
        "email": r["primary_email"] or "",
        "phone": r["primary_phone"] or "",
        "location": ", ".join(p for p in (r["city"], r["state"]) if p),
        "household_name": r["household_name"] or "",
        "has_household": r["household_id"] is not None,
    } for r in ranked[:limit]]


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
