"""Compliance exceptions adapter (Phase D.47).

Derives registered compliance exceptions from their AUTHORITATIVE sources — the exception engine (the single
authoritative exception owner) plus the portfolio cadence reads — and normalizes them to the
``ComplianceException`` model. It NEVER raises, opens, resolves, or mutates an exception (that stays with
``exception_engine``). Only exceptions that map to a registered exception type are emitted; unmapped ones are
suppressed (counted). ``person_ids``: ``None`` = firm-wide (record.read_all), ``set`` = restricted,
``()`` = none.
"""
from __future__ import annotations

from .. import stats
from ..model import ComplianceException

# keyword → registered exception type (for exception_engine rows).
_KEYWORD_MAP = (
    ("beneficiar", "missing_beneficiary"),
    ("disclosure", "unsigned_disclosure"),
    ("signature", "unsigned_disclosure"),
    ("document", "missing_document"),
    ("missing doc", "missing_document"),
    ("stale", "stale_financial_information"),
    ("outdated", "stale_financial_information"),
)


def _classify(row):
    text = f"{row.get('category', '')} {row.get('title', '')}".lower()
    for kw, etype in _KEYWORD_MAP:
        if kw in text:
            return etype
    return None


def _exc(etype, *, ex_id, severity, status, title, summary, explanation, evidence, person_id=None,
         household_id=None):
    from .. import registry
    tdef = registry.exception_type(etype)
    return ComplianceException(
        exception_id=ex_id, exception_type=etype, severity=severity, status=status, title=title,
        summary=summary, explanation=explanation, governing_policy=(tdef.governing_policy if tdef else "policy"),
        evidence=evidence, owner=(tdef.owner if tdef else "exception_engine"),
        escalation=(tdef.escalation if tdef else "compliance officer"),
        deep_link="/compliance", related_person_id=person_id, related_household_id=household_id)


def compliance_exceptions(principal, person_ids):
    """Return the registered compliance exceptions for a book/person set. Never raises."""
    out = []
    # 1. Exception-engine rows (compliance-relevant), mapped to registered types.
    try:
        from app.services.exception_engine import open_exceptions_for_people
        rows = open_exceptions_for_people(person_ids)
    except Exception:
        stats.note("adapter_failures", source="exception_engine")
        rows = []
    for row in rows:
        etype = _classify(row)
        if etype is None:
            stats.note("suppressed")
            continue
        out.append(_exc(etype, ex_id=f"exc:{etype}:exception:{row.get('id')}",
                        severity=row.get("severity") or "medium", status=row.get("status") or "open",
                        title=row.get("title") or etype.replace("_", " ").title(),
                        summary=f"{row.get('domain', '')} exception in status {row.get('status')}.".strip(),
                        explanation=f"An open exception ({etype.replace('_', ' ')}) is recorded by the "
                                    f"exception engine in the {row.get('domain')} domain.",
                        evidence=(f"exception_id={row.get('id')}", f"domain={row.get('domain')}",
                                  f"severity={row.get('severity')}"),
                        person_id=row.get("person_id"), household_id=row.get("household_id")))
        stats.note("exceptions_composed", severity=row.get("severity"))
    # 2. Missing required beneficiary (portfolio cadence — authoritative read).
    try:
        from app.services.portfolio import accounts_missing_required_beneficiary
        for a in accounts_missing_required_beneficiary(person_ids):
            out.append(_exc("missing_beneficiary", ex_id=f"exc:missing_beneficiary:account:{a.get('id')}",
                            severity="medium", status="open",
                            title=f"Missing beneficiary — {a.get('account_name') or 'account'}",
                            summary="A retirement account has no active beneficiary designation.",
                            explanation="A retirement account is missing a required beneficiary designation, "
                                        "per portfolio.accounts_missing_required_beneficiary.",
                            evidence=(f"account_id={a.get('id')}",
                                      f"registration={a.get('registration_type')}"),
                            person_id=a.get("person_id"), household_id=a.get("household_id")))
            stats.note("exceptions_composed", severity="medium")
    except Exception:
        stats.note("adapter_failures", source="portfolio.beneficiary")
    # 3. Overdue account reviews (portfolio cadence).
    try:
        from app.services.portfolio import accounts_due_for_review
        for a in accounts_due_for_review(person_ids):
            out.append(_exc("overdue_review", ex_id=f"exc:overdue_review:account:{a.get('id')}",
                            severity="high", status="open",
                            title=f"Overdue review — {a.get('account_name') or 'account'}",
                            summary="An account is overdue for its periodic review.",
                            explanation="An account's last review date is missing or older than the review "
                                        "cadence threshold, per portfolio.accounts_due_for_review.",
                            evidence=(f"account_id={a.get('id')}",
                                      f"last_review_date={a.get('last_review_date')}"),
                            person_id=a.get("person_id"), household_id=a.get("household_id")))
            stats.note("exceptions_composed", severity="high")
            stats.note("overdue_reviews")
    except Exception:
        stats.note("adapter_failures", source="portfolio.review")
    return [e for e in out if e.is_explainable]
