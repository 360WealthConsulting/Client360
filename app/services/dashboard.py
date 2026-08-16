from sqlalchemy import func, select

from app.db import (
    accounts,
    activities,
    engine,
    households,
    people,
    tasks,
)
from app.services.portfolio import get_firm_portfolio_metrics

# Firm-wide financial aggregates (firm AUM, total account value, cash waiting, largest household /
# position, and their sibling firm-wide portfolio counts) are privileged firm-level information. They are
# gated on this dedicated capability — NOT on record.read_all — so firm-metric visibility is independently
# controllable (an employee does not see firm AUM merely because they can read all records).
FIRM_METRICS_CAPABILITY = "portfolio.firm_metrics"


def can_view_firm_metrics(principal) -> bool:
    return principal is not None and principal.can(FIRM_METRICS_CAPABILITY)


def get_dashboard_data(principal=None):
    """Operational dashboard data for everyone; firm-wide financial aggregates ONLY for a principal that
    holds ``portfolio.firm_metrics``. When unauthorized, the sensitive metrics are neither computed nor
    returned (fail-safe: a missing/insufficient principal yields no firm metrics) — they cannot leak via
    the template context or the /api/stats JSON."""
    with engine.connect() as connection:
        total_people = connection.scalar(
            select(func.count()).select_from(people)
        )

        total_households = connection.scalar(
            select(func.count()).select_from(households)
        )

        total_accounts = connection.scalar(
            select(func.count()).select_from(accounts)
        )

        open_tasks = connection.scalar(
            select(func.count())
            .select_from(tasks)
            .where(tasks.c.status != "complete")
        )

        recent_activities = connection.execute(
            select(activities)
            .order_by(
                activities.c.occurred_at.desc(),
                activities.c.id.desc(),
            )
            .limit(10)
        ).mappings().all()

    # Real backlog of duplicate-match review groups awaiting a decision. The
    # prior query counted a decision value ("pending") that is never persisted,
    # so it was always zero (H14). Imported locally to avoid a service->route
    # module-load dependency.
    from app.routes.matches import count_pending_match_groups
    pending_matches = count_pending_match_groups()

    result = {
        "people": total_people,
        "households": total_households,
        "accounts": total_accounts,
        "open_tasks": open_tasks,
        "pending_matches": pending_matches,
        "recent_activities": recent_activities,
    }
    if can_view_firm_metrics(principal):
        with engine.connect() as connection:
            total_aum = connection.scalar(
                select(func.coalesce(func.sum(accounts.c.total_value), 0))
            ) or 0
        result["total_aum"] = total_aum
        result.update(get_firm_portfolio_metrics())
    return result
