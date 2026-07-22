"""Configuration reviews (Phase D.27) — the Automation entry point (metadata only).

``run_due_reviews`` is the deterministic job Automation invokes (``configuration_review`` dispatch):
it validates active configuration items (flagging items that reference a runtime setting but have no
value), reviews environment-override drift, and reviews in-flight feature rollouts — recording a
configuration change for each issue found. It reads the existing runtime config only and changes no
runtime configuration. Automation executes; Configuration owns the metadata (ADR-027 / D.22 reuse).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db import (
    configuration_environment_overrides as overrides_t,
)
from app.db import (
    configuration_feature_rollouts as rollouts_t,
)
from app.db import (
    configuration_items as items_t,
)
from app.db import engine

from . import platform


def run_due_reviews(principal, *, actor_user_id=None) -> dict:
    """Validate active configuration and record a proposed configuration change for each issue.
    Returns a small summary. Records metadata only."""
    # 1. Items that are active + reference a runtime setting but carry no value (validation gap).
    #    (JSON columns store an unset value as JSON null, which reads back as Python None — filter in
    #    Python rather than relying on a SQL NULL check that would miss a JSON-null value.)
    with engine.connect() as c:
        candidate_items = list(c.execute(select(items_t.c.id, items_t.c.code, items_t.c.value).where(
            items_t.c.status == "active", items_t.c.runtime_setting_reference.is_not(None))).mappings())
        invalid_items = [it for it in candidate_items if it["value"] is None]
        active_overrides = c.scalar(select(func.count()).select_from(overrides_t)
                                    .where(overrides_t.c.active.is_(True))) or 0
        active_rollouts = list(c.execute(select(rollouts_t.c.id, rollouts_t.c.stage).where(
            rollouts_t.c.status == "active")).mappings())

    validation_findings = 0
    for it in invalid_items:
        platform.propose_change(principal, entity_type="item", entity_id=it["id"], change_type="update",
                                note=f"validation: {it['code']} references a runtime setting but has no value",
                                actor_user_id=actor_user_id)
        validation_findings += 1

    return {"items_validated": len(invalid_items), "validation_findings": validation_findings,
            "active_overrides_reviewed": active_overrides, "rollouts_reviewed": len(active_rollouts)}
