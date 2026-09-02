"""Document classification (Phase 6A) — the broad "what is this document?" step of the Knowledge pipeline.

Deterministic, rule-based classifier (v1) over the canonical document's filename + OCR text. No ML
dependency — patterns are ordered most-specific first so the result is stable and testable. Returns a
document type from the fixed vocabulary plus a confidence and the classifier version. A later phase can
add a learned/AI classifier behind the same interface; this module is the honest first pass.
"""
from __future__ import annotations

import re

CLASSIFIER_VERSION = "rules-v1"

# The fixed document vocabulary (Phase 6A spec).
DOC_TYPES = (
    "1040", "1041", "1065", "1120", "1120S", "W-2", "1099", "1095-A", "8879", "K-1",
    # Added alongside the originals. Every one already had a display label in
    # ``document_naming.DISPLAY_LABELS``, so the classifier was the narrower half of an existing
    # vocabulary — this closes the gap rather than inventing categories.
    "SSA-1099", "1098", "1098-E", "1098-T", "1095-B", "1095-C", "5498", "2553", "8824",
    "brokerage_statement", "bank_statement", "irs_notice", "state_notice",
    "drivers_license", "passport", "organizer", "engagement_letter", "insurance_policy",
    "benefits_enrollment", "trust_document", "estate_document", "financial_statement", "unknown",
)

_INSTITUTIONS = ("schwab", "fidelity", "vanguard", "merrill", "morgan stanley", "chase",
                 "wells fargo", "bank of america", "citibank", "td ameritrade", "raymond james")

# Ordered rules: (doc_type, [regex patterns], base_confidence). First rule whose pattern matches wins.
# Specific tax forms first; 1120S before 1120; notices before generic statements.
_RULES: list[tuple[str, list[str], float]] = [
    ("1120S", [r"\bform\s*1120-?s\b", r"\b1120s\b", r"u\.?s\.? income tax return for an s corporation"], 0.9),
    ("1120", [r"\bform\s*1120\b", r"u\.?s\.? corporation income tax return"], 0.9),
    ("1065", [r"\bform\s*1065\b", r"u\.?s\.? return of partnership income"], 0.9),
    ("1041", [r"\bform\s*1041\b", r"u\.?s\.? income tax return for estates and trusts"], 0.9),
    ("K-1", [r"\bschedule\s*k-?1\b", r"\bk-?1\b", r"partner'?s share of income"], 0.88),
    ("1040", [r"\bform\s*1040\b", r"\b1040\b", r"u\.?s\.? individual income tax return"], 0.9),
    ("W-2", [r"\bw-?2\b", r"wage and tax statement"], 0.9),
    ("1095-A", [r"\b1095-?a\b", r"health insurance marketplace statement"], 0.9),
    ("8879", [r"\b8879[-\s_]?s?\b", r"e-?file signature authorization",
              r"irs e-file signature"], 0.88),
    # SSA-1099 must precede the generic 1099 rule, or "SSA-1099" classifies as a plain 1099.
    ("SSA-1099", [r"\bssa-?1099\b", r"social security benefit statement"], 0.9),
    ("1099", [r"\b1099-?(?:int|div|b|misc|nec|r|g|k|s|q|sa|oid|patr)?\b", r"\bform\s*1099\b"], 0.85),
    # -E and -T precede bare 1098 for the same reason.
    ("1098-E", [r"\b1098-?e\b", r"student loan interest statement"], 0.9),
    ("1098-T", [r"\b1098-?t\b", r"tuition statement"], 0.9),
    ("1098", [r"\b1098\b", r"mortgage interest statement"], 0.88),
    ("1095-B", [r"\b1095-?b\b", r"health coverage statement"], 0.9),
    ("1095-C", [r"\b1095-?c\b", r"employer-provided health insurance offer"], 0.9),
    ("5498", [r"\b5498(?:-sa)?\b", r"ira contribution information"], 0.9),
    ("2553", [r"\b2553\b", r"election by a small business corporation"], 0.9),
    ("8824", [r"\b8824\b", r"like-kind exchange"], 0.9),
    ("irs_notice", [r"internal revenue service", r"\birs\b.*notice",
                    r"notice\s*(?:cp|ltr)\s*\d+",
                    # A bare CP/LTR notice number is how these commonly arrive as a filename.
                    r"\b(?:cp|ltr)-?\d{2,4}\b"], 0.8),
    ("state_notice", [r"(?:department|dept)\.? of revenue", r"franchise tax board",
                      r"state.*tax.*notice"], 0.75),
    ("engagement_letter", [r"engagement letter", r"terms of engagement"], 0.85),
    ("organizer", [r"\btax organizer\b", r"client organizer"], 0.85),
    ("passport", [r"\bpassport\b", r"united states of america.*passport"], 0.85),
    ("drivers_license", [r"driver'?s? licen[sc]e", r"\bdln\b", r"operator licen[sc]e"], 0.85),
    ("insurance_policy", [r"\bpolicy number\b", r"insurance policy", r"declarations page",
                          r"death benefit"], 0.75),
    ("benefits_enrollment", [r"benefits? enrollment", r"open enrollment", r"401\(k\) enrollment"], 0.75),
    ("trust_document", [r"\brevocable trust\b", r"\birrevocable trust\b", r"declaration of trust",
                        r"trust agreement"], 0.8),
    ("estate_document", [r"last will and testament", r"\bprobate\b", r"letters testamentary",
                         r"estate plan"], 0.8),
    ("brokerage_statement", [r"brokerage (?:account )?statement", r"account statement",
                             r"portfolio (?:summary|statement)"], 0.7),
    ("bank_statement", [r"bank statement", r"checking account", r"savings account statement"], 0.7),
    ("financial_statement", [r"balance sheet", r"income statement", r"statement of cash flows",
                             r"profit and loss"], 0.7),
]


def classify_document(name: str | None, text: str | None) -> tuple[str, float]:
    """Classify a canonical document from its filename + OCR text. Returns ``(doc_type, confidence)``.
    ``unknown`` with 0.0 confidence when no rule matches."""
    fname = (name or "").lower()
    body = (text or "").lower()
    haystack = f"{fname}\n{body}"
    for doc_type, patterns, base in _RULES:
        for pat in patterns:
            if re.search(pat, haystack):
                # A hit in BOTH the filename and the body is more convincing than one alone.
                in_name = re.search(pat, fname) is not None
                in_body = re.search(pat, body) is not None
                conf = min(0.99, base + 0.05) if (in_name and in_body) else base
                if in_name and not in_body:
                    conf = base - 0.05      # filename-only is slightly less certain than content
                return doc_type, round(conf, 3)
    return "unknown", 0.0


def institution_hints(text: str | None) -> list[str]:
    """Known financial-institution names present in the text (title-cased). Small, high-precision list."""
    body = (text or "").lower()
    return [inst.title() for inst in _INSTITUTIONS if inst in body]
