"""Domain-observation adapter (Phase D.46).

Normalizes the platform's domain-owned deterministic OBSERVATION sets — pipeline
(``opportunity.intelligence``), business development (``bizdev.intelligence``), and firm-level
(``analytics.intelligence``) — into the unified ``Recommendation`` contract. These are the observation
families deliberately kept OUT of the advisor_intelligence producer seam (ADR-018/019/020); this layer READS
them (never regenerates them). Read-only, fail-closed, book-scoped by the underlying principal-scoped reads.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .. import registry, stats
from ..model import Recommendation

# observation source → (recommendation type, reader import path)
_SOURCES = (
    ("pipeline_health", "opportunity.intelligence", "pipeline_intelligence"),
    ("bizdev_health", "bizdev.intelligence", "business_development_intelligence"),
    ("firm_health", "analytics.intelligence", "firm_intelligence"),
)


def _read(reader_module, reader_fn, principal):
    mod = __import__(f"app.services.{reader_module}", fromlist=[reader_fn])
    return getattr(mod, reader_fn)(principal)


def _recommendation_from_observation(rtype, obs, source_service) -> Recommendation | None:
    tdef = registry.recommendation_type(rtype)
    summary = obs.get("summary") or ""
    evidence = tuple(e for e in (summary, obs.get("kind")) if e)
    if not evidence:
        return None
    return Recommendation(
        recommendation_id=f"rec:{rtype}:{obs.get('id')}",
        type=rtype, category=(tdef.category if tdef else rtype),
        priority=obs.get("priority") or "informational", severity=obs.get("priority") or "info",
        title=obs.get("title") or rtype.replace("_", " ").title(), summary=summary,
        explanation=(tdef.explanation_template if tdef else summary),
        governing_rule=f"{source_service}:{obs.get('kind')}", evidence=evidence,
        authoritative_source=source_service,
        workflow_owner=(tdef.workflow_owner if tdef else "operations"),
        confidence=1.0,  # deterministic observation (fixed thresholds, no ML)
        generated_at=datetime.now(UTC).isoformat(),
        deep_link=(tdef.deep_link_target if tdef else "/workspace"),
        recommended_next_action="Review the observation",
        metadata={"kind": obs.get("kind")})


def observation_recommendations(principal) -> list[Recommendation]:
    """Compose the domain observation sets into Recommendations. Firm/book-level (no per-client anchor).
    Each source failure is isolated (fail-closed) and counted."""
    out = []
    for rtype, module, fn in _SOURCES:
        try:
            result = _read(module, fn, principal)
        except Exception:
            stats.note("adapter_failures", source=module)
            continue
        for obs in (result.get("observations") or []):
            try:
                rec = _recommendation_from_observation(rtype, obs, module)
            except Exception:
                stats.note("rule_failures", source=module)
                continue
            if rec is not None and rec.is_explainable:
                out.append(rec)
    return out


def workload_distribution(principal) -> dict:
    """The work-queue workload distribution for the workspace panel — a rollup, NOT per-item recommendations
    (those are already the advisor_intelligence overdue-task signals). Reuses the authoritative summary."""
    try:
        from app.services.work_queue.summary import work_queue_summary
        s = work_queue_summary(principal)
        return {"by_domain": s.get("by_domain", {}), "my_overdue": s.get("my_overdue", 0),
                "sla_breaches": s.get("sla_breaches", 0), "due_today": s.get("due_today", 0),
                "high_priority": s.get("high_priority", 0), "unassigned_team": s.get("unassigned_team", 0)}
    except Exception:
        stats.note("adapter_failures", source="work_queue")
        return {"by_domain": {}, "my_overdue": 0, "sla_breaches": 0, "due_today": 0,
                "high_priority": 0, "unassigned_team": 0}
