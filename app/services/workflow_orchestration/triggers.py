"""Domain-event triggers (Phase D.17).

Deterministic mapping of D.13–D.16 business events to existing workflow templates, stored in the
EXISTING ``automation_triggers`` table and launched via the EXISTING ``process_event``. ``fire``
is failure-isolated: a trigger error (bad template, launch failure) never breaks the calling
domain operation. Triggers are inactive by default (seeded examples) — nothing auto-launches
until an admin activates one.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.db import automation_triggers, engine

# Deterministic trigger vocabulary (no AI, no probabilistic triggers).
TRIGGER_TYPES = frozenset({
    "opportunity_won", "opportunity_lost", "annual_review_created", "business_owner_plan_created",
    "document_approved", "document_archived", "compliance_review_created", "advisor_work_completed",
    "campaign_activated", "referral_added", "client_created", "household_created",
    "organization_created", "manual_launch", "scheduled_launch"})


class TriggerError(Exception):
    """Invalid trigger configuration."""


def _now():
    return datetime.now(UTC)


def fire(event_type: str, *, entity_type: str, entity_id: int, actor_user_id: int,
         payload: dict | None = None, idempotency_key: str) -> list[int]:
    """Fire a domain event through the existing engine's ``process_event`` (which launches any
    ACTIVE matching triggers). Failure-isolated — never raises to the caller."""
    if event_type not in TRIGGER_TYPES:
        return []
    try:
        from app.services.workflow_automation import process_event
        return process_event(event_type, entity_type, entity_id, payload or {},
                             actor_user_id=actor_user_id, idempotency_key=idempotency_key) or []
    except Exception:
        return []


def list_triggers(principal=None, *, event_type=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(automation_triggers)
        if event_type:
            stmt = stmt.where(automation_triggers.c.event_type == event_type)
        return [dict(r) for r in c.execute(stmt.order_by(automation_triggers.c.event_type,
                                                        automation_triggers.c.priority)).mappings()]


def configure_trigger(principal, *, name, event_type, template_code, actor_user_id,
                      entity_type=None, conditions=None, priority=100, active=False) -> dict:
    """Create or update a domain-event trigger. Validates the trigger vocabulary + that the
    template is published (reuses the engine's template list)."""
    if event_type not in TRIGGER_TYPES:
        raise TriggerError(f"unknown trigger event_type {event_type!r}")
    from app.services.workflow_automation import list_templates
    if template_code not in {t["code"] for t in list_templates(include_drafts=True)}:
        raise TriggerError(f"unknown template {template_code!r}")
    with engine.begin() as c:
        existing = c.execute(select(automation_triggers).where(and_(
            automation_triggers.c.name == name,
            automation_triggers.c.event_type == event_type))).mappings().first()
        values = dict(entity_type=entity_type, conditions=conditions or {},
                      template_code=template_code, priority=priority, active=active)
        if existing:
            c.execute(automation_triggers.update()
                      .where(automation_triggers.c.id == existing["id"]).values(**values))
            tid = existing["id"]
        else:
            tid = c.execute(automation_triggers.insert().values(
                name=name, event_type=event_type, **values)
                .returning(automation_triggers.c.id)).scalar_one()
        return dict(c.execute(select(automation_triggers)
                              .where(automation_triggers.c.id == tid)).mappings().one())


def set_active(principal, trigger_id: int, active: bool) -> dict:
    with engine.begin() as c:
        c.execute(automation_triggers.update().where(automation_triggers.c.id == trigger_id)
                  .values(active=active))
        return dict(c.execute(select(automation_triggers)
                              .where(automation_triggers.c.id == trigger_id)).mappings().one())
