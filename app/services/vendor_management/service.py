"""Enterprise Vendor Management, Third-Party Risk & Technology Lifecycle Governance engine (Phase D.56).

A READ-ONLY composition over the platform's authoritative vendor / technology owners — the Integration
Platform provider registry (`integration.connectors` / `sync` / `service`, the vendor inventory of record),
the Security certificate & secret store (`security.secrets`), the Observability service catalog (technology
lifecycle), Insurance licensing (producer licenses), and Security incidents + Compliance Intelligence
(third-party risk). It composes named vendor dashboards (vendors, licensing, lifecycle, renewals, third-party
risk, operational dependencies, technology governance) from a declarative vendor + technology-lifecycle +
panel registry. It owns NO persistence, introduces NO second vendor-management platform, procurement system,
contract repository, CMDB, asset inventory, licensing platform, or risk engine, defines NO new metrics, and
NEVER modifies a vendor, renews a license, terminates a contract, alters an integration, or changes a
subscription. Procurement / contracts / subscriptions have no authoritative owner in the platform today —
those are declared registry classes reporting `not_configured` owners honestly. Every dashboard carries its
generated timestamp, governing services, source inventory, explainable panels, and deep links. Gate- and
policy-aware; returns ``None`` when a dashboard is not registered or the principal lacks its required
capability (route → 404/403). No contract contents, credentials, license keys, secrets, or procurement
payloads are ever emitted — counts + status only.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import gate, registry, stats
from .model import VendorDashboard
from .panels import compute_panel


def _authorized(principal, dash) -> bool:
    try:
        return any(principal.can(c) for c in dash.required_capabilities)
    except Exception:
        return False


def _disabled():
    return {"enabled": False, "dashboard": None}


def compose_dashboard(principal, key):
    """Compose a registered vendor dashboard. None when not registered or unauthorized; disabled envelope
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
    board = VendorDashboard(
        key=dash.key, name=dash.key.replace("_", " ").title(), audience=dash.audience,
        generated_at=datetime.now(UTC).isoformat(), panels=tuple(panels),
        governing_services=dash.governing_services, source_inventory=sources, deep_links=deep_links,
        navigation=dash.navigation, refresh_policy=dash.refresh_policy)
    stats.note("dashboards_composed", dashboard=dash.key)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return {"enabled": True, "dashboard": board.to_dict()}


def list_dashboards(principal):
    """The vendor dashboards the principal may open (holds at least one required capability). Metadata only —
    never a panel value."""
    if not gate.enabled():
        return {"enabled": False, "dashboards": []}
    out = []
    for d in registry.VENDOR_DASHBOARDS:
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


def vendor_summary(principal):
    """The firm vendor / technology-health summary — a compact, non-leaking envelope backing the Advisor
    Workspace Technology & Vendor Health panel + the Executive Dashboard + AI grounding. Never raises. Counts
    + status only; never a contract/credential/key/secret/payload."""
    if not gate.enabled():
        return {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}
    t0 = time.monotonic()
    panel_keys = ("vendor_governance_score", "expiring_certificates", "integration_dependencies",
                  "vendor_inventory")
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
            "governing_services": ["integration", "security", "observability"]}


def client_technology(principal, person_id):
    """A compact technology-dependencies summary in the context of ONE client — the external vendors /
    systems the client's data depends on, composed read-only from the authoritative Integration Hub
    per-entity read (source systems from person lineage). Counts + vendor names only, never a payload;
    deep-links to the authoritative integration surface. Record scope is validated at the Client360
    boundary. Never modifies a vendor/integration."""
    if not gate.enabled() or person_id is None:
        return {"enabled": False, "vendor_dependencies": 0}
    try:
        from app.services.integration_hub import client_integrations
        ci = client_integrations(principal, person_id)
        systems = ci.get("source_systems", [])
        return {"enabled": True, "source": "integration_hub.client_integrations", "not_a_second_engine": True,
                "vendor_dependencies": len(systems), "vendors": systems, "deep_link": "/vendor-management"}
    except Exception:
        stats.note("aggregation_failures", panel="client_technology")
        return {"enabled": True, "vendor_dependencies": 0, "error": "unavailable"}


def household_technology(principal, household_id, member_ids=None):
    """Aggregated technology-dependencies summary in the context of a household — the external vendors /
    systems the household's members depend on, composed read-only from the authoritative Integration Hub
    per-entity read. Counts + vendor names only; a rollup, never a payload."""
    if not gate.enabled() or household_id is None:
        return {"enabled": False, "vendor_dependencies": 0}
    try:
        from app.services.integration_hub import household_integrations
        hi = household_integrations(principal, household_id, member_ids)
        systems = hi.get("source_systems", [])
        return {"enabled": True, "source": "integration_hub.household_integrations",
                "not_a_second_engine": True, "vendor_dependencies": len(systems), "vendors": systems,
                "member_count": len(member_ids or []), "deep_link": "/vendor-management"}
    except Exception:
        stats.note("aggregation_failures", panel="household_technology")
        return {"enabled": True, "vendor_dependencies": 0, "error": "unavailable"}
