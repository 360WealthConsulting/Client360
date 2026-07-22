"""Enterprise Document Management, Knowledge Repository & Client Artifact platform (Phase D.16).

Documents are the authoritative source domain; every other domain REFERENCES them. This phase
EXTENDS the existing ``documents`` table (preserving all rows) into a full platform and adds the
supporting tables — it does not replace the existing documents domain, and the legacy
``/documents`` routes + ``document.read/write`` capabilities remain.

- ``documents`` gains: nullable ``person_id`` (firm/internal docs), owner/household/organization
  anchors, classification + status lifecycle, folder + retention refs, effective/expiration dates,
  soft-delete + archive timestamps, storage provider/URI (SharePoint/OneDrive references — no
  storage duplication), OCR/preview/signature/encryption status, tags, notes, current_version.
- New tables: ``document_folders`` (hierarchical), ``document_versions`` (immutable history with
  major/minor/current/approval), ``document_relationships`` (polymorphic multi-domain links),
  ``document_retention_policies``, ``document_events`` (lifecycle log).
- Nine ``documents.*`` capabilities. Additive and reversible; capabilities seeded idempotently.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "n4e5f6a7b8c9"
down_revision = "m3d4e5f6a7b8"
branch_labels = None
depends_on = None

_CLASSIFICATIONS = ("client", "compliance", "tax", "insurance", "benefits", "retirement",
                    "estate", "investment", "operations", "marketing", "legal", "hr",
                    "internal", "archived")
_STATUSES = ("draft", "active", "review", "approved", "superseded", "archived", "deleted")

_CAPS = (
    ("documents.view", "View documents in the document library.", False,
     ("administrator", "advisor", "operations", "compliance")),
    ("documents.edit", "Create and edit document metadata.", False,
     ("administrator", "advisor", "operations")),
    ("documents.delete", "Delete (soft) documents.", False, ("administrator",)),
    ("documents.version", "Create new document versions.", False,
     ("administrator", "advisor", "operations")),
    ("documents.approve", "Approve documents.", False, ("administrator", "compliance")),
    ("documents.archive", "Archive documents.", False, ("administrator", "operations")),
    ("documents.restore", "Restore document versions and deleted documents.", False,
     ("administrator",)),
    ("documents.export", "Export document metadata.", False, ("administrator", "operations")),
    ("documents.manage_retention", "Manage retention policies.", False, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "document_folders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("parent_folder_id", sa.Integer,
                  sa.ForeignKey("document_folders.id", ondelete="SET NULL")),
        sa.Column("classification", sa.Text),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "document_retention_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("retention_years", sa.Integer),
        sa.Column("action_on_expiry", sa.Text, nullable=False, server_default="review"),
        sa.Column("description", sa.Text),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("action_on_expiry IN ('review','archive','delete')",
                           name="ck_retention_action"),
    )

    # Extend the existing documents table (additive; preserves existing rows).
    op.alter_column("documents", "person_id", existing_type=sa.Integer, nullable=True)
    op.add_column("documents", sa.Column("owner_user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("documents", sa.Column("household_id", sa.Integer,
                  sa.ForeignKey("households.id", ondelete="SET NULL")))
    op.add_column("documents", sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")))
    op.add_column("documents", sa.Column("classification", sa.Text))
    op.add_column("documents", sa.Column("subcategory", sa.Text))
    op.add_column("documents", sa.Column("status", sa.Text, nullable=False, server_default="active"))
    op.add_column("documents", sa.Column("folder_id", sa.Integer,
                  sa.ForeignKey("document_folders.id", ondelete="SET NULL")))
    op.add_column("documents", sa.Column("retention_policy_id", sa.Integer,
                  sa.ForeignKey("document_retention_policies.id", ondelete="SET NULL")))
    op.add_column("documents", sa.Column("effective_date", sa.Date))
    op.add_column("documents", sa.Column("expiration_date", sa.Date))
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("storage_provider", sa.Text, nullable=False,
                  server_default="local"))
    op.add_column("documents", sa.Column("storage_uri", sa.Text))
    op.add_column("documents", sa.Column("ocr_status", sa.Text))
    op.add_column("documents", sa.Column("preview_status", sa.Text))
    op.add_column("documents", sa.Column("signature_status", sa.Text))
    op.add_column("documents", sa.Column("encryption_status", sa.Text))
    op.add_column("documents", sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("documents", sa.Column("notes", sa.Text, nullable=False, server_default=""))
    op.add_column("documents", sa.Column("current_version", sa.Integer, nullable=False, server_default="1"))
    op.create_check_constraint(
        "ck_documents_status", "documents",
        "status IN ('draft','active','review','approved','superseded','archived','deleted')")
    op.create_check_constraint(
        "ck_documents_classification", "documents",
        "classification IS NULL OR classification IN "
        "('client','compliance','tax','insurance','benefits','retirement','estate','investment',"
        "'operations','marketing','legal','hr','internal','archived')")
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_classification", "documents", ["classification"])
    op.create_index("ix_documents_folder", "documents", ["folder_id"])

    # document_versions already exists (client portal, f640a6c4e5f6). EXTEND it additively into
    # the full platform version model (preserving portal columns version_number/
    # previous_document_id/uploaded_by_*).
    for col in (sa.Column("major", sa.Integer, nullable=False, server_default="1"),
                sa.Column("minor", sa.Integer, nullable=False, server_default="0"),
                sa.Column("stored_name", sa.Text),
                sa.Column("storage_path", sa.Text),
                sa.Column("storage_uri", sa.Text),
                sa.Column("sha256", sa.Text),
                sa.Column("size_bytes", sa.BigInteger),
                sa.Column("content_type", sa.Text),
                sa.Column("author_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
                sa.Column("notes", sa.Text),
                sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.text("false")),
                sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
                sa.Column("approved_at", sa.DateTime(timezone=True))):
        op.add_column("document_versions", col)

    op.create_table(
        "document_relationships",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("relationship_type", sa.Text),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("document_id", "entity_type", "entity_id", name="uq_document_relationship"),
    )
    op.create_index("ix_document_relationships_doc", "document_relationships", ["document_id"])
    op.create_index("ix_document_relationships_entity", "document_relationships",
                    ["entity_type", "entity_id"])

    op.create_table(
        "document_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_document_events_doc", "document_events", ["document_id"])

    # Seed a couple of standard retention policies (idempotent).
    for code, name, years, action in (("standard-7y", "Standard (7 years)", 7, "review"),
                                      ("permanent", "Permanent", None, "review"),
                                      ("marketing-2y", "Marketing (2 years)", 2, "archive")):
        if bind.execute(sa.text("SELECT id FROM document_retention_policies WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text("INSERT INTO document_retention_policies "
                                 "(code, name, retention_years, action_on_expiry) "
                                 "VALUES (:c, :n, :y, :a)"),
                         {"c": code, "n": name, "y": years, "a": action})

    for code, description, sensitive, roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive}).scalar()
        for role_code in roles:
            role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                                     "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    op.drop_table("document_events")
    op.drop_table("document_relationships")
    for col in ("approved_at", "approved_by", "is_current", "notes", "author_user_id",
                "content_type", "size_bytes", "sha256", "storage_uri", "storage_path",
                "stored_name", "minor", "major"):
        op.drop_column("document_versions", col)
    op.drop_index("ix_documents_folder", table_name="documents")
    op.drop_index("ix_documents_classification", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_constraint("ck_documents_classification", "documents", type_="check")
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    for col in ("current_version", "notes", "tags", "encryption_status", "signature_status",
                "preview_status", "ocr_status", "storage_uri", "storage_provider", "deleted_at",
                "archived_at", "expiration_date", "effective_date", "retention_policy_id",
                "folder_id", "status", "subcategory", "classification", "organization_id",
                "household_id", "owner_user_id"):
        op.drop_column("documents", col)
    op.alter_column("documents", "person_id", existing_type=sa.Integer, nullable=False)
    op.drop_table("document_retention_policies")
    op.drop_table("document_folders")
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
