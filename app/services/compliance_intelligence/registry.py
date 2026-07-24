"""Compliance Intelligence registries (Phase D.47) — the two declarative catalogs of the supervisory layer:

  * SUPERVISORY_REGISTRY — every supervisory review type (owner, governing workflow, policy owner, required
    evidence, approval authority, escalation path, retention class, deep link, runtime gate). This is the
    authoritative catalog of supervisory review types.
  * EXCEPTION_REGISTRY — every compliance exception type (owner, severity, lifecycle, suppression rules,
    governing policy, escalation).

The supervisory layer is a COMPOSITION over the authoritative compliance/review/exception/audit/approval
services. Governance verifies every review + exception type is registered and that no supervisory item is
ever surfaced without a registered type. Types with no backing data yet (advertising, complaint, trade,
account-opening, communication, workflow reviews) are declared here so the catalog is complete; the engine
emits items only where an authoritative source supplies them.
"""
from __future__ import annotations

from dataclasses import dataclass

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

# Retention classes (declarative governance buckets — enforcement stays with the authoritative owner).
RETENTION_REGULATORY = "regulatory"
RETENTION_STANDARD = "standard"


# --- supervisory review registry ---------------------------------------------

@dataclass(frozen=True)
class SupervisoryReviewType:
    key: str
    owner: str                    # authoritative owning service
    governing_workflow: str       # the workflow that owns resolution
    policy_owner: str             # the compliance/policy owner
    required_evidence: str        # what evidence supports the review
    approval_authority: str       # who may approve/decide
    escalation_path: str
    retention_class: str
    deep_link: str                # the authoritative surface to open
    runtime_gate: str             # the governed runtime flag guarding the type
    lifecycle: str
    populated: bool               # whether an authoritative source currently supplies items


def _s(key, owner, workflow, policy_owner, evidence, authority, escalation, deep_link, gate,
       *, retention=RETENTION_REGULATORY, populated=True, lifecycle="active"):
    return SupervisoryReviewType(key, owner, workflow, policy_owner, evidence, authority, escalation,
                                 retention, deep_link, gate, lifecycle, populated)


SUPERVISORY_REGISTRY = (
    _s("suitability_review", "compliance.reviews", "compliance.reviews", "Compliance",
       "governed recommendation + evidence snapshot", "authorized compliance reviewer",
       "compliance officer", "/compliance/reviews", "supervision.enabled"),
    _s("annual_review_oversight", "annual_review", "annual_review", "Compliance",
       "annual review session + compliance summary", "authorized compliance reviewer",
       "compliance officer", "/annual-review", "supervision.enabled"),
    _s("account_opening_review", "compliance.reviews", "compliance.reviews", "Compliance",
       "new-account governed recommendation", "authorized compliance reviewer", "compliance officer",
       "/compliance/reviews", "supervision.enabled", populated=False),
    _s("trade_review", "compliance.reviews", "compliance.reviews", "Compliance",
       "trade governed recommendation", "authorized compliance reviewer", "compliance officer",
       "/compliance/reviews", "supervision.enabled", populated=False),
    _s("document_review", "document_platform", "document_platform", "Compliance",
       "document in review status", "documents.approve holder", "operations", "/document-library",
       "supervision.enabled", retention=RETENTION_STANDARD),
    _s("communication_review", "communications", "communications", "Compliance",
       "communication audit history", "authorized compliance reviewer", "compliance officer",
       "/communications", "supervision.enabled", populated=False),
    _s("workflow_review", "workflow_automation", "workflow_automation", "Operations",
       "workflow work approval", "work.approve holder", "operations", "/workflows",
       "supervision.enabled", retention=RETENTION_STANDARD, populated=False),
    _s("advertising_review", "compliance.reviews", "compliance.reviews", "Compliance",
       "advertising/marketing material submission", "authorized compliance reviewer", "compliance officer",
       "/compliance/reviews", "supervision.enabled", populated=False),
    _s("compliance_exception", "exception_engine", "exception_engine", "Compliance",
       "open compliance-category exception", "compliance officer", "compliance officer",
       "/compliance", "supervision.enabled"),
    _s("licensing_review", "insurance_licensing", "insurance_licensing", "Compliance",
       "producer license record + expiry", "compliance officer", "compliance officer", "/insurance",
       "supervision.enabled", retention=RETENTION_STANDARD),
    _s("continuing_education_review", "insurance_licensing", "insurance_licensing", "Compliance",
       "producer CE record + credits", "compliance officer", "compliance officer", "/insurance",
       "supervision.enabled", retention=RETENTION_STANDARD),
    _s("complaint_review", "compliance.reviews", "compliance.reviews", "Compliance",
       "client complaint record", "authorized compliance reviewer", "compliance officer",
       "/compliance/reviews", "supervision.enabled", populated=False),
)

_SUP_BY_KEY = {t.key: t for t in SUPERVISORY_REGISTRY}
POPULATED_REVIEW_TYPES = tuple(t.key for t in SUPERVISORY_REGISTRY if t.populated)


# --- exception registry ------------------------------------------------------

@dataclass(frozen=True)
class ComplianceExceptionType:
    key: str
    owner: str
    default_severity: str
    lifecycle: str
    governing_policy: str
    escalation: str
    suppression: tuple[str, ...]


def _x(key, owner, severity, policy, escalation, *, suppression=(), lifecycle="active"):
    return ComplianceExceptionType(key, owner, severity, lifecycle, policy, escalation, tuple(suppression))


EXCEPTION_REGISTRY = (
    _x("overdue_review", "compliance.reviews", "high", "review cadence policy", "compliance officer",
       suppression=("review_current",)),
    _x("missing_document", "exception_engine", "high", "document retention policy", "operations",
       suppression=("document_present",)),
    _x("unsigned_disclosure", "portal.signatures", "high", "disclosure delivery policy", "compliance officer",
       suppression=("disclosure_signed",)),
    _x("missing_beneficiary", "portfolio", "medium", "beneficiary designation policy", "advisor",
       suppression=("beneficiary_present",)),
    _x("stale_financial_information", "portfolio", "medium", "data-currency policy", "advisor",
       suppression=("information_current",)),
    _x("missing_compliance_approval", "compliance.reviews", "critical", "supervisory approval policy",
       "compliance officer", suppression=("approval_recorded",)),
    _x("communication_exception", "communications", "medium", "communication supervision policy",
       "compliance officer", suppression=("no_action_required",)),
    _x("licensing_issue", "insurance_licensing", "high", "producer licensing policy", "compliance officer",
       suppression=("license_active",)),
    _x("ce_deficiency", "insurance_licensing", "medium", "continuing-education policy", "compliance officer",
       suppression=("ce_complete",)),
    _x("workflow_violation", "workflow_automation", "high", "workflow segregation-of-duties policy",
       "operations", suppression=("no_violation",)),
)

_EXC_BY_KEY = {t.key: t for t in EXCEPTION_REGISTRY}


# --- lookups -----------------------------------------------------------------

def review_type(key) -> SupervisoryReviewType | None:
    return _SUP_BY_KEY.get(key)


def exception_type(key) -> ComplianceExceptionType | None:
    return _EXC_BY_KEY.get(key)


def review_registered(key) -> bool:
    return key in _SUP_BY_KEY


def exception_registered(key) -> bool:
    return key in _EXC_BY_KEY


def coverage() -> dict:
    return {
        "review_types": len(SUPERVISORY_REGISTRY),
        "populated_review_types": len(POPULATED_REVIEW_TYPES),
        "exception_types": len(EXCEPTION_REGISTRY),
        "regulatory_retention": sum(1 for t in SUPERVISORY_REGISTRY
                                    if t.retention_class == RETENTION_REGULATORY),
    }
