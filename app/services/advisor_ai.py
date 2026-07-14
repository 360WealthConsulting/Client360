from typing import Any, Mapping, Optional


def build_advisor_recommendations(
    summary: Mapping[str, Any],
    relationship_graph: Optional[Mapping[str, Any]] = None,
    portfolio: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Build deterministic next-step guidance from a client summary."""
    recommendations: list[str] = []
    portfolio_keys = {"cash_percent", "largest_position_percent", "accounts_requiring_beneficiary"}
    if portfolio is None and relationship_graph and portfolio_keys.intersection(relationship_graph):
        portfolio = relationship_graph
        relationship_graph = None
    portfolio = portfolio or {}

    if portfolio.get("cash_percent", 0) >= 15:
        recommendations.append("Review excessive cash waiting to be invested.")
    if portfolio.get("largest_position_percent", 0) >= 25:
        recommendations.append("Review concentrated stock exposure over 25%.")
    if portfolio.get("high_unrealized_gains"):
        recommendations.append("Evaluate tax-efficient diversification of high unrealized gains.")
    if portfolio.get("tax_loss_candidates"):
        recommendations.append("Review tax-loss harvesting opportunities.")
    if portfolio.get("rmd_candidate"):
        recommendations.append("Confirm the upcoming required minimum distribution plan.")
    if portfolio.get("roth_conversion_candidate"):
        recommendations.append("Evaluate a Roth conversion opportunity.")
    if portfolio.get("accounts_requiring_beneficiary", 0) > portfolio.get("beneficiary_count", 0):
        recommendations.append("Complete the required beneficiary review.")

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
