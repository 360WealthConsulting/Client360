"""Advisor knowledge adapter (Phase D.45).

Composes the client→advisor edge from the AUTHORITATIVE ``record_assignments`` table (the single source of
record ownership). Read-only, fail-closed. Advisor nodes are internal-only (staff), terminal (never
traversed onward). Never mutates.
"""
from __future__ import annotations

from datetime import date

from .. import registry, stats
from ..model import AUTHORITATIVE, Edge, Node


def advisor_nodes_edges(person_id):
    """Return (nodes, edges) for the staff advisors assigned to a person. Never raises."""
    try:
        from sqlalchemy import or_, select

        from app.db import engine, record_assignments, users
        today = date.today()
        with engine.connect() as conn:
            rows = conn.execute(
                select(users.c.id, users.c.display_name, record_assignments.c.assignment_type)
                .select_from(record_assignments.join(users, users.c.id == record_assignments.c.user_id))
                .where(record_assignments.c.entity_type == "person",
                       record_assignments.c.entity_id == person_id,
                       record_assignments.c.user_id.isnot(None),
                       record_assignments.c.effective_date <= today,
                       or_(record_assignments.c.inactive_date.is_(None),
                           record_assignments.c.inactive_date >= today))
            ).mappings().all()
    except Exception:
        stats.note("adapter_failures", source="record_assignments")
        return [], []

    root_id = f"person:{person_id}"
    nodes, edges, seen = [], [], set()
    for r in rows:
        aid = f"advisor:{r['id']}"
        if aid in seen:
            continue
        seen.add(aid)
        nodes.append(Node(node_id=aid, entity_type="advisor", label=r["display_name"] or "Advisor",
                          owner="identity", deep_link=registry.deep_link_for("advisor", r["id"]),
                          visibility="internal", metadata={"assignment_type": r["assignment_type"]}))
        edges.append(Edge(source_id=root_id, target_id=aid, relationship="advised_by",
                          label=f"Advised by ({r['assignment_type']})", owner="identity",
                          provenance=AUTHORITATIVE, visibility="internal"))
        stats.note("registry_lookups", edge_type="advised_by")
    return nodes, edges
