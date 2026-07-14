from __future__ import annotations

from typing import Any, Mapping


def build_advisor_recommendations(
    summary: Mapping[str, Any],
    portfolio: Mapping[str, Any] | None = None,
) -> list[str]:
    """Build deterministic next-step guidance from a client summary."""
    recommendations: list[str] = []
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

    if not recommendations:
        recommendations.append("No immediate action recommended.")

    return recommendations
