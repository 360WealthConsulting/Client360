# Client360 — Database Foundation & Migration Standards (E1.3)

The database is PostgreSQL, managed by Alembic. This guide records the standards,
the validated state of the foundation, and the known risks. It preserves the
existing schema and migration history (ADR-013) — it does **not** redesign them.

## Layout
- **`app/database/schema.py`** — SQLAlchemy Core `MetaData` (`metadata`) plus
  `DATABASE_URL`. This is Alembic's `target_metadata`. It declares the
  **autogenerate-managed core** (currently 52 tables).
- **`app/db.py`** — creates the engine and **reflects the whole database** at
  import, exposing `Table` objects for every table (currently 151). Application
  code uses these reflected tables (SQLAlchemy Core; no ORM session layer).
- **`migrations/`** + **`alembic.ini`** — the authoritative migration history
  (single linear graph; one head).
- **`migrations/env.py`** — imports `metadata` + `DATABASE_URL` from
  `app.database.schema`; runs with `compare_type=True`.

## Validated state (E1.3)
| Invariant | Result |
|---|---|
| Single Alembic head | ✅ `d0l1n2o3i4k5` |
| Schema at head (`current == heads`) | ✅ |
| Full reversibility (base ↔ head, every downgrade) | ✅ `check_migrations_reversible.sh` |
| Every table has a primary key | ✅ 151/151 |
| Every declared table exists in the DB | ✅ 52/52 |

## Migration standards (mandatory)
1. **Never** renumber, rewrite, delete, or squash an **applied** migration, and
   never alter production history. Fix problems **forward-only** with a new
   migration.
2. **One head.** Rebase your migration onto the current head before merge; CI
   enforces a single head (`check_migration_heads.sh`).
3. **Every migration is reversible.** Provide a working `downgrade`; CI walks the
   whole graph down and back up (`check_migrations_reversible.sh`).
4. **Hand-write migrations for non-core domains.** See the autogenerate warning
   below. When you add a table to the autogenerate-managed core, add it to
   `app/database/schema.py` *and* write the migration.
5. Keep migrations data-safe: no destructive change without a backup and an
   explicit, reviewed decision.

> ⚠️ **Autogenerate is NOT safe against this database.** `target_metadata`
> (`app/database/schema.py`) is a **partial** declaration: it holds 52 of the
> 151 tables. The other 99 (tax_*, portal_*, benefit_*, insurance_*, workflow
> extras, exceptions, …) are created by **hand-written migrations** and reached
> via reflection. Running `alembic revision --autogenerate` would therefore emit
> **destructive `drop_table` operations** for those 99 tables. Write migrations
> by hand, or extend `schema.py` first and review the diff line by line.

## Consistency checks
| Check | What it proves | Safe against |
|---|---|---|
| `scripts/check_migration_heads.sh` | exactly one head | any (read-only) |
| `scripts/check_schema_at_head.sh` | `current == head` | any (read-only) |
| `scripts/check_migrations_reversible.sh` | every downgrade works | **disposable** DB only |
| `scripts/check_schema_consistency.py` | single head · declared⊆DB · every table has a PK | any (read-only) |
| `tests/test_e1_3_database_foundation.py` | the same invariants, in the suite (runs in CI) | test DB |

Run locally:
```bash
DATABASE_URL=postgresql://localhost/client360_test python scripts/check_schema_consistency.py
scripts/check_migrations_reversible.sh   # disposable DB
```

## Developer database workflow
See [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md). In short:
```bash
scripts/dev.sh setup     # create dev DB if missing + migrate to head
scripts/dev.sh migrate   # apply new migrations (forward-only)
scripts/dev.sh doctor    # includes a schema-at-head check
scripts/test.sh run      # reset the disposable test DB + full suite
```
Creating a migration:
```bash
# Hand-write (recommended for non-core domains):
python -m alembic revision -m "short description"   # then fill upgrade()/downgrade()
python -m alembic upgrade head
# Verify before commit:
python -m alembic heads            # must show ONE head
python scripts/check_schema_consistency.py
```

## Connection & startup
- `create_engine(DATABASE_URL)` (SQLAlchemy Core, default QueuePool). No custom
  pool sizing or connection-retry layer today — see Known risks.
- `app/db.py` **reflects at import**, so a migrated, reachable database must
  exist before the application (or anything importing `app.db`) starts. Startup
  raises a clear error if `DATABASE_URL` is unset. Configuration is
  environment-aware via `app/config.py` (`validate_startup_configuration`).

## Known risks & technical debt (documented; forward-only candidates)
- **Partial `target_metadata` (52/151).** Autogenerate is unsafe (see warning).
  *Forward-only candidate:* incrementally declare the remaining tables in
  `schema.py` (or adopt reflection-as-target) so autogenerate becomes usable.
  Not done in E1.3 (large, and the hand-written workflow is safe today).
- **`alembic check` is unusable** here for two reasons: (a) 60 `json`-typed
  columns trip its type comparison (`SELECT '{}'::json = '{}'` — `json` has no
  `=` operator in PostgreSQL), and (b) the partial metadata would report drops.
  Do **not** wire `alembic check` into CI. *Forward-only candidates:* migrate
  `json` → `jsonb` where appropriate; resolve the partial-metadata gap.
- **No connection-retry / pool tuning.** Defaults are used. Adequate for current
  scale; revisit if reliability/scale requires it (would be an ADR-tracked change).

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Multiple head revisions are present` | Rebase your migration onto head; keep one head |
| `DATABASE_URL is missing` | Set it in `app/.env` (see `config/.env.example`) |
| Autogenerate wants to drop many tables | Expected — target_metadata is partial; hand-write the migration |
| Downgrade fails in CI | Your migration's `downgrade` is broken/missing — fix it |
| Schema not at head | `scripts/dev.sh migrate` |
