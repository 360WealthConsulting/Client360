"""Enterprise Knowledge Management panel composition (Phase D.62).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric, and
never any document content / confidential procedure / credential / token / client-sensitive documentation.
Documentation-health panels compose Document Intelligence; inventory / publication / ownership / version panels
compose the Document Platform (the DMS of record); retention panels compose Data Governance retention; SOP /
knowledge catalog + health panels are DERIVED from the declarative registries (labeled ``derived``). SOP
governance, runbooks, playbooks, onboarding SOPs, knowledge base, institutional memory, wiki, Confluence, and a
dedicated full-text / vector search index have no authoritative owner and are emitted ``available=False`` with
``config_status='not_configured'`` — honest, never a fabricated SOP / knowledge / version history. Every compose
is fail-closed and self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel,
never its value or count. This layer NEVER creates a document, edits a document, approves a document, publishes
documentation, changes a version, or alters metadata — it only composes counts, status, and coverage. A derived
value describes a documentation-coverage summary, never fabricated documentation or institutional knowledge.
"""
from __future__ import annotations

from . import registry, stats
from .model import PanelResult


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False, derived=pdef.derived)


def _result(pdef, value, *, available=True, config_status="configured"):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available,
                       derived=pdef.derived, config_status=config_status)


def _kpi(summary, key):
    if not isinstance(summary, dict):
        return None
    return (summary.get("kpis") or {}).get(key)


# --- Document Intelligence (documentation health) ------------------------------------------------------

def _document_summary(principal):
    from app.services.document_intelligence import document_summary
    return document_summary(principal)


def _documentation_completeness(principal, pdef):
    try:
        s = _document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"completeness_score": _kpi(s, "completeness_score")})
    except Exception:
        return _result(pdef, None, available=False)


def _documentation_gaps(principal, pdef):
    try:
        s = _document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"missing_documents": _kpi(s, "missing_documents")})
    except Exception:
        return _result(pdef, None, available=False)


def _expiring_documents(principal, pdef):
    try:
        s = _document_summary(principal)
        if not s or not s.get("enabled"):
            return _result(pdef, None, available=False)
        return _result(pdef, {"expiring_documents": _kpi(s, "expiring_documents")})
    except Exception:
        return _result(pdef, None, available=False)


# --- Document Platform (inventory / publication / ownership / version) ---------------------------------

def _list_documents(principal, **kw):
    from app.services.document_platform.service import list_documents
    return list_documents(principal, **kw)


def _status_total(principal, status):
    r = _list_documents(principal, status=status, page_size=1)
    return r.get("total", 0) if isinstance(r, dict) else 0


_STATUSES = ("draft", "active", "review", "approved", "superseded", "archived")


def _document_inventory_by_status(principal, pdef):
    try:
        by_status = {s: _status_total(principal, s) for s in _STATUSES}
        return _result(pdef, {"by_status": by_status, "total": sum(by_status.values())})
    except Exception:
        return _result(pdef, None, available=False)


def _document_inventory_by_classification(principal, pdef):
    try:
        classes = ("client", "compliance", "tax", "operations", "legal", "internal")
        by_class = {c: (_list_documents(principal, classification=c, page_size=1).get("total", 0))
                    for c in classes}
        return _result(pdef, {"by_classification": by_class})
    except Exception:
        return _result(pdef, None, available=False)


def _document_freshness(principal, pdef):
    try:
        active = _status_total(principal, "active")
        superseded = _status_total(principal, "superseded")
        archived = _status_total(principal, "archived")
        return _result(pdef, {"active": active, "superseded": superseded, "archived": archived})
    except Exception:
        return _result(pdef, None, available=False)


def _publication_readiness(principal, pdef):
    try:
        approved = _status_total(principal, "approved")
        draft = _status_total(principal, "draft")
        review = _status_total(principal, "review")
        pending = draft + review
        total = approved + pending
        pct = round(approved / total * 100, 1) if total else 0.0
        return _result(pdef, {"approved": approved, "pending": pending, "published_percent": pct})
    except Exception:
        return _result(pdef, None, available=False)


def _draft_documents(principal, pdef):
    try:
        return _result(pdef, {"draft": _status_total(principal, "draft")})
    except Exception:
        return _result(pdef, None, available=False)


def _approved_documents(principal, pdef):
    try:
        return _result(pdef, {"approved": _status_total(principal, "approved")})
    except Exception:
        return _result(pdef, None, available=False)


def _pending_review_documents(principal, pdef):
    try:
        return _result(pdef, {"review": _status_total(principal, "review")})
    except Exception:
        return _result(pdef, None, available=False)


def _superseded_documents(principal, pdef):
    try:
        return _result(pdef, {"superseded": _status_total(principal, "superseded")})
    except Exception:
        return _result(pdef, None, available=False)


def _version_awareness(principal, pdef):
    try:
        return _result(pdef, {"superseded_versions": _status_total(principal, "superseded"),
                              "immutable_version_store": True})
    except Exception:
        return _result(pdef, None, available=False)


def _ownership_scan(principal):
    r = _list_documents(principal, page_size=200)
    rows = r.get("rows", []) if isinstance(r, dict) else []
    total = r.get("total", len(rows)) if isinstance(r, dict) else len(rows)
    with_owner = sum(1 for d in rows
                     if d.get("owner_user_id") is not None or d.get("created_by_user_id") is not None)
    sampled = total > len(rows)
    return with_owner, len(rows), total, sampled


def _ownership_coverage(principal, pdef):
    try:
        with_owner, window, total, sampled = _ownership_scan(principal)
        pct = round(with_owner / window * 100, 1) if window else 0.0
        return _result(pdef, {"with_owner": with_owner, "window": window, "total": total,
                              "ownership_percent": pct, "sampled": sampled})
    except Exception:
        return _result(pdef, None, available=False)


def _orphaned_documentation(principal, pdef):
    try:
        with_owner, window, total, sampled = _ownership_scan(principal)
        return _result(pdef, {"orphaned": window - with_owner, "window": window, "sampled": sampled})
    except Exception:
        return _result(pdef, None, available=False)


# --- Data Governance retention -------------------------------------------------------------------------

def _retention_coverage(principal, pdef):
    try:
        from app.services.governance.retention import metrics
        m = metrics(principal)
        return _result(pdef, {"active_legal_holds": m.get("active_legal_holds", 0),
                              "pending_deletion_reviews": m.get("pending_deletion_reviews", 0),
                              "open_cases": m.get("open_cases", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- registry-derived (DERIVED) ------------------------------------------------------------------------

def _sop_coverage(principal, pdef):
    try:
        owned = [s.key for s in registry.SOP_CATEGORY_REGISTRY if s.owner != registry.NOT_CONFIGURED]
        nc = [s.key for s in registry.SOP_CATEGORY_REGISTRY if s.owner == registry.NOT_CONFIGURED]
        total = len(registry.SOP_CATEGORY_REGISTRY)
        pct = round(len(owned) / total * 100, 1) if total else 0.0
        return _result(pdef, {"with_owner": len(owned), "total": total, "coverage_percent": pct,
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _runbook_coverage(principal, pdef):
    return _result(pdef, {"status": registry.NOT_CONFIGURED,
                          "note": "no authoritative runbook / playbook owner exists in the platform"},
                   available=False, config_status=registry.NOT_CONFIGURED)


def _knowledge_domain_inventory(principal, pdef):
    try:
        nc = [k.key for k in registry.KNOWLEDGE_DOMAIN_REGISTRY if k.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.KNOWLEDGE_DOMAIN_REGISTRY),
                              "domains": [k.key for k in registry.KNOWLEDGE_DOMAIN_REGISTRY],
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _knowledge_source_inventory(principal, pdef):
    try:
        nc = [k.key for k in registry.KNOWLEDGE_SOURCE_REGISTRY if k.config_status == registry.NOT_CONFIGURED]
        return _result(pdef, {"count": len(registry.KNOWLEDGE_SOURCE_REGISTRY),
                              "sources": [k.key for k in registry.KNOWLEDGE_SOURCE_REGISTRY],
                              "not_configured": nc})
    except Exception:
        return _result(pdef, None, available=False)


def _knowledge_gaps(principal, pdef):
    try:
        nc = list(registry.not_configured_domains())
        return _result(pdef, {"count": len(nc), "not_configured": nc},
                       config_status=(registry.NOT_CONFIGURED if nc else registry.CONFIGURED))
    except Exception:
        return _result(pdef, None, available=False)


def _knowledge_health(principal, pdef):
    try:
        configured = len(registry.configured_domains())
        total = configured + len(registry.not_configured_domains())
        cov = round(configured / total * 100, 1) if total else 0.0
        completeness = None
        try:
            s = _document_summary(principal)
            if s and s.get("enabled"):
                completeness = _kpi(s, "completeness_score")
        except Exception:
            pass
        return _result(pdef, {"domain_coverage_percent": cov, "documentation_completeness": completeness,
                              "documentation_coverage_not_fabricated_knowledge": True})
    except Exception:
        return _result(pdef, None, available=False)


def _executive_knowledge_status(principal, pdef):
    """DERIVED executive knowledge posture — deterministic, authoritative inputs, labeled derived. A
    documentation-coverage summary only, never fabricated documentation, SOP approval, version history, or
    institutional knowledge."""
    try:
        configured = len(registry.configured_domains())
        not_configured = list(registry.not_configured_domains())
        signals = {}
        try:
            s = _document_summary(principal)
            if s and s.get("enabled"):
                signals["documentation_completeness"] = _kpi(s, "completeness_score")
        except Exception:
            pass
        try:
            signals["approved_documents"] = _status_total(principal, "approved")
        except Exception:
            pass
        return _result(pdef, {"derived": True, "documentation_coverage_not_fabricated_knowledge": True,
                              "not_a_certified_sop_or_approval": True,
                              "configured_domains": configured, "not_configured_domains": len(not_configured),
                              "not_configured": not_configured, "signals": signals})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "documentation_completeness": _documentation_completeness,
    "documentation_gaps": _documentation_gaps,
    "expiring_documents": _expiring_documents,
    "document_freshness": _document_freshness,
    "document_inventory_by_status": _document_inventory_by_status,
    "document_inventory_by_classification": _document_inventory_by_classification,
    "publication_readiness": _publication_readiness,
    "draft_documents": _draft_documents,
    "approved_documents": _approved_documents,
    "pending_review_documents": _pending_review_documents,
    "superseded_documents": _superseded_documents,
    "ownership_coverage": _ownership_coverage,
    "orphaned_documentation": _orphaned_documentation,
    "version_awareness": _version_awareness,
    "retention_coverage": _retention_coverage,
    "sop_coverage": _sop_coverage,
    "runbook_coverage": _runbook_coverage,
    "knowledge_domain_inventory": _knowledge_domain_inventory,
    "knowledge_source_inventory": _knowledge_source_inventory,
    "knowledge_gaps": _knowledge_gaps,
    "knowledge_health": _knowledge_health,
    "executive_knowledge_status": _executive_knowledge_status,
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
