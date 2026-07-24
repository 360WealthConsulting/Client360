"""Knowledge adapters (Phase D.45) — read-only, scope-aware, fail-closed node/edge producers over the
authoritative services. Each never mutates, never duplicates a store, never bypasses record scope, and is
independently testable in isolation.
"""
from .advisor import advisor_nodes_edges
from .domain import domain_nodes_edges
from .relationship import relationship_nodes_edges

__all__ = ["relationship_nodes_edges", "advisor_nodes_edges", "domain_nodes_edges"]
