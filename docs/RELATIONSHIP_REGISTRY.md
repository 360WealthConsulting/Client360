# Knowledge Relationship Registry (Phase D.45)

`RELATIONSHIP_REGISTRY` in `app/services/knowledge/registry.py` is the single declarative catalog of every
relationship type the knowledge graph can expose as an edge, plus the map from the relationship engine's raw
codes onto registered graph relationships. See [`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).

## Relationship record
Each `RelationshipType` declares:
- `code` — the relationship type;
- `direction` — `directed` | `undirected`;
- `authoritative_owner` — the subsystem that owns the edge;
- `visibility` — `internal` | `external` | `both`;
- `explanation` — why this relationship exists (the "why" the explainability engine returns);
- `traversal_rule` — `one_hop` (people/households/businesses), `root_only` (domain collections), or
  `terminal` (leaf nodes like advisor — never traversed onward);
- `lifecycle`;
- `category` — `family` | `business` | `professional` | `estate` | `ownership` | `assignment` | `domain`.

## Registered relationships
- **Entity edges** (backed by the relationship engine): `member_of`, `spouse`, `child`, `owns`,
  `employer_of`, `trustee_of`, `beneficiary_of`, `advises`, `related_to`.
- **Assignment edge** (backed by `record_assignments`): `advised_by`.
- **Domain edges** (to bounded collection nodes): `has_accounts`, `insured_by`, `has_tax_returns`,
  `uploaded`, `attends`, `communicated`, `has_work`, `has_opportunities`, `enrolled_in`.

## Classification of raw codes
`map_raw_relationship(raw_code)` projects the relationship engine's raw codes (`spouse`, `owner`, `cpa`,
`trustee`, `household_member`, …) onto registered graph relationships (`spouse`, `owns`, `advises`,
`trustee_of`, `member_of`, …). Unmapped raw codes are dropped and counted as **orphan relationships** in
diagnostics — they are never rendered as unregistered edges. Governance asserts every raw-code mapping
targets a registered relationship.

## Explainability contract
For any edge, the explainability engine (`explain.py`) answers the six questions:
1. **Why** does this relationship exist? → the registry `explanation`.
2. **Which** authoritative service owns it? → the edge/registry `owner`.
3. **What** evidence supports it? → an authoritative-record statement (+ confidence) or an explicit
   "Inferred — not an authoritative record" note.
4. **Which** deep link opens it? → the edge deep link.
5. **When** was it last updated? → the edge `last_updated` (when the source provides it).
6. **Is** it inferred or authoritative? → the edge `provenance`; an inferred edge is NEVER presented as
   authoritative.

## Onboarding a new relationship
Add a `RelationshipType` (via `_r(...)`) with its owner, explanation, traversal rule, and category; if it
originates from a raw relationship-engine code, add the mapping in `_RAW_CODE_MAP`. Governance verifies
completeness + single ownership (no duplicate codes).

## References
`app/services/knowledge/registry.py`, `app/services/knowledge/explain.py`,
`app/services/knowledge/adapters/relationship.py`, `tests/test_knowledge_graph.py`, ADR-050.
