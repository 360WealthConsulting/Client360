"""Enterprise Knowledge Graph & Explainable Relationship Layer (Phase D.45).

A governed SEMANTIC COMPOSITION over the platform's authoritative entities and the existing relationship
engine — it connects people, households, businesses, trusts/estates, professionals, advisors, and bounded
per-domain collections into one explainable graph. It is NOT a graph database, NOT RDF/SPARQL, and NOT a
second relationship engine: nodes/edges are composed read-only from the authoritative services
(``relationships.build_relationship_graph``, ``record_assignments``, ``get_client_snapshot``, the D.44
engagement summary), scope-enforced, cycle-safe, bounded, and every edge is explainable with its
authoritative owner + evidence + deep link.
"""
from .service import (
    explain_relationship,
    knowledge_graph,
    knowledge_summary,
    search_entities,
    traverse,
)

__all__ = ["knowledge_graph", "traverse", "explain_relationship", "search_entities", "knowledge_summary"]
