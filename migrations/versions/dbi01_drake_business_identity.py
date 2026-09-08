"""Non-natural Drake identity foundation — businesses, estates and trusts.

WHY

``drake_identity`` has one owner column, ``primary_person_id``, so every Drake identifier was forced
onto a ``people`` row. 150 of the 1,802 production identifiers are not natural persons — 141 file
entity returns (1120/1120S/1065/990) and 8 are estates or mixed decedent/estate identifiers — and 46
of those are attached to a person today. ``person_source_links`` and ``source_contacts`` have no
entity column either, so the source contacts behind a business identifier can only ever attach to a
person. That is a schema limitation, not a tooling gap, and it is what this migration removes.

WHAT THIS ADDS

``drake_business_identity`` — Drake tax identities for non-natural taxpayers, pointing at
``relationship_entities`` instead of ``people``. It has no person column at all, so the boundary is
structural: a business identity cannot require a dummy ``people`` row because there is nowhere to put
one.

``entity_source_links`` — the entity-side parallel of ``person_source_links``. Deliberately a second
table rather than a polymorphic retrofit: making ``person_source_links.person_id`` nullable would
weaken an invariant that holds across 11,129 rows, break ``uq_person_source_link``, and turn every
existing reader — including the person-merge registry, which walks that table for every merge — into
a NULL-handling hazard. Nothing about ``person_source_links`` is touched here.

Also extends ``ck_org_profiles_entity_form`` with ``estate``, ``revocable_trust`` and
``irrevocable_trust``. Production stores estates as ``relationship_entities.entity_type = 'trust'``
(all five of them) and ``canonical_population._DRAKE_ENTITY_BY_RETURN`` already maps 1041 -> "trust",
so the bucket stays as it is and the legal distinction goes where the taxonomy already lives. No
``entity_type = 'estate'`` path is introduced.

WHAT THIS DOES NOT DO

Nothing is backfilled, moved, deleted or relinked. No ``drake_identity`` row changes, no
``person_source_links`` row changes, no entity is created, and both new tables are created empty.
The capability is dormant until a later, separately authorised phase. The missing foreign key on
``drake_identity.primary_person_id`` is deliberately NOT added here; it is a separate hardening
decision about an existing populated table.

Revision ID: dbi01
Revises: emailnorm01
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op

revision = "dbi01"
down_revision = "emailnorm01"
branch_labels = None
depends_on = None

#: Mirrors app.services.link_trust.TRUST_LEVELS and psl02's list for person_source_links. The two
#: vocabularies must not drift, so the same values are used verbatim.
_TRUST_LEVELS = (
    "identifier_verified", "human_approved", "machine_exact_name", "machine_name_location",
    "machine_contact", "canonical_repair", "unknown_legacy",
)
_CONFIRMATION_SOURCES = ("human", "machine", "unknown")
_SUBJECT_TYPES = ("business_entity", "estate_or_trust")

_ENTITY_FORMS_BEFORE = ("llc", "c_corp", "s_corp", "partnership", "nonprofit", "trust",
                        "sole_prop", "professional_practice")
_ENTITY_FORMS_AFTER = _ENTITY_FORMS_BEFORE + ("estate", "revocable_trust", "irrevocable_trust")


def _in_list(column: str, values) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "drake_business_identity",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # The join key to drake_client_returns and source_contacts.raw_data->>'identifier_hash'.
        # Same hashed form as drake_identity; no raw taxpayer identifier is stored.
        sa.Column("identifier_hash", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        # Nullable on purpose: 111 of the 150 non-natural identifiers have no entity today, and an
        # unadjudicated identity must be representable without inventing one. NULL means "not yet
        # adjudicated", which is what the partial index below serves.
        sa.Column("relationship_entity_id", sa.Integer(),
                  sa.ForeignKey("relationship_entities.id"), nullable=True),
        # Year bounds are not decoration. For an identifier that is a person and later an estate,
        # these are what keep both histories intact without deleting either.
        sa.Column("first_year", sa.Integer(), nullable=False),
        sa.Column("last_year", sa.Integer(), nullable=False),
        sa.Column("return_count", sa.Integer(), nullable=False),
        sa.Column("subject_name", sa.Text(), nullable=False),
        # The evidence that produced subject_type, kept on the row so a classification can be
        # audited without re-deriving it. An array because six identifiers carry more than one.
        sa.Column("return_types", sa.ARRAY(sa.Text()), nullable=False),
        # An estate files under the decedent's identifier. This records "this estate succeeds that
        # person identity" explicitly rather than leaving it to be inferred.
        sa.Column("decedent_identifier_hash", sa.Text(), nullable=True),
        sa.Column("trust_level", sa.Text(), nullable=True),
        sa.Column("confirmation_source", sa.Text(), nullable=True),
        sa.Column("evidence_method", sa.Text(), nullable=True),
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # NOT identifier_hash alone. One identifier may be a natural person in early years and an
        # estate later; keying on the hash alone would force one of the two subjects to be dropped.
        # This also guarantees one identity cannot reference two entities, because
        # relationship_entity_id is a column on the unique row.
        sa.UniqueConstraint("identifier_hash", "subject_type", name="uq_drake_business_identity"),
        sa.CheckConstraint(_in_list("subject_type", _SUBJECT_TYPES), name="ck_dbi_subject_type"),
        sa.CheckConstraint("last_year >= first_year", name="ck_dbi_year_range"),
        sa.CheckConstraint("return_count > 0", name="ck_dbi_return_count"),
        sa.CheckConstraint(f"trust_level IS NULL OR {_in_list('trust_level', _TRUST_LEVELS)}",
                           name="ck_dbi_trust_level"),
        sa.CheckConstraint(
            f"confirmation_source IS NULL OR "
            f"{_in_list('confirmation_source', _CONFIRMATION_SOURCES)}",
            name="ck_dbi_confirmation_source"),
        # The gap that forced the D5 relink's actor into an external receipt: a human-approved
        # assignment cannot be recorded without an actor and a timestamp.
        sa.CheckConstraint(
            "trust_level IS DISTINCT FROM 'human_approved' "
            "OR (confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_dbi_human_approval_attributed"),
    )
    op.create_index("ix_dbi_entity", "drake_business_identity", ["relationship_entity_id"])
    op.create_index("ix_dbi_unadjudicated", "drake_business_identity",
                    ["identifier_hash"], postgresql_where=sa.text(
                        "relationship_entity_id IS NULL"))

    op.create_table(
        "entity_source_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("relationship_entity_id", sa.Integer(),
                  sa.ForeignKey("relationship_entities.id"), nullable=False),
        sa.Column("source_contact_id", sa.Integer(),
                  sa.ForeignKey("source_contacts.id"), nullable=False),
        sa.Column("match_method", sa.String(length=100), nullable=True),
        sa.Column("match_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("trust_level", sa.Text(), nullable=True),
        sa.Column("confirmation_source", sa.Text(), nullable=True),
        sa.Column("evidence_method", sa.Text(), nullable=True),
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("relationship_entity_id", "source_contact_id",
                            name="uq_entity_source_link"),
        sa.CheckConstraint(f"trust_level IS NULL OR {_in_list('trust_level', _TRUST_LEVELS)}",
                           name="ck_entity_source_links_trust_level"),
        sa.CheckConstraint(
            f"confirmation_source IS NULL OR "
            f"{_in_list('confirmation_source', _CONFIRMATION_SOURCES)}",
            name="ck_entity_source_links_confirmation_source"),
        sa.CheckConstraint(
            "trust_level IS DISTINCT FROM 'human_approved' "
            "OR (confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_entity_source_links_human_approval_attributed"),
    )
    op.create_index("ix_entity_source_links_trust_level", "entity_source_links", ["trust_level"])

    # Subtype for a non-natural legal person, so the entity_type bucket does not have to carry it.
    op.drop_constraint("ck_org_profiles_entity_form", "organization_profiles", type_="check")
    op.create_check_constraint(
        "ck_org_profiles_entity_form", "organization_profiles",
        f"entity_form IS NULL OR {_in_list('entity_form', _ENTITY_FORMS_AFTER)}")


def downgrade() -> None:
    op.drop_constraint("ck_org_profiles_entity_form", "organization_profiles", type_="check")
    op.create_check_constraint(
        "ck_org_profiles_entity_form", "organization_profiles",
        f"entity_form IS NULL OR {_in_list('entity_form', _ENTITY_FORMS_BEFORE)}")

    op.drop_index("ix_entity_source_links_trust_level", table_name="entity_source_links")
    op.drop_table("entity_source_links")

    op.drop_index("ix_dbi_unadjudicated", table_name="drake_business_identity")
    op.drop_index("ix_dbi_entity", table_name="drake_business_identity")
    op.drop_table("drake_business_identity")
