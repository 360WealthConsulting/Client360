"""Client 360 Workspace (Phase D.40) — the master client record as a COMPOSITION surface.

`/client/{id}` composes a person's (or household's) full operational picture — summary, financial,
tax, insurance, benefits, opportunities, documents, meetings, compliance, activity timeline, and
relationships — read-only from the authoritative domain services. It is NOT a second client database
and never the source of truth: every edit is a deep link into the authoritative workflow (the queue
never mutates here). It preserves Runtime, Policy, RBAC, record scope, audit, and the transactional
outbox. Record scope is verified ONCE at the workspace boundary (some domain reads are person-keyed and
do not self-check), then sections fan out; a section the principal lacks capability for is omitted
(never shown-then-403); sections fail closed. No projection is duplicated — per-client reads are
authoritative composition.
"""
from .service import get_workspace  # noqa: F401

__all__ = ["get_workspace"]
