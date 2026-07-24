# Enterprise Knowledge Graph (Phase D.45)

The Enterprise Knowledge Graph is a **governed semantic composition** over the platform's authoritative
entities and the existing relationship engine. It connects people, households, businesses, trusts/estates,
professionals, advisors, and bounded per-domain collections into one explainable graph. It is **NOT a graph
database**, **NOT RDF/SPARQL**, and **NOT a second relationship engine**. See
[`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).

## Where it lives
`app/services/knowledge/` — `registry.py`, `model.py`, `service.py`, `explain.py`, `gate.py`, `stats.py`,
`metrics.py`, `diagnostics.py`, `governance.py`, `adapters/{relationship,advisor,domain}.py`. Routes:
`app/routes/knowledge.py`.

## Authoritative source map (composition, never ownership)
| Node/edge | Authoritative owner | How the layer reads it |
| --- | --- | --- |
| Entity relationships (family/business/estate/professional/ownership) | relationship engine | `relationships.build_relationship_graph(person_id)` |
| Person/household nodes | People / Households (CRM) | relationship engine + `people`/`households` |
| Business nodes | `organization_service` | relationship_entities of type business |
| Trust/estate/professional nodes | relationships | named relationship_entities |
| Advisor edge (advised_by) | Identity | `record_assignments` (entity_type=person) |
| Domain collections (accounts/insurance/tax/work/communications) | portfolio/insurance/tax/work/engagement | `get_client_snapshot` + D.44 engagement summary — count + deep link only |

Every mutation stays with the authoritative owner. The layer only reads.

## Composition
`knowledge_graph(principal, *, person_id|household_id)`:
1. Record-scope check (`record_in_scope`) → out of scope returns `None` (route 404).
2. Relationship adapter (`build_relationship_graph`, scope-filtered), advisor adapter
   (`record_assignments`), domain adapter (bounded collection nodes).
3. Dedup nodes by id, edges by (source, target, relationship); drop self-loops (cycle protection).
4. Attach explanations (if `explain.enabled`).
5. Returns `{enabled, root_id, nodes, edges, explanations, node_count, edge_count, suppressed_nodes,
   depth_limit, cycle_protection}`.

The household graph reuses the authoritative member resolution + each member's relationship graph, deduped
and bounded — mirroring the D.41 household composition (never a second store).

## Explainability
Every edge is explainable — see [`RELATIONSHIP_REGISTRY.md`](RELATIONSHIP_REGISTRY.md) and the explainability
engine (`explain.py`): why the relationship exists, which authoritative service owns it, what evidence
supports it, which deep link opens it, when it changed, and whether it is **inferred or authoritative**. An
inferred relationship is never presented as authoritative.

## Bounded traversal & search
Traversal is bounded (root → entity, one further hop max), cycle-safe, and scope-enforced — see
[`KNOWLEDGE_TRAVERSAL.md`](KNOWLEDGE_TRAVERSAL.md). Semantic search filters the already-scoped, registered
node set by entity type / relationship / owner / visibility — it never searches hidden or out-of-scope
entities.

## Runtime & policy governance
Gated through the Runtime Engine (`knowledge.enabled`, `knowledge.search.enabled`, `explain.enabled`; no env
fallback) AND the Policy Engine (`policy.evaluate("knowledge.*")` composed alongside RBAC — never bypassing
either). Reads require `client.read`; diagnostics require `observability.audit`.

## Why this is not a graph database / second relationship engine
- It imports no Neo4j/RDF/SPARQL/gremlin/networkx (governance forbids them), defines no table, writes no
  rows, publishes no events, writes no audit.
- The relationship adapter composes `build_relationship_graph` — the single relationship store — and never
  re-implements traversal over raw tables.
- Nodes/edges reference authoritative records; content is never copied.

## Integration
Client 360 + Household 360 gain a **Knowledge** section (connected entities + explanations); AI Assist
grounds on the connected-entity count (from the composed section). The client portal is unchanged (D.43
reuse only — no graph exposure). See [`KNOWLEDGE_GOVERNANCE.md`](KNOWLEDGE_GOVERNANCE.md),
[`ENTITY_REGISTRY.md`](ENTITY_REGISTRY.md).

## References
`app/services/knowledge/*`, `app/routes/knowledge.py`, `docs/platform_architecture_manifest.yaml`,
`tests/test_knowledge_graph.py`, ADR-050.
