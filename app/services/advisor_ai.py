from typing import Any, Mapping, Optional


def build_advisor_recommendations(
    summary: Mapping[str, Any],
    relationship_graph: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Build deterministic next-step guidance from a client summary."""
    recommendations: list[str] = []

    if summary.get("overdue_task_count", 0) > 0:
        recommendations.append(
            "Complete overdue tasks before the next client interaction."
        )

    days = summary.get("days_since_last_contact")

    if days is not None and days >= 60:
        recommendations.append(
            "Schedule a proactive client review."
        )

    if summary.get("document_count", 0) == 0:
        recommendations.append(
            "Request important client documents."
        )

    if summary.get("activity_count", 0) == 0:
        recommendations.append(
            "Record the first client interaction."
        )

    graph = relationship_graph or {}
    codes = set(graph.get("codes", set()))
    relationships = graph.get("relationships", [])

    if "cpa" not in codes:
        recommendations.append("Record the client's CPA relationship.")

    if "spouse" in codes and "beneficiary" not in codes:
        recommendations.append(
            "Review whether the spouse should be included in the estate plan."
        )

    if "child" in codes and "beneficiary" not in codes:
        recommendations.append("Review missing child beneficiary designations.")

    if "owner" in codes and "buy_sell_agreement" not in codes:
        recommendations.append(
            "Review the business owner's buy-sell agreement coverage."
        )

    has_trust = any(
        item.get("entity_type") == "trust" for item in relationships
    )
    if has_trust and "successor_trustee" not in codes:
        recommendations.append("Record a successor trustee for the trust.")

    if not recommendations:
        recommendations.append("No immediate action recommended.")

    return recommendations
