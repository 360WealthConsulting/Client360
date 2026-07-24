"""Explainability engine (Phase D.45).

For any edge the graph produces, answers: WHY does this relationship exist, WHICH authoritative service owns
it, WHAT evidence supports it, WHICH deep link opens it, WHEN was it last updated, and IS it inferred or
authoritative. It NEVER presents an unsupported inferred relationship as authoritative — the provenance from
the edge is carried through verbatim, and the registry's declared explanation is the "why".
"""
from __future__ import annotations

from . import registry, stats
from .model import INFERRED, Explanation


def explain_edge(edge) -> Explanation:
    """Build an Explanation for one Edge. Pure; never raises for a well-formed edge."""
    rdef = registry.relationship_type(edge.relationship)
    why = rdef.explanation if rdef else f"A {edge.relationship.replace('_', ' ')} relationship."
    owner = edge.owner or (rdef.authoritative_owner if rdef else "relationships")
    if edge.provenance == INFERRED:
        evidence = "Inferred — derived from platform data; not an authoritative record."
    else:
        evidence = f"Authoritative record owned by {owner}."
        if edge.confidence is not None:
            evidence += f" Confidence {edge.confidence:g}."
    stats.note("explanations")
    return Explanation(
        relationship=edge.relationship, why=why, authoritative_service=owner, evidence=evidence,
        deep_link=edge.deep_link, last_updated=edge.last_updated, provenance=edge.provenance)


def explain_all(edges) -> list[dict]:
    return [explain_edge(e).to_dict() for e in edges]


def explanation_completeness(edges) -> float:
    """Fraction of edges whose relationship is registered (has a declared explanation). Diagnostics only."""
    if not edges:
        return 1.0
    known = sum(1 for e in edges if registry.relationship_type(e.relationship) is not None)
    return round(known / len(edges), 3)
