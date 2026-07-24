"""Client Portal visibility registry (Phase D.43) — the single declarative source of external-visibility
decisions. Every field the portal may expose is registered here with its source, required portal grant
permission, masking rule, freshness marker, mutation owner, deep-link, lifecycle, and compliance owner.
Governance verifies completeness and that no ``internal_only``/``prohibited`` field is ever exposed. This
keeps external-visibility decisions OUT of templates and testable.
"""
from __future__ import annotations

from dataclasses import dataclass

# Visibility states.
VISIBLE = "visible"
CONDITIONAL = "conditional"     # visible only with the required grant permission + scope
INTERNAL_ONLY = "internal_only"
PROHIBITED = "prohibited"
DEPRECATED = "deprecated"

# Masking rules.
MASK_NONE = "none"
MASK_ACCOUNT = "account_last4"
MASK_OMIT = "omit"


@dataclass(frozen=True)
class PortalField:
    key: str
    source_domain: str
    source_service: str
    external_visibility: str
    required_permission: str | None   # portal grant permission (grant-based, not RBAC)
    required_scope: str               # "person" | "household" | "organization" | "account"
    masking_rule: str
    freshness: bool                   # whether a freshness/as-of marker is shown
    mutation_owner: str | None        # authoritative service that owns any mutation (None = read-only)
    deep_link: str | None
    lifecycle: str
    compliance_owner: str


def _f(key, domain, service, visibility, permission, scope, mask=MASK_NONE, freshness=False,
       owner=None, link=None, lifecycle="active", compliance="Compliance"):
    return PortalField(key, domain, service, visibility, permission, scope, mask, freshness, owner, link,
                       lifecycle, compliance)


# The registry. Externally-served fields are `visible`/`conditional`; the `internal_only`/`prohibited`
# entries are declared explicitly so governance can assert they are NEVER exposed on a portal surface.
REGISTRY = (
    # --- externally visible ---
    _f("profile.client_name", "people", "portal.service", VISIBLE, None, "person"),
    _f("profile.household_name", "households", "portal.service", VISIBLE, None, "household"),
    _f("profile.service_contacts", "assignments", "portal.service", VISIBLE, None, "person"),
    _f("dashboard.next_appointment", "scheduling", "timeline", CONDITIONAL, "appointments", "person",
       link="/portal/appointments"),
    _f("dashboard.open_requests", "exceptions", "exception_engine.client_action_items", CONDITIONAL,
       "tasks", "person", link="/portal/requests"),
    _f("dashboard.unread_messages", "communications", "portal.service", CONDITIONAL, "messages", "person",
       owner="portal.service", link="/portal/messages"),
    _f("documents.list", "documents", "document_platform", CONDITIONAL, "documents", "person",
       link="/portal/documents"),
    _f("documents.download", "documents", "document_platform", CONDITIONAL, "documents", "person",
       owner="document_platform", link="/portal/documents"),
    _f("documents.upload", "documents", "portal.service", CONDITIONAL, "documents", "person",
       owner="portal.service", link="/portal/requests"),
    _f("financial.account_name", "portfolio", "portal.financial", CONDITIONAL, "financial", "account",
       freshness=True, link="/portal/financial"),
    _f("financial.account_number", "portfolio", "portal.financial", CONDITIONAL, "financial", "account",
       mask=MASK_ACCOUNT, freshness=True),
    _f("financial.current_value", "portfolio", "portal.financial", CONDITIONAL, "financial", "account",
       freshness=True),
    _f("financial.allocation", "portfolio", "portal.financial", CONDITIONAL, "financial", "account",
       freshness=True),
    _f("financial.custodian", "portfolio", "portal.financial", CONDITIONAL, "financial", "account",
       freshness=True),
    _f("tax.return_status", "tax", "tax_return_lifecycle.portal_returns", CONDITIONAL, "tasks", "person",
       owner="tax_return_lifecycle", link="/portal/requests"),
    _f("insurance.policy_summary", "insurance", "insurance_portal", CONDITIONAL, "insurance", "person",
       link="/portal/insurance"),
    _f("benefits.action_items", "benefits", "exception_engine.employer_action_items", CONDITIONAL,
       "benefits", "organization", link="/portal/benefits/action-needed"),
    _f("messages.thread", "communications", "portal.service", CONDITIONAL, "messages", "person",
       owner="portal.service", link="/portal/messages"),
    _f("appointments.upcoming", "scheduling", "timeline", CONDITIONAL, "appointments", "person",
       link="/portal/appointments"),
    # Delegated: the client requests via a governed secure-message thread; the advisor books the real
    # meeting in the authoritative scheduling service. Owner is the portal messaging layer.
    _f("appointments.request", "scheduling", "portal.appointments", CONDITIONAL, "messages", "person",
       owner="portal.service", link="/portal/appointments"),
    _f("household.members", "households", "portal.service", CONDITIONAL, None, "household",
       link="/portal/household"),
    _f("preferences.notification_channels", "portal", "portal.service", VISIBLE, None, "person",
       owner="portal.service"),
    _f("consents.status", "portal", "portal.consent", VISIBLE, None, "person", owner="portal.consent"),
    _f("security.session_status", "portal", "portal.service", VISIBLE, None, "person",
       owner="portal.service"),

    # --- explicitly NEVER exposed on the portal (declared so governance can assert absence) ---
    _f("internal.advisor_notes", "notes", "notes", INTERNAL_ONLY, None, "person"),
    _f("internal.assignments", "assignments", "work_management", INTERNAL_ONLY, None, "person"),
    _f("internal.advisor_work", "advisor_work", "advisor_work", INTERNAL_ONLY, None, "person"),
    _f("internal.work_queue", "work_queue", "work_queue", INTERNAL_ONLY, None, "person"),
    _f("internal.compliance_reasoning", "compliance", "compliance.reviews", PROHIBITED, None, "person"),
    _f("internal.suitability_findings", "compliance", "compliance.reviews", PROHIBITED, None, "person"),
    _f("internal.audit_history", "audit", "audit", PROHIBITED, None, "person"),
    _f("internal.policy_explanations", "policy", "policy.engine", INTERNAL_ONLY, None, "person"),
    _f("internal.ai_assist_brief", "ai_assist", "ai_assist", PROHIBITED, None, "person"),
    _f("internal.opportunity_revenue", "opportunity", "opportunity", INTERNAL_ONLY, None, "person"),
    _f("internal.relationship_graph", "relationships", "relationships", INTERNAL_ONLY, None, "household"),
    _f("internal.net_worth", "portfolio", "portfolio", PROHIBITED, None, "person"),
)

_BY_KEY = {f.key: f for f in REGISTRY}
EXTERNAL_STATES = (VISIBLE, CONDITIONAL)
FORBIDDEN_STATES = (INTERNAL_ONLY, PROHIBITED)


def field(key) -> PortalField | None:
    return _BY_KEY.get(key)


def external_fields() -> list[PortalField]:
    return [f for f in REGISTRY if f.external_visibility in EXTERNAL_STATES]


def is_externally_visible(key) -> bool:
    f = _BY_KEY.get(key)
    return bool(f and f.external_visibility in EXTERNAL_STATES and f.lifecycle != DEPRECATED)


def mask_account_number(value) -> str:
    """Never expose a full account number externally — last 4 only."""
    s = str(value or "")
    return ("••••" + s[-4:]) if len(s) >= 4 else "••••"


def coverage() -> dict:
    total = len(REGISTRY)
    return {"total_fields": total, "external": len(external_fields()),
            "internal_only": sum(1 for f in REGISTRY if f.external_visibility == INTERNAL_ONLY),
            "prohibited": sum(1 for f in REGISTRY if f.external_visibility == PROHIBITED),
            "masked": sum(1 for f in REGISTRY if f.masking_rule != MASK_NONE)}
