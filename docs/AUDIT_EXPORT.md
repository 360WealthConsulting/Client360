# Client360 — Auditor Read/Export (F3.4 / Epic 3)

A controlled, **read-only** auditor surface for inspecting and exporting audit
records (F3.1), their tamper-evidence status (F3.2), and evidence references
(F3.3). It preserves store immutability — only SELECTs, **no mutation path** — and
introduces **no** public/unauthenticated export.

`app/security/audit_export.py`

## Authorization model (reuses the existing capability system)
Every operation requires the existing **`audit.read`** capability, enforced via the
F2.2 Authorization Foundation:
```python
default_authorization_service().require(AuthorizationContext.from_principal(principal), "audit.read")
```
- **Read-only, explicitly authorized, least privilege.** An unauthorized caller
  raises `AuthorizationDenied`.
- The surface **cannot** create/update/delete audit or evidence records (no such
  functions; underlying `audit_events_immutable` / `evidence_immutable` triggers
  still block mutation), cannot bypass existing authorization, and exposes **no**
  unrelated administrative operations.
- No hard-coded role checks — capability checks are reused.

## Services (internal; no public/HTTP endpoints)
```python
from app.security.audit_export import (
    read_audit_events, read_evidence, verify_integrity, build_export, serialize_export,
)
read_audit_events(principal, filters={...}, limit=100, offset=0)   # reference-only, redacted
read_evidence(principal, filters={...}, limit=100, offset=0)       # references + checksum only
verify_integrity(principal, chain_id="default", from_id=None)      # F3.2 verifier
export = build_export(principal, filters={...}, chain_id="default", generated_at="…")
serialize_export(export)                                           # deterministic JSON
```

## Export schema (versioned, deterministic)
`export_schema_version = 1`. `serialize_export` uses stable key ordering
(`json.dumps(sort_keys=True, separators=(",",":"))`), so the export is reproducible
for the same data, filters, `chain_id`, and (caller-supplied) `generated_at`.
```jsonc
{
  "export_schema_version": 1,
  "generated_at": "<caller-supplied>",        // deterministic input, not wall-clock
  "chain_id": "default",
  "filters": { ... },                          // filters applied
  "record_counts": { "audit_events": N, "evidence": M, "legacy_unchained": K },
  "integrity": {                               // F3.2 verifier result
    "chain_id": "…", "ok": true, "checked": N, "first_failure_id": null,
    "reason": "…", "checkpoint": null, "genesis": "<64-zero>",
    "head": "<entry_hash|null>", "legacy_unchained_count": K
  },
  "audit_events": [ { reference-only fields incl. entry_hash/prev_hash/chain_id, "chained": bool, "metadata": <redacted> } ],
  "evidence":     [ { evidence reference fields incl. checksum/reference/provenance } ]
}
```
Records are ordered by `id`. The export **never** includes binary document content.

## Privacy & redaction
- **Preserves existing redaction:** audit metadata is redacted at write; the export
  re-applies `redact_metadata` (idempotent) and also redacts evidence metadata — so
  sensitive-named values never appear.
- **Fields intentionally excluded:** `ip_address` and `user_agent` (client
  identifiers not needed for content review) are **not** exported. No secrets,
  credentials, session tokens, or internal security material are exposed. No binary
  content.

## Filtering & pagination
Justified audit filters only: **audit** — `actor_user_id`, `action`, `entity_type`,
`entity_id`, `outcome`, `request_id`, `chain_id`, `start`/`end` (occurred_at range);
**evidence** — `evidence_type`, `classification`, `source`, `audit_event_id`.
Retrieval is **bounded** (`limit` capped at `MAX_LIMIT = 1000`, `offset` supported).
No full-text search, OCR, or indexing.

## Integrity reporting (reuses F3.2)
`verify_integrity` / the export's `integrity` block calls the existing
`audit_chain.verify_chain` (no second verifier; no change to hash computation) and
reports: verification status, records checked, first integrity failure (if any),
chain id, genesis/checkpoint used, chain head, and the count of legacy **unchained**
records (pre-F3.2 rows with NULL hash columns).

## Compatibility
- **F3.1 / F3.2 / F3.3 preserved** — append-only, hash chain, and write-once
  enforcement unchanged; this surface is read-only.
- **No API behavior change** — internal service; no new HTTP routes (route inventory
  unchanged). A future feature may expose it over authenticated HTTP.
- **No migration** — read/export over existing stores.

## Deferred work (Epic 3+, not implemented here)
- External immutable-storage delivery.
- Scheduled audit packages.
- SIEM integration.
- Long-term retention automation.
- Regulator-specific filing formats.

## References
ADR-013, ADR-015, `docs/AUDIT_LOG.md` (F3.1), `docs/AUDIT_INTEGRITY.md` (F3.2),
`docs/EVIDENCE.md` (F3.3), `docs/AUTHORIZATION.md` (F2.2), Engineering Constitution §§6, 9.
