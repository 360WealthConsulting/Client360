"""Reviewer authority lookup (Phase D.7).

Establishes whether a principal may make a FINAL compliance decision for a given
governed rule / policy gate. Authority is a recorded fact in ``reviewer_authorities``
(seeded EMPTY this phase). The catalog is not administered here — until an authorized
reviewer is recorded, this returns ``None`` for everyone, so final approval stays
blocked. No reviewer is ever fabricated or inferred from a job-title string.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db import engine, reviewer_authorities, users


def reviewer_authority(principal_id: int | None, *, rule_id: str, policy_gate: str,
                       today: date | None = None) -> dict | None:
    """Return an active ``reviewer_authorities`` record that covers this rule/gate for
    the principal, or ``None``. An empty catalog (D.7's default) always yields ``None``.

    A record confers authority only when ALL of the following hold (D.8):
    ``status = 'active'`` (draft/suspended/revoked/superseded never qualify), the
    principal's user is **active**, ``effective_date`` has been reached, ``expiration_date``
    has not passed (an expired-by-date active record is treated as expired — computed,
    not mutated), and ``authority_scope`` contains the ``rule_id``, the ``policy_gate``,
    or the wildcard ``"*"``. An empty/ambiguous scope never confers unrestricted
    authority (an empty scope matches nothing).
    """
    if principal_id is None:
        return None
    today = today or date.today()
    with engine.connect() as conn:
        # The principal must be an active user for the authority to be usable.
        if conn.scalar(select(users.c.id).where(
                users.c.id == principal_id, users.c.status == "active")) is None:
            return None
        rows = conn.execute(
            select(reviewer_authorities).where(
                reviewer_authorities.c.principal_id == principal_id,
                reviewer_authorities.c.status == "active",
            )
        ).mappings().all()
    for row in rows:
        eff, exp = row["effective_date"], row["expiration_date"]
        if eff is not None and eff > today:
            continue
        if exp is not None and exp < today:
            continue
        scope = row["authority_scope"] or []
        if "*" in scope or rule_id in scope or policy_gate in scope:
            return dict(row)
    return None
