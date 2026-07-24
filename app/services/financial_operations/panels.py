"""Financial Operations panel composition (Phase D.57).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any payroll detail / tax return / bank account number / payment credential / accounting payload.
Revenue panels compose the AUTHORITATIVE portfolio AUM owner + the single Analytics Registry
(`analytics.metrics` / `analytics.trends`); commission panels compose the AUTHORITATIVE insurance commission
ledger (`insurance_reporting.commission_report`, the one money owner); firm-KPI panels compose Executive
Reporting; operating panels compose Practice Management; technology-spend panels compose the D.56 Vendor
Management layer. Where the platform owns no authoritative source (operating expenses, payroll, GL,
profitability), the panel reports `not_configured` honestly — never a fabricated figure. Every compose is
fail-closed (a source outage or missing sub-capability yields an unavailable panel, never an exception) and
self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel, never its value.
This layer NEVER creates an invoice, posts a journal entry, processes payroll, calculates taxes, pays a
commission, or modifies an accounting record — it only composes aggregate totals + status.
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


# --- Analytics Registry revenue metrics (single registry) ----------------------------------------------

def _metric_value(principal, key):
    from app.services.analytics.metrics import compute_metric
    m = compute_metric(principal, key)
    return m.get("value") if isinstance(m, dict) else None


def _firm_aum(principal, pdef):
    try:
        v = _metric_value(principal, "aum")
        if v is None:
            return _result(pdef, None, available=False)
        return _result(pdef, {"aum": float(v), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _recurring_revenue(principal, pdef):
    try:
        v = _metric_value(principal, "aum")
        if v is None:
            return _result(pdef, None, available=False)
        return _result(pdef, {"recurring_basis": float(v), "basis": "aum",
                              "fee_billing": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


def _business_development_revenue(principal, pdef):
    try:
        v = _metric_value(principal, "total_bd_revenue")
        if v is None:
            return _result(pdef, None, available=False)
        return _result(pdef, {"bd_revenue": float(v), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _pipeline_revenue(principal, pdef):
    try:
        v = _metric_value(principal, "pipeline_value")
        if v is None:
            return _result(pdef, None, available=False)
        return _result(pdef, {"pipeline_value": float(v), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _forecast_revenue(principal, pdef):
    try:
        v = _metric_value(principal, "forecast_revenue")
        if v is None:
            return _result(pdef, None, available=False)
        return _result(pdef, {"forecast_revenue": float(v), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _revenue_trend(principal, pdef):
    try:
        from app.services.analytics.trends import metric_trend
        t = metric_trend("aum")
        points = t.get("points") or t.get("series") or []
        return _result(pdef, {"metric": "aum", "point_count": len(points),
                              "latest": (points[-1] if points else None)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Insurance commission ledger (the one authoritative money owner) -----------------------------------

def _commission_report(principal):
    from app.services import insurance_reporting
    return insurance_reporting.commission_report(principal)


def _commission_revenue(principal, pdef):
    try:
        r = _commission_report(principal)
        return _result(pdef, {"expected_total": r.get("expected_total", 0.0),
                              "received_total": r.get("received_total", 0.0),
                              "entry_count": r.get("entry_count", 0), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _commission_reconciliation(principal, pdef):
    try:
        r = _commission_report(principal)
        return _result(pdef, {"outstanding_total": r.get("outstanding_total", 0.0),
                              "variance_total": r.get("variance_total", 0.0),
                              "by_status": r.get("by_status", {}), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _producer_payouts(principal, pdef):
    try:
        r = _commission_report(principal)
        return _result(pdef, {"producer_payouts": r.get("producer_payouts", {}),
                              "agency_retained": r.get("agency_retained", {}), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


def _collections(principal, pdef):
    try:
        r = _commission_report(principal)
        return _result(pdef, {"outstanding_receivables": r.get("outstanding_total", 0.0), "currency": "USD"})
    except Exception:
        return _result(pdef, None, available=False)


# --- Executive Reporting / Practice Management (firm KPIs / operating) ----------------------------------

def _firm_kpis(principal, pdef):
    try:
        from app.services.executive_intelligence import executive_summary
        summary = executive_summary(principal)
        kpis = summary.get("kpis") or summary.get("widgets") or {}
        count = len(kpis) if hasattr(kpis, "__len__") else 0
        return _result(pdef, {"kpi_count": count, "source": "executive_intelligence"})
    except Exception:
        return _result(pdef, None, available=False)


def _operating_metrics(principal, pdef):
    try:
        from app.services.practice_management import practice_summary
        s = practice_summary(principal)
        kpis = s.get("kpis", {}) if isinstance(s, dict) else {}
        util = kpis.get("firm_capacity_utilization")
        return _result(pdef, {"capacity_utilization": util,
                              "note": "operating efficiency signal, not revenue or cost"})
    except Exception:
        return _result(pdef, None, available=False)


# --- Vendor Management (technology dependencies; spend not_configured) ----------------------------------

def _vendor_dependencies(principal, pdef):
    try:
        from app.services.vendor_management import vendor_summary
        vs = vendor_summary(principal)
        kpis = vs.get("kpis", {}) if isinstance(vs, dict) else {}
        inv = kpis.get("vendor_inventory") or {}
        count = inv.get("count") if isinstance(inv, dict) else None
        return _result(pdef, {"vendor_dependencies": count, "spend": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


# --- Tax workload (operational proxy; tax-prep billing not_configured) ----------------------------------

def _tax_workload(principal, pdef):
    try:
        from app.services.analytics.sources import tax_dashboard
        td = tax_dashboard(principal)
        metrics = td.get("metrics", {}) if isinstance(td, dict) else {}
        return _result(pdef, {"engagements": metrics.get("engagements"),
                              "billed_revenue": registry.NOT_CONFIGURED})
    except Exception:
        return _result(pdef, None, available=False)


# --- catalog panels ------------------------------------------------------------------------------------

def _registered_revenue(principal, pdef):
    try:
        owned = sum(1 for r in registry.REVENUE_REGISTRY if r.authoritative_owner != registry.NOT_CONFIGURED)
        return _result(pdef, {"count": len(registry.REVENUE_REGISTRY),
                              "types": [r.key for r in registry.REVENUE_REGISTRY], "with_owner": owned})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_financial(principal, pdef):
    try:
        owned = sum(1 for f in registry.FINANCIAL_REGISTRY if f.authoritative_owner != registry.NOT_CONFIGURED)
        return _result(pdef, {"count": len(registry.FINANCIAL_REGISTRY),
                              "categories": [f.key for f in registry.FINANCIAL_REGISTRY], "with_owner": owned})
    except Exception:
        return _result(pdef, None, available=False)


def _financial_coverage(principal, pdef):
    try:
        owned, not_configured = [], []
        for f in registry.FINANCIAL_REGISTRY:
            (owned if f.authoritative_owner != registry.NOT_CONFIGURED else not_configured).append(f.key)
        return _result(pdef, {"owned": owned, "not_configured": not_configured,
                              "owned_count": len(owned), "not_configured_count": len(not_configured)})
    except Exception:
        return _result(pdef, None, available=False)


# --- composed indicators (deterministic, advisory) -----------------------------------------------------

def _revenue_mix(principal, pdef):
    try:
        commissions = None  # commissions come from the authoritative ledger, not the metrics registry
        try:
            r = _commission_report(principal)
            commissions = r.get("received_total", 0.0)
        except Exception:
            commissions = None
        bd = _metric_value(principal, "total_bd_revenue")
        aum = _metric_value(principal, "aum")
        mix = {"commissions": (float(commissions) if commissions is not None else None),
               "business_development": (float(bd) if bd is not None else None),
               "aum_basis": (float(aum) if aum is not None else None)}
        if all(v is None for v in mix.values()):
            return _result(pdef, None, available=False)
        return _result(pdef, {"mix": mix, "advisory_only": True})
    except Exception:
        return _result(pdef, None, available=False)


def _profitability_indicator(principal, pdef):
    try:
        aum = _metric_value(principal, "aum")
        has_revenue = aum is not None
        return _result(pdef, {"revenue_signals_owned": has_revenue,
                              "operating_expenses": registry.NOT_CONFIGURED,
                              "payroll": registry.NOT_CONFIGURED,
                              "margin": registry.NOT_CONFIGURED,
                              "note": "margin cannot be computed — no authoritative expense / payroll / GL "
                                      "owner in the platform today"})
    except Exception:
        return _result(pdef, None, available=False)


def _firm_performance_score(principal, pdef):
    try:
        signals = 0
        for key in ("aum", "total_bd_revenue"):
            if _metric_value(principal, key):
                signals += 1
        collection_health = None
        try:
            r = _commission_report(principal)
            exp = r.get("expected_total", 0.0) or 0.0
            rec = r.get("received_total", 0.0) or 0.0
            collection_health = round(rec / exp * 100, 1) if exp else 100.0
        except Exception:
            pass
        score = round(min(100.0, signals * 40.0 + (collection_health or 0.0) * 0.2), 1)
        return _result(pdef, {"performance_percent": score, "revenue_signals": signals,
                              "collection_health_percent": collection_health, "advisory_only": True})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "firm_aum": _firm_aum,
    "recurring_revenue": _recurring_revenue,
    "business_development_revenue": _business_development_revenue,
    "pipeline_revenue": _pipeline_revenue,
    "forecast_revenue": _forecast_revenue,
    "revenue_trend": _revenue_trend,
    "revenue_mix": _revenue_mix,
    "commission_revenue": _commission_revenue,
    "commission_reconciliation": _commission_reconciliation,
    "producer_payouts": _producer_payouts,
    "collections": _collections,
    "firm_kpis": _firm_kpis,
    "firm_performance_score": _firm_performance_score,
    "operating_metrics": _operating_metrics,
    "profitability_indicator": _profitability_indicator,
    "vendor_dependencies": _vendor_dependencies,
    "financial_coverage": _financial_coverage,
    "tax_workload": _tax_workload,
    "registered_revenue": _registered_revenue,
    "registered_financial": _registered_financial,
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
