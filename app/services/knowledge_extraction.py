"""Structured extraction (Phase 6A) — the pluggable "what does this document say?" step.

A small registry of deterministic, regex/keyword extractors turns a classified document's OCR text into
candidate facts. Each extractor is independent and returns zero or more ``Fact`` dicts; adding a new one
is a single ``@extractor`` registration — the pipeline calls them all. No ML dependency.

PRIVACY: full SSNs are never extracted or stored — only the last four digits (``ssn_last4``). Any 9-digit
SSN pattern contributes only its last four.
"""
from __future__ import annotations

import re
from datetime import date

EXTRACTOR_VERSION = "extract-v1"

# fact_type constants (the Phase 6A initial set).
FACT_TYPES = (
    "tax_year", "document_year", "client_name", "spouse_name", "entity_name", "ein",
    "ssn_last4", "date", "dollar_amount", "employer_name", "financial_institution", "return_type",
    "filing_status",
)

_REGISTRY: list = []


def extractor(fn):
    _REGISTRY.append(fn)
    return fn


def _fact(fact_type, value, confidence):
    return {"fact_type": fact_type, "value": str(value), "confidence": round(float(confidence), 3)}


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_EIN_RE = re.compile(r"\b(\d{2}-\d{7})\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-(\d{4})\b")
_SSN_MASKED_RE = re.compile(r"(?:xxx|\*\*\*)[-\s]?(?:xx|\*\*)[-\s]?(\d{4})", re.IGNORECASE)
_MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_LABEL_NAME_RE = {
    "client_name": re.compile(r"(?:taxpayer|client name|name of taxpayer)\s*[:\-]\s*([A-Z][A-Za-z.'\- ]{2,60})"),
    "spouse_name": re.compile(r"(?:spouse|spouse'?s name)\s*[:\-]\s*([A-Z][A-Za-z.'\- ]{2,60})"),
    "entity_name": re.compile(r"(?:entity name|name of (?:entity|partnership|corporation)|business name)\s*[:\-]\s*([A-Z0-9][A-Za-z0-9.,'\-& ]{2,80})"),
    "employer_name": re.compile(r"(?:employer|employer'?s name)\s*[:\-]?\s*([A-Z0-9][A-Za-z0-9.,'\-& ]{2,80})"),
}
_FILING_STATUS_RE = re.compile(
    r"(married filing jointly|married filing separately|head of household|qualifying (?:widow|surviving spouse)|single)",
    re.IGNORECASE)
_INSTITUTIONS = ("Schwab", "Fidelity", "Vanguard", "Merrill", "Morgan Stanley", "Chase",
                 "Wells Fargo", "Bank of America", "Citibank", "TD Ameritrade", "Raymond James")
_TAX_FORM_TYPES = {"1040", "1041", "1065", "1120", "1120S"}


@extractor
def _years(ctx):
    text = ctx["text"] or ""
    years = [int(m.group(0)) for m in _YEAR_RE.finditer(text)]
    years = [y for y in years if 1990 <= y <= date.today().year + 1]
    if not years:
        return []
    # The most frequent plausible year is the document/tax year.
    best = max(set(years), key=years.count)
    facts = [_fact("document_year", best, 0.7)]
    if ctx.get("doc_type") in _TAX_FORM_TYPES or ctx.get("doc_type") in ("W-2", "1099", "K-1"):
        facts.append(_fact("tax_year", best, 0.75))
    return facts


@extractor
def _ein(ctx):
    m = _EIN_RE.search(ctx["text"] or "")
    return [_fact("ein", m.group(1), 0.85)] if m else []


@extractor
def _ssn_last4(ctx):
    text = ctx["text"] or ""
    m = _SSN_RE.search(text) or _SSN_MASKED_RE.search(text)
    # Only ever the last four digits — the full SSN is never captured or stored.
    return [_fact("ssn_last4", m.group(1), 0.8)] if m else []


@extractor
def _money(ctx):
    seen, facts = set(), []
    for m in _MONEY_RE.finditer(ctx["text"] or ""):
        val = m.group(1)
        if val not in seen:
            seen.add(val)
            facts.append(_fact("dollar_amount", f"${val}", 0.6))
        if len(facts) >= 10:
            break
    return facts


@extractor
def _dates(ctx):
    seen, facts = set(), []
    for m in _DATE_RE.finditer(ctx["text"] or ""):
        val = m.group(1)
        if val not in seen:
            seen.add(val)
            facts.append(_fact("date", val, 0.65))
        if len(facts) >= 10:
            break
    return facts


@extractor
def _labeled_names(ctx):
    text = ctx["text"] or ""
    facts = []
    for fact_type, rx in _LABEL_NAME_RE.items():
        m = rx.search(text)
        if m:
            facts.append(_fact(fact_type, m.group(1).strip(), 0.7))
    return facts


@extractor
def _institutions(ctx):
    text = ctx["text"] or ""
    facts, seen = [], set()
    for inst in _INSTITUTIONS:
        if inst.lower() in text.lower() and inst not in seen:
            seen.add(inst)
            facts.append(_fact("financial_institution", inst, 0.75))
    return facts


@extractor
def _return_type(ctx):
    dt = ctx.get("doc_type")
    if dt in _TAX_FORM_TYPES:
        return [_fact("return_type", dt, 0.9)]
    return []


@extractor
def _filing_status(ctx):
    m = _FILING_STATUS_RE.search(ctx["text"] or "")
    return [_fact("filing_status", m.group(1).lower(), 0.75)] if m else []


def extract_facts(*, name: str | None, text: str | None, doc_type: str | None) -> list[dict]:
    """Run every registered extractor over the document. Returns a de-duplicated list of fact dicts
    ``{fact_type, value, confidence}`` — validation/persistence is the pipeline's job."""
    ctx = {"name": name or "", "text": text or "", "doc_type": doc_type}
    facts: list[dict] = []
    for fn in _REGISTRY:
        try:
            facts.extend(fn(ctx) or [])
        except Exception:      # noqa: BLE001 — a broken extractor must not sink the whole document
            continue
    return facts
