"""Drake returns: a stable, content-derived identity to replace the positional upsert key.

``drake_client_returns`` was keyed — and upserted — on ``UNIQUE (tax_year, source_row_number)``, where
``source_row_number`` is ``enumerate(csv.DictReader(...), start=1)``: the row's POSITION in the Drake
export. Position is not identity. A re-export that inserts, deletes or re-sorts one row shifts every
row after it, so row *N* becomes a different taxpayer and ``ON CONFLICT (tax_year, source_row_number)
DO UPDATE`` silently overwrites one client's return — AGI, filing status, acknowledgements and both
identifier hashes — with another client's. That is a live correctness hazard for any repeatable Drake
sync, and it is why no scheduled Drake import exists yet.

The real Drake exports were inspected for a Drake-native client or return identifier to key on
instead. ``CLIENT.CSV`` has none, in any year 2021-2025 — its only client key is ``TP_Social``. So the
identity is DERIVED, by ``app.services.drake_return_identity``:

    return_identity_key = SHA-256( tax_year | taxpayer_hash | spouse_hash | return_type | filing_status )

Every input is already a salted hash or a non-sensitive field, so this migration needs no secret and
introduces no new one; the SQL below reproduces the Python expression exactly. **No raw SSN or EIN is
read, written, or derivable from the result** — the identifier hashes are the existing
``SHA-256(KEY : digits)`` values the import already stores.

``filing_status`` is in the key for a non-obvious reason. Preparers compute an MFJ and an MFS version
of the same couple's return to compare them, producing two rows identical in year, both hashes and
``1040``, differing only in ``FS`` (2 vs 3). Three such pairs exist in production; without
``filing_status`` they collide and one overwrites the other.

FAIL CLOSED. Two populations cannot be given an identity, and this migration invents one for neither.
Measured against production at authoring time (3,690 rows):

  * 6 rows have no taxpayer identifier at all — 1041 estates/trusts filed without a ``TP_Social``,
    including two DIFFERENT Stone estates in 2021. Keying them on "no identifier" would merge
    unrelated estates. They get ``identity_status = 'unidentified_no_taxpayer_identifier'``.
  * 3 rows share one complete identity tuple — one taxpayer, 2021, three separate 1040s at FS=1 with
    different AGI. Which is "the" return is not knowable from the export, so none is chosen. They get
    ``identity_status = 'unidentified_ambiguous_collision'``.

3,681 rows are cleanly identified; 9 are quarantined. A quarantined row keeps every column it has and
stays fully visible to staff tooling — it is excluded from IDENTITY-keyed upsert and from person
resolution, not from the database.

THE OLD UNIQUE CONSTRAINT IS DROPPED, deliberately. Keeping ``UNIQUE (tax_year, source_row_number)``
would defeat the fix: when a re-export moves a return to a new position, the identity-keyed upsert
updates that return's ``source_row_number`` to its new value, which would collide with whichever row
currently sits at that position. ``source_row_number`` is RETAINED as provenance — it records where in
the export a row was found, which is genuinely useful for reconciliation — but it is never again an
identity or an upsert key.

Additive and data-preserving: no row is deleted, no existing column is altered or rewritten, and the
only values written are the two new columns. Fully reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "drake02"
down_revision = "mcp01"
branch_labels = None
depends_on = None

_OLD_POSITIONAL_CONSTRAINT = "drake_client_returns_tax_year_source_row_number_key"

#: Mirrors ``app.services.drake_return_identity.identity_payload`` exactly. Any change must change
#: both, and ``tests/test_drake_return_identity.py`` asserts the two agree.
_PAYLOAD_SQL = """
    concat_ws('|',
        tax_year::text,
        lower(btrim(taxpayer_identifier_hash)),
        CASE WHEN lower(btrim(coalesce(spouse_identifier_hash, ''))) ~ '^[0-9a-f]{64}$'
             THEN lower(btrim(spouse_identifier_hash))
             ELSE '' END,
        upper(btrim(coalesce(return_type, ''))),
        btrim(coalesce(filing_status, ''))
    )
"""

_VALID_TAXPAYER_HASH = "lower(btrim(coalesce(taxpayer_identifier_hash, ''))) ~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.add_column("drake_client_returns",
                  sa.Column("return_identity_key", sa.String(64), nullable=True))
    op.add_column("drake_client_returns",
                  sa.Column("identity_status", sa.Text(), nullable=False,
                            server_default="unidentified_no_taxpayer_identifier"))

    # 1. Every row that HAS a usable taxpayer identifier gets its derived key.
    op.execute(f"""
        UPDATE drake_client_returns
           SET return_identity_key =
                   encode(sha256(convert_to({_PAYLOAD_SQL}, 'UTF8')), 'hex'),
               identity_status = 'identified'
         WHERE {_VALID_TAXPAYER_HASH}
    """)

    # 2. Withdraw the key from any tuple claimed by more than one row. Both/all claimants are
    #    quarantined — picking one would be exactly the silent overwrite this migration exists to
    #    prevent.
    op.execute("""
        UPDATE drake_client_returns
           SET return_identity_key = NULL,
               identity_status = 'unidentified_ambiguous_collision'
         WHERE return_identity_key IN (
                   SELECT return_identity_key
                     FROM drake_client_returns
                    WHERE return_identity_key IS NOT NULL
                    GROUP BY return_identity_key
                   HAVING count(*) > 1)
    """)

    # 3. Only now can the uniqueness be enforced. Partial, so the quarantined rows coexist.
    op.create_index("uq_drake_client_returns_identity", "drake_client_returns",
                    ["return_identity_key"], unique=True,
                    postgresql_where=sa.text("return_identity_key IS NOT NULL"))
    op.create_index("ix_drake_client_returns_identity_status", "drake_client_returns",
                    ["identity_status"])

    op.create_check_constraint(
        "ck_drake_client_returns_identity_status", "drake_client_returns",
        "identity_status IN ('identified', 'unidentified_no_taxpayer_identifier', "
        "'unidentified_ambiguous_collision')")

    # An identified row must carry a key, and an unidentified one must not — so the status can never
    # drift away from the fact it describes.
    op.create_check_constraint(
        "ck_drake_client_returns_identity_coherent", "drake_client_returns",
        "(identity_status = 'identified') = (return_identity_key IS NOT NULL)")

    # 4. Retire the positional key. See the module docstring: leaving it in place would block the
    #    identity-keyed upsert from ever moving a return to a new export position.
    op.drop_constraint(_OLD_POSITIONAL_CONSTRAINT, "drake_client_returns", type_="unique")


def downgrade() -> None:
    # Restoring the positional constraint first: if the data has since drifted such that two rows
    # share a (tax_year, source_row_number), this fails loudly here rather than leaving the table in a
    # half-reverted state.
    op.create_unique_constraint(_OLD_POSITIONAL_CONSTRAINT, "drake_client_returns",
                                ["tax_year", "source_row_number"])
    op.drop_constraint("ck_drake_client_returns_identity_coherent", "drake_client_returns",
                       type_="check")
    op.drop_constraint("ck_drake_client_returns_identity_status", "drake_client_returns",
                       type_="check")
    op.drop_index("ix_drake_client_returns_identity_status", table_name="drake_client_returns")
    op.drop_index("uq_drake_client_returns_identity", table_name="drake_client_returns")
    op.drop_column("drake_client_returns", "identity_status")
    op.drop_column("drake_client_returns", "return_identity_key")
