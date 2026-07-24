"""Integration Hub registries (Phase D.53) — the declarative catalogs of the integration-hub layer.

Four frozen, declarative catalogs; the layer owns NO persistence and defines NO new integration platform,
ESB, API gateway, synchronization engine, webhook processor, message broker, or event bus:

  * INTEGRATION_REGISTRY — every connected platform (Schwab, AssetMark, Wealthbox, TaxDome, Drake,
    Betterment, Guideline, ADP, Microsoft 365, Google Workspace, DocuSign, QuickBooks, IRS, State e-file,
    carrier APIs, CRM connectors, email providers, calendar providers). Each names its authoritative owner,
    connection owner, authentication owner, synchronization owner, runtime gate, and deep links. The layer
    connects NOTHING — it references these owners.
  * CONNECTOR_REGISTRY — every connector class. Each names its protocol, authentication, polling owner,
    webhook owner, retry owner, monitoring owner, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * INTEGRATION_DASHBOARDS — every integration dashboard (owner, audience, runtime gate, panel list, required
    capabilities, navigation, refresh, governing services).

Governance verifies every integration + connector is registered, every panel names an authoritative owner +
source + deep link, and that this layer never becomes a second integration platform, ESB, API gateway,
synchronization engine, webhook processor, or event bus.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- integration registry ----------------------------------------------------

@dataclass(frozen=True)
class Integration:
    key: str
    label: str
    authoritative_owner: str   # the authoritative service that owns the integration record
    connection_owner: str      # the authoritative connection/connector owner (never re-implemented)
    authentication_owner: str  # the authoritative auth/credential owner (never re-implemented)
    synchronization_owner: str # the authoritative sync owner (never re-implemented)
    provider_type: str         # custodian | crm | tax | payroll | recordkeeper | productivity | filing | ...
    runtime_gate: str
    deep_links: tuple


def _int(key, label, provider_type, deep_links, *, authoritative_owner="integration.connectors",
         connection_owner="integration.connectors", authentication_owner="integration.connectors",
         synchronization_owner="integration.sync", runtime_gate="integrations.enabled"):
    return Integration(key, label, authoritative_owner, connection_owner, authentication_owner,
                       synchronization_owner, provider_type, runtime_gate, tuple(deep_links))


INTEGRATION_REGISTRY = (
    _int("schwab", "Schwab", "custodian", ("/integration?provider=schwab", "/portfolio")),
    _int("assetmark", "AssetMark", "custodian", ("/integration?provider=assetmark", "/portfolio")),
    _int("wealthbox", "Wealthbox", "crm", ("/integration?provider=wealthbox",)),
    _int("taxdome", "TaxDome", "tax", ("/integration?provider=taxdome", "/tax")),
    _int("drake", "Drake", "tax", ("/integration?provider=drake", "/tax")),
    _int("betterment", "Betterment", "custodian", ("/integration?provider=betterment", "/portfolio")),
    _int("guideline", "Guideline", "recordkeeper", ("/integration?provider=guideline", "/benefits")),
    _int("adp", "ADP", "payroll", ("/integration?provider=adp", "/benefits")),
    _int("microsoft_365", "Microsoft 365", "productivity",
         ("/integration?provider=microsoft_365", "/microsoft365/status"),
         authentication_owner="microsoft_identity", synchronization_owner="microsoft_sync"),
    _int("google_workspace", "Google Workspace", "productivity",
         ("/integration?provider=google_workspace",)),
    _int("docusign", "DocuSign", "productivity", ("/integration?provider=docusign",),
         connection_owner="portal.providers", authentication_owner="portal.providers"),
    _int("quickbooks", "QuickBooks", "recordkeeper", ("/integration?provider=quickbooks",)),
    _int("irs", "IRS", "filing", ("/integration?provider=irs", "/tax"),
         synchronization_owner="tax_filing_providers"),
    _int("state_efile", "State e-file", "filing", ("/integration?provider=state_efile", "/tax"),
         synchronization_owner="tax_filing_providers"),
    _int("carrier_apis", "Carrier APIs", "custodian", ("/integration?provider=carrier_apis", "/insurance"),
         connection_owner="insurance_integrations", authentication_owner="insurance_integrations"),
    _int("crm_connectors", "CRM Connectors", "crm", ("/integration?provider_type=crm",)),
    _int("email_providers", "Email Providers", "productivity",
         ("/integration?provider_type=productivity", "/communications"),
         synchronization_owner="communications"),
    _int("calendar_providers", "Calendar Providers", "productivity",
         ("/integration?provider_type=productivity", "/scheduling"),
         synchronization_owner="scheduling"),
)

_INT_BY_KEY = {i.key: i for i in INTEGRATION_REGISTRY}


# --- connector registry ------------------------------------------------------

@dataclass(frozen=True)
class Connector:
    key: str
    label: str
    protocol: str              # file | rest | graph | oidc | webhook | port
    authentication: str        # none | api_key | oauth2 | msal | hmac | disabled
    polling_owner: str         # the authoritative polling owner (never re-implemented)
    webhook_owner: str         # the authoritative webhook owner (never re-implemented)
    retry_owner: str           # the authoritative retry owner (never re-implemented)
    monitoring_owner: str      # the authoritative monitoring owner
    runtime_gate: str = "connectors.enabled"


def _conn(key, label, protocol, authentication, *, polling_owner="integration.sync",
          webhook_owner="integration.webhooks", retry_owner="integration.sync",
          monitoring_owner="integration.service"):
    return Connector(key, label, protocol, authentication, polling_owner, webhook_owner, retry_owner,
                     monitoring_owner)


CONNECTOR_REGISTRY = (
    _conn("file_import", "File Import", "file", "none", polling_owner="importers",
          webhook_owner="none", retry_owner="import_jobs"),
    _conn("rest_api", "REST API", "rest", "oauth2"),
    _conn("graph_api", "Microsoft Graph", "graph", "msal", polling_owner="microsoft_sync",
          retry_owner="microsoft_sync", monitoring_owner="microsoft_identity"),
    _conn("oidc_identity", "OIDC Identity", "oidc", "oauth2", polling_owner="none", webhook_owner="none",
          retry_owner="none", monitoring_owner="integrations.identity"),
    _conn("webhook_inbound", "Inbound Webhook", "webhook", "hmac", polling_owner="none",
          webhook_owner="integration.webhooks", retry_owner="integration.webhooks"),
    _conn("webhook_outbound", "Outbound Webhook", "webhook", "hmac", polling_owner="none",
          webhook_owner="integration.webhooks", retry_owner="integration.webhooks"),
    _conn("esign_provider", "E-Signature Provider", "rest", "oauth2", polling_owner="portal.providers",
          webhook_owner="portal.signatures", retry_owner="portal.providers",
          monitoring_owner="portal.providers"),
    _conn("insurance_port", "Insurance Port", "port", "disabled", polling_owner="insurance_integrations",
          webhook_owner="none", retry_owner="insurance_integrations",
          monitoring_owner="insurance_integrations"),
    _conn("tax_filing_port", "Tax Filing Port", "port", "disabled", polling_owner="tax_filing_providers",
          webhook_owner="none", retry_owner="tax_filing_providers",
          monitoring_owner="tax_filing_providers"),
)

_CONN_BY_KEY = {c.key: c for c in CONNECTOR_REGISTRY}


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
    deep_link: str             # the authoritative connector-owner surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # integrations
    _p("registered_integrations", "integration_hub", "integration_hub.registry", "integrations", "count",
       "list", "integration.view", "/integration",
       "The registered connected-platform catalog — each naming its authoritative / connection / auth / sync "
       "owner. No second integration platform."),
    _p("integration_overview", "integration", "integration.service.overview_metrics", "integrations", "count",
       "card", "integration.view", "/integration",
       "Firm integration overview (providers / connected connectors / failures), from the Integration "
       "Platform (the authoritative integration owner)."),
    _p("connected_connectors", "integration", "integration.connectors.list_connectors", "integrations",
       "count", "card", "integration.view", "/integration",
       "Connected connectors, from the Integration Platform. No second connector framework."),
    # synchronization
    _p("sync_metrics", "integration", "integration.sync.metrics", "sync", "count", "card", "integration.view",
       "/integration",
       "Synchronization health (failures / connector errors / unresolved conflicts), from the Integration "
       "Platform sync engine. No second synchronization engine."),
    _p("sync_runs", "integration", "integration.sync.list_sync_runs", "sync", "count", "chart",
       "integration.view", "/integration",
       "Sync runs by status, from the Integration Platform (run metadata only — no provider I/O)."),
    _p("sync_profiles", "integration", "integration.sync.list_sync_profiles", "sync", "count", "card",
       "integration.view", "/integration",
       "Registered sync profiles, from the Integration Platform."),
    # authentication
    _p("credential_status", "integration", "integration.connectors.list_credentials", "auth", "count", "card",
       "integration.view", "/integration",
       "Registered credential references (ciphertext/pointers only — never plaintext), from the Integration "
       "Platform. No second authentication store."),
    _p("connector_status", "integration", "integration.connectors.list_connectors", "auth", "count", "chart",
       "integration.view", "/integration",
       "Connectors by connection status (connected / error / not_connected / disabled), from the Integration "
       "Platform."),
    _p("api_clients", "integration", "integration.api.list_api_clients", "auth", "count", "chart",
       "integration.view", "/integration",
       "API clients by status, from the Integration Platform."),
    # webhooks
    _p("webhook_metrics", "integration", "integration.webhooks.metrics", "webhooks", "count", "card",
       "integration.view", "/integration",
       "Webhook health (failures / unverified endpoints), from the Integration Platform. No second webhook "
       "processor."),
    _p("webhook_endpoints", "integration", "integration.webhooks.list_endpoints", "webhooks", "count", "card",
       "integration.view", "/integration",
       "Registered webhook endpoints (signing secret stripped), from the Integration Platform."),
    _p("webhook_deliveries", "integration", "integration.webhooks.list_deliveries", "webhooks", "count",
       "chart", "integration.view", "/integration",
       "Webhook deliveries by status, from the Integration Platform delivery ledger."),
    # connectors
    _p("registered_connectors", "integration_hub", "integration_hub.registry", "connectors", "count", "list",
       "integration.view", "/integration",
       "The registered connector catalog — each naming its protocol / auth / polling / webhook / retry / "
       "monitoring owner."),
    _p("providers", "integration", "integration.connectors.list_providers", "connectors", "count", "chart",
       "integration.view", "/integration",
       "Registered integration providers by type, from the Integration Platform provider registry."),
    # api health
    _p("api_metrics", "integration", "integration.api.metrics", "api", "count", "card", "integration.view",
       "/integration",
       "API connectivity (active clients / requests), from the Integration Platform. No second API gateway."),
    _p("api_usage", "integration", "integration.api.list_usage", "api", "count", "card", "integration.view",
       "/integration", "API usage records, from the Integration Platform usage ledger."),
    # event routing
    _p("event_activity", "events", "events.diagnostics.event_counts", "events", "count", "chart",
       "integration.view", "/events",
       "Event outbox activity by delivery status, from the Event outbox diagnostics. No second event bus."),
    _p("event_subscribers", "events", "events.diagnostics.subscriber_health", "events", "count", "card",
       "integration.view", "/events",
       "Event subscriber health, from the Event registry (consumer coverage)."),
    _p("integration_events", "integration", "integration.events.list_definitions", "events", "count", "card",
       "integration.view", "/integration",
       "Registered integration event definitions, from the Integration Platform event catalog."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # integrations | operations | executive
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


INTEGRATION_DASHBOARDS = (
    _d("integrations", "integration_hub", "integrations", "integrations.enabled",
       ("registered_integrations", "integration_overview", "connected_connectors"),
       ("integration.view",), "/integration-hub?dashboard=integrations", ("integration",)),
    _d("synchronization", "integration_hub", "integrations", "synchronization.enabled",
       ("sync_metrics", "sync_runs", "sync_profiles"),
       ("integration.view",), "/integration-hub?dashboard=synchronization", ("integration",)),
    _d("authentication", "integration_hub", "integrations", "integrations.enabled",
       ("credential_status", "connector_status", "api_clients"),
       ("integration.view",), "/integration-hub?dashboard=authentication", ("integration",)),
    _d("webhooks", "integration_hub", "integrations", "connectors.enabled",
       ("webhook_metrics", "webhook_endpoints", "webhook_deliveries"),
       ("integration.view",), "/integration-hub?dashboard=webhooks", ("integration",)),
    _d("connectors", "integration_hub", "integrations", "connectors.enabled",
       ("registered_connectors", "providers", "connector_status"),
       ("integration.view",), "/integration-hub?dashboard=connectors", ("integration",)),
    _d("api_health", "integration_hub", "operations", "integrations.enabled",
       ("api_metrics", "api_clients", "api_usage"),
       ("integration.view",), "/integration-hub?dashboard=api_health", ("integration",)),
    _d("event_routing", "integration_hub", "operations", "integrations.enabled",
       ("event_activity", "event_subscribers", "integration_events"),
       ("integration.view",), "/integration-hub?dashboard=event_routing", ("events", "integration")),
)

_DASH_BY_KEY = {d.key: d for d in INTEGRATION_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def integration(key) -> Integration | None:
    return _INT_BY_KEY.get(key)


def connector(key) -> Connector | None:
    return _CONN_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def integration_registered(key) -> bool:
    return key in _INT_BY_KEY


def connector_registered(key) -> bool:
    return key in _CONN_BY_KEY


def coverage() -> dict:
    return {
        "integrations": len(INTEGRATION_REGISTRY),
        "connectors": len(CONNECTOR_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(INTEGRATION_DASHBOARDS),
    }
