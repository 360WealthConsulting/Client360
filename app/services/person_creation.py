"""Create ONE canonical ``people`` record from staff-entered details — the reusable service.

Client360 had no staff-facing "add a client" path. People arrived through ingestion
(``prospect_import``, ``document_entity_proposal``, matching/promotion), so a client who had never
appeared in an import simply could not be created, and the portal invite screen dead-ended.

This is the canonical creation service for that case. It deliberately mirrors
``prospect_import.create_prospect`` — the one existing staff-facing, capability-gated, audited
creation path — rather than adding a tenth ad-hoc ``people.insert()``:

* normalisation uses ONLY the established helpers: ``security.identity_utils.normalize_email``
  (what ``normalized_email`` is actually written with everywhere else) and
  ``services.people._normalize_phone``. The repo contains nine different ``normalize_email``
  functions; ``matching.matcher``'s gmail-dot-stripping variant is a MATCHING heuristic and is not
  what is stored, so using it here would silently break duplicate detection against existing rows;
* ``active=True`` is set EXPLICITLY. The column is nullable with no default, and every search
  filters ``active IS TRUE`` — an unset value would make the new client invisible to the very
  screen that created them;
* ``contact_type`` is left NULL. ``create_prospect`` writes "prospect", but that is a lead
  lifecycle marker, not a canonical classification; provenance is carried by the audit event;
* the household comes from the supported ``assign_people_to_household`` service, never from local
  SQL, so ``people.household_id`` AND the ``household_relationships`` row are both written;
* the creator is assigned to BOTH the new person and the household this workflow created, so they
  immediately hold the record scope the rest of the application requires — the person scope the
  portal invite flow checks, and the household scope the workspace/task/AI surfaces check
  separately. A pre-existing household is never assigned.

Duplicate protection is the reason this is a service and not a route. See :func:`create_client`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select

from app.db import engine, people
from app.security.audit import write_audit_event
from app.security.identity_utils import normalize_email
from app.services.people import _normalize_phone

#: Capability required to create a canonical client. The same one ``create_prospect`` requires and
#: the invite form already demands — deliberately NOT a new capability.
REQUIRED_CAPABILITY = "client.write"

#: Provenance. Distinguishes a staff member typing a client into Client360 from a lead import.
CREATION_SOURCE = "client360.staff_manual"
PRODUCER = "portal.admin.client_creation"

#: Shown when a hard duplicate exists that the principal may NOT see. It names nothing — no name,
#: email, phone or household — because the record is outside their scope, but creation is still
#: refused: duplicate protection is a data-integrity rule, not a visibility rule.
OUT_OF_SCOPE_DUPLICATE = ("A matching client already exists. Contact an administrator if you "
                          "cannot locate the record.")


class PersonCreationError(ValueError):
    """Creation refused. The message is staff-facing and never names an internal id."""


class DuplicateClientError(PersonCreationError):
    """A hard duplicate exists. Never overridable."""


class PossibleDuplicateWarning(PersonCreationError):
    """An exact name match exists. Overridable with an explicit second acknowledgement.

    ``candidates`` holds only the possible duplicates the principal may actually see."""

    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class CreatedClient:
    """A newly created canonical person. Human-readable; the id is opaque form state only."""

    person_id: int
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str
    household_name: str
    household_created: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)


# --- duplicate detection ----------------------------------------------------------------

def _accessible(connection, principal):
    from app.security.authorization import accessible_person_ids
    return accessible_person_ids(connection, principal)          # None = firm-wide


def _hard_duplicate_ids(connection, *, norm_email, norm_phone) -> set[int]:
    """People that make this creation a HARD duplicate, searched WITHOUT scope.

    Unscoped on purpose: a duplicate the creator cannot see is still a duplicate. The caller must
    never reveal these rows — see :data:`OUT_OF_SCOPE_DUPLICATE`."""
    conditions = []
    if norm_email:
        conditions.append(func.lower(people.c.normalized_email) == norm_email)
    if norm_phone:
        conditions.append(people.c.normalized_phone == norm_phone)
    if not conditions:
        return set()
    return set(connection.scalars(
        select(people.c.id).where(or_(*conditions), people.c.active.is_(True))))


def _name_match_rows(connection, *, first_name, last_name):
    """Active people with EXACTLY this first and last name. Unscoped; the caller filters for
    display and uses the unfiltered set for the safety decision."""
    return connection.execute(
        select(people.c.id, people.c.first_name, people.c.last_name, people.c.full_name,
               people.c.primary_email, people.c.primary_phone,
               people.c.normalized_email, people.c.normalized_phone)
        .where(func.lower(func.trim(people.c.first_name)) == first_name.strip().lower(),
               func.lower(func.trim(people.c.last_name)) == last_name.strip().lower(),
               people.c.active.is_(True))).mappings().all()


def _visible(rows, accessible):
    return [r for r in rows if accessible is None or r["id"] in accessible]


def _candidate_view(row) -> dict:
    """A duplicate candidate staff can both RECOGNISE and ACT ON.

    The visible half — name, email, phone — is what staff read. ``person_id`` and the name parts are
    hidden form state, carried so "Use this client" can hand the record to the existing invite
    selection instead of making staff retype it; identifying a duplicate and then being unable to
    use it was the whole defect. The id is never rendered as text, exactly like the invite form's
    existing hidden ``person_id`` field, and it grants nothing: every candidate here has already
    passed ``_visible(..., accessible)``, and the invitation route re-resolves and re-authorizes the
    id server-side on submit regardless of what the browser sends back."""
    return {"person_id": row["id"],
            "first_name": row["first_name"] or "", "last_name": row["last_name"] or "",
            "full_name": row["full_name"] or f"{row['first_name'] or ''} {row['last_name'] or ''}".strip(),
            "email": row["primary_email"] or "", "phone": row["primary_phone"] or ""}


def find_possible_duplicates(principal, *, first_name=None, last_name=None,
                             email=None, phone=None) -> list[dict]:
    """Possible existing clients, for staff REVIEW, scoped to what the principal may see.

    Reuses ``prospect_matching.find_matches`` for its matching semantics — exact email, then exact
    phone, then exact name, never a LIKE sweep — but that function is deliberately left unchanged
    for its existing callers, which are not principal-scoped. Scope is applied here, before
    anything reaches a browser."""
    from app.services.prospect_matching import find_matches

    name = " ".join(p for p in ((first_name or "").strip(), (last_name or "").strip()) if p)
    result = find_matches(email=email or None, phone=phone or None, name=name or None)
    matches = result.get("matches") or result.get("candidates") or []
    ids = [m.get("person_id") or m.get("id") for m in matches]
    ids = [i for i in ids if i]
    if not ids:
        return []
    with engine.connect() as connection:
        accessible = _accessible(connection, principal)
        rows = connection.execute(
            select(people.c.id, people.c.first_name, people.c.last_name, people.c.full_name,
                   people.c.primary_email, people.c.primary_phone)
            .where(people.c.id.in_(ids), people.c.active.is_(True))).mappings().all()
    return [_candidate_view(r) for r in _visible(rows, accessible)]


# --- creation ---------------------------------------------------------------------------

def _validated(first_name, last_name, email, phone, *, require_email):
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()
    if not first:
        raise PersonCreationError("A first name is required.")
    if not last:
        raise PersonCreationError("A last name is required.")
    if require_email and not email:
        raise PersonCreationError("An email address is required to invite a client to the portal.")
    if not email and not phone:
        raise PersonCreationError("An email address or a phone number is required.")
    return first, last, email or None, phone or None


def create_client(principal, *, first_name, last_name, email=None, phone=None,
                  acknowledge_name_duplicate: bool = False, require_email: bool = False,
                  request_id: str | None = None) -> CreatedClient:
    """Create one canonical person, their household, and the creator's record assignment.

    Refuses, never merges:

    * an exact ``normalized_email`` or ``normalized_phone`` match is a HARD duplicate and can never
      be overridden — including when the matching record is outside the principal's scope, where
      the refusal is generic so no identifying detail leaks;
    * an exact first+last match raises :class:`PossibleDuplicateWarning`, which the caller may
      override once staff explicitly acknowledge it. This holds even when the existing record has
      no email and no phone — a name alone is not evidence that two people are the same person, and
      a contact-less row is LESS corroborated, not more.

    Every duplicate check runs AGAIN inside the write transaction immediately before the insert —
    the staff member's review may be minutes old, and another staff member may have created the
    same person meanwhile."""
    if not principal.can(REQUIRED_CAPABILITY):
        raise PersonCreationError("Creating a client record requires client.write.")

    first, last, email, phone = _validated(first_name, last_name, email, phone,
                                           require_email=require_email)
    norm_email = normalize_email(email) if email else None
    norm_phone = _normalize_phone(phone)
    full_name = " ".join(p for p in (first, last) if p)
    request_id = request_id or f"client-create-{uuid.uuid4()}"

    with engine.begin() as connection:
        accessible = _accessible(connection, principal)

        # --- HARD duplicates, re-checked transactionally --------------------------------
        hard = _hard_duplicate_ids(connection, norm_email=norm_email, norm_phone=norm_phone)
        if hard:
            visible = _visible(
                connection.execute(
                    select(people.c.id, people.c.first_name, people.c.last_name,
                           people.c.full_name, people.c.primary_email, people.c.primary_phone)
                    .where(people.c.id.in_(hard))).mappings().all(),
                accessible)
            if not visible:
                raise DuplicateClientError(OUT_OF_SCOPE_DUPLICATE)
            names = ", ".join(_candidate_view(r)["full_name"] for r in visible[:3])
            raise DuplicateClientError(
                f"A client with this email address or phone number already exists ({names}). "
                "Use the existing record instead of creating a second one.")

        # --- name-only duplicates: review, then one explicit override ---------------------
        # A first+last collision is never conclusive on its own — real people share names — so it
        # is ALWAYS an overridable warning, never a block. That includes a match on a record with
        # no email and no phone: a contact-less row is LESS corroborated, not more. The only
        # non-overridable conditions are the exact identifier matches handled above.
        name_rows = _name_match_rows(connection, first_name=first, last_name=last)
        if name_rows and not acknowledge_name_duplicate:
            raise PossibleDuplicateWarning(
                f"{len(name_rows)} existing client(s) are already named {full_name}. "
                "Review them before creating a separate client.",
                [_candidate_view(r) for r in _visible(name_rows, accessible)])

        person_id = connection.execute(people.insert().values(
            first_name=first, last_name=last, full_name=full_name or None,
            primary_email=email, normalized_email=norm_email,
            primary_phone=phone, normalized_phone=norm_phone,
            # contact_type deliberately NULL — provenance lives in the audit event, not in a new
            # lifecycle taxonomy invented for this feature.
            active=True,
            created_by_user_id=principal.user_id, updated_by_user_id=principal.user_id,
        ).returning(people.c.id)).scalar_one()

        from app.services.events import publisher
        publisher.publish_safe("people.person_created", {"person_id": person_id}, conn=connection,
                               producer=PRODUCER, subject_ref=f"person:{person_id}")

    # --- household + assignment, through the supported services ---------------------------
    # A brand-new person always gets their OWN household. Households are never matched by surname
    # and family relationships are never guessed; joining an existing household stays a separate,
    # explicit workflow.
    from app.services.households import assign_people_to_household
    household = assign_people_to_household([person_id], actor_user_id=principal.user_id,
                                           request_id=request_id)

    # The creator must immediately hold write record scope on what they just created, or the very
    # next step (resolve_invite_target) would refuse the client they typed in themselves.
    # ``assign_record`` is the existing identity service and already supports both entity types;
    # record_assignments is never written directly from here.
    from app.services.identity import assign_record
    assign_record(principal.user_id, "person", person_id, "primary")
    household_id = household.get("household_id")
    if household_id is not None and household.get("household_created"):
        # This workflow created that household, so the creator should be able to open it on the
        # surfaces that authorize household records separately (workspace, task dashboard,
        # ai_assist). Only the household created HERE is assigned — never a pre-existing one.
        assign_record(principal.user_id, "household", household_id, "primary")

    write_audit_event(action="person.created", entity_type="person", entity_id=person_id,
                      actor_user_id=principal.user_id, request_id=request_id,
                      metadata={"source": CREATION_SOURCE, "via": "portal_admin",
                                "household_created": bool(household.get("household_created"))})

    from app.services.timeline import add_timeline_event
    add_timeline_event(person_id=person_id, source="client360", event_type="person_created",
                       title="Client created by staff",
                       summary=full_name, event_metadata={"source": CREATION_SOURCE})

    return CreatedClient(
        person_id=person_id, first_name=first, last_name=last, full_name=full_name,
        email=email or "", phone=phone or "",
        household_name=household.get("household_name") or "",
        household_created=bool(household.get("household_created")))
