"""Advisor Workspace orchestrator (Phase D.38).

``get_workspace`` composes the personalized advisor home: a greeting, the TODAY summary row, the
deterministic PRIORITIES panel, and the personalized WIDGET GRID (the advisor's order, with hidden
widgets removed and pinned favorites floated to the top). It reuses the existing record-scoped daily
dashboard for the detail panels + priorities, computes each widget through the projection-backed
sources (graceful fallback), and applies the advisor's saved preferences. Read-only, capability-aware,
never bypasses RBAC — a widget the principal cannot open is never assembled.
"""
from __future__ import annotations

from datetime import datetime

from app.services.advisor_workspace import get_daily_dashboard

from . import digest, preferences
from .registry import WIDGETS, WidgetDef
from .widgets import FIRM_TZ, compute_widget


def _greeting(now) -> str:
    h = now.hour
    if h < 12:
        return "Good Morning"
    if h < 17:
        return "Good Afternoon"
    return "Good Evening"


def _widget_view(key: str, principal, *, now, filters, pinned: bool) -> dict:
    w: WidgetDef = WIDGETS[key]
    return {
        "key": w.key, "title": w.title, "section": w.section, "kind": w.kind,
        "detail_href": w.detail_href, "projection_backed": w.projection_backed,
        "description": w.description, "pinned": pinned,
        "data": compute_widget(key, principal, now=now, filters=filters.get(key)),
    }


def get_workspace(principal, *, now=None) -> dict:
    """Compose the personalized advisor home for ``principal``. Never mutates."""
    now = now or datetime.now(FIRM_TZ)
    prefs = preferences.get_preferences(principal.user_id)

    # Eligible = the widgets whose capability the principal holds, in the advisor's order.
    eligible = [k for k in prefs["order"] if k in WIDGETS and principal.can(WIDGETS[k].capability)]
    pinned = [k for k in prefs["pinned"] if k in eligible]
    hidden = set(prefs["hidden"])
    # A widget is visible unless hidden — but pinning overrides hiding.
    visible = [k for k in eligible if k not in hidden or k in pinned]
    ordered = [k for k in pinned if k in visible] + [k for k in visible if k not in pinned]

    widgets = [_widget_view(k, principal, now=now, filters=prefs["filters"], pinned=(k in pinned))
               for k in ordered]
    # Eligible-but-hidden widgets (for the customize panel's "hidden" list).
    hidden_meta = [{"key": WIDGETS[k].key, "title": WIDGETS[k].title, "section": WIDGETS[k].section}
                   for k in eligible if k in hidden and k not in pinned]

    dashboard = get_daily_dashboard(principal)

    # Operational Intelligence panel (D.46) — a read-only composition over the authoritative recommendation
    # sources. Guarded so a failure/gate-off never breaks the advisor home.
    try:
        from app.services.recommendations import workspace_recommendations
        operational_intelligence = workspace_recommendations(principal)
    except Exception:
        operational_intelligence = {"enabled": False, "recommendations": [], "workload": {}}

    # Advisor-visible compliance tasks (D.47) — the NON-supervisory projection: only the governed advisor
    # compliance recommendations, never supervisory-only findings/reviewer identities/approval state.
    try:
        from app.services.compliance_intelligence import advisor_compliance_tasks
        compliance_tasks = advisor_compliance_tasks(principal)
    except Exception:
        compliance_tasks = {"enabled": False, "tasks": []}

    # Executive Insights panel (D.48) — firm executive summary, composed over the SINGLE Analytics Registry.
    # A non-executive principal gets a non-leaking restricted envelope. Guarded so a gate-off never breaks home.
    try:
        from app.services.executive_intelligence import executive_summary
        executive_insights = executive_summary(principal)
    except Exception:
        executive_insights = {"enabled": False, "authorized": False, "widgets": [], "kpis": {}}

    # Capacity Planning panel (D.49) — a read-only practice-management summary (firm utilization + workload +
    # staffing signals), composed over the authoritative capacity/work-queue owners. A principal lacking
    # capacity.read still gets their book-scoped workload, never firm capacity. Guarded so a gate-off never
    # breaks home; this panel never assigns work or modifies staffing.
    try:
        from app.services.practice_management import practice_summary
        capacity_planning = practice_summary(principal)
    except Exception:
        capacity_planning = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Document Intelligence panel (D.50) — a read-only records summary (inventory + missing documents +
    # expiring/completeness) composed over the authoritative Document Platform + Governance retention +
    # Compliance Intelligence. Guarded so a gate-off never breaks home; this panel never alters metadata,
    # archives, or deletes documents. Counts + status only — never document content.
    try:
        from app.services.document_intelligence import document_summary
        document_intelligence = document_summary(principal)
    except Exception:
        document_intelligence = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Automation Status panel (D.51) — a read-only automation summary (workflow status + pending approvals +
    # failed/escalated) composed over the authoritative Workflow / Automation / Event owners. Guarded so a
    # gate-off never breaks home; this panel never executes/launches/fires anything. Counts + status only.
    try:
        from app.services.automation_orchestration import automation_summary
        automation_status = automation_summary(principal)
    except Exception:
        automation_status = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Data Governance panel (D.52) — a read-only governance summary (validation issues + duplicate alerts +
    # governance overview + data-quality score) composed over the authoritative Governance package. Guarded
    # so a gate-off never breaks home; this panel never merges/alters/approves anything. Counts + status only.
    try:
        from app.services.data_governance import governance_summary
        data_governance = governance_summary(principal)
    except Exception:
        data_governance = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Integration Health panel (D.53) — a read-only integration summary (overview + sync + connector +
    # webhook health) composed over the authoritative Integration Platform. Guarded so a gate-off never
    # breaks home; this panel never connects/syncs/invokes/refreshes anything. Counts + status only.
    try:
        from app.services.integration_hub import integration_summary
        integration_health = integration_summary(principal)
    except Exception:
        integration_health = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Security Operations panel (D.54) — a read-only security summary (overview + MFA coverage + authorization
    # failures + audit integrity) composed over the authoritative Security domain + Identity + audit log.
    # Guarded so a gate-off never breaks home; this panel never authenticates/authorizes/alters anything.
    try:
        from app.services.security_operations import security_summary
        security_operations = security_summary(principal)
    except Exception:
        security_operations = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Operational Resilience panel (D.55) — a read-only continuity summary (resilience score + infrastructure
    # availability + service incidents + backup coverage) composed over the authoritative Observability +
    # Runtime owners. Guarded so a gate-off never breaks home; this panel never backs up/restores/alters
    # anything. Counts + status only.
    try:
        from app.services.business_continuity import continuity_summary
        operational_resilience = continuity_summary(principal)
    except Exception:
        operational_resilience = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Technology & Vendor Health panel (D.56) — a read-only vendor summary (governance score + expiring
    # certificates + integration dependencies + vendor inventory) composed over the authoritative Integration
    # + Security + Observability owners. Guarded so a gate-off never breaks home; this panel never
    # modifies/renews/terminates anything. Counts + status only.
    try:
        from app.services.vendor_management import vendor_summary
        technology_vendor_health = vendor_summary(principal)
    except Exception:
        technology_vendor_health = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Financial Performance panel (D.57) — a read-only firm financial summary (performance score + recurring
    # revenue + commission revenue + collections + vendor dependencies) composed over the authoritative
    # insurance commission ledger + portfolio AUM owner + the single Analytics Registry. Guarded so a gate-off
    # never breaks home; this panel never bills/invoices/pays/posts anything. Aggregate totals + status only.
    try:
        from app.services.financial_operations import firm_financial_summary
        financial_performance = firm_financial_summary(principal)
    except Exception:
        financial_performance = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Enterprise Risk & Controls panel (D.58) — a read-only enterprise-risk summary (posture + open findings +
    # security incidents + workflow escalations + vendor risk + continuity gaps + financial-control status +
    # control coverage) composed over the authoritative Compliance / Exception / Security / Vendor / Continuity
    # / Financial owners. Guarded so a gate-off never breaks home; this panel never mutates anything and an
    # absent finding never certifies compliance. Counts + status only.
    try:
        from app.services.enterprise_risk import risk_summary
        enterprise_risk = risk_summary(principal)
    except Exception:
        enterprise_risk = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Regulatory Readiness panel (D.59) — a read-only operational-readiness summary (derived readiness coverage
    # + evidence availability + documentation gaps + unresolved findings + blocked certifications + licensing +
    # stale evidence) composed over the authoritative Compliance / Document / Exception / Licensing owners.
    # Guarded so a gate-off never breaks home; this panel never mutates anything. Operational readiness does
    # NOT constitute regulatory certification, and an absent finding is never compliance.
    try:
        from app.services.regulatory_readiness import readiness_summary
        regulatory_readiness = readiness_summary(principal)
    except Exception:
        regulatory_readiness = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Operational Status panel (D.60) — a read-only operational-resilience summary (executive operational
    # posture + service health + degraded services + reliability incidents + open alerts + active maintenance
    # windows + resilience gaps) composed over the authoritative Observability / Security / Business Continuity
    # owners. Guarded so a gate-off never breaks home; this panel never mutates anything. Operational posture
    # is NOT a certification that production is healthy, and an absent incident is not health.
    try:
        from app.services.operational_resilience import resilience_summary
        operational_status = resilience_summary(principal)
    except Exception:
        operational_status = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Capacity & Workload panel (D.61) — a read-only workforce/capacity summary (executive workforce posture +
    # firm capacity utilization + queue health + advisor workload + staffing readiness + staffing gaps +
    # automation workload) composed over the authoritative Operations capacity / Work Queue / Practice
    # Management owners. Guarded so a gate-off never breaks home; this panel never assigns/schedules anything.
    # An operational summary only — never a certified staffing figure and never an HR record.
    try:
        from app.services.capacity_planning import capacity_summary
        capacity_workload = capacity_summary(principal)
    except Exception:
        capacity_workload = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    # Knowledge & SOPs panel (D.62) — a read-only knowledge/documentation summary (executive knowledge posture
    # + documentation completeness + documentation gaps + SOP coverage + publication readiness + knowledge
    # gaps + knowledge health) composed over the authoritative Document Platform / Document Intelligence /
    # retention owners. Guarded so a gate-off never breaks home; this panel never creates/edits/publishes
    # anything. A documentation-coverage summary only — never fabricated documentation or institutional
    # knowledge.
    try:
        from app.services.knowledge_management import knowledge_summary
        knowledge_sops = knowledge_summary(principal)
    except Exception:
        knowledge_sops = {"enabled": False, "panels": [], "kpis": {}, "dashboards": []}

    return {
        "greeting": _greeting(now),
        "display_name": getattr(principal, "display_name", None) or "there",
        "date": now.date(),
        "now": now,
        "today": digest.today_counts(principal, now=now),
        "priorities": digest.priorities(dashboard),
        "widgets": widgets,
        "hidden_widgets": hidden_meta,
        "presets": preferences.list_presets(principal.user_id),
        "active_preset_id": prefs.get("active_preset_id"),
        "can_personalize": principal.can("workspace.personalize"),
        "daily": dashboard,
        "operational_intelligence": operational_intelligence,
        "compliance_tasks": compliance_tasks,
        "executive_insights": executive_insights,
        "capacity_planning": capacity_planning,
        "document_intelligence": document_intelligence,
        "automation_status": automation_status,
        "data_governance": data_governance,
        "integration_health": integration_health,
        "security_operations": security_operations,
        "operational_resilience": operational_resilience,
        "technology_vendor_health": technology_vendor_health,
        "financial_performance": financial_performance,
        "enterprise_risk": enterprise_risk,
        "regulatory_readiness": regulatory_readiness,
        "operational_status": operational_status,
        "capacity_workload": capacity_workload,
        "knowledge_sops": knowledge_sops,
    }
