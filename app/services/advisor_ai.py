from typing import Any, Mapping


def build_advisor_recommendations(
    summary: Mapping[str, Any],
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

    if not recommendations:
        recommendations.append("No immediate action recommended.")

    return recommendations
