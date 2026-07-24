"""Data Governance panel composition (Phase D.52).

Each panel's value is composed on READ by its authoritative owner — never persisted, never merged, never a
second metric, and never any client-sensitive payload. Master-data / metadata panels compose the AUTHORITATIVE
Governance data catalog (D.23); duplicate/merge panels compose the Governance MDM engine + the
entity-resolution engine (which own the authoritative person-merge — never re-implemented); validation panels
compose the Governance quality engine; lineage panels compose the catalog survivorship rules + the Event
registry dependency graph; ownership panels compose the declarative master-data + stewardship registries.
Every compose is fail-closed (a source outage yields an unavailable panel, never an exception) and
self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel, never its value.
This layer NEVER merges an entity, alters an identity, modifies metadata, approves stewardship, changes
ownership, or mutates anything — it only composes counts + status.
"""
from __future__ import annotations

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False)


def _result(pdef, value, *, available=True):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available)


# --- declarative-registry panels ----------------------------------------------------------------------

def _registered_entities(principal, pdef):
    try:
        by_owner = {}
        for e in registry.MASTER_DATA_REGISTRY:
            by_owner[e.authoritative_owner] = by_owner.get(e.authoritative_owner, 0) + 1
        return _result(pdef, {"count": len(registry.MASTER_DATA_REGISTRY),
                              "entities": [e.key for e in registry.MASTER_DATA_REGISTRY],
                              "by_owner": by_owner})
    except Exception:
        return _result(pdef, None, available=False)


def _entity_ownership(principal, pdef):
    try:
        by_owner = {}
        for e in registry.MASTER_DATA_REGISTRY:
            by_owner[e.authoritative_owner] = by_owner.get(e.authoritative_owner, 0) + 1
        return _result(pdef, {"distinct_owners": len(by_owner), "by_owner": by_owner})
    except Exception:
        return _result(pdef, None, available=False)


def _lineage_coverage(principal, pdef):
    try:
        by_lineage = {}
        for e in registry.MASTER_DATA_REGISTRY:
            by_lineage[e.lineage_owner] = by_lineage.get(e.lineage_owner, 0) + 1
        covered = sum(1 for e in registry.MASTER_DATA_REGISTRY if e.lineage_owner)
        return _result(pdef, {"entities_with_lineage_owner": covered, "by_lineage_owner": by_lineage})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_stewardship(principal, pdef):
    try:
        return _result(pdef, {"count": len(registry.STEWARDSHIP_REGISTRY),
                              "roles": [s.key for s in registry.STEWARDSHIP_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _stewardship_coverage(principal, pdef):
    try:
        by_business = {}
        for s in registry.STEWARDSHIP_REGISTRY:
            by_business[s.business_owner] = by_business.get(s.business_owner, 0) + 1
        return _result(pdef, {"roles": len(registry.STEWARDSHIP_REGISTRY), "by_business_owner": by_business})
    except Exception:
        return _result(pdef, None, available=False)


# --- Governance data catalog (the authoritative metadata owner) ----------------------------------------

def _data_domains(principal, pdef):
    try:
        from app.services.governance.catalog import list_domains
        return _result(pdef, {"count": len(list_domains())})
    except Exception:
        return _result(pdef, None, available=False)


def _data_elements(principal, pdef):
    try:
        from app.services.governance.catalog import list_elements
        return _result(pdef, {"count": len(list_elements())})
    except Exception:
        return _result(pdef, None, available=False)


def _domain_stewards(principal, pdef):
    try:
        from app.services.governance.catalog import list_domains
        domains = list_domains()
        assigned = sum(1 for d in domains if d.get("steward_user_id"))
        return _result(pdef, {"domains": len(domains), "with_steward": assigned})
    except Exception:
        return _result(pdef, None, available=False)


def _lineage_rules(principal, pdef):
    try:
        from app.services.governance.catalog import list_survivorship_rules
        return _result(pdef, {"count": len(list_survivorship_rules())})
    except Exception:
        return _result(pdef, None, available=False)


def _quality_rules(principal, pdef):
    try:
        from app.services.governance.catalog import list_rules
        return _result(pdef, {"count": len(list_rules())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Governance retention / cases ----------------------------------------------------------------------

def _remediation_cases(principal, pdef):
    try:
        from app.services.governance.retention import list_cases
        rows = list_cases()
        open_cases = sum(1 for r in rows if r.get("status") in ("open", "in_progress"))
        return _result(pdef, {"open": open_cases, "total": len(rows)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Event registry (event-dependency lineage) ---------------------------------------------------------

def _event_lineage(principal, pdef):
    try:
        from app.services.events.registry import dependency_graph
        graph = dependency_graph()
        edges = sum(len(v or []) for v in graph.values())
        return _result(pdef, {"nodes": len(graph), "edges": edges})
    except Exception:
        return _result(pdef, None, available=False)


# --- Governance MDM (duplicate/merge — composes the authoritative person-merge) ------------------------

def _duplicate_candidates(principal, pdef):
    try:
        from app.services.governance.mdm import list_candidates
        q = list_candidates(principal, status="pending", page=1, page_size=1)
        return _result(pdef, {"pending": q.get("total", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _merge_summary(principal, pdef):
    try:
        from app.services.governance.mdm import list_candidates
        by_status = {}
        for st in ("pending", "approved", "rejected"):
            q = list_candidates(principal, status=st, page=1, page_size=1)
            by_status[st] = q.get("total", 0)
        return _result(pdef, {"by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _ambiguous_unlinked(principal, pdef):
    try:
        from app.matching.promote import list_ambiguous_unlinked
        return _result(pdef, {"ambiguous_unlinked": len(list_ambiguous_unlinked())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Governance quality (validation) -------------------------------------------------------------------

def _validation_findings(principal, pdef):
    try:
        from app.services.governance.quality import list_findings
        by_sev = {}
        for sev in ("critical", "high", "medium", "low"):
            q = list_findings(principal, severity=sev, page=1, page_size=1)
            by_sev[sev] = q.get("total", 0)
        return _result(pdef, {"by_severity": {k: v for k, v in by_sev.items() if v}})
    except Exception:
        return _result(pdef, None, available=False)


def _validation_metrics(principal, pdef):
    try:
        from app.services.governance.quality import metrics
        m = metrics(principal)
        return _result(pdef, {"open": m.get("open", 0), "critical_open": m.get("critical_open", 0),
                              "total": m.get("total", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _governance_overview(principal, pdef):
    try:
        from app.services.governance.service import overview_metrics
        return _result(pdef, overview_metrics(principal))
    except Exception:
        return _result(pdef, None, available=False)


def _data_quality_score(principal, pdef):
    try:
        entities = len(registry.MASTER_DATA_REGISTRY)
        open_findings = 0
        try:
            from app.services.governance.quality import metrics
            open_findings = metrics(principal).get("open", 0)
        except Exception:
            pass
        denom = entities + open_findings
        score = round(entities / denom * 100, 1) if denom else 100.0
        return _result(pdef, {"quality_percent": score, "governed_entities": entities,
                              "open_findings": open_findings, "advisory_only": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "registered_entities": _registered_entities,
    "data_domains": _data_domains,
    "data_elements": _data_elements,
    "registered_stewardship": _registered_stewardship,
    "domain_stewards": _domain_stewards,
    "remediation_cases": _remediation_cases,
    "lineage_rules": _lineage_rules,
    "event_lineage": _event_lineage,
    "lineage_coverage": _lineage_coverage,
    "entity_ownership": _entity_ownership,
    "stewardship_coverage": _stewardship_coverage,
    "duplicate_candidates": _duplicate_candidates,
    "ambiguous_unlinked": _ambiguous_unlinked,
    "merge_summary": _merge_summary,
    "validation_findings": _validation_findings,
    "validation_metrics": _validation_metrics,
    "quality_rules": _quality_rules,
    "governance_overview": _governance_overview,
    "data_quality_score": _data_quality_score,
}


def compute_panel(principal, key):
    """Compose one panel by key. Read-only, fail-closed, self-restricting. Returns a PanelResult, or None
    if the panel is not registered / not explainable."""
    pdef = registry.panel(key)
    fn = _COMPUTE.get(key)
    if pdef is None or fn is None:
        return None
    try:
        entitled = principal.can(pdef.permission)
    except Exception:
        entitled = False
    if not entitled:
        stats.note("restricted_panels")
        return _restricted(pdef)
    try:
        result = fn(principal, pdef)
    except Exception:
        stats.note("aggregation_failures", panel=key)
        return None
    if result is None or not result.is_explainable:
        stats.note("missing_explainability", panel=key)
        return None
    stats.note("panels_composed")
    return result
