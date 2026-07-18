# Client360 — Audit Hash-chain Integrity (F3.2 / Epic 3)

Tamper-evident cryptographic integrity for the append-only `audit_events` log
(F3.1), implemented in-place per **ADR-015 Option A**. Each new record links to the
previous one in its chain via a SHA-256 hash, so content tampering or broken
linkage is evident on verification. **Evidence store, auditor export, external
anchoring, SIEM, and ledger integrations are out of scope** (deferred).

`app/security/audit_chain.py`

## Schema (additive — migration `f2h3c4a5i6n7`)
Four **nullable** columns on `audit_events` (existing rows untouched → remain valid
but **unchained**; no backfill):
| Column | Meaning |
|---|---|
| `prev_hash` | The previous entry's `entry_hash` in this chain (or genesis for the first) |
| `entry_hash` | This entry's hash (see contract) |
| `hash_version` | Serialization/algorithm version (currently 1) |
| `chain_id` | Chain stream identifier (default `"default"`) |
Indexes: `ix_audit_events_chain (chain_id, id)`; partial unique `uq_audit_events_entry_hash (entry_hash) WHERE entry_hash IS NOT NULL`.

The migration uses **idempotent DDL** (`ADD COLUMN`/`CREATE INDEX IF NOT EXISTS`,
`DROP … IF EXISTS`), so it is safe/reversible regardless of build path. The columns
are intentionally **not** declared in `identity_tables.py` (that table is created
from the declared metadata by migration `c410f4a1b2c3`, so declaring them would
pre-create the columns); `app.db` reflects the live schema at runtime.

## Serialization contract (v1) — defined once
The hashed content is a JSON object of the record's **application fields** with
sorted keys and compact separators; the entry hash binds version, chain, previous
hash, and that content:
```
content_json = json.dumps(
    {actor_user_id, action, entity_type, entity_id, ip_address,
     metadata, outcome, request_id, user_agent},
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
entry_hash = sha256("v{hash_version}|{chain_id}|{prev_hash}|" + content_json).hexdigest()
```
- `metadata` is the **redacted** metadata that is stored (via `redact_metadata`), so
  verification recomputes from stored values.
- `id` and `occurred_at` (DB-generated) are **excluded** — so the existing write
  behavior is unchanged and the hash is computable before insert.
- The single implementation lives in `audit_chain.canonical_serialization` /
  `compute_entry_hash`; there is no second serializer.

## Write path (unchanged surface)
`audit.write_audit_event(...)` — still the sole INSERT path — now, within its
existing transaction: takes a per-chain **advisory lock** (`pg_advisory_xact_lock`)
so writers cannot fork the chain, reads the chain tail's `entry_hash` (or genesis),
computes `entry_hash`, and inserts it with the record. Signature gains one optional
`chain_id` kwarg (default `"default"`) — fully backward compatible. F2.5's
`DbAuditSink` is unaffected (it delegates to this path).

## Genesis
The first chained record in a chain uses `prev_hash = GENESIS_PREV_HASH` (64
zeros); it is the documented genesis of that chain from deployment forward. Legacy
pre-migration rows have NULL hash columns and are excluded from verification
(valid but unchained).

## Integrity verification (internal service — no public API)
`audit_chain.IntegrityVerifier` / `verify_chain(chain_id, from_id=None)`:
- validates an entire chain from **genesis**, or from an **arbitrary checkpoint**
  (`from_id`, whose stored hash is trusted as the anchor);
- recomputes each entry's hash and checks linkage;
- returns a deterministic `VerifyResult(ok, chain_id, checked, first_failure_id,
  reason)` identifying the **first** integrity failure.

Because `audit_events` is append-only (F3.1), a committed record cannot be updated
in place; verification detects any forged/appended record or broken linkage.

## Guarantees / compatibility
- **Deterministic** hashing (same content ⇒ same hash across executions).
- **Append-only preserved** (F3.1 trigger unchanged); **F2.5 preserved**.
- **Additive, reversible** migration; existing rows and behavior unchanged; **no API
  behavior change** (no new endpoints; optional kwarg only).

## Deferred work (Epic 3, not implemented here)
- **F3.3** Evidence write-once store.
- **F3.4** Auditor read/export.
- Future external head-hash anchoring; future immutable-storage integration; future
  SIEM integration.

## References
ADR-015 (Option A), ADR-013, `docs/AUDIT_LOG.md` (F3.1), `docs/DATABASE.md`
(migration standard), Engineering Constitution §§6, 9.
