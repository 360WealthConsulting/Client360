"""Unified Work Queue (Phase D.39) — a cross-domain COMPOSITION surface over the authoritative work
services. It normalizes, ranks, filters, and presents actionable work from the existing task, workflow,
exception, compliance, document, tax, insurance, and opportunity/meeting services into one governed
queue. It is NOT a task/workflow/exception/assignment engine and is NEVER the source of truth: every
state-changing action delegates to the authoritative owning service, which audits and (where it does)
publishes its own domain event through the transactional outbox. Projections stay disposable read
models — counts reuse the D.37 adoption layer with graceful fallback; the queue never reads an ``rm_*``
table directly. RBAC, record-scope, Runtime, and Policy are always preserved; adapters fail closed.
"""
from .service import compose_queue  # noqa: F401

__all__ = ["compose_queue"]
