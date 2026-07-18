# Client360 — Evidence Write-once Store (F3.3 / Epic 3)

A canonical, immutable, **reference-only** store for regulatory/operational
evidence associated with workflows. Evidence records are **write-once**: once
created they cannot be modified or deleted, so creation metadata, checksum, and
provenance are preserved. Implemented in-place (ADR-013) alongside the append-only
audit log (F3.1) and its hash chain (F3.2), which are unchanged.

`app/security/evidence.py`

## Reconciliation (ADR-013)
- Reuses the established **append-only DB trigger** pattern (`evidence_immutable`,
  mirroring `audit_events_immutable` from F3.1) — no new append-only logic invented.
- Independent of `audit_events` (the audit log) — evidence links to it optionally.
- Distinct from the domain table `tax_document_match_evidence`; this is the
  canonical, cross-domain evidence store.

## Schema (additive — migration `f3d4e5v6i7d8`)
Table `evidence`:
| Column | Meaning |
|---|---|
| `id` | Surrogate key |
| `evidence_uid` | Stable external identifier (uuid, unique) |
| `evidence_type` | What kind of evidence (e.g. `document_reference`, `signature`, `attestation`) |
| `classification` | e.g. `operational`, `regulatory` (default `operational`) |
| `source` | Where it came from (e.g. `taxdome`, `workflow`, `manual`) |
| `checksum` | Caller-supplied checksum of the referenced content (the store never sees the content) |
| `reference` | A **reference/URI** to the actual evidence — never binary content |
| `evidence_metadata` | Reference metadata (JSON; references only) |
| `provenance` | Who/what produced it |
| `audit_event_id` | Optional FK to `audit_events.id` (linkage) |
| `created_by` | Actor reference |
| `created_at` | Creation timestamp (DB-set) |
Indexes: `ix_evidence_audit_event`, `ix_evidence_created_at`; unique `uq_evidence_uid`.

## Write-once enforcement (DB-level, path-independent)
```sql
CREATE FUNCTION prevent_evidence_mutation() RETURNS trigger AS $$
  BEGIN RAISE EXCEPTION 'evidence records are write-once'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER evidence_immutable BEFORE UPDATE OR DELETE ON evidence
  FOR EACH ROW EXECUTE FUNCTION prevent_evidence_mutation();
```
INSERT is permitted; **UPDATE and DELETE raise** `evidence records are write-once`
for every caller (including the DB owner). Checksum, provenance, and creation
metadata are therefore immutable once written.

## Services (internal only — no public API)
```python
from app.security.evidence import record_evidence, get_evidence, list_evidence_for_audit, compute_checksum

rec = record_evidence(
    evidence_type="document_reference", source="taxdome",
    checksum=compute_checksum(file_bytes),          # caller computes; store never sees content
    reference="taxdome://doc/abc",                  # reference only
    classification="regulatory",
    provenance="workflow:TAXOPS-SOP-05",
    audit_event_id=audit_id,                         # optional linkage
    metadata={"document_ref": "abc"},                # references only, no PII
)
get_evidence(evidence_uid=rec.evidence_uid)
list_evidence_for_audit(audit_id)
```
All writes go through `record_evidence`; there is no update/delete service.

## Reference-only contract (privacy)
`reference` and `evidence_metadata` carry **references only** — never secrets, PII,
SSNs, tax-return data, or binary content (Constitution §9). Binary document content
is out of scope for this feature; the store records a checksum + reference.

## Guarantees / compatibility
- **Write-once** (append-only; update/delete rejected); checksum/provenance/creation
  metadata immutable.
- **Additive, reversible** migration; **existing audit functionality preserved**
  (F3.1 append-only and F3.2 hash chain unchanged).
- **No public API change** (internal service; no endpoints/routers).

## Deferred work (Epic 3+, not implemented here)
- **F3.4** Auditor read/export.
- External immutable storage (object store / WORM).
- Long-term retention automation.
- SIEM integration.

## References
ADR-013, `docs/AUDIT_LOG.md` (F3.1), `docs/AUDIT_INTEGRITY.md` (F3.2),
`docs/DATABASE.md` (migration standard), Engineering Constitution §§6, 9.
