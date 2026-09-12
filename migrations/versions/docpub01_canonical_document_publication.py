"""Canonical document publication — an explicit grant from a ``documents`` row to a client audience.

WHY. The client portal reads ``vault_documents`` and nothing else (``app/portal/vault_documents.py``:
"There is NO fallback to the canonical table — a vault miss fails closed"). Every ingested document —
Drake, TaxDome, SharePoint — lands in the canonical ``documents`` table instead, and no code path
moves one into the vault. The two stores share no rows, so a correctly owned client document is
structurally unreachable by its own client no matter how it is owned, classified or filed.

The obvious fix — copy the file into the vault — is the wrong one. It duplicates every byte, forks
the OCR/classification/version pipeline that only the canonical row has, and creates a second row
whose ownership can drift from the first. ADR-072 exists precisely to stop that: ONE canonical
document, many references.

WHAT THIS ADDS. A publication is a REFERENCE, not a copy: a row saying "canonical document D is
published to audience A, with this visibility decision, made by this person, at this time, and here
is how to withdraw it." No bytes are copied. No canonical row is created, modified or deleted. The
existing vault store is untouched and keeps working exactly as it does today.

OWNERSHIP IS NOT VISIBILITY. ``documents.person_id`` says who a file belongs to; it has never said
who may READ it, and it cannot, because the canonical resolver deduplicates by content hash and fills
only NULL ownership — so one row can legitimately be the same file for two unrelated clients while
carrying a single owner. Deriving client access from that single owner would hand one client another
client's document the first time a hash collided. Access therefore derives ONLY from a publication
row, and publishing the same canonical document to two audiences creates two independent rows.

AUDIENCE. Person, household and organization are separate audience types with separate anchors,
enforced by a CHECK so a row can never be ambiguous about who it addresses. Organization reuses
``relationship_entities``, which is where ``documents.organization_id`` already points.

LIFECYCLE. A publication is never hard-deleted by the application. ``revoked_at`` withdraws it,
``archived_at`` retires it, and ``client_visible`` is the standing decision — all three are read as
exclusions by the portal, so withdrawal is a single-column write with no cascade.

AUDIT. ``document_publication_events`` is an append-only ledger of publish / revoke / archive /
visibility changes. Its ``publication_id`` is ON DELETE SET NULL and its ``document_id`` carries no
foreign key, so deleting a document (which cascades its publications away) leaves the history intact
rather than erasing the evidence that the document was once client-visible.

REVERSIBLE. The downgrade refuses while any LIVE publication exists rather than silently revoking
client access as a side effect of a schema rollback. Revoke first, then roll back — see the rollback
procedure in the pull request.

Single Alembic head preserved.
"""
import sqlalchemy as sa
from alembic import op

revision = "docpub01"
down_revision = "docpipe01"
branch_labels = None
depends_on = None

AUDIENCE_TYPES = ("person", "household", "organization")

#: Where a visibility decision came from. ``policy_preview`` is reserved for a decision a human
#: accepted FROM the read-only preview; the preview itself never writes.
DECISION_SOURCES = ("staff_manual", "policy_preview", "import_rule", "migration_backfill")

PUBLICATION_ACTIONS = (
    "published", "revoked", "restored", "archived",
    "visibility_granted", "visibility_withdrawn",
)

#: Exactly one anchor, and it must be the one the audience_type names. Without this a row could
#: claim audience_type='person' while carrying only a household_id, and the portal's person query
#: and household query would disagree about who the document was published to.
_ANCHOR_CHECK = (
    "(audience_type = 'person' AND person_id IS NOT NULL "
    "     AND household_id IS NULL AND organization_id IS NULL) OR "
    "(audience_type = 'household' AND household_id IS NOT NULL "
    "     AND person_id IS NULL AND organization_id IS NULL) OR "
    "(audience_type = 'organization' AND organization_id IS NOT NULL "
    "     AND person_id IS NULL AND household_id IS NULL)"
)

#: "Live" is the predicate the portal and the uniqueness rules share: not revoked, not archived.
#: Revoking a publication therefore frees the audience to be published again later.
_LIVE = "revoked_at IS NULL AND archived_at IS NULL"


def upgrade():
    op.create_table(
        "document_publications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audience_type", sa.Text, nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="CASCADE")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="CASCADE")),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE")),
        # The standing decision. Default false so a row created without an explicit decision
        # publishes nothing — absence of a decision is never read as permission.
        sa.Column("client_visible", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("decision_source", sa.Text, nullable=False),
        # The type and year AS DECIDED, kept on the publication rather than read live from the
        # document: a reclassification must not silently change what a client was granted.
        sa.Column("document_type", sa.Text),
        sa.Column("tax_year", sa.SmallInteger),
        sa.Column("note", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "audience_type IN (" + ",".join(f"'{a}'" for a in AUDIENCE_TYPES) + ")",
            name="ck_document_publications_audience_type"),
        sa.CheckConstraint(
            "decision_source IN (" + ",".join(f"'{d}'" for d in DECISION_SOURCES) + ")",
            name="ck_document_publications_decision_source"),
        sa.CheckConstraint(_ANCHOR_CHECK, name="ck_document_publications_audience_anchor"),
        sa.CheckConstraint("tax_year IS NULL OR (tax_year >= 1990 AND tax_year <= 2100)",
                           name="ck_document_publications_tax_year"),
    )
    op.create_index("ix_document_publications_document_id", "document_publications", ["document_id"])
    op.create_index("ix_document_publications_person_id", "document_publications", ["person_id"])
    op.create_index("ix_document_publications_household_id", "document_publications", ["household_id"])
    op.create_index("ix_document_publications_organization_id", "document_publications",
                    ["organization_id"])

    # One LIVE publication per (document, audience). A revoked or archived row does not occupy the
    # slot, so re-publishing after a withdrawal is a fresh, separately audited decision rather than
    # a silent resurrection of the old one.
    for anchor in ("person", "household", "organization"):
        op.create_index(
            f"uq_document_publications_live_{anchor}",
            "document_publications", ["document_id", f"{anchor}_id"], unique=True,
            postgresql_where=sa.text(f"{anchor}_id IS NOT NULL AND {_LIVE}"))

    # The portal's read path: live + visible, by audience anchor.
    for anchor in ("person", "household", "organization"):
        op.create_index(
            f"ix_document_publications_visible_{anchor}",
            "document_publications", [f"{anchor}_id"],
            postgresql_where=sa.text(f"client_visible AND {anchor}_id IS NOT NULL AND {_LIVE}"))

    op.create_table(
        "document_publication_events",
        sa.Column("id", sa.Integer, primary_key=True),
        # SET NULL, not CASCADE: the ledger outlives the publication it describes.
        sa.Column("publication_id", sa.Integer,
                  sa.ForeignKey("document_publications.id", ondelete="SET NULL")),
        # Deliberately NOT a foreign key — a deleted document must not erase the record that it was
        # published, which is exactly the fact an auditor would be looking for.
        sa.Column("document_id", sa.Integer, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("audience_type", sa.Text),
        sa.Column("audience_id", sa.Integer),
        sa.Column("client_visible", sa.Boolean),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("ip_address", sa.Text),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "action IN (" + ",".join(f"'{a}'" for a in PUBLICATION_ACTIONS) + ")",
            name="ck_document_publication_events_action"),
    )
    op.create_index("ix_document_publication_events_publication_id",
                    "document_publication_events", ["publication_id"])
    op.create_index("ix_document_publication_events_document_id",
                    "document_publication_events", ["document_id"])


def downgrade():
    # Refuse while any LIVE publication exists. Dropping the table would withdraw client access to
    # every published document as an invisible side effect of a schema rollback, and the audit
    # ledger would go with it. Revoke the publications first — that is an auditable decision — and
    # the rollback then proceeds.
    bind = op.get_bind()
    live = bind.execute(sa.text(
        "SELECT count(*) FROM document_publications "
        f"WHERE client_visible AND {_LIVE}")).scalar_one()
    if live:
        raise RuntimeError(
            f"Refusing to downgrade: {live} live client-visible publication(s) exist. "
            "Revoke them first (POST /api/publications/{id}/revoke, or "
            "app.services.publication.service.revoke) so the withdrawal is audited, then retry.")

    op.drop_index("ix_document_publication_events_document_id",
                  table_name="document_publication_events")
    op.drop_index("ix_document_publication_events_publication_id",
                  table_name="document_publication_events")
    op.drop_table("document_publication_events")

    for anchor in ("person", "household", "organization"):
        op.drop_index(f"ix_document_publications_visible_{anchor}", table_name="document_publications")
        op.drop_index(f"uq_document_publications_live_{anchor}", table_name="document_publications")
    op.drop_index("ix_document_publications_organization_id", table_name="document_publications")
    op.drop_index("ix_document_publications_household_id", table_name="document_publications")
    op.drop_index("ix_document_publications_person_id", table_name="document_publications")
    op.drop_index("ix_document_publications_document_id", table_name="document_publications")
    op.drop_table("document_publications")
