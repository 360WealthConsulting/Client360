from sqlalchemy import func, select

from app.db import (
    accounts,
    activities,
    engine,
    households,
    match_review_decisions,
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

        pending_matches = connection.scalar(
            select(func.count())
            .select_from(match_review_decisions)
            .where(match_review_decisions.c.decision == "pending")
        ) or 0

        total_aum = connection.scalar(
            select(func.coalesce(func.sum(accounts.c.total_value), 0))
        ) or 0

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
