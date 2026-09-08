"""One evidence evaluator for every Drake identity/person linkage decision.

THE PROBLEM THIS REPLACES
-------------------------
Drake linkage was decided in two places that had drifted apart, each with its own private copy of the
normalisation helpers and its own idea of what the evidence meant:

``scripts/link_drake_to_people.py`` (automatic)
    Four exact indexes -- email 100, name+DOB 99, phone 98, name+city+state 95 -- guarded by a
    uniqueness check and a cross-signal agreement check. Its ``best_score < 95`` cut-off is dead
    code: every method it can emit already scores 95 or more.

``scripts/build_drake_identity_review.py`` -> ``app/routes/matches.py`` (human review)
    Additive scoring -- name 55, email 40, phone 35, city 10, state 5 -- listing anything at 55 or
    above, ordered with ties broken by the LOWEST ``person_id``.

A read-only replay of all 1,802 production identities established four defects, all of which are
properties of the *semantics*, not of any single row:

1.  **Roles were pooled.** The review scorer builds ``{taxpayer_name, spouse_name}`` as one set, so a
    person carrying the SPOUSE's name scores an exact-name hit against a TAXPAYER identity. 64
    identities carry both names.

2.  **Return-level contact was treated as person-specific.** Drake exposes ``TP_Cell_Phone`` /
    ``TP_Day_Phone`` / ``TP_Eve_Phone`` -- explicitly the taxpayer's -- and a bare ``Email`` with no
    attribution at all. The importer attaches both to the taxpayer contact, and the review scorer
    then pays 40/35 points for them. On a joint return that email is the household's. This is how
    identity ``6e4b8ada0e03`` linked the taxpayer *Titus Glick* to his spouse *Clara Glick*.

3.  **A spouse has no contact evidence, ever.** Drake has no ``SP_`` email or phone field. The
    importer writes ``email: None, phone: None`` for spouse contacts, so 147 linked spouse identities
    rest on a name alone.

4.  **Confidence did not mean anything.** ``build_drake_identity`` writes a literal ``100`` for all
    1,802 identities merely because the identity exists; the review path then overwrites it with an
    additive score. Neither number describes the evidence.

THE DESIGN
----------
Both paths now build a :class:`IdentityEvidence` and call :func:`evaluate`, so they cannot disagree
about what a signal means. The evaluator is pure -- no database, no I/O -- which is what lets the
regression suite pin every rule below without touching production data.

The rules, each of which fails CLOSED:

*   An identity is scored only against the name belonging to its OWN role. Taxpayer and spouse are
    never pooled, and a spouse never inherits taxpayer contact evidence.
*   A bare ``Email`` is person-attributed only when the return carries no spouse. On a joint return
    it is household context: retained, reportable, never identity evidence.
*   ``TP_*`` phones are person-attributed to the TAXPAYER only.
*   An exact normalised name is real evidence, but name-only uniqueness within the current roster is
    uniqueness among the people we happen to hold -- not in the world. It can propose a review
    candidate; it can never auto-link.
*   City/state corroborate a name; they never identify anyone on their own.
*   DOB is used only when the source attributes it to this role AND the roster holds a DOB to compare.
*   Two people, or two signals pointing at different people, resolve to nothing.
*   Where the resolved person has a name twin in the roster -- the same name held by another person
    record -- the decision fails closed, because choosing between two duplicate records on a contact
    point is arbitrary and the real remediation is a merge.
*   Confidence is derived from the signals actually present, and there is no tie-break by id.

This module decides; it does not write. Nothing here modifies an existing link.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.link_trust import (
    MACHINE_CONTACT,
    MACHINE_EXACT_NAME,
    MACHINE_NAME_LOCATION,
)

# --- roles ------------------------------------------------------------------------------------

TAXPAYER = "taxpayer"
SPOUSE = "spouse"
ROLES = (TAXPAYER, SPOUSE)

# --- outcomes ---------------------------------------------------------------------------------

#: Evidence identifies exactly one person and nothing contradicts it. Safe to link without a human.
AUTO_LINK = "auto_link"
#: Real evidence, but not enough to decide alone. Belongs in the review queue, not in a write.
REVIEW_CANDIDATE = "review_candidate"
#: Signals disagree, or a signal points at more than one person. Deliberately resolves to nothing.
AMBIGUOUS = "ambiguous"
#: No usable evidence at all.
NO_MATCH = "no_match"

OUTCOMES = (AUTO_LINK, REVIEW_CANDIDATE, AMBIGUOUS, NO_MATCH)

#: Method strings recorded on a link, classified by :mod:`app.services.link_trust`.
METHOD_CONTACT_NAME = "drake_attributed_contact_and_role_name"
METHOD_CONTACT = "drake_attributed_contact"
METHOD_NAME = "drake_role_exact_name"
METHOD_NAME_LOCATION = "drake_role_exact_name_city_state"

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


# --- normalisation: ONE definition, shared by both paths ---------------------------------------

def clean(value):
    """Trim, drop NULs, and treat the empty string as absent."""
    if value is None:
        return None
    value = str(value).replace("\x00", "").strip()
    return value or None


def normalize_name(value):
    """Lower-case, strip punctuation, collapse whitespace. No fuzzy matching, ever."""
    value = _PUNCT.sub(" ", (clean(value) or "").lower())
    return _WS.sub(" ", value).strip() or None


def join_name(first, last):
    """Build a full name from parts before normalising, so both paths agree on the input."""
    return " ".join(part for part in (clean(first), clean(last)) if part) or None


def name_tokens(value):
    """The token SET of a name, so ``SPANGLER, ROBERT`` and ``Robert Spangler`` compare equal.

    Used ONLY to detect duplicate person records. It is never used to match an identity to a person:
    treating a reordered name as a match would be exactly the fuzzy behaviour this module forbids.
    """
    return frozenset((normalize_name(value) or "").split())


def normalize_email(value):
    value = (clean(value) or "").lower()
    return value if "@" in value else None


def normalize_phone(value):
    digits = "".join(ch for ch in (clean(value) or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else None


def normalize_dob(value):
    """A DOB compares as its ISO date text, or not at all."""
    value = clean(value)
    if not value:
        return None
    return str(value)[:10] or None


# --- the roster ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class RosterPerson:
    """One canonical person, reduced to the fields linkage is allowed to look at."""

    person_id: int
    full_name: str | None = None
    dob: str | None = None
    emails: frozenset = field(default_factory=frozenset)
    phones: frozenset = field(default_factory=frozenset)
    city: str | None = None
    state: str | None = None


class Roster:
    """Exact-match indexes over the canonical people, built once and queried per identity.

    Contact points contributed by Drake itself must NOT be loaded here. A Drake identity whose email
    matches the Drake source contact that the audited link created is evidence for nothing -- it is
    the link vouching for itself.
    """

    def __init__(self, people):
        self._people = {}
        self._by_name = {}
        self._by_email = {}
        self._by_phone = {}
        self._by_tokens = {}
        for person in people:
            self._people[person.person_id] = person
            key = normalize_name(person.full_name)
            if key:
                self._by_name.setdefault(key, set()).add(person.person_id)
                self._by_tokens.setdefault(name_tokens(person.full_name), set()).add(
                    person.person_id)
            for value in person.emails:
                value = normalize_email(value)
                if value:
                    self._by_email.setdefault(value, set()).add(person.person_id)
            for value in person.phones:
                value = normalize_phone(value)
                if value:
                    self._by_phone.setdefault(value, set()).add(person.person_id)

    def person(self, person_id):
        return self._people.get(person_id)

    def by_name(self, name):
        return set(self._by_name.get(normalize_name(name) or "", ()))

    def by_email(self, value):
        return set(self._by_email.get(normalize_email(value) or "", ()))

    def by_phone(self, value):
        return set(self._by_phone.get(normalize_phone(value) or "", ()))

    def by_name_location(self, name, city, state):
        """Name holders narrowed to a city/state. Location NARROWS a name; it never finds one."""
        city, state = (clean(city) or "").lower(), (clean(state) or "").lower()
        if not (city and state):
            return set()
        return {pid for pid in self.by_name(name)
                if (clean(self._people[pid].city) or "").lower() == city
                and (clean(self._people[pid].state) or "").lower() == state}

    def name_twins(self, person_id):
        """Other person records carrying the same name -- i.e. apparent duplicate records."""
        person = self._people.get(person_id)
        if person is None:
            return set()
        return set(self._by_tokens.get(name_tokens(person.full_name), ())) - {person_id}


# --- the identity under evaluation ---------------------------------------------------------------

@dataclass(frozen=True)
class IdentityEvidence:
    """Everything one Drake identity is allowed to assert about itself.

    ``role_name`` is the name for THIS identity's role and nothing else; the caller resolves it so
    the pooling defect cannot be reintroduced here. ``household_contacts`` exists so joint-return
    contact information stays visible and reportable while being structurally incapable of
    identifying a person -- :func:`evaluate` never reads it as identity evidence.
    """

    identifier_hash: str
    role: str
    role_name: str | None = None
    dob: str | None = None
    attributed_emails: frozenset = field(default_factory=frozenset)
    attributed_phones: frozenset = field(default_factory=frozenset)
    household_contacts: frozenset = field(default_factory=frozenset)
    city: str | None = None
    state: str | None = None
    joint_return: bool = False


def build_identity_evidence(identifier_hash, role, *, taxpayer_name=None, spouse_name=None,
                            emails=(), phones=(), dob=None, city=None, state=None,
                            has_spouse=False):
    """Assemble the evidence for one identity, applying Drake's own attribution rules.

    ``emails`` / ``phones`` are the raw contact points the return carried. Drake attributes phones to
    the taxpayer explicitly (``TP_*``) and attributes the bare ``Email`` to nobody, so:

    * a SPOUSE identity is given no contact evidence at all -- Drake has no spouse-attributed field,
      and inventing one by borrowing the taxpayer's is the defect this replaces;
    * a TAXPAYER identity keeps its ``TP_*`` phones, and keeps the email only when the return has no
      spouse. On a joint return the email becomes household context.
    """
    if role not in ROLES:
        raise ValueError(f"unknown Drake role: {role!r}")

    role_name = taxpayer_name if role == TAXPAYER else spouse_name
    emails = {normalize_email(v) for v in emails}
    emails.discard(None)
    phones = {normalize_phone(v) for v in phones}
    phones.discard(None)

    if role == SPOUSE:
        attributed_emails, attributed_phones = set(), set()
        household = emails | phones
    else:
        attributed_phones = set(phones)
        if has_spouse:
            attributed_emails, household = set(), set(emails)
        else:
            attributed_emails, household = set(emails), set()

    return IdentityEvidence(
        identifier_hash=identifier_hash,
        role=role,
        role_name=clean(role_name),
        dob=normalize_dob(dob),
        attributed_emails=frozenset(attributed_emails),
        attributed_phones=frozenset(attributed_phones),
        household_contacts=frozenset(household),
        city=clean(city),
        state=clean(state),
        joint_return=bool(has_spouse),
    )


# --- the decision ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """What the evidence supports. ``person_id`` is set only for :data:`AUTO_LINK`."""

    outcome: str
    person_id: int | None = None
    trust_level: str | None = None
    method: str | None = None
    confidence: int = 0
    reasons: tuple = ()
    candidates: tuple = ()

    @property
    def is_auto_link(self):
        return self.outcome == AUTO_LINK


def _sole(candidates):
    """The one person in a candidate set, or None. A set of two never becomes a choice."""
    return next(iter(candidates)) if len(candidates) == 1 else None


def evaluate(evidence, roster):
    """Resolve one Drake identity against the roster. Pure, deterministic, and fails closed."""
    reasons = []

    contact_hits = set()
    for value in evidence.attributed_emails:
        contact_hits |= roster.by_email(value)
    for value in evidence.attributed_phones:
        contact_hits |= roster.by_phone(value)
    if evidence.attributed_emails:
        reasons.append("person_attributed_email")
    if evidence.attributed_phones:
        reasons.append(f"person_attributed_phone({evidence.role})")
    if evidence.household_contacts:
        # Recorded so the household signal stays visible in reports; never used to identify.
        reasons.append(f"household_contact_ignored({len(evidence.household_contacts)})")

    name_hits = roster.by_name(evidence.role_name) if evidence.role_name else set()
    if evidence.role_name:
        reasons.append(f"role_name({evidence.role})={len(name_hits)}")

    # DOB may only narrow a name, and only when BOTH sides carry one. In this data model
    # ``people.birth_date`` is populated on 1 of 7,794 rows, so the usual outcome is that the source
    # DOB is person-attributed and perfectly good but has nothing to be compared against.
    dob_hits = set()
    if evidence.dob:
        comparable = {pid for pid in name_hits
                      if roster.person(pid) and normalize_dob(roster.person(pid).dob)}
        if comparable:
            dob_hits = {pid for pid in comparable
                        if normalize_dob(roster.person(pid).dob) == evidence.dob}
            reasons.append(f"role_dob_comparable({len(dob_hits)}/{len(comparable)})")
        else:
            reasons.append("role_dob_present_but_no_canonical_dob")

    location_hits = roster.by_name_location(evidence.role_name, evidence.city, evidence.state)
    if location_hits:
        reasons.append(f"name_city_state({len(location_hits)})")

    candidates = tuple(sorted(contact_hits | name_hits))

    # --- fail closed on disagreement -------------------------------------------------------------
    if len(contact_hits) > 1:
        return Decision(AMBIGUOUS,
                        reasons=tuple(reasons + [f"contact maps to {len(contact_hits)} people"]),
                        candidates=candidates)
    if contact_hits and name_hits and not (contact_hits & name_hits):
        return Decision(AMBIGUOUS,
                        reasons=tuple(reasons + ["contact and role-correct name disagree"]),
                        candidates=candidates)

    # --- the only path to an automatic link -------------------------------------------------------
    person_id = _sole(contact_hits)
    if person_id is not None:
        twins = roster.name_twins(person_id)
        if twins:
            # Two person records carrying this name: a contact point picks one arbitrarily, and the
            # real defect is the duplicate. Refuse rather than relink between duplicates.
            return Decision(REVIEW_CANDIDATE, trust_level=MACHINE_CONTACT,
                            method=METHOD_CONTACT, confidence=70,
                            reasons=tuple(reasons + [
                                "duplicate person records share this name: "
                                + ",".join(str(t) for t in sorted(twins))]),
                            candidates=tuple(sorted(set(candidates) | twins)))
        agrees = bool(name_hits) and person_id in name_hits
        return Decision(
            AUTO_LINK, person_id=person_id, trust_level=MACHINE_CONTACT,
            method=METHOD_CONTACT_NAME if agrees else METHOD_CONTACT,
            confidence=95 if agrees else 85,
            reasons=tuple(reasons + ["attributed contact resolves to one person"
                                     + (" and the role-correct name agrees" if agrees else "")]),
            candidates=candidates)

    # --- everything below is a review candidate at most --------------------------------------------
    if len(name_hits) > 1:
        return Decision(AMBIGUOUS,
                        reasons=tuple(reasons + [
                            f"{len(name_hits)} people share this exact name"]),
                        candidates=candidates)

    sole_name = _sole(name_hits)
    if sole_name is not None:
        if _sole(dob_hits) == sole_name:
            return Decision(REVIEW_CANDIDATE, person_id=None, trust_level=MACHINE_EXACT_NAME,
                            method=METHOD_NAME, confidence=75,
                            reasons=tuple(reasons + ["role-correct name and role-attributed DOB "
                                                     "agree; a name is still not an identifier"]),
                            candidates=candidates)
        if sole_name in location_hits:
            return Decision(REVIEW_CANDIDATE, trust_level=MACHINE_NAME_LOCATION,
                            method=METHOD_NAME_LOCATION, confidence=60,
                            reasons=tuple(reasons + ["name unique in roster, corroborated by "
                                                     "city/state; location is context only"]),
                            candidates=candidates)
        return Decision(REVIEW_CANDIDATE, trust_level=MACHINE_EXACT_NAME, method=METHOD_NAME,
                        confidence=55,
                        reasons=tuple(reasons + ["name unique in the CURRENT roster only; "
                                                 "not sufficient for an automatic link"]),
                        candidates=candidates)

    return Decision(NO_MATCH, reasons=tuple(reasons + ["no usable person-identifying evidence"]),
                    candidates=candidates)
