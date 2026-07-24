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
    SectionDef("summary", "Summary", None, sections.summary),
    SectionDef("financial", "Financial", None, sections.financial),
    SectionDef("tax", "Tax", "tax.read", sections.tax),
    SectionDef("insurance", "Insurance", "insurance.read", sections.insurance),
    SectionDef("benefits", "Benefits", "benefits.read", sections.benefits),
    SectionDef("opportunities", "Opportunities", "opportunity.view", sections.opportunities),
    SectionDef("documents", "Documents", "documents.view", sections.documents),
    SectionDef("meetings", "Meetings", None, sections.meetings),
    SectionDef("compliance", "Compliance", "compliance.review.read", sections.compliance),
    SectionDef("communications", "Communications", "communications.view", sections.communications),
    SectionDef("knowledge", "Knowledge", None, sections.knowledge),
    SectionDef("recommendations", "Recommendations", None, sections.recommendations),
    SectionDef("compliance_summary", "Compliance Oversight", "compliance.supervise", sections.compliance_summary),
    SectionDef("executive", "Executive", "analytics.executive", sections.executive),
    SectionDef("timeline", "Activity", "timeline.read", sections.timeline),
    SectionDef("relationships", "Relationships", None, sections.relationships),
    SectionDef("work", "Work", "advisor_work.read", sections.work),
    SectionDef("operational_workload", "Operational Workload", "capacity.read", sections.operational_workload),
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
    QuickAction("upload_document", "Upload Document", "documents.view",
                lambda p, h: _pref("/document-library", p, h)),
    QuickAction("add_note", "Add Note", "client.read",
                lambda p, h: (f"/people/{p}/notes" if p else "/people")),
    QuickAction("create_task", "Create Task", "work.read",
                lambda p, h: _pref("/operations/items", p, h)),
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
