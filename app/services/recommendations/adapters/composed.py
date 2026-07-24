"""Composed recommendation adapter (Phase D.46).

The one thin composed rule that fills a genuine gap not already covered by the authoritative signal engine:
a COMMUNICATION FOLLOW-UP recommendation derived from the D.44 engagement summary (unresolved action items).
It composes an existing authoritative, scoped read (``engagement_summary``) — it never queries a domain
directly and never mutates. Read-only, fail-closed, explainability enforced.
"""
from __future__ import annotations

from .. import registry, stats
from ..model import Recommendation


def communication_followup(principal, *, person_id=None, household_id=None) -> list[Recommendation]:
    """Emit a communication-followup recommendation when the client has unresolved communication actions.
    Suppressed (nothing emitted) when there is no action required."""
    try:
        from app.services.communications.engagement import engagement_summary
        summ = engagement_summary(principal, person_id=person_id, household_id=household_id)
    except Exception:
        stats.note("adapter_failures", source="engagement_summary")
        return []
    if not summ.get("enabled"):
        return []
    action_required = summ.get("action_required") or 0
    unread = summ.get("unread") or 0
    if action_required <= 0 and unread <= 0:
        stats.note("suppressed", category="communication")
        return []
    tdef = registry.recommendation_type("communication_followup")
    anchor = person_id if person_id is not None else household_id
    last = summ.get("last_interaction") or {}
    evidence = (f"{action_required} action-required interaction(s)", f"{unread} unread")
    rec = Recommendation(
        recommendation_id=f"rec:communication_followup:{'person' if person_id else 'household'}:{anchor}",
        type="communication_followup", category="communication",
        priority="medium" if action_required else "low", severity="medium" if action_required else "low",
        title="Communication follow-up needed",
        summary=f"{action_required} interaction(s) need a response"
                + (f"; last: {last.get('subject')}" if last.get("subject") else ""),
        explanation=tdef.explanation_template, governing_rule="recommendations:communication_followup:v1",
        evidence=evidence, authoritative_source="communications.engagement",
        workflow_owner="communications.engagement", confidence=1.0,
        generated_at=last.get("timestamp"),
        deep_link=(f"/engagement?person_id={person_id}" if person_id
                   else f"/engagement?household_id={household_id}"),
        recommended_next_action="Follow up with the client",
        related_person_id=person_id, related_household_id=household_id)
    return [rec] if rec.is_explainable else []
