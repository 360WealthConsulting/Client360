"""Client Vault MVP — vault_documents + versions + links + audit_events, and vault.* capabilities.

Reuses the existing users/people/households/engagements tables (FK anchors), the roles /
capabilities / role_capabilities RBAC model, and the audit conventions. No parallel document
platform: this is a focused, employee-facing client-document vault.
"""
import sqlalchemy as sa
from alembic import op

revision = "va01ult0mvp1"
down_revision = "n5s6u7p8v9w0"
branch_labels = None
depends_on = None

CATEGORIES = ("tax", "wealth", "accounting", "payroll", "benefits", "insurance", "compliance", "general")
STATUSES = ("uploaded", "under_review", "approved", "rejected", "signed", "filed", "archived")

# vault.* capabilities. Category access is granted per-department; vault.access.all is the
# cross-department override (firm leadership / compliance oversight).
CAPABILITIES = {
    "vault.view": "View Client Vault documents the user is authorized for",
    "vault.upload": "Upload Client Vault documents and new versions",
    "vault.download": "Download Client Vault document contents",
    "vault.manage": "Edit metadata and archive Client Vault documents",
    "vault.access.all": "Access Client Vault documents across all departments/categories",
    **{f"vault.category.{c}": f"Access Client Vault documents categorized '{c}'" for c in CATEGORIES},
}

_ALL_CATEGORY_CAPS = [f"vault.category.{c}" for c in CATEGORIES]
_FULL = {"vault.view", "vault.upload", "vault.download", "vault.manage",
         "vault.access.all", *_ALL_CATEGORY_CAPS}


def _dept(*category_caps):
    # A department role: view/upload/download its own categories, plus shared "general".
    return {"vault.view", "vault.upload", "vault.download",
            "vault.category.general", *category_caps}


# Grant map keyed on existing role CODES; roles absent in a given DB are skipped (idempotent).
# Generic roles (advisor/operations) are deliberately NOT granted vault access.
ROLE_GRANTS = {
    "administrator": set(_FULL),
    "executive": set(_FULL),
    "compliance": {"vault.view", "vault.download", "vault.manage", "vault.access.all",
                   "vault.category.general"},
    "benefits_compliance": {"vault.view", "vault.download", "vault.manage", "vault.access.all",
                            "vault.category.general"},
    "insurance_compliance": {"vault.view", "vault.download", "vault.manage", "vault.access.all",
                             "vault.category.general"},
    "tax": _dept("vault.category.tax"),
    "accounting": _dept("vault.category.accounting"),
    "payroll": _dept("vault.category.payroll"),
    "wealth": _dept("vault.category.wealth"),
    "benefits": _dept("vault.category.benefits"),
    "benefits_advisor": _dept("vault.category.benefits"),
    "benefits_operations": _dept("vault.category.benefits"),
    "insurance": _dept("vault.category.insurance"),
    "insurance_agent": _dept("vault.category.insurance"),
    "insurance_operations": _dept("vault.category.insurance"),
}


def upgrade():
    op.create_table(
        "vault_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("document_type", sa.Text),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("security_classification", sa.Text, nullable=False, server_default="internal"),
        sa.Column("status", sa.Text, nullable=False, server_default="uploaded"),
        sa.Column("mime_type", sa.Text),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("checksum_sha256", sa.Text, nullable=False),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("uploaded_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("category IN (" + ",".join(f"'{c}'" for c in CATEGORIES) + ")",
                           name="ck_vault_documents_category"),
        sa.CheckConstraint("status IN (" + ",".join(f"'{v}'" for v in STATUSES) + ")",
                           name="ck_vault_documents_status"),
    )
    op.create_index(op.f("ix_vault_documents_category"), "vault_documents", ["category"])
    op.create_index(op.f("ix_vault_documents_status"), "vault_documents", ["status"])

    op.create_table(
        "vault_document_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("vault_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("checksum_sha256", sa.Text, nullable=False),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("uploaded_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "version_number", name="uq_vault_version_number"),
    )
    op.create_index(op.f("ix_vault_document_versions_document_id"),
                    "vault_document_versions", ["document_id"])

    op.create_table(
        "vault_document_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("vault_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer),        # organizations table not present in all DBs
        sa.Column("engagement_id", sa.Integer, sa.ForeignKey("engagements.id", ondelete="SET NULL")),
        sa.Column("work_item_id", sa.Integer),           # work-item model varies; plain reference
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_vault_document_links_document_id"), "vault_document_links", ["document_id"])
    op.create_index(op.f("ix_vault_document_links_person_id"), "vault_document_links", ["person_id"])
    op.create_index(op.f("ix_vault_document_links_household_id"), "vault_document_links", ["household_id"])

    op.create_table(
        "vault_document_audit_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("vault_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ip_address", sa.Text),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(op.f("ix_vault_document_audit_events_document_id"),
                    "vault_document_audit_events", ["document_id"])

    _seed_capabilities()


def _seed_capabilities():
    bind = op.get_bind()
    cap_ids = {}
    for code, description in CAPABILITIES.items():
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(sa.text(
                "INSERT INTO capabilities (code, description, sensitive) VALUES (:c, :d, true) RETURNING id"),
                {"c": code, "d": description}).scalar()
        cap_ids[code] = cid

    for role_code, codes in ROLE_GRANTS.items():
        role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
        if role_id is None:
            continue                                   # role absent in this DB — skip
        for code in codes:
            cid = cap_ids[code]
            exists = bind.execute(sa.text(
                "SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(sa.text(
                    "INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                    {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    codes = list(CAPABILITIES)
    bind.execute(sa.text(
        "DELETE FROM role_capabilities WHERE capability_id IN "
        "(SELECT id FROM capabilities WHERE code = ANY(:codes))"), {"codes": codes})
    bind.execute(sa.text("DELETE FROM capabilities WHERE code = ANY(:codes)"), {"codes": codes})
    op.drop_table("vault_document_audit_events")
    op.drop_table("vault_document_links")
    op.drop_table("vault_document_versions")
    op.drop_table("vault_documents")
