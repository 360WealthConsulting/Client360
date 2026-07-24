"""Integration Hub panel composition (Phase D.53).

Each panel's value is composed on READ by its authoritative owner — never persisted, never a second metric,
and never any secret / token / credential / client payload. Integration / sync / auth / webhook / connector /
API panels compose the AUTHORITATIVE D.24 Integration Platform (`integration.service` / `sync` / `connectors`
/ `webhooks` / `api` / `events`); event-routing panels compose the Event outbox diagnostics + Event registry.
Every compose is fail-closed (a source outage yields an unavailable panel, never an exception) and
self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel, never its value.
This layer NEVER mutates an external system, triggers synchronization, invokes an API, refreshes a token,
reconnects a system, or changes an integration setting — it only composes counts + status.
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


# --- declarative-registry panels ----------------------------------------------------------------------

def _registered_integrations(principal, pdef):
    try:
        by_type = {}
        for i in registry.INTEGRATION_REGISTRY:
            by_type[i.provider_type] = by_type.get(i.provider_type, 0) + 1
        return _result(pdef, {"count": len(registry.INTEGRATION_REGISTRY),
                              "integrations": [i.key for i in registry.INTEGRATION_REGISTRY],
                              "by_type": by_type})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_connectors(principal, pdef):
    try:
        by_protocol = {}
        for c in registry.CONNECTOR_REGISTRY:
            by_protocol[c.protocol] = by_protocol.get(c.protocol, 0) + 1
        return _result(pdef, {"count": len(registry.CONNECTOR_REGISTRY),
                              "connectors": [c.key for c in registry.CONNECTOR_REGISTRY],
                              "by_protocol": by_protocol})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform: overview / connectors -------------------------------------------------------

def _integration_overview(principal, pdef):
    try:
        from app.services.integration.service import overview_metrics
        m = overview_metrics(principal)
        return _result(pdef, {"providers": m.get("providers", 0),
                              "connected_connectors": m.get("connected_connectors", 0),
                              "sync_failures": m.get("sync_failures", 0),
                              "connector_errors": m.get("connector_errors", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _connected_connectors(principal, pdef):
    try:
        from app.services.integration.connectors import list_connectors
        return _result(pdef, {"connected": len(list_connectors(status="connected"))})
    except Exception:
        return _result(pdef, None, available=False)


def _connector_status(principal, pdef):
    try:
        from app.services.integration.connectors import list_connectors
        by_status = {}
        for r in list_connectors():
            s = r.get("status") or "not_connected"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _providers(principal, pdef):
    try:
        from app.services.integration.connectors import list_providers
        by_type = {}
        for r in list_providers():
            t = r.get("provider_type") or "other"
            by_type[t] = by_type.get(t, 0) + 1
        return _result(pdef, {"count": sum(by_type.values()), "by_type": by_type})
    except Exception:
        return _result(pdef, None, available=False)


def _credential_status(principal, pdef):
    try:
        from app.services.integration.connectors import list_credentials
        return _result(pdef, {"credential_references": len(list_credentials())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform: sync ------------------------------------------------------------------------

def _sync_metrics(principal, pdef):
    try:
        from app.services.integration.sync import metrics
        m = metrics(principal)
        return _result(pdef, {"sync_failures": m.get("sync_failures", 0),
                              "connector_errors": m.get("connector_errors", 0),
                              "unresolved_conflicts": m.get("unresolved_conflicts", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _sync_runs(principal, pdef):
    try:
        from app.services.integration.sync import list_sync_runs
        by_status = {}
        for st in ("pending", "running", "succeeded", "failed", "partial"):
            q = list_sync_runs(status=st, page=1, page_size=1)
            n = q.get("total", 0)
            if n:
                by_status[st] = n
        return _result(pdef, {"by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _sync_profiles(principal, pdef):
    try:
        from app.services.integration.sync import list_sync_profiles
        return _result(pdef, {"profiles": len(list_sync_profiles())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform: webhooks --------------------------------------------------------------------

def _webhook_metrics(principal, pdef):
    try:
        from app.services.integration.webhooks import metrics
        m = metrics(principal)
        return _result(pdef, {"webhook_failures": m.get("webhook_failures", 0),
                              "unverified_endpoints": m.get("unverified_endpoints", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _webhook_endpoints(principal, pdef):
    try:
        from app.services.integration.webhooks import list_endpoints
        return _result(pdef, {"endpoints": len(list_endpoints())})
    except Exception:
        return _result(pdef, None, available=False)


def _webhook_deliveries(principal, pdef):
    try:
        from app.services.integration.webhooks import list_deliveries
        by_status = {}
        for st in ("pending", "delivered", "failed"):
            q = list_deliveries(status=st, page=1, page_size=1)
            n = q.get("total", 0)
            if n:
                by_status[st] = n
        return _result(pdef, {"by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


# --- Integration Platform: api -------------------------------------------------------------------------

def _api_metrics(principal, pdef):
    try:
        from app.services.integration.api import metrics
        m = metrics(principal)
        return _result(pdef, {"active_api_clients": m.get("active_api_clients", 0),
                              "api_requests": m.get("api_requests", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _api_clients(principal, pdef):
    try:
        from app.services.integration.api import list_api_clients
        by_status = {}
        for r in list_api_clients():
            s = r.get("status") or "inactive"
            by_status[s] = by_status.get(s, 0) + 1
        return _result(pdef, {"by_status": by_status})
    except Exception:
        return _result(pdef, None, available=False)


def _api_usage(principal, pdef):
    try:
        from app.services.integration.api import list_usage
        return _result(pdef, {"usage_records": len(list_usage())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Event outbox / registry (event routing) -----------------------------------------------------------

def _event_activity(principal, pdef):
    try:
        from app.services.events.diagnostics import event_counts
        c = event_counts()
        return _result(pdef, {"by_status": c.get("by_status", {}),
                              "dead_lettered": c.get("dead_lettered", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _event_subscribers(principal, pdef):
    try:
        from app.services.events.diagnostics import subscriber_health
        rows = subscriber_health()
        with_consumer = sum(1 for r in rows if r.get("has_consumer"))
        return _result(pdef, {"event_types": len(rows), "with_consumer": with_consumer})
    except Exception:
        return _result(pdef, None, available=False)


def _integration_events(principal, pdef):
    try:
        from app.services.integration.events import list_definitions
        return _result(pdef, {"definitions": len(list_definitions())})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "registered_integrations": _registered_integrations,
    "integration_overview": _integration_overview,
    "connected_connectors": _connected_connectors,
    "sync_metrics": _sync_metrics,
    "sync_runs": _sync_runs,
    "sync_profiles": _sync_profiles,
    "credential_status": _credential_status,
    "connector_status": _connector_status,
    "api_clients": _api_clients,
    "webhook_metrics": _webhook_metrics,
    "webhook_endpoints": _webhook_endpoints,
    "webhook_deliveries": _webhook_deliveries,
    "registered_connectors": _registered_connectors,
    "providers": _providers,
    "api_metrics": _api_metrics,
    "api_usage": _api_usage,
    "event_activity": _event_activity,
    "event_subscribers": _event_subscribers,
    "integration_events": _integration_events,
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
