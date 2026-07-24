"""Automation Orchestration registries (Phase D.51) — the declarative catalogs of the automation-orchestration
layer.

Five frozen, declarative catalogs; the layer owns NO persistence and defines NO new metrics, workflow engine,
scheduler, or event bus:

  * AUTOMATION_REGISTRY — every business automation (annual reviews, onboarding, document collection, client
    communications, compliance/licensing reminders, tax/insurance/advisory workflow). Each names its OWNER,
    workflow owner, trigger source, execution owner, runtime gate, scheduling owner, notification owner, and
    deep links. The layer executes NOTHING — it composes these owners.
  * TRIGGER_REGISTRY — every trigger type (scheduled, event, workflow, document, compliance, manual,
    lifecycle). Each names its owner, source, execution owner, and runtime gate.
  * ACTION_REGISTRY — every automation action (send notification, create work item, request document, launch
    workflow, generate reminder, escalate). Each names its authoritative owner, execution service,
    permissions, and runtime gate.
  * PANEL_REGISTRY — every dashboard panel (owner, source, measure, permission, deep link, explainability).
  * ORCHESTRATION_DASHBOARDS — every automation dashboard (owner, audience, runtime gate, panel list,
    required capabilities, navigation, refresh, governing services).

Governance verifies every automation + trigger + action + panel + dashboard is registered, every panel names
an authoritative owner + source + deep link, and that this layer never becomes a second workflow engine,
scheduler, rules engine, orchestration engine, event bus, or automation platform.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


# --- automation registry -----------------------------------------------------

@dataclass(frozen=True)
class Automation:
    key: str
    label: str
    owner: str                 # the authoritative business-process owner
    workflow_owner: str        # the authoritative workflow-execution owner (never re-implemented)
    trigger_source: str        # the authoritative trigger owner
    execution_owner: str       # the authoritative execution owner (workflow/automation engine)
    scheduling_owner: str      # the authoritative scheduling owner
    notification_owner: str    # the authoritative notification owner
    runtime_gate: str
    deep_links: tuple


def _auto(key, label, owner, trigger_source, deep_links, *,
          workflow_owner="workflow_automation", execution_owner="workflow_orchestration",
          scheduling_owner="scheduling", notification_owner="communications",
          runtime_gate="orchestration.enabled"):
    return Automation(key, label, owner, workflow_owner, trigger_source, execution_owner, scheduling_owner,
                      notification_owner, runtime_gate, tuple(deep_links))


AUTOMATION_REGISTRY = (
    _auto("annual_reviews", "Annual Reviews", "annual_review", "scheduled",
          ("/workflows?type=annual_review", "/annual-review")),
    _auto("onboarding", "Client Onboarding", "workflow_automation", "event",
          ("/workflows?type=onboarding",)),
    _auto("document_collection", "Document Collection", "document_platform", "document",
          ("/workflows?type=document_collection", "/document-intelligence")),
    _auto("client_communications", "Client Communications", "communications", "event",
          ("/communications", "/workflows"), notification_owner="communications"),
    _auto("compliance_reminders", "Compliance Reminders", "compliance", "compliance",
          ("/workflows?type=compliance", "/supervision")),
    _auto("licensing_reminders", "Licensing Reminders", "insurance_licensing", "scheduled",
          ("/workflows?type=licensing", "/supervision")),
    _auto("tax_workflow", "Tax Workflow", "tax_domain", "workflow",
          ("/workflows?type=tax", "/tax")),
    _auto("insurance_workflow", "Insurance Workflow", "insurance", "workflow",
          ("/workflows?type=insurance", "/insurance")),
    _auto("advisory_workflow", "Advisory Workflow", "advisor_work", "event",
          ("/workflows?type=advisory", "/work")),
)

_AUTO_BY_KEY = {a.key: a for a in AUTOMATION_REGISTRY}


# --- trigger registry --------------------------------------------------------

@dataclass(frozen=True)
class TriggerType:
    key: str
    label: str
    owner: str                 # the authoritative trigger owner
    source: str                # the authoritative source the trigger fires from
    execution_owner: str       # the authoritative execution owner (never re-implemented)
    runtime_gate: str = "triggers.enabled"


def _trig(key, label, owner, source, execution_owner):
    return TriggerType(key, label, owner, source, execution_owner)


TRIGGER_REGISTRY = (
    _trig("scheduled", "Scheduled", "automation", "automation.runner (schedules)", "automation"),
    _trig("event", "Domain Event", "workflow_orchestration.triggers", "events.outbox",
          "workflow_automation"),
    _trig("workflow", "Workflow", "workflow_automation", "workflow_automation (steps)",
          "workflow_automation"),
    _trig("document", "Document", "document_platform", "document.* events", "workflow_automation"),
    _trig("compliance", "Compliance", "compliance", "compliance.* events", "workflow_automation"),
    _trig("manual", "Manual", "workflow_orchestration", "operator action", "workflow_orchestration"),
    _trig("lifecycle", "Lifecycle", "workflow_automation", "entity lifecycle events",
          "workflow_automation"),
)

_TRIG_BY_KEY = {t.key: t for t in TRIGGER_REGISTRY}


# --- action registry ---------------------------------------------------------

@dataclass(frozen=True)
class AutomationAction:
    key: str
    label: str
    authoritative_owner: str   # the authoritative owner of the effect (never re-implemented here)
    execution_service: str     # the service that performs the action
    permissions: tuple         # capabilities the authoritative execution requires
    runtime_gate: str = "automation.enabled"


def _act(key, label, authoritative_owner, execution_service, permissions):
    return AutomationAction(key, label, authoritative_owner, execution_service, tuple(permissions))


ACTION_REGISTRY = (
    _act("send_notification", "Send Notification", "communications", "communications.service",
         ("communications.manage",)),
    _act("create_work_item", "Create Work Item", "work_management", "workflow_orchestration.actions",
         ("work.write",)),
    _act("request_document", "Request Document", "document_platform", "workflow_orchestration.actions",
         ("documents.edit",)),
    _act("launch_workflow", "Launch Workflow", "workflow_automation", "workflow_automation.launch_workflow",
         ("workflow.execute",)),
    _act("generate_reminder", "Generate Reminder", "automation", "automation.runner",
         ("automation.execute",)),
    _act("escalate", "Escalate", "workflow_automation", "workflow_automation.evaluate_sla",
         ("workflow.execute",)),
)

_ACT_BY_KEY = {a.key: a for a in ACTION_REGISTRY}


# --- panel registry ----------------------------------------------------------

@dataclass(frozen=True)
class PanelDef:
    key: str
    owner: str                 # authoritative owning service
    source: str                # the authoritative read the value is composed from
    measure: str               # inventory | workflow | trigger | execution | pending | failed
    unit: str
    viz: str
    permission: str            # capability required to see the panel value (else restricted)
    deep_link: str             # the authoritative workflow/automation surface to drill into
    explainability: str
    refresh: str = "on_view"
    lifecycle: str = "active"


def _p(key, owner, source, measure, unit, viz, permission, deep_link, explainability, *,
       refresh="on_view", lifecycle="active"):
    return PanelDef(key, owner, source, measure, unit, viz, permission, deep_link, explainability,
                    refresh, lifecycle)


PANEL_REGISTRY = (
    # inventory
    _p("registered_automations", "automation_orchestration", "automation_orchestration.registry", "inventory",
       "count", "list", "automation.view", "/automation-orchestration",
       "The registered business automations catalog — each naming its authoritative workflow / trigger / "
       "execution / scheduling / notification owner. No second automation platform."),
    _p("workflow_templates", "workflow_orchestration", "workflow_orchestration.service.templates", "inventory",
       "count", "card", "automation.view", "/workflow-automation",
       "Registered workflow templates, from the Workflow Orchestration facade."),
    _p("automation_jobs", "automation", "automation.service.metrics", "inventory", "count", "card",
       "automation.view", "/automation",
       "Registered scheduled-automation jobs (total + enabled), from the Automation engine (ADR-027)."),
    _p("registered_actions", "automation_orchestration", "workflow_orchestration.actions.list_actions",
       "inventory", "count", "list", "automation.view", "/automation-orchestration",
       "The automation actions catalog — each action names its authoritative execution owner. This layer "
       "executes none of them."),
    # workflow
    _p("workflow_status", "workflow_automation", "workflow_automation.workflow_metrics", "workflow", "count",
       "chart", "automation.view", "/workflows",
       "Workflow instances by status (firm-level), from the Workflow Engine. No second workflow engine."),
    _p("workflow_instances", "workflow_orchestration", "workflow_orchestration.service.list_instances",
       "workflow", "count", "card", "automation.view", "/workflows",
       "In-scope workflow instances, from the Workflow Orchestration facade (record-scoped)."),
    _p("workflow_pending_approvals", "workflow_automation", "workflow_automation.workflow_metrics",
       "pending", "count", "card", "automation.view", "/workflows",
       "Pending workflow approvals (firm-level), from the Workflow Engine."),
    # trigger
    _p("registered_triggers", "workflow_orchestration.triggers", "workflow_orchestration.triggers.list_triggers",
       "trigger", "count", "list", "automation.view", "/workflow-automation",
       "Configured domain-event triggers, from the Trigger engine. No second trigger engine."),
    _p("trigger_types", "automation_orchestration", "automation_orchestration.registry", "trigger", "count",
       "chart", "automation.view", "/automation-orchestration",
       "The registered trigger-type catalog (scheduled/event/workflow/document/compliance/manual/lifecycle)."),
    _p("event_activity", "events", "events.diagnostics.event_counts", "trigger", "count", "chart",
       "automation.view", "/events",
       "Event outbox activity by delivery status, from the Event outbox diagnostics. No second event bus."),
    # execution
    _p("automation_runs", "automation", "automation.service.metrics", "execution", "count", "chart",
       "automation.view", "/automation",
       "Automation run execution (total / running / succeeded / failed), from the Automation engine."),
    _p("scheduling_activity", "scheduling", "scheduling.service.metrics", "execution", "count", "card",
       "automation.view", "/scheduling",
       "Scheduling activity (total / upcoming / completed / cancelled), from the Scheduling domain."),
    _p("notification_activity", "communications", "communications.service.metrics", "execution", "count",
       "card", "automation.view", "/communications",
       "Notification / messaging activity (open conversations / messages / sent), from Communications."),
    # pending
    _p("open_escalations", "workflow_automation", "workflow_automation.workflow_metrics", "pending", "count",
       "card", "automation.view", "/workflows",
       "Open workflow escalations (firm-level), from the Workflow Engine."),
    _p("awaiting_delivery", "events", "events.diagnostics.events_by_domain", "pending", "count", "chart",
       "automation.view", "/events",
       "Events awaiting delivery by domain, from the Event outbox diagnostics."),
    # failed
    _p("failed_runs", "automation", "automation.service.metrics", "failed", "count", "card",
       "automation.view", "/automation",
       "Failed / dead automation runs, from the Automation engine."),
    _p("dead_letters", "events", "events.diagnostics.event_counts", "failed", "count", "card",
       "automation.view", "/events",
       "Dead-lettered events, from the Event outbox diagnostics."),
)

_PANEL_BY_KEY = {p.key: p for p in PANEL_REGISTRY}


# --- dashboard registry ------------------------------------------------------

@dataclass(frozen=True)
class DashboardDef:
    key: str
    owner: str
    audience: str              # operations | executive | advisor | records
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


ORCHESTRATION_DASHBOARDS = (
    _d("automation_inventory", "automation_orchestration", "operations", "automation.enabled",
       ("registered_automations", "workflow_templates", "automation_jobs", "registered_actions"),
       ("automation.view",), "/automation-orchestration?dashboard=automation_inventory",
       ("workflow_orchestration", "automation")),
    _d("workflow_automation", "automation_orchestration", "operations", "orchestration.enabled",
       ("workflow_status", "workflow_instances", "workflow_pending_approvals"),
       ("automation.view",), "/automation-orchestration?dashboard=workflow_automation",
       ("workflow_automation", "workflow_orchestration")),
    _d("trigger_activity", "automation_orchestration", "operations", "triggers.enabled",
       ("registered_triggers", "trigger_types", "event_activity"),
       ("automation.view",), "/automation-orchestration?dashboard=trigger_activity",
       ("workflow_orchestration", "events")),
    _d("execution_status", "automation_orchestration", "operations", "orchestration.enabled",
       ("automation_runs", "scheduling_activity", "notification_activity"),
       ("automation.view",), "/automation-orchestration?dashboard=execution_status",
       ("automation", "scheduling", "communications")),
    _d("pending_automation", "automation_orchestration", "operations", "automation.enabled",
       ("workflow_pending_approvals", "open_escalations", "awaiting_delivery"),
       ("automation.view",), "/automation-orchestration?dashboard=pending_automation",
       ("workflow_automation", "events")),
    _d("failed_automation", "automation_orchestration", "operations", "automation.enabled",
       ("failed_runs", "dead_letters", "open_escalations"),
       ("automation.view",), "/automation-orchestration?dashboard=failed_automation",
       ("automation", "events", "workflow_automation")),
)

_DASH_BY_KEY = {d.key: d for d in ORCHESTRATION_DASHBOARDS}


# --- lookups -----------------------------------------------------------------

def automation(key) -> Automation | None:
    return _AUTO_BY_KEY.get(key)


def trigger_type(key) -> TriggerType | None:
    return _TRIG_BY_KEY.get(key)


def action(key) -> AutomationAction | None:
    return _ACT_BY_KEY.get(key)


def panel(key) -> PanelDef | None:
    return _PANEL_BY_KEY.get(key)


def dashboard(key) -> DashboardDef | None:
    return _DASH_BY_KEY.get(key)


def panel_registered(key) -> bool:
    return key in _PANEL_BY_KEY


def dashboard_registered(key) -> bool:
    return key in _DASH_BY_KEY


def automation_registered(key) -> bool:
    return key in _AUTO_BY_KEY


def trigger_registered(key) -> bool:
    return key in _TRIG_BY_KEY


def action_registered(key) -> bool:
    return key in _ACT_BY_KEY


def coverage() -> dict:
    return {
        "automations": len(AUTOMATION_REGISTRY),
        "triggers": len(TRIGGER_REGISTRY),
        "actions": len(ACTION_REGISTRY),
        "panels": len(PANEL_REGISTRY),
        "dashboards": len(ORCHESTRATION_DASHBOARDS),
    }
