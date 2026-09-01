"""Production role library for 360 Wealth Consulting / 360 Tax Solutions.

Single source of truth for the firm's reusable access profiles. Each profile maps to capabilities
that ALREADY exist in the catalogue — this module invents no capabilities, adds no schema, and does
not touch the RBAC engine. The many-to-many role/capability model is unchanged; effective permissions
for an employee remain the union of every profile assigned to them (``rbac.resolve_capabilities``).

The seeding migration (``migrations/versions/prodrolelib01_seed_production_role_library.py``) reads
``NEW_PROFILES`` from here so the seed and the tests can never drift. The seven profiles that already
existed before this library (administrator, advisor, operations, benefits_advisor, benefits_operations,
insurance_agent, compliance) keep their established capability sets and are listed in
``EXISTING_PROFILES`` for completeness/verification only — they are not re-seeded here.

Least-privilege: no profile in ``NEW_PROFILES`` holds any identity/role/team control capability, any
firm-wide record capability, ``audit.read``, vault master keys, ``security.*``, or any ``*.admin``
capability. Those remain exclusive to the administrator profile (and, where appropriate to their job,
the pre-existing operations/compliance profiles).
"""
from __future__ import annotations

# Control-plane capabilities that must remain exclusive to the administrator profile across the whole
# library. Used by the no-privilege-escalation tests.
ADMINISTRATOR_ONLY = frozenset({
    "identity.manage", "role.manage", "team.manage", "record.write_all", "security.admin",
})

# Capabilities that none of the NEW firm profiles may ever carry (they would approach admin / platform
# / compliance-supervisor authority). Existing operations/compliance profiles are intentionally
# exempt — they predate this library and carry oversight/platform caps by design.
FORBIDDEN_FOR_NEW_PROFILES = frozenset({
    "identity.manage", "role.manage", "team.manage", "assignment.manage",
    "audit.read", "record.read_all", "record.write_all",
    "vault.access.all", "vault.manage",
    "compliance.supervise", "compliance.authority.manage", "compliance.review.assign",
    "automation.admin", "communications.admin", "configuration.admin", "governance.admin",
    "integration.admin", "observability.admin", "operations.admin", "reporting.admin",
    "runtime.admin", "scheduling.admin", "security.admin", "workflow.admin",
    "security.audit", "security.execute", "security.manage", "security.view",
})

# --- the seven NEW profiles this library introduces --------------------------------------------
# (code -> (display name, description, frozenset of EXISTING capability codes))

NEW_PROFILES: dict[str, tuple[str, str, frozenset[str]]] = {
    "senior_tax": (
        "Senior Tax",
        "Tax lead: prepares, reviews, and signs off returns; manages tax deadlines and tax documents.",
        frozenset({
            "tax.read", "tax.write", "tax.intake.read", "tax.intake.write",
            "tax.review", "tax.document.review", "tax.deadline.manage",
            "vault.category.tax", "vault.category.general", "vault.view", "vault.upload", "vault.download",
            "client.read", "client.write",
            "document.read", "document.write", "documents.view", "documents.edit",
            "work.read", "work.write", "task.read", "task.write",
            "exception.read", "exception.write", "annual_review.read", "communication.read",
            "reporting.view", "timeline.read", "workspace.personalize",
        }),
    ),
    "tax_staff": (
        "Tax Staff",
        "Tax preparer: intake and return preparation. No review sign-off or deadline management.",
        frozenset({
            "tax.read", "tax.write", "tax.intake.read", "tax.intake.write",
            "vault.category.tax", "vault.category.general", "vault.view", "vault.upload", "vault.download",
            "client.read",
            "document.read", "document.write", "documents.view", "documents.edit",
            "work.read", "work.write", "task.read", "task.write",
            "exception.read", "timeline.read", "workspace.personalize",
        }),
    ),
    "accounting": (
        "Accounting",
        "Bookkeeping and accounting: accounting documents, client books, and financial reporting.",
        frozenset({
            "vault.category.accounting", "vault.category.general", "vault.view", "vault.upload", "vault.download",
            "client.read",
            "document.read", "document.write", "documents.view", "documents.edit",
            "work.read", "work.write", "task.read", "task.write",
            "exception.read", "reporting.view", "timeline.read", "workspace.personalize",
        }),
    ),
    "payroll": (
        "Payroll",
        "Payroll processing: payroll documents and client payroll records.",
        frozenset({
            "vault.category.payroll", "vault.category.general", "vault.view", "vault.upload", "vault.download",
            "client.read",
            "document.read", "document.write", "documents.view", "documents.edit",
            "work.read", "work.write", "task.read", "task.write",
            "exception.read", "reporting.view", "timeline.read", "workspace.personalize",
        }),
    ),
    "client_service": (
        "Client Service",
        "Client coordinator: client contact info, scheduling, communications, and general documents.",
        frozenset({
            "client.read", "client.write",
            "communication.read", "communication.write", "communications.view", "communications.send",
            "scheduling.view", "scheduling.manage",
            "task.read", "task.write", "work.read", "work.write",
            "document.read", "documents.view", "organization.read", "annual_review.read",
            "vault.category.general", "vault.view",
            "timeline.read", "workspace.personalize",
        }),
    ),
    "reviewer": (
        "Reviewer",
        "Quality reviewer: reviews and approves work, documents, and compliance reviews. No supervisory "
        "assignment, audit, or firm-wide access.",
        frozenset({
            "work.read", "work.approve",
            "document.read", "documents.view", "documents.approve",
            "compliance.review.read", "compliance.review.submit", "compliance.review.decide",
            "exception.read", "client.read", "annual_review.read", "task.read",
            "reporting.view", "timeline.read", "workspace.personalize",
        }),
    ),
    "read_only": (
        "Read Only",
        "View-only access to client records, work, documents, tasks, and reports. No write capability.",
        frozenset({
            "client.read", "work.read", "document.read", "documents.view", "task.read",
            "annual_review.read", "communication.read", "reporting.view", "analytics.view",
            "timeline.read", "workspace.personalize",
        }),
    ),
}

# Profiles that already existed before this library (verified, not re-seeded here). Value is a small
# set of capabilities each MUST contain — used only as a smoke assertion in tests.
EXISTING_PROFILES: dict[str, frozenset[str]] = {
    "administrator": frozenset({"identity.manage", "role.manage", "audit.read", "record.read_all"}),
    "advisor": frozenset({"client.read", "client.write", "work.read"}),
    "operations": frozenset({"operations.manage", "client.read", "work.write"}),
    "benefits_advisor": frozenset({"benefits.read", "benefits.write", "vault.category.benefits"}),
    "benefits_operations": frozenset({"benefits.read", "benefits.write", "vault.category.benefits"}),
    "insurance_agent": frozenset({"insurance.read", "insurance.write", "vault.category.insurance"}),
    "compliance": frozenset({"audit.read", "compliance.review.read", "compliance.supervise"}),
}

# --- capabilities granted to library profiles AFTER the library was seeded ---------------------
#
# These cannot live in NEW_PROFILES. ``prodrolelib01`` reads NEW_PROFILES live and hard-fails on any
# capability that is not yet in the catalogue AT ITS POINT IN HISTORY, so a profile may only
# reference capabilities that already existed when the library was seeded. A capability introduced
# by a later migration is therefore recorded here, next to the migration that creates and grants it,
# and the library stays the single source of truth for what a profile ends up holding.
#
# Each entry is (capability -> {profile codes}) and MUST match the grants in the named migration
# exactly; tests/test_production_role_library.py folds these into its exact-set assertion, so the
# two cannot drift.
#
#   msgcap01 — secure client Messages. A dedicated read/write pair rather than reusing client.read,
#   which eleven roles hold: reading a client's correspondence is a narrower authority than reading
#   their record. tax_staff gets read only — a preparer needs the conversation for context, but
#   replying to the client is the coordinator's job.
POST_SEED_GRANTS: dict[str, frozenset[str]] = {
    "client_service": frozenset({"communications.message.read", "communications.message.write"}),
    "senior_tax": frozenset({"communications.message.read", "communications.message.write"}),
    "tax_staff": frozenset({"communications.message.read"}),
}


def effective_capabilities(profile_code: str) -> frozenset[str]:
    """Everything a NEW_PROFILES profile holds once post-seed migrations have run."""
    return frozenset(NEW_PROFILES[profile_code][2]) | POST_SEED_GRANTS.get(profile_code, frozenset())


# The full production library = the seven new profiles + the seven pre-existing ones.
ALL_PROFILE_CODES: frozenset[str] = frozenset(NEW_PROFILES) | frozenset(EXISTING_PROFILES)
