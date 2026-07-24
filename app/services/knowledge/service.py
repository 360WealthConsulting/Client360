"""Enterprise Knowledge Graph composition service (Phase D.45).

Composes the read-only knowledge adapters into a bounded, explainable, scope-enforced semantic graph over
the authoritative models — WITHOUT a graph database and WITHOUT a second relationship engine. Every public
entry is gate-aware (Runtime Engine), policy-aware (composes ``policy.evaluate`` alongside RBAC), and returns
``None`` when out of scope so the route emits 404. Nothing here mutates.
"""
from __future__ import annotations

import time

from . import gate, registry, stats
from .adapters import advisor_nodes_edges, domain_nodes_edges, relationship_nodes_edges
from .explain import explain_all, explanation_completeness
from .model import graph_dict

DEPTH_LIMIT = 2          # bounded traversal: root → entity → (one further hop max)
_MAX_SEARCH = 100


def _in_person_scope(principal, person_id):
    try:
        from app.security.authorization import record_in_scope
        return record_in_scope(principal, "person", person_id)
    except Exception:
        return False


def _in_household_scope(principal, household_id):
    try:
        from app.security.authorization import record_in_scope
        return record_in_scope(principal, "household", household_id)
    except Exception:
        return False


def _compose_person(principal, person_id):
    """Assemble the bounded, scope-filtered node/edge set for a person (root + one hop + collections)."""
    rel_nodes, rel_edges, suppressed = relationship_nodes_edges(principal, person_id)
    adv_nodes, adv_edges = advisor_nodes_edges(person_id)
    dom_nodes, dom_edges = domain_nodes_edges(principal, person_id)
    # Dedup nodes by id; edges by (source, target, relationship) — cycle/dup protection.
    nodes, node_ids = [], set()
    for n in rel_nodes + adv_nodes + dom_nodes:
        if n.node_id not in node_ids:
            node_ids.add(n.node_id)
            nodes.append(n)
    edges, edge_keys = [], set()
    for e in rel_edges + adv_edges + dom_edges:
        if e.source_id == e.target_id:      # self-loop protection
            stats.note("cycles_avoided")
            continue
        if e.edge_key in edge_keys:
            continue
        edge_keys.add(e.edge_key)
        edges.append(e)
    return nodes, edges, suppressed


def knowledge_graph(principal, *, person_id=None, household_id=None):
    """The bounded, explainable knowledge graph for a person or household. Returns None when out of scope;
    a disabled envelope when the gate is off."""
    if not gate.enabled():
        return {"enabled": False, "nodes": [], "edges": [], "explanations": []}
    t0 = time.monotonic()
    if person_id is not None:
        if not _in_person_scope(principal, person_id):
            return None
        nodes, edges, suppressed = _compose_person(principal, person_id)
        root_id = f"person:{person_id}"
    elif household_id is not None:
        if not _in_household_scope(principal, household_id):
            return None
        nodes, edges, suppressed = _compose_household(principal, household_id)
        root_id = f"household:{household_id}"
    else:
        return None
    out = graph_dict(root_id, nodes, edges, suppressed=suppressed, depth_limit=DEPTH_LIMIT)
    out["enabled"] = True
    out["explanations"] = explain_all(edges) if gate.gate("explain.enabled") else []
    stats.note("graphs_composed", depth=DEPTH_LIMIT)
    stats.note_ms((time.monotonic() - t0) * 1000)
    return out


def _compose_household(principal, household_id):
    """Household graph — REUSES the authoritative household member resolution + each member's relationship
    graph, deduped, one hop per member (mirrors the D.41 household composition; never a second store)."""
    try:
        from sqlalchemy import select

        from app.db import engine, people
        with engine.connect() as conn:
            member_ids = list(conn.scalars(select(people.c.id).where(people.c.household_id == household_id)))
    except Exception:
        stats.note("adapter_failures", source="household_members")
        member_ids = []
    nodes, node_ids = [], set()
    edges, edge_keys = [], set()
    hroot = f"household:{household_id}"
    from .model import Node
    nodes.append(Node(node_id=hroot, entity_type="household", label="Household", owner="households",
                      deep_link=registry.deep_link_for("household", household_id), visibility="both"))
    node_ids.add(hroot)
    suppressed = 0
    for pid in member_ids:
        pnodes, pedges, psup = _compose_person(principal, pid)
        suppressed += psup
        for n in pnodes:
            if n.node_id not in node_ids:
                node_ids.add(n.node_id)
                nodes.append(n)
        # link the household to each member
        from .model import AUTHORITATIVE, Edge
        me = Edge(source_id=hroot, target_id=f"person:{pid}", relationship="member_of",
                  label="Household member", owner="households", provenance=AUTHORITATIVE, visibility="both")
        for e in [me] + pedges:
            if e.source_id == e.target_id or e.edge_key in edge_keys:
                continue
            edge_keys.add(e.edge_key)
            edges.append(e)
    return nodes, edges, suppressed


def traverse(principal, *, person_id=None, household_id=None, target_type=None, depth=1):
    """Bounded, cycle-safe traversal producing explainable paths from the root to targets (optionally
    filtered to a target entity type). Depth is clamped to DEPTH_LIMIT. Returns None out of scope."""
    if not gate.enabled():
        return {"enabled": False, "paths": []}
    if not gate.policy_ok("traverse"):
        return {"enabled": True, "paths": [], "denied": "policy"}
    depth = max(1, min(int(depth or 1), DEPTH_LIMIT))
    graph = knowledge_graph(principal, person_id=person_id, household_id=household_id)
    if graph is None:
        return None
    if not graph.get("enabled"):
        return {"enabled": False, "paths": []}
    root_id = graph["root_id"]
    paths = []
    for e in graph["edges"]:
        if e["source_id"] != root_id:
            continue
        if target_type:
            tnode = next((n for n in graph["nodes"] if n["node_id"] == e["target_id"]), None)
            if not tnode or tnode["entity_type"] != target_type:
                continue
        paths.append({"nodes": [e["source_id"], e["target_id"]],
                      "edges": [e], "depth": 1})
    stats.note("traversals", depth=depth)
    return {"enabled": True, "root_id": root_id, "paths": paths[:200], "depth_limit": DEPTH_LIMIT}


def explain_relationship(principal, *, person_id=None, household_id=None, target_id=None,
                         relationship=None):
    """Explain one edge from the root to a target. Scope-checked via the graph composition. 404 → None."""
    if not gate.enabled() or not gate.gate("explain.enabled"):
        return {"enabled": False, "explanation": None}
    graph = knowledge_graph(principal, person_id=person_id, household_id=household_id)
    if graph is None:
        return None
    match = None
    for exp, e in zip(graph.get("explanations", []), graph.get("edges", []), strict=False):
        if (target_id is None or e["target_id"] == target_id) and \
           (relationship is None or e["relationship"] == relationship):
            match = exp
            break
    return {"enabled": True, "explanation": match}


def search_entities(principal, *, person_id=None, household_id=None, query=None, entity_type=None,
                    relationship=None, owner=None, visibility=None):
    """Semantic search over the REGISTERED entity nodes reachable from the (scoped) graph. Never searches
    hidden/out-of-scope entities — it only filters the already-scope-composed node set. Gate-aware."""
    if not gate.enabled() or not gate.gate("knowledge.search.enabled"):
        return {"enabled": False, "results": []}
    if not gate.policy_ok("search"):
        return {"enabled": True, "results": [], "denied": "policy"}
    graph = knowledge_graph(principal, person_id=person_id, household_id=household_id)
    if graph is None:
        return None
    q = (query or "").strip().lower()
    rel_targets = None
    if relationship:
        rel_targets = {e["target_id"] for e in graph.get("edges", []) if e["relationship"] == relationship}
    results = []
    for n in graph.get("nodes", []):
        if entity_type and n["entity_type"] != entity_type:
            continue
        if owner and n["owner"] != owner:
            continue
        if visibility and n["visibility"] not in (visibility, "both"):
            continue
        if rel_targets is not None and n["node_id"] not in rel_targets:
            continue
        if q and q not in (n["label"] or "").lower() and q not in n["entity_type"]:
            continue
        results.append(n)
    stats.note("searches")
    return {"enabled": True, "results": results[:_MAX_SEARCH], "total": len(results)}


def knowledge_summary(principal, *, person_id=None, household_id=None):
    """Compact summary for the Client 360 / Household 360 sections and (through them) AI grounding — counts
    of connected entities by type + explanation completeness. Never raises; counts only."""
    if not gate.enabled():
        return {"enabled": False, "connected": 0, "by_type": {}, "explanation_completeness": None}
    graph = knowledge_graph(principal, person_id=person_id, household_id=household_id)
    if graph is None or not graph.get("enabled"):
        return {"enabled": True, "connected": 0, "by_type": {}, "explanation_completeness": None}
    by_type = {}
    for n in graph["nodes"]:
        if n["node_id"] == graph["root_id"]:
            continue
        by_type[n["entity_type"]] = by_type.get(n["entity_type"], 0) + 1
    from .model import Edge

    # rebuild Edge objects only to score completeness deterministically (edges already dicts).
    completeness = None
    try:
        edges = [Edge(source_id=e["source_id"], target_id=e["target_id"], relationship=e["relationship"],
                      label=e["label"], owner=e["owner"], provenance=e["provenance"]) for e in graph["edges"]]
        completeness = explanation_completeness(edges)
    except Exception:
        completeness = None
    return {"enabled": True, "connected": sum(by_type.values()), "by_type": by_type,
            "edge_count": graph["edge_count"], "suppressed_nodes": graph["suppressed_nodes"],
            "explanation_completeness": completeness}
