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


def get_dashboard_data():
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

        total_aum = connection.scalar(
            select(func.coalesce(func.sum(accounts.c.total_value), 0))
        ) or 0

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
        "total_aum": total_aum,
        "recent_activities": recent_activities,
    }
    result.update(get_firm_portfolio_metrics())
    return result
