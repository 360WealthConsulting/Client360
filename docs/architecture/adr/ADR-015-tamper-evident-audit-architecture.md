# ADR-015 — Tamper-Evident Audit Architecture

- **Status:** Accepted
- **Date:** 2026-07-18
- **Relates to:** [ADR-013](ADR-013-repository-reconciliation.md) (in-place
  reconciliation), the founding security/evidence-first decisions (ADR-001…012),
  and Epic 3 (Audit & Evidence: F3.1–F3.4).

## Context
The append-only audit log (F3.1) already prevents in-place mutation via a Postgres
`BEFORE UPDATE OR DELETE` trigger. Append-only, however, is not by itself
**tamper-evident**: a sufficiently privileged actor who can `INSERT` (or who
restores/rewrites storage out of band) could append fabricated records or splice a
chain, and nothing in the log would reveal it on read. Epic 3 requires that any
such tampering be **detectable** — that an auditor can verify, cryptographically,
that the log has not been altered or forged since it was written.

We need this without abandoning ADR-013: reconcile **in place**, additively,
preserving the existing write surface and existing rows, and adding no external
runtime dependency the platform does not already run.

## Decision
Adopt **Option A — an in-database, per-chain cryptographic hash chain** over the
existing append-only `audit_events` log.

1. **Hash chain per stream.** Each record carries `prev_hash`, `entry_hash`,
   `hash_version`, and `chain_id`. `entry_hash` binds the serialization version,
   chain id, previous entry's hash, and the record's application content via
   SHA-256; the first record in a chain links to a fixed genesis
   (`GENESIS_PREV_HASH` = 64 zeros).
2. **One canonical serializer.** The hashed content is the record's application
   fields with sorted keys / compact separators, excluding DB-generated `id` and
   `occurred_at` so the hash is computable before insert and stable on
   re-verification. The single implementation lives in `app/security/audit_chain.py`
   (`compute_entry_hash` / canonical serialization) — there is no second serializer.
3. **Write path unchanged in surface.** `audit.write_audit_event(...)` remains the
   sole INSERT path. Within its existing transaction it takes a per-chain
   `pg_advisory_xact_lock` (so concurrent writers cannot fork a chain), reads the
   chain tail, computes the hash, and inserts it. The signature gains only one
   optional `chain_id` kwarg (default `"default"`) — fully backward compatible.
4. **Additive, reversible migration.** Migration `f2h3c4a5i6n7` adds the four
   columns as **nullable** with idempotent DDL (`ADD COLUMN` / `CREATE INDEX IF NOT
   EXISTS`, `DROP … IF EXISTS`). Existing rows are untouched → they remain valid but
   **unchained** (NULL hash columns); there is no backfill and no rewrite of
   history. The columns are deliberately **not** declared in the ORM metadata to
   avoid pre-creation by the declared-metadata migration `c410f4a1b2c3`.
5. **Verification is an internal service.** `IntegrityVerifier` / `verify_chain`
   recomputes each entry and checks linkage from genesis or an arbitrary trusted
   checkpoint, returning a deterministic result that identifies the first failure.
   No new HTTP surface is introduced.

## Alternatives considered
- **Option B — external notarization / WORM anchoring.** Stream each event (or a
  rolling digest) to an external append-only ledger, timestamping authority, or
  WORM object store. Rejected as the primary mechanism: it introduces a network
  dependency, cost, and operational surface disproportionate to the current threat
  model, and violates the "no new external runtime dependency" posture. Retained as
  **future** work — a periodic head-hash anchor can be layered on top of Option A
  without changing the record format.
- **Option C — full cryptographic ledger (Merkle accumulator / signed
  checkpoints / blockchain-style structure).** Rejected for now: materially heavier
  to build, operate, and reason about than the detection guarantee Epic 3 requires.
  Option A is forward-compatible with it — signed Merkle checkpoints can be added
  over the same per-record hashes later.

## Consequences
- **Positive:** tampering (forged/appended records, broken linkage, altered
  content) is cryptographically **evident** on verification; the guarantee is
  path-independent (enforced at the data layer, not the application); deterministic
  and reproducible; no new external dependency; existing rows, write surface, and
  API behavior are preserved.
- **Neutral / limits:** the chain is **tamper-evident, not tamper-proof** — it
  detects alteration rather than preventing an INSERT-privileged actor from
  appending; the chain head is not yet externally anchored, so a wholesale replay
  from genesis by a fully-privileged actor is out of scope until Option B's head
  anchor is added. Pre-migration rows remain **unchained** by design and are
  reported (not silently trusted) by the verifier.
- **Follow-on (deferred):** external head-hash anchoring (Option B), signed
  periodic checkpoints (Option C), and immutable-storage / SIEM export integrations.

## References
`docs/AUDIT_LOG.md` (F3.1), `docs/AUDIT_INTEGRITY.md` (F3.2), `docs/EVIDENCE.md`
(F3.3), `docs/AUDIT_EXPORT.md` (F3.4), `docs/DATABASE.md` (migration standard),
Engineering Constitution §§6, 9.
