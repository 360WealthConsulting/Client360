#!/usr/bin/env python3
"""Schema-consistency check (E1.3).

READ-ONLY. Validates the database foundation invariants without modifying
anything, so it is safe against any database:

  1. The migration graph has exactly one head.
  2. Every table DECLARED in app.database.schema.metadata (Alembic's
     target_metadata) exists in the connected database. A declared table that
     is missing from the DB means the schema and the migrations have diverged.
  3. Every table in the connected database has a primary key.

It deliberately does NOT require the declared metadata to be *equal* to the
database: app/database/schema.py is a PARTIAL target_metadata (it declares the
autogenerate-managed core), while many later domains are created by hand-written
migrations and reached via reflection in app/db.py. Equality is therefore not an
invariant — and running `alembic revision --autogenerate` against this partial
metadata would emit destructive drops. See docs/DATABASE.md.

Usage:
    DATABASE_URL=postgresql://localhost/<db> python scripts/check_schema_consistency.py
Exit code 0 on success, 1 on any violation.
"""
from __future__ import annotations

import os
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine


def main() -> int:
    problems: list[str] = []

    # 1. Single head.
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    if len(heads) == 1:
        print(f"OK: exactly one Alembic head ({heads[0]}).")
    else:
        problems.append(f"expected exactly one head, found {len(heads)}: {sorted(heads)}")

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("SKIP: DATABASE_URL not set; head check only.", file=sys.stderr)
        return 1 if problems else 0

    engine = create_engine(url)
    try:
        from app.database.schema import metadata as declared

        reflected = MetaData()
        reflected.reflect(bind=engine)
    finally:
        engine.dispose()

    declared_tables = set(declared.tables)
    reflected_tables = set(reflected.tables) - {"alembic_version"}

    # 2. Declared tables must all exist in the database.
    missing = sorted(declared_tables - reflected_tables)
    if missing:
        problems.append(f"declared tables missing from the database: {missing}")
    else:
        print(f"OK: all {len(declared_tables)} declared tables exist in the database.")

    # Informational: the partial-metadata gap is expected, not a failure.
    hand_written = len(reflected_tables - declared_tables)
    print(
        f"INFO: {len(reflected_tables)} tables in DB; {len(declared_tables)} declared in "
        f"target_metadata; {hand_written} created by hand-written migrations "
        f"(autogenerate is NOT safe — see docs/DATABASE.md)."
    )

    # 3. Every table has a primary key.
    no_pk = sorted(
        name for name, table in reflected.tables.items()
        if name != "alembic_version" and not table.primary_key.columns
    )
    if no_pk:
        problems.append(f"tables without a primary key: {no_pk}")
    else:
        print(f"OK: all {len(reflected_tables)} tables have a primary key.")

    if problems:
        print("\nSCHEMA CONSISTENCY FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("Schema consistency: OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
