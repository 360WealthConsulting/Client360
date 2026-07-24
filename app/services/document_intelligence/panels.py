"""Document Intelligence panel composition (Phase D.50).

Each panel's value is composed on READ by its authoritative owner — never persisted, never re-stored, never
a second metric, and never any document content. Inventory / lifecycle / archive-state / OCR-status panels
compose the AUTHORITATIVE Document Platform (Phase D.16 — the single document + metadata + lifecycle owner);
retention / disposition panels compose Governance retention (Phase D.23); missing-documentation panels
compose Compliance Intelligence (Phase D.47, which normalizes the authoritative exception engine). Every
compose is fail-closed (a source outage yields an unavailable panel, never an exception) and self-restricts:
a principal lacking the panel's capability is shown a ``restricted`` panel, never its value. This layer NEVER
alters metadata, archives/deletes documents, modifies retention, runs OCR, or builds an index — it only
composes counts + status.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from . import registry, stats
from .model import PanelResult

# Document Platform vocabularies (the authoritative classification + status sets).
_CLASSIFICATIONS = ("client", "compliance", "tax", "insurance", "benefits", "retirement", "estate",
                    "investment", "operations", "marketing", "legal", "hr", "internal", "archived")
_STATUSES = ("draft", "active", "review", "approved", "superseded", "archived")
_EXPIRY_HORIZON_DAYS = 90


def _restricted(pdef):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=None,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, restricted=True,
                       available=False)


def _result(pdef, value, *, available=True):
    return PanelResult(key=pdef.key, title=pdef.key.replace("_", " ").title(), owner=pdef.owner,
                       source=pdef.source, measure=pdef.measure, unit=pdef.unit, viz=pdef.viz, value=value,
                       explanation=pdef.explainability, deep_link=pdef.deep_link, available=available)


# --- Document Platform reads (the authoritative repository) --------------------------------------------

def _count(principal, **filters):
    from app.services.document_platform.service import list_documents
    return list_documents(principal, page_size=1, **filters).get("total", 0)


def _page(principal, **filters):
    from app.services.document_platform.service import list_documents
    return list_documents(principal, page_size=200, **filters).get("rows", [])


def _inventory_by_classification(principal, pdef):
    try:
        by_class = {}
        for c in _CLASSIFICATIONS:
            n = _count(principal, classification=c)
            if n:
                by_class[c] = n
        return _result(pdef, {"by_classification": by_class, "total": sum(by_class.values())})
    except Exception:
        return _result(pdef, None, available=False)


def _inventory_by_status(principal, pdef):
    try:
        by_status = {s: _count(principal, status=s) for s in _STATUSES}
        by_status = {k: v for k, v in by_status.items() if v}
        return _result(pdef, {"by_status": by_status, "total": sum(by_status.values())})
    except Exception:
        return _result(pdef, None, available=False)


def _lifecycle_status(principal, pdef):
    try:
        by_status = {s: _count(principal, status=s) for s in _STATUSES}
        return _result(pdef, {"by_status": {k: v for k, v in by_status.items() if v},
                              "states": list(_STATUSES)})
    except Exception:
        return _result(pdef, None, available=False)


def _folder_inventory(principal, pdef):
    try:
        from app.services.document_platform.service import list_folders
        return _result(pdef, {"folder_count": len(list_folders())})
    except Exception:
        return _result(pdef, None, available=False)


def _archived_documents(principal, pdef):
    try:
        return _result(pdef, {"archived": _count(principal, status="archived")})
    except Exception:
        return _result(pdef, None, available=False)


def _pending_review(principal, pdef):
    try:
        return _result(pdef, {"pending_review": _count(principal, status="review")})
    except Exception:
        return _result(pdef, None, available=False)


def _superseded_documents(principal, pdef):
    try:
        return _result(pdef, {"superseded": _count(principal, status="superseded")})
    except Exception:
        return _result(pdef, None, available=False)


def _ocr_status(principal, pdef):
    try:
        rows = _page(principal)
        by_ocr = {}
        for r in rows:
            key = r.get("ocr_status") or "not_processed"
            by_ocr[key] = by_ocr.get(key, 0) + 1
        return _result(pdef, {"by_ocr_status": by_ocr, "sampled": len(rows),
                              "note": "reported from Document Platform metadata; this layer runs no OCR"})
    except Exception:
        return _result(pdef, None, available=False)


def _expiring_documents(principal, pdef):
    try:
        today = datetime.now(UTC).date()
        rows = _page(principal)
        expired = approaching = 0
        for r in rows:
            exp = r.get("expiration_date")
            if isinstance(exp, datetime):
                exp = exp.date()
            if not isinstance(exp, date):
                continue
            if exp < today:
                expired += 1
            elif (exp - today).days <= _EXPIRY_HORIZON_DAYS:
                approaching += 1
        return _result(pdef, {"expired": expired, "approaching": approaching, "sampled": len(rows),
                              "horizon_days": _EXPIRY_HORIZON_DAYS})
    except Exception:
        return _result(pdef, None, available=False)


# --- Document Platform retention-policy store ----------------------------------------------------------

def _retention_policies(principal, pdef):
    try:
        from app.services.document_platform.service import list_retention_policies
        policies = list_retention_policies()
        return _result(pdef, {"count": len(policies),
                              "policies": [{"code": p.get("code"), "name": p.get("name"),
                                            "retention_years": p.get("retention_years"),
                                            "action_on_expiry": p.get("action_on_expiry")}
                                           for p in policies]})
    except Exception:
        return _result(pdef, None, available=False)


# --- Governance retention (the records retention/archive owner) ----------------------------------------

def _retention_assignments(principal, pdef):
    try:
        from app.services.governance.retention import list_retention_assignments
        rows = list_retention_assignments()
        by_status = {}
        for r in rows:
            s = r.get("status") or "active"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"total": len(rows), "by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _retention_metrics(principal, pdef):
    try:
        from app.services.governance.retention import metrics
        return _result(pdef, metrics(principal))
    except Exception:
        return _result(pdef, None, available=False)


def _archive_readiness(principal, pdef):
    try:
        from app.services.governance.retention import list_retention_assignments
        rows = list_retention_assignments()
        archival = sum(1 for r in rows if r.get("archival_eligible"))
        expired = sum(1 for r in rows if r.get("status") == "expired")
        return _result(pdef, {"archival_eligible": archival, "expired": expired, "total": len(rows)})
    except Exception:
        return _result(pdef, None, available=False)


def _disposition_requests(principal, pdef):
    try:
        from app.services.governance.retention import list_deletion_requests
        rows = list_deletion_requests()
        by_type = {}
        for r in rows:
            if r.get("status") in ("executed", "rejected"):
                continue
            t = r.get("request_type") or "deletion"
            by_type[t] = by_type.get(t, 0) + 1
        return _result(pdef, {"open": sum(by_type.values()), "by_type": by_type})
    except Exception:
        return _result(pdef, None, available=False)


# --- Compliance Intelligence (missing documentation) ---------------------------------------------------

_DOC_GAP_TYPES = ("missing_document", "unsigned_disclosure", "missing_beneficiary")


def _supervisory_exceptions(principal):
    from app.services.compliance_intelligence import supervisory_dashboard
    result = supervisory_dashboard(principal)
    if result is None or not result.get("enabled"):
        return None
    return result.get("exceptions", [])


def _missing_documents(principal, pdef):
    try:
        exc = _supervisory_exceptions(principal)
        if exc is None:
            return _result(pdef, None, available=False)
        missing = [e for e in exc if e.get("exception_type") == "missing_document"]
        return _result(pdef, {"count": len(missing),
                              "items": [{"title": e.get("title"), "severity": e.get("severity"),
                                         "deep_link": e.get("deep_link")} for e in missing[:25]]})
    except Exception:
        return _result(pdef, None, available=False)


def _unsigned_agreements(principal, pdef):
    try:
        exc = _supervisory_exceptions(principal)
        if exc is None:
            return _result(pdef, None, available=False)
        unsigned = [e for e in exc if e.get("exception_type") == "unsigned_disclosure"]
        return _result(pdef, {"count": len(unsigned)})
    except Exception:
        return _result(pdef, None, available=False)


def _documentation_gaps(principal, pdef):
    try:
        exc = _supervisory_exceptions(principal)
        if exc is None:
            return _result(pdef, None, available=False)
        by_type = {}
        for e in exc:
            t = e.get("exception_type")
            if t in _DOC_GAP_TYPES:
                by_type[t] = by_type.get(t, 0) + 1
        return _result(pdef, {"by_type": by_type, "total": sum(by_type.values())})
    except Exception:
        return _result(pdef, None, available=False)


# --- completeness (deterministic, advisory) ------------------------------------------------------------

def _completeness_score(principal, pdef):
    try:
        total = _count(principal)
        gaps = 0
        exc = _supervisory_exceptions(principal)
        if exc is not None:
            gaps = sum(1 for e in exc if e.get("exception_type") in _DOC_GAP_TYPES)
        denom = total + gaps
        score = round(total / denom * 100, 1) if denom else 100.0
        return _result(pdef, {"completeness_percent": score, "documents": total, "open_gaps": gaps,
                              "advisory_only": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "inventory_by_classification": _inventory_by_classification,
    "inventory_by_status": _inventory_by_status,
    "folder_inventory": _folder_inventory,
    "retention_policies": _retention_policies,
    "retention_assignments": _retention_assignments,
    "retention_metrics": _retention_metrics,
    "archived_documents": _archived_documents,
    "archive_readiness": _archive_readiness,
    "disposition_requests": _disposition_requests,
    "lifecycle_status": _lifecycle_status,
    "pending_review": _pending_review,
    "superseded_documents": _superseded_documents,
    "missing_documents": _missing_documents,
    "unsigned_agreements": _unsigned_agreements,
    "documentation_gaps": _documentation_gaps,
    "completeness_score": _completeness_score,
    "ocr_status": _ocr_status,
    "expiring_documents": _expiring_documents,
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
