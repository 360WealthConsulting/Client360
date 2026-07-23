"""Benefits & retirement exception detectors (Release 0.9.11, Phase 3 — ADR-18).

Detectors translate **stored** Client360 benefits data (organizations, service lines,
engagements, plans, plan years, employments, enrollments, retirement elections/details,
document links) into platform Exception Engine records with ``domain='benefits'``. They
reuse the exact detector / stable-dedupe / reconcile / auto-resolve / reopen contract used
by the tax detectors — **no second exception engine, state machine, or event log.**

Each active detector uses an approved seeded exception type, a deterministic stable dedupe
key, and anchors the Organization via ``related_entity_type='organization'`` /
``related_entity_id`` (plus the employee ``person``/``household`` for employee-level items),
so Phase-2 Organization record-scope authorization applies. When a source condition clears,
the matching open exception auto-resolves (open→in_progress→resolved) so recurrence reopens.

Titles/metadata are **generic and non-sensitive** (no names, SSNs, deferral %, comp, or
balances). Several requested detectors have **no reliable stored field** yet — those are
documented in ``DETECTOR_GAPS`` and are **inert** (never inferred). Disabled-integration
exception types stay seeded but inert until real integrations exist.

Tax domain is untouched. No compliance calendar, scheduled notifications, UI, portal, or
Work Management queues here (later phases).
"""
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app import config
from app.db import (
    benefit_document_links,
    benefit_employments,
    benefit_enrollments,
    benefit_obligations,
    benefit_plan_types,
    benefit_plan_years,
    benefit_plans,
    benefit_retirement_elections,
    benefit_retirement_plan_details,
    engagements,
    engine,
    exceptions,
    organization_profiles,
    organization_service_lines,
    people,
    service_lines,
)
from app.services import exception_engine as ee


def _cfg_days(key: str, fallback: int) -> int:
    """(D.30) Resolve a benefits day-window through the runtime engine, falling back to the legacy
    ``app.config`` value — behavior-preserving: with no runtime config item defined, the env-backed
    default is used, so detector behavior is unchanged. Resolved once per detector (not per row)."""
    from app.services.runtime import consumption
    val = consumption.config_value(f"benefits.{key}", default=fallback)
    try:
        return int(val)
    except (TypeError, ValueError):
        return fallback

# Obligation type -> approved benefits exception code (date-driven detector). Types absent
# from this map are intentionally inert — notably contribution_deposit_review, which stays
# inactive without reliable payroll/deposit data (never inferred).
OBLIGATION_EXCEPTION_CODE = {
    "form_5500": "BEN_5500_FILING_DUE",
    "fiduciary_review": "BEN_FIDUCIARY_REVIEW_DUE",
    "nondiscrimination_testing": "BEN_NONDISCRIMINATION_TEST_DUE",
    "safe_harbor_notice": "BEN_ANNUAL_NOTICE_MISSING",
    "qdia_notice": "BEN_ANNUAL_NOTICE_MISSING",
    "auto_enrollment_notice": "BEN_ANNUAL_NOTICE_MISSING",
    "fee_disclosure": "BEN_ANNUAL_NOTICE_MISSING",
    "plan_amendment": "BEN_PLAN_AMENDMENT_REQUIRED",
    "restatement": "BEN_PLAN_AMENDMENT_REQUIRED",
    "renewal": "BEN_RENEWAL_AT_RISK",
    "renewal_identified": "BEN_RENEWAL_AT_RISK",
    "marketing_begins": "BEN_RENEWAL_AT_RISK",
    "quotes_due": "BEN_RENEWAL_AT_RISK",
    "recommendation_due": "BEN_RENEWAL_AT_RISK",
    "employer_decision": "BEN_RENEWAL_AT_RISK",
    "submission_due": "BEN_RENEWAL_AT_RISK",
    "effective_date": "BEN_RENEWAL_AT_RISK",
    "census_requested": "BEN_CENSUS_OVERDUE",
    "census_due": "BEN_CENSUS_OVERDUE",
    "open_enrollment_begins": "BEN_OPEN_ENROLLMENT_INCOMPLETE",
    "open_enrollment_ends": "BEN_OPEN_ENROLLMENT_INCOMPLETE",
    "spd_delivery": "BEN_SPD_MISSING",
    "sbc_delivery": "BEN_SBC_MISSING",
}

_ACTIVE_ENROLLED = ("eligible", "elected", "enrolled")
_HANDLED_ENROLLED = ("elected", "enrolled", "waived")
_OPEN_YEAR = ("open_enrollment", "active")
_CLOSED = ("resolved", "cancelled")

# Requested detectors with no reliable stored field yet — documented, NOT inferred.
DETECTOR_GAPS = {
    "qualifying_event_pending": "No stored 'pending QLE' state; Phase-2 QLE elects coverage "
        "directly. Needs a QLE-intake record with a pending status.",
    "census_headcount_mismatch": "No stored census headcount; a received census is only a "
        "document link. Needs an extracted/entered census employee count to compare to enrollments.",
    "annual_fiduciary_review_due": "No stored last-fiduciary-review date on the plan. Date-driven; "
        "belongs to the Phase-4 compliance calendar or needs a review-date field.",
    "nondiscrimination_testing_due": "No stored testing deadline / per-plan-year testing status. "
        "Date-driven; Phase-4 compliance calendar.",
    "contribution_deposit_late": "No stored contribution deposit dates/schedule; timeliness depends on "
        "payroll data, which is a disabled integration — must not be inferred.",
    "required_annual_notice_missing": "No stored notice-sent tracking / notice calendar. Date-driven; "
        "Phase-4 compliance calendar.",
    "plan_amendment_cycle_required": "No stored restatement/amendment-cycle trigger date. Date-driven; "
        "Phase-4 compliance calendar. (The BEN_PLAN_AMENDMENT_REQUIRED type is used by the "
        "adoption-agreement-missing detector, which IS data-supported.)",
    "betterment_connection_stale": "Disabled integration; BEN_PROVIDER_CONNECTION_STALE stays inert.",
    "payroll_sync_failed": "Disabled integration; BEN_PAYROLL_SYNC_FAILED stays inert.",
    "carrier_submission_failed": "Disabled integration; BEN_CARRIER_SUBMISSION_FAILED stays inert.",
}


def _today(today):
    return today or date.today()


# --- reconcile / auto-resolve (same contract as tax detectors) ---------------

def _auto_resolve(exception_id, status, actor_user_id, resolution="auto_source_cleared"):
    """System-resolve an exception whose source cleared, so recurrence reopens it.
    Un-started exceptions are advanced open→in_progress first (both legal transitions)."""
    if status in ("open", "acknowledged", "reopened"):
        ee.begin_work(exception_id, principal=None, actor_user_id=actor_user_id)
    ee.resolve(exception_id, resolution, principal=None, actor_user_id=actor_user_id)


def _reconcile(prefix, code, conditions, *, actor_user_id):
    """Raise an exception for each current condition (idempotent) and auto-close any open
    exception in this detector's dedupe family whose condition has cleared.

    Each raise/resolve is isolated: a single organization's bad data fails only its own
    condition (recorded in ``failures``) and never aborts the rest of the scan."""
    raised, closed, failures = 0, 0, []
    for key, scope in conditions.items():
        try:
            ee.raise_exception(code=code, actor_user_id=actor_user_id, principal=None,
                               source="system", dedupe_key=key, **scope)
            raised += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            failures.append({"dedupe_key": key, "error": type(exc).__name__})
    with engine.connect() as c:
        stale = c.execute(
            select(exceptions.c.id, exceptions.c.status, exceptions.c.dedupe_key)
            .where(exceptions.c.dedupe_key.like(f"{prefix}%"), exceptions.c.status.notin_(_CLOSED))
        ).mappings().all()
    for row in stale:
        if row["dedupe_key"] in conditions:
            continue
        try:
            _auto_resolve(row["id"], row["status"], actor_user_id)
            closed += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            failures.append({"dedupe_key": row["dedupe_key"], "error": type(exc).__name__})
    return {"raised": raised, "closed": closed, "failures": failures}


def _scope(org_id, title, *, person_id=None, household_id=None):
    return {"related_entity_type": "organization", "related_entity_id": org_id, "title": title,
            "person_id": person_id, "household_id": household_id}


# --- shared context load -----------------------------------------------------

def _load_context():
    """Load stored benefits data once; detectors are pure functions of this context."""
    with engine.connect() as c:
        emps = {r["id"]: dict(r) for r in c.execute(select(
            benefit_employments.c.id, benefit_employments.c.organization_id.label("org_id"),
            benefit_employments.c.person_id, benefit_employments.c.employee_status.label("status"),
            benefit_employments.c.hire_date, benefit_employments.c.benefit_class, people.c.household_id)
            .select_from(benefit_employments.join(people, people.c.id == benefit_employments.c.person_id))).mappings()}
        plans = {r["id"]: dict(r) for r in c.execute(select(
            benefit_plans.c.id, benefit_plans.c.organization_id.label("org_id"), benefit_plans.c.status,
            benefit_plans.c.provider_id, benefit_plans.c.funding_type, benefit_plans.c.effective_date,
            benefit_plans.c.renewal_date, benefit_plan_types.c.code.label("plan_type"),
            benefit_plan_types.c.line_of_coverage.label("line"))
            .select_from(benefit_plans.join(benefit_plan_types,
                benefit_plan_types.c.id == benefit_plans.c.plan_type_id))).mappings()}
        years = {r["id"]: dict(r) for r in c.execute(select(
            benefit_plan_years.c.id, benefit_plan_years.c.plan_id, benefit_plan_years.c.plan_year,
            benefit_plan_years.c.status, benefit_plan_years.c.open_enrollment_end.label("oe_end"))).mappings()}
        enrs = [dict(r) for r in c.execute(select(
            benefit_enrollments.c.id, benefit_enrollments.c.benefit_employment_id.label("emp_id"),
            benefit_enrollments.c.plan_year_id, benefit_enrollments.c.status,
            benefit_enrollments.c.effective_date, benefit_enrollments.c.coverage_tier)).mappings()]
        elections = set(c.scalars(select(benefit_retirement_elections.c.benefit_enrollment_id)))
        ret_details = {r["plan_id"]: dict(r) for r in c.execute(select(
            benefit_retirement_plan_details.c.plan_id,
            benefit_retirement_plan_details.c.adoption_agreement_document_id)).mappings()}
        docs_by_plan, docs_by_org = defaultdict(set), defaultdict(set)
        for d in c.execute(select(benefit_document_links.c.organization_id.label("org_id"),
                                  benefit_document_links.c.plan_id, benefit_document_links.c.doc_kind)).mappings():
            docs_by_org[d["org_id"]].add(d["doc_kind"])
            if d["plan_id"]:
                docs_by_plan[d["plan_id"]].add(d["doc_kind"])
        census_engs = [dict(r) for r in c.execute(select(
            engagements.c.id, engagements.c.organization_id.label("org_id"),
            engagements.c.engagement_type, engagements.c.status, engagements.c.due_date)
            .select_from(engagements.join(service_lines, service_lines.c.id == engagements.c.service_line_id))
            .where(service_lines.c.code == "benefits")).mappings()]
        benefits_sl_orgs = set(c.scalars(select(organization_service_lines.c.organization_id)
            .select_from(organization_service_lines.join(service_lines,
                service_lines.c.id == organization_service_lines.c.service_line_id))
            .where(service_lines.c.code == "benefits", organization_service_lines.c.status == "active")))
        renewal_month = {r["relationship_entity_id"]: r["renewal_month"] for r in c.execute(
            select(organization_profiles.c.relationship_entity_id, organization_profiles.c.renewal_month)).mappings()}
        obligations = [dict(r) for r in c.execute(select(
            benefit_obligations.c.id, benefit_obligations.c.organization_id.label("org_id"),
            benefit_obligations.c.obligation_type, benefit_obligations.c.title,
            benefit_obligations.c.due_date, benefit_obligations.c.warning_days,
            benefit_obligations.c.status).where(
            benefit_obligations.c.status.in_(("scheduled", "in_progress")))).mappings()]

    # derive: enrollments enriched with plan line/org; plan years by plan
    plan_years_by_plan = defaultdict(list)
    for y in years.values():
        plan_years_by_plan[y["plan_id"]].append(y)
    enr_by_emp = defaultdict(list)
    enriched = []
    for e in enrs:
        y = years.get(e["plan_year_id"])
        if not y:
            continue
        p = plans.get(y["plan_id"])
        if not p:
            continue
        row = {**e, "line": p["line"], "plan_id": p["id"], "org_id": p["org_id"],
               "py_status": y["status"], "oe_end": y["oe_end"]}
        enriched.append(row)
        enr_by_emp[e["emp_id"]].append(row)
    org_open = {"health": set(), "retirement": set()}
    for y in years.values():
        p = plans.get(y["plan_id"])
        if p and y["status"] in _OPEN_YEAR and p["line"] in org_open:
            org_open[p["line"]].add(p["org_id"])
    return dict(emps=emps, plans=plans, years=years, enr=enriched, enr_by_emp=enr_by_emp,
                elections=elections, ret_details=ret_details, docs_by_plan=docs_by_plan,
                docs_by_org=docs_by_org, census_engs=census_engs, benefits_sl_orgs=benefits_sl_orgs,
                renewal_month=renewal_month, plan_years_by_plan=plan_years_by_plan, org_open=org_open,
                obligations=obligations)


def _emp(ctx, emp_id):
    return ctx["emps"].get(emp_id)


def _emp_person(ctx, emp_id):
    e = ctx["emps"][emp_id]
    return {"person_id": e["person_id"], "household_id": e["household_id"]}


# --- employee / enrollment detectors -----------------------------------------

def detect_eligibility_unresolved(ctx, today, actor_user_id):
    today = _today(today)
    new_hire_window = _cfg_days("new_hire_window_days", config.benefits_new_hire_window_days())
    cond = {}
    for eid, e in ctx["emps"].items():
        if e["status"] != "active" or e["hire_date"] is None:
            continue
        if (today - e["hire_date"]).days <= new_hire_window:
            continue  # recent hires are the new-hire detector's job
        if e["org_id"] not in ctx["org_open"]["health"]:
            continue
        if any(x["line"] == "health" for x in ctx["enr_by_emp"].get(eid, [])):
            continue
        cond[f"ben:eligibility:{eid}"] = _scope(e["org_id"], "Benefit eligibility unresolved", **_emp_person(ctx, eid))
    return _reconcile("ben:eligibility:", "BEN_ELIGIBILITY_UNRESOLVED", cond, actor_user_id=actor_user_id)


def detect_new_hire_enrollment_due(ctx, today, actor_user_id):
    today = _today(today)
    new_hire_window = _cfg_days("new_hire_window_days", config.benefits_new_hire_window_days())
    cond = {}
    for eid, e in ctx["emps"].items():
        if e["status"] != "active" or e["hire_date"] is None:
            continue
        if (today - e["hire_date"]).days > new_hire_window:
            continue
        if e["org_id"] not in ctx["org_open"]["health"]:
            continue
        if any(x["line"] == "health" for x in ctx["enr_by_emp"].get(eid, [])):
            continue
        cond[f"ben:newhire:{eid}"] = _scope(e["org_id"], "New-hire enrollment due", **_emp_person(ctx, eid))
    return _reconcile("ben:newhire:", "BEN_NEW_HIRE_ENROLLMENT_DUE", cond, actor_user_id=actor_user_id)


def detect_missing_waiver(ctx, today, actor_user_id):
    today = _today(today)
    cond = {}
    for r in ctx["enr"]:
        if r["line"] != "health" or r["status"] != "eligible":
            continue
        if r["oe_end"] is None or r["oe_end"] >= today:
            continue  # only once the OE window has closed
        cond[f"ben:waiver:{r['id']}"] = _scope(r["org_id"], "Coverage election or waiver missing",
                                               **_emp_person(ctx, r["emp_id"]))
    return _reconcile("ben:waiver:", "BEN_WAIVER_MISSING", cond, actor_user_id=actor_user_id)


def detect_open_enrollment_incomplete(ctx, today, actor_user_id):
    today = _today(today)
    oe_warning = _cfg_days("open_enrollment_warning_days", config.benefits_open_enrollment_warning_days())
    handled_by_year = defaultdict(set)
    for r in ctx["enr"]:
        if r["status"] in _HANDLED_ENROLLED:
            handled_by_year[r["plan_year_id"]].add(r["emp_id"])
    cond = {}
    for yid, y in ctx["years"].items():
        p = ctx["plans"].get(y["plan_id"])
        if not p or p["line"] != "health" or y["status"] != "open_enrollment":
            continue
        if y["oe_end"] is None or y["oe_end"] > today + timedelta(days=oe_warning):
            continue
        active_emps = {eid for eid, e in ctx["emps"].items()
                       if e["org_id"] == p["org_id"] and e["status"] == "active"}
        if active_emps - handled_by_year.get(yid, set()):
            cond[f"ben:oe_incomplete:{yid}"] = _scope(p["org_id"], "Open enrollment incomplete")
    return _reconcile("ben:oe_incomplete:", "BEN_OPEN_ENROLLMENT_INCOMPLETE", cond, actor_user_id=actor_user_id)


def detect_terminated_still_enrolled(ctx, today, actor_user_id):
    cond = {}
    for r in ctx["enr"]:
        e = ctx["emps"].get(r["emp_id"])
        if not e or e["status"] != "terminated":
            continue
        if r["status"] not in _ACTIVE_ENROLLED:
            continue
        cond[f"ben:term_enrolled:{r['id']}"] = _scope(r["org_id"], "Terminated employee still enrolled",
                                                      **_emp_person(ctx, r["emp_id"]))
    return _reconcile("ben:term_enrolled:", "BEN_CENSUS_MISMATCH", cond, actor_user_id=actor_user_id)


def detect_enrollment_effective_date_mismatch(ctx, today, actor_user_id):
    cond = {}
    for r in ctx["enr"]:
        e = ctx["emps"].get(r["emp_id"])
        if not e or r["effective_date"] is None or e["hire_date"] is None:
            continue
        if r["effective_date"] >= e["hire_date"]:
            continue  # coverage effective on/after hire is fine
        cond[f"ben:eff_mismatch:{r['id']}"] = _scope(r["org_id"], "Enrollment effective-date mismatch",
                                                    **_emp_person(ctx, r["emp_id"]))
    return _reconcile("ben:eff_mismatch:", "BEN_CENSUS_MISMATCH", cond, actor_user_id=actor_user_id)


# --- employer / census detectors ---------------------------------------------

def detect_census_overdue(ctx, today, actor_user_id):
    today = _today(today)
    cond = {}
    for eng in ctx["census_engs"]:
        if "census" not in (eng["engagement_type"] or "").lower():
            continue
        grace = timedelta(days=_cfg_days("census_grace_days", config.benefits_census_grace_days()))
        if eng["status"] in ("closed", "cancelled") or eng["due_date"] is None or eng["due_date"] + grace >= today:
            continue
        if "census" in ctx["docs_by_org"].get(eng["org_id"], set()):
            continue  # a census document has arrived
        cond[f"ben:census_overdue:{eng['id']}"] = _scope(eng["org_id"], "Census overdue")
    return _reconcile("ben:census_overdue:", "BEN_CENSUS_OVERDUE", cond, actor_user_id=actor_user_id)


def detect_roster_information_missing(ctx, today, actor_user_id):
    cond = {}
    for eid, e in ctx["emps"].items():
        if e["status"] != "active" or e["hire_date"] is not None:
            continue  # missing hire date blocks eligibility timing
        cond[f"ben:roster_missing:{eid}"] = _scope(e["org_id"], "Required roster information missing",
                                                   **_emp_person(ctx, eid))
    return _reconcile("ben:roster_missing:", "BEN_CENSUS_MISMATCH", cond, actor_user_id=actor_user_id)


def detect_employer_renewal_data_incomplete(ctx, today, actor_user_id):
    cond = {}
    for org_id in ctx["benefits_sl_orgs"]:
        if ctx["renewal_month"].get(org_id) is None:
            cond[f"ben:renewal_data:{org_id}"] = _scope(org_id, "Employer renewal data incomplete")
    return _reconcile("ben:renewal_data:", "BEN_RENEWAL_AT_RISK", cond, actor_user_id=actor_user_id)


# --- health / welfare plan detectors -----------------------------------------

def _past_document_grace(plan, today):
    """A required document is only 'missing' once the configured grace period has
    elapsed since the plan became effective. Grace defaults to 0 (fire immediately —
    identical to the Phase-3 semantics); a future/unknown effective date never suppresses."""
    grace = _cfg_days("document_grace_days", config.benefits_document_grace_days())
    eff = plan["effective_date"]
    if grace <= 0 or eff is None:
        return True
    return (today - eff).days >= grace


def detect_spd_missing(ctx, today, actor_user_id):
    today = _today(today)
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["line"] != "health" or p["status"] not in ("active", "renewing"):
            continue
        if "spd" in ctx["docs_by_plan"].get(pid, set()) or not _past_document_grace(p, today):
            continue
        cond[f"ben:spd:{pid}"] = _scope(p["org_id"], "Required SPD missing")
    return _reconcile("ben:spd:", "BEN_SPD_MISSING", cond, actor_user_id=actor_user_id)


def detect_sbc_missing(ctx, today, actor_user_id):
    today = _today(today)
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["plan_type"] != "medical" or p["status"] not in ("active", "renewing"):
            continue
        if "sbc" in ctx["docs_by_plan"].get(pid, set()) or not _past_document_grace(p, today):
            continue
        cond[f"ben:sbc:{pid}"] = _scope(p["org_id"], "Required SBC missing")
    return _reconcile("ben:sbc:", "BEN_SBC_MISSING", cond, actor_user_id=actor_user_id)


def detect_renewal_at_risk(ctx, today, actor_user_id):
    today = _today(today)
    renewal_warning = _cfg_days("renewal_warning_days", config.benefits_renewal_warning_days())
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["status"] != "active" or p["renewal_date"] is None:
            continue
        if p["renewal_date"] > today + timedelta(days=renewal_warning):
            continue
        cond[f"ben:renewal_risk:{pid}"] = _scope(p["org_id"], "Renewal at risk")
    return _reconcile("ben:renewal_risk:", "BEN_RENEWAL_AT_RISK", cond, actor_user_id=actor_user_id)


def detect_plan_year_missing(ctx, today, actor_user_id):
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["line"] != "health" or p["status"] not in ("active", "draft"):
            continue
        if ctx["plan_years_by_plan"].get(pid):
            continue
        cond[f"ben:no_plan_year:{pid}"] = _scope(p["org_id"], "Plan year missing")
    return _reconcile("ben:no_plan_year:", "BEN_RENEWAL_AT_RISK", cond, actor_user_id=actor_user_id)


def detect_plan_information_incomplete(ctx, today, actor_user_id):
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["line"] != "health" or p["status"] != "active":
            continue
        if p["provider_id"] is not None and p["funding_type"] is not None and p["effective_date"] is not None:
            continue
        cond[f"ben:plan_info:{pid}"] = _scope(p["org_id"], "Carrier or plan information incomplete")
    return _reconcile("ben:plan_info:", "BEN_RENEWAL_AT_RISK", cond, actor_user_id=actor_user_id)


# --- retirement-plan detectors -----------------------------------------------

def detect_retirement_eligibility_unresolved(ctx, today, actor_user_id):
    cond = {}
    for eid, e in ctx["emps"].items():
        if e["status"] != "active" or e["org_id"] not in ctx["org_open"]["retirement"]:
            continue
        if any(x["line"] == "retirement" for x in ctx["enr_by_emp"].get(eid, [])):
            continue
        cond[f"ben:ret_elig:{eid}"] = _scope(e["org_id"], "Retirement eligibility unresolved", **_emp_person(ctx, eid))
    return _reconcile("ben:ret_elig:", "BEN_RETIREMENT_ELIGIBILITY_UNRESOLVED", cond, actor_user_id=actor_user_id)


def detect_deferral_election_due(ctx, today, actor_user_id):
    cond = {}
    for r in ctx["enr"]:
        if r["line"] != "retirement" or r["status"] not in _ACTIVE_ENROLLED:
            continue
        if r["id"] in ctx["elections"]:
            continue
        cond[f"ben:deferral:{r['id']}"] = _scope(r["org_id"], "Deferral election due", **_emp_person(ctx, r["emp_id"]))
    return _reconcile("ben:deferral:", "BEN_DEFERRAL_ELECTION_DUE", cond, actor_user_id=actor_user_id)


def detect_retirement_plan_year_missing(ctx, today, actor_user_id):
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["line"] != "retirement" or p["status"] not in ("active", "draft"):
            continue
        if ctx["plan_years_by_plan"].get(pid):
            continue
        cond[f"ben:ret_no_plan_year:{pid}"] = _scope(p["org_id"], "Retirement plan year missing")
    return _reconcile("ben:ret_no_plan_year:", "BEN_RENEWAL_AT_RISK", cond, actor_user_id=actor_user_id)


def detect_adoption_agreement_missing(ctx, today, actor_user_id):
    cond = {}
    for pid, p in ctx["plans"].items():
        if p["line"] != "retirement" or p["status"] not in ("active", "draft"):
            continue
        details = ctx["ret_details"].get(pid)
        has_field = bool(details and details["adoption_agreement_document_id"])
        has_link = "adoption_agreement" in ctx["docs_by_plan"].get(pid, set())
        if has_field or has_link:
            continue
        cond[f"ben:adoption:{pid}"] = _scope(p["org_id"], "Adoption agreement missing")
    return _reconcile("ben:adoption:", "BEN_PLAN_AMENDMENT_REQUIRED", cond, actor_user_id=actor_user_id)


# --- date-driven obligation detector -----------------------------------------

def _due_datetime(due_date):
    return datetime(due_date.year, due_date.month, due_date.day, 23, 59, tzinfo=UTC)


def detect_obligation_deadlines(ctx, today, actor_user_id):
    """Date-driven: raise a benefits exception for each active obligation whose warning window
    has been reached, mapping obligation type -> approved code and setting the exception SLA to
    the obligation's real due date. Auto-resolves when the obligation is no longer active."""
    today = _today(today)
    cond = {}
    for ob in ctx["obligations"]:
        code = OBLIGATION_EXCEPTION_CODE.get(ob["obligation_type"])
        if code is None:  # unsupported / intentionally inert obligation type
            continue
        warn = ob["warning_days"] or 0
        if today < ob["due_date"] - timedelta(days=warn):
            continue  # warning window not yet reached
        cond[f"ben:obligation:{ob['id']}"] = {
            "related_entity_type": "organization", "related_entity_id": ob["org_id"],
            "title": ob["title"], "person_id": None, "household_id": None,
            "sla_due_at": _due_datetime(ob["due_date"])}
    return _reconcile_obligations(cond, ctx, actor_user_id=actor_user_id)


def _reconcile_obligations(conditions, ctx, *, actor_user_id):
    """Reconcile the shared ``ben:obligation:`` family where each condition raises under its own
    obligation-derived code. Close = auto-resolve any open obligation exception whose obligation
    is no longer active."""
    raised, closed, failures = 0, 0, []
    type_by_id = {ob["id"]: ob["obligation_type"] for ob in ctx["obligations"]}
    for key, scope in conditions.items():
        ob_id = int(key.rsplit(":", 1)[1])
        code = OBLIGATION_EXCEPTION_CODE[type_by_id[ob_id]]
        try:
            ee.raise_exception(code=code, actor_user_id=actor_user_id, principal=None,
                               source="system", dedupe_key=key, **scope)
            raised += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            failures.append({"dedupe_key": key, "error": type(exc).__name__})
    with engine.connect() as c:
        stale = c.execute(select(exceptions.c.id, exceptions.c.status, exceptions.c.dedupe_key)
            .where(exceptions.c.dedupe_key.like("ben:obligation:%"), exceptions.c.status.notin_(_CLOSED))).mappings().all()
    for row in stale:
        if row["dedupe_key"] in conditions:
            continue
        try:
            _auto_resolve(row["id"], row["status"], actor_user_id)
            closed += 1
        except Exception as exc:  # pragma: no cover
            failures.append({"dedupe_key": row["dedupe_key"], "error": type(exc).__name__})
    return {"raised": raised, "closed": closed, "failures": failures}


# --- orchestrator ------------------------------------------------------------

DETECTORS = (
    detect_eligibility_unresolved, detect_new_hire_enrollment_due, detect_missing_waiver,
    detect_open_enrollment_incomplete, detect_terminated_still_enrolled,
    detect_enrollment_effective_date_mismatch, detect_census_overdue,
    detect_roster_information_missing, detect_employer_renewal_data_incomplete,
    detect_spd_missing, detect_sbc_missing, detect_renewal_at_risk, detect_plan_year_missing,
    detect_plan_information_incomplete, detect_retirement_eligibility_unresolved,
    detect_deferral_election_due, detect_retirement_plan_year_missing,
    detect_adoption_agreement_missing, detect_obligation_deadlines,
)


def scan_benefits_exceptions(*, actor_user_id=None, today=None, ctx=None):
    """Run every active benefits detector once (idempotent). Returns per-detector counts.
    Each detector is isolated: a failure in one never aborts the others (recorded per
    detector). Inert/gap detectors and disabled-integration types are never run."""
    ctx = ctx if ctx is not None else _load_context()
    summary = {}
    for detector in DETECTORS:
        try:
            summary[detector.__name__] = detector(ctx, today, actor_user_id)
        except Exception as exc:  # pragma: no cover - defensive isolation
            summary[detector.__name__] = {"raised": 0, "closed": 0,
                                          "failures": [{"detector": detector.__name__, "error": type(exc).__name__}]}
    return summary


def _benefits_org_ids(ctx):
    """Organizations with a benefits footprint (plans, employees, census engagements, or an
    active benefits service line) — the set the scheduled scan attributes results to."""
    ids = set(ctx["benefits_sl_orgs"])
    ids |= {p["org_id"] for p in ctx["plans"].values()}
    ids |= {e["org_id"] for e in ctx["emps"].values()}
    ids |= {eng["org_id"] for eng in ctx["census_engs"]}
    return ids


def _open_benefits_status():
    """id -> status for every benefits exception (used to diff opened/resolved/reopened)."""
    with engine.connect() as c:
        return {r["id"]: r["status"] for r in c.execute(
            select(exceptions.c.id, exceptions.c.status).where(exceptions.c.domain == "benefits")).mappings()}


def run_benefits_scan(*, actor_user_id=None, today=None):
    """Scheduled entry point. Runs the idempotent detector scan and returns an **honest**
    execution result: scanned organizations, exceptions opened / resolved / reopened /
    skipped (idempotent replays), and per-condition failures. One organization's bad data
    is isolated by ``_reconcile`` and reported, never aborting the rest of the scan."""
    # Materialize any due next-occurrences first (idempotent) so the scan sees them.
    from app.services.benefits_obligations import materialize_recurring
    try:
        materialized = materialize_recurring(actor_user_id=actor_user_id, today=today)
    except Exception:  # pragma: no cover - materialization failure must not abort the scan
        materialized = {"considered": 0, "materialized": 0, "failures": 1}
    ctx = _load_context()
    scanned_orgs = len(_benefits_org_ids(ctx))
    before = _open_benefits_status()
    summary = scan_benefits_exceptions(actor_user_id=actor_user_id, today=today, ctx=ctx)
    after = _open_benefits_status()

    raised_total = sum(s.get("raised", 0) for s in summary.values())
    failures = [f for s in summary.values() for f in s.get("failures", [])]
    active = lambda st: st not in _CLOSED
    opened = sum(1 for i, st in after.items() if active(st) and i not in before)
    reopened = sum(1 for i, st in after.items() if active(st) and i in before and not active(before[i]))
    resolved = sum(1 for i, st in after.items() if not active(st) and i in before and active(before[i]))
    skipped = max(0, raised_total - opened - reopened)  # idempotent replays (condition persists)
    return {
        "scanned_organizations": scanned_orgs,
        "exceptions_opened": opened,
        "exceptions_resolved": resolved,
        "exceptions_reopened": reopened,
        "exceptions_skipped": skipped,
        "obligations_materialized": materialized["materialized"],
        "failures": len(failures) + materialized["failures"],
        "failure_detail": failures,
    }
