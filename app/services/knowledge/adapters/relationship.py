"""Relationship knowledge adapter (Phase D.45).

Composes graph nodes/edges from the AUTHORITATIVE relationship engine (``relationships.build_relationship_graph``)
— the single relationship store. It never mutates, never duplicates the graph, and never bypasses record
scope: person-type counterparts are filtered through ``accessible_person_ids`` (out-of-scope people are
suppressed, never leaked). Raw relationship codes are mapped onto the registered relationship vocabulary;
unmapped codes are dropped (and counted as orphan relationships). Fail-closed → empty on any error.
"""
from __future__ import annotations

from .. import registry, stats
from ..model import AUTHORITATIVE, Edge, Node

# Entity types the relationship engine can surface as counterpart nodes.
_ENTITY_TYPE_MAP = {"person": "person", "household": "household", "business": "business",
                    "trust": "trust", "estate": "estate", "professional": "professional",
                    "insurance_carrier": "insurance_carrier"}


def relationship_nodes_edges(principal, person_id):
    """Return (nodes, edges, suppressed) for a person's authoritative relationship graph, scope-filtered.
    Nodes/edges are registered graph types only. Never raises."""
    try:
        from app.db import engine
        from app.security.authorization import accessible_person_ids
        from app.services.relationships import build_relationship_graph
        graph = build_relationship_graph(person_id)
        with engine.connect() as conn:
            reachable = accessible_person_ids(conn, principal)   # None = firm-wide
    except Exception:
        stats.note("adapter_failures", source="relationships")
        return [], [], 0

    root_id = f"person:{person_id}"
    nodes = {root_id: Node(node_id=root_id, entity_type="person", label="Client", owner="people",
                           deep_link=registry.deep_link_for("person", person_id), visibility="both")}
    edges = []
    suppressed = 0
    for item in graph.get("relationships", []):
        raw_code = item.get("code")
        rel = registry.map_raw_relationship(raw_code)
        if rel is None:
            stats.note("orphan_relationships")
            continue
        etype = _ENTITY_TYPE_MAP.get(item.get("entity_type") or "")
        if etype is None:
            stats.note("orphan_relationships")
            continue
        counterpart_person = item.get("person_id")
        # Scope: a person counterpart the principal cannot access is suppressed (never leaked).
        if etype == "person" and reachable is not None and counterpart_person not in reachable:
            suppressed += 1
            stats.note("hidden_suppressed")
            continue
        # Node identity: people/households by their id; named entities by relationship_entities id.
        if etype == "person":
            target_id = f"person:{counterpart_person}"
            link = registry.deep_link_for("person", counterpart_person)
        elif etype == "household":
            target_id = f"household:{item.get('household_id')}"
            link = registry.deep_link_for("household", item.get("household_id"))
        else:
            target_id = f"{etype}:{item.get('entity_id')}"
            link = registry.deep_link_for(etype, item.get("entity_id"))
        edef = registry.entity_type(etype)
        nodes.setdefault(target_id, Node(
            node_id=target_id, entity_type=etype, label=item.get("name") or etype.title(),
            owner=edef.owner if edef else "relationships", deep_link=link,
            visibility=edef.visibility if edef else "internal",
            metadata={"category": raw_code}))
        rdef = registry.relationship_type(rel)
        try:
            conf = float(item.get("confidence_level")) if item.get("confidence_level") is not None else None
        except (TypeError, ValueError):
            conf = None
        edges.append(Edge(
            source_id=root_id, target_id=target_id, relationship=rel,
            label=item.get("label") or rel.replace("_", " ").title(),
            owner=rdef.authoritative_owner if rdef else "relationships",
            provenance=AUTHORITATIVE, visibility=rdef.visibility if rdef else "internal",
            confidence=conf, deep_link=link))
        stats.note("registry_lookups", edge_type=rel)
    return list(nodes.values()), edges, suppressed
