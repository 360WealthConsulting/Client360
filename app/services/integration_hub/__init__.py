"""Enterprise Integration Hub & Connected Platform Governance layer (Phase D.53).

A governed, READ-ONLY composition that provides a single governed view of all external systems,
integrations, synchronization health, API connectivity, and connector status — WITHOUT introducing a second
integration platform, ESB, API gateway, synchronization engine, webhook processor, message broker, or event
bus. It composes named integration dashboards from declarative integration + connector + panel registries
over the platform's AUTHORITATIVE integration owners: the D.24 Integration Platform (`integration.service`
overview, `sync`, `connectors`, `webhooks`, `api`, `events`), the Event outbox + Event registry, and the
M365 / insurance / signature connectors. It defines no new metrics, owns no persistence, and never mutates
an external system, triggers synchronization, invokes an API, refreshes a token, reconnects a system, or
changes an integration setting; every panel is explainable, deep-links to its authoritative connector-owner
surface, and carries counts + status only — never a secret, token, credential, or client payload.
"""
from .service import (
    client_integrations,
    compose_dashboard,
    get_panel,
    household_integrations,
    integration_summary,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "integration_summary",
    "client_integrations",
    "household_integrations",
]
