# Knowledge Governance (Phase D.45)

`app/services/knowledge/governance.py` is a read-only checker that verifies the Enterprise Knowledge Graph
stays a **semantic composition** over the authoritative models and never becomes a graph database or a second
relationship engine. It returns `{ok, issue_count, findings}` and never raises into normal use. See
[`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).

## Invariants enforced
1. **No graph database / RDF / SPARQL.** No knowledge module imports `neo4j`, `rdflib`, `sparql`, `gremlin`,
   or `networkx`.
2. **No second store / no writes.** No module defines a table (`Table(` / `define_*_tables`) or writes the
   DB (`insert`/`update`/`delete`).
3. **No second event bus / audit.** No module publishes to the outbox or writes audit events.
4. **No direct projection reads.** No module reads `rm_*` tables directly.
5. **Composes the authoritative relationship engine.** The relationship adapter reuses
   `build_relationship_graph` (no second relationship engine); the composition reuses the authoritative
   scoped reads (`record_assignments`, `get_client_snapshot`, the D.44 engagement summary).
6. **Registry completeness + single ownership.** Every entity + relationship type is fully declared; no
   duplicate entity/relationship keys; every raw-code mapping targets a registered relationship.
7. **Bounded traversal.** `DEPTH_LIMIT` is declared and integer.
8. **Governed gating.** Every gate is a runtime flag in the `GATES` registry; no raw environment fallback.

The checker excludes `governance.py` from its own source scan (it holds the detection string-literals).

## Additional guarantees proven by tests
- **No hidden-entity leakage** — out-of-scope person counterparts are suppressed (`hidden_suppressed`);
  an out-of-scope root returns `None` (404).
- **No unrestricted traversal** — depth is bounded, results capped, cycles avoided.
- **No policy/runtime bypass** — traversal/search compose `policy.evaluate` alongside RBAC; an explicit deny
  is honored; gates are runtime-governed.
- **No mutation** — every module is read-only.
- **Explainability** — every edge is authoritative-or-explicitly-inferred; inferred is never presented as
  authoritative.

## How it runs
`validate_knowledge()` returns `{ok, issue_count, findings}`, surfaced through the internal diagnostics
(`app/services/knowledge/diagnostics.py`) on the `observability.audit` surface (`GET
/knowledge/diagnostics`) and asserted clean by `tests/test_knowledge_graph.py::test_governance_clean`.

## Diagnostics & analytics
`knowledge_diagnostics()` composes gate snapshot + in-process counters (low-cardinality — no client
relationship contents, ids, or evidence) + registry coverage + adapter availability + governance:
relationship counts by type, adapter failures by source, traversal depth distribution, hidden-node
suppression, orphan relationships, cycles avoided, average traversal latency. Four low-cardinality metrics
(`knowledge_traversals`, `knowledge_explanations`, `knowledge_searches`, `knowledge_adapter_failures`) are
registered in the platform Analytics registry.

## Observability
Following the platform's established instrumentation pattern (there is no span/trace API), the layer
instruments traversal, search, explanation generation, registry lookups, and adapter failures with an
in-process counter module (`stats.py`) and surfaces them via Analytics + diagnostics. It never logs client
relationship contents.

## References
`app/services/knowledge/governance.py`, `app/services/knowledge/diagnostics.py`,
`app/services/knowledge/stats.py`, `app/services/analytics/{sources,metrics}.py`,
`tests/test_knowledge_graph.py`, ADR-050.
