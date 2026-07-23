"""Workflow registry (Phase D.33) — discovery, ownership, lifecycle, versions, dependencies.

The durable, discoverable catalog of the declarative workflow definitions (``orchestration_definitions``).
Supports discovery (list/get/by-category), ownership + category, lifecycle status (active / in_domain /
deprecated / retired), versioning, the dependency graph between definitions, and deprecation tracking.
Major lifecycle events (definition deprecated / retired / registry updated) record to the D.33
``orchestration_events`` scope via the shared audit; routine transitions are never recorded here. This
performs no orchestration itself — the engine drives execution.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.orchestration_tables import DEFINITION_STATUSES
from app.db import engine as db
from app.db import orchestration_definitions

from .common import now, write_audit
from .definitions import DOMAINS, ORCHESTRATION_DEFINITIONS


def list_definitions(*, status=None, category=None) -> list[dict]:
    with db.connect() as c:
        stmt = select(orchestration_definitions).order_by(
            orchestration_definitions.c.category, orchestration_definitions.c.code)
        if status:
            stmt = stmt.where(orchestration_definitions.c.status == status)
        if category:
            stmt = stmt.where(orchestration_definitions.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_definition(code: str) -> dict | None:
    with db.connect() as c:
        row = c.execute(select(orchestration_definitions).where(
            orchestration_definitions.c.code == code)).mappings().first()
        return dict(row) if row else None


def dependency_graph() -> dict:
    """The definition dependency graph ``{code: [depends_on, …]}`` (from the registry)."""
    return {d["code"]: list(d.get("depends_on") or []) for d in list_definitions()}


def coverage() -> dict:
    """Orchestration coverage. ``in_domain`` definitions are documented exceptions (the lifecycle stays
    authoritative in the owning domain) and are excluded from the migratable denominator — mirroring how
    D.30 excludes deterministic behaviors and D.32 excludes in-domain policies."""
    with db.connect() as c:
        rows = list(c.execute(select(orchestration_definitions.c.status,
                    func.count().label("n")).group_by(orchestration_definitions.c.status)).mappings())
        cats = set(c.scalars(select(orchestration_definitions.c.category).where(
            orchestration_definitions.c.status.in_(("active", "in_domain")))))
    counts = {r["status"]: r["n"] for r in rows}
    active = counts.get("active", 0)
    in_domain = counts.get("in_domain", 0)
    deprecated = counts.get("deprecated", 0)
    retired = counts.get("retired", 0)
    total = active + in_domain + deprecated + retired
    migratable = active + counts.get("legacy", 0)
    adoption_pct = round((active / migratable) * 100, 1) if migratable else 100.0
    domains_covered = len(cats & set(DOMAINS))
    coverage_pct = round((domains_covered / len(DOMAINS)) * 100, 1) if DOMAINS else 100.0
    return {"total": total, "active": active, "in_domain": in_domain, "deprecated": deprecated,
            "retired": retired, "migratable": migratable, "adoption_pct": adoption_pct,
            "domains": len(DOMAINS), "domains_covered": domains_covered, "coverage_pct": coverage_pct}


def adoption(principal=None) -> dict:
    from .engine import stats
    cov = coverage()
    return {"registry": cov, "execution": stats(), "adoption_pct": cov["adoption_pct"],
            "coverage_pct": cov["coverage_pct"]}


# --- lifecycle ---------------------------------------------------------------

def _set_status(code, status, *, actor_user_id=None, reason=None) -> dict:
    if status not in DEFINITION_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with db.begin() as c:
        d = c.execute(select(orchestration_definitions).where(
            orchestration_definitions.c.code == code)).mappings().first()
        if d is None:
            raise ValueError(f"unknown definition {code!r}")
        values = {"status": status, "updated_at": now()}
        if status == "deprecated":
            values["deprecated_at"] = now()
            values["deprecated_reason"] = reason
        row = dict(c.execute(orchestration_definitions.update().where(
            orchestration_definitions.c.id == d["id"]).values(**values).returning(
                *orchestration_definitions.c)).mappings().one())
    write_audit(f"orchestration.definition_{status}", entity_type="orchestration_definition",
                entity_id=row["id"], actor_user_id=actor_user_id, metadata={"code": code})
    return row


def deprecate(code, *, reason=None, actor_user_id=None) -> dict:
    return _set_status(code, "deprecated", actor_user_id=actor_user_id, reason=reason)


def retire(code, *, actor_user_id=None) -> dict:
    return _set_status(code, "retired", actor_user_id=actor_user_id)


def definitions_index() -> dict:
    """The in-code executable definitions keyed by code (for governance reconciliation)."""
    return dict(ORCHESTRATION_DEFINITIONS)
