"""person_source_links: explicit trust + confirmation provenance, without redefining ``confirmed``.

``person_source_links.confirmed`` is one boolean carrying seven different meanings. It is set to
``TRUE`` unconditionally by automated code — ``app/matching/promote.py::_link`` hardcodes it, and so
does ``scripts/link_drake_to_people.py`` — and the table records nothing about WHO decided or on WHAT
evidence. Measured against production: **all 3,607 Drake links carry ``confirmed = true``**, none is
unconfirmed, and 41.1% of them rest on name-derived evidence (1,404 ``unique_exact_name`` plus 79
``exact_name_city_state``). So ``confirmed`` cannot answer the question a tax-return read surface has
to ask — *did a human decide this, or did two strings match?*

This migration adds the fields that can answer it, and changes nothing that exists:

  ``trust_level``          the class of evidence behind the link (see ``app.services.link_trust``)
  ``confirmation_source``  ``human`` / ``machine`` / ``unknown`` — who decided
  ``evidence_method``      the concrete method, kept beside the classified level so the classification
                           can always be re-derived and audited rather than taken on faith
  ``confirmed_by_user_id`` the reviewer who approved it, when one did
  ``confirmed_at``         when that approval happened

``confirmed`` is NOT touched, NOT redefined and NOT rewritten. Every deployed reader that consults it
sees exactly what it saw before this migration.

NOTHING IS BACK-FILLED, ON PURPOSE. Every existing row keeps ``trust_level = NULL``, which
``app.services.link_trust`` reads as ``unknown_legacy``. Restating 11,124 historical links from method
strings — several written by scripts that are no longer in the source tree at all, including the 1,404
``unique_exact_name`` rows — would manufacture confidence that was never established. Legacy rows can
still be REPORTED on through ``derive_legacy_trust``, which is read-only and explicitly labelled as
derived rather than recorded. Reclassification, if it ever happens, is its own reviewed migration with
its own evidence.

The immediate consequence is deliberate and must be understood before anything is wired to it: with no
recorded trust anywhere, a strict reading of these columns trusts NOTHING. The Drake resolver
therefore takes an explicit policy argument rather than defaulting to a behaviour; see
``app.services.drake_return_resolution``.

Additive, data-preserving, fully reversible. No row is written by this migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "psl02"
down_revision = "drake02"
branch_labels = None
depends_on = None

_TRUST_LEVELS = (
    "identifier_verified", "human_approved", "machine_exact_name", "machine_name_location",
    "machine_contact", "canonical_repair", "unknown_legacy",
)
_CONFIRMATION_SOURCES = ("human", "machine", "unknown")


def _in_list(column: str, values) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("person_source_links", sa.Column("trust_level", sa.Text(), nullable=True))
    op.add_column("person_source_links", sa.Column("confirmation_source", sa.Text(), nullable=True))
    op.add_column("person_source_links", sa.Column("evidence_method", sa.Text(), nullable=True))
    op.add_column("person_source_links",
                  sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True))
    op.add_column("person_source_links",
                  sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key("fk_person_source_links_confirmed_by_user", "person_source_links",
                          "users", ["confirmed_by_user_id"], ["id"])

    # NULL stays legal — that is precisely what "never recorded" looks like, and every existing row is
    # in that state. The constraints only police values that ARE written.
    op.create_check_constraint(
        "ck_person_source_links_trust_level", "person_source_links",
        f"trust_level IS NULL OR {_in_list('trust_level', _TRUST_LEVELS)}")
    op.create_check_constraint(
        "ck_person_source_links_confirmation_source", "person_source_links",
        f"confirmation_source IS NULL OR "
        f"{_in_list('confirmation_source', _CONFIRMATION_SOURCES)}")

    # A human approval must say WHICH human and WHEN. This is the structural difference between the
    # new semantics and the old boolean: 'human_approved' can no longer be asserted anonymously.
    op.create_check_constraint(
        "ck_person_source_links_human_approval_attributed", "person_source_links",
        "trust_level IS DISTINCT FROM 'human_approved' "
        "OR (confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)")

    op.create_index("ix_person_source_links_trust_level", "person_source_links", ["trust_level"])


def downgrade() -> None:
    op.drop_index("ix_person_source_links_trust_level", table_name="person_source_links")
    op.drop_constraint("ck_person_source_links_human_approval_attributed", "person_source_links",
                       type_="check")
    op.drop_constraint("ck_person_source_links_confirmation_source", "person_source_links",
                       type_="check")
    op.drop_constraint("ck_person_source_links_trust_level", "person_source_links", type_="check")
    op.drop_constraint("fk_person_source_links_confirmed_by_user", "person_source_links",
                       type_="foreignkey")
    op.drop_column("person_source_links", "confirmed_at")
    op.drop_column("person_source_links", "confirmed_by_user_id")
    op.drop_column("person_source_links", "evidence_method")
    op.drop_column("person_source_links", "confirmation_source")
    op.drop_column("person_source_links", "trust_level")
