"""Webhook endpoints, subscriptions & deliveries (Phase D.24) — metadata only.

Models outbound webhook metadata: endpoints (with Fernet-encrypted signing secrets — never
plaintext), subscriptions (event_type → endpoint), and deliveries (status + attempts + retry
metadata + a computed HMAC signature). **No outbound HTTP is performed this phase** (mirroring the
D.18 delivery / D.21 export metadata-only precedent) and there is **no external broker**. Signature
computation decrypts the signing secret only in-process to sign a reference payload; the secret is
never returned or logged.
"""
from __future__ import annotations

import hashlib
import hmac

from sqlalchemy import and_, func, select

from app.database.integration_tables import (
    DELIVERY_STATUSES,
    SIGNING_ALGORITHMS,
    WEBHOOK_DIRECTIONS,
)
from app.db import engine
from app.db import integration_webhook_deliveries as deliveries_t
from app.db import integration_webhook_endpoints as endpoints_t
from app.db import integration_webhook_subscriptions as subs_t

from .common import (
    IntegrationError,
    IntegrationNotFound,
    encrypt_secret,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

# --- endpoints ---------------------------------------------------------------

def list_endpoints(*, active_only=False):
    with engine.connect() as c:
        stmt = select(endpoints_t).order_by(endpoints_t.c.code)
        if active_only:
            stmt = stmt.where(endpoints_t.c.active.is_(True))
        # Never expose the signing secret.
        return [{k: v for k, v in dict(r).items() if k != "signing_secret_ciphertext"}
                for r in c.execute(stmt).mappings()]


def get_endpoint(principal, endpoint_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(endpoints_t).where(endpoints_t.c.id == endpoint_id)).mappings().first()
    if row is None:
        return None
    return {k: v for k, v in dict(row).items() if k != "signing_secret_ciphertext"}


def create_endpoint(principal, *, code, name, direction="outbound", url=None,
                    signing_algorithm="hmac_sha256", signing_secret=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise IntegrationError("code and name are required")
    if direction not in WEBHOOK_DIRECTIONS:
        raise IntegrationError(f"invalid direction {direction!r}")
    if signing_algorithm not in SIGNING_ALGORITHMS:
        raise IntegrationError(f"invalid signing_algorithm {signing_algorithm!r}")
    with engine.begin() as c:
        if c.scalar(select(endpoints_t.c.id).where(endpoints_t.c.code == code)) is not None:
            raise IntegrationError(f"endpoint code {code!r} already exists")
        row = c.execute(endpoints_t.insert().values(
            code=code, name=name.strip(), direction=direction, url=url,
            signing_algorithm=signing_algorithm, signing_secret_ciphertext=encrypt_secret(signing_secret),
            verification_status="unverified", active=True, created_by_user_id=actor_user_id)
            .returning(*endpoints_t.c)).mappings().one()
        row = dict(row)
    return {k: v for k, v in row.items() if k != "signing_secret_ciphertext"}


def _compute_signature(endpoint: dict, message: str) -> str | None:
    """Compute an HMAC signature using the decrypted signing secret (in-process only). Returns a hex
    digest, or None when no secret / no signing. The plaintext secret is never returned/logged."""
    if endpoint["signing_algorithm"] == "none" or not endpoint.get("signing_secret_ciphertext"):
        return None
    try:
        from app.security.integration_crypto import decrypt
        secret = decrypt(endpoint["signing_secret_ciphertext"]).encode("utf-8")
    except Exception:
        return None
    digestmod = hashlib.sha256 if endpoint["signing_algorithm"] == "hmac_sha256" else hashlib.sha1
    return hmac.new(secret, message.encode("utf-8"), digestmod).hexdigest()


def verify_endpoint(principal, endpoint_id: int, *, actor_user_id=None) -> dict:
    """Deterministically verify an endpoint's signing configuration (metadata): a signable endpoint
    with a secret computes a challenge signature and is marked verified."""
    with engine.begin() as c:
        ep = c.execute(select(endpoints_t).where(endpoints_t.c.id == endpoint_id)).mappings().first()
        if ep is None:
            raise IntegrationNotFound(str(endpoint_id))
        ep = dict(ep)
        signature = _compute_signature(ep, f"verify:{endpoint_id}")
        status = "verified" if (ep["signing_algorithm"] == "none" or signature) else "failed"
        row = c.execute(endpoints_t.update().where(endpoints_t.c.id == endpoint_id).values(
            verification_status=status, verified_at=(now() if status == "verified" else None),
            updated_at=now()).returning(*endpoints_t.c)).mappings().one()
        record_event(c, entity_type="webhook_endpoint", entity_id=endpoint_id,
                     event_type=f"webhook_{status}", to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    if status == "verified":
        write_audit("integration.webhook_verified", entity_type="webhook_endpoint",
                    entity_id=endpoint_id, actor_user_id=actor_user_id)
        publish_timeline(row, "webhook_verified", title=f"Webhook verified: {ep['name']}")
    return {"id": endpoint_id, "verification_status": status}


# --- subscriptions -----------------------------------------------------------

def create_subscription(principal, endpoint_id: int, *, event_type, filter=None, actor_user_id=None) -> dict:
    event_type = (event_type or "").strip()
    if not event_type:
        raise IntegrationError("event_type is required")
    with engine.begin() as c:
        if c.scalar(select(endpoints_t.c.id).where(endpoints_t.c.id == endpoint_id)) is None:
            raise IntegrationError("endpoint not found")
        if c.scalar(select(subs_t.c.id).where(subs_t.c.endpoint_id == endpoint_id,
                                              subs_t.c.event_type == event_type)) is not None:
            raise IntegrationError("subscription already exists for that endpoint and event")
        row = c.execute(subs_t.insert().values(
            endpoint_id=endpoint_id, event_type=event_type, filter=filter, active=True,
            created_by_user_id=actor_user_id).returning(*subs_t.c)).mappings().one()
        return dict(row)


def list_subscriptions(*, endpoint_id=None):
    with engine.connect() as c:
        stmt = select(subs_t).order_by(subs_t.c.id.desc())
        if endpoint_id is not None:
            stmt = stmt.where(subs_t.c.endpoint_id == endpoint_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


# --- deliveries (metadata only; no outbound HTTP) ----------------------------

def record_delivery(principal, *, event_type, endpoint_id=None, subscription_id=None, event_id=None,
                    status="pending", response_code=None, last_error=None, actor_user_id=None) -> dict:
    if status not in DELIVERY_STATUSES:
        raise IntegrationError(f"invalid status {status!r}")
    with engine.begin() as c:
        signature = None
        if endpoint_id is not None:
            ep = c.execute(select(endpoints_t).where(endpoints_t.c.id == endpoint_id)).mappings().first()
            if ep is not None:
                signature = _compute_signature(dict(ep), f"{event_type}:{event_id or ''}")
        row = c.execute(deliveries_t.insert().values(
            subscription_id=subscription_id, endpoint_id=endpoint_id, event_type=event_type,
            event_id=event_id, status=status, attempts=(1 if status != "pending" else 0),
            response_code=response_code, signature=signature, last_error=last_error,
            delivered_at=(now() if status == "delivered" else None)).returning(*deliveries_t.c)).mappings().one()
        record_event(c, entity_type="webhook_delivery", entity_id=dict(row)["id"],
                     event_type=f"delivery_{status}", to_status=status, actor_user_id=actor_user_id)
        return dict(row)


def list_deliveries(*, endpoint_id=None, status=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        conds = []
        if endpoint_id is not None:
            conds.append(deliveries_t.c.endpoint_id == endpoint_id)
        if status:
            conds.append(deliveries_t.c.status == status)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(deliveries_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(deliveries_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(deliveries_t.c.id.desc()).limit(page_size).offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size}


def metrics(principal) -> dict:
    with engine.connect() as c:
        failed = c.scalar(select(func.count()).select_from(deliveries_t)
                          .where(deliveries_t.c.status.in_(("failed", "dead")))) or 0
        unverified = c.scalar(select(func.count()).select_from(endpoints_t)
                              .where(endpoints_t.c.verification_status != "verified",
                                     endpoints_t.c.active.is_(True))) or 0
    return {"webhook_failures": failed, "unverified_endpoints": unverified}
