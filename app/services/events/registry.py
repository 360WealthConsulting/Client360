"""Event registry (Phase D.34) — discovery, versioning, lifecycle, producers, subscribers.

The durable, discoverable catalog of the domain-event contracts (``domain_event_contracts``) and the
subscription registry (``domain_event_subscriptions``). Supports contract discovery (list/get/by-
category), versioning, lifecycle status (active / deprecated / retired), ownership + producer, the
subscriber set per event, dependency, and deprecation tracking. Major lifecycle events (contract
deprecated / retired) record to the shared audit hash-chain; routine published events are never
recorded here. This owns no event data (the outbox owns the event log) and performs no delivery.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.event_tables import CONTRACT_STATUSES
from app.db import domain_event_contracts, domain_event_subscriptions, engine

from .common import now, write_audit
from .contracts import DOMAINS, EVENT_CONTRACTS


def list_contracts(*, status=None, category=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(domain_event_contracts).order_by(
            domain_event_contracts.c.category, domain_event_contracts.c.event_type)
        if status:
            stmt = stmt.where(domain_event_contracts.c.status == status)
        if category:
            stmt = stmt.where(domain_event_contracts.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_contract(event_type: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(domain_event_contracts).where(
            domain_event_contracts.c.event_type == event_type)).mappings().first()
        return dict(row) if row else None


def list_subscriptions(*, event_type=None, status=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(domain_event_subscriptions).order_by(
            domain_event_subscriptions.c.event_type, domain_event_subscriptions.c.consumer)
        if event_type:
            stmt = stmt.where(domain_event_subscriptions.c.event_type == event_type)
        if status:
            stmt = stmt.where(domain_event_subscriptions.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def subscribers_of(event_type: str) -> list[str]:
    return [s["consumer"] for s in list_subscriptions(event_type=event_type, status="active")]


def dependency_graph() -> dict:
    """The event dependency (causation) graph ``{event_type: [depends_on, …]}``."""
    return {c["event_type"]: list(c.get("depends_on") or []) for c in list_contracts()}


def coverage() -> dict:
    """Registry coverage. Consumer coverage = active contracts with ≥1 active subscription ÷ active;
    producer coverage = active contracts with a declared producer ÷ active. Domain coverage = the event
    domains with a registered contract ÷ the identified event domains."""
    with engine.connect() as c:
        rows = list(c.execute(select(domain_event_contracts.c.status,
                    func.count().label("n")).group_by(domain_event_contracts.c.status)).mappings())
        active_rows = [dict(r) for r in c.execute(select(domain_event_contracts).where(
            domain_event_contracts.c.status == "active")).mappings()]
        cats = {r["category"] for r in active_rows}
        subbed = set(c.scalars(select(domain_event_subscriptions.c.event_type).where(
            domain_event_subscriptions.c.status == "active")))
        sub_total = c.scalar(select(func.count()).select_from(domain_event_subscriptions)) or 0
        sub_active = c.scalar(select(func.count()).select_from(domain_event_subscriptions)
                              .where(domain_event_subscriptions.c.status == "active")) or 0
    counts = {r["status"]: r["n"] for r in rows}
    active = counts.get("active", 0)
    deprecated = counts.get("deprecated", 0)
    retired = counts.get("retired", 0)
    total = active + deprecated + retired
    with_consumer = sum(1 for r in active_rows if r["event_type"] in subbed)
    with_producer = sum(1 for r in active_rows if r.get("producer"))
    domains_covered = len(cats & set(DOMAINS))
    return {"total": total, "active": active, "deprecated": deprecated, "retired": retired,
            "subscriptions": sub_total, "active_subscriptions": sub_active,
            "consumer_coverage_pct": round((with_consumer / active) * 100, 1) if active else 100.0,
            "producer_coverage_pct": round((with_producer / active) * 100, 1) if active else 100.0,
            "domains": len(DOMAINS), "domains_covered": domains_covered,
            "coverage_pct": round((domains_covered / len(DOMAINS)) * 100, 1) if DOMAINS else 100.0}


def adoption(principal=None) -> dict:
    from .common import stats
    cov = coverage()
    return {"registry": cov, "publication": stats(), "coverage_pct": cov["coverage_pct"]}


# --- lifecycle ---------------------------------------------------------------

def _set_status(event_type, status, *, actor_user_id=None, reason=None) -> dict:
    if status not in CONTRACT_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with engine.begin() as c:
        row = c.execute(select(domain_event_contracts).where(
            domain_event_contracts.c.event_type == event_type)).mappings().first()
        if row is None:
            raise ValueError(f"unknown event contract {event_type!r}")
        values = {"status": status, "updated_at": now()}
        if status == "deprecated":
            values["deprecated_at"] = now()
            values["deprecated_reason"] = reason
        out = dict(c.execute(domain_event_contracts.update().where(
            domain_event_contracts.c.id == row["id"]).values(**values).returning(
                *domain_event_contracts.c)).mappings().one())
    write_audit(f"domain_event.contract_{status}", entity_id=out["id"], actor_user_id=actor_user_id,
                metadata={"event_type": event_type})
    return out


def deprecate(event_type, *, reason=None, actor_user_id=None) -> dict:
    return _set_status(event_type, "deprecated", actor_user_id=actor_user_id, reason=reason)


def retire(event_type, *, actor_user_id=None) -> dict:
    return _set_status(event_type, "retired", actor_user_id=actor_user_id)


def contracts_index() -> dict:
    """The in-code executable contracts keyed by event type (for governance reconciliation)."""
    return dict(EVENT_CONTRACTS)
