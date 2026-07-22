"""Analytics source-reading layer (Phase D.15).

The single place Analytics reads source data. It (a) composes existing principal-scoped domain
reports and (b) runs bounded, scope-filtered COUNT/SUM aggregates using the shared
``accessible_person_ids`` primitive (None = firm-wide, set = restricted, empty = zero). It
re-implements no business logic and never writes. Firm-wide (unrestricted) reads are only
reached by principals with ``record.read_all`` (executive); an advisor's numbers are their book.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.db import (
    annual_review_sessions,
    business_planning_profiles,
    campaigns,
    engine,
    households,
    people,
    referral_sources,
    relationship_entities,
    tasks,
    timeline_events,
)
from app.security.authorization import accessible_person_ids

ZERO = Decimal("0")


def book_scope(principal):
    """Resolve the principal's accessible person-id scope: None (firm-wide, read_all),
    a set (restricted), or an empty set (nothing)."""
    with engine.connect() as c:
        return accessible_person_ids(c, principal)


def _person_household_ids(c, ids):
    if not ids:
        return set()
    return set(c.scalars(select(people.c.household_id).where(
        people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))


# --- bounded scoped counts / sums --------------------------------------------

def client_count(principal) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        if ids is None:
            return c.scalar(select(func.count()).select_from(people)) or 0
        return len(ids)


def household_count(principal) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        if ids is None:
            return c.scalar(select(func.count()).select_from(households)) or 0
        return len(_person_household_ids(c, ids))


def organization_count(principal) -> int:
    """Firm business entities (organizations). Firm asset — full count (executive metric)."""
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(relationship_entities)
                        .where(relationship_entities.c.entity_type == "business")) or 0


def open_task_count(principal) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(tasks).where(tasks.c.status != "complete")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(tasks.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def timeline_activity_count(principal, *, since=None) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(timeline_events)
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(timeline_events.c.person_id.in_(tuple(ids)))
        if since is not None:
            stmt = stmt.where(timeline_events.c.event_time >= since)
        return c.scalar(stmt) or 0


def annual_review_count(principal, *, completed_only=False) -> int:
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(annual_review_sessions)
        if completed_only:
            stmt = stmt.where(annual_review_sessions.c.status == "completed")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(annual_review_sessions.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def business_plan_count(principal) -> int:
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(business_planning_profiles)) or 0


def document_count(principal) -> int:
    """Book-scoped active document count (Phase D.16 — Analytics consumes document statistics;
    Documents never depend on Analytics). Excludes soft-deleted."""
    from app.db import documents
    ids = book_scope(principal)
    with engine.connect() as c:
        stmt = select(func.count()).select_from(documents).where(documents.c.status != "deleted")
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(documents.c.person_id.in_(tuple(ids)))
        return c.scalar(stmt) or 0


def book_aum(principal) -> Decimal:
    from app.services.portfolio import book_aum as portfolio_book_aum
    return portfolio_book_aum(book_scope(principal))


def active_campaign_count(principal) -> int:
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(campaigns)
                        .where(campaigns.c.status == "active")) or 0


def active_referral_source_count(principal) -> int:
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(referral_sources)
                        .where(referral_sources.c.status == "active")) or 0


# --- composed domain reports (principal-scoped) ------------------------------

def pipeline_report(principal, *, today=None):
    from app.services.opportunity import reporting
    return reporting.pipeline_report(principal, today=today)


def forecast_report(principal):
    from app.services.opportunity import reporting
    return reporting.forecast_report(principal)


def bizdev_summary(principal):
    from app.services.bizdev import intelligence
    return intelligence.executive_summary(principal)


def campaign_report(principal):
    from app.services.campaign import reporting
    return reporting.campaign_report(principal)


def referral_report(principal):
    from app.services.referral import reporting
    return reporting.referral_report(principal)


def insurance_dashboard(principal):
    from app.services import insurance_reporting
    return insurance_reporting.operations_dashboard(principal)


def tax_dashboard(principal):
    from app.services import tax_domain
    return tax_domain.dashboard(principal)


def open_work_total(principal) -> int:
    from app.services import advisor_work
    return advisor_work.list_work(principal, page=1, page_size=1)["total"]


def open_compliance_total(principal) -> int:
    from app.services.compliance import reviews
    return reviews.list_reviews(principal, page=1, page_size=1)["total"]


def advisor_open_opportunities(principal):
    """Open opportunities grouped by primary advisor (for advisor-production dimensions)."""
    from app.services.opportunity import service as opp_svc
    rows = opp_svc.all_in_scope(principal, statuses=("open",))
    counts: dict[int, int] = {}
    for o in rows:
        counts[o["primary_advisor_id"]] = counts.get(o["primary_advisor_id"], 0) + 1
    return counts
