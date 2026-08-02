"""Client Workspace — Tax tab composition (read-only).

The tax operating center for one client, composed from the AUTHORITATIVE tax domain (engagements →
returns → missing items → filing events → lifecycle events → deadlines → document links) scoped to the
client's ownership (ADR-073), plus the existing exception/task/timeline services. It introduces no new
tax, document, workflow, task, or ownership logic and no new tables — it reads what the tax domain
already records.

Truthfulness (per the Tax-tab spec): concepts the platform does not yet model authoritatively —
estimated payments, carryforward balances, planning opportunities — are returned with an explicit
availability ``status`` (``not_connected`` / ``no_authoritative_source`` / ``pending_drake`` /
``requires_review`` / ``no_data``) so the UI renders an honest state rather than a fabricated record.
Acknowledgements / filing evidence are shown from real ``tax_filing_events`` rows when present, and as
``no_data`` (pending a filing provider such as Drake) when absent.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select

from app.db import (
    documents,
    engine,
    filing_jurisdictions,
    record_assignments,
    tasks,
    tax_deadlines,
    tax_document_links,
    tax_engagement_returns,
    tax_engagements,
    tax_filing_events,
    tax_missing_items,
    tax_return_lifecycle_events,
    tax_return_types,
    tax_years,
)

AVAILABLE = "available"
NO_DATA = "no_data"
NOT_CONNECTED = "not_connected"
NO_AUTHORITATIVE_SOURCE = "no_authoritative_source"
PENDING_DRAKE = "pending_drake"
REQUIRES_REVIEW = "requires_review"

_OPEN_RETURN_STATES = {"filed", "accepted", "completed", "delivered"}


def _safe(fn, default):
    try:
        return fn()
    except Exception:      # noqa: BLE001 — one section must never break the Tax tab
        return default


def _returns(conn, person_ids, household_id):
    conds = []
    if person_ids:
        conds.append(tax_engagements.c.person_id.in_(list(person_ids)))
    if household_id:
        conds.append(tax_engagements.c.household_id == household_id)
    if not conds:
        return []
    q = (select(
            tax_engagement_returns.c.id.label("return_id"),
            tax_engagements.c.id.label("engagement_id"),
            tax_engagements.c.engagement_type, tax_engagements.c.status.label("engagement_status"),
            tax_years.c.year, tax_return_types.c.code.label("return_type"),
            tax_return_types.c.name.label("return_type_name"), tax_return_types.c.entity_type,
            filing_jurisdictions.c.code.label("jurisdiction"),
            tax_engagement_returns.c.status, tax_engagement_returns.c.filing_status,
            tax_engagement_returns.c.preparation_started_at,
            tax_engagement_returns.c.preparation_completed_at,
            tax_engagement_returns.c.filed_at, tax_engagement_returns.c.accepted_at,
            tax_engagement_returns.c.delivered_at, tax_engagement_returns.c.filing_external_id,
            tax_deadlines.c.due_date)
         .select_from(tax_engagements
            .join(tax_engagement_returns,
                  tax_engagement_returns.c.tax_engagement_id == tax_engagements.c.id)
            .join(tax_return_types, tax_return_types.c.id == tax_engagement_returns.c.return_type_id)
            .join(tax_years, tax_years.c.id == tax_engagements.c.tax_year_id)
            .outerjoin(filing_jurisdictions,
                       filing_jurisdictions.c.id == tax_engagement_returns.c.jurisdiction_id)
            .outerjoin(tax_deadlines,
                       tax_deadlines.c.tax_engagement_return_id == tax_engagement_returns.c.id))
         .where(or_(*conds))
         .order_by(tax_years.c.year.desc(), tax_engagement_returns.c.id.desc()))
    return [dict(r) for r in conn.execute(q).mappings()]


def _linked_documents(conn, return_ids):
    if not return_ids:
        return {}
    rows = conn.execute(
        select(tax_document_links.c.tax_engagement_return_id, documents.c.id, documents.c.original_name)
        .select_from(tax_document_links.join(documents, documents.c.id == tax_document_links.c.document_id))
        .where(tax_document_links.c.tax_engagement_return_id.in_(return_ids))).mappings().all()
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r["tax_engagement_return_id"], []).append(
            {"id": r["id"], "name": r["original_name"]})
    return out


def _assignments(conn, return_ids):
    if not return_ids:
        return {}
    rows = conn.execute(
        select(record_assignments.c.entity_id, record_assignments.c.assignment_type,
               record_assignments.c.user_id)
        .where(record_assignments.c.entity_type == "tax_return",
               record_assignments.c.entity_id.in_(return_ids),
               record_assignments.c.inactive_date.is_(None))).mappings().all()
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r["entity_id"], []).append(
            {"role": r["assignment_type"], "user_id": r["user_id"]})
    return out


def _missing_items(conn, return_ids):
    if not return_ids:
        return []
    rows = conn.execute(
        select(tax_missing_items.c.id, tax_missing_items.c.item_type, tax_missing_items.c.title,
               tax_missing_items.c.status, tax_missing_items.c.due_date,
               tax_missing_items.c.tax_engagement_return_id)
        .where(tax_missing_items.c.tax_engagement_return_id.in_(return_ids),
               tax_missing_items.c.status != "resolved")
        .order_by(tax_missing_items.c.due_date.nullslast())).mappings().all()
    return [dict(r) for r in rows]


def _filing_events(conn, return_ids):
    if not return_ids:
        return []
    rows = conn.execute(
        select(tax_filing_events.c.filing_status, tax_filing_events.c.provider_key,
               tax_filing_events.c.external_id, tax_filing_events.c.submission_id,
               tax_filing_events.c.reason_code, tax_filing_events.c.message,
               tax_filing_events.c.created_at, tax_filing_events.c.tax_engagement_return_id)
        .where(tax_filing_events.c.tax_engagement_return_id.in_(return_ids))
        .order_by(tax_filing_events.c.created_at.desc())).mappings().all()
    return [dict(r) for r in rows]


def _timeline(conn, return_ids):
    """One chronological tax history: lifecycle transitions + filing events."""
    if not return_ids:
        return []
    events = []
    for r in conn.execute(
            select(tax_return_lifecycle_events.c.to_status, tax_return_lifecycle_events.c.from_status,
                   tax_return_lifecycle_events.c.reason, tax_return_lifecycle_events.c.created_at)
            .where(tax_return_lifecycle_events.c.tax_engagement_return_id.in_(return_ids))).mappings():
        events.append({"kind": "lifecycle", "label": f"{r['from_status'] or '—'} → {r['to_status']}",
                       "detail": r["reason"], "at": r["created_at"]})
    for r in conn.execute(
            select(tax_filing_events.c.filing_status, tax_filing_events.c.message,
                   tax_filing_events.c.created_at)
            .where(tax_filing_events.c.tax_engagement_return_id.in_(return_ids))).mappings():
        events.append({"kind": "filing", "label": f"filing: {r['filing_status']}",
                       "detail": r["message"], "at": r["created_at"]})
    return sorted(events, key=lambda e: e["at"] or _MIN, reverse=True)


class _Min:
    def __lt__(self, other):
        return True


_MIN = _Min()


def _tax_tasks(conn, scope_ids):
    ids = [i for i in scope_ids if i]
    if not ids:
        return []
    rows = conn.execute(
        select(tasks.c.id, tasks.c.title, tasks.c.status, tasks.c.priority, tasks.c.due_date,
               tasks.c.assigned_to, tasks.c.workflow_name, tasks.c.work_type)
        .where(tasks.c.person_id.in_(ids), tasks.c.status != "complete",
               or_(tasks.c.work_type.ilike("%tax%"), tasks.c.workflow_name.ilike("%tax%")))
        .order_by(tasks.c.due_date.nullslast()).limit(50)).mappings().all()
    return [dict(r) for r in rows]


def build_tax_workspace(principal, *, person_id=None, household_id=None, scope_ids=None):
    """Compose the Tax tab for one client. Read-only; scope is already verified at the workspace
    boundary. Returns the ten Tax-tab sections, each carrying an honest availability status.

    ``scope_ids`` is the set of member person ids in scope (just ``[person_id]`` for an individual);
    tax engagements keyed to any of them — or to the household — are included."""
    scope_ids = scope_ids or ([person_id] if person_id else [])
    with engine.connect() as conn:
        returns = _safe(lambda: _returns(conn, scope_ids, household_id), [])
        return_ids = [r["return_id"] for r in returns]
        linked = _safe(lambda: _linked_documents(conn, return_ids), {})
        assigns = _safe(lambda: _assignments(conn, return_ids), {})
        for r in returns:
            r["documents"] = linked.get(r["return_id"], [])
            r["assignments"] = assigns.get(r["return_id"], [])
        missing = _safe(lambda: _missing_items(conn, return_ids), [])
        filing = _safe(lambda: _filing_events(conn, return_ids), [])
        timeline = _safe(lambda: _timeline(conn, return_ids), [])
        tax_tasks = _safe(lambda: _tax_tasks(conn, scope_ids), [])

    tax_exceptions = _safe(
        lambda: [e for e in _open_exceptions(person_id, household_id, scope_ids)
                 if e.get("domain") == "tax"], [])
    k1s = [m for m in missing if "k1" in str(m.get("item_type", "")).lower()
           or "k-1" in str(m.get("title", "")).lower()]

    current = returns[0] if returns else None
    today = date.today()
    summary = {
        "status": AVAILABLE if returns else NO_DATA,
        "current": current,
        "return_count": len(returns),
        "open_missing": len(missing),
        "open_exceptions": len(tax_exceptions),
        "open_tasks": len(tax_tasks),
        "next_deadline": min((r["due_date"] for r in returns if r.get("due_date")), default=None),
        "filed": bool(current and current.get("filed_at")),
        "accepted": bool(current and current.get("accepted_at")),
    }

    return {
        "status_summary": summary,
        "return_history": {"status": AVAILABLE if returns else NO_DATA, "returns": returns},
        "missing_and_exceptions": {
            "status": AVAILABLE if (missing or tax_exceptions) else NO_DATA,
            "missing_items": missing, "exceptions": tax_exceptions},
        "acknowledgements": {
            "status": AVAILABLE if filing else NO_DATA,
            "events": filing,
            "note": "Filing evidence is recorded per submission; none yet — a filing provider "
                    "(e.g. Drake) populates IRS/state acknowledgements." if not filing else None},
        "estimated_payments": {
            "status": NO_AUTHORITATIVE_SOURCE,
            "note": "Estimated payments are not yet modelled in Client360 — pending Drake integration."},
        "carryforwards_planning": {
            "status": REQUIRES_REVIEW,
            "note": "Carryforwards and planning items have no authoritative source yet; review manually "
                    "until Drake / planning data is connected."},
        "k1_tracking": {"status": AVAILABLE if k1s else NO_DATA, "k1_items": k1s,
                        "note": "Issuer-level K-1 completeness is pending Drake integration."},
        "extensions_deadlines": {
            "status": AVAILABLE if any(r.get("due_date") for r in returns) else NO_DATA,
            "deadlines": [{"year": r["year"], "return_type": r["return_type"],
                           "due_date": r.get("due_date"),
                           "overdue": bool(r.get("due_date") and r["due_date"] < today
                                           and r["status"] not in _OPEN_RETURN_STATES)}
                          for r in returns if r.get("due_date")]},
        "tax_tasks": {"status": AVAILABLE if tax_tasks else NO_DATA, "tasks": tax_tasks},
        "filing_timeline": {"status": AVAILABLE if timeline else NO_DATA, "events": timeline},
    }


def _open_exceptions(person_id, household_id, scope_ids):
    from app.services.exception_engine import (
        open_exceptions_for_client,
        open_exceptions_for_people,
    )
    if household_id and scope_ids:
        return open_exceptions_for_people(set(scope_ids))
    if person_id:
        return open_exceptions_for_client(person_id, household_id)
    return []
