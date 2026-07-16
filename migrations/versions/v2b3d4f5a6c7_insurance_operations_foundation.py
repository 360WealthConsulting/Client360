"""insurance operations foundation (Release 0.10.0, Phase 0)

Schema foundation for the Insurance Operations domain — individual life & annuities.
Reuses the 0.9.11 platform: carriers are ``relationship_entities`` org nodes (AD-1), a
case wraps an ``engagement`` 1:1 (AD-2), and the exception engine / work management gain
an ``insurance`` domain. New tables exist only where a regulated insurance concept cannot
be represented by the platform (Refinement 1). See
``docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md``.

Additive and reversible; single Alembic head. Schema + capabilities/roles + domain
registration only — services, routes, detectors, portal, and reporting land in later
phases. The regulated-event tables (suitability, replacements, reviews, licensing,
commissions) are created by their own feature-phase migrations.
"""
import sqlalchemy as sa
from alembic import op

revision = "v2b3d4f5a6c7"
down_revision = "u1f9c0i9h8g7"
branch_labels = None
depends_on = None

# --- controlled vocabularies -------------------------------------------------
# Current exception domains (after the benefits foundation added 'benefits').
EXC_DOMAINS_OLD = ("tax", "wealth", "operations", "compliance", "portal", "microsoft", "benefits")
EXC_DOMAINS_NEW = EXC_DOMAINS_OLD + ("insurance",)

PRODUCT_TYPES = ("term_life", "whole_life", "universal_life", "iul", "vul",
                 "fixed_annuity", "variable_annuity", "fia")
PRODUCT_LINES = ("life", "annuity")
FAMILY_STATUS = ("active", "inactive")

CASE_TYPES = ("new_business", "replacement", "review", "servicing")
CASE_STATUS = ("open", "fact_find", "proposed", "underwriting", "issued", "declined", "closed")

POLICY_STATUS = ("proposed", "applied", "underwriting", "in_force",
                 "lapsed", "surrendered", "replaced", "death_claim")

PARTY_ROLES = ("owner", "insured", "annuitant", "payer", "beneficiary", "assignee")
PARTY_ENTITY_TYPES = ("person", "household", "organization")
DESIGNATIONS = ("primary", "contingent")

PRODUCER_ENTITY_TYPES = ("user", "organization")
PRODUCER_ROLES = ("writing_agent", "servicing_agent", "broker_of_record", "override")

POLICY_RELATION_TYPES = ("replaces", "funded_by_1035", "rider_of", "successor", "same_case")

# (code, description, sensitive)
CAPABILITIES = [
    ("insurance.read", "View insurance cases, policies, and products", False),
    ("insurance.write", "Create and update insurance cases and policies", False),
    ("insurance.suitability", "Review insurance suitability and replacements", False),
    ("insurance.commissions.read", "View insurance commissions", False),
    ("insurance.licensing.read", "View producer licensing and continuing education", False),
    ("insurance.licensing.write", "Manage producer licensing and continuing education", False),
    ("insurance.sensitive.read", "View sensitive insurance data (identifiers, financials)", True),
]

# (code, name, description)
INSURANCE_ROLES = [
    ("insurance_agent", "Insurance Agent", "Writes and services individual life and annuity policies"),
    ("insurance_operations", "Insurance Operations", "Processes insurance cases, servicing, and requirements"),
    ("insurance_compliance", "Insurance Compliance", "Reviews suitability, replacements, and producer licensing"),
]


def _check(col, allowed):
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def _ts():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    # ---------------------------------------------------------------- product catalog (AD-5 / Refinement 5)
    op.create_table(
        "insurance_carrier_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("relationship_entity_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("naic_company_code", sa.String(16)),
        sa.Column("am_best_rating", sa.String(8)),
        sa.Column("appointment_status", sa.String(32)),
        *_ts(),
    )

    op.create_table(
        "insurance_product_families",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("carrier_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_type", sa.String(32), nullable=False),
        sa.Column("line", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_ts(),
        sa.CheckConstraint(_check("product_type", PRODUCT_TYPES), name="ck_ins_family_product_type"),
        sa.CheckConstraint(_check("line", PRODUCT_LINES), name="ck_ins_family_line"),
        sa.CheckConstraint(_check("status", FAMILY_STATUS), name="ck_ins_family_status"),
    )
    op.create_index("ix_ins_family_carrier", "insurance_product_families", ["carrier_id"])

    op.create_table(
        "insurance_product_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("family_id", sa.Integer,
                  sa.ForeignKey("insurance_product_families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_label", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.Date),
        sa.Column("effective_to", sa.Date),
        sa.Column("state_availability", sa.JSON),
        sa.Column("spec", sa.JSON),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_ts(),
        sa.UniqueConstraint("family_id", "version_label", name="uq_ins_product_version"),
    )

    # ---------------------------------------------------------------- case (AD-2)
    op.create_table(
        "insurance_cases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("engagement_id", sa.Integer,
                  sa.ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("case_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("objective", sa.String(1000)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("metadata_json", sa.JSON),
        *_ts(),
        sa.CheckConstraint(_check("case_type", CASE_TYPES), name="ck_ins_case_type"),
        sa.CheckConstraint(_check("status", CASE_STATUS), name="ck_ins_case_status"),
    )

    # ---------------------------------------------------------------- policy core
    op.create_table(
        "insurance_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("insurance_cases.id", ondelete="SET NULL")),
        sa.Column("carrier_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="RESTRICT")),
        sa.Column("product_version_id", sa.Integer,
                  sa.ForeignKey("insurance_product_versions.id", ondelete="RESTRICT")),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("policy_number", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("issue_date", sa.Date),
        sa.Column("face_amount", sa.Numeric(16, 2)),
        sa.Column("premium_amount", sa.Numeric(14, 2)),
        sa.Column("premium_mode", sa.String(24)),
        sa.Column("metadata_json", sa.JSON),
        *_ts(),
        sa.CheckConstraint(_check("status", POLICY_STATUS), name="ck_ins_policy_status"),
    )
    op.create_index("ix_ins_policy_case", "insurance_policies", ["case_id"])
    op.create_index("ix_ins_policy_carrier", "insurance_policies", ["carrier_id"])
    op.create_index("ix_ins_policy_person", "insurance_policies", ["person_id"])
    op.create_index("ix_ins_policy_household", "insurance_policies", ["household_id"])

    op.create_table(
        "insurance_coverages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("coverage_type", sa.String(48), nullable=False),
        sa.Column("face_amount", sa.Numeric(16, 2)),
        sa.Column("status", sa.String(24), server_default="active"),
        *_ts(),
    )
    op.create_index("ix_ins_coverage_policy", "insurance_coverages", ["policy_id"])

    op.create_table(
        "insurance_riders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rider_type", sa.String(48), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("face_amount", sa.Numeric(16, 2)),
        sa.Column("status", sa.String(24), server_default="active"),
        *_ts(),
    )
    op.create_index("ix_ins_rider_policy", "insurance_riders", ["policy_id"])

    op.create_table(
        "insurance_policy_values",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("cash_value", sa.Numeric(16, 2)),
        sa.Column("surrender_value", sa.Numeric(16, 2)),
        sa.Column("death_benefit", sa.Numeric(16, 2)),
        sa.Column("source", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("policy_id", "as_of_date", name="uq_ins_policy_value_asof"),
    )

    # ---------------------------------------------------------------- parties (Refinement 3) & producers (Refinement 4)
    op.create_table(
        "insurance_policy_parties",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("party_role", sa.String(16), nullable=False),
        sa.Column("party_entity_type", sa.String(16), nullable=False),
        sa.Column("party_entity_id", sa.Integer, nullable=False),
        sa.Column("share_percentage", sa.Numeric(6, 3)),
        sa.Column("designation", sa.String(12)),
        sa.Column("is_primary_insured", sa.Boolean, server_default=sa.false()),
        sa.Column("relationship_to_insured", sa.String(48)),
        sa.Column("effective_date", sa.Date),
        sa.Column("inactive_date", sa.Date),
        *_ts(),
        sa.CheckConstraint(_check("party_role", PARTY_ROLES), name="ck_ins_party_role"),
        sa.CheckConstraint(_check("party_entity_type", PARTY_ENTITY_TYPES), name="ck_ins_party_entity_type"),
        sa.CheckConstraint("designation IS NULL OR " + _check("designation", DESIGNATIONS),
                           name="ck_ins_party_designation"),
    )
    op.create_index("ix_ins_party_policy", "insurance_policy_parties", ["policy_id"])
    op.create_index("ix_ins_party_entity", "insurance_policy_parties",
                    ["party_entity_type", "party_entity_id"])

    op.create_table(
        "insurance_policy_producers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE")),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("insurance_cases.id", ondelete="CASCADE")),
        sa.Column("producer_entity_type", sa.String(16), nullable=False),
        sa.Column("producer_entity_id", sa.Integer, nullable=False),
        sa.Column("producer_role", sa.String(24), nullable=False),
        sa.Column("split_percentage", sa.Numeric(6, 3)),
        sa.Column("effective_date", sa.Date),
        sa.Column("inactive_date", sa.Date),
        *_ts(),
        sa.CheckConstraint(_check("producer_entity_type", PRODUCER_ENTITY_TYPES), name="ck_ins_producer_entity_type"),
        sa.CheckConstraint(_check("producer_role", PRODUCER_ROLES), name="ck_ins_producer_role"),
        sa.CheckConstraint("policy_id IS NOT NULL OR case_id IS NOT NULL", name="ck_ins_producer_anchor"),
    )
    op.create_index("ix_ins_producer_policy", "insurance_policy_producers", ["policy_id"])
    op.create_index("ix_ins_producer_case", "insurance_policy_producers", ["case_id"])
    op.create_index("ix_ins_producer_entity", "insurance_policy_producers",
                    ["producer_entity_type", "producer_entity_id"])

    op.create_table(
        "insurance_policy_relationships",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("from_policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(24), nullable=False),
        sa.Column("effective_date", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_check("relation_type", POLICY_RELATION_TYPES), name="ck_ins_policy_rel_type"),
        sa.CheckConstraint("from_policy_id <> to_policy_id", name="ck_ins_policy_rel_distinct"),
        sa.UniqueConstraint("from_policy_id", "to_policy_id", "relation_type", name="uq_ins_policy_rel"),
    )

    # ---------------------------------------------------------------- register the insurance domain
    for table in ("exceptions", "exception_types"):
        op.drop_constraint(f"ck_{table}_domain", table, type_="check")
        op.create_check_constraint(f"ck_{table}_domain", table, _check("domain", EXC_DOMAINS_NEW))

    # ---------------------------------------------------------------- capabilities / roles / grants
    bind = op.get_bind()

    for code, description, sensitive in CAPABILITIES:
        bind.execute(sa.text(
            "INSERT INTO capabilities (code, description, sensitive) VALUES (:code, :description, :sensitive) "
            "ON CONFLICT (code) DO NOTHING"),
            {"code": code, "description": description, "sensitive": sensitive})

    for code, name, description in INSURANCE_ROLES:
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
    grant("insurance_agent", ["insurance.read", "insurance.write", "insurance.commissions.read",
                              "exception.read", "exception.write"])
    grant("insurance_operations", ["insurance.read", "insurance.write", "insurance.licensing.read",
                                   "exception.read", "exception.write", "work.read", "capacity.read"])
    grant("insurance_compliance", ["insurance.read", "insurance.suitability", "insurance.sensitive.read",
                                   "insurance.licensing.read", "exception.read", "exception.compliance"])


def downgrade():
    bind = op.get_bind()

    # Remove any insurance exception data BEFORE narrowing the domain CHECK.
    bind.execute(sa.text("DELETE FROM exceptions WHERE domain='insurance'"))
    bind.execute(sa.text("DELETE FROM exception_types WHERE domain='insurance'"))
    for table in ("exceptions", "exception_types"):
        op.drop_constraint(f"ck_{table}_domain", table, type_="check")
        op.create_check_constraint(f"ck_{table}_domain", table, _check("domain", EXC_DOMAINS_OLD))

    bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id IN "
                         "(SELECT id FROM capabilities WHERE code LIKE 'insurance.%')"))
    bind.execute(sa.text("DELETE FROM roles WHERE code IN "
                         "('insurance_agent','insurance_operations','insurance_compliance')"))
    bind.execute(sa.text("DELETE FROM capabilities WHERE code LIKE 'insurance.%'"))

    for table in (
        "insurance_policy_relationships",
        "insurance_policy_producers",
        "insurance_policy_parties",
        "insurance_policy_values",
        "insurance_riders",
        "insurance_coverages",
        "insurance_policies",
        "insurance_cases",
        "insurance_product_versions",
        "insurance_product_families",
        "insurance_carrier_profiles",
    ):
        op.drop_table(table)
