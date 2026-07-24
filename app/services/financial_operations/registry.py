"""Financial Operations registries (Phase D.57) — the declarative catalogs of the financial-operations layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new accounting platform, ERP,
billing engine, commission engine, payroll system, bookkeeping platform, general ledger, or budgeting
application:

  * FINANCIAL_REGISTRY — every financial category (advisory revenue, tax revenue, insurance commissions,
    planning fees, subscriptions, payroll, operating expenses, technology costs, vendor spend, profitability
    categories). Each names its authoritative owner, reporting owner, calculation owner, runtime gate, and
    deep links. Where the platform owns no authoritative source (billing / payroll / GL / expenses /
    profitability), the category declares a `not_configured` owner — reported honestly, never fabricated (the
    D.55 / D.56 precedent).
  * REVENUE_REGISTRY — every revenue type (recurring revenue, one-time revenue, commissions, advisory fees,
    tax preparation, planning engagements, consulting, implementation). Each names its authoritative owner,
    reporting owner, recognition owner, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * FINANCIAL_DASHBOARDS — every financial dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every financial category + revenue type is registered, every panel names an authoritative
owner + source + deep link, and that this layer never becomes a second accounting / ERP / billing / commission
/ payroll / bookkeeping / GL / budgeting system. The insurance commission ledger is the authoritative owner of
money; AUM / business-development / pipeline revenue come from the portfolio owner + the single Analytics
Registry — the layer stores NOTHING.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

NOT_CONFIGURED = "not_configured"


# --- financial registry ------------------------------------------------------

@dataclass(frozen=True)
class FinancialCategory:
    key: str
    label: str
    authoritative_owner: str   # the authoritative owner of the financial record (or "not_configured")
    reporting_owner: str       # the authoritative reporting owner (or "not_configured")
    calculation_owner: str     # the authoritative calculation owner (or "not_configured")
    runtime_gate: str
    deep_links: tuple


def _fin(key, label, authoritative_owner, reporting_owner, calculation_owner, deep_links, *,
         runtime_gate="financial_operations.enabled"):
    return FinancialCategory(key, label, authoritative_owner, reporting_owner, calculation_owner,
                             runtime_gate, tuple(deep_links))


FINANCIAL_REGISTRY = (
    _fin("advisory_revenue", "Advisory Revenue", "portfolio", "analytics.metrics", NOT_CONFIGURED,
         ("/financial-operations?dashboard=revenue", "/analytics")),
    _fin("tax_revenue", "Tax Revenue", NOT_CONFIGURED, "tax_domain", NOT_CONFIGURED,
         ("/financial-operations?dashboard=revenue", "/tax")),
    _fin("insurance_commissions", "Insurance Commissions", "insurance_commissions", "insurance_reporting",
         "insurance_commissions", ("/financial-operations?dashboard=commissions", "/insurance")),
    _fin("planning_fees", "Planning Fees", NOT_CONFIGURED, NOT_CONFIGURED, NOT_CONFIGURED,
         ("/financial-operations?dashboard=revenue",)),
    _fin("subscriptions", "Subscriptions", NOT_CONFIGURED, NOT_CONFIGURED, NOT_CONFIGURED,
         ("/financial-operations?dashboard=revenue",)),
    _fin("payroll", "Payroll", NOT_CONFIGURED, NOT_CONFIGURED, NOT_CONFIGURED,
         ("/financial-operations?dashboard=payroll",)),
    _fin("operating_expenses", "Operating Expenses", NOT_CONFIGURED, NOT_CONFIGURED, NOT_CONFIGURED,
         ("/financial-operations?dashboard=expenses",)),
    _fin("technology_costs", "Technology Costs", NOT_CONFIGURED, "vendor_management", NOT_CONFIGURED,
         ("/financial-operations?dashboard=expenses", "/vendor-management")),
    _fin("vendor_spend", "Vendor Spend", NOT_CONFIGURED, "vendor_management", NOT_CONFIGURED,
         ("/financial-operations?dashboard=expenses", "/vendor-management")),
    _fin("profitability", "Profitability Categories", NOT_CONFIGURED, NOT_CONFIGURED, NOT_CONFIGURED,
         ("/financial-operations?dashboard=profitability",)),
)

_FIN_BY_KEY = {f.key: f for f in FINANCIAL_REGISTRY}


# --- revenue registry --------------------------------------------------------

@dataclass(frozen=True)
class RevenueType:
    key: str
    label: str
    category: str              # recurring | one_time | commission | advisory | tax | planning | consulting | implementation
    authoritative_owner: str   # the authoritative owner of the revenue signal (or "not_configured")
    reporting_owner: str       # the authoritative reporting owner (or "not_configured")
    recognition_owner: str     # the authoritative revenue-recognition owner (or "not_configured")
    runtime_gate: str = "revenue.enabled"


def _rev(key, label, category, authoritative_owner, reporting_owner, *, recognition_owner=NOT_CONFIGURED):
    return RevenueType(key, label, category, authoritative_owner, reporting_owner, recognition_owner)


REVENUE_REGISTRY = (
    _rev("recurring_revenue", "Recurring Revenue", "recurring", "portfolio", "analytics.metrics"),
    _rev("one_time_revenue", "One-Time Revenue", "one_time", "bizdev", "analytics.metrics"),
    _rev("commissions", "Commissions", "commission", "insurance_commissions", "insurance_reporting",
         recognition_owner="insurance_commissions"),
    _rev("advisory_fees", "Advisory Fees", "advisory", "portfolio", "analytics.metrics"),
    _rev("tax_preparation", "Tax Preparation", "tax", NOT_CONFIGURED, "tax_domain"),
    _rev("planning_engagements", "Planning Engagements", "planning", NOT_CONFIGURED, NOT_CONFIGURED),
    _rev("consulting", "Consulting", "consulting", NOT_CONFIGURED, NOT_CONFIGURED),
    _rev("implementation", "Implementation", "implementation", NOT_CONFIGURED, NOT_CONFIGURED),
)

_REV_BY_KEY = {r.key: r for r in REVENUE_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative financial-owner surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


# Firm financial figures require analytics.executive; catalog/operational panels require analytics.view.
PANEL_REGISTRY = (
    # revenue
    _p("firm_aum", "portfolio", "analytics.metrics:aum", "revenue", "currency", "card",
       "analytics.executive", "/analytics",
       "Firm assets under management (the advisory revenue basis), from the portfolio owner via the single "
       "Analytics Registry `aum` metric. No second revenue engine."),
    _p("recurring_revenue", "portfolio", "analytics.metrics:aum", "revenue", "currency", "card",
       "analytics.executive", "/analytics",
       "Recurring advisory revenue basis (AUM), from the portfolio owner via the Analytics Registry. Recurring "
       "fee billing itself has no authoritative owner — reported honestly, never fabricated."),
    _p("business_development_revenue", "bizdev", "analytics.metrics:total_bd_revenue", "revenue", "currency",
       "card", "analytics.executive", "/analytics",
       "Business-development revenue (campaign + referral), from the bizdev owner via the Analytics Registry "
       "`total_bd_revenue` metric."),
    _p("pipeline_revenue", "opportunity", "analytics.metrics:pipeline_value", "revenue", "currency", "card",
       "analytics.executive", "/analytics",
       "Weighted opportunity-pipeline value, from the opportunity reporting owner via the Analytics Registry."),
    _p("forecast_revenue", "opportunity", "analytics.metrics:forecast_revenue", "revenue", "currency", "card",
       "analytics.executive", "/analytics",
       "Weighted revenue forecast, from the opportunity forecast owner via the Analytics Registry."),
    _p("revenue_trend", "analytics.trends", "analytics.trends:aum", "revenue", "mixed", "chart",
       "analytics.executive", "/analytics",
       "AUM revenue trend (time series + moving average), from the single Analytics trends owner."),
    _p("revenue_mix", "financial_operations", "financial_operations.compose", "revenue", "mixed", "chart",
       "analytics.executive", "/financial-operations?dashboard=revenue",
       "Revenue mix across the authoritative signals (commissions vs business-development vs AUM basis) — "
       "composed read-only from the owners. Advisory only; never posts a journal entry."),
    # commissions (the one authoritative money ledger)
    _p("commission_revenue", "insurance_commissions", "insurance_reporting.commission_report", "commissions",
       "currency", "card", "analytics.executive", "/insurance",
       "Insurance commission revenue (expected vs received), from the authoritative commission ledger via "
       "`insurance_reporting.commission_report` (requires insurance.commissions.read; unavailable otherwise). "
       "Aggregate totals only — no statement payloads."),
    _p("commission_reconciliation", "insurance_commissions", "insurance_reporting.commission_report",
       "commissions", "currency", "card", "analytics.executive", "/insurance",
       "Commission reconciliation (outstanding + variance), from the authoritative commission ledger. No "
       "second commission engine; the layer never pays a commission."),
    _p("producer_payouts", "insurance_commissions", "insurance_reporting.commission_report", "commissions",
       "currency", "card", "analytics.executive", "/insurance",
       "Producer payout vs agency-retained split, from the authoritative commission ledger. Aggregate totals "
       "only."),
    _p("collections", "insurance_commissions", "insurance_reporting.commission_report", "revenue", "currency",
       "card", "analytics.executive", "/insurance",
       "Outstanding commission receivables (collections), from the authoritative commission ledger. Aggregate "
       "total only; the layer never bills or collects."),
    # firm performance / KPIs
    _p("firm_kpis", "executive_intelligence", "executive_intelligence.executive_summary", "performance",
       "mixed", "list", "analytics.executive", "/executive",
       "Firm executive KPIs (AUM + revenue), from Executive Reporting composed over the single Analytics "
       "Registry. No second BI engine."),
    _p("firm_performance_score", "financial_operations", "financial_operations.compose", "performance",
       "percent", "gauge", "analytics.executive", "/financial-operations?dashboard=firm_performance",
       "Deterministic firm-performance indicator (revenue signals present + commission collection health) — "
       "composed from the authoritative owners. Advisory only; never a computed profit figure."),
    _p("operating_metrics", "practice_management", "practice_management.practice_summary", "performance",
       "percent", "gauge", "analytics.executive", "/practice-management",
       "Operating efficiency (firm capacity utilization), from Practice Management. An operating signal, not "
       "revenue; never a payroll or cost figure."),
    # profitability (no authoritative cost owner — honest indicator)
    _p("profitability_indicator", "financial_operations", "financial_operations.compose", "profitability",
       "status", "card", "analytics.executive", "/financial-operations?dashboard=profitability",
       "Profitability indicator — revenue signals are owned, but operating expenses / payroll / GL have no "
       "authoritative owner, so a margin cannot be computed and is reported `not_configured`. Never a "
       "fabricated profit figure."),
    # expenses / payroll / tech spend (not_configured — reported honestly)
    _p("vendor_dependencies", "vendor_management", "vendor_management.vendor_summary", "expenses", "count",
       "card", "analytics.view", "/vendor-management",
       "Technology / vendor dependency count, from the D.56 Vendor Management layer. Vendor spend has no "
       "authoritative cost owner (`not_configured`) — counts only, never a spend figure."),
    _p("financial_coverage", "financial_operations", "financial_operations.registry", "catalog", "count",
       "list", "analytics.view", "/financial-operations",
       "Financial-category coverage — which categories have an authoritative owner vs `not_configured` "
       "(billing / payroll / GL / operating expenses / profitability are not owned in the platform today)."),
    _p("tax_workload", "tax_domain", "analytics.sources:tax_dashboard", "revenue", "count", "card",
       "analytics.view", "/tax",
       "Tax engagement workload (an operational proxy — tax preparation revenue has no authoritative billing "
       "owner), from the tax domain via the analytics source layer. A workload count, not billed revenue."),
    # catalog panels
    _p("registered_revenue", "financial_operations", "financial_operations.registry", "catalog", "count",
       "list", "analytics.view", "/financial-operations",
       "The registered revenue-type catalog — each naming its authoritative / reporting / recognition owner."),
    _p("registered_financial", "financial_operations", "financial_operations.registry", "catalog", "count",
       "list", "analytics.view", "/financial-operations",
       "The registered financial-category catalog — each naming its authoritative / reporting / calculation "
       "owner + runtime gate + deep links."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # executive | operations | revenue
    runtime_gate: str
    panels: tuple
    required_capabilities: tuple
    navigation: str
    refresh_policy: str
    governing_services: tuple
    lifecycle: str = "active"


def _d(key, owner, audience, gate, panels, caps, navigation, governing, *, refresh="on_view",
       lifecycle="active"):
    return DashboardDef(key, owner, audience, gate, tuple(panels), tuple(caps), navigation, refresh,
                        tuple(governing), lifecycle)


FINANCIAL_DASHBOARDS = (
    _d("firm_performance", "financial_operations", "executive", "financial_operations.enabled",
       ("firm_performance_score", "firm_kpis", "firm_aum"),
       ("analytics.view",), "/financial-operations?dashboard=firm_performance",
       ("portfolio", "executive_intelligence", "analytics")),
    _d("revenue", "financial_operations", "revenue", "revenue.enabled",
       ("recurring_revenue", "business_development_revenue", "revenue_mix"),
       ("analytics.view",), "/financial-operations?dashboard=revenue",
       ("portfolio", "bizdev", "analytics")),
    _d("profitability", "financial_operations", "executive", "profitability.enabled",
       ("profitability_indicator", "revenue_mix", "financial_coverage"),
       ("analytics.view",), "/financial-operations?dashboard=profitability",
       ("financial_operations", "analytics")),
    _d("expenses", "financial_operations", "operations", "financial_operations.enabled",
       ("financial_coverage", "vendor_dependencies", "operating_metrics"),
       ("analytics.view",), "/financial-operations?dashboard=expenses",
       ("vendor_management", "practice_management")),
    _d("payroll", "financial_operations", "operations", "financial_operations.enabled",
       ("financial_coverage", "registered_financial", "operating_metrics"),
       ("analytics.view",), "/financial-operations?dashboard=payroll",
       ("financial_operations", "practice_management")),
    _d("commissions", "financial_operations", "revenue", "financial_operations.enabled",
       ("commission_revenue", "commission_reconciliation", "producer_payouts"),
       ("analytics.view",), "/financial-operations?dashboard=commissions",
       ("insurance_commissions", "insurance_reporting")),
    _d("financial_operations", "financial_operations", "executive", "financial_operations.enabled",
       ("firm_kpis", "collections", "revenue_trend", "forecast_revenue"),
       ("analytics.view",), "/financial-operations?dashboard=financial_operations",
       ("executive_intelligence", "insurance_commissions", "analytics")),
)

_DASH_BY_KEY = {d.key: d for d in FINANCIAL_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def financial_category(key) -> FinancialCategory | None:
    return _FIN_BY_KEY.get(key)


def revenue_type(key) -> RevenueType | None:
    return _REV_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def financial_registered(key) -> bool:
    return key in _FIN_BY_KEY


def revenue_registered(key) -> bool:
    return key in _REV_BY_KEY


def coverage() -> dict:
    return {
        "financial_categories": len(FINANCIAL_REGISTRY),
        "revenue_types": len(REVENUE_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(FINANCIAL_DASHBOARDS),
    }
