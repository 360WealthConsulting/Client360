"""Automation Orchestration panel composition (Phase D.51).

Each panel's value is composed on READ by its authoritative owner — never persisted, never executed, never a
second metric, and never any workflow payload. Workflow panels compose the AUTHORITATIVE Workflow Engine
(`workflow_automation.workflow_metrics`) + the Workflow Orchestration facade; automation-job panels compose
the Automation engine (ADR-027); trigger/action panels compose the Trigger engine + action catalog; event
panels compose the Event outbox diagnostics; scheduling/notification panels compose Scheduling +
Communications. Every compose is fail-closed (a source outage yields an unavailable panel, never an
exception) and self-restricts: a principal lacking the panel's capability is shown a ``restricted`` panel,
never its value. This layer NEVER executes an automation, launches a workflow, fires a trigger, routes an
event, or mutates anything — it only composes counts + status.
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


# --- Workflow Engine (the authoritative workflow-execution owner) --------------------------------------

def _workflow_metrics():
    from app.services.workflow_automation import workflow_metrics
    return workflow_metrics()


def _workflow_status(principal, pdef):
    try:
        return _result(pdef, {"by_status": _workflow_metrics().get("by_status", {})})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_instances(principal, pdef):
    try:
        from app.services.workflow_orchestration.service import list_instances
        q = list_instances(principal, page=1, page_size=1)
        return _result(pdef, {"total": q.get("total", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_pending_approvals(principal, pdef):
    try:
        return _result(pdef, {"pending_approvals": _workflow_metrics().get("pending_approvals", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _open_escalations(principal, pdef):
    try:
        return _result(pdef, {"open_escalations": _workflow_metrics().get("open_escalations", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _workflow_templates(principal, pdef):
    try:
        from app.services.workflow_orchestration.service import templates
        return _result(pdef, {"count": len(templates())})
    except Exception:
        return _result(pdef, None, available=False)


# --- Automation engine (scheduled jobs / runs, ADR-027) ------------------------------------------------

def _automation_metrics(principal):
    from app.services.automation.service import metrics
    return metrics(principal)


def _automation_jobs(principal, pdef):
    try:
        m = _automation_metrics(principal)
        return _result(pdef, {"jobs": m.get("jobs", 0), "enabled_jobs": m.get("enabled_jobs", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _automation_runs(principal, pdef):
    try:
        m = _automation_metrics(principal)
        return _result(pdef, {"runs": m.get("runs", 0), "running": m.get("running", 0),
                              "succeeded": m.get("succeeded", 0), "failed": m.get("failed", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _failed_runs(principal, pdef):
    try:
        return _result(pdef, {"failed": _automation_metrics(principal).get("failed", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Trigger engine + action catalog -------------------------------------------------------------------

def _registered_triggers(principal, pdef):
    try:
        from app.services.workflow_orchestration.triggers import list_triggers
        rows = list_triggers(principal)
        by_event = {}
        for r in rows:
            ev = r.get("event_type") or "unknown"
            by_event[ev] = by_event.get(ev, 0) + 1
        return _result(pdef, {"configured": len(rows), "by_event_type": by_event})
    except Exception:
        return _result(pdef, None, available=False)


def _trigger_types(principal, pdef):
    try:
        return _result(pdef, {"count": len(registry.TRIGGER_REGISTRY),
                              "types": [t.key for t in registry.TRIGGER_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


def _registered_actions(principal, pdef):
    try:
        from app.services.workflow_orchestration.actions import list_actions
        catalog = list_actions()
        return _result(pdef, {"registered_actions": len(registry.ACTION_REGISTRY),
                              "engine_actions": len(catalog),
                              "actions": [a.key for a in registry.ACTION_REGISTRY]})
    except Exception:
        return _result(pdef, {"registered_actions": len(registry.ACTION_REGISTRY),
                              "actions": [a.key for a in registry.ACTION_REGISTRY]})


def _registered_automations(principal, pdef):
    try:
        by_trigger = {}
        for a in registry.AUTOMATION_REGISTRY:
            by_trigger[a.trigger_source] = by_trigger.get(a.trigger_source, 0) + 1
        return _result(pdef, {"count": len(registry.AUTOMATION_REGISTRY),
                              "by_trigger_source": by_trigger,
                              "automations": [a.key for a in registry.AUTOMATION_REGISTRY]})
    except Exception:
        return _result(pdef, None, available=False)


# --- Event outbox diagnostics (the authoritative event bus) --------------------------------------------

def _event_activity(principal, pdef):
    try:
        from app.services.events.diagnostics import event_counts
        c = event_counts()
        return _result(pdef, {"by_status": c.get("by_status", {}),
                              "dead_lettered": c.get("dead_lettered", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _awaiting_delivery(principal, pdef):
    try:
        from app.services.events.diagnostics import events_by_domain
        d = events_by_domain()
        return _result(pdef, {"awaiting_delivery_by_domain": d.get("awaiting_delivery_by_domain", {})})
    except Exception:
        return _result(pdef, None, available=False)


def _dead_letters(principal, pdef):
    try:
        from app.services.events.diagnostics import event_counts
        return _result(pdef, {"dead_lettered": event_counts().get("dead_lettered", 0)})
    except Exception:
        return _result(pdef, None, available=False)


# --- Scheduling + Communications -----------------------------------------------------------------------

def _scheduling_activity(principal, pdef):
    try:
        from app.services.scheduling.service import metrics
        m = metrics(principal)
        return _result(pdef, {"total": m.get("total", 0), "upcoming": m.get("upcoming", 0),
                              "completed": m.get("completed", 0), "cancelled": m.get("cancelled", 0)})
    except Exception:
        return _result(pdef, None, available=False)


def _notification_activity(principal, pdef):
    try:
        from app.services.communications.service import metrics
        m = metrics(principal)
        return _result(pdef, {"open_conversations": m.get("open_conversations", 0),
                              "messages": m.get("messages", 0), "sent": m.get("sent", 0)})
    except Exception:
        return _result(pdef, None, available=False)


_COMPUTE = {
    "registered_automations": _registered_automations,
    "workflow_templates": _workflow_templates,
    "automation_jobs": _automation_jobs,
    "registered_actions": _registered_actions,
    "workflow_status": _workflow_status,
    "workflow_instances": _workflow_instances,
    "workflow_pending_approvals": _workflow_pending_approvals,
    "registered_triggers": _registered_triggers,
    "trigger_types": _trigger_types,
    "event_activity": _event_activity,
    "automation_runs": _automation_runs,
    "scheduling_activity": _scheduling_activity,
    "notification_activity": _notification_activity,
    "open_escalations": _open_escalations,
    "awaiting_delivery": _awaiting_delivery,
    "failed_runs": _failed_runs,
    "dead_letters": _dead_letters,
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
