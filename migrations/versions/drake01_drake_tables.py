"""Drake tables — source-controlled schema for the Drake tax + identity-review integration.

Creates the four ``drake_*`` tables the Drake integration reads:
  * ``drake_client_returns``            — imported Drake return-status rows (client tax returns)
  * ``drake_efile_records``             — imported Drake e-file acknowledgement rows
  * ``drake_identity``                  — one canonical Drake identity per identifier hash
  * ``drake_identity_match_candidates`` — ranked person-match candidates for identity review

Until now these tables existed only where the import/build scripts had been run
(``scripts/import_drake_2025.py``, ``scripts/build_drake_identity.py``,
``scripts/build_drake_identity_review.py``), so the schema was not under source control. This migration
brings the exact production DDL into Alembic so the tables provision consistently in every environment.

IDEMPOTENT ON PURPOSE: production already has these tables (created out-of-band by the scripts, which use
``CREATE TABLE IF NOT EXISTS``). A plain ``op.create_table`` would fail there with "relation already
exists", so this migration issues the identical ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT
EXISTS`` DDL — a no-op where the tables are present, a create where they are not. The DDL mirrors the
scripts verbatim so the schema is identical whichever path created it.

The application code that reads these tables is deployment-order tolerant (``to_regclass`` guards), so it
degrades to empty rather than erroring in an environment where this migration has not yet run.

Downgrade drops the four tables. On production this would delete imported Drake data (3690 client-return
rows at capture time), so it is intended only for a deliberate rollback on a non-production database.

Revision ID: drake01
Revises: advfw01
Create Date: 2026-08-04
"""
from alembic import op

revision = "drake01"
down_revision = "advfw01"
branch_labels = None
depends_on = None


DDL = """
CREATE TABLE IF NOT EXISTS drake_client_returns (
    id BIGSERIAL PRIMARY KEY,
    tax_year INTEGER NOT NULL,
    source_row_number INTEGER NOT NULL,

    taxpayer_identifier_hash VARCHAR(64),
    spouse_identifier_hash VARCHAR(64),

    taxpayer_first_name TEXT,
    taxpayer_last_name TEXT,
    taxpayer_normalized_name TEXT,
    taxpayer_dob DATE,

    spouse_first_name TEXT,
    spouse_last_name TEXT,
    spouse_normalized_name TEXT,
    spouse_dob DATE,

    filing_status TEXT,
    return_type TEXT,
    preparer_code TEXT,

    agi NUMERIC,
    preparer_fee NUMERIC,

    prepare_date DATE,
    review_date DATE,
    approved_date DATE,
    complete_date DATE,

    federal_product TEXT,
    federal_ack_date DATE,
    federal_ack_code TEXT,

    state_product TEXT,
    state_ack_date DATE,
    state_ack_code TEXT,

    source_updated_at TIMESTAMPTZ NOT NULL,
    raw_data JSONB NOT NULL,

    UNIQUE (tax_year, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_drake_client_returns_taxpayer_hash
    ON drake_client_returns (taxpayer_identifier_hash);

CREATE INDEX IF NOT EXISTS ix_drake_client_returns_taxpayer_name
    ON drake_client_returns (taxpayer_normalized_name);

CREATE TABLE IF NOT EXISTS drake_efile_records (
    id BIGSERIAL PRIMARY KEY,
    tax_year INTEGER NOT NULL,
    source_row_number INTEGER NOT NULL,

    taxpayer_identifier_hash VARCHAR(64),
    spouse_identifier_hash VARCHAR(64),

    taxpayer_name TEXT,
    return_type TEXT,
    preparer_code TEXT,

    agi NUMERIC,
    refund_amount NUMERIC,
    balance_due NUMERIC,

    transmission_date DATE,
    acknowledgement_date DATE,
    acknowledgement_code TEXT,
    submission_id TEXT,

    source_updated_at TIMESTAMPTZ NOT NULL,
    raw_data JSONB NOT NULL,

    UNIQUE (tax_year, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_drake_efile_records_taxpayer_hash
    ON drake_efile_records (taxpayer_identifier_hash);

CREATE TABLE IF NOT EXISTS drake_identity (
    identifier_hash TEXT PRIMARY KEY,
    primary_person_id INTEGER,
    first_year INTEGER,
    last_year INTEGER,
    return_count INTEGER,
    taxpayer_name TEXT,
    spouse_name TEXT,
    confidence INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drake_identity_match_candidates (
    id BIGSERIAL PRIMARY KEY,
    identifier_hash TEXT NOT NULL,
    person_id BIGINT NOT NULL,
    score INTEGER NOT NULL,
    reasons JSONB NOT NULL,
    rank INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TIMESTAMPTZ,
    reviewed_by_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (identifier_hash, person_id)
);

CREATE INDEX IF NOT EXISTS ix_drake_identity_match_candidates_status
    ON drake_identity_match_candidates (status, score DESC);
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS drake_identity_match_candidates;"
        "DROP TABLE IF EXISTS drake_identity;"
        "DROP TABLE IF EXISTS drake_efile_records;"
        "DROP TABLE IF EXISTS drake_client_returns;"
    )
