"""Editable canonical person contact/address fields (Sprint 2).

Staff can correct a client's contact and address details on the canonical ``people`` record.
Imports write ``source_contacts`` and never overwrite ``people`` (promotion only inserts new
people), so these staff edits are the durable source of truth for display. Identity fields such
as ``full_name`` are intentionally NOT part of ``update_person_contact`` and are not reachable
from the contact-edit form or the client portal. Correcting one is a separate, explicitly named
operation — ``correct_person_identity`` — so that an identity change can never happen as a side
effect of an ordinary contact edit.

Each edit records an audit event (changed field NAMES only — no PII values in the audit trail)
and a client timeline event.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import engine, households, people
from app.security.audit import write_audit_event
from app.security.service import normalize_email
from app.services.timeline import add_timeline_event

#: The only person columns staff may edit through the profile.
EDITABLE_FIELDS = (
    "primary_email", "primary_phone", "preferred_name",
    "address_line_1", "address_line_2", "city", "state", "postal_code",
)


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def update_person_contact(person_id: int, updates: dict, *, actor_user_id: int | None,
                          request_id: str | None = None, conn=None) -> list[str]:
    """Apply staff edits to a person's contact/address fields. Only fields whose value actually
    changes are written. Returns the sorted list of changed field names ([] if nothing changed).
    Raises ``ValueError`` if the person does not exist. Records timeline + audit when something
    changed (and keeps ``normalized_email``/``normalized_phone`` in sync)."""
    clean = {}
    for field in EDITABLE_FIELDS:
        if field in updates:
            clean[field] = (updates[field] or "").strip() or None

    def _do(c):
        current = c.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if current is None:
            raise ValueError("Person not found.")
        changed = {k: v for k, v in clean.items() if current[k] != v}
        if not changed:
            return []
        values = dict(changed)
        if "primary_email" in changed:
            values["normalized_email"] = (
                normalize_email(changed["primary_email"]) if changed["primary_email"] else None
            )
        if "primary_phone" in changed:
            values["normalized_phone"] = _normalize_phone(changed["primary_phone"])
        c.execute(people.update().where(people.c.id == person_id).values(**values))
        _changed = sorted(changed.keys())
        # (D.35) Publish the updated business FACT — field NAMES only (never values), transactional.
        from app.services.events import publisher
        publisher.publish_safe("people.person_updated",
                               {"person_id": person_id, "changed_fields": _changed}, conn=c,
                               producer="people.service", subject_ref=f"person:{person_id}")
        return _changed

    changed_fields = _run(conn, _do)
    if changed_fields:
        # ``conn`` is handed on so the trail commits — or rolls back — with the row it describes.
        # Without it a caller that batches this edit with an identity or household change could
        # abort and still leave an audit entry claiming the contact details were updated, which is
        # the one thing an audit trail may never say.
        add_timeline_event(
            person_id=person_id, source="client360", event_type="person_updated",
            title="Client details updated", summary="Updated: " + ", ".join(changed_fields),
            event_metadata={"fields": changed_fields, "actor_user_id": actor_user_id},
            conn=conn,
        )
        write_audit_event(
            action="person.updated", entity_type="person", entity_id=person_id,
            actor_user_id=actor_user_id, request_id=request_id or f"person-{uuid.uuid4()}",
            metadata={"fields": changed_fields}, conn=conn,
        )
    return changed_fields


def _collapse_whitespace(value: str | None) -> str:
    """Collapse leading/trailing and repeated internal whitespace (including tabs/newlines)."""
    return " ".join((value or "").split())


def correct_person_identity(person_id: int, *, full_name: str, actor_user_id: int,
                            reason: str | None = None, request_id: str | None = None,
                            conn=None) -> list[str]:
    """Correct a person's canonical ``full_name`` — the ONE governed path that may write it.

    ``update_person_contact`` deliberately excludes identity fields and the client portal cannot
    reach them at all; this is an explicit staff/system correction, never a bulk or client-driven
    edit, so it is deliberately a separate entry point rather than a widening of EDITABLE_FIELDS.

    Returns ``["full_name"]`` when the name changed, ``[]`` when it already matched (no event, no
    timeline, no audit). Raises ``ValueError`` for a missing actor, a blank name, or an unknown
    person — "Person not found." matching ``update_person_contact``.

    Authorization is enforced at the route/caller layer, as everywhere else in this service:
    ``actor_user_id`` is recorded for attribution and is NOT itself a permission check.
    """
    if actor_user_id is None:
        raise ValueError("An identity correction requires an actor_user_id.")
    cleaned = _collapse_whitespace(full_name)
    if not cleaned:
        raise ValueError("full_name cannot be blank.")
    clean_reason = _collapse_whitespace(reason) or None

    def _do(c):
        current = c.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if current is None:
            raise ValueError("Person not found.")
        if current["full_name"] == cleaned:
            return []
        c.execute(people.update().where(people.c.id == person_id).values(full_name=cleaned))
        # Same FACT the contact edit publishes, so people.summary needs no new subscription.
        from app.services.events import publisher
        publisher.publish_safe("people.person_updated",
                               {"person_id": person_id, "changed_fields": ["full_name"]}, conn=c,
                               producer="people.service", subject_ref=f"person:{person_id}")
        # Governance rides the SAME transaction as the write it describes: a rolled-back
        # correction must not leave an audit or timeline entry claiming it happened. The reason
        # lives on the TIMELINE (person-scoped, already carries names) and never in the audit
        # trail, which records field names only — see this module's docstring.
        add_timeline_event(
            person_id=person_id, source="client360", event_type="person_identity_corrected",
            title="Canonical name corrected",
            summary=f"Canonical name corrected. Reason: {clean_reason}" if clean_reason
                    else "Canonical name corrected.",
            event_metadata={"fields": ["full_name"], "actor_user_id": actor_user_id,
                            "reason": clean_reason, "correction": "identity"},
            conn=c,
        )
        write_audit_event(
            action="person.identity_corrected", entity_type="person", entity_id=person_id,
            actor_user_id=actor_user_id,
            request_id=request_id or f"person-identity-{uuid.uuid4()}",
            metadata={"fields": ["full_name"], "reason_provided": clean_reason is not None},
            conn=c,
        )
        return ["full_name"]

    return _run(conn, _do)


def correct_person_birth_date(person_id: int, *, birth_date, actor_user_id: int,
                              reason: str | None = None, request_id: str | None = None,
                              conn=None) -> list[str]:
    """Correct a person's canonical ``birth_date`` — the ONE governed path that may write it.

    A sibling of :func:`correct_person_identity`, and separate from
    :func:`update_person_contact` for the same stated reason: a date of birth identifies a person
    rather than describing how to reach them, so changing one must be an explicit, named act and
    never a side effect of correcting a phone number. Widening ``EDITABLE_FIELDS`` would have made
    it exactly that side effect.

    ``birth_date`` accepts a ``date`` or an ISO ``YYYY-MM-DD`` string; ``None`` clears it. Returns
    ``["birth_date"]`` when the value changed and ``[]`` when it already matched — no event, no
    timeline, no audit for a no-op. Raises ``ValueError`` for a missing actor, an unparseable or
    impossible date, or an unknown person ("Person not found.", matching its siblings).

    Authorization is enforced at the route layer, as everywhere else in this service.
    """
    if actor_user_id is None:
        raise ValueError("An identity correction requires an actor_user_id.")
    parsed = _parse_birth_date(birth_date)
    clean_reason = _collapse_whitespace(reason) or None

    def _do(c):
        current = c.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if current is None:
            raise ValueError("Person not found.")
        if current["birth_date"] == parsed:
            return []
        c.execute(people.update().where(people.c.id == person_id).values(birth_date=parsed))
        from app.services.events import publisher
        publisher.publish_safe("people.person_updated",
                               {"person_id": person_id, "changed_fields": ["birth_date"]}, conn=c,
                               producer="people.service", subject_ref=f"person:{person_id}")
        # Governance rides the same transaction as the write, and carries field NAMES only — the
        # date itself is PII and never reaches the audit trail. See this module's docstring.
        add_timeline_event(
            person_id=person_id, source="client360", event_type="person_identity_corrected",
            title="Date of birth corrected",
            summary=f"Date of birth corrected. Reason: {clean_reason}" if clean_reason
                    else "Date of birth corrected.",
            event_metadata={"fields": ["birth_date"], "actor_user_id": actor_user_id,
                            "reason": clean_reason, "correction": "identity"},
            conn=c,
        )
        write_audit_event(
            action="person.identity_corrected", entity_type="person", entity_id=person_id,
            actor_user_id=actor_user_id,
            request_id=request_id or f"person-identity-{uuid.uuid4()}",
            metadata={"fields": ["birth_date"], "reason_provided": clean_reason is not None},
            conn=c,
        )
        return ["birth_date"]

    return _run(conn, _do)


def set_person_household(person_id: int, household_id: int | None, *, actor_user_id: int,
                         reason: str | None = None, request_id: str | None = None,
                         conn=None) -> list[str]:
    """Assign this ONE person to the household the caller named, or clear the assignment.

    Deliberately narrow, and deliberately NOT ``households.assign_people_to_household``. That
    service takes a set of people and puts them in "one common household", which it may CREATE, or
    infer from whichever member already has one. Both behaviours are right for the bulk household
    tools and wrong here: a profile edit must move exactly the person in front of you, into exactly
    the household that was picked from the list, and must never bring a household into existence or
    guess a family grouping as a side effect of someone correcting a phone number.

    So this does one thing:

    * ``household_id`` must name an EXISTING household, verified in the same transaction as the
      write. A stale or hand-edited id is rejected, never created.
    * ``None`` clears the assignment. That is an explicit choice the form has to express, and it is
      audited and timelined exactly like setting one.
    * No other person's row is read or written. Spouses and household members are untouched.

    Returns ``["household_id"]`` when the assignment changed, ``[]`` when it already matched.
    Raises ``ValueError`` for a missing actor, an unknown person, or an unknown household.

    Authorization is enforced at the route layer, as everywhere else in this service;
    ``actor_user_id`` is recorded for attribution and is not itself a permission check.
    """
    if actor_user_id is None:
        raise ValueError("A household assignment requires an actor_user_id.")
    target = int(household_id) if household_id is not None else None
    clean_reason = _collapse_whitespace(reason) or None

    def _do(c):
        current = c.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if current is None:
            raise ValueError("Person not found.")
        if target is not None:
            exists = c.execute(
                select(households.c.id).where(households.c.id == target)).scalar()
            if exists is None:
                # Never created here. A household that does not exist is a bad selection, not an
                # instruction to invent one.
                raise ValueError("Household not found.")
        if current["household_id"] == target:
            return []

        previous = current["household_id"]
        c.execute(people.update().where(people.c.id == person_id).values(household_id=target))
        from app.services.events import publisher
        publisher.publish_safe("people.person_updated",
                               {"person_id": person_id, "changed_fields": ["household_id"]},
                               conn=c, producer="people.service",
                               subject_ref=f"person:{person_id}")
        add_timeline_event(
            person_id=person_id, source="client360", event_type="person_household_assigned",
            title="Household assignment changed" if target else "Household assignment removed",
            summary=(f"Assigned to household #{target}." if target
                     else "Removed from their household.")
                    + (f" Reason: {clean_reason}" if clean_reason else ""),
            event_metadata={"fields": ["household_id"], "actor_user_id": actor_user_id,
                            "previous_household_id": previous, "household_id": target,
                            "reason": clean_reason},
            conn=c,
        )
        # Household ids are record identifiers rather than personal detail, so they are safe to
        # record; no name, address or other PII enters the audit trail.
        write_audit_event(
            action="person.household_assigned", entity_type="person", entity_id=person_id,
            actor_user_id=actor_user_id,
            request_id=request_id or f"person-household-{uuid.uuid4()}",
            metadata={"fields": ["household_id"], "previous_household_id": previous,
                      "household_id": target, "reason_provided": clean_reason is not None},
            conn=c,
        )
        return ["household_id"]

    return _run(conn, _do)


def people_sharing_email(email: str | None, *, exclude_person_id: int | None = None,
                         limit: int = 10, conn=None) -> list[dict]:
    """Other ACTIVE people already carrying this email address. A WARNING, never an action.

    A shared address is a real and legitimate thing in this corpus — spouses on one mailbox, a
    family using an office address, an assistant handling a client's mail. So this never blocks the
    edit and never re-points anything: it reports who else holds the address so a human decides.

    What it deliberately does NOT do is guess. It does not merge, it does not move communications,
    and it does not pick a "primary" holder. Re-anchoring correspondence on a duplicate address
    would silently move one client's mail onto another's record, which is the failure this warning
    exists to prevent rather than automate.
    """
    normalized = normalize_email(email) if email else None
    if not normalized:
        return []

    def _do(c):
        query = select(people.c.id, people.c.full_name).where(
            people.c.normalized_email == normalized, people.c.active.is_(True))
        if exclude_person_id is not None:
            query = query.where(people.c.id != exclude_person_id)
        rows = c.execute(query.order_by(people.c.id).limit(limit)).mappings().all()
        return [{"person_id": r["id"], "name": r["full_name"]} for r in rows]

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


def _parse_birth_date(value):
    """A ``date``, an ISO ``YYYY-MM-DD`` string, or None. Anything else raises.

    Deliberately strict: a date of birth that silently parses the wrong way round is worse than a
    rejected edit, so no locale-dependent or ambiguous format is accepted here.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, _date) and not isinstance(value, _datetime):
        parsed = value
    elif isinstance(value, _datetime):
        parsed = value.date()
    else:
        try:
            parsed = _datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("birth_date must be an ISO date (YYYY-MM-DD).") from exc
    if parsed > _date.today():
        raise ValueError("birth_date cannot be in the future.")
    if parsed.year < 1900:
        raise ValueError("birth_date is implausibly early.")
    return parsed


def _run(conn, fn):
    if conn is not None:
        return fn(conn)
    with engine.begin() as c:
        return fn(c)
