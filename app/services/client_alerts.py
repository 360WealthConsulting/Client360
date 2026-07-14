from typing import Any, Dict, List


def build_client_alerts(
    summary: Dict[str, Any],
) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []

    overdue = summary.get("overdue_task_count", 0)

    if overdue:
        alerts.append(
            {
                "level": "danger",
                "title": "Overdue tasks",
                "message": (
                    f"{overdue} overdue task"
                    f"{'' if overdue == 1 else 's'} need attention."
                ),
            }
        )

    days_since = summary.get("days_since_last_contact")

    if days_since is None:
        alerts.append(
            {
                "level": "warning",
                "title": "No recorded contact",
                "message": (
                    "There is no recorded client interaction yet."
                ),
            }
        )
    elif days_since >= 90:
        alerts.append(
            {
                "level": "danger",
                "title": "Client contact overdue",
                "message": (
                    f"No recorded client contact in {days_since} days."
                ),
            }
        )
    elif days_since >= 45:
        alerts.append(
            {
                "level": "warning",
                "title": "Follow-up may be due",
                "message": (
                    f"Last recorded contact was {days_since} days ago."
                ),
            }
        )

    if summary.get("document_count", 0) == 0:
        alerts.append(
            {
                "level": "info",
                "title": "No client documents",
                "message": (
                    "No active documents are stored for this client."
                ),
            }
        )

    if summary.get("open_task_count", 0) == 0:
        alerts.append(
            {
                "level": "success",
                "title": "No open tasks",
                "message": "There are currently no open tasks.",
            }
        )

    return alerts
