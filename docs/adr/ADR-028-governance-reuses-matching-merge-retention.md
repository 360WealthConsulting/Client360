# ADR-028 — Data Governance reuses matching/merge/retention; never an unsafe merge or hard delete

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Data Governance); Compliance Architecture (deletion/legal-hold
approval, lineage provenance); Business Operations Owner (Michael Shelton — records-control
requirements). Authorized compliance reviewer: Not yet designated (deletion/hold approval workflows
require compliance sign-off before any regulated destruction).

## Context
The platform already has the canonical identity model (`people`/`households`/`accounts`;
organizations = `relationship_entities` + `organization_profiles`), deterministic **lineage**
(`source_contacts` + `person_source_links`), deterministic **matching/merge**
(`promote.list_ambiguous_unlinked`/`resolve_link_to_person`/`resolve_create_person`;
`person_merge.merge_source_contacts`, which **refuses to merge records that resolve to different
people**; `match_review_decisions`), a **document retention** model (`document_retention_policies`
with deterministic expiration derivation, soft-delete only — **no hard delete anywhere**), a
tamper-evident **audit hash-chain** (`write_audit_event`), Compliance reviews/decisions, Workflow,
Automation (D.22), Analytics, and the Timeline. There was **no** data-quality, duplicate-candidate,
survivorship, retention-assignment, legal-hold, or deletion-request table/service. The risk of a new
governance domain is that it re-implements matching/survivorship or performs unsafe merges/hard
deletes.

## Decision
Data Governance is a new authoritative **governance domain** that owns **governance metadata only**
and is **never the source of truth for client or business data**. Canonical domains remain
authoritative.
- **Owns:** `governance_data_domains`/`_data_elements`, `governance_lineage` (non-person entities
  only), `governance_quality_rules`/`_quality_checks`/`_quality_findings`,
  `governance_duplicate_candidates`, `governance_survivorship_rules`, `governance_merge_decisions`,
  `governance_retention_assignments`, `governance_legal_holds`, `governance_deletion_requests`,
  `governance_cases` (remediation/exception/certification), and `governance_events` (an **append-only**
  audit ledger). It also writes governance actions to the shared `audit_events` hash-chain.
- **Reuses matching/merge — never reimplements or performs an unsafe merge.** Duplicate detection
  reads the EXISTING ambiguity queue (`promote.list_ambiguous_unlinked`); an approved merge is applied
  **only** through `person_merge.merge_source_contacts`, which already refuses cross-person merges.
  Governance recomputes no matching math and never inserts/merges a canonical record itself. Applying
  a merge requires `governance.review`/`governance.admin`.
- **Lineage** is **read** from `person_source_links` + `source_contacts` for people (never shadowed);
  governance-owned lineage rows are added only for non-person entities.
- **Retention references the Document Platform policies** (`document_retention_policies`) — no
  parallel policy table — and derives expiration deterministically. It extends coverage to
  person/household/account/document via a polymorphic `governance_retention_assignments`.
- **No hard delete, ever; no automatic destruction.** A deletion/archival request is a metadata
  review + approval that **requires `governance.review`/`governance.admin`**, is **refused when the
  entity is under an active legal hold**, and — even when approved and "executed" — records intent and
  provenance only; canonical destruction (if any) is a separate, dedicated, separately-approved
  process (and would launch a workflow / reference a compliance review).
- **Survivorship is deterministic** (`most_recent`/`most_complete`/`source_priority`/`manual`) — no
  probabilistic survivorship, no AI matching.
- **Integrations:** Governance **launches** remediation/merge-review/deletion-review workflows
  (Workflow authoritative); **Automation** may drive governance jobs (`governance_quality_scan` /
  `governance_stale_scan` / `governance_retention_review`, added to the D.22 dispatch registry with a
  widened `JOB_TYPES` CHECK); **Analytics** consumes governance statistics (open findings, active
  legal holds) — Governance never depends on Analytics; **Timeline** receives approved,
  **client-anchored** governance lifecycle events (finding opened, merge approved, legal hold placed,
  deletion approved, remediation completed) — firm-level items record to `governance_events` only.
- **Security:** `governance.view/manage/review*/audit*/admin*` (`*` = sensitive), gated in-route
  (`/governance` matches no middleware RULE). Record scope enforced on client-anchored items;
  merge apply / legal holds / deletion approval require `governance.review`.

## Alternatives considered
1. **Re-implement matching/survivorship inside Governance.** Rejected: duplicates a mature,
   audited deterministic engine and risks false merges. Governance orchestrates `merge_source_contacts`
   / `promote.*` only.
2. **Let Governance hard-delete on approval.** Rejected: the platform is soft-delete-only and the
   phase forbids destruction without a dedicated approved review. Governance records intent only.
3. **A parallel `retention_policies` table.** Rejected: reuses `document_retention_policies`.
4. **Shadow `person_source_links` in a governance lineage table.** Rejected: reads the existing
   links; adds governance lineage only for entities that have none (organizations/accounts).

## Reasons for the decision
The firm needs authoritative governance over quality, duplicates, survivorship, lineage, retention,
holds, and deletion — with audit — without a second identity/matching engine and without any unsafe
merge or destruction. A governance-metadata domain that orchestrates the existing deterministic
infrastructure delivers this while preserving every ADR and the D.5 golden.

## Consequences
### Positive consequences
- One authoritative governance domain (findings/duplicates/merges/survivorship/lineage/retention/
  holds/deletion/cases) with an append-only ledger and the shared audit hash-chain.
- The canonical identity/matching/merge engine and document retention are reused, not duplicated;
  no unsafe merge and no hard delete are possible through Governance.
- Automation can run governance scans; Analytics gains governance metrics; the timeline receives only
  approved client-anchored events.

### Negative consequences and tradeoffs
- Deletion "execution" is metadata only — actual canonical destruction is out of scope and requires a
  separate dedicated process (a documented limitation).
- Person lineage is a read over `person_source_links`; governance lineage rows exist only for
  non-person entities (two lineage surfaces, by design).
- The D.22 `JOB_TYPES` CHECK constraints were widened (a cross-domain migration touch) to admit
  governance job types — a documented, reversible change.

## Enforcement
- `app/database/governance_tables.py::define_governance_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `u1f2a3b4c5d6` (14 tables +
  append-only trigger on `governance_events` + 5 `governance.*` capabilities + widened automation
  `JOB_TYPES` CHECK + seeds). Services `app/services/governance/{common,catalog,quality,mdm,retention,
  service}.py` — `mdm.py` reuses `person_merge.merge_source_contacts`/`promote.*`; `retention.py`
  references `document_retention_policies` and never hard-deletes. Routes `app/routes/governance.py`
  (in-route `governance.*` gating; `/governance` matches no middleware RULE; review-gated approvals).
  Governance is registered in `source_producer_modules` (must not import composition layers). The D.5
  golden, matching/merge, document retention, and the audit hash-chain are untouched. Tests:
  `tests/test_governance.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Implementing actual canonical destruction on deletion approval, an org/account merge engine,
probabilistic/AI survivorship or matching, or a governance-owned retention policy vocabulary would
each warrant a new or superseding ADR (and, for regulated destruction, compliance sign-off).

## References
- `app/services/governance/`, `app/routes/governance.py`, `app/database/governance_tables.py`,
  migration `migrations/versions/u1f2a3b4c5d6_governance_platform.py`
- Reused infra: `app/services/person_merge.py`, `app/matching/promote.py`, `person_source_links`,
  `source_contacts`, `document_retention_policies`, `app/security/audit.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_governance.py`; relates to ADR-002, ADR-003, ADR-004, ADR-008, ADR-009, ADR-016,
  ADR-021, ADR-022
