"""employee benefits / employer operations foundation (Release 0.9.11, Phase 1)

Adds the shared Organization/Engagement/service-line/relationship-role layer and the
benefits + retirement schema on top of it (ADR-18). Organizations reuse the existing
``relationship_entities`` graph; an optional 1:1 ``organization_profiles`` holds employer
operational fields; ownership is a typed 1:1 detail (``relationship_ownership``) on the
existing ``relationships`` edge (per the §6A ownership validation). Benefits and retirement
plans are first-class; **Betterment at Work** is the first seeded recordkeeper (no
integration). Provider connections are design-ready but inert.

All additive and reversible; single Alembic head. Schema only — services, detectors, work,
API, portal, and reporting land in later phases. Tax (``tax_engagements``) is untouched;
``engagements.legacy_tax_engagement_id`` is the documented future-convergence bridge.
"""
from alembic import op
import sqlalchemy as sa

revision = "r8c69f7e6d5c"
down_revision = "q7b58f6c5d4e"
branch_labels = None
depends_on = None

# --- controlled vocabularies -------------------------------------------------
EXC_DOMAINS_OLD = ("tax", "wealth", "operations", "compliance", "portal", "microsoft")
EXC_DOMAINS_NEW = EXC_DOMAINS_OLD + ("benefits",)

SERVICE_LINES = [
    ("tax", "Tax"), ("wealth", "Wealth Management"), ("benefits", "Employee Benefits"),
    ("retirement", "Retirement Plans"), ("insurance", "Insurance"),
    ("bookkeeping", "Bookkeeping"), ("payroll", "Payroll"),
    ("consulting", "Business Consulting"), ("estate_coordination", "Estate Planning Coordination"),
]

# (code, category, line_of_coverage, name)
PLAN_TYPES = [
    ("medical", "health", "health", "Group Health / Medical"),
    ("dental", "health", "health", "Dental"),
    ("vision", "health", "health", "Vision"),
    ("group_life", "income_protection", "health", "Group Life"),
    ("std", "income_protection", "health", "Short-Term Disability"),
    ("ltd", "income_protection", "health", "Long-Term Disability"),
    ("accident", "supplemental_health", "health", "Accident"),
    ("critical_illness", "supplemental_health", "health", "Critical Illness"),
    ("hospital_indemnity", "supplemental_health", "health", "Hospital Indemnity"),
    ("hsa", "spending_account", "health", "Health Savings Account"),
    ("fsa", "spending_account", "health", "Flexible Spending Account"),
    ("hra", "spending_account", "health", "Health Reimbursement Arrangement"),
    ("401k", "retirement", "retirement", "401(k)"),
    ("simple_ira", "retirement", "retirement", "SIMPLE IRA"),
    ("sep_ira", "retirement", "retirement", "SEP IRA"),
    ("cash_balance", "retirement", "retirement", "Cash Balance Plan"),
    ("deferred_comp", "deferred_compensation", "retirement", "Deferred Compensation"),
]
PLAN_TYPE_CATEGORIES = ("health", "income_protection", "supplemental_health",
                        "spending_account", "retirement", "deferred_compensation")

# (code, provider_type, line_of_coverage, name)
PROVIDERS = [
    ("betterment", "recordkeeper", "retirement", "Betterment at Work"),
]
PROVIDER_TYPES = ("carrier", "recordkeeper", "tpa", "payroll", "hris", "broker")

SLA_BY_SEVERITY = {"blocker": 1440, "high": 2880, "medium": 7200, "low": 14400}
# (code, category, severity, owner_role, blocks_lifecycle, compliance_visible, name)
BENEFIT_EXCEPTION_TYPES = [
    ("BEN_ELIGIBILITY_UNRESOLVED", "client", "medium", "advisor", False, False, "Benefit eligibility unresolved"),
    ("BEN_NEW_HIRE_ENROLLMENT_DUE", "client", "high", "advisor", False, False, "New-hire enrollment due"),
    ("BEN_WAIVER_MISSING", "client", "low", "advisor", False, False, "Coverage waiver missing"),
    ("BEN_QUALIFYING_EVENT_PENDING", "client", "medium", "advisor", False, False, "Qualifying life event pending"),
    ("BEN_OPEN_ENROLLMENT_INCOMPLETE", "workflow", "high", "operations", False, False, "Open enrollment incomplete"),
    ("BEN_CENSUS_OVERDUE", "document", "high", "operations", False, False, "Census overdue"),
    ("BEN_CENSUS_MISMATCH", "document", "medium", "operations", False, False, "Census / enrollment mismatch"),
    ("BEN_SPD_MISSING", "document", "medium", "operations", False, False, "Summary Plan Description missing"),
    ("BEN_SBC_MISSING", "document", "medium", "operations", False, False, "Summary of Benefits & Coverage missing"),
    ("BEN_RENEWAL_AT_RISK", "workflow", "high", "operations", False, False, "Renewal at risk"),
    ("BEN_5500_FILING_DUE", "compliance", "blocker", "compliance", True, True, "Form 5500 filing due"),
    ("BEN_ACA_MEASUREMENT_RISK", "compliance", "high", "compliance", False, True, "ACA measurement risk"),
    ("BEN_ERISA_NOTICE_MISSING", "compliance", "high", "compliance", False, True, "ERISA notice missing"),
    ("BEN_INVOICE_DISCREPANCY", "operational", "medium", "operations", False, False, "Carrier invoice discrepancy"),
    ("BEN_RETIREMENT_ELIGIBILITY_UNRESOLVED", "client", "medium", "advisor", False, False, "Retirement eligibility unresolved"),
    ("BEN_DEFERRAL_ELECTION_DUE", "client", "high", "advisor", False, False, "Deferral election due"),
    ("BEN_FIDUCIARY_REVIEW_DUE", "compliance", "high", "compliance", False, True, "Annual fiduciary review due"),
    ("BEN_NONDISCRIMINATION_TEST_DUE", "compliance", "high", "compliance", False, True, "Nondiscrimination testing due"),
    ("BEN_CONTRIBUTION_DEPOSIT_LATE", "compliance", "blocker", "compliance", True, True, "Contribution deposit late"),
    ("BEN_ANNUAL_NOTICE_MISSING", "compliance", "high", "compliance", False, True, "Annual retirement notice missing"),
    ("BEN_PLAN_AMENDMENT_REQUIRED", "workflow", "high", "operations", False, False, "Plan amendment required"),
    # future / inert until carrier / payroll / recordkeeper ports are built
    ("BEN_CARRIER_SUBMISSION_FAILED", "filing", "high", "operations", False, False, "Carrier submission failed (future)"),
    ("BEN_PAYROLL_SYNC_FAILED", "operational", "medium", "operations", False, False, "Payroll sync failed (future)"),
    ("BEN_PROVIDER_CONNECTION_STALE", "operational", "low", "operations", False, False, "Provider connection stale (future)"),
]

# (code, name, inverse_name, category)
RELATIONSHIP_TYPES = [
    ("owns", "Owns", "Owned by", "ownership"),
    ("parent_of", "Parent of", "Subsidiary of", "org_structure"),
    ("affiliate_of", "Affiliate of", "Affiliate of", "org_structure"),
    ("related_to", "Related to", "Related to", "org_structure"),
    ("advisor", "Advisor", "Advised by", "servicing"),
    ("benefits_consultant", "Benefits Consultant", "Benefits client of", "servicing"),
    ("tax_manager", "Tax Manager", "Tax managed by", "servicing"),
    ("tax_preparer", "Tax Preparer", "Tax prepared by", "servicing"),
    ("relationship_manager", "Relationship Manager", "Relationship managed by", "servicing"),
    ("primary_producer", "Primary Producer", "Primary produced by", "servicing"),
    ("secondary_producer", "Secondary Producer", "Secondary produced by", "servicing"),
    ("account_manager", "Account Manager", "Account managed by", "servicing"),
    ("service_rep", "Service Representative", "Serviced by", "servicing"),
    ("renewal_owner", "Renewal Owner", "Renewal owned by", "servicing"),
    ("cpa", "CPA", "CPA for", "professional"),
    ("attorney", "Attorney", "Attorney for", "professional"),
    ("banker", "Banking Relationship", "Banker for", "professional"),
    ("broker_of_record", "Broker of Record", "Broker of record for", "professional"),
]

# (code, description, sensitive)
CAPABILITIES = [
    ("organization.read", "View organizations, service lines, roles, and revenue", False),
    ("organization.write", "Manage organizations, service lines, roles, and revenue", False),
    ("benefits.read", "View benefit and retirement plans, enrollments, and exceptions", False),
    ("benefits.write", "Manage benefit and retirement plans and plan years", False),
    ("benefits.enroll", "Manage eligibility, enrollments, waivers, and deferral elections", False),
    ("benefits.compliance", "Resolve benefits compliance items (5500/ACA/ERISA/fiduciary/testing)", True),
    ("benefits.sensitive.read", "View benefits PHI and retirement financial PII", True),
]
BENEFITS_ROLES = [
    ("benefits_advisor", "Benefits Advisor", "Benefits/retirement advisory and enrollment"),
    ("benefits_operations", "Benefits Operations", "Benefits/retirement operations and work"),
    ("benefits_compliance", "Benefits Compliance", "Benefits/retirement compliance review"),
]


def _check(col, allowed):
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def upgrade():
    # ------------------------------------------------------------------ shared org layer
    op.create_table(
        "organization_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("relationship_entity_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("legal_name", sa.String(500)),
        sa.Column("ein", sa.String(64)),  # encrypted at rest by the service layer (Phase 2)
        sa.Column("industry", sa.String(120)),
        sa.Column("naics_code", sa.String(10)),
        sa.Column("entity_form", sa.String(30)),
        sa.Column("employee_count_band", sa.String(30)),
        sa.Column("renewal_month", sa.Integer),
        sa.Column("status", sa.String(30), nullable=False, server_default="prospect"),
        sa.Column("address_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("entity_form", ("llc", "c_corp", "s_corp", "partnership",
                                                  "nonprofit", "trust", "sole_prop",
                                                  "professional_practice")) + " OR entity_form IS NULL",
                           name="ck_org_profiles_entity_form"),
        sa.CheckConstraint(_check("status", ("prospect", "active", "inactive")), name="ck_org_profiles_status"),
        sa.CheckConstraint("renewal_month IS NULL OR (renewal_month BETWEEN 1 AND 12)",
                           name="ck_org_profiles_renewal_month"),
    )

    op.create_table(
        "relationship_ownership",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("relationship_id", sa.Integer,
                  sa.ForeignKey("relationships.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("ownership_percentage", sa.Numeric(5, 2)),  # NULL = unknown
        sa.Column("voting_percentage", sa.Numeric(5, 2)),
        sa.Column("ownership_type", sa.String(30)),
        sa.Column("is_direct", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("evidence_source", sa.String(120)),
        sa.Column("as_of_date", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("ownership_percentage IS NULL OR (ownership_percentage BETWEEN 0 AND 100)",
                           name="ck_rel_ownership_pct"),
        sa.CheckConstraint("voting_percentage IS NULL OR (voting_percentage BETWEEN 0 AND 100)",
                           name="ck_rel_ownership_vote"),
    )

    op.create_table(
        "service_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "organization_service_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_line_id", sa.Integer, sa.ForeignKey("service_lines.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="prospect"),
        sa.Column("since_date", sa.Date),
        sa.Column("renewal_owner_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("status", ("prospect", "active", "inactive")), name="ck_org_service_line_status"),
        sa.UniqueConstraint("organization_id", "service_line_id", name="uq_org_service_line"),
    )

    op.create_table(
        "organization_service_roles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_type_id", sa.Integer, sa.ForeignKey("relationship_types.id"), nullable=False),
        sa.Column("service_line_id", sa.Integer, sa.ForeignKey("service_lines.id")),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("effective_date", sa.Date, nullable=False, server_default=sa.text("current_date")),
        sa.Column("inactive_date", sa.Date),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "engagements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="CASCADE")),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="CASCADE")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="CASCADE")),
        sa.Column("service_line_id", sa.Integer, sa.ForeignKey("service_lines.id"), nullable=False),
        sa.Column("engagement_type", sa.String(60), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("due_date", sa.Date),
        sa.Column("opened_on", sa.Date),
        sa.Column("closed_on", sa.Date),
        sa.Column("metadata", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("legacy_tax_engagement_id", sa.Integer, sa.ForeignKey("tax_engagements.id")),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "service_revenue",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_line_id", sa.Integer, sa.ForeignKey("service_lines.id"), nullable=False),
        sa.Column("revenue_category", sa.String(40), nullable=False),
        sa.Column("amount_kind", sa.String(20), nullable=False, server_default="estimated"),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("period", sa.String(20), nullable=False, server_default="annual"),
        sa.Column("as_of_date", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("revenue_category", (
            "tax_fees", "planning_fees", "advisory", "aum", "benefits_commissions",
            "insurance_commissions", "retirement_plan_revenue", "consulting", "recurring")),
            name="ck_service_revenue_category"),
        sa.CheckConstraint(_check("amount_kind", ("estimated", "actual")), name="ck_service_revenue_kind"),
        sa.CheckConstraint(_check("period", ("annual", "monthly", "one_time")), name="ck_service_revenue_period"),
    )

    # ------------------------------------------------------------------ benefits reference
    op.create_table(
        "benefit_plan_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("line_of_coverage", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(_check("category", PLAN_TYPE_CATEGORIES), name="ck_plan_type_category"),
        sa.CheckConstraint(_check("line_of_coverage", ("health", "retirement")), name="ck_plan_type_line"),
    )

    op.create_table(
        "benefit_providers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider_type", sa.String(20), nullable=False),
        sa.Column("line_of_coverage", sa.String(20)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(_check("provider_type", PROVIDER_TYPES), name="ck_provider_type"),
        sa.CheckConstraint("line_of_coverage IS NULL OR " + _check("line_of_coverage", ("health", "retirement")),
                           name="ck_provider_line"),
    )

    # ------------------------------------------------------------------ benefits plans
    op.create_table(
        "benefit_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_type_id", sa.Integer, sa.ForeignKey("benefit_plan_types.id"), nullable=False),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("benefit_providers.id")),
        sa.Column("engagement_id", sa.Integer, sa.ForeignKey("engagements.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("funding_type", sa.String(30)),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("effective_date", sa.Date),
        sa.Column("renewal_date", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("status", ("draft", "active", "renewing", "terminated")), name="ck_plan_status"),
        sa.CheckConstraint("funding_type IS NULL OR " + _check("funding_type", (
            "fully_insured", "level_funded", "self_funded", "trustee", "custodial")), name="ck_plan_funding"),
    )
    op.create_index("ix_benefit_plans_org", "benefit_plans", ["organization_id"])
    op.create_index("ix_benefit_plans_status", "benefit_plans", ["status"])
    op.create_index("ix_benefit_plans_type", "benefit_plans", ["plan_type_id"])

    op.create_table(
        "benefit_retirement_plan_details",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("benefit_plans.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("benefit_providers.id")),
        sa.Column("safe_harbor_type", sa.String(30), nullable=False, server_default="none"),
        sa.Column("match_formula", sa.String(255)),
        sa.Column("auto_enrollment", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("auto_enroll_default_percent", sa.Numeric(5, 2)),
        sa.Column("vesting_schedule", sa.String(120)),
        sa.Column("eligibility_rule", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("fiduciary_role", sa.String(10), nullable=False, server_default="none"),
        sa.Column("erisa", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("adoption_agreement_document_id", sa.Integer, sa.ForeignKey("documents.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("safe_harbor_type", ("none", "basic_match", "enhanced_match", "nonelective")),
                           name="ck_retirement_safe_harbor"),
        sa.CheckConstraint(_check("fiduciary_role", ("3(21)", "3(38)", "none")), name="ck_retirement_fiduciary"),
    )

    op.create_table(
        "benefit_plan_years",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("benefit_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_year", sa.Integer, nullable=False),
        sa.Column("effective_date", sa.Date),
        sa.Column("renewal_date", sa.Date),
        sa.Column("open_enrollment_start", sa.Date),
        sa.Column("open_enrollment_end", sa.Date),
        sa.Column("status", sa.String(30), nullable=False, server_default="upcoming"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("status", ("upcoming", "open_enrollment", "active", "closed")),
                           name="ck_plan_year_status"),
        sa.UniqueConstraint("plan_id", "plan_year", name="uq_benefit_plan_year"),
    )

    op.create_table(
        "benefit_employments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("hire_date", sa.Date),
        sa.Column("termination_date", sa.Date),
        sa.Column("benefit_class", sa.String(60)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("employee_status", ("active", "terminated", "cobra", "retired")),
                           name="ck_employment_status"),
        sa.UniqueConstraint("person_id", "organization_id", name="uq_benefit_employment"),
    )
    op.create_index("ix_benefit_employments_org", "benefit_employments", ["organization_id", "employee_status"])

    op.create_table(
        "benefit_enrollments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("benefit_employment_id", sa.Integer,
                  sa.ForeignKey("benefit_employments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_year_id", sa.Integer, sa.ForeignKey("benefit_plan_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("coverage_tier", sa.String(30), nullable=False, server_default="employee"),
        sa.Column("status", sa.String(20), nullable=False, server_default="eligible"),
        sa.Column("elected_at", sa.DateTime(timezone=True)),
        sa.Column("effective_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("source", sa.String(20), nullable=False, server_default="staff"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("coverage_tier", ("employee", "employee_spouse", "employee_children",
                                                    "family", "waived")), name="ck_enrollment_tier"),
        sa.CheckConstraint(_check("status", ("eligible", "elected", "enrolled", "waived", "terminated", "cobra")),
                           name="ck_enrollment_status"),
        sa.CheckConstraint(_check("source", ("staff", "portal", "import")), name="ck_enrollment_source"),
        sa.UniqueConstraint("benefit_employment_id", "plan_year_id", name="uq_benefit_enrollment"),
    )
    op.create_index("ix_benefit_enrollments_year_status", "benefit_enrollments", ["plan_year_id", "status"])

    op.create_table(
        "benefit_retirement_elections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("benefit_enrollment_id", sa.Integer,
                  sa.ForeignKey("benefit_enrollments.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("deferral_percent", sa.Numeric(5, 2)),
        sa.Column("roth_percent", sa.Numeric(5, 2)),
        sa.Column("contribution_type", sa.String(20), nullable=False, server_default="none"),
        sa.Column("auto_enrolled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("vesting_snapshot", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("effective_date", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("contribution_type", ("pre_tax", "roth", "mixed", "none")),
                           name="ck_retirement_election_type"),
    )

    op.create_table(
        "benefit_dependents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("benefit_enrollment_id", sa.Integer,
                  sa.ForeignKey("benefit_enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship", sa.String(30), nullable=False),
        sa.Column("effective_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("benefit_enrollment_id", "person_id", name="uq_benefit_dependent"),
    )

    op.create_table(
        "benefit_document_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("benefit_plans.id", ondelete="CASCADE")),
        sa.Column("plan_year_id", sa.Integer, sa.ForeignKey("benefit_plan_years.id", ondelete="CASCADE")),
        sa.Column("doc_kind", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_benefit_document_links_org", "benefit_document_links", ["organization_id"])

    op.create_table(
        "benefit_provider_connections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("benefit_providers.id"), nullable=False),
        sa.Column("connection_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="not_connected"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_status", sa.String(30)),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("connection_type", ("recordkeeper", "payroll", "hris")), name="ck_provider_conn_type"),
        sa.CheckConstraint(_check("status", ("not_connected", "pending", "connected", "error")),
                           name="ck_provider_conn_status"),
        sa.UniqueConstraint("organization_id", "provider_id", "connection_type", name="uq_provider_connection"),
    )

    # ------------------------------------------------------------------ extend existing tables
    op.add_column("portal_access_grants",
                  sa.Column("organization_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="CASCADE")))
    op.add_column("timeline_events",
                  sa.Column("organization_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="CASCADE")))

    # extend the exception domain CHECK to allow 'benefits'
    for table in ("exceptions", "exception_types"):
        op.drop_constraint(f"ck_{table}_domain", table, type_="check")
        op.create_check_constraint(f"ck_{table}_domain", table, _check("domain", EXC_DOMAINS_NEW))

    # ------------------------------------------------------------------ seeds
    bind = op.get_bind()

    op.bulk_insert(sa.table("service_lines", sa.column("code"), sa.column("name")),
                   [{"code": c, "name": n} for c, n in SERVICE_LINES])

    op.bulk_insert(sa.table("benefit_plan_types", sa.column("code"), sa.column("name"),
                            sa.column("category"), sa.column("line_of_coverage")),
                   [{"code": c, "name": n, "category": cat, "line_of_coverage": loc}
                    for c, cat, loc, n in PLAN_TYPES])

    op.bulk_insert(sa.table("benefit_providers", sa.column("code"), sa.column("name"),
                            sa.column("provider_type"), sa.column("line_of_coverage")),
                   [{"code": c, "name": n, "provider_type": pt, "line_of_coverage": loc}
                    for c, pt, loc, n in PROVIDERS])

    op.bulk_insert(
        sa.table("exception_types", sa.column("domain"), sa.column("code"), sa.column("category"),
                 sa.column("name"), sa.column("default_severity"), sa.column("default_owner_role"),
                 sa.column("sla_minutes"), sa.column("blocks_lifecycle"), sa.column("compliance_visible")),
        [{"domain": "benefits", "code": code, "category": category, "name": name,
          "default_severity": severity, "default_owner_role": owner,
          "sla_minutes": SLA_BY_SEVERITY[severity], "blocks_lifecycle": blocks, "compliance_visible": comp}
         for (code, category, severity, owner, blocks, comp, name) in BENEFIT_EXCEPTION_TYPES])

    for code, name, inverse, category in RELATIONSHIP_TYPES:
        bind.execute(sa.text(
            "INSERT INTO relationship_types (code, name, inverse_name, category, directed, active) "
            "VALUES (:code, :name, :inverse, :category, true, true) ON CONFLICT (code) DO NOTHING"),
            {"code": code, "name": name, "inverse": inverse, "category": category})

    for code, description, sensitive in CAPABILITIES:
        bind.execute(sa.text(
            "INSERT INTO capabilities (code, description, sensitive) VALUES (:code, :description, :sensitive) "
            "ON CONFLICT (code) DO NOTHING"),
            {"code": code, "description": description, "sensitive": sensitive})

    for code, name, description in BENEFITS_ROLES:
        bind.execute(sa.text(
            "INSERT INTO roles (code, name, description, system_role, active) "
            "VALUES (:code, :name, :description, false, true) ON CONFLICT (code) DO NOTHING"),
            {"code": code, "name": name, "description": description})

    def grant(role_code, cap_codes):
        bind.execute(sa.text(
            "INSERT INTO role_capabilities (role_id, capability_id) "
            "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
            "WHERE r.code = :role AND c.code = ANY(:caps) ON CONFLICT DO NOTHING"),
            {"role": role_code, "caps": list(cap_codes)})

    all_caps = [c for c, _, _ in CAPABILITIES]
    grant("administrator", all_caps)
    grant("benefits_advisor", ["organization.read", "benefits.read", "benefits.write",
                               "benefits.enroll", "exception.read", "exception.write"])
    grant("benefits_operations", ["organization.read", "benefits.read", "benefits.write",
                                  "benefits.enroll", "exception.read", "exception.write",
                                  "work.read", "capacity.read"])
    grant("benefits_compliance", ["organization.read", "benefits.read", "benefits.compliance",
                                  "benefits.sensitive.read", "exception.read", "exception.compliance"])


def downgrade():
    bind = op.get_bind()

    # Remove benefits exception data BEFORE narrowing the domain CHECK, so the
    # narrowed constraint validates against the remaining rows.
    bind.execute(sa.text("DELETE FROM exceptions WHERE domain='benefits'"))
    bind.execute(sa.text("DELETE FROM exception_types WHERE domain='benefits'"))

    # restore the original exception domain CHECK (drop 'benefits')
    for table in ("exceptions", "exception_types"):
        op.drop_constraint(f"ck_{table}_domain", table, type_="check")
        op.create_check_constraint(f"ck_{table}_domain", table, _check("domain", EXC_DOMAINS_OLD))

    op.drop_column("timeline_events", "organization_id")
    op.drop_column("portal_access_grants", "organization_id")

    # seed rows in shared/existing tables
    bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id IN "
                         "(SELECT id FROM capabilities WHERE code LIKE 'benefits.%' OR code LIKE 'organization.%')"))
    bind.execute(sa.text("DELETE FROM roles WHERE code IN ('benefits_advisor','benefits_operations','benefits_compliance')"))
    bind.execute(sa.text("DELETE FROM capabilities WHERE code LIKE 'benefits.%' OR code LIKE 'organization.%'"))
    rel_codes = tuple(c for c, *_ in RELATIONSHIP_TYPES)
    bind.execute(sa.text("DELETE FROM relationship_types WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": list(rel_codes)})

    # drop new tables (reverse dependency order)
    for tbl in (
        "benefit_provider_connections", "benefit_document_links", "benefit_dependents",
        "benefit_retirement_elections", "benefit_enrollments", "benefit_employments",
        "benefit_plan_years", "benefit_retirement_plan_details", "benefit_plans",
        "benefit_providers", "benefit_plan_types", "service_revenue", "engagements",
        "organization_service_roles", "organization_service_lines", "service_lines",
        "relationship_ownership", "organization_profiles",
    ):
        op.drop_table(tbl)
