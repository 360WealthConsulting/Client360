# Knowledge Traversal (Phase D.45)

Traversal over the Enterprise Knowledge Graph is **bounded, cycle-safe, scope-enforced, and explainable**. It
composes over the authoritative relationship engine — it never explores a graph database and never performs
unrestricted graph exploration. See [`KNOWLEDGE_GRAPH.md`](KNOWLEDGE_GRAPH.md) and
[`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).

## Bounds
- **Depth limit** — `DEPTH_LIMIT = 2` (root → entity → at most one further hop). `traverse(...)` clamps the
  requested depth to this limit.
- **Result cap** — traversal returns at most 200 paths; search returns at most 100 results.
- **Per-source cap** — the relationship engine's own bounds apply (one hop per household member for the
  household graph).

## Cycle protection
Nodes are deduplicated by id and edges by `(source, target, relationship)`; self-loops are dropped and
counted (`cycles_avoided`). The household composition additionally reuses the D.41 dedup + depth cap.

## Scope enforcement
- The root (person/household) is checked with `record_in_scope` before composition; out of scope →
  `traverse`/`knowledge_graph`/`search` return `None` → the route emits 404.
- Person-type counterparts are filtered through `accessible_person_ids`; an out-of-scope related person is
  **suppressed** (counted as `hidden_suppressed`), never leaked.

## Policy
`traverse` and `search` compose the Policy Engine — `gate.policy_ok("traverse"|"search")` evaluates
`policy.evaluate("knowledge.<area>")` alongside the route's RBAC check. RBAC is never bypassed; an explicit
policy deny returns an empty result with `denied: "policy"`.

## Explainable paths
Each traversal path is `{nodes, edges, depth}` where every edge carries its relationship, owner, provenance,
and deep link. The explainability engine turns any edge into a full explanation (why/owner/evidence/deep-
link/updated/inferred-vs-authoritative).

## Supported traversals
Client → Household, Client → Advisor, Client → Business (owns), Client → Trust/Estate, Client → Professional
(advises), and Client → domain collections (Insurance, Accounts, Tax, Work, Communications). Household →
member businesses/trusts/shared advisors via the member-merged household graph.

## API
- `knowledge_graph(principal, *, person_id|household_id)` — the full bounded graph.
- `traverse(principal, *, person_id|household_id, target_type=None, depth=1)` — explainable paths, optionally
  filtered to a target entity type.
- `explain_relationship(principal, *, person_id|household_id, target_id=None, relationship=None)` — explain
  one edge.
- `search_entities(principal, *, person_id|household_id, query, entity_type, relationship, owner,
  visibility)` — semantic search over the scoped node set.
- `knowledge_summary(principal, *, person_id|household_id)` — connected-entity counts + explanation
  completeness (backs the Client 360 / Household 360 sections + AI grounding).

## References
`app/services/knowledge/service.py`, `app/services/knowledge/gate.py`,
`app/services/knowledge/adapters/*`, `tests/test_knowledge_graph.py`, ADR-050.
