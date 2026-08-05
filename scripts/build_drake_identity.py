from dotenv import load_dotenv

load_dotenv(r"C:\Client360\app\.env")

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

print("=" * 70)
print("BUILDING DRAKE IDENTITIES")
print("=" * 70)

with engine.begin() as conn:

    conn.execute(text("""

        CREATE TABLE IF NOT EXISTS drake_identity (

            identifier_hash text PRIMARY KEY,

            primary_person_id integer,

            first_year integer,

            last_year integer,

            return_count integer,

            taxpayer_name text,

            spouse_name text,

            confidence integer,

            created_at timestamptz default now()

        )

    """))

    conn.execute(text("DELETE FROM drake_identity"))

    conn.execute(text("""

        INSERT INTO drake_identity (

            identifier_hash,

            first_year,

            last_year,

            return_count,

            taxpayer_name,

            spouse_name,

            confidence

        )

        SELECT

            raw_data->>'identifier_hash',

            MIN((raw_data->>'tax_year')::integer),

            MAX((raw_data->>'tax_year')::integer),

            COUNT(*),

            MAX(
                CASE
                    WHEN raw_data->>'role'='taxpayer'
                    THEN full_name
                END
            ),

            MAX(
                CASE
                    WHEN raw_data->>'role'='spouse'
                    THEN full_name
                END
            ),

            100

        FROM source_contacts

        WHERE source_system='Drake'

          AND raw_data->>'identifier_hash' IS NOT NULL

        GROUP BY raw_data->>'identifier_hash'

    """))

    rows = conn.execute(text("""

        SELECT COUNT(*)

        FROM drake_identity

    """)).scalar()

print()

print("Drake identities built:", rows)

print()

print("Finished.")