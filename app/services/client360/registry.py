"""Client 360 Workspace registry (Phase D.40) — the sections and quick actions.

Section order here is the tab order. A section's ``capability`` gates its tab (a section the principal
cannot open is never shown — no shown-then-403); ``None`` means it rides the page-level ``client.read``.
Quick actions are deep links into the AUTHORITATIVE create workflow (the workspace never mutates) — each
gated by the capability needed to use it and prefilled with the client's id.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import sections


@dataclass(frozen=True)
class SectionDef:
    key: str
    label: str
    capability: str | None   # None → rides the page-level client.read
    builder: object


SECTIONS = (
    SectionDef("dashboard", "Dashboard", None, sections.dashboard),
    SectionDef("summary", "Summary", None, sections.summary),
    SectionDef("financial", "Financial", None, sections.financial),
    SectionDef("tax", "Tax", "tax.read", sections.tax),
    SectionDef("insurance", "Insurance", "insurance.read", sections.insurance),
    SectionDef("benefits", "Benefits", "benefits.read", sections.benefits),
    SectionDef("opportunities", "Opportunities", "opportunity.view", sections.opportunities),
    # The former standalone "Vault" tab is consolidated into "Documents" (canonical + Vault, one list).
    SectionDef("documents", "Documents", "documents.view", sections.documents),
    SectionDef("meetings", "Meetings", None, sections.meetings),
    SectionDef("compliance", "Compliance", "compliance.review.read", sections.compliance),
    SectionDef("communications", "Communications", "communications.view", sections.communications),
    SectionDef("knowledge", "Knowledge", None, sections.knowledge),
    SectionDef("recommendations", "Recommendations", None, sections.recommendations),
    SectionDef("compliance_summary", "Compliance Oversight", "compliance.supervise", sections.compliance_summary),
    SectionDef("executive", "Executive", "analytics.executive", sections.executive),
    SectionDef("timeline", "Timeline", "timeline.read", sections.timeline),
    SectionDef("tasks", "Tasks", None, sections.tasks),
    SectionDef("notes", "Notes", None, sections.notes),
    SectionDef("audit", "Audit", "audit.read", sections.audit),
    SectionDef("relationships", "Relationships", None, sections.relationships),
    SectionDef("work", "Work", "advisor_work.read", sections.work),
    SectionDef("operational_workload", "Operational Workload", "capacity.read", sections.operational_workload),
    SectionDef("document_intelligence", "Document Intelligence", "documents.view", sections.document_intelligence),
    SectionDef("automation_history", "Automation History", "automation.view", sections.automation_history),
    SectionDef("data_governance", "Data Governance", "governance.view", sections.data_governance),
    SectionDef("external_integrations", "External Integrations", "integration.view", sections.external_integrations),
    SectionDef("security_access", "Security & Access", "security.view", sections.security_access),
    SectionDef("business_continuity", "Business Continuity", "observability.view", sections.business_continuity),
    SectionDef("technology_dependencies", "Technology Dependencies", "integration.view", sections.technology_dependencies),
    SectionDef("financial_relationship", "Financial Relationship", "analytics.executive", sections.financial_relationship),
    SectionDef("risk_controls", "Risk & Controls", "compliance.supervise", sections.risk_controls),
    SectionDef("evidence_readiness", "Evidence & Supervisory Readiness", "compliance.supervise", sections.evidence_readiness),
    SectionDef("operational_impact", "Operational Impact", "observability.view", sections.operational_impact),
    SectionDef("servicing_team", "Servicing Team", "capacity.read", sections.servicing_team),
    SectionDef("knowledge_documentation", "Documentation", "documents.view", sections.knowledge_documentation),
    SectionDef("change_impact", "Change Impact", "observability.view", sections.change_impact),
    SectionDef("platform_dependencies", "Platform Dependencies", "observability.view", sections.platform_dependencies),
    SectionDef("authorization_context", "Authorization Context", "observability.view", sections.authorization_context),
    SectionDef("data_governance_metadata", "Data Lineage & Provenance", "governance.view", sections.data_governance_metadata),
)

SECTION_KEYS = tuple(s.key for s in SECTIONS)


@dataclass(frozen=True)
class QuickAction:
    key: str
    label: str
    capability: str
    # href(person_id, household_id) → the deep link into the authoritative create surface.
    href: object


def _pref(base, person_id, household_id):
    if person_id:
        return f"{base}?person_id={person_id}"
    if household_id:
        return f"{base}?household_id={household_id}"
    return base


QUICK_ACTIONS = (
    QuickAction("schedule_meeting", "Schedule Meeting", "scheduling.view",
                lambda p, h: _pref("/scheduling", p, h)),
    # The workspace's OWN Documents tab, which since the workspace-upload work carries an
    # owner-aware upload form. It used to deep-link to /document-library, which sent staff out of
    # the client and made them re-establish the owner the workspace already knew.
    QuickAction("upload_document", "Upload Document", "documents.view",
                lambda p, h: (f"/client/{p}?tab=documents" if p
                              else (f"/client/household/{h}?tab=documents" if h
                                    else "/document-library"))),
    QuickAction("add_note", "Add Note", "client.read",
                lambda p, h: (f"/people/{p}/notes" if p else "/people")),
    # /tasks -- the canonical staff dashboard over the authoritative client `tasks` table
    # (ADR-025). This used to point into Operations (/operations/items, then
    # /operations/task-list), which reads `operational_tasks` -- a DIFFERENT store that cannot hold
    # a client task, so the client's real tasks were never there and anything created was invisible
    # on /client/{id}?tab=tasks. Gated on task.read to match the capability /tasks actually
    # requires; it was work.read, which does not gate /tasks at all.
    QuickAction("create_task", "Create Task", "task.read",
                lambda p, h: _pref("/tasks", p, h)),
    QuickAction("start_tax_return", "Start Tax Return", "tax.read",
                lambda p, h: _pref("/tax/intake", p, h)),
    QuickAction("create_opportunity", "Create Opportunity", "opportunity.view",
                lambda p, h: _pref("/opportunities", p, h)),
    QuickAction("start_insurance_case", "Start Insurance Case", "insurance.read",
                lambda p, h: _pref("/insurance", p, h)),
    QuickAction("send_secure_message", "Send Secure Message", "communications.read",
                lambda p, h: _pref("/communications", p, h)),
    QuickAction("generate_meeting_prep", "Generate Meeting Prep", "client.read",
                lambda p, h: (f"/workspace/meetings/{p}" if p else "/workspace")),
)


def visible_sections(principal):
    return [s for s in SECTIONS if s.capability is None or principal.can(s.capability)]


def visible_quick_actions(principal, person_id, household_id):
    return [{"key": a.key, "label": a.label, "href": a.href(person_id, household_id)}
            for a in QUICK_ACTIONS if principal.can(a.capability)]
