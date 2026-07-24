"""Enterprise Operational Intelligence recommendation engine (Phase D.46).

A READ-ONLY composition over the authoritative recommendation sources — the advisor_intelligence Signal
engine (the platform's existing deterministic, propose-only recommendation engine), the domain observation
sets (pipeline/bizdev/firm), the unified work queue, and the D.44 engagement summary. It normalizes them to
one explainable ``Recommendation`` contract, deduplicates, suppresses, prioritizes, and aggregates for
households. It is NOT a second recommendation/workflow/opportunity engine: it never re-derives domain logic,
never mutates, and never invents a recommendation. Every emitted recommendation is explainable (why +
evidence + deep link) — non-explainable ones are dropped. Gate- and policy-aware; returns ``None`` when out
of scope so the route emits 404.
"""
from __future__ import annotations

import time

from . import gate, stats
from .adapters import (
    communication_followup,
    observation_recommendations,
    signals_to_recommendations,
    workload_distribution,
)


def _prioritize(recs):
    return sorted(recs, key=lambda r: (r.priority_rank, r.recommendation_id), reverse=True)


def _dedupe(recs):
    """Deduplicate by recommendation id (stable, source-qualified). Counts collapses."""
    seen, out = set(), []
    for r in recs:
        if r.recommendation_id in seen:
            stats.note("duplicates_collapsed")
            continue
        seen.add(r.recommendation_id)
        out.append(r)
    return out


def _emit(recs):
    """Explainability gate + per-recommendation stats. Non-explainable recommendations never leave here."""
    out = []
    for r in recs:
        if not r.is_explainable:
            stats.note("missing_evidence")
            continue
        stats.note("generated", category=r.category, severity=r.severity)
        out.append(r)
    return out


def _in_scope(principal, entity_type, entity_id):
    try:
        from app.security.authorization import record_in_scope
        return record_in_scope(principal, entity_type, entity_id)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "recommendations": [], "counts": {}}


def client_recommendations(principal, person_id):
    """Explainable recommendations for one client. None when out of scope; disabled envelope when gated off."""
    if not gate.enabled():
        return _disabled()
    if not gate.policy_ok("client"):
        return {"enabled": True, "recommendations": [], "counts": {}, "denied": "policy"}
    if not _in_scope(principal, "person", person_id):
        return None
    t0 = time.monotonic()
    from app.services.advisor_intelligence import get_client_signals
    recs = signals_to_recommendations(get_client_signals(principal, person_id))
    recs += communication_followup(principal, person_id=person_id)
    recs = _emit(_dedupe(recs))
    result = _package(_prioritize(recs), person_id=person_id)
    stats.note("compositions")
    stats.note_ms((time.monotonic() - t0) * 1000)
    return result


def household_recommendations(principal, household_id):
    """Aggregated, de-duplicated household recommendations with household-wide prioritization. None when out
    of scope. Duplicate recommendations across members collapse (by type + title)."""
    if not gate.enabled() or not gate.gate("recommendations.household.enabled"):
        return _disabled()
    if not gate.policy_ok("household"):
        return {"enabled": True, "recommendations": [], "counts": {}, "denied": "policy"}
    if not _in_scope(principal, "household", household_id):
        return None
    t0 = time.monotonic()
    from app.services.advisor_intelligence import get_household_signals
    recs = signals_to_recommendations(get_household_signals(principal, household_id))
    recs += communication_followup(principal, household_id=household_id)
    # Household aggregation: collapse duplicates that recur across members by (type, title).
    seen, aggregated = set(), []
    for r in _prioritize(recs):
        key = (r.type, r.title)
        if key in seen:
            stats.note("duplicates_collapsed")
            continue
        seen.add(key)
        aggregated.append(r)
    result = _package(_emit(_dedupe(aggregated)), household_id=household_id)
    stats.note("compositions")
    stats.note_ms((time.monotonic() - t0) * 1000)
    return result


def workspace_recommendations(principal):
    """The Advisor Workspace Operational Intelligence panel — book-scoped highest-priority recommendations +
    the domain observations + the workload distribution. Read-only, gate-aware."""
    if not gate.enabled() or not gate.gate("recommendations.workspace.enabled"):
        return {**_disabled(), "workload": {}}
    t0 = time.monotonic()
    from app.services.advisor_intelligence import get_dashboard_signals
    recs = signals_to_recommendations(get_dashboard_signals(principal))
    recs += observation_recommendations(principal)
    recs = _emit(_dedupe(recs))
    ordered = _prioritize(recs)
    result = _package(ordered, top=25)
    result["workload"] = workload_distribution(principal)
    stats.note("compositions")
    stats.note_ms((time.monotonic() - t0) * 1000)
    return result


def recommendation_summary(principal, *, person_id=None, household_id=None):
    """Compact summary (counts by category/severity + top) for the Client 360 / Household 360 sections and
    (through them) AI grounding. Never raises; counts only, no client-sensitive evidence."""
    if not gate.enabled():
        return {"enabled": False, "total": 0, "by_category": {}, "by_severity": {}, "top": None}
    if person_id is not None:
        result = client_recommendations(principal, person_id)
    elif household_id is not None:
        result = household_recommendations(principal, household_id)
    else:
        return {"enabled": True, "total": 0, "by_category": {}, "by_severity": {}, "top": None}
    if result is None or not result.get("enabled"):
        return {"enabled": True, "total": 0, "by_category": {}, "by_severity": {}, "top": None}
    recs = result["recommendations"]
    top = recs[0] if recs else None
    return {"enabled": True, "total": len(recs), "by_category": result["counts"].get("by_category", {}),
            "by_severity": result["counts"].get("by_severity", {}),
            "top": {"type": top["type"], "title": top["title"], "priority": top["priority"],
                    "deep_link": top["deep_link"]} if top else None}


def explain_recommendation(principal, recommendation_id, *, person_id=None, household_id=None):
    """Return the full explanation of one recommendation (why/rule/sources/evidence/workflow-owner/deep-link).
    Scope-checked via the composition. None when out of scope."""
    if not gate.enabled():
        return {"enabled": False, "explanation": None}
    if person_id is not None:
        result = client_recommendations(principal, person_id)
    elif household_id is not None:
        result = household_recommendations(principal, household_id)
    else:
        return {"enabled": True, "explanation": None}
    if result is None:
        return None
    match = next((r for r in result.get("recommendations", [])
                 if r["recommendation_id"] == recommendation_id), None)
    if match is None:
        return {"enabled": True, "explanation": None}
    return {"enabled": True, "explanation": {
        "why": match["explanation"], "governing_rule": match["governing_rule"],
        "authoritative_source": match["authoritative_source"], "evidence": match["evidence"],
        "workflow_owner": match["workflow_owner"], "deep_link": match["deep_link"],
        "confidence": match["confidence"]}}


def _package(recs, *, person_id=None, household_id=None, top=None):
    rows = recs[:top] if top else recs
    by_category, by_severity = {}, {}
    for r in recs:
        by_category[r.category] = by_category.get(r.category, 0) + 1
        by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
    return {"enabled": True, "recommendations": [r.to_dict() for r in rows], "total": len(recs),
            "counts": {"by_category": by_category, "by_severity": by_severity}}
