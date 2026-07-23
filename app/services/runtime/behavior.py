"""Runtime behavior registry (Phase D.30) — the behavioral-migration catalog + adoption coverage.

Tracks which application behaviors have been migrated to consume the runtime engine (``migrated``),
which remain on the legacy path (``legacy``), which are data-driven with no switch to migrate
(``deterministic``), and which have had their legacy fallback removed (``retired``). Migration
coverage / adoption percentage is computed from this durable registry. Major behavioral events
(behavior adopted / legacy retired / migration completed) record to the D.28 ``runtime_events``
append-only ledger (entity_type ``behavior``); routine feature evaluations are never recorded.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database.runtime_behavior_tables import BEHAVIOR_STATUSES
from app.db import engine, runtime_behaviors

from .common import now, record_event, write_audit


def list_behaviors(*, status=None, module=None):
    with engine.connect() as c:
        stmt = select(runtime_behaviors).order_by(runtime_behaviors.c.module, runtime_behaviors.c.code)
        if status:
            stmt = stmt.where(runtime_behaviors.c.status == status)
        if module:
            stmt = stmt.where(runtime_behaviors.c.module == module)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_behavior(code: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_behaviors).where(runtime_behaviors.c.code == code)).mappings().first()
        return dict(row) if row else None


def _set_status(code: str, status: str, *, event_type, action, actor_user_id=None,
                stamp_field=None) -> dict:
    if status not in BEHAVIOR_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with engine.begin() as c:
        b = c.execute(select(runtime_behaviors).where(runtime_behaviors.c.code == code)).mappings().first()
        if b is None:
            raise ValueError(f"unknown behavior {code!r}")
        values = {"status": status, "updated_at": now()}
        if stamp_field:
            values[stamp_field] = now()
        row = c.execute(runtime_behaviors.update().where(runtime_behaviors.c.id == b["id"]).values(**values)
                        .returning(*runtime_behaviors.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="behavior", entity_id=row["id"], event_type=event_type,
                     from_status=b["status"], to_status=status, actor_user_id=actor_user_id,
                     payload={"code": code})
    write_audit(action, entity_type="behavior", entity_id=row["id"], actor_user_id=actor_user_id,
                metadata={"code": code})
    return row


def mark_migrated(code: str, *, actor_user_id=None) -> dict:
    """Record that an application behavior now consumes the runtime engine (runtime behavior adopted)."""
    return _set_status(code, "migrated", event_type="runtime_behavior_adopted",
                       action="runtime.behavior_adopted", actor_user_id=actor_user_id,
                       stamp_field="migrated_at")


def mark_retired(code: str, *, actor_user_id=None) -> dict:
    """Record that a behavior's legacy fallback path has been removed (legacy behavior retired)."""
    return _set_status(code, "retired", event_type="legacy_behavior_retired",
                       action="runtime.legacy_retired", actor_user_id=actor_user_id,
                       stamp_field="retired_at")


def coverage() -> dict:
    """Adoption coverage from the registry. ``deterministic`` behaviors have no switch and are
    excluded from the migratable denominator (documented separately)."""
    with engine.connect() as c:
        rows = list(c.execute(select(runtime_behaviors.c.status,
                                     func.count().label("n")).group_by(runtime_behaviors.c.status)).mappings())
    counts = {r["status"]: r["n"] for r in rows}
    migrated = counts.get("migrated", 0)
    retired = counts.get("retired", 0)
    legacy = counts.get("legacy", 0)
    deterministic = counts.get("deterministic", 0)
    migratable = migrated + retired + legacy
    adoption_pct = round(((migrated + retired) / migratable) * 100, 1) if migratable else 100.0
    return {"migrated": migrated, "retired": retired, "legacy": legacy, "deterministic": deterministic,
            "migratable": migratable, "total": migratable + deterministic,
            "adoption_pct": adoption_pct}


def adoption(principal=None) -> dict:
    """Combined behavioral-adoption view: durable registry coverage + live in-process consumption
    counters (feature/config lookups, runtime decisions vs legacy fallbacks). For observability and
    analytics — routine feature evaluations are counted, never individually recorded."""
    from .consumption import adoption_stats
    cov = coverage()
    stats = adoption_stats()
    return {"registry": cov, "consumption": stats,
            "adoption_pct": cov["adoption_pct"],
            "migrated_behaviors": cov["migrated"] + cov["retired"],
            "legacy_behaviors": cov["legacy"], "deterministic_behaviors": cov["deterministic"]}


def audit_history(principal=None, *, code=None, limit=100) -> list[dict]:
    """Behavioral lifecycle events from the D.28 runtime_events ledger (entity_type=behavior)."""
    from app.db import engine as _engine
    from app.db import runtime_events
    with _engine.connect() as c:
        stmt = select(runtime_events).where(runtime_events.c.entity_type == "behavior")
        if code is not None:
            b = get_behavior(code)
            if b:
                stmt = stmt.where(runtime_events.c.entity_id == b["id"])
        return [dict(r) for r in c.execute(
            stmt.order_by(runtime_events.c.id.desc()).limit(min(500, max(1, limit)))).mappings()]


def record_migration_completed(*, actor_user_id=None) -> dict:
    """Record a firm-level ``migration_completed`` event (no legacy behaviors remain migratable)."""
    cov = coverage()
    with engine.begin() as c:
        record_event(c, entity_type="behavior", entity_id=0, event_type="migration_completed",
                     actor_user_id=actor_user_id, payload=cov)
    write_audit("runtime.migration_completed", entity_type="behavior", entity_id=0,
                actor_user_id=actor_user_id, metadata={"adoption_pct": cov["adoption_pct"]})
    return cov
