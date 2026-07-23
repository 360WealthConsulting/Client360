"""Policy registry (Phase D.32) — discovery, versioning, lifecycle, ownership, dependency graph.

The durable, discoverable catalog of the declarative decision policies (``runtime_policies``). It
supports policy discovery (list/get/by-category), versioning + lifecycle status (active / in_domain /
deprecated / retired), ownership + category, the dependency graph between policies, and deprecation
tracking. Major lifecycle events (policy deprecated / retired / registry updated) record to the D.28
``runtime_events`` append-only ledger (entity_type ``policy``); routine policy evaluations are never
recorded. This performs no configuration evaluation — the runtime engine remains the sole evaluator.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.runtime_policy_tables import POLICY_STATUSES
from app.db import engine, runtime_policies
from app.services.runtime.common import now, record_event, write_audit

from .definitions import DECISION_AREAS, POLICY_DEFINITIONS


def list_policies(*, status=None, category=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(runtime_policies).order_by(runtime_policies.c.category, runtime_policies.c.code)
        if status:
            stmt = stmt.where(runtime_policies.c.status == status)
        if category:
            stmt = stmt.where(runtime_policies.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_policy(code: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_policies).where(runtime_policies.c.code == code)).mappings().first()
        return dict(row) if row else None


def dependency_graph() -> dict:
    """The policy dependency graph ``{code: [depends_on, …]}`` (from the registry)."""
    return {p["code"]: list(p.get("depends_on") or []) for p in list_policies()}


def coverage() -> dict:
    """Registry coverage. ``in_domain`` policies are documented exceptions (enforcement stays in the
    owning domain) and are excluded from the migratable denominator — mirroring how D.30 excludes
    deterministic behaviors."""
    with engine.connect() as c:
        rows = list(c.execute(select(runtime_policies.c.status,
                                     func.count().label("n")).group_by(runtime_policies.c.status)).mappings())
        cats = set(c.scalars(select(runtime_policies.c.category).where(
            runtime_policies.c.status.in_(("active", "in_domain")))))
    counts = {r["status"]: r["n"] for r in rows}
    active = counts.get("active", 0)
    in_domain = counts.get("in_domain", 0)
    deprecated = counts.get("deprecated", 0)
    retired = counts.get("retired", 0)
    total = active + in_domain + deprecated + retired
    migratable = active + counts.get("legacy", 0)
    adoption_pct = round((active / migratable) * 100, 1) if migratable else 100.0
    areas_covered = len(cats & set(DECISION_AREAS))
    coverage_pct = round((areas_covered / len(DECISION_AREAS)) * 100, 1) if DECISION_AREAS else 100.0
    return {"total": total, "active": active, "in_domain": in_domain, "deprecated": deprecated,
            "retired": retired, "migratable": migratable, "adoption_pct": adoption_pct,
            "decision_areas": len(DECISION_AREAS), "areas_covered": areas_covered,
            "coverage_pct": coverage_pct}


def adoption(principal=None) -> dict:
    """Combined policy-adoption view: durable registry coverage + live in-process execution counters."""
    from .engine import evaluation_stats
    cov = coverage()
    return {"registry": cov, "execution": evaluation_stats(), "adoption_pct": cov["adoption_pct"],
            "coverage_pct": cov["coverage_pct"]}


# --- lifecycle ---------------------------------------------------------------

def _set_status(code: str, status: str, *, event_type, action, actor_user_id=None, reason=None) -> dict:
    if status not in POLICY_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with engine.begin() as c:
        p = c.execute(select(runtime_policies).where(runtime_policies.c.code == code)).mappings().first()
        if p is None:
            raise ValueError(f"unknown policy {code!r}")
        values = {"status": status, "updated_at": now()}
        if status == "deprecated":
            values["deprecated_at"] = now()
            values["deprecated_reason"] = reason
        row = dict(c.execute(runtime_policies.update().where(runtime_policies.c.id == p["id"])
                             .values(**values).returning(*runtime_policies.c)).mappings().one())
        record_event(c, entity_type="policy", entity_id=row["id"], event_type=event_type,
                     from_status=p["status"], to_status=status, actor_user_id=actor_user_id,
                     payload={"code": code, "reason": reason})
    write_audit(action, entity_type="policy", entity_id=row["id"], actor_user_id=actor_user_id,
                metadata={"code": code})
    return row


def deprecate(code: str, *, reason=None, actor_user_id=None) -> dict:
    """Mark a policy deprecated (superseded); records a firm-level ``policy_deprecated`` event."""
    return _set_status(code, "deprecated", event_type="policy_deprecated", action="policy.deprecated",
                       actor_user_id=actor_user_id, reason=reason)


def retire(code: str, *, actor_user_id=None) -> dict:
    """Mark a policy retired (removed from the decision path)."""
    return _set_status(code, "retired", event_type="policy_retired", action="policy.retired",
                       actor_user_id=actor_user_id)


def record_registry_updated(*, actor_user_id=None) -> dict:
    """Record a firm-level ``policy_registry_updated`` event (registry reconciled/coverage recomputed)."""
    cov = coverage()
    with engine.begin() as c:
        record_event(c, entity_type="policy", entity_id=0, event_type="policy_registry_updated",
                     actor_user_id=actor_user_id, payload=cov)
    write_audit("policy.registry_updated", entity_type="policy", entity_id=0,
                actor_user_id=actor_user_id, metadata={"coverage_pct": cov["coverage_pct"]})
    return cov


def audit_history(principal=None, *, code=None, limit=100) -> list[dict]:
    """Policy lifecycle events from the D.28 runtime_events ledger (entity_type=policy)."""
    from app.db import runtime_events
    with engine.connect() as c:
        stmt = select(runtime_events).where(runtime_events.c.entity_type == "policy")
        if code is not None:
            p = get_policy(code)
            if p:
                stmt = stmt.where(runtime_events.c.entity_id == p["id"])
        return [dict(r) for r in c.execute(
            stmt.order_by(runtime_events.c.id.desc()).limit(min(500, max(1, limit)))).mappings()]


def definitions_index() -> dict:
    """The in-code policy definitions keyed by code (for governance reconciliation)."""
    return dict(POLICY_DEFINITIONS)
