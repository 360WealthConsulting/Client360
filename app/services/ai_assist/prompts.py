"""Governed, versioned prompt templates for Advisor AI Assist (Phase D.42).

Prompts are declarative + versioned with ownership/lifecycle metadata. Every prompt states the assistant
is read-only and enforces grounding + the prohibited-use constraints. The default provider is
deterministic and does not require these strings, but they are the contract a real model provider would
be given (and governance verifies each carries the read-only + grounding constraints).
"""
from __future__ import annotations

# The constraints every prompt MUST contain (governance-verified substrings).
REQUIRED_CONSTRAINTS = (
    "read-only",
    "only the supplied context",
    "cite",
    "state when data is missing",
    "must not create, update, delete, approve, assign, file, submit, send, or complete",
    "no investment, tax, legal, insurance, or suitability advice",
    "no policy or compliance decision",
)

_SHARED_CONSTRAINTS = (
    "You are Advisor Assist, a READ-ONLY briefing assistant. "
    "Use ONLY the supplied context facts; never invent, infer, or predict. "
    "You must not create, update, delete, approve, assign, file, submit, send, or complete any record — "
    "propose actions only as deep links into the authoritative workflow. "
    "Provide no investment, tax, legal, insurance, or suitability advice; make no policy or compliance "
    "decision or approval. "
    "Cite every factual statement to its supplied source, list limitations, and state when data is "
    "missing (say 'Not tracked' or 'Unavailable'). Return the required structured fields; never omit "
    "citations or limitations."
)


def _p(version, capability, owner, lifecycle, body):
    return {"version": version, "capability": capability, "owner": owner, "lifecycle": lifecycle,
            "template": _SHARED_CONSTRAINTS + "\n\nTask: " + body}


PROMPTS = {
    "daily_brief": _p("1.0.0", "daily_brief", "Advisor Experience", "active",
                      "Summarize the advisor's day from the supplied daily-brief, priorities, and "
                      "work-queue summary facts. Highlight top priorities, meetings, overdue work, and "
                      "compliance-sensitive items; suggest navigation links."),
    "client_brief": _p("1.0.0", "client_brief", "Advisor Experience", "active",
                       "Summarize the client from the supplied Client 360 snapshot facts. Present "
                       "financial, work, opportunities, tax, insurance, benefits, and compliance side by "
                       "side (never summed) and suggest navigation links."),
    "household_brief": _p("1.0.0", "household_brief", "Advisor Experience", "active",
                          "Summarize the household from the supplied Household 360 snapshot facts: "
                          "members, primary member, combined portfolio (never summed with other figures), "
                          "shared work, opportunities, meetings, compliance; suggest navigation links."),
    "meeting_prep": _p("1.0.0", "meeting_prep", "Advisor Experience", "active",
                       "Prepare the advisor for a meeting from the supplied minimized meeting-brief "
                       "facts. Organize prior context, open items, deadlines, and questions the advisor "
                       "may consider. Do not make suitability determinations or generate regulated "
                       "advice."),
    "work_explanation": _p("1.0.0", "work_explanation", "Advisor Experience", "active",
                           "Explain the supplied work item: its owning domain, status, due date, why it "
                           "surfaced, the linked client/household, and the permitted authoritative next "
                           "step (deep link only)."),
    "factual_question_answering": _p("1.0.0", "factual_question_answering", "Advisor Experience", "active",
                                     "Answer the advisor's factual question using ONLY the supplied "
                                     "context. If the answer is not present, say so and mark the question "
                                     "unsupported. Refuse regulated requests."),
}


def prompt_version(capability) -> str | None:
    p = PROMPTS.get(capability)
    return p["version"] if p else None
