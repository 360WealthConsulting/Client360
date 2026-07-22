"""Security reviews (Phase D.25) — the Automation entry point (metadata only).

``run_due_reviews`` is the deterministic job Automation invokes (``security_review`` dispatch): it
flags secrets past their rotation date, certificates near/after expiry, and records the results as
security findings + ledger events. It performs **no cryptographic operation** and mutates no
canonical record — it only records security METADATA. Automation executes; Security owns the
metadata (ADR-027 / D.22 reuse).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from app.db import engine
from app.db import security_certificate_references as certs_t
from app.db import security_secret_references as secrets_t

from . import incidents
from .common import now, record_event

_EXPIRY_WARN_DAYS = 30


def run_due_reviews(principal, *, actor_user_id=None) -> dict:
    """Flag overdue secret rotations and expiring/expired certificates as findings (idempotent per
    run — one finding per due item). Returns a small summary. Records metadata only."""
    ts = now()
    warn_cutoff = ts + timedelta(days=_EXPIRY_WARN_DAYS)
    secrets_flagged = 0
    certs_flagged = 0

    with engine.connect() as c:
        overdue_secrets = list(c.execute(select(secrets_t.c.id, secrets_t.c.code, secrets_t.c.name)
                                         .where(secrets_t.c.status == "active",
                                                secrets_t.c.next_rotation_at.is_not(None),
                                                secrets_t.c.next_rotation_at <= ts)).mappings())
        expiring_certs = list(c.execute(select(certs_t.c.id, certs_t.c.code, certs_t.c.name,
                                               certs_t.c.not_after, certs_t.c.status)
                                        .where(certs_t.c.status.in_(("valid", "expiring")),
                                               certs_t.c.not_after.is_not(None),
                                               certs_t.c.not_after <= warn_cutoff)).mappings())

    for s in overdue_secrets:
        incidents.create_finding(principal, title=f"Secret rotation overdue: {s['name']}",
                                 finding_type="rotation_overdue", severity="high", source="automation",
                                 detail=f"Secret reference {s['code']} is past its rotation date.",
                                 secret_reference_id=s["id"], actor_user_id=actor_user_id)
        secrets_flagged += 1

    for cert in expiring_certs:
        expired = cert["not_after"] is not None and cert["not_after"] <= ts
        # Deterministically reflect the certificate's status (expiring vs expired).
        with engine.begin() as c:
            c.execute(update(certs_t).where(certs_t.c.id == cert["id"]).values(
                status=("expired" if expired else "expiring"), updated_at=now()))
            record_event(c, entity_type="certificate", entity_id=cert["id"],
                         event_type=("certificate_expired" if expired else "certificate_expiring"),
                         actor_user_id=actor_user_id)
        incidents.create_finding(principal,
                                 title=f"Certificate {'expired' if expired else 'expiring'}: {cert['name']}",
                                 finding_type="certificate_expiry",
                                 severity=("critical" if expired else "medium"), source="automation",
                                 detail=f"Certificate {cert['code']} not_after={cert['not_after']}.",
                                 certificate_reference_id=cert["id"], actor_user_id=actor_user_id)
        certs_flagged += 1

    return {"secrets_flagged": secrets_flagged, "certificates_flagged": certs_flagged,
            "reviewed": len(overdue_secrets) + len(expiring_certs)}
