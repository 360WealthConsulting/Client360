"""Work-queue source adapters (Phase D.39).

Each adapter reads a bounded, record-scoped, actionable set from ONE authoritative service and maps it
to UnifiedWorkItems. Adapters NEVER mutate and NEVER read an ``rm_*`` projection table directly. They
fail CLOSED: any error → an empty list (an item never appears because scope could not be determined).
The three legacy work sources (tasks, workflow steps, exceptions) are read through the existing
``work_management.work_items`` (which already merges + record-scopes them) — the queue does not build a
second task/workflow/exception engine. Per-item ``capability`` lets the service suppress items the
principal may not see (never shown-then-403).
"""
from __future__ import annotations

from datetime import UTC, datetime

from .contract import make_item

# Per-adapter candidate cap — bounded fetch, merged + paginated in the service (no unbounded loads).
CANDIDATE_LIMIT = 200


class Adapter:
    """Base adapter. Subclasses set domain/capability and implement ``_fetch`` + ``_to_items``."""
    domain = ""
    capability = ""       # base capability required to query this source at all
    label = ""
    actions = ()          # actions this domain supports (validated again per item/principal)

    def enabled_for(self, principal) -> bool:
        return bool(self.capability) and principal.can(self.capability)

    def list_items(self, principal, *, now=None, limit=CANDIDATE_LIMIT):
        now = now or datetime.now(UTC)
        if not self.enabled_for(principal):
            return []
        try:
            return list(self._to_items(self._fetch(principal, limit), principal, now))
        except Exception:
            return []   # fail closed — never surface an item whose scope could not be resolved

    def _fetch(self, principal, limit):
        raise NotImplementedError

    def _to_items(self, rows, principal, now):
        raise NotImplementedError


# --- core: tasks + workflow steps + exceptions via work_management.work_items ------------------------

class CoreWorkAdapter(Adapter):
    """Tasks, workflow steps, and exceptions — read through the authoritative merged, record-scoped
    ``work_management.work_items``. Emits three source domains; exception items carry the
    ``exception.read`` capability so they are suppressed for principals without it."""
    domain = "core"
    capability = "work.read"
    label = "Work"

    def _fetch(self, principal, limit):
        from app.services.work_management import work_items
        return work_items(principal)[:limit * 3]

    def _to_items(self, rows, principal, now):
        for r in rows:
            et = r.get("entity_type")
            if et == "task":
                yield make_item(
                    source_domain="tasks", source_type="task", source_id=r["id"],
                    title=r.get("title"), status=r.get("status") or "open",
                    priority=r.get("priority"), capability="work.read",
                    deep_link=(f"/people/{r['person_id']}" if r.get("person_id") else "/tasks"),
                    allowed_actions=("open", "claim", "assign"),
                    due_at=r.get("due_date"), sla_due_at=r.get("sla_due_at"),
                    person_id=r.get("person_id"), household_id=r.get("household_id"),
                    team=r.get("team_id"), created_at=r.get("created_at"),
                    updated_at=r.get("updated_at"), now=now,
                    source_reference={"entity_type": "task", "entity_id": r["id"]})
            elif et == "workflow_step":
                yield make_item(
                    source_domain="workflow", source_type="workflow_step", source_id=r["id"],
                    title=r.get("title") or r.get("name"), status=r.get("status") or "active",
                    priority=r.get("priority"), capability="work.read",
                    deep_link=f"/workflow-automation/{r.get('parent_workflow_id') or r.get('workflow_instance_id')}",
                    allowed_actions=("open", "claim", "assign", "complete"),
                    due_at=r.get("due_date"), sla_due_at=r.get("sla_due_at"),
                    person_id=r.get("person_id"), household_id=r.get("household_id"),
                    workflow_instance_id=r.get("parent_workflow_id") or r.get("workflow_instance_id"),
                    created_at=r.get("created_at"), updated_at=r.get("updated_at"), now=now,
                    source_reference={"entity_type": "workflow_step", "entity_id": r["id"]})
            elif et == "exception":
                yield make_item(
                    source_domain="exceptions", source_type="exception", source_id=r["id"],
                    title=r.get("title") or r.get("code"), status=r.get("status") or "open",
                    priority=r.get("priority") or r.get("severity"), capability="exception.read",
                    deep_link=f"/exceptions/{r['id']}",
                    allowed_actions=("open", "claim", "assign", "acknowledge", "resolve"),
                    due_at=r.get("due_date"), sla_due_at=r.get("sla_due_at"),
                    escalated=bool((r.get("escalation_level") or 0) > 0),
                    assignee_user_id=r.get("owner_user_id"), team=r.get("owner_team_id") or r.get("team_id"),
                    person_id=r.get("person_id"), household_id=r.get("household_id"),
                    exception_id=r["id"], summary=r.get("domain"), now=now,
                    source_reference={"entity_type": "exception", "entity_id": r["id"],
                                      "domain": r.get("domain")})


# --- standalone adapters ----------------------------------------------------------------------------

class AdvisorWorkAdapter(Adapter):
    domain = "advisor_work"
    capability = "advisor_work.read"
    label = "Advisor Work"
    actions = ("open",)   # claim/complete are handled on the /advisor-work surface (expected_status flow)

    def _fetch(self, principal, limit):
        from app.services.advisor_work import list_work
        return list_work(principal, page=1, page_size=limit).get("rows", [])

    def _to_items(self, rows, principal, now):
        for r in rows:
            if str(r.get("status")) in ("completed", "cancelled", "archived"):
                continue
            yield make_item(
                source_domain="advisor_work", source_type="advisor_work_item", source_id=r["id"],
                title=r.get("title") or r.get("recommendation_type") or "Advisor work",
                status=r.get("status") or "new", priority=r.get("priority"),
                capability="advisor_work.read", deep_link=f"/advisor-work/{r['id']}",
                allowed_actions=("open",), due_at=r.get("due_date"),
                assignee_user_id=r.get("owner_principal_id"), person_id=r.get("person_id"),
                household_id=r.get("household_id"), created_at=r.get("created_at"),
                updated_at=r.get("updated_at"), now=now,
                source_reference={"status": r.get("status")})


class ComplianceAdapter(Adapter):
    domain = "compliance"
    capability = "compliance.review.read"
    label = "Compliance"
    actions = ("open",)

    def _fetch(self, principal, limit):
        from app.services.compliance.reviews import list_reviews
        return list_reviews(principal, page=1, page_size=limit).get("rows", [])

    def _to_items(self, rows, principal, now):
        open_states = {"pending_submission", "pending_assignment", "pending_review",
                       "blocked_pending_authorized_reviewer"}
        for r in rows:
            if r.get("status") not in open_states:
                continue
            yield make_item(
                source_domain="compliance", source_type="compliance_review", source_id=r["id"],
                title=r.get("governing_rule") or "Compliance review", status=r.get("status"),
                priority=r.get("policy_gate") or "normal", capability="compliance.review.read",
                deep_link=f"/compliance/reviews/{r['id']}", allowed_actions=self.actions,
                assignee_user_id=r.get("assigned_reviewer_principal_id"),
                person_id=r.get("person_id"), household_id=r.get("household_id"),
                summary=r.get("policy_gate"), created_at=r.get("submitted_at") or r.get("created_at"),
                updated_at=r.get("updated_at"), now=now,
                source_reference={"governing_rule": r.get("governing_rule")})


class DocumentAdapter(Adapter):
    domain = "documents"
    capability = "documents.view"
    label = "Documents"
    actions = ("open", "claim", "assign", "approve")

    def _fetch(self, principal, limit):
        from app.services.document_platform.service import list_documents
        return list_documents(principal, status="review", page=1, page_size=limit).get("rows", [])

    def _to_items(self, rows, principal, now):
        for r in rows:
            yield make_item(
                source_domain="documents", source_type="document", source_id=r["id"],
                title=r.get("title") or r.get("classification") or f"Document {r['id']}",
                status=r.get("status") or "review", priority="normal", capability="documents.view",
                deep_link=f"/document-library/{r['id']}", allowed_actions=self.actions,
                due_at=r.get("review_due_at"), assignee_user_id=r.get("owner_user_id"),
                person_id=r.get("person_id"), household_id=r.get("household_id"),
                created_at=r.get("created_at"), updated_at=r.get("updated_at"), now=now,
                source_reference={"review_status": r.get("review_status")})


class TaxAdapter(Adapter):
    domain = "tax"
    capability = "tax.read"
    label = "Tax"
    actions = ("open", "claim", "assign")

    def _fetch(self, principal, limit):
        from app.services.tax_domain import list_engagements
        return list_engagements(principal)[:limit]

    def _to_items(self, rows, principal, now):
        closed = {"filed", "closed", "archived", "cancelled"}
        for r in rows:
            rid = r.get("return_id") or r.get("id")
            if rid is None or str(r.get("status")) in closed:
                continue
            yield make_item(
                source_domain="tax", source_type="tax_return", source_id=rid,
                title=(f"Tax return {rid}" + (f" ({r['tax_year']})" if r.get("tax_year") else "")),
                status=r.get("status") or "received", priority=r.get("priority"),
                capability="tax.read", deep_link=f"/tax/returns?return_id={rid}",
                allowed_actions=self.actions, due_at=r.get("due_date"),
                person_id=r.get("person_id"), household_id=r.get("household_id"),
                created_at=r.get("created_at"), now=now,
                source_reference={"entity_type": "tax_return", "entity_id": rid,
                                  "tax_year": r.get("tax_year")})


class InsuranceAdapter(Adapter):
    domain = "insurance"
    capability = "insurance.read"
    label = "Insurance"
    actions = ("open",)

    def _fetch(self, principal, limit):
        from app.services.insurance import list_cases
        return list_cases(principal, limit=limit)

    def _to_items(self, rows, principal, now):
        closed = {"issued", "declined", "closed"}
        for r in rows:
            if str(r.get("status")) in closed:
                continue
            yield make_item(
                source_domain="insurance", source_type="insurance_case", source_id=r["id"],
                title=(r.get("case_type") or "Insurance case").replace("_", " ").title(),
                status=r.get("status") or "open", priority="normal", capability="insurance.read",
                deep_link=f"/insurance/cases/{r['id']}", allowed_actions=self.actions,
                person_id=r.get("person_id"), household_id=r.get("household_id"),
                created_at=r.get("created_at"), updated_at=r.get("updated_at"), now=now,
                source_reference={"case_type": r.get("case_type")})


class OpportunityAdapter(Adapter):
    domain = "opportunities"
    capability = "opportunity.read"
    label = "Opportunities"
    actions = ("open",)

    def _fetch(self, principal, limit):
        from app.services.opportunity.service import list_opportunities
        return list_opportunities(principal, status="open", page=1, page_size=limit).get("rows", [])

    def _to_items(self, rows, principal, now):
        for r in rows:
            # A "follow-up" is an open opportunity with a next action due; skip those with no follow-up.
            if not r.get("next_action_date"):
                continue
            yield make_item(
                source_domain="opportunities", source_type="opportunity", source_id=r["id"],
                title=r.get("title") or "Opportunity follow-up", status=r.get("status") or "open",
                priority="normal", capability="opportunity.read",
                deep_link=f"/opportunities/{r['id']}", allowed_actions=self.actions,
                due_at=r.get("next_action_date"), summary=r.get("next_action"),
                assignee_user_id=r.get("primary_advisor_id"), person_id=r.get("person_id"),
                household_id=r.get("household_id"), created_at=r.get("created_at"),
                updated_at=r.get("updated_at"), now=now,
                source_reference={"next_action": r.get("next_action")})


class MeetingAdapter(Adapter):
    domain = "meetings"
    capability = "scheduling.view"
    label = "Meetings"
    actions = ("open",)

    def _fetch(self, principal, limit):
        from app.services.scheduling.service import list_meetings
        return list_meetings(principal, upcoming_only=True, page=1, page_size=limit).get("rows", [])

    def _to_items(self, rows, principal, now):
        for r in rows:
            yield make_item(
                source_domain="meetings", source_type="meeting", source_id=r["id"],
                title=r.get("title") or r.get("subject") or "Meeting", status=r.get("status") or "scheduled",
                priority=r.get("priority"), capability="scheduling.view",
                deep_link=f"/scheduling/{r['id']}", allowed_actions=self.actions,
                due_at=r.get("starts_at"), assignee_user_id=r.get("organizer_user_id"),
                person_id=r.get("person_id"), household_id=r.get("household_id"),
                created_at=r.get("created_at"), updated_at=r.get("updated_at"), now=now,
                source_reference={"starts_at": str(r.get("starts_at"))})


# Registry — insertion order is the deterministic adapter order.
ADAPTERS = (
    CoreWorkAdapter(),
    AdvisorWorkAdapter(),
    ComplianceAdapter(),
    DocumentAdapter(),
    TaxAdapter(),
    InsuranceAdapter(),
    OpportunityAdapter(),
    MeetingAdapter(),
)

# source_domain → the adapter that owns dispatch for it (core emits three domains).
DOMAIN_ADAPTER = {}
for _a in ADAPTERS:
    if _a.domain == "core":
        for _d in ("tasks", "workflow", "exceptions"):
            DOMAIN_ADAPTER[_d] = _a
    else:
        DOMAIN_ADAPTER[_a.domain] = _a

# All source domains the queue can present (for tabs / filters / governance).
SOURCE_DOMAINS = ("tasks", "workflow", "exceptions", "advisor_work", "compliance", "documents",
                  "tax", "insurance", "opportunities", "meetings")

# Per-domain capability required to SEE items (tab visibility + item suppression).
DOMAIN_CAPABILITY = {
    "tasks": "work.read", "workflow": "work.read", "exceptions": "exception.read",
    "advisor_work": "advisor_work.read", "compliance": "compliance.review.read",
    "documents": "documents.view", "tax": "tax.read", "insurance": "insurance.read",
    "opportunities": "opportunity.read", "meetings": "scheduling.view",
}
