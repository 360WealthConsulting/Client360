"""Find existing people who might already BE the detected prospect. Read-only.

Pure SELECT. This module creates nothing, updates nothing, and merges nothing — it answers "have we
already got this person?" and hands the answer to a human. Auto-merging on an email seen in a
forwarded signature is exactly the mistake that produces duplicate or wrongly-merged client records.

Normalisation, chosen deliberately
----------------------------------
The repository contains two different ``normalize_email`` implementations:

* ``app.security.identity_utils.normalize_email`` — ``strip().casefold()``
* ``app.matching.matcher.normalize_email`` — additionally folds gmail dots and ``+`` suffixes

This module uses the FIRST one, because it is the function that WRITES ``people.normalized_email``
(``app.services.people.update_person_contact`` imports it via ``app.security.service``). Comparing
with the gmail-folding variant would normalise the needle differently from the haystack, so
``j.doe@gmail.com`` would silently fail to match a stored ``j.doe@gmail.com``. Matching the writer is
the only way an exact-match lookup is actually exact. Phone uses
``app.services.people._normalize_phone`` for the same reason — it is what writes
``people.normalized_phone``. A test asserts both agree with their writers.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select

from app.db import engine, people
from app.security.identity_utils import normalize_email
from app.services.people import _normalize_phone
from app.services.person_names import person_row_display_name

_COLUMNS = (people.c.id, people.c.full_name, people.c.first_name, people.c.last_name,
            people.c.primary_email, people.c.normalized_email, people.c.primary_phone,
            people.c.normalized_phone, people.c.contact_type, people.c.active)

#: Ordered strongest-first. The first tier that returns anything wins; tiers are never merged, so a
#: weak name hit can never dilute a strong email hit.
MATCH_TIERS = ("email", "phone", "name")


def _row(r):
    return {
        "person_id": r["id"],
        "display_name": person_row_display_name(r, fallback=f"person {r['id']}"),
        "email": r["primary_email"],
        "phone": r["primary_phone"],
        "contact_type": r["contact_type"],
        "active": r["active"],
        "workspace_url": f"/client/{r['id']}",
    }


def find_matches(*, email=None, phone=None, name=None, limit: int = 10) -> dict:
    """Existing people who may already be this prospect.

    Returns ``{"strategy", "matches", "outcome"}`` where outcome is ``none`` / ``one`` / ``multiple``
    — the three cases the UI has to distinguish. ``strategy`` names the tier that produced the rows
    so the page can say WHY something matched.
    """
    norm_email = normalize_email(email) or None
    norm_phone = _normalize_phone(phone)
    norm_name = " ".join((name or "").split()).casefold() or None

    with engine.connect() as c:
        rows = []
        strategy = None

        if norm_email:
            rows = c.execute(select(*_COLUMNS).where(
                func.lower(people.c.normalized_email) == norm_email).limit(limit)).mappings().all()
            strategy = "email" if rows else None

        if not rows and norm_phone:
            rows = c.execute(select(*_COLUMNS).where(
                people.c.normalized_phone == norm_phone).limit(limit)).mappings().all()
            strategy = "phone" if rows else None

        if not rows and norm_name:
            # Exact on the whole name, or on the first/last pair — never a LIKE/prefix sweep, which
            # would return a browsable slice of the client book from a typed query string.
            conds = [func.lower(func.trim(people.c.full_name)) == norm_name]
            parts = norm_name.split()
            if len(parts) >= 2:
                conds.append(func.lower(people.c.first_name + " " + people.c.last_name) == norm_name)
                conds.append(func.lower(people.c.last_name) == parts[-1])
            rows = c.execute(select(*_COLUMNS).where(or_(*conds)).limit(limit)).mappings().all()
            strategy = "name" if rows else None

    matches = [_row(r) for r in rows]
    outcome = "none" if not matches else ("one" if len(matches) == 1 else "multiple")
    return {"strategy": strategy, "matches": matches, "outcome": outcome}


def match_for_candidate(candidate: dict, *, limit: int = 10) -> dict:
    """``find_matches`` for an ``forwarded_email.extract_candidate`` result."""
    return find_matches(email=candidate.get("candidate_email"),
                        phone=candidate.get("candidate_phone"),
                        name=candidate.get("candidate_name"), limit=limit)
