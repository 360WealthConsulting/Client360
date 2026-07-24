# Knowledge Entity Registry (Phase D.45)

`ENTITY_REGISTRY` in `app/services/knowledge/registry.py` is the single declarative catalog of every entity
type the knowledge graph can expose as a node. See [`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).

## Entity record
Each `EntityType` declares:
- `key` — the entity type;
- `owner` — the authoritative owning service (where mutations happen);
- `source_service` — the module whose scoped read produces the node;
- `lifecycle` — `active` | `experimental` | `deprecated` | `retired`;
- `visibility` — `internal` | `external` | `both`;
- `deep_link` — the deep-link template (`…/{id}`) into the authoritative surface;
- `explainability` — how the platform knows this entity exists;
- `collection` — whether it is a bounded per-domain summary node (count + link) rather than an individual
  record.

## Registered entity types
- **Graph entities** (individual nodes from the relationship engine / identity): `person`, `household`,
  `business`, `trust`, `estate`, `professional`, `insurance_carrier`, `advisor`.
- **Bounded collection entities** (count + deep link, never individual hidden records): `accounts`,
  `insurance`, `tax`, `documents`, `meetings`, `communications`, `work`, `opportunities`, `benefits`.

Each maps to an authoritative scoped read and a deep link — e.g. `person` → Client 360 → `/client/{id}`;
`business` → `organization_service` → `/organizations/{id}`; `advisor` → `record_assignments`; `insurance`
(collection) → `/insurance`.

## Deep links & explainability
`deep_link_for(entity_key, entity_id)` renders the deep link. Every node carries its owner + deep link so
the surface can route the user to the authoritative record — the graph never mutates and never inlines an
action.

## Onboarding a new entity type
Add an `EntityType` (via the `_e(...)` helper) to `ENTITY_REGISTRY` with its owner, source service,
deep-link template, visibility, and explainability. If it is a bounded summary, set `collection=True` and
add its domain edge in `adapters/domain.py`. Governance verifies the new type is fully declared; the
registry coverage guard locks the catalog.

## Visibility
`INTERNAL_ONLY_ENTITIES` (advisor + the internal collections) must never be exposed to an external surface —
the client portal is unchanged by D.45 (D.43 reuse only). `coverage()` reports totals (entity/relationship
types, collections, internal-only, raw-code mappings, domain relationships) and feeds diagnostics.

## References
`app/services/knowledge/registry.py`, `app/services/knowledge/adapters/*`,
`app/services/knowledge/governance.py`, `tests/test_knowledge_graph.py`, ADR-050.
