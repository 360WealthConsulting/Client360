"""Enterprise Vendor Management, Third-Party Risk & Technology Lifecycle Governance layer (Phase D.56).

A governed, READ-ONLY composition that provides a single governed operational view of vendors, software,
platforms, contracts, licensing, lifecycle, and third-party risk — WITHOUT introducing a second
vendor-management platform, procurement system, contract repository, CMDB, asset inventory, licensing
platform, or risk engine. It composes named vendor dashboards from declarative vendor + technology-lifecycle
+ panel registries over the platform's AUTHORITATIVE owners: the Integration Platform provider registry
(`integration.connectors` — the vendor inventory of record), the Security certificate & secret store
(`security.secrets`), the Observability service catalog (technology lifecycle), Insurance licensing (producer
licenses), and Security incidents + Compliance Intelligence (third-party risk). Procurement / contracts /
subscriptions have no authoritative owner in the platform today — those are declared registry classes with a
`not_configured` owner, never a fabricated record. It defines no new metrics, owns no persistence, and never
modifies a vendor, renews a license, terminates a contract, alters an integration, or changes a subscription;
every panel is explainable, deep-links to its authoritative vendor-owner surface, and carries counts + status
only — never a contract, credential, license key, secret, or procurement payload.
"""
from .service import (
    client_technology,
    compose_dashboard,
    get_panel,
    household_technology,
    list_dashboards,
    vendor_summary,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "vendor_summary",
    "client_technology",
    "household_technology",
]
