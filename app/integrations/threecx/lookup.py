"""Resolve one ringing number to exactly one Client360 person — or to nobody.

WHAT A SCREEN-POP IS, AND WHY THAT CONSTRAINS THIS. When a call arrives, 3CX asks this endpoint who
is calling and then opens the returned URL on the answering staff member's screen, before anyone
says hello. The answer is therefore acted on without review, which makes a WRONG answer worse than
no answer: an advisor who opens Jane Smith's profile and greets the caller by her name has
disclosed that Jane is a client of this firm to whoever actually rang.

So the rule here is narrower than ordinary client matching:

  * **Exact normalized-phone equality only.** No prefix, suffix, substring, fuzzy or last-N-digits
    matching, and no fallback to name or email. The comparison is against
    ``people.normalized_phone`` under the repository's one normalization convention
    (:mod:`app.services.communications.phone_numbers`), which is also what the AssetMark, Schwab
    and Wealthbox importers wrote.

  * **One match, or none.** A phone number is not a person — couples share a mobile, households
    share a landline. Where :mod:`app.services.communications.sms_ingest` may file a shared number
    under a household, this module will NOT: a household has no single profile to pop and no single
    name to greet. Two matches produce ``found=False`` with a count, and 3CX pops nothing.

  * **No contact detail unless it pops.** A zero-match or ambiguous result carries a count and
    nothing else. Returning "we hold two people on this number, here are their names" would leak
    client identities to a caller who merely dialled the firm.

This module READS. It writes no row, and it never records the number it was asked about.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db import people
from app.services.communications.phone_numbers import normalize_phone

#: Path of the staff client profile a screen-pop opens. Kept next to the query that produces it so
#: the URL and the person id can never come from different ideas of what a client page is.
PROFILE_PATH = "/people/{person_id}"


@dataclass(frozen=True)
class LookupResult:
    """The outcome of one number lookup.

    ``person_id`` and ``display_name`` are populated only when exactly one active person matched;
    :attr:`found` is the single question 3CX is really asking, and it is false for both "nobody"
    and "more than one".
    """

    match_count: int
    person_id: int | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    household_id: int | None = None

    @property
    def found(self) -> bool:
        """Whether there is exactly one client to pop. Ambiguity is never a match."""
        return self.match_count == 1 and self.person_id is not None

    @property
    def ambiguous(self) -> bool:
        """More than one person holds this number, so no profile may be opened automatically."""
        return self.match_count > 1

    def profile_url(self, base_url: str | None) -> str | None:
        """Absolute staff profile URL for a screen-pop, or ``None``.

        ``None`` when there is no single match, and also when the deployment has no validated
        canonical origin: 3CX opens this URL on a staff desktop, so a relative or host-header-
        derived path would either fail to open or open against an attacker-chosen host. Failing
        closed costs a screen-pop; guessing costs a redirect to somewhere else entirely.
        """
        if not self.found or not base_url:
            return None
        return base_url.rstrip("/") + PROFILE_PATH.format(person_id=self.person_id)


def _display_name(row) -> str:
    """What the advisor should see on screen before answering.

    The preferred name wins when the record holds one — a client who goes by "Bill" should not be
    greeted as "William" because that is what the tax return says.
    """
    for candidate in (row.preferred_name, row.full_name):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    parts = [p for p in (row.first_name, row.last_name) if p and str(p).strip()]
    return " ".join(str(p).strip() for p in parts) or f"Client #{row.id}"


def lookup_by_number(conn, raw_number) -> LookupResult:
    """Match one number against ``people.normalized_phone``. Never raises for an unknown caller.

    INACTIVE PEOPLE ARE EXCLUDED. A former client's record still holds their number, and popping
    it would present a closed relationship as a live one to whoever answers; an inactive match is
    counted as no match at all, so the advisor simply sees an unknown caller.
    """
    normalized = normalize_phone(raw_number)
    if not normalized:
        return LookupResult(match_count=0)

    rows = conn.execute(
        select(people.c.id, people.c.household_id, people.c.full_name, people.c.preferred_name,
               people.c.first_name, people.c.last_name, people.c.primary_email)
        .where(people.c.normalized_phone == normalized, people.c.active.is_(True))
        # Deterministic, so a repeated lookup on an ambiguous number reports the same count and
        # never the same "first" person twice by accident of row order.
        .order_by(people.c.id)
    ).all()

    if len(rows) != 1:
        # Ambiguous or unknown: the count, and deliberately nothing else.
        return LookupResult(match_count=len(rows))

    row = rows[0]
    return LookupResult(
        match_count=1, person_id=row.id, display_name=_display_name(row),
        first_name=row.first_name, last_name=row.last_name,
        email=row.primary_email, household_id=row.household_id,
    )
