# Client360 — Append-only Audit Log (F3.1 / Epic 3)

The canonical audit log is **append-only**: committed audit records can be created
but never modified or deleted. F3.1 **formalizes and verifies** this guarantee —
which already exists in the repository — as the immutable persistence substrate
that F3.2 tamper-evident hash-chaining will build on (ADR-015). F3.1 adds **no**
hash computation, integrity verification, or evidence-store functionality.

## Canonical model (reconciliation, ADR-013)
- **Table:** `audit_events` (declared in `app/database/identity_tables.py`):
  `id, actor_user_id, action, entity_type, entity_id, outcome, request_id,
  ip_address, user_agent, metadata (JSON, redacted), occurred_at`.
- **Sole persistence entry point:** `app.security.audit.write_audit_event(...)` —
  the only `INSERT` path; it applies `redact_metadata` and returns the new id.
  All producers route through it (portal, bootstrap, demo, middleware
  denial-audit, and the F2.5 `DbAuditSink`).
- **Read path:** `app/routes/admin.py` (read-only audit view). No update/delete
  path exists in application code.

## Append-only enforcement (already in place)
Enforced at the **database** level (the strongest, path-independent guarantee),
introduced in migration `c410f4a1b2c3` and part of the current baseline:

```sql
CREATE FUNCTION prevent_audit_event_mutation() RETURNS trigger AS $$
  BEGIN RAISE EXCEPTION 'audit_events are append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_events_immutable
  BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
```

- **INSERT** (append) is permitted; **UPDATE** and **DELETE** raise
  `audit_events are append-only` for every caller (including the DB owner) — so
  the guarantee holds regardless of code path or role.
- The trigger has a matching `downgrade` (drops trigger + function), so it is fully
  reversible under the migration standard.
- This is one instance of a repository-wide immutability pattern (e.g.
  `workflow_events_immutable`, published-`workflow_templates` immutability).

> F3.1 does **not** add a second/competing trigger — it reconciles, documents, and
> agreement-tests the existing one (ADR-013: formalize what exists).

## Guarantees (F3.1 acceptance)
- Audit creation succeeds via `write_audit_event`.
- Any `UPDATE` of a committed `audit_events` row **fails**.
- Any `DELETE` of a committed `audit_events` row **fails**.
- Existing audit behavior and the F2.5 `DbAuditSink` are unchanged.
- No API or schema change; no public behavior change.

## Compatibility with F3.2 (hash-chain)
The append-only trigger fires on **row** `UPDATE`/`DELETE`, not on DDL, so F3.2's
planned **additive** hash-chain columns (`prev_hash`, `entry_hash`, `hash_version`,
`chain_id` — `ALTER TABLE ADD COLUMN`) apply cleanly on top of this substrate
(ADR-015, Option A). Because rows are immutable, each entry's hash is fixed once
written — exactly the property a hash chain requires.

## Deferred work (Epic 3, not implemented here)
- **F3.2** hash-chain integrity (tamper-evident chaining + verifier).
- **F3.3** Evidence write-once store.
- **F3.4** Auditor read/export.

## References
- ADR-015 (Tamper-Evident Audit Architecture), ADR-013 (in-place reconciliation).
- F2.5 Security Audit Foundation (`docs/SECURITY_AUDIT.md`).
- Migration `c410f4a1b2c3` (`audit_events_immutable` trigger).
- Engineering Constitution §6 (audit/evidence tables are append-only).
