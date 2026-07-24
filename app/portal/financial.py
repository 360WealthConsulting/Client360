"""Client Portal financial summary (Phase D.43) — a minimized, masked, read-only view over the
authoritative ``accounts`` records. The portal never owns portfolio data and never mutates it; this module
only reads what the client is entitled to see, masks account numbers to last-4, marks freshness, and fails
closed. Gated by ``portal.financial_summary_enabled`` AND the grant ``financial`` permission AND person
scope. Fields served here are declared in the visibility registry (``financial.*``).
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import accounts, engine
from app.portal import stats
from app.portal.gate import gate
from app.portal.service import portal_scope
from app.portal.visibility import mask_account_number


def financial_summary(principal):
    """Return a masked, per-account financial summary for every account the portal account is entitled to
    (person-scoped, ``financial`` grant permission). Fails closed: if the feature gate is off or the grant
    does not allow ``financial``, returns an empty, disabled summary rather than raising."""
    if not gate("portal.financial_summary_enabled"):
        return {"enabled": False, "accounts": [], "total_value": None}

    scope = portal_scope(principal.account_id, permission="financial")
    person_ids = scope["person_ids"]
    if not person_ids:
        stats.note("scope_denials")
        return {"enabled": True, "accounts": [], "total_value": None}

    with engine.connect() as connection:
        rows = connection.execute(
            select(
                accounts.c.id, accounts.c.custodian, accounts.c.account_number, accounts.c.account_name,
                accounts.c.registration_type, accounts.c.total_value, accounts.c.status,
                accounts.c.last_imported_at,
            ).where(accounts.c.person_id.in_(person_ids), accounts.c.status != "closed")
            .order_by(accounts.c.total_value.desc().nullslast())
        ).mappings().all()

    out = []
    total = 0
    for r in rows:
        value = r["total_value"]
        if value is not None:
            total += value
        out.append({
            "account_name": r["account_name"] or r["registration_type"] or "Account",
            "custodian": r["custodian"],
            "account_number_masked": mask_account_number(r["account_number"]),
            "registration_type": r["registration_type"],
            "current_value": None if value is None else float(value),
            # Freshness marker so the client sees data currency, never a live/authoritative claim.
            "as_of": r["last_imported_at"].date().isoformat() if r["last_imported_at"] else None,
        })
    stats.note("composition", section="financial")
    return {"enabled": True, "accounts": out, "total_value": float(total) if out else None}
