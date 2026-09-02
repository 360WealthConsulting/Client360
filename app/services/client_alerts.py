from typing import Any, Dict, List

#: Alerts that report an ABSENCE of data rather than work to be done. They are useful on a
#: data-quality surface, but they are not "open work": a client with no logged calls yet has
#: nothing for staff to action, and listing three of them as alert rows on the profile buried
#: the one row that WOULD have been actionable. Consumers that render actionable work call
#: :func:`actionable_alerts`; the full list stays available for surfaces that want it.
DATA_COMPLETENESS_ALERTS = frozenset({
    "no_contact_recorded",
    "no_documents_recorded",
    "no_open_tasks",
})


def actionable_alerts(alerts):
    """The subset of alerts that describe work, not missing data."""
    return [a for a in alerts if a.get("kind") not in DATA_COMPLETENESS_ALERTS]



def build_client_alerts(
    summary: Dict[str, Any],
) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []

    overdue = summary.get("overdue_task_count", 0)

    if overdue:
        alerts.append(
            {
                "level": "danger",
                "kind": "overdue_tasks",
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
                "kind": "no_contact_recorded",
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
                "kind": "contact_overdue",
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
                "kind": "follow_up_due",
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
                "kind": "no_documents_recorded",
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
                "kind": "no_open_tasks",
                "title": "No open tasks",
                "message": "There are currently no open tasks.",
            }
        )

    return alerts
