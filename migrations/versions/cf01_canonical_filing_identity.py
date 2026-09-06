"""Canonical filing: first-class tax year, owner-scoped folder identity, filing service vocabulary.

Revision ID: cf01
Revises: psl02

WHAT THIS ADDS, AND WHY EACH PIECE
-----------------------------------
**1. ``documents.tax_year`` / ``tax_year_confidence`` / ``tax_year_source``.**
Tax year is a folder level in the canonical hierarchy, so it must be range-queryable, indexable and
constrainable. Today it lives in ``documents.tags->>'tax_year'`` on ~515 rows: a JSONB tag cannot be
constrained to an integer, cannot be range-scanned efficiently, and — the part that matters most —
has nowhere to record HOW CONFIDENT the value is. The strong/moderate split is precisely what
separates a document that may be filed automatically from one that may not, so the confidence has to
live beside the value rather than being recomputed by every reader.

The tag is NOT removed and NOT backfilled here. Both representations coexist during the transition;
readers keep working unchanged.

**2. ``document_folders`` owner-scoped natural identity.**
The table's only uniqueness guarantee is ``code``, a text slug. That makes folder identity a
property of a string, and a string is exactly what a rename changes. These columns make identity
structural — ``(owner_scope_type, owner_scope_id, folder_kind, service_code, tax_year)`` — so a
client renamed in master data keeps its folder, and two owners who happen to sanitize to the same
visible label keep two.

The unique index uses ``coalesce`` rather than plain nullable columns because Postgres treats NULLs
as distinct in a unique index: without it, every client-level row (``service_code`` and ``tax_year``
both NULL) would be mutually non-conflicting and the duplicate prevention would silently do nothing.
It is also PARTIAL — legacy folders carry no owner scope and must not be forced into the constraint.

**3. ``service_lines`` rows for the filing services that have none.**
``service_lines`` has no row for Sales & Litter Tax, Client Services, 1099 Processing or Tax
Resolution, so the two vocabularies cannot currently be reconciled. These are inserted idempotently
by code, never by id.

NOT APPLIED TO PRODUCTION. Additive and fully reversible; no existing column is altered, no data is
rewritten, and nothing is backfilled.
"""
import sqlalchemy as sa
from alembic import op

revision = "cf01"
down_revision = "psl02"
branch_labels = None
depends_on = None

_FOLDER_IDENTITY_INDEX = "uq_document_folders_owner_scope_identity"
_TAX_YEAR_INDEX = "ix_documents_tax_year"

_FOLDER_KINDS = ("client", "service", "year")
_YEAR_CONFIDENCE = ("strong", "moderate", "conflict")

#: Filing services with no ``service_lines`` counterpart. Mirrors
#: ``filing_service_vocabulary.SERVICE_LINE_SEED_ROWS``; kept literal so the migration does not
#: import application code.
_SERVICE_LINE_SEED = (
    ("sales_litter_tax", "Sales & Litter Tax"),
    ("client_services", "Client Services"),
    ("form_1099_processing", "1099 Processing"),
    ("tax_resolution", "Tax Resolution"),
)


def upgrade() -> None:
    # --- 1. first-class tax year ---------------------------------------------------------------
    op.add_column("documents", sa.Column("tax_year", sa.SmallInteger(), nullable=True))
    op.add_column("documents", sa.Column("tax_year_confidence", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("tax_year_source", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_documents_tax_year_range", "documents",
        "tax_year IS NULL OR (tax_year BETWEEN 1990 AND 2100)")
    op.create_check_constraint(
        "ck_documents_tax_year_confidence", "documents",
        "tax_year_confidence IS NULL OR tax_year_confidence IN "
        f"({', '.join(repr(c) for c in _YEAR_CONFIDENCE)})")
    # A confidence without a year is meaningless, and a year whose confidence nobody recorded must
    # never be mistaken for a strong one.
    op.create_check_constraint(
        "ck_documents_tax_year_confidence_pairing", "documents",
        "(tax_year IS NULL) = (tax_year_confidence IS NULL)")
    op.create_index(_TAX_YEAR_INDEX, "documents", ["tax_year"],
                    postgresql_where=sa.text("tax_year IS NOT NULL"))

    # --- 2. owner-scoped folder identity --------------------------------------------------------
    op.add_column("document_folders", sa.Column("owner_scope_type", sa.Text(), nullable=True))
    op.add_column("document_folders", sa.Column("owner_scope_id", sa.Integer(), nullable=True))
    op.add_column("document_folders", sa.Column("folder_kind", sa.Text(), nullable=True))
    op.add_column("document_folders", sa.Column("service_code", sa.Text(), nullable=True))
    op.add_column("document_folders", sa.Column("tax_year", sa.SmallInteger(), nullable=True))
    op.add_column("document_folders",
                  sa.Column("owner_source_label", sa.Text(), nullable=True))

    op.create_check_constraint(
        "ck_document_folders_scope_type", "document_folders",
        "owner_scope_type IS NULL OR owner_scope_type IN ('person', 'household', 'organization')")
    op.create_check_constraint(
        "ck_document_folders_kind", "document_folders",
        "folder_kind IS NULL OR folder_kind IN "
        f"({', '.join(repr(k) for k in _FOLDER_KINDS)})")
    # Scope type and id travel together, and a canonical folder always has both plus a kind.
    op.create_check_constraint(
        "ck_document_folders_scope_pairing", "document_folders",
        "(owner_scope_type IS NULL) = (owner_scope_id IS NULL)")
    op.create_check_constraint(
        "ck_document_folders_kind_pairing", "document_folders",
        "(owner_scope_type IS NULL) = (folder_kind IS NULL)")
    # Each kind carries exactly the identity fields its level needs — no more, no less. This is what
    # stops a 'client' row acquiring a tax year, which would make it a different folder.
    op.create_check_constraint(
        "ck_document_folders_kind_shape", "document_folders",
        "folder_kind IS NULL"
        " OR (folder_kind = 'client'  AND service_code IS NULL AND tax_year IS NULL)"
        " OR (folder_kind = 'service' AND service_code IS NOT NULL AND tax_year IS NULL)"
        " OR (folder_kind = 'year'    AND service_code IS NOT NULL AND tax_year IS NOT NULL)")
    op.create_check_constraint(
        "ck_document_folders_tax_year_range", "document_folders",
        "tax_year IS NULL OR (tax_year BETWEEN 1990 AND 2100)")

    # Partial + coalesced: NULLs would otherwise compare distinct and defeat the whole point.
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX {_FOLDER_IDENTITY_INDEX} ON document_folders ("
        " owner_scope_type, owner_scope_id, folder_kind,"
        " coalesce(service_code, ''), coalesce(tax_year, -1))"
        " WHERE owner_scope_type IS NOT NULL"))

    # --- 3. reconcile the service vocabulary ----------------------------------------------------
    for code, name in _SERVICE_LINE_SEED:
        op.execute(sa.text(
            "INSERT INTO service_lines (code, name, active) VALUES (:code, :name, true) "
            "ON CONFLICT (code) DO NOTHING").bindparams(code=code, name=name))


def downgrade() -> None:
    for code, _name in _SERVICE_LINE_SEED:
        op.execute(sa.text("DELETE FROM service_lines WHERE code = :code").bindparams(code=code))

    op.execute(sa.text(f"DROP INDEX IF EXISTS {_FOLDER_IDENTITY_INDEX}"))
    for name in ("ck_document_folders_tax_year_range", "ck_document_folders_kind_shape",
                 "ck_document_folders_kind_pairing", "ck_document_folders_scope_pairing",
                 "ck_document_folders_kind", "ck_document_folders_scope_type"):
        op.drop_constraint(name, "document_folders", type_="check")
    for column in ("owner_source_label", "tax_year", "service_code", "folder_kind",
                   "owner_scope_id", "owner_scope_type"):
        op.drop_column("document_folders", column)

    op.drop_index(_TAX_YEAR_INDEX, table_name="documents")
    for name in ("ck_documents_tax_year_confidence_pairing", "ck_documents_tax_year_confidence",
                 "ck_documents_tax_year_range"):
        op.drop_constraint(name, "documents", type_="check")
    for column in ("tax_year_source", "tax_year_confidence", "tax_year"):
        op.drop_column("documents", column)
