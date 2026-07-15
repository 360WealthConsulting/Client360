"""benefits work queues + domain-scope existing tax exception queues (Release 0.9.11, Phase 4)

Data-only, reversible. Seeds seven data-driven benefits work queues (consumed by the
existing Work Management ``queue_items``/``queue_matches`` filters — no hard-coded UI logic)
and narrows the three existing exception-based tax queues to ``domain='tax'`` so benefits
exceptions never leak into them now that both domains project through ``work_items()``.

No schema change; single Alembic head preserved. No new date-driven detectors, SLA sweeps,
or notifications here (Phase 5).
"""
import json

from alembic import op

revision = "t0e8b9h8g7f6"
down_revision = "s9d7a8g7f6e5"
branch_labels = None
depends_on = None

_ENROLLMENT = ["BEN_ELIGIBILITY_UNRESOLVED", "BEN_NEW_HIRE_ENROLLMENT_DUE", "BEN_WAIVER_MISSING",
               "BEN_QUALIFYING_EVENT_PENDING", "BEN_OPEN_ENROLLMENT_INCOMPLETE"]
_CENSUS_DOCS = ["BEN_CENSUS_OVERDUE", "BEN_CENSUS_MISMATCH", "BEN_SPD_MISSING", "BEN_SBC_MISSING"]
_RETIREMENT = ["BEN_RETIREMENT_ELIGIBILITY_UNRESOLVED", "BEN_DEFERRAL_ELECTION_DUE",
               "BEN_FIDUCIARY_REVIEW_DUE", "BEN_NONDISCRIMINATION_TEST_DUE",
               "BEN_CONTRIBUTION_DEPOSIT_LATE", "BEN_ANNUAL_NOTICE_MISSING", "BEN_PLAN_AMENDMENT_REQUIRED"]

# (code, name, description, criteria, required_capability)
BENEFITS_QUEUES = [
    ("benefits_unassigned", "Benefits — Unassigned", "Benefits work not yet assigned",
     {"domain": "benefits", "entity_type": "exception", "unassigned": True}, "benefits.read"),
    ("benefits_enrollment", "Benefits — Enrollment", "Eligibility, new-hire, waiver, QLE, open enrollment",
     {"domain": "benefits", "codes": _ENROLLMENT}, "benefits.read"),
    ("benefits_renewals", "Benefits — Renewals", "Renewal and plan-readiness work",
     {"domain": "benefits", "codes": ["BEN_RENEWAL_AT_RISK"]}, "benefits.read"),
    ("benefits_census_documents", "Benefits — Census and Documents", "Census and required plan documents",
     {"domain": "benefits", "codes": _CENSUS_DOCS}, "benefits.read"),
    ("benefits_retirement", "Benefits — Retirement Plans", "Retirement plan work",
     {"domain": "benefits", "codes": _RETIREMENT}, "benefits.read"),
    ("benefits_compliance", "Benefits — Compliance", "Benefits and retirement compliance items",
     {"domain": "benefits", "entity_type": "exception", "category": "compliance"}, "benefits.compliance"),
    ("benefits_high_priority", "Benefits — High Priority or Blockers", "Blocker/high-severity benefits work",
     {"domain": "benefits", "entity_type": "exception", "severity": ["blocker", "high"]}, "benefits.read"),
]

# existing tax exception queues -> add domain='tax' (and their original criteria for downgrade)
_TAX_NEW = {
    "tax_exceptions": {"entity_type": "exception", "domain": "tax"},
    "tax_exceptions_critical": {"entity_type": "exception", "severity": ["blocker", "high"], "domain": "tax"},
    "compliance_exceptions": {"entity_type": "exception", "category": "compliance", "domain": "tax"},
}
_TAX_OLD = {
    "tax_exceptions": {"entity_type": "exception"},
    "tax_exceptions_critical": {"entity_type": "exception", "severity": ["blocker", "high"]},
    "compliance_exceptions": {"entity_type": "exception", "category": "compliance"},
}


def _set_criteria(code, criteria):
    op.execute("UPDATE work_queues SET criteria = CAST('%s' AS json) WHERE code = '%s'"
               % (json.dumps(criteria), code))


def upgrade():
    for code, criteria in _TAX_NEW.items():
        _set_criteria(code, criteria)
    for code, name, description, criteria, cap in BENEFITS_QUEUES:
        op.execute(
            "INSERT INTO work_queues (code, name, description, criteria, required_capability) "
            "VALUES ('%s', '%s', '%s', CAST('%s' AS json), '%s')"
            % (code, name.replace("'", "''"), description.replace("'", "''"),
               json.dumps(criteria), cap))


def downgrade():
    codes = ", ".join(f"'{c[0]}'" for c in BENEFITS_QUEUES)
    op.execute(f"DELETE FROM work_queues WHERE code IN ({codes})")
    for code, criteria in _TAX_OLD.items():
        _set_criteria(code, criteria)
