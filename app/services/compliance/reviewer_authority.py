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

from app.db import engine, reviewer_authorities


def reviewer_authority(principal_id: int | None, *, rule_id: str, policy_gate: str,
                       today: date | None = None) -> dict | None:
    """Return an active ``reviewer_authorities`` record that covers this rule/gate for
    the principal, or ``None``. An empty catalog (this phase) always yields ``None``.

    ``authority_scope`` is a list of tokens; a record covers a rule when its scope
    contains the ``rule_id``, the ``policy_gate``, or the wildcard ``"*"``.
    """
    if principal_id is None:
        return None
    today = today or date.today()
    with engine.connect() as conn:
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
