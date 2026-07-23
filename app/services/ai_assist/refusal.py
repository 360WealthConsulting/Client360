"""Regulated-request refusal for Advisor AI Assist (Phase D.42).

The assistant is read-only and must refuse or constrain requests for trade recommendations, tax filing
conclusions, legal advice, compliance/suitability approval, autonomous actions, or unsupported
predictions. Detection is deterministic keyword matching; on a match the assistant returns a constrained
refusal with a suggested authoritative deep link.
"""
from __future__ import annotations

# category → (keyword substrings, refusal message, suggested authoritative deep link)
REGULATED = {
    "trade_recommendation": (
        ("buy", "sell", "should i invest", "recommend a fund", "recommend an investment", "rebalance",
         "allocate", "which stock", "trade recommendation", "what should i buy", "should we sell"),
        "Advisor AI Assist does not make investment or trade recommendations. Review the portfolio and "
        "decide within the authoritative workflow.", "/portfolio"),
    "tax_conclusion": (
        ("file my taxes", "should i deduct", "tax deduction", "claim on taxes", "tax advice",
         "how should i file", "is this deductible", "file the return"),
        "Advisor AI Assist does not provide tax filing conclusions. Use the Tax workflow.", "/tax"),
    "legal_advice": (
        ("legal advice", "is this legal", "should i sue", "lawsuit", "am i liable", "legally required"),
        "Advisor AI Assist does not provide legal advice.", None),
    "compliance_approval": (
        ("approve", "sign off", "authorize the review", "clear the review", "pass the review"),
        "Advisor AI Assist cannot approve compliance items. Decisions are made in the Compliance "
        "workflow.", "/compliance"),
    "suitability_determination": (
        ("is this suitable", "suitability", "is this appropriate for", "recommend this product for"),
        "Advisor AI Assist does not make suitability determinations.", None),
    "autonomous_action": (
        ("do it for me", "execute the", "send the message", "submit the", "complete the task",
         "assign it", "file the", "just do it", "apply the suggestion", "approve all"),
        "Advisor AI Assist is read-only and cannot take actions. Open the authoritative workflow to "
        "proceed.", "/work"),
    "unsupported_prediction": (
        ("predict the market", "forecast the market", "will the market", "future return", "guarantee"),
        "Advisor AI Assist does not make market predictions.", None),
    "client_communication": (
        ("draft an email to the client", "send to the client", "message the client", "email the client"),
        "Advisor AI Assist does not send client communications; any drafted content requires advisor "
        "review in the Communications workflow.", "/communications"),
}


def check_regulated(question):
    """Return (category, message, suggested_link) for the first regulated match, else None."""
    q = (question or "").lower()
    for category, (keywords, message, link) in REGULATED.items():
        if any(kw in q for kw in keywords):
            return category, message, link
    return None
