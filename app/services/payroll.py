"""Payroll Hub canonical service (360Plus / Client360 — foundation).

Payroll as a first-class business-client module: **payroll accounts/providers**, **employees**,
**payroll periods/runs**, **payroll<->document links** (reusing the canonical ``documents`` store, never
duplicating it), and **payroll issues/tasks** — all anchored to a business (an organization =
``relationship_entities`` row; every ``organization_id`` is a ``relationship_entities.id``).

This is an information + workflow system, **not a payroll processor**: no payroll submission, direct
deposit, ACH, tax payment, or money movement, and no live ADP/QuickBooks API (provider adapters in
:mod:`app.services.payroll_providers` are inert). Money is integer USD cents.

Authorization mirrors the platform pattern: ``payroll.write`` to mutate, ``payroll.read`` to read; record
scope resolves to the business (``organization_in_scope``). Per-business availability is the existing
feature catalog (:func:`payroll_enabled_for_org`). Every mutation writes an audit event.
"""
import uuid

from sqlalchemy import func, select

from app.db import (
    documents,
    engine,
    payroll_accounts,
    payroll_document_links,
    payroll_employees,
    payroll_issues,
    payroll_providers,
    payroll_runs,
)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope

FEATURE_KEY = "payroll"

ACCOUNT_STATUSES = frozenset({"prospect", "active", "suspended", "inactive"})
PAY_FREQUENCIES = frozenset({"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual", "other"})
EMP_STATUSES = frozenset({"active", "terminated", "on_leave", "pending"})
COMP_TYPES = frozenset({"salary", "hourly", "commission", "contract", "other"})
COMP_PERIODS = frozenset({"annual", "monthly", "weekly", "hourly", "other"})
RUN_STATUSES = frozenset({"scheduled", "draft", "processed", "paid", "void"})
DOC_CATEGORIES = frozenset({"payroll_report", "w2", "w3", "941", "state_filing",
                            "retirement_contribution_report", "other"})
ISSUE_TYPES = frozenset({"missing_filing", "payroll_discrepancy", "contribution_issue",
                         "employee_setup_issue", "tax_notice", "general_payroll_task"})
ISSUE_SEVERITIES = frozenset({"low", "medium", "high"})
ISSUE_STATUSES = frozenset({"open", "in_progress", "resolved", "cancelled"})
_OPEN_ISSUE_STATUSES = ("open", "in_progress")

_RUN_MONEY_FIELDS = ("gross_cents", "employee_taxes_cents", "employer_taxes_cents", "deductions_cents",
                     "retirement_contributions_cents", "benefits_cents", "net_cents")


class PayrollError(RuntimeError):
    """Bad input for a payroll operation."""


class PayrollNotFound(PayrollError):
    """A payroll record does not exist."""


class PayrollUnavailable(PayrollError):
    """The payroll module tables are not present (migration payroll01 not applied)."""


# --- guards ------------------------------------------------------------------

def _ensure_available():
    if payroll_accounts is None or payroll_providers is None:
        raise PayrollUnavailable("Payroll module is not available (migration payroll01 not applied)")


def _rid(request_id):
    return request_id or f"payroll-{uuid.uuid4()}"


def _require(principal, capability):
    if not principal.can(capability):
        raise PermissionError(f"Missing capability: {capability}")


def _require_scope(principal, organization_id, *, write, connection):
    if not organization_in_scope(principal, organization_id, write=write, connection=connection):
        raise PermissionError("Business is outside your record scope")


def _one(value):
    """Money/int coercion — None passes through, everything else becomes an int (cents)."""
    return None if value is None else int(value)


# --- providers (reference data; no scope) -----------------------------------

def list_providers():
    _ensure_available()
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(payroll_providers)
                .order_by(payroll_providers.c.id)).mappings()]


def _provider_id(c, code):
    if code is None:
        return None
    pid = c.scalar(select(payroll_providers.c.id).where(payroll_providers.c.code == code))
    if pid is None:
        raise PayrollError(f"Unknown payroll provider: {code}")
    return pid


# --- per-business availability (existing feature catalog) --------------------

def payroll_enabled_for_org(organization_id) -> bool:
    """Whether the Payroll feature is enabled for this business, per the existing feature-control
    framework (firm state + product entitlement + per-client override). Read-only; never raises — an
    unavailable framework fails closed (disabled)."""
    try:
        from app.services.features.service import effective_access
        return effective_access("organization", organization_id, FEATURE_KEY, actor="staff").allowed
    except Exception:  # noqa: BLE001 — availability check must never break a read
        return False


# --- accounts ----------------------------------------------------------------

def create_account(principal, *, organization_id, provider_code=None, external_account_id=None,
                   status="active", pay_frequency=None, next_payroll_date=None, notes=None,
                   request_id=None):
    _ensure_available()
    _require(principal, "payroll.write")
    if status not in ACCOUNT_STATUSES:
        raise PayrollError(f"Unsupported account status: {status}")
    if pay_frequency is not None and pay_frequency not in PAY_FREQUENCIES:
        raise PayrollError(f"Unsupported pay frequency: {pay_frequency}")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        provider_id = _provider_id(c, provider_code)
        account_id = c.execute(payroll_accounts.insert().values(
            organization_id=organization_id, provider_id=provider_id,
            external_account_id=external_account_id, status=status, pay_frequency=pay_frequency,
            next_payroll_date=next_payroll_date, notes=notes,
            created_by_user_id=principal.user_id).returning(payroll_accounts.c.id)).scalar_one()
    write_audit_event(action="payroll.account.created", entity_type="payroll_account",
                      entity_id=account_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"organization_id": organization_id, "provider": provider_code,
                                "status": status})
    return get_account(account_id, principal=principal)


def get_account(account_id, *, principal, connection=None):
    _ensure_available()
    _require(principal, "payroll.read")

    def _load(c):
        row = c.execute(select(payroll_accounts).where(
            payroll_accounts.c.id == account_id)).mappings().one_or_none()
        if row is None:
            raise PayrollNotFound(f"Payroll account {account_id} not found")
        _require_scope(principal, row["organization_id"], write=False, connection=c)
        return dict(row)

    if connection is not None:
        return _load(connection)
    with engine.connect() as c:
        return _load(c)


def _account_org(c, account_id, *, principal, write):
    row = c.execute(select(payroll_accounts.c.id, payroll_accounts.c.organization_id)
                    .where(payroll_accounts.c.id == account_id)).mappings().one_or_none()
    if row is None:
        raise PayrollNotFound(f"Payroll account {account_id} not found")
    _require_scope(principal, row["organization_id"], write=write, connection=c)
    return row["organization_id"]


def update_account(account_id, *, principal, request_id=None, **fields):
    _ensure_available()
    _require(principal, "payroll.write")
    allowed = {"external_account_id", "status", "pay_frequency", "next_payroll_date", "notes"}
    values = {k: v for k, v in fields.items() if k in allowed}
    if "status" in values and values["status"] not in ACCOUNT_STATUSES:
        raise PayrollError(f"Unsupported account status: {values['status']}")
    if values.get("pay_frequency") is not None and values["pay_frequency"] not in PAY_FREQUENCIES:
        raise PayrollError(f"Unsupported pay frequency: {values['pay_frequency']}")
    with engine.begin() as c:
        _account_org(c, account_id, principal=principal, write=True)
        if "provider_code" in fields:
            values["provider_id"] = _provider_id(c, fields["provider_code"])
        if values:
            c.execute(payroll_accounts.update().where(payroll_accounts.c.id == account_id).values(**values))
    write_audit_event(action="payroll.account.updated", entity_type="payroll_account",
                      entity_id=account_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"fields": sorted(values)})
    return get_account(account_id, principal=principal)


def list_accounts(organization_id, *, principal):
    _ensure_available()
    _require(principal, "payroll.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        return [dict(r) for r in c.execute(select(payroll_accounts)
                .where(payroll_accounts.c.organization_id == organization_id)
                .order_by(payroll_accounts.c.id)).mappings()]


# --- employees ---------------------------------------------------------------

def add_employee(principal, *, payroll_account_id, full_name=None, first_name=None, last_name=None,
                 employment_status="active", hire_date=None, termination_date=None,
                 compensation_type=None, compensation_amount_cents=None, compensation_period="annual",
                 provider_employee_id=None, retirement_plan_participant=False, person_id=None,
                 request_id=None):
    _ensure_available()
    _require(principal, "payroll.write")
    name = (full_name or " ".join(p for p in (first_name, last_name) if p) or "").strip()
    if not name:
        raise PayrollError("Employee name is required")
    if employment_status not in EMP_STATUSES:
        raise PayrollError(f"Unsupported employment status: {employment_status}")
    if compensation_type is not None and compensation_type not in COMP_TYPES:
        raise PayrollError(f"Unsupported compensation type: {compensation_type}")
    if compensation_period not in COMP_PERIODS:
        raise PayrollError(f"Unsupported compensation period: {compensation_period}")
    with engine.begin() as c:
        organization_id = _account_org(c, payroll_account_id, principal=principal, write=True)
        employee_id = c.execute(payroll_employees.insert().values(
            payroll_account_id=payroll_account_id, organization_id=organization_id, person_id=person_id,
            first_name=first_name, last_name=last_name, full_name=name,
            employment_status=employment_status, hire_date=hire_date, termination_date=termination_date,
            compensation_type=compensation_type, compensation_amount_cents=_one(compensation_amount_cents),
            compensation_period=compensation_period, provider_employee_id=provider_employee_id,
            retirement_plan_participant=bool(retirement_plan_participant)
        ).returning(payroll_employees.c.id)).scalar_one()
    write_audit_event(action="payroll.employee.added", entity_type="payroll_employee",
                      entity_id=employee_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"payroll_account_id": payroll_account_id, "status": employment_status})
    return employee_id


def update_employee(employee_id, *, principal, request_id=None, **fields):
    _ensure_available()
    _require(principal, "payroll.write")
    allowed = {"full_name", "first_name", "last_name", "employment_status", "hire_date",
               "termination_date", "compensation_type", "compensation_amount_cents",
               "compensation_period", "provider_employee_id", "retirement_plan_participant", "person_id"}
    values = {k: v for k, v in fields.items() if k in allowed}
    if values.get("employment_status") and values["employment_status"] not in EMP_STATUSES:
        raise PayrollError(f"Unsupported employment status: {values['employment_status']}")
    if values.get("compensation_type") is not None and values["compensation_type"] not in COMP_TYPES:
        raise PayrollError(f"Unsupported compensation type: {values['compensation_type']}")
    if "compensation_amount_cents" in values:
        values["compensation_amount_cents"] = _one(values["compensation_amount_cents"])
    if "retirement_plan_participant" in values:
        values["retirement_plan_participant"] = bool(values["retirement_plan_participant"])
    with engine.begin() as c:
        row = c.execute(select(payroll_employees.c.organization_id)
                        .where(payroll_employees.c.id == employee_id)).mappings().one_or_none()
        if row is None:
            raise PayrollNotFound(f"Payroll employee {employee_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        if values:
            c.execute(payroll_employees.update().where(
                payroll_employees.c.id == employee_id).values(**values))
    write_audit_event(action="payroll.employee.updated", entity_type="payroll_employee",
                      entity_id=employee_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"fields": sorted(values)})
    return employee_id


def list_employees(organization_id, *, principal, status=None, payroll_account_id=None):
    _ensure_available()
    _require(principal, "payroll.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        q = select(payroll_employees).where(payroll_employees.c.organization_id == organization_id)
        if status is not None:
            q = q.where(payroll_employees.c.employment_status == status)
        if payroll_account_id is not None:
            q = q.where(payroll_employees.c.payroll_account_id == payroll_account_id)
        return [dict(r) for r in c.execute(q.order_by(payroll_employees.c.full_name)).mappings()]


# --- payroll runs (periods) --------------------------------------------------

def record_run(principal, *, payroll_account_id, period_start=None, period_end=None, pay_date=None,
               status="scheduled", notes=None, request_id=None, **money):
    _ensure_available()
    _require(principal, "payroll.write")
    if status not in RUN_STATUSES:
        raise PayrollError(f"Unsupported run status: {status}")
    values = {f: _one(money.get(f)) for f in _RUN_MONEY_FIELDS}
    with engine.begin() as c:
        organization_id = _account_org(c, payroll_account_id, principal=principal, write=True)
        run_id = c.execute(payroll_runs.insert().values(
            payroll_account_id=payroll_account_id, organization_id=organization_id,
            period_start=period_start, period_end=period_end, pay_date=pay_date, status=status,
            notes=notes, created_by_user_id=principal.user_id, **values
        ).returning(payroll_runs.c.id)).scalar_one()
    write_audit_event(action="payroll.run.recorded", entity_type="payroll_run", entity_id=run_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"payroll_account_id": payroll_account_id, "status": status,
                                "pay_date": str(pay_date) if pay_date else None})
    return run_id


def set_run_status(run_id, status, *, principal, request_id=None):
    _ensure_available()
    _require(principal, "payroll.write")
    if status not in RUN_STATUSES:
        raise PayrollError(f"Unsupported run status: {status}")
    with engine.begin() as c:
        row = c.execute(select(payroll_runs.c.organization_id)
                        .where(payroll_runs.c.id == run_id)).mappings().one_or_none()
        if row is None:
            raise PayrollNotFound(f"Payroll run {run_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        c.execute(payroll_runs.update().where(payroll_runs.c.id == run_id).values(status=status))
    write_audit_event(action="payroll.run.status_changed", entity_type="payroll_run", entity_id=run_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"to": status})
    return run_id


def list_runs(organization_id, *, principal, payroll_account_id=None, limit=200):
    _ensure_available()
    _require(principal, "payroll.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        q = select(payroll_runs).where(payroll_runs.c.organization_id == organization_id)
        if payroll_account_id is not None:
            q = q.where(payroll_runs.c.payroll_account_id == payroll_account_id)
        return [dict(r) for r in c.execute(
            q.order_by(payroll_runs.c.pay_date.desc().nullslast(), payroll_runs.c.id.desc())
            .limit(limit)).mappings()]


# --- document links (REUSE the canonical documents store) --------------------

def link_document(principal, *, organization_id, document_id, category, payroll_account_id=None,
                  payroll_run_id=None, period_label=None, request_id=None):
    """Associate an EXISTING canonical document with payroll for a business. Never creates, copies, or
    modifies the document or its storage — the ``documents`` row must already exist."""
    _ensure_available()
    _require(principal, "payroll.write")
    if category not in DOC_CATEGORIES:
        raise PayrollError(f"Unsupported payroll document category: {category}")
    from sqlalchemy.exc import IntegrityError
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        if c.scalar(select(documents.c.id).where(documents.c.id == document_id)) is None:
            raise PayrollNotFound(f"Document {document_id} not found")
        try:
            link_id = c.execute(payroll_document_links.insert().values(
                document_id=document_id, organization_id=organization_id,
                payroll_account_id=payroll_account_id, payroll_run_id=payroll_run_id,
                category=category, period_label=period_label,
                created_by_user_id=principal.user_id).returning(payroll_document_links.c.id)).scalar_one()
        except IntegrityError:
            raise PayrollError("This document is already linked to payroll under that category") from None
    write_audit_event(action="payroll.document.linked", entity_type="payroll_document_link",
                      entity_id=link_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"organization_id": organization_id, "document_id": document_id,
                                "category": category})
    return link_id


def unlink_document(link_id, *, principal, request_id=None):
    _ensure_available()
    _require(principal, "payroll.write")
    with engine.begin() as c:
        row = c.execute(select(payroll_document_links.c.organization_id)
                        .where(payroll_document_links.c.id == link_id)).mappings().one_or_none()
        if row is None:
            raise PayrollNotFound(f"Payroll document link {link_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        c.execute(payroll_document_links.delete().where(payroll_document_links.c.id == link_id))
    write_audit_event(action="payroll.document.unlinked", entity_type="payroll_document_link",
                      entity_id=link_id, actor_user_id=principal.user_id, request_id=_rid(request_id))


def list_documents(organization_id, *, principal):
    """Payroll document links joined to the canonical documents (names, not raw ids)."""
    _ensure_available()
    _require(principal, "payroll.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        rows = c.execute(
            select(payroll_document_links.c.id, payroll_document_links.c.document_id,
                   payroll_document_links.c.category, payroll_document_links.c.period_label,
                   payroll_document_links.c.payroll_run_id, payroll_document_links.c.created_at,
                   documents.c.original_name)
            .select_from(payroll_document_links.join(
                documents, documents.c.id == payroll_document_links.c.document_id))
            .where(payroll_document_links.c.organization_id == organization_id)
            .order_by(payroll_document_links.c.created_at.desc())).mappings().all()
    return [dict(r) for r in rows]


# --- issues / tasks (business-scoped) ---------------------------------------

def open_issue(principal, *, organization_id, issue_type, title, description=None, severity="medium",
               payroll_account_id=None, payroll_run_id=None, payroll_employee_id=None, document_id=None,
               assigned_user_id=None, due_date=None, request_id=None):
    _ensure_available()
    _require(principal, "payroll.write")
    if issue_type not in ISSUE_TYPES:
        raise PayrollError(f"Unsupported payroll issue type: {issue_type}")
    if severity not in ISSUE_SEVERITIES:
        raise PayrollError(f"Unsupported severity: {severity}")
    if not (title or "").strip():
        raise PayrollError("Issue title is required")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        issue_id = c.execute(payroll_issues.insert().values(
            organization_id=organization_id, payroll_account_id=payroll_account_id,
            payroll_run_id=payroll_run_id, payroll_employee_id=payroll_employee_id, document_id=document_id,
            issue_type=issue_type, title=title.strip(), description=description, severity=severity,
            status="open", assigned_user_id=assigned_user_id, due_date=due_date,
            created_by_user_id=principal.user_id).returning(payroll_issues.c.id)).scalar_one()
    write_audit_event(action="payroll.issue.opened", entity_type="payroll_issue", entity_id=issue_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"organization_id": organization_id, "issue_type": issue_type,
                                "severity": severity})
    return issue_id


def set_issue_status(issue_id, status, *, principal, request_id=None):
    _ensure_available()
    _require(principal, "payroll.write")
    if status not in ISSUE_STATUSES:
        raise PayrollError(f"Unsupported issue status: {status}")
    from datetime import UTC, datetime
    with engine.begin() as c:
        row = c.execute(select(payroll_issues.c.organization_id)
                        .where(payroll_issues.c.id == issue_id)).mappings().one_or_none()
        if row is None:
            raise PayrollNotFound(f"Payroll issue {issue_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        values = {"status": status}
        if status == "resolved":
            values["resolved_at"] = datetime.now(UTC)
            values["resolved_by_user_id"] = principal.user_id
        c.execute(payroll_issues.update().where(payroll_issues.c.id == issue_id).values(**values))
    write_audit_event(action="payroll.issue.status_changed", entity_type="payroll_issue",
                      entity_id=issue_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"to": status})
    return issue_id


def list_issues(organization_id, *, principal, status=None, open_only=False):
    _ensure_available()
    _require(principal, "payroll.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        q = select(payroll_issues).where(payroll_issues.c.organization_id == organization_id)
        if open_only:
            q = q.where(payroll_issues.c.status.in_(_OPEN_ISSUE_STATUSES))
        elif status is not None:
            q = q.where(payroll_issues.c.status == status)
        return [dict(r) for r in c.execute(
            q.order_by(payroll_issues.c.created_at.desc())).mappings()]


# --- dashboard summary (the Business -> Payroll cards) -----------------------

def payroll_summary(organization_id, *, principal):
    """Read-only summary powering the Business -> Payroll dashboard cards: next payroll date, active
    employee count, latest gross / employer taxes / retirement contributions, and open-issue count."""
    _ensure_available()
    _require(principal, "payroll.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)

        accounts = [dict(r) for r in c.execute(select(payroll_accounts)
                    .where(payroll_accounts.c.organization_id == organization_id)
                    .order_by(payroll_accounts.c.id)).mappings()]

        next_payroll_date = c.scalar(
            select(func.min(payroll_accounts.c.next_payroll_date)).where(
                payroll_accounts.c.organization_id == organization_id,
                payroll_accounts.c.status == "active",
                payroll_accounts.c.next_payroll_date.isnot(None)))

        employee_count = c.scalar(
            select(func.count()).select_from(payroll_employees).where(
                payroll_employees.c.organization_id == organization_id,
                payroll_employees.c.employment_status == "active")) or 0

        latest = c.execute(
            select(payroll_runs.c.id, payroll_runs.c.pay_date, payroll_runs.c.gross_cents,
                   payroll_runs.c.employer_taxes_cents, payroll_runs.c.retirement_contributions_cents,
                   payroll_runs.c.net_cents, payroll_runs.c.status)
            .where(payroll_runs.c.organization_id == organization_id)
            .order_by(payroll_runs.c.pay_date.desc().nullslast(), payroll_runs.c.id.desc())
            .limit(1)).mappings().one_or_none()

        open_issue_count = c.scalar(
            select(func.count()).select_from(payroll_issues).where(
                payroll_issues.c.organization_id == organization_id,
                payroll_issues.c.status.in_(_OPEN_ISSUE_STATUSES))) or 0

    latest = dict(latest) if latest else None
    return {
        "organization_id": organization_id,
        "enabled": payroll_enabled_for_org(organization_id),
        "account_count": len(accounts),
        "accounts": accounts,
        "next_payroll_date": next_payroll_date,
        "employee_count": employee_count,
        "latest_run": latest,
        "latest_gross_cents": latest["gross_cents"] if latest else None,
        "latest_employer_taxes_cents": latest["employer_taxes_cents"] if latest else None,
        "latest_retirement_contributions_cents": latest["retirement_contributions_cents"] if latest else None,
        "open_issue_count": open_issue_count,
    }


def portal_summary(organization_id):
    """CLIENT-FACING, read-only, capability-free summary for the portal. The caller MUST have already
    validated portal access to this business (``client_can(..., organization_id=...)`` + portal scope) —
    this returns only client-safe headline numbers, never per-employee compensation or account internals."""
    _ensure_available()
    with engine.connect() as c:
        next_payroll_date = c.scalar(
            select(func.min(payroll_accounts.c.next_payroll_date)).where(
                payroll_accounts.c.organization_id == organization_id,
                payroll_accounts.c.status == "active",
                payroll_accounts.c.next_payroll_date.isnot(None)))
        employee_count = c.scalar(
            select(func.count()).select_from(payroll_employees).where(
                payroll_employees.c.organization_id == organization_id,
                payroll_employees.c.employment_status == "active")) or 0
        latest = c.execute(
            select(payroll_runs.c.pay_date, payroll_runs.c.gross_cents, payroll_runs.c.net_cents)
            .where(payroll_runs.c.organization_id == organization_id)
            .order_by(payroll_runs.c.pay_date.desc().nullslast(), payroll_runs.c.id.desc())
            .limit(1)).mappings().one_or_none()
    return {"organization_id": organization_id, "next_payroll_date": next_payroll_date,
            "employee_count": employee_count,
            "latest_pay_date": latest["pay_date"] if latest else None,
            "latest_gross_cents": latest["gross_cents"] if latest else None}
