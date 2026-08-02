# ADR-073 — Household domain as the authoritative ownership model for Client360

## Status
Accepted — authoritative ownership architecture; partially realized today (see "Current state").

## Date
2026-08-02

## Decision owners
Platform Architecture; Business Operations Owner (Michael Shelton); Domain Owner (Household / CRM).

## Context
Client360 needs one truthful answer to "who owns this?" — for a document, an account, a tax return, a
plan, a portal login, a compliance obligation, or an AI answer. Ownership is a **relationship** concern
(household, person(s), spouse, dependents, businesses, trusts, estates, beneficiaries), distinct from a
record's content or its source system. If each module (Documents, Tax, CRM, Planning, Compliance, AI,
Workflow, Reporting, Client Portal) invents its own notion of ownership — its own household grouping,
its own spouse/dependent linkage, its own "show to the family" rule — the platform forks ownership
logic, drifts out of sync, mis-scopes sensitive data, and makes household merge/split impossible to
reason about. ADR-072 established the **canonical document** (one document, many source references,
ownership independent of source); this ADR establishes the **household domain** as the single
authoritative **ownership** model those documents — and every other record — reference.

## Decision
The **Household domain is the authoritative ownership model for Client360.** Every module references it;
no module implements independent ownership logic.

**Entities and relationships (the ownership graph).**
1. **Person** — the canonical individual (`people`); the atomic party. Never duplicated per module.
2. **Household** — the primary ownership grouping of related people (`households` +
   `household_relationships`). A person belongs to at most one primary household.
3. **Relationships** — typed edges between parties (`household_relationships` for membership;
   `relationship_entities` / relationship types for the broader graph). **Spouse** and **dependent** are
   relationship roles within a household, not separate tables. Dependents are people with a dependent
   relationship to the household/guardians.
4. **Businesses, Trusts, Estates** — non-person owning parties (organizations and legal entities,
   `organization_id` / relationship entities). They can own records and can relate to households and
   people (e.g. a trust whose beneficiaries are household members).
5. **Beneficiaries** — a relationship role (person/trust/estate designated on an account, policy, or
   plan), expressed as relationships to the owning party — never a duplicated party record.

**Ownership kinds (all reference the same parties above).**
6. **Household ownership** — membership of people (and the household's related entities) in a household.
7. **Document ownership** — a document (ADR-072 canonical document) is owned by a household and/or
   person(s)/business/trust/estate via ownership relationships, **independent of source**. Document
   modules MUST resolve visibility through this ownership, not by re-deriving families.
8. **Financial ownership** — accounts, policies, plans, and tax returns are owned by the same parties
   (person / household / business / trust / estate) and beneficiary relationships; financial modules
   MUST NOT invent a parallel client/household concept.

**Cross-cutting concerns bound to the ownership model.**
9. **Permissions** — record scope and authorization (ADR-004) are computed against ownership: a
   principal's access to a record derives from the owning household/person/entity, not from per-module
   rules. Ownership feeds `accessible_person_ids` / record-scope resolution.
10. **Audit history** — every ownership change (assign/remove member, add/retire relationship, merge,
    split, beneficiary change) is audited against the household/party, so "who changed ownership, when,
    and why" is answerable in one place.
11. **Household merge** — combining two households that represent the same real family: the surviving
    household absorbs members, relationships, and ownership references; the other is retired (not hard-
    deleted). Merge is an explicit, audited, human-verified operation — never automatic.
12. **Household split** — separating a household (e.g. divorce): people and their owned records move to
    the correct resulting household(s) via relationship changes; history is preserved. Explicit,
    audited, human-verified.
13. **Duplicate household detection** — surfaces likely-duplicate households (shared members, address,
    or name) as **candidates for human review**; it never auto-merges. Mirrors the Match Review
    philosophy for people.
14. **AI context** — AI features receive the household ownership graph as read-only context (who is in
    the household, their relationships and owned records) and MUST NOT infer, create, or mutate
    ownership; AI never merges households, assigns members, or fabricates relationships.
15. **Future workflow integration** — workflows, automations, the API, and reporting act on ownership
    through the household service; a workflow that needs "the household's documents" or "route to the
    household's advisor" resolves it through the same service, not a private query.

**Service boundary.** `app/services/households.py` (and the surrounding Household domain services) is the
**single supported API** for ownership operations. `assign_people_to_household` is the first realized
operation; merge, split, relationship management, and duplicate detection are added to the same domain
service. Every consumer — the future Household Management UI, the API, workflows, automations, and the
CLI — calls the service; the CLI is one consumer, not the implementation. Safety guarantees hold at the
service boundary regardless of caller: person records and document/source links are preserved, no
duplicate households are created, operations are idempotent, and people already in different households
are never merged automatically.

## Alternatives considered
1. **Per-module ownership** (each of Documents/Tax/CRM/Planning/… keeps its own household/family
   notion). Rejected: forks ownership logic, drifts, mis-scopes sensitive data, and makes merge/split
   and consistent permissions impossible.
2. **Ownership as document/account attributes only** (no first-class household graph). Rejected: cannot
   express spouse/dependent/trust/beneficiary relationships, household merge/split, or AI context.
3. **Encode ownership into record identity or de-duplication** (e.g. dedup documents per person).
   Rejected (consistent with ADR-072): ownership is orthogonal to identity; a household document must
   not fork per member.
4. **Build the full ownership platform (merge/split/duplicate-detection/UI) now.** Deferred, not
   rejected: this ADR fixes the architecture; the operations land incrementally on the same service
   (Household Management UI next, then Drake integration), avoiding a premature build ahead of need.

## Reasons for the decision
- A single ownership model is the only way permissions, document/financial visibility, AI context, and
  reporting stay consistent as modules multiply.
- Modeling spouse/dependent/beneficiary as relationship roles (not new tables) keeps the graph
  extensible without schema churn and matches the existing `households`/`household_relationships`/
  `relationship_entities` structures.
- Making merge/split/duplicate-detection explicit, audited, and human-verified matches Client360's
  established conservative philosophy (Match Review for people; no automatic merges) and protects
  client data.
- Pairing this with ADR-072 gives the platform two clean foundations — **document** (what a record is)
  and **ownership** (whose it is) — that every module composes rather than reinvents.

## Consequences

### Positive consequences
- New modules add ownership by referencing the household service, not new ownership tables; scope,
  visibility, and AI context are consistent by construction.
- Household merge/split/duplicate-detection have one authoritative, audited home.
- Document ownership (ADR-072) and every financial/tax/plan record resolve visibility the same way, so
  "the family's records" means one thing platform-wide.

### Negative consequences and tradeoffs
- Modules that today carry ad-hoc `household_id`/`person_id` columns must, over time, resolve ownership
  through the service rather than private logic; this is a migration of *behavior*, done incrementally.
- Many-valued ownership (multiple persons, trust, estate on one record) and merge/split are not fully
  implemented yet; until then some ownership is single-valued (`household_id`/`person_id`), which covers
  the common and household cases but not every legal-entity scenario.
- Duplicate-household detection produces review candidates, not automatic cleanup — a deliberate cost
  (human time) taken to avoid wrong merges.

## Current state
- `people`, `households`, `household_relationships`, `relationship_entities`, and `organization_id`
  already model persons, households, membership, relationships, and non-person owning entities.
- `app/services/households.py::assign_people_to_household` is the first realized ownership operation on
  the authoritative service (human-verified, audited-capable, idempotent, no duplicate/auto-merge).
- Document ownership already resolves through the household model: `get_person_documents` returns a
  person's documents **and** their household's documents (ADR-072 alignment).
- **Not yet implemented (future, additive, on the same service):** household merge, household split,
  relationship/beneficiary management, duplicate-household detection, many-valued
  `document_ownership`/record ownership, the Household Management UI, and Drake integration consuming
  these foundations.

## Enforcement
- New modules MUST resolve ownership (who owns / who may see a record) through the Household domain
  service and record scope (ADR-004); they MUST NOT implement a private household/family grouping or a
  parallel client concept.
- Ownership operations (assign, merge, split, relationship/beneficiary changes) MUST go through the
  household service, be audited, and preserve person records and document/source links.
- Household merge/split and any cross-household change MUST be explicit and human-verified; automatic
  merges across existing households are prohibited (the service raises rather than merging).
- AI and automated callers MUST treat the ownership graph as read-only context and MUST NOT create or
  mutate ownership.
- Reviewers reject changes that add per-module ownership logic, duplicate households/people, or couple
  ownership into record identity/de-duplication.

## Exceptions
- Existing single-valued ownership columns (`household_id`/`person_id`/`organization_id`) remain valid
  until many-valued ownership is required; they are read through the service's helpers, not extended
  with private grouping rules.
- A record with no resolvable owner is left unresolved for human review (as with unresolved TaxDome
  folders) — never assigned by a weak automatic match.

## Revisit conditions
- Household merge, split, or duplicate-household detection is scheduled — implement on the household
  service with audit + human verification.
- Many-valued ownership (multiple persons, trust, or estate on one record) becomes a requirement — add
  the `document_ownership` / record-ownership join (additive, per ADR-072).
- The Household Management UI or Drake integration is built — both MUST consume this service rather than
  re-deriving ownership.

## References
- `app/services/households.py` — the authoritative household ownership service (`assign_people_to_household`).
- `app/services/household_derivation.py` — automatic derivation engine (policy-gated; conventions shared).
- `app/db.py` — `people`, `households`, `household_relationships`, `relationship_entities` tables.
- `app/services/documents.py` — document visibility resolved through household ownership (ADR-072).
- `app/importers/taxdome_drive.py` — `resolve_folder` links documents to household/person ownership.
- `tests/test_household_admin.py` — safety guarantees for the ownership service.
- ADR-072 (docs/adr/ADR-072-canonical-document-model.md) — canonical document; ownership independent of
  source. ADR-002 (domain ownership & source of truth) and ADR-004 (server-side authorization & record
  scope) — permissions computed against ownership.
