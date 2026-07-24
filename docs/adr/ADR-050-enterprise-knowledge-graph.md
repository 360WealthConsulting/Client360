# ADR-050 — Enterprise Knowledge Graph and Explainable Relationship Layer: A Semantic Composition, Not a Graph Database

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Client Experience / Relationships); Reliability / Operations; Security /
Authorization (RBAC + record scope ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.45 audit found that the platform already has a complete, authoritative relationship engine
and every entity the graph would connect:

* **Relationship engine** — `app/services/relationships.py` with `build_relationship_graph(person_id)` over
  the `relationship_entities` (polymorphic node) + `relationships` (edges) + `relationship_types`
  (vocabulary) triad. Family, business, professional, estate, ownership, and org-structure edges ALL compose
  over this single engine; business ownership adds a typed `relationship_ownership` detail; trusts/estates
  are named `relationship_entities`. The **household graph** (`client360/household.py::_relationships`)
  already composes it with dedup + cycle protection + a depth cap.
* **Authoritative entities** — each with a scoped read + deep link: person/household (Client 360),
  business (`organization_service`), accounts/portfolio, insurance, tax returns, documents
  (`document_platform`), meetings (`scheduling`), communications (D.44 engagement), workflows,
  opportunities, benefits, and advisor↔client via `record_assignments`.
* **No knowledge/semantic layer exists** — confirmed by grep (no Neo4j/RDF/SPARQL/graph store anywhere).

Introducing a graph database (Neo4j/RDF/SPARQL), duplicate relationship tables, or duplicate entity tables
would violate the platform's "no second system" invariant and duplicate the dedup/scope/cycle work the
relationship engine and household composition already do.

## Decision
Phase D.45 adds a **governed semantic composition layer** (`app/services/knowledge/`) — an explainable
knowledge graph over the authoritative models, with NO new store:

1. Two **declarative registries** (`registry.py`): `ENTITY_REGISTRY` (every entity type → owner, source
   service, lifecycle, visibility, deep-link, explainability) and `RELATIONSHIP_REGISTRY` (every
   relationship type → direction, authoritative owner, visibility, explanation, traversal rule, lifecycle).
   A raw-code map projects the relationship engine's codes onto registered graph relationships.
2. Normalized read-models (`model.py`): `Node`, `Edge`, `Path`, `Explanation` — references only, no copied
   content.
3. Read-only, scope-aware, fail-closed **adapters** (`adapters/`): the relationship adapter composes
   `build_relationship_graph` and filters person counterparts through `accessible_person_ids` (out-of-scope
   people suppressed, never leaked); the advisor adapter reads `record_assignments`; the domain adapter
   exposes BOUNDED per-domain collection nodes (count + deep link) from `get_client_snapshot` + the D.44
   engagement summary — never individual hidden records.
4. A bounded, cycle-safe, scope-enforced **traversal + graph service** (`service.py`), an **explainability
   engine** (`explain.py`) answering why/owner/evidence/deep-link/updated/inferred-vs-authoritative, and a
   **semantic search** over the scoped, registered node set.
5. **Runtime gates** (`gate.py`: `knowledge.enabled`, `knowledge.search.enabled`, `explain.enabled`),
   **policy composition** (`policy.evaluate("knowledge.*")` alongside RBAC — never bypassing either),
   low-cardinality **analytics** (4 metrics), internal **diagnostics** (`observability.audit`), and a
   read-only **governance** checker.

No migration, no new table, no new capability (reuses `client.read` + `observability.audit`), no new outbox
contract. Single Alembic head stays `m4p5o6r7t8c9`.

## Alternatives considered
- **A graph database (Neo4j) / RDF+SPARQL.** Rejected: a second store, an operational dependency, and a
  duplicate of the authoritative relationship engine. Governance actively forbids these imports.
- **Duplicate relationship / entity tables.** Rejected: the `relationship_entities`/`relationships` triad is
  already the canonical node/edge store; the graph composes over it.
- **Enumerating every connected record as a node.** Rejected: unbounded and a hidden-record leakage risk;
  replaced by bounded per-domain collection nodes (count + deep link).
- **Letting AI explore the graph freely.** Rejected: AI grounds on the composed Client 360 knowledge
  summary (connected-entity counts only); every explanation cites its authoritative service.

## Reasons for the decision
Composition over the authoritative relationship engine gives an explainable graph for free — already deduped,
cycle-safe, and (with the added `accessible_person_ids` filter) scope-enforced. The registries add the
missing semantic catalog + explainability; bounded traversal + collection nodes keep it safe and non-leaky;
deep links (never inline mutation) route the user to the authoritative surface to act.

## Consequences

### Positive consequences
- One explainable, governed knowledge graph with no graph database and no second relationship engine.
- Every edge is explainable with its authoritative owner + evidence + provenance; inferred is never shown as
  authoritative.
- Bounded, cycle-safe, scope-enforced traversal; out-of-scope people suppressed, hidden records never leaked.
- Zero schema change: no migration, table, capability, or outbox contract.

### Negative consequences and tradeoffs
- Traversal is bounded (root → entity, one further hop max) — deep multi-hop graph analytics are out of
  scope by design.
- Domain connections are collection summaries (count + link), not per-record nodes; drilling into a record
  happens on its authoritative surface.

## Enforcement
`tests/test_knowledge_graph.py` (registries; composition; every edge authoritative + registered; bounded
typed traversal; no self-loops/dedup; out-of-scope root → None; out-of-scope person suppressed; explanation
answers the six questions; inferred never authoritative; search; runtime + policy gates; Client 360 /
Household 360 integration; AI grounding; analytics; diagnostics; governance; and the architecture invariants
— no graph DB import / no Table / no mutation / no outbox / no audit write in any knowledge module).
`app/services/knowledge/governance.py` enforces the invariants at runtime. Route count, section registry, and
migration head are guarded by `tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`docs/platform_architecture_manifest.yaml`.

## Exceptions
The domain adapter reads bounded per-person aggregations (`get_client_snapshot`) rather than a graph edge,
because those domain connections are not modeled as `relationships` edges; it exposes counts + deep links
only, never individual records.

## Revisit conditions
Revisit when deep multi-hop graph analytics are required (beyond the bounded traversal), when trust/estate
detail modeling is added (today they are named entities + estate relationship types), or if a knowledge
lifecycle event gains a consumer that would justify an outbox contract.

## References
- `app/services/knowledge/*` (`registry.py`, `model.py`, `service.py`, `explain.py`, `gate.py`, `stats.py`,
  `metrics.py`, `diagnostics.py`, `governance.py`, `adapters/relationship.py`, `adapters/advisor.py`,
  `adapters/domain.py`)
- `app/routes/knowledge.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; AI grounding in
  `app/services/ai_assist/context.py`; analytics in `app/services/analytics/{sources,metrics}.py`
- Reuses `app/services/relationships.py` (`build_relationship_graph`), `record_assignments`,
  `app/services/advisor_workspace.py` (`get_client_snapshot`), the D.44 engagement summary
- `docs/KNOWLEDGE_GRAPH.md`, `docs/ENTITY_REGISTRY.md`, `docs/RELATIONSHIP_REGISTRY.md`,
  `docs/KNOWLEDGE_TRAVERSAL.md`, `docs/KNOWLEDGE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_knowledge_graph.py`; relates to ADR-004, ADR-013, ADR-018, ADR-028, ADR-030, ADR-040,
  ADR-041, ADR-044 through ADR-049
