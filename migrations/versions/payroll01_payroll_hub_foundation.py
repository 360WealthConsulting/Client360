"""payroll hub foundation (360Plus / Client360)

Adds the Payroll Hub as a first-class business-client module: payroll accounts/providers,
employees, payroll periods/runs, payroll<->document links (reusing the canonical ``documents``
store), payroll issues/tasks, and an inert provider-connection seam for future ADP / QuickBooks
Payroll adapters. Businesses reuse the existing ``relationship_entities`` graph (an organization is
a ``relationship_entities`` row; every ``organization_id`` here is a ``relationship_entities.id``).

This is an information + workflow system only — NOT a payroll processor. There is no payroll
submission, direct deposit, ACH, tax payment, or money movement, and no live ADP/QuickBooks API.
Money fields are integer USD cents. Access reuses the existing capability model
(``payroll.read`` / ``payroll.write``, seeded + granted to administrator here) and the existing
per-client feature catalog (the ``payroll`` feature is already registered, product ``business``).

All additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "payroll01"
down_revision = "billing01"
branch_labels = None
depends_on = None

# (code, name, adapter_status) — reference providers. 'manual' = staff-entered, no live API.
PROVIDERS = [
    ("adp", "ADP", "disabled"),
    ("quickbooks_payroll", "QuickBooks Payroll", "disabled"),
    ("other", "Other", "manual"),
]

CAPABILITIES = [
    ("payroll.read", "View payroll accounts, employees, runs, documents, and issues", False),
    ("payroll.write", "Manage payroll accounts, employees, runs, documents, and issues", False),
]

_ACCOUNT_STATUS = ("prospect", "active", "suspended", "inactive")
_PAY_FREQUENCY = ("weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual", "other")
_EMP_STATUS = ("active", "terminated", "on_leave", "pending")
_COMP_TYPE = ("salary", "hourly", "commission", "contract", "other")
_COMP_PERIOD = ("annual", "monthly", "weekly", "hourly", "other")
_RUN_STATUS = ("scheduled", "draft", "processed", "paid", "void")
_DOC_CATEGORY = ("payroll_report", "w2", "w3", "941", "state_filing",
                 "retirement_contribution_report", "other")
_ISSUE_TYPE = ("missing_filing", "payroll_discrepancy", "contribution_issue",
               "employee_setup_issue", "tax_notice", "general_payroll_task")
_ISSUE_SEVERITY = ("low", "medium", "high")
_ISSUE_STATUS = ("open", "in_progress", "resolved", "cancelled")
_CONN_TYPE = ("payroll", "hris")
_CONN_STATUS = ("not_connected", "pending", "connected", "error")
_ADAPTER_STATUS = ("disabled", "manual", "connected")


def _check(col, allowed):
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"))


def upgrade():
    # ---------------------------------------------------------------- providers (reference)
    op.create_table(
        "payroll_providers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("adapter_status", sa.String(20), nullable=False, server_default="disabled"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(_check("adapter_status", _ADAPTER_STATUS), name="ck_payroll_provider_adapter"),
    )

    # ---------------------------------------------------------------- accounts
    op.create_table(
        "payroll_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("payroll_providers.id")),
        sa.Column("external_account_id", sa.String(120)),   # provider company/account identifier
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("pay_frequency", sa.String(20)),
        sa.Column("next_payroll_date", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(_check("status", _ACCOUNT_STATUS), name="ck_payroll_account_status"),
        sa.CheckConstraint("pay_frequency IS NULL OR " + _check("pay_frequency", _PAY_FREQUENCY),
                           name="ck_payroll_account_frequency"),
    )
    op.create_index("ix_payroll_accounts_org", "payroll_accounts", ["organization_id"])

    # ---------------------------------------------------------------- employees
    op.create_table(
        "payroll_employees",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payroll_account_id", sa.Integer,
                  sa.ForeignKey("payroll_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("first_name", sa.String(120)),
        sa.Column("last_name", sa.String(120)),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("employment_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("hire_date", sa.Date),
        sa.Column("termination_date", sa.Date),
        sa.Column("compensation_type", sa.String(20)),
        sa.Column("compensation_amount_cents", sa.Integer),   # money as integer USD cents
        sa.Column("compensation_period", sa.String(20), nullable=False, server_default="annual"),
        sa.Column("provider_employee_id", sa.String(120)),    # payroll-provider employee ID
        sa.Column("retirement_plan_participant", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(_check("employment_status", _EMP_STATUS), name="ck_payroll_employee_status"),
        sa.CheckConstraint("compensation_type IS NULL OR " + _check("compensation_type", _COMP_TYPE),
                           name="ck_payroll_employee_comp_type"),
        sa.CheckConstraint(_check("compensation_period", _COMP_PERIOD), name="ck_payroll_employee_comp_period"),
    )
    op.create_index("ix_payroll_employees_account", "payroll_employees",
                    ["payroll_account_id", "employment_status"])
    op.create_index("ix_payroll_employees_org", "payroll_employees", ["organization_id"])

    # ---------------------------------------------------------------- periods / runs
    op.create_table(
        "payroll_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payroll_account_id", sa.Integer,
                  sa.ForeignKey("payroll_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date),
        sa.Column("pay_date", sa.Date),
        sa.Column("gross_cents", sa.Integer),
        sa.Column("employee_taxes_cents", sa.Integer),
        sa.Column("employer_taxes_cents", sa.Integer),
        sa.Column("deductions_cents", sa.Integer),
        sa.Column("retirement_contributions_cents", sa.Integer),
        sa.Column("benefits_cents", sa.Integer),
        sa.Column("net_cents", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("notes", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(_check("status", _RUN_STATUS), name="ck_payroll_run_status"),
    )
    op.create_index("ix_payroll_runs_account", "payroll_runs", ["payroll_account_id", "pay_date"])
    op.create_index("ix_payroll_runs_org", "payroll_runs", ["organization_id"])

    # ---------------------------------------------------------------- document links (reuse documents)
    op.create_table(
        "payroll_document_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payroll_account_id", sa.Integer, sa.ForeignKey("payroll_accounts.id", ondelete="CASCADE")),
        sa.Column("payroll_run_id", sa.Integer, sa.ForeignKey("payroll_runs.id", ondelete="CASCADE")),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("period_label", sa.String(60)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        _ts("created_at"),
        sa.CheckConstraint(_check("category", _DOC_CATEGORY), name="ck_payroll_doc_category"),
        sa.UniqueConstraint("document_id", "organization_id", "category", name="uq_payroll_document_link"),
    )
    op.create_index("ix_payroll_document_links_org", "payroll_document_links", ["organization_id"])

    # ---------------------------------------------------------------- issues / tasks (business-scoped)
    op.create_table(
        "payroll_issues",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payroll_account_id", sa.Integer, sa.ForeignKey("payroll_accounts.id", ondelete="CASCADE")),
        sa.Column("payroll_run_id", sa.Integer, sa.ForeignKey("payroll_runs.id", ondelete="SET NULL")),
        sa.Column("payroll_employee_id", sa.Integer, sa.ForeignKey("payroll_employees.id", ondelete="SET NULL")),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("issue_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("assigned_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("due_date", sa.Date),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(_check("issue_type", _ISSUE_TYPE), name="ck_payroll_issue_type"),
        sa.CheckConstraint(_check("severity", _ISSUE_SEVERITY), name="ck_payroll_issue_severity"),
        sa.CheckConstraint(_check("status", _ISSUE_STATUS), name="ck_payroll_issue_status"),
    )
    op.create_index("ix_payroll_issues_org_status", "payroll_issues", ["organization_id", "status"])
    op.create_index("ix_payroll_issues_account", "payroll_issues", ["payroll_account_id"])

    # ---------------------------------------------------------------- provider connections (inert seam)
    op.create_table(
        "payroll_provider_connections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("payroll_providers.id"), nullable=False),
        sa.Column("connection_type", sa.String(20), nullable=False, server_default="payroll"),
        sa.Column("status", sa.String(20), nullable=False, server_default="not_connected"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_status", sa.String(30)),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(_check("connection_type", _CONN_TYPE), name="ck_payroll_conn_type"),
        sa.CheckConstraint(_check("status", _CONN_STATUS), name="ck_payroll_conn_status"),
        sa.UniqueConstraint("organization_id", "provider_id", "connection_type",
                            name="uq_payroll_provider_connection"),
    )

    # ---------------------------------------------------------------- seeds
    bind = op.get_bind()
    op.bulk_insert(
        sa.table("payroll_providers", sa.column("code"), sa.column("name"), sa.column("adapter_status")),
        [{"code": c, "name": n, "adapter_status": s} for c, n, s in PROVIDERS])

    for code, description, sensitive in CAPABILITIES:
        bind.execute(sa.text(
            "INSERT INTO capabilities (code, description, sensitive) VALUES (:code, :description, :sensitive) "
            "ON CONFLICT (code) DO NOTHING"),
            {"code": code, "description": description, "sensitive": sensitive})
    bind.execute(sa.text(
        "INSERT INTO role_capabilities (role_id, capability_id) "
        "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
        "WHERE r.code = 'administrator' AND c.code = ANY(:caps) ON CONFLICT DO NOTHING"),
        {"caps": [c for c, _, _ in CAPABILITIES]})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id IN "
                         "(SELECT id FROM capabilities WHERE code LIKE 'payroll.%')"))
    bind.execute(sa.text("DELETE FROM capabilities WHERE code LIKE 'payroll.%'"))
    for tbl in (
        "payroll_provider_connections", "payroll_issues", "payroll_document_links",
        "payroll_runs", "payroll_employees", "payroll_accounts", "payroll_providers",
    ):
        op.drop_table(tbl)
