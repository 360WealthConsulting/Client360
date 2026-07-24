"""Enterprise Knowledge Graph read-models (Phase D.45).

Normalized, read-only projections of graph nodes, edges, paths, and explanations. Every node/edge REFERENCES
its authoritative record (owner service + deep link) and never copies domain content. This is a semantic
layer over the authoritative models — not a graph database, not a second store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Visibility (mirrors the registry vocabulary).
INTERNAL = "internal"      # advisor/staff only
EXTERNAL = "external"      # client-appropriate
BOTH = "both"

# Evidence provenance.
AUTHORITATIVE = "authoritative"   # a real edge/record owned by a service
INFERRED = "inferred"             # derived; must never be presented as authoritative


@dataclass(frozen=True)
class Node:
    node_id: str               # stable, source-qualified (e.g. "person:12", "collection:insurance:12")
    entity_type: str           # registered entity type key
    label: str                 # display name (safe — no sensitive content)
    owner: str                 # authoritative owning service
    deep_link: str | None
    visibility: str = INTERNAL
    count: int | None = None   # for collection nodes (e.g. "3 policies")
    lifecycle: str = "active"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"node_id": self.node_id, "entity_type": self.entity_type, "label": self.label,
                "owner": self.owner, "deep_link": self.deep_link, "visibility": self.visibility,
                "count": self.count, "lifecycle": self.lifecycle, "metadata": self.metadata}


@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    relationship: str          # registered relationship type code
    label: str
    owner: str                 # authoritative owner of the edge
    provenance: str = AUTHORITATIVE   # authoritative | inferred
    visibility: str = INTERNAL
    confidence: float | None = None
    deep_link: str | None = None
    last_updated: datetime | None = None

    @property
    def edge_key(self):
        return (self.source_id, self.target_id, self.relationship)

    def to_dict(self) -> dict:
        return {"source_id": self.source_id, "target_id": self.target_id, "relationship": self.relationship,
                "label": self.label, "owner": self.owner, "provenance": self.provenance,
                "visibility": self.visibility, "confidence": self.confidence, "deep_link": self.deep_link,
                "last_updated": self.last_updated.isoformat() if self.last_updated else None}


@dataclass(frozen=True)
class Explanation:
    """Answers, for one edge: why it exists, who owns it, the evidence, the deep link, when it changed, and
    whether it is inferred or authoritative. Never presents an inferred relationship as authoritative."""
    relationship: str
    why: str
    authoritative_service: str
    evidence: str
    deep_link: str | None
    last_updated: datetime | None
    provenance: str            # authoritative | inferred

    def to_dict(self) -> dict:
        return {"relationship": self.relationship, "why": self.why,
                "authoritative_service": self.authoritative_service, "evidence": self.evidence,
                "deep_link": self.deep_link,
                "last_updated": self.last_updated.isoformat() if self.last_updated else None,
                "provenance": self.provenance, "inferred": self.provenance == INFERRED}


@dataclass(frozen=True)
class Path:
    """A bounded, explainable traversal path from a root to a target."""
    nodes: tuple[str, ...]
    edges: tuple[Edge, ...]
    depth: int

    def to_dict(self) -> dict:
        return {"nodes": list(self.nodes), "edges": [e.to_dict() for e in self.edges], "depth": self.depth}


def graph_dict(root_id, nodes, edges, *, suppressed=0, depth_limit=1) -> dict:
    return {
        "root_id": root_id,
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "suppressed_nodes": suppressed,
        "depth_limit": depth_limit,
        "cycle_protection": True,
    }
