"""Advisor AI Assist (Phase D.42) — governed, READ-ONLY briefing intelligence with human review.

A read-only surface that summarizes, explains, compares, and NAVIGATES over the deterministic summaries
and snapshots already produced by D.38–D.41 (Advisor Workspace, Unified Work Queue, Client 360,
Household 360, meeting brief). It is NOT a business-rules, policy, workflow, recommendation, or mutation
engine and is NEVER a source of truth. It never creates/updates/deletes/approves/assigns/files/submits/
sends/completes a business record — every proposed action is a DEEP LINK into an authoritative workflow.

Invariants: the Runtime Engine remains the sole evaluator; the Runtime Policy Engine remains the sole
decision engine; the authoritative domain services remain the sole mutation layer; the transactional
outbox remains the sole event bus (AI reads publish nothing). Every response is grounded in
platform-provided facts, carries internal citations + limitations, is labelled "Advisor Assist — Review
Required", and refuses regulated requests (trade/tax/legal/compliance/suitability/autonomous). The
default provider is a deterministic, offline LocalProvider — the suite runs with no network/credentials.
"""
from .assistant import (  # noqa: F401
    client_brief,
    daily_brief,
    household_brief,
    meeting_prep,
    work_explanation,
)

__all__ = ["daily_brief", "client_brief", "household_brief", "meeting_prep", "work_explanation"]
