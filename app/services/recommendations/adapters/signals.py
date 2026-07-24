"""Signal-normalization adapter (Phase D.46).

Normalizes the AUTHORITATIVE advisor_intelligence ``Signal`` objects (the platform's existing deterministic,
propose-only recommendation engine — D.5A–D.5D) into the unified ``Recommendation`` contract. It NEVER
re-derives domain logic and NEVER invents recommendations — every field is carried from the Signal's own
title/summary/explainability/evidence/route. A signal without a why + evidence + deep link is dropped
(explainability enforced). Read-only, fail-closed.
"""
from __future__ import annotations

from .. import registry, stats
from ..model import Recommendation


def _next_action(rtype, deep_link):
    tdef = registry.recommendation_type(rtype)
    owner = tdef.workflow_owner if tdef else "workspace"
    verbs = {
        "client_attention": "Review the open exceptions",
        "review_cadence": "Open the review workflow",
        "task_workload": "Work the overdue items",
        "meeting_prep": "Prepare for the meeting",
        "service_opportunity": "Review the opportunity",
        "governed_recommendation": "Route to compliance review",
        "communication_followup": "Follow up with the client",
    }
    return verbs.get(rtype, f"Open {owner}")


def recommendation_from_signal(signal_dict) -> Recommendation | None:
    """Normalize one advisor_intelligence Signal dict → Recommendation, or None if not explainable."""
    rtype = registry.classify_signal(signal_dict)
    tdef = registry.recommendation_type(rtype)
    expl = signal_dict.get("explainability") or {}
    why = expl.get("why") or (tdef.explanation_template if tdef else "")
    evidence = tuple(expl.get("evidence") or signal_dict.get("evidence") or ())
    src_record = signal_dict.get("source_record") or {}
    person_id = src_record.get("entity_id") if src_record.get("entity_type") == "person" else None
    household_id = src_record.get("entity_id") if src_record.get("entity_type") == "household" else None
    deep_link = signal_dict.get("route") or registry.deep_link_for(
        rtype, person_id if person_id is not None else household_id)
    rec_meta = signal_dict.get("recommendation") or {}
    governing_rule = rec_meta.get("governing_rule") or expl.get("source_service") \
        or signal_dict.get("source_service") or "advisor_intelligence"
    rec = Recommendation(
        recommendation_id=f"rec:{rtype}:{signal_dict.get('id')}",
        type=rtype, category=(tdef.category if tdef else "attention"),
        priority=signal_dict.get("priority") or "informational",
        severity=signal_dict.get("severity") or "info",
        title=signal_dict.get("title") or rtype.replace("_", " ").title(),
        summary=signal_dict.get("summary") or "",
        explanation=why, governing_rule=governing_rule, evidence=evidence,
        authoritative_source=signal_dict.get("source_service")
        or (tdef.owner_service if tdef else "advisor_intelligence"),
        workflow_owner=(tdef.workflow_owner if tdef else "workspace"),
        confidence=float(expl.get("confidence") or 1.0),
        generated_at=signal_dict.get("created_at"),
        deep_link=deep_link, recommended_next_action=_next_action(rtype, deep_link),
        related_person_id=person_id, related_household_id=household_id,
        metadata={"policy_gate": signal_dict.get("policy_gate"), "group": signal_dict.get("group")})
    stats.note("registry_lookups", category=rec.category)
    if not rec.is_explainable:
        stats.note("missing_evidence")
        return None
    return rec


def signals_to_recommendations(signals) -> list[Recommendation]:
    """Normalize a tuple of advisor_intelligence Signal objects into Recommendations (dropping any that are
    not explainable). Accepts Signal objects (with .to_dict()) or already-serialized dicts."""
    out = []
    for s in (signals or []):
        try:
            d = s.to_dict() if hasattr(s, "to_dict") else s
            rec = recommendation_from_signal(d)
        except Exception:
            stats.note("rule_failures", source="advisor_intelligence")
            continue
        if rec is not None:
            out.append(rec)
    return out
