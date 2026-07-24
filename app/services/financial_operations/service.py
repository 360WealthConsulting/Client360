"""Enterprise Financial Operations, Revenue Intelligence & Firm Performance Governance engine (Phase D.57).

A READ-ONLY composition over the platform's authoritative financial owners — the insurance commission ledger
(`insurance_commissions` / `insurance_reporting.commission_report`, the one money owner), the portfolio AUM
owner, the single Analytics Registry revenue metrics (`analytics.metrics` / `analytics.trends`), Executive
Reporting, and Practice Management. It composes named financial dashboards (firm performance, revenue,
profitability, expenses, payroll, commissions, financial operations) from a declarative financial + revenue +
panel registry. It owns NO persistence, introduces NO second accounting platform, ERP, billing engine,
commission engine, payroll system, bookkeeping platform, general ledger, or budgeting application, defines NO
new metrics, and NEVER creates an invoice, posts a journal entry, processes payroll, calculates taxes, pays a
commission, or modifies an accounting record. Billing / fee calculation / payroll / operating expenses / GL /
profitability have no authoritative owner in the platform today — those are declared registry categories
reporting `not_configured` owners honestly. Every dashboard carries its generated timestamp, governing
services, source inventory, explainable panels, and deep links. Gate- and policy-aware; returns ``None`` when
a dashboard is not registered or the principal lacks its required capability (route → 404/403). No payroll
details, tax returns, bank account numbers, payment credentials, or accounting payloads are ever emitted —
firm-level aggregate totals + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import FinancialDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered financial dashboard. None when not registered or unauthorized; disabled envelope
    when gated off."""
    if not gate.enabled():
        return _disabled()
    dash = registry.dashboard(key)
    if dash is None:
        return None
    if not _authorized(principal, dash):
        stats.note("authorization_failures")
        return None
    if not gate.gate(dash.runtime_gate):
        return {"enabled": False, "dashboard": None, "gated": dash.runtime_gate}
    if not gate.policy_ok("dashboard"):
        return {"enabled": True, "dashboard": None, "denied": "policy"}
    t0 = time.monotonic()
    panels = []
    for pkey in dash.panels:
        p = compute_panel(principal, pkey)
        if p is not None:
            panels.append(p)
    sources = tuple(dict.fromkeys(p.source for p in panels))
    deep_links = {p.key: p.deep_link for p in panels if p.deep_link}
    board = FinancialDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The financial dashboards the principal may open (holds at least one required capability). Metadata only
    — never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.FINANCIAL_DASHBOARDS:
        if _authorized(principal, d):
            out.append({"key": d.key, "audience": d.audience, "navigation": d.navigation,
                        "panel_count": len(d.panels), "runtime_gate": d.runtime_gate,
                        "required_capabilities": list(d.required_capabilities),
                        "governing_services": list(d.governing_services)})
    return {"enabled": True, "dashboards": out}


def get_panel(principal, key):
    """Compose a single panel by key. None when not registered / not explainable."""
    if not gate.enabled():
        return None
    p = compute_panel(principal, key)
    return p.to_dict() if p is not None else None


def firm_financial_summary(principal):
    """The firm financial-performance summary — a compact, non-leaking envelope backing the Advisor Workspace
    Financial Performance panel + the Executive Dashboard + AI grounding. Never raises. Firm-level aggregate
    totals + status only; never a payroll detail / tax return / bank account number / payment credential /
    accounting payload."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("firm_performance_score", "recurring_revenue", "commission_revenue", "collections",
                  "vendor_dependencies")
    panels = []
    for pkey in panel_keys:
        p = compute_panel(principal, pkey)
        if p is not None:
            panels.append(p.to_dict())
    kpis = {p["key"]: p["value"] for p in panels if not p["restricted"] and p["value"] is not None}
    stats.note("summaries_composed")
    stats.note_ms((time.monotonic() - t0) * 1000)
    dashboards = list_dashboards(principal).get("dashboards", [])
    return {"enabled": True, "generated_at": datetime.now(UTC).isoformat(), "panels": panels,
            "kpis": kpis, "dashboards": dashboards,
            "governing_services": ["insurance_commissions", "portfolio", "analytics", "executive_intelligence"]}


def client_financial(principal, person_id):
    """A compact financial-relationship summary in the context of ONE client — the advisory revenue basis
    (the client's AUM) the firm's relationship rests on, composed read-only from the authoritative portfolio
    owner. Aggregate total only, never a payload; per-client fee / commission billing has no authoritative
    owner (`not_configured`) and is never fabricated. Record scope is validated at the Client 360 boundary.
    Never bills, invoices, or posts anything."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "advisory_revenue_basis": None}
    try:
        from app.services.portfolio import book_aum
        aum = book_aum([person_id])
        return {"enabled": True, "source": "portfolio.book_aum", "not_a_second_engine": True,
                "advisory_revenue_basis": float(aum) if aum is not None else 0.0,
                "fee_billing": registry.NOT_CONFIGURED, "deep_link": "/financial-operations"}
    except Exception:
        stats.note("aggregation_failures", panel="client_financial")
        return {"enabled": True, "advisory_revenue_basis": None, "error": "unavailable"}


def household_financial(principal, household_id, member_ids=None):
    """Aggregated financial-relationship summary in the context of a household — the advisory revenue basis
    (the household members' AUM) composed read-only from the authoritative portfolio owner. Aggregate total
    only; a rollup, never a payload. Per-household fee / commission billing has no authoritative owner
    (`not_configured`)."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "advisory_revenue_basis": None}
    try:
        from app.services.portfolio import book_aum
        ids = list(member_ids or [])
        aum = book_aum(ids) if ids else 0.0
        return {"enabled": True, "source": "portfolio.book_aum", "not_a_second_engine": True,
                "advisory_revenue_basis": float(aum) if aum is not None else 0.0,
                "fee_billing": registry.NOT_CONFIGURED, "member_count": len(ids),
                "deep_link": "/financial-operations"}
    except Exception:
        stats.note("aggregation_failures", panel="household_financial")
        return {"enabled": True, "advisory_revenue_basis": None, "error": "unavailable"}
