# Client360 — Database Foundation & Migration Standards (E1.3)

The database is PostgreSQL, managed by Alembic. This guide records the standards,
the validated state of the foundation, and the known risks. It preserves the
existing schema and migration history (ADR-013) — it does **not** redesign them.

## Layout
- **`app/database/schema.py`** — SQLAlchemy Core `MetaData` (`metadata`) plus `DATABASE_URL`. It is the
  app's **declarative core** (imported by `app/platform/outbox.py` and tests) and supplies `DATABASE_URL`
  to Alembic — but it is a **partial** metadata (~245 of ~381 live tables, some columns stale) and is
  **NOT** used as Alembic's `target_metadata` (that would be a destructive-autogenerate trap — Phase 0A).
- **`app/db.py`** — creates the engine and **reflects the whole database** at
  import, exposing `Table` objects for every table (currently 151). Application
  code uses these reflected tables (SQLAlchemy Core; no ORM session layer).
- **`migrations/`** + **`alembic.ini`** — the authoritative migration history
  (single linear graph; one head).
- **`migrations/env.py`** — imports `DATABASE_URL` from `app.database.schema` (connection URL only).
  Alembic has **no `target_metadata`**, and `revision --autogenerate` is **rejected/fails closed** (see
  the warning below). It deliberately does NOT use schema.py's partial `metadata`. `alembic upgrade`/
  `downgrade`/`current`/`heads` are unaffected.

## Validated state (E1.3)
| Invariant | Result |
|---|---|
| Single Alembic head | ✅ `d0l1n2o3i4k5` |
| Schema at head (`current == heads`) | ✅ |
| STRUCTURAL reversibility (base ↔ head DDL runs; NOT data-preserving) | ✅ `check_migrations_reversible.sh` — see **Rollback (data-safe)** below |
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
4. **Hand-write every migration.** Autogenerate is disabled (see the warning below); write
   `upgrade()`/`downgrade()` by hand. Updating `app/database/schema.py` is optional (it is the app's
   declarative core, not an Alembic target) — do it only if app code/tests need the new `Table` object.
5. Keep migrations data-safe: no destructive change without a backup and an
   explicit, reviewed decision.

> ⛔ **Autogenerate is DISABLED and fails closed (Phase 0A).** `migrations/env.py` rejects
> `alembic revision --autogenerate` — it terminates with a clear error **before any revision file is
> created** and does not fall back to schema.py. This is deliberate: `app/database/schema.py` is a
> partial, stale metadata (~245 of the ~381 live tables; stale columns such as `documents.person_id`),
> so autogenerating against it would emit destructive `drop_table` / `drop_column` / NOT-NULL-reversion
> operations. Client360 migrations are **hand-authored**:
> ```bash
> alembic revision -m "<short description>"   # then write upgrade()/downgrade() by hand
> ```
> `schema.py` remains the app's declarative core (imported by `app/platform/outbox.py` and tests) but is
> **not** an Alembic target. `tests/test_alembic_autogenerate_guard.py` fails if autogenerate ever
> succeeds, writes a file, or if env.py points back at schema.py's metadata.

## Consistency checks
| Check | What it proves | Safe against |
|---|---|---|
| `scripts/check_migration_heads.sh` | exactly one head | any (read-only) |
| `scripts/check_schema_at_head.sh` | `current == head` | any (read-only) |
| `scripts/check_migrations_reversible.sh` | every downgrade works | **disposable** DB only |
| `scripts/check_schema_consistency.py` | single head · declared⊆DB · every table has a PK | any (read-only) |
| `tests/test_e1_3_database_foundation.py` | the same invariants, in the suite (runs in CI) | test DB |
| `tests/test_alembic_autogenerate_guard.py` | `--autogenerate` is rejected (no revision file); normal ops work; env never targets stale schema.py | test DB |

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

## Rollback (data-safe)

⚠️ **Alembic reversibility is STRUCTURAL, not data-preserving.** `check_migrations_reversible.sh` proves
every `downgrade`'s DDL runs on an empty schema — it does **not** test downgrade-with-data. Several
downgrades `DELETE` from append-only tables (`audit_events`, `exception_events`), and every `drop_table`/
`drop_column` in a downgrade destroys the data it held. **So a rollback can silently lose data.**

Rollback is therefore gated behind a **verified backup** (`app/deploy/rollback.py`, wrapped by
`scripts/rollback.sh`). It refuses to downgrade unless a `pg_dump` custom-format backup has been created
**and** verified with `pg_restore --list`, and the operator has confirmed:

```bash
# 1. Plan only — prints current/target + where the backup will go; makes NO change:
scripts/rollback.sh --to <revision> --dry-run
#    (or: python -m app.deploy rollback --to <revision> --dry-run)

# 2. Execute — takes+verifies a backup, prints its location, prompts, then downgrades:
CLIENT360_BACKUP_DIR=/durable/backups scripts/rollback.sh --to <revision>
#    add --yes to skip the interactive prompt (a verified backup is STILL taken)
```

Gate order (any refusal/failure before the downgrade leaves the DB untouched):
**connectivity → confirmation → create backup → verify backup → print backup location → downgrade → verify head.**
- **Fail closed:** if backup creation or verification fails, the downgrade never runs.
- **Backup location** is printed before any downgrade begins; the backup is retained afterward.
- Set `CLIENT360_BACKUP_DIR` to durable storage (default `<repo>/backups`). An operator backup hook can be
  supplied via `CLIENT360_BACKUP_CMD` (it receives the target path in `CLIENT360_BACKUP_FILE` and must write
  a `pg_restore`-readable dump there; it is verified the same way).
- Forward migration (`app.deploy.migrate` / `scripts/dev.sh migrate`, upgrade-only) is **unchanged**.

### Manual restore (auto-restore is intentionally NOT done)
Automatic restore-on-failed-downgrade is **deliberately not implemented**: restoring means dropping and
recreating the live database from the dump — a destructive, environment-sensitive operation (e.g. encrypted
Microsoft token caches need the original `MICROSOFT_TOKEN_KEY`, or accounts must reconnect) that must be a
deliberate operator decision, not an automatic reaction to a partial failure. On a failed downgrade the
tool prints the manual procedure:

```bash
# Restore into a SCRATCH database first and verify (never straight over the live DB):
scripts/restore_rehearsal.sh <backup.dump> client360_restore_rehearsal
# Then, once verified, restore into the target (operator decision):
dropdb <db> && createdb <db>
pg_restore --no-owner --dbname=<db> <backup.dump>
```

## Connection & startup
- `create_engine(DATABASE_URL)` (SQLAlchemy Core, default QueuePool). No custom
  pool sizing or connection-retry layer today — see Known risks.
- `app/db.py` **reflects at import**, so a migrated, reachable database must
  exist before the application (or anything importing `app.db`) starts. Startup
  raises a clear error if `DATABASE_URL` is unset. Configuration is
  environment-aware via `app/config.py` (`validate_startup_configuration`).

## Known risks & technical debt (documented; forward-only candidates)
- **Partial `target_metadata` — NEUTRALIZED (Phase 0A).** The destructive-autogenerate trap is closed:
  `migrations/env.py` has no target metadata and **rejects `--autogenerate`** outright, so it can never
  drop the hand-authored tables from the stale `schema.py`. `schema.py` staleness is now cosmetic **for
  Alembic** (it remains the app's declarative core). Guarded by
  `tests/test_alembic_autogenerate_guard.py`. *Forward-only candidate (unchanged):* converge or retire
  `schema.py` as a declarative artifact.
- **`alembic check` is still unusable** here: its 60 `json`-typed columns trip type comparison
  (`SELECT '{}'::json = '{}'` — `json` has no `=` operator in PostgreSQL) and there is no target
  metadata. Do **not** wire `alembic check` into CI.
- **No connection-retry / pool tuning.** Defaults are used. Adequate for current
  scale; revisit if reliability/scale requires it (would be an ADR-tracked change).

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Multiple head revisions are present` | Rebase your migration onto head; keep one head |
| `DATABASE_URL is missing` | Set it in `app/.env` (see `config/.env.example`) |
| `alembic revision --autogenerate` fails "autogenerate is DISABLED" | Expected (Phase 0A) — author the migration by hand: `alembic revision -m "..."` |
| Autogenerate ever succeeds or proposes dropping tables/columns | The trap is back — `migrations/env.py` must reject `--autogenerate` and must not target schema.py `metadata` |
| Downgrade fails in CI | Your migration's `downgrade` is broken/missing — fix it |
| Schema not at head | `scripts/dev.sh migrate` |
