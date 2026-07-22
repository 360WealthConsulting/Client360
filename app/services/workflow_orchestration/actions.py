"""Workflow action registry (Phase D.17) — deterministic actions invoking existing services.

Actions coordinate work by calling EXISTING domain services; the workflow never duplicates
business logic. Each action is a pure function of its context. This is the "domain adapter" seam
the base engine documents — the actions here run through the orchestration layer (not by mutating
the engine's ``execute_automation_action``, which is left untouched for the tax flow).
"""
from __future__ import annotations


class ActionError(Exception):
    """Unknown action or missing required context."""


def _timeline_event(context, actor_user_id):
    from app.services.timeline import add_timeline_event
    if context.get("person_id") is None and context.get("household_id") is None:
        raise ActionError("timeline_event requires person_id or household_id")
    return add_timeline_event(
        source="workflow", event_type=context.get("event_type", "workflow_action"),
        title=context.get("title", "Workflow action"), person_id=context.get("person_id"),
        household_id=context.get("household_id"), external_id=context["external_id"],
        event_metadata=context.get("metadata", {}))


def _document_relationship(context, actor_user_id):
    from app.services.document_platform.relationships import link_entity
    return link_entity(context["principal"], context["document_id"],
                       entity_type=context["entity_type"], entity_id=context["entity_id"],
                       actor_user_id=actor_user_id,
                       relationship_type=context.get("relationship_type", "workflow"))


def _notification(context, actor_user_id):
    """Record a content-free notification via the existing notifications ledger (no dispatch)."""
    from app.services.notifications import record_notification
    return record_notification(**{k: v for k, v in context.items()
                                  if k in ("recipient_user_id", "purpose", "channel", "dedupe_key",
                                           "reference_type", "reference_id")})


def _assign(context, actor_user_id):
    from app.services.workflow_orchestration.service import assign_step
    return assign_step(context["principal"], context["step_id"], context["user_id"],
                       actor_user_id=actor_user_id)


ACTION_REGISTRY = {
    "timeline_event": _timeline_event,
    "document_relationship": _document_relationship,
    "notification": _notification,
    "assign": _assign,
}


def execute_action(action_type: str, *, context: dict, actor_user_id: int):
    """Deterministically dispatch an orchestration action to its existing-service implementation."""
    fn = ACTION_REGISTRY.get(action_type)
    if fn is None:
        raise ActionError(f"unknown action {action_type!r}")
    return fn(context, actor_user_id)


def list_actions() -> list[str]:
    return sorted(ACTION_REGISTRY)
