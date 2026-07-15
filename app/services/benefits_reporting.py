"""Benefits dashboards & reporting (Release 0.9.11, Phase 8 — ADR-18).

Proportional, decision-oriented metrics for the Employer-Operations MVP — a book view, a
compliance/renewal calendar (upcoming + overdue obligations), participation, and the benefits
exception metrics (reusing the shared ``exception_reporting`` engine with ``domain='benefits'``).
No second reporting framework. Every figure is **authorization-filtered before aggregation**
(a principal only aggregates organizations they may see; ``record.read_all`` sees firm-wide) and
comes from **stored data only** — nothing about revenue, deadlines, or compliance is inferred.
No EIN / compensation / deferral / employee identity is exposed.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import false as sql_false, func, or_, select

from app.db import (benefit_employments, benefit_enrollments, benefit_obligations,
    benefit_plan_types, benefit_plans, engagements, engine, record_assignments, service_lines)
from app.security.authorization import team_ids
from app.services import exception_reporting as er

UPCOMING_DAYS = 30
RENEWAL_WINDOW_DAYS = 90
COMPLIANCE_TYPES = frozenset({"form_5500", "fiduciary_review", "nondiscrimination_testing",
                              "safe_harbor_notice", "qdia_notice", "auto_enrollment_notice",
                              "fee_disclosure"})
_ACTIVE_OBLIGATION = ("scheduled", "in_progress")


def _today(now):
    return (now or datetime.now(timezone.utc)).date()


def _scoped_org_ids(principal):
    """Organizations the principal may aggregate: ``None`` = firm-wide (record.read_all),
    otherwise the set of organizations assigned to the user or their teams."""
    if principal.can("record.read_all"):
        return None
    today = date.today()
    with engine.connect() as c:
        tids = team_ids(c, principal)
        scope = or_(record_assignments.c.user_id == principal.user_id,
                    record_assignments.c.team_id.in_(tids) if tids else sql_false())
        return set(c.scalars(select(record_assignments.c.entity_id).where(
            record_assignments.c.entity_type == "organization",
            record_assignments.c.effective_date <= today,
            or_(record_assignments.c.inactive_date.is_(None), record_assignments.c.inactive_date >= today),
            scope)))


def _org_clause(column, org_ids):
    if org_ids is None:
        return None  # firm-wide
    if not org_ids:
        return sql_false()
    return column.in_(tuple(org_ids))


def benefits_report(principal, *, now=None):
    """Authorization-filtered benefits book, participation, compliance/renewal calendar, and
    exception metrics. Requires ``benefits.read``."""
    if not principal.can("benefits.read"):
        from app.services.exception_engine import ExceptionAuthorizationError
        raise ExceptionAuthorizationError("Missing capability: benefits.read")
    today = _today(now)
    org_ids = _scoped_org_ids(principal)

    with engine.connect() as c:
        def scoped(query, col):
            clause = _org_clause(col, org_ids)
            return query if clause is None else query.where(clause)

        # --- book: plans by line of coverage ---
        plan_rows = c.execute(scoped(select(benefit_plans.c.id, benefit_plans.c.status,
                                  benefit_plan_types.c.line_of_coverage.label("line"))
                           .select_from(benefit_plans.join(benefit_plan_types,
                               benefit_plan_types.c.id == benefit_plans.c.plan_type_id)),
                           benefit_plans.c.organization_id)).mappings().all()
        active_plans = [p for p in plan_rows if p["status"] in ("active", "renewing")]
        plans_by_line = dict(Counter(p["line"] for p in active_plans))

        # employers with a benefits/retirement service line
        employer_rows = c.execute(scoped(select(benefit_plans.c.organization_id).distinct(),
                                         benefit_plans.c.organization_id)).scalars()
        employers = len(set(employer_rows))

        # --- participation (stored data only) ---
        emp_rows = c.execute(scoped(select(benefit_employments.c.id, benefit_employments.c.employee_status),
                          benefit_employments.c.organization_id)).mappings().all()
        eligible = sum(1 for e in emp_rows if e["employee_status"] == "active")
        enrolled = c.scalar(scoped(
            select(func.count()).select_from(benefit_enrollments
                .join(benefit_employments, benefit_employments.c.id == benefit_enrollments.c.benefit_employment_id))
            .where(benefit_enrollments.c.status.in_(("elected", "enrolled"))),
            benefit_employments.c.organization_id)) or 0

        # --- obligations: status + compliance/renewal calendar ---
        ob_rows = c.execute(scoped(select(benefit_obligations.c.obligation_type, benefit_obligations.c.title,
                                benefit_obligations.c.due_date, benefit_obligations.c.status),
                         benefit_obligations.c.organization_id)).mappings().all()
        active_obs = [o for o in ob_rows if o["status"] in _ACTIVE_OBLIGATION]
        upcoming = sorted(({"title": o["title"], "obligation_type": o["obligation_type"],
                            "due_date": o["due_date"].isoformat()}
                           for o in active_obs if today <= o["due_date"] <= today + timedelta(days=UPCOMING_DAYS)),
                          key=lambda x: x["due_date"])
        overdue = sorted(({"title": o["title"], "obligation_type": o["obligation_type"],
                           "due_date": o["due_date"].isoformat()}
                          for o in active_obs if o["due_date"] < today),
                         key=lambda x: x["due_date"])
        compliance_calendar = sorted(({"title": o["title"], "obligation_type": o["obligation_type"],
                                       "due_date": o["due_date"].isoformat()}
                                      for o in active_obs if o["obligation_type"] in COMPLIANCE_TYPES),
                                     key=lambda x: x["due_date"])

        # --- renewals pipeline ---
        renewal_plans = sorted(({"name": p["name"], "renewal_date": p["renewal_date"].isoformat()}
                                for p in c.execute(scoped(select(benefit_plans.c.name, benefit_plans.c.renewal_date,
                                                       benefit_plans.c.status), benefit_plans.c.organization_id)).mappings()
                                if p["status"] == "active" and p["renewal_date"]
                                and p["renewal_date"] <= today + timedelta(days=RENEWAL_WINDOW_DAYS)),
                               key=lambda x: x["renewal_date"])
        renewal_engagements = c.scalar(scoped(
            select(func.count()).select_from(engagements.join(service_lines,
                service_lines.c.id == engagements.c.service_line_id))
            .where(service_lines.c.code == "benefits", engagements.c.status.notin_(("closed", "cancelled")),
                   engagements.c.engagement_type.ilike("%renewal%")),
            engagements.c.organization_id)) or 0

    report = {
        "book": {
            "employers": employers,
            "active_plans": len(active_plans),
            "plans_by_line": plans_by_line,
            "eligible_employees": eligible,
            "enrolled_employees": enrolled,
            "participation_rate": round(enrolled / eligible, 4) if eligible else None,
        },
        "obligations": {
            "by_status": dict(Counter(o["status"] for o in ob_rows)),
            "active": len(active_obs),
            "upcoming": upcoming,
            "overdue": overdue,
        },
        "compliance_calendar": compliance_calendar,
        "renewals": {"plans_renewing": renewal_plans, "open_renewal_engagements": renewal_engagements},
    }
    # reuse the shared exception reporting engine for benefits exception metrics
    report["exceptions"] = (er.exception_report(principal, domain="benefits")
                            if principal.can("exception.read") else None)
    return report
