"""Domain-collection knowledge adapter (Phase D.45).

Exposes BOUNDED per-domain "collection" nodes (Accounts, Insurance, Tax, Communications, Work) — each a
count + deep link, never an individual hidden record. It composes over authoritative per-person aggregations
(``get_client_snapshot`` — the same read the Client 360 summary uses — and the D.44 engagement summary), so
it never bypasses record scope and never enumerates individual records. Read-only, fail-closed. Collections
with no records are omitted (no orphan nodes).
"""
from __future__ import annotations

from .. import registry, stats
from ..model import AUTHORITATIVE, Edge, Node

# collection entity key → (relationship code, label)
_DOMAIN_EDGES = {
    "accounts": ("has_accounts", "Holds accounts"),
    "insurance": ("insured_by", "Insured"),
    "tax": ("has_tax_returns", "Has tax returns"),
    "work": ("has_work", "Has open work"),
    "communications": ("communicated", "Has communications"),
}


def _collection_node(entity_key, count):
    edef = registry.entity_type(entity_key)
    rel, label = _DOMAIN_EDGES[entity_key]
    node = Node(node_id=f"collection:{entity_key}", entity_type=entity_key,
                label=f"{entity_key.title()} ({count})", owner=edef.owner,
                deep_link=edef.deep_link, visibility=edef.visibility, count=count)
    edge = Edge(source_id="__root__", target_id=node.node_id, relationship=rel, label=label,
                owner=edef.owner, provenance=AUTHORITATIVE, visibility=edef.visibility,
                deep_link=edef.deep_link)
    return node, edge


def domain_nodes_edges(principal, person_id, household_id=None):
    """Return (nodes, edges) of bounded domain-collection nodes for a person. Never raises. Caller has
    already verified the person is in record scope."""
    root_id = f"person:{person_id}" if person_id is not None else f"household:{household_id}"
    counts = {}
    try:
        from app.services.advisor_workspace import get_client_snapshot
        snap = get_client_snapshot(person_id, household_id) if person_id is not None else {}
        if (snap.get("aum") or 0) > 0:
            counts["accounts"] = None   # count of accounts not exposed here — presence + deep link only
        ins = snap.get("insurance") or {}
        if (ins.get("policy_count") or 0) > 0:
            counts["insurance"] = ins["policy_count"]
        tax = snap.get("tax") or {}
        if (tax.get("active") or 0) > 0:
            counts["tax"] = tax["active"]
        work = (snap.get("open_tasks") or 0) + (snap.get("open_exceptions") or 0)
        if work > 0:
            counts["work"] = work
    except Exception:
        stats.note("adapter_failures", source="client_snapshot")
    # Communications volume — the D.44 engagement summary (already scoped + composed).
    try:
        from app.services.communications.engagement import engagement_summary
        summ = engagement_summary(principal, person_id=person_id, household_id=household_id)
        if summ.get("enabled") and (summ.get("total") or 0) > 0:
            counts["communications"] = summ["total"]
    except Exception:
        stats.note("adapter_failures", source="engagement_summary")

    nodes, edges = [], []
    for entity_key, count in counts.items():
        node, edge = _collection_node(entity_key, count)
        node = Node(**{**node.__dict__})   # copy
        edge = Edge(source_id=root_id, target_id=edge.target_id, relationship=edge.relationship,
                    label=edge.label, owner=edge.owner, provenance=AUTHORITATIVE,
                    visibility=edge.visibility, deep_link=edge.deep_link)
        nodes.append(node)
        edges.append(edge)
        stats.note("registry_lookups", edge_type=edge.relationship)
    return nodes, edges
