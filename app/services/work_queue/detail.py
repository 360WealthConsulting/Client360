"""Unified work-item DETAIL composer (read-only).

Given a ``(source_domain, source_id)`` this assembles everything the work-item detail screen needs by
REUSING the existing engine — it does not introduce a parallel store:

  * the normalized item itself, via ``service.collect`` (same adapters / scope / capability as the queue);
  * the authoritative assignments for the item's entity (``record_assignments``);
  * open/decided approvals (``work_approvals``);
  * linked Vault documents (``vault_document_links.work_item_id`` — the existing column, no new schema);
  * a merged history from the owning domain's immutable event log + assignment events + the client timeline.

Scope is already enforced by ``collect`` (an item the principal may not see never appears), so the detail
returns ``None`` (→ route 404) for an unknown or out-of-scope item.
"""
from __future__ import annotations

from sqlalchemy import desc, select

from app.db import (
    advisor_work_events,
    assignment_events,
    engine,
    record_assignments,
    users,
    vault_document_links,
    vault_documents,
    work_approvals,
    workflow_events,
)
from app.services.work_queue.dispatch import ASSIGN_ENTITY
from app.services.work_queue.service import collect


def _find_item(principal, domain, source_id):
    items, _ = collect(principal)
    target = str(source_id)
    for it in items:
        if it.source_domain == domain and str(it.source_id) == target:
            return it
    return None


def _assignments(conn, entity_type, entity_id):
    if not entity_type:
        return []
    rows = conn.execute(
        select(record_assignments.c.id, record_assignments.c.user_id, record_assignments.c.team_id,
               record_assignments.c.assignment_type, record_assignments.c.effective_date,
               users.c.display_name)
        .select_from(record_assignments.outerjoin(users, users.c.id == record_assignments.c.user_id))
        .where(record_assignments.c.entity_type == entity_type,
               record_assignments.c.entity_id == entity_id,
               record_assignments.c.inactive_date.is_(None))
        .order_by(record_assignments.c.assignment_type)).mappings().all()
    return [dict(r) for r in rows]


def _approvals(conn, entity_type, entity_id):
    if not entity_type:
        return []
    rows = conn.execute(
        select(work_approvals).where(work_approvals.c.entity_type == entity_type,
                                     work_approvals.c.entity_id == entity_id)
        .order_by(desc(work_approvals.c.created_at))).mappings().all()
    return [dict(r) for r in rows]


def _documents(conn, source_id):
    """Vault documents linked to this work item via the existing work_item_id column."""
    rows = conn.execute(
        select(vault_documents.c.id, vault_documents.c.display_name, vault_documents.c.category,
               vault_documents.c.status, vault_documents.c.client_visible, vault_documents.c.created_at)
        .select_from(vault_documents.join(
            vault_document_links, vault_document_links.c.document_id == vault_documents.c.id))
        .where(vault_document_links.c.work_item_id == source_id)
        .distinct().order_by(desc(vault_documents.c.created_at))).mappings().all()
    return [dict(r) for r in rows]


def _history(conn, domain, entity_type, source_id):
    events = []
    if domain == "advisor_work":
        for r in conn.execute(
            select(advisor_work_events).where(advisor_work_events.c.advisor_work_item_id == source_id)
            .order_by(desc(advisor_work_events.c.occurred_at))).mappings():
            events.append({"when": r["occurred_at"], "type": r["event_type"],
                           "detail": f"{r['prior_status']} → {r['new_status']}" if r["new_status"] else r["note"],
                           "actor": r["actor_principal_id"]})
    if domain == "workflow":
        for r in conn.execute(
            select(workflow_events).where(workflow_events.c.workflow_step_id == source_id)
            .order_by(desc(workflow_events.c.occurred_at))).mappings():
            events.append({"when": r["occurred_at"], "type": r["event_type"],
                           "detail": None, "actor": r["actor_user_id"]})
    if entity_type:
        for r in conn.execute(
            select(assignment_events).where(assignment_events.c.entity_type == entity_type,
                                            assignment_events.c.entity_id == source_id)
            .order_by(desc(assignment_events.c.id))).mappings():
            events.append({"when": None, "type": r["event_type"],
                           "detail": r.get("reason"), "actor": r["actor_user_id"]})
    return events


def work_item_detail(principal, domain, source_id):
    """Compose the full detail for one work item, or ``None`` if unknown / out of scope."""
    item = _find_item(principal, domain, source_id)
    if item is None:
        return None
    entity_type = ASSIGN_ENTITY.get(domain)
    with engine.connect() as conn:
        assignments = _assignments(conn, entity_type, source_id)
        approvals = _approvals(conn, entity_type, source_id)
        documents = _documents(conn, source_id)
        history = _history(conn, domain, entity_type, source_id)
    return {
        "item": item.to_dict(),
        "entity_type": entity_type,
        "source_domain": domain,
        "source_id": source_id,
        "assignments": assignments,
        "approvals": approvals,
        "documents": documents,
        "history": history,
        "can_assign": entity_type is not None,
    }
