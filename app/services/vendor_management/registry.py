"""Vendor Management registries (Phase D.56) — the declarative catalogs of the vendor-management layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new vendor-management
platform, procurement system, contract repository, CMDB, asset inventory, licensing platform, or risk engine:

  * VENDOR_REGISTRY — every vendor class (software vendors, custodians, tax providers, insurance carriers,
    cloud providers, communication providers, infrastructure providers, identity providers). Each names its
    authoritative owner, integration owner, security owner, lifecycle owner, runtime gate, and deep links.
    The vendor inventory of record is the Integration Platform provider registry — the layer stores NOTHING.
  * TECHNOLOGY_LIFECYCLE_REGISTRY — every technology-lifecycle class (production systems, SaaS platforms,
    infrastructure services, subscriptions, licenses, certificates, integrations, identity providers). Each
    names its owner, lifecycle owner, renewal owner, support owner, category, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * VENDOR_DASHBOARDS — every vendor dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every vendor class + lifecycle class is registered, every panel names an authoritative
owner + source + deep link, and that this layer never becomes a second vendor / procurement / contract / CMDB
/ licensing / risk system.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- vendor registry ---------------------------------------------------------

@dataclass(frozen=True)
class VendorClass:
    key: str
    label: str
    authoritative_owner: str   # the authoritative owner of the vendor record (never re-implemented)
    integration_owner: str     # the authoritative integration owner
    security_owner: str        # the authoritative security owner
    lifecycle_owner: str       # the authoritative technology-lifecycle owner
    provider_type: str         # the Integration Platform provider_type this class maps to (or "n/a")
    runtime_gate: str
    deep_links: tuple


def _vendor(key, label, provider_type, deep_links, *, authoritative_owner="integration.connectors",
            integration_owner="integration.connectors", security_owner="security", lifecycle_owner="observability.catalog",
            runtime_gate="vendor_management.enabled"):
    return VendorClass(key, label, authoritative_owner, integration_owner, security_owner, lifecycle_owner,
                       provider_type, runtime_gate, tuple(deep_links))


VENDOR_REGISTRY = (
    _vendor("software_vendors", "Software Vendors", "productivity",
            ("/vendor-management", "/integration")),
    _vendor("custodians", "Custodians", "custodian", ("/integration?provider_type=custodian", "/portfolio")),
    _vendor("tax_providers", "Tax Providers", "tax", ("/integration?provider_type=tax", "/tax")),
    _vendor("insurance_carriers", "Insurance Carriers", "other",
            ("/integration", "/insurance"), authoritative_owner="organization_service",
            lifecycle_owner="insurance_licensing"),
    _vendor("cloud_providers", "Cloud Providers", "other", ("/observability", "/integration"),
            lifecycle_owner="observability.catalog"),
    _vendor("communication_providers", "Communication Providers", "productivity",
            ("/integration?provider_type=productivity", "/communications")),
    _vendor("infrastructure_providers", "Infrastructure Providers", "other",
            ("/observability",), lifecycle_owner="observability.catalog"),
    _vendor("identity_providers", "Identity Providers", "other", ("/security", "/integration"),
            authoritative_owner="security.providers", security_owner="security.providers"),
)

_VENDOR_BY_KEY = {v.key: v for v in VENDOR_REGISTRY}


# --- technology lifecycle registry -------------------------------------------

@dataclass(frozen=True)
class TechnologyLifecycle:
    key: str
    label: str
    category: str              # production_system | saas | infrastructure | subscription | license | certificate | integration | identity
    owner: str                 # the authoritative owner (or "not_configured")
    lifecycle_owner: str       # the authoritative lifecycle owner
    renewal_owner: str         # the authoritative renewal owner (or "not_configured")
    support_owner: str         # the authoritative support owner
    runtime_gate: str = "lifecycle.enabled"


def _tech(key, label, category, owner, *, lifecycle_owner="observability.catalog",
          renewal_owner="not_configured", support_owner="observability.catalog"):
    return TechnologyLifecycle(key, label, category, owner, lifecycle_owner, renewal_owner, support_owner)


TECHNOLOGY_LIFECYCLE_REGISTRY = (
    _tech("production_systems", "Production Systems", "production_system", "observability.catalog"),
    _tech("saas_platforms", "SaaS Platforms", "saas", "integration.connectors",
          lifecycle_owner="integration.connectors"),
    _tech("infrastructure_services", "Infrastructure Services", "infrastructure", "observability.catalog"),
    _tech("subscriptions", "Subscriptions", "subscription", "not_configured", renewal_owner="not_configured"),
    _tech("licenses", "Producer Licenses", "license", "insurance_licensing",
          lifecycle_owner="insurance_licensing", renewal_owner="insurance_licensing"),
    _tech("certificates", "Certificates", "certificate", "security.secrets",
          lifecycle_owner="security.secrets", renewal_owner="security.secrets"),
    _tech("integrations", "Integrations", "integration", "integration.connectors",
          lifecycle_owner="integration.sync", renewal_owner="integration.connectors"),
    _tech("identity_providers", "Identity Providers", "identity", "security.providers",
          lifecycle_owner="security.providers"),
)

_TECH_BY_KEY = {t.key: t for t in TECHNOLOGY_LIFECYCLE_REGISTRY}


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
    deep_link: str             # the authoritative vendor-owner surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # vendors
    _p("vendor_inventory", "integration.connectors", "integration.connectors.list_providers", "vendors",
       "count", "chart", "integration.view", "/integration",
       "Vendor inventory by provider type, from the Integration Platform provider registry (the vendor list "
       "of record). No second vendor platform."),
    _p("registered_vendors", "vendor_management", "vendor_management.registry", "vendors", "count", "list",
       "integration.view", "/vendor-management",
       "The registered vendor-class catalog — each naming its authoritative / integration / security / "
       "lifecycle owner."),
    _p("connected_vendors", "integration.connectors", "integration.connectors.list_connectors", "vendors",
       "count", "card", "integration.view", "/integration",
       "Connected vendor connectors, from the Integration Platform."),
    # licensing
    _p("certificates", "security.secrets", "security.secrets.list_certificates", "licensing", "count",
       "chart", "integration.view", "/security",
       "Certificates by status (valid / expiring / expired), from the Security certificate store. No key "
       "material — status only."),
    _p("producer_licenses", "insurance_licensing", "insurance_licensing.list_licenses", "licensing", "count",
       "card", "integration.view", "/insurance",
       "Producer licenses by status, from the Insurance licensing owner (requires insurance.licensing.read; "
       "unavailable otherwise)."),
    _p("credential_expiry", "integration.connectors", "integration.connectors.list_credentials", "licensing",
       "count", "card", "integration.view", "/integration",
       "Credential references with an expiry, from the Integration Platform (ciphertext stripped). No key "
       "material."),
    # lifecycle
    _p("registered_lifecycle", "vendor_management", "vendor_management.registry", "lifecycle", "count", "list",
       "integration.view", "/vendor-management",
       "The registered technology-lifecycle catalog — each naming its owner / lifecycle / renewal / support "
       "owner."),
    _p("production_systems", "observability.catalog", "observability.catalog.metrics", "lifecycle", "count",
       "gauge", "integration.view", "/observability",
       "Production-system inventory (operational vs total services), from the Observability service catalog. "
       "No second CMDB."),
    _p("service_environments", "observability.catalog", "observability.catalog.list_environment_profiles",
       "lifecycle", "count", "card", "integration.view", "/observability",
       "Registered environment profiles + deployment references, from the Observability service catalog."),
    # renewals
    _p("expiring_certificates", "security.secrets", "security.secrets.metrics", "renewals", "count", "card",
       "integration.view", "/security",
       "Expiring / expired certificates, from the Security certificate store (a renewal signal)."),
    _p("overdue_rotations", "security.secrets", "security.secrets.overdue_rotations", "renewals", "count",
       "card", "integration.view", "/security",
       "Secret references overdue for rotation, from the Security secret store (ciphertext stripped)."),
    _p("expiring_licenses", "insurance_licensing", "insurance_licensing.list_licenses", "renewals", "count",
       "card", "integration.view", "/insurance",
       "Producer licenses approaching / past expiry, from the Insurance licensing owner (requires "
       "insurance.licensing.read; unavailable otherwise)."),
    # third-party risk
    _p("security_risk", "security.incidents", "security.incidents.metrics", "risk", "count", "card",
       "security.view", "/security/incidents",
       "Open security incidents / findings / exceptions (third-party risk headline), from the Security "
       "incidents domain. No second risk engine."),
    _p("vendor_findings", "security.incidents", "security.incidents.metrics", "risk", "count", "card",
       "security.view", "/security/incidents",
       "Open security findings attributable to vendors/third parties, from the Security incidents domain."),
    _p("compliance_risk", "compliance_intelligence", "compliance_intelligence.supervisory_dashboard", "risk",
       "count", "card", "security.view", "/supervision",
       "Supervisory compliance risk (open reviews / exceptions), from Compliance Intelligence (requires "
       "compliance.supervise; unavailable otherwise)."),
    # operational dependencies
    _p("integration_dependencies", "integration", "integration.sync.metrics", "dependencies", "count", "card",
       "integration.view", "/integration",
       "Integration dependency health (sync failures / connector errors / conflicts), from the Integration "
       "Platform sync engine."),
    _p("integration_overview", "integration", "integration.service.overview_metrics", "dependencies", "count",
       "card", "integration.view", "/integration",
       "Firm integration overview (providers / connected connectors / failures), from the Integration "
       "Platform."),
    _p("service_dependencies", "observability.catalog", "observability.catalog.list_dependencies",
       "dependencies", "count", "card", "integration.view", "/observability",
       "Declared service dependencies, from the Observability service catalog."),
    # technology governance
    _p("technology_health", "observability.catalog", "observability.catalog.metrics", "governance", "count",
       "gauge", "integration.view", "/observability",
       "Technology health (operational vs degraded services), from the Observability service catalog."),
    _p("vendor_governance_score", "vendor_management", "vendor_management.compose", "governance", "percent",
       "gauge", "integration.view", "/vendor-management",
       "Deterministic vendor-governance indicator (connected vendors + valid certificates vs expiring/"
       "expired + open incidents) — composed from the authoritative owners. Advisory only; never mutates."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # vendors | operations | executive
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


VENDOR_DASHBOARDS = (
    _d("vendors", "vendor_management", "vendors", "vendor_management.enabled",
       ("vendor_inventory", "registered_vendors", "connected_vendors"),
       ("integration.view",), "/vendor-management?dashboard=vendors", ("integration",)),
    _d("licensing", "vendor_management", "vendors", "licensing.enabled",
       ("certificates", "producer_licenses", "credential_expiry"),
       ("integration.view",), "/vendor-management?dashboard=licensing", ("security", "insurance_licensing")),
    _d("lifecycle", "vendor_management", "operations", "lifecycle.enabled",
       ("registered_lifecycle", "production_systems", "service_environments"),
       ("integration.view",), "/vendor-management?dashboard=lifecycle", ("observability",)),
    _d("renewals", "vendor_management", "vendors", "licensing.enabled",
       ("expiring_certificates", "overdue_rotations", "expiring_licenses"),
       ("integration.view",), "/vendor-management?dashboard=renewals", ("security", "insurance_licensing")),
    _d("third_party_risk", "vendor_management", "operations", "vendor_management.enabled",
       ("security_risk", "vendor_findings", "compliance_risk"),
       ("integration.view",), "/vendor-management?dashboard=third_party_risk",
       ("security", "compliance_intelligence")),
    _d("operational_dependencies", "vendor_management", "operations", "vendor_management.enabled",
       ("integration_dependencies", "integration_overview", "service_dependencies"),
       ("integration.view",), "/vendor-management?dashboard=operational_dependencies",
       ("integration", "observability")),
    _d("technology_governance", "vendor_management", "executive", "vendor_management.enabled",
       ("technology_health", "vendor_governance_score", "registered_lifecycle"),
       ("integration.view",), "/vendor-management?dashboard=technology_governance",
       ("observability", "integration")),
)

_DASH_BY_KEY = {d.key: d for d in VENDOR_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def vendor_class(key) -> VendorClass | None:
    return _VENDOR_BY_KEY.get(key)


def technology_lifecycle(key) -> TechnologyLifecycle | None:
    return _TECH_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def vendor_registered(key) -> bool:
    return key in _VENDOR_BY_KEY


def lifecycle_registered(key) -> bool:
    return key in _TECH_BY_KEY


def coverage() -> dict:
    return {
        "vendor_classes": len(VENDOR_REGISTRY),
        "lifecycle_classes": len(TECHNOLOGY_LIFECYCLE_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(VENDOR_DASHBOARDS),
    }
