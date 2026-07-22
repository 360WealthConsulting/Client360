"""API clients, usage & rate limits (Phase D.24) — metadata only.

Models API-platform metadata: clients (with scopes + rate limits + a credential reference — never a
plaintext key), aggregated usage windows, and rate-limit configuration. This is metadata only — it
does **not** change the authentication middleware and stores **no plaintext API keys** (keys are
credential references / Fernet ciphertext). Usage is recorded as aggregated windows (not per-request
events, per the "do not emit events for every API request" rule).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.integration_tables import API_CLIENT_STATUSES
from app.db import engine
from app.db import integration_api_clients as clients_t
from app.db import integration_api_usage as usage_t

from .common import IntegrationError, IntegrationNotFound, now, record_event

# --- API clients -------------------------------------------------------------

def list_api_clients(*, status=None):
    with engine.connect() as c:
        stmt = select(clients_t).order_by(clients_t.c.code)
        if status:
            stmt = stmt.where(clients_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_api_client(principal, client_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(clients_t).where(clients_t.c.id == client_id)).mappings().first()
        return dict(row) if row else None


def create_api_client(principal, *, code, name, client_type="internal", scopes=None,
                      credential_reference_id=None, rate_limit_per_minute=None, rate_limit_per_day=None,
                      description=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise IntegrationError("code and name are required")
    with engine.begin() as c:
        if c.scalar(select(clients_t.c.id).where(clients_t.c.code == code)) is not None:
            raise IntegrationError(f"API client code {code!r} already exists")
        row = c.execute(clients_t.insert().values(
            code=code, name=name.strip(), client_type=client_type, status="active", scopes=scopes,
            credential_reference_id=credential_reference_id, rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_per_day=rate_limit_per_day, description=description,
            created_by_user_id=actor_user_id).returning(*clients_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="api_client", entity_id=row["id"], event_type="api_client_created",
                     actor_user_id=actor_user_id, payload={"client_type": client_type})
        return row


def set_api_client_status(principal, client_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in API_CLIENT_STATUSES:
        raise IntegrationError(f"invalid status {status!r}")
    with engine.begin() as c:
        client = c.execute(select(clients_t).where(clients_t.c.id == client_id)).mappings().first()
        if client is None:
            raise IntegrationNotFound(str(client_id))
        row = c.execute(clients_t.update().where(clients_t.c.id == client_id)
                        .values(status=status, updated_at=now()).returning(*clients_t.c)).mappings().one()
        record_event(c, entity_type="api_client", entity_id=client_id, event_type=f"api_client_{status}",
                     from_status=client["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


def rate_limit_for(client_id: int) -> dict:
    with engine.connect() as c:
        row = c.execute(select(clients_t.c.rate_limit_per_minute, clients_t.c.rate_limit_per_day)
                        .where(clients_t.c.id == client_id)).mappings().first()
    if row is None:
        raise IntegrationNotFound(str(client_id))
    return {"per_minute": row["rate_limit_per_minute"], "per_day": row["rate_limit_per_day"]}


# --- usage (aggregated windows; not per-request) -----------------------------

def record_usage(principal, api_client_id: int, *, endpoint=None, method=None, request_count=0,
                 error_count=0, window_start=None, window_end=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(clients_t.c.id).where(clients_t.c.id == api_client_id)) is None:
            raise IntegrationNotFound(str(api_client_id))
        row = c.execute(usage_t.insert().values(
            api_client_id=api_client_id, endpoint=endpoint, method=method,
            request_count=int(request_count), error_count=int(error_count),
            window_start=(window_start or now()), window_end=(window_end or now()))
            .returning(*usage_t.c)).mappings().one()
        return dict(row)


def list_usage(*, api_client_id=None):
    with engine.connect() as c:
        stmt = select(usage_t).order_by(usage_t.c.id.desc())
        if api_client_id is not None:
            stmt = stmt.where(usage_t.c.api_client_id == api_client_id)
        return [dict(r) for r in c.execute(stmt.limit(200)).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        active_clients = c.scalar(select(func.count()).select_from(clients_t)
                                  .where(clients_t.c.status == "active")) or 0
        requests = c.scalar(select(func.coalesce(func.sum(usage_t.c.request_count), 0))) or 0
    return {"active_api_clients": active_clients, "api_requests": int(requests)}
