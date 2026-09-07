"""The ONE canonical filing-service vocabulary.

WHY THIS MODULE EXISTS
----------------------
Client360 grew two unrelated service vocabularies and neither knew about the other:

* ``service_lines`` — nine rows (``tax``, ``wealth``, ``benefits``, ``retirement``, ``insurance``,
  ``bookkeeping``, ``payroll``, ``consulting``, ``estate_coordination``). A clean vocabulary with
  **nothing pointing at it**: ``engagements``, ``organization_service_lines``, ``service_agreements``
  and ``invoice_line_items`` all carry a ``service_line_id`` and all are empty.
* The filing labels the firm actually uses, which exist only as SharePoint folder names mapped by
  ``document_filing_preview.SHAREPOINT_CATEGORY_MAP`` — "Tax Preparation", "Sales & Litter Tax",
  "Wealth Accounts" and so on.

The second is the one with data behind it; the first is the one the schema is built for. They do not
line up: ``service_lines`` has no row for **Sales & Litter Tax**, which is the second-largest filing
service in the corpus, and its ``tax`` row covers what filing splits three ways (Tax Preparation,
Tax Resolution, 1099 Processing).

This module is the single reconciliation point. It defines a canonical CODE per filing service,
carries the display label, and records how each maps onto ``service_lines`` — including, honestly,
the ones that have no counterpart yet.

CODE vs LABEL, AND WHY FOLDER IDENTITY USES THE CODE
-----------------------------------------------------
:data:`CANONICAL_SERVICE_CODES` is the identity. The label is display text and may be re-worded
without moving a single folder. A folder's natural identity therefore keys on the code, never on
"Sales & Litter Tax" — one renamed label would otherwise fork every folder beneath it.

NOTHING HERE RENAMES PRODUCTION DATA. :data:`SERVICE_LINE_SEED_ROWS` describes the rows a future
migration would add to ``service_lines``; it is a description, not an action.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class FilingService(NamedTuple):
    """One canonical filing service."""

    code: str
    label: str
    #: ``service_lines.code`` this maps onto, or ``None`` when no counterpart exists yet.
    service_line_code: str | None
    #: True when ``service_lines`` would need a new row for this service.
    needs_service_line_row: bool


#: The canonical vocabulary. Order is display order; the code is identity.
FILING_SERVICES: tuple[FilingService, ...] = (
    FilingService("tax_preparation", "Tax Preparation", "tax", False),
    FilingService("sales_litter_tax", "Sales & Litter Tax", None, True),
    FilingService("payroll", "Payroll", "payroll", False),
    FilingService("bookkeeping", "Bookkeeping", "bookkeeping", False),
    FilingService("wealth_accounts", "Wealth Accounts", "wealth", False),
    FilingService("client_services", "Client Services", None, True),
    FilingService("form_1099_processing", "1099 Processing", None, True),
    FilingService("tax_resolution", "Tax Resolution", None, True),
)

BY_CODE: dict[str, FilingService] = {s.code: s for s in FILING_SERVICES}
BY_LABEL: dict[str, FilingService] = {s.label: s for s in FILING_SERVICES}

CANONICAL_SERVICE_CODES: tuple[str, ...] = tuple(s.code for s in FILING_SERVICES)
CANONICAL_SERVICE_LABELS: tuple[str, ...] = tuple(s.label for s in FILING_SERVICES)

#: Provenance buckets. TaxDome's level 2 says who produced a document, not which service it belongs
#: to. These are facets and must never reach :func:`service_code_for_label`.
PROVENANCE_LABELS: frozenset[str] = frozenset({"Client Uploads", "Firm Deliverables"})

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_label(value) -> str:
    """Fold a filing label to its identity form: lowercase, punctuation and spacing collapsed.

    ``"Sales & Litter Tax"``, ``"sales  &  litter  tax"`` and ``"Sales, Litter Tax"`` all fold to
    ``"sales litter tax"``. Used to look a label up, never to build a folder segment.
    """
    return _NORMALIZE_RE.sub(" ", str(value or "").lower()).strip()


#: Every spelling seen in production that resolves to a canonical service. Built from the
#: SharePoint taxonomy map plus the canonical labels themselves.
_LABEL_ALIASES: dict[str, str] = {
    "tax preparation": "tax_preparation",
    "tax preparation 1": "tax_preparation",
    "sales litter pp tax": "sales_litter_tax",
    "sales litter tax": "sales_litter_tax",
    "payroll": "payroll",
    "bookkeeping": "bookkeeping",
    "bookkeeping 1": "bookkeeping",
    "client services": "client_services",
    "1099 processing": "form_1099_processing",
    "tax resolution": "tax_resolution",
    "accounts": "wealth_accounts",
    "wealth accounts": "wealth_accounts",
}


def service_code_for_label(label) -> str | None:
    """Canonical code for a filing label, or ``None`` when the label is not a service.

    Returns ``None`` — never a guess — for provenance buckets and for anything unrecognised. A
    caller that gets ``None`` must route the document to REVIEW or UNRESOLVED; it must not invent a
    service.
    """
    if label is None:
        return None
    text = str(label).strip()
    if text in PROVENANCE_LABELS:
        return None
    return _LABEL_ALIASES.get(normalize_label(text))


def service_label_for_code(code) -> str | None:
    service = BY_CODE.get(str(code or ""))
    return service.label if service else None


def is_provenance_label(label) -> bool:
    return str(label or "").strip() in PROVENANCE_LABELS


#: Rows a migration would add to make ``service_lines`` cover the filing vocabulary. DESCRIPTION
#: ONLY — nothing in this module writes it, and the accompanying migration is not applied.
SERVICE_LINE_SEED_ROWS: tuple[dict[str, str], ...] = tuple(
    {"code": s.code, "name": s.label} for s in FILING_SERVICES if s.needs_service_line_row
)


def reconciliation_report() -> dict:
    """What lines up with ``service_lines`` today and what does not. For docs and tests."""
    return {
        "canonical_services": [
            {"code": s.code, "label": s.label, "service_line_code": s.service_line_code,
             "needs_service_line_row": s.needs_service_line_row}
            for s in FILING_SERVICES
        ],
        "mapped_to_existing_service_line": sorted(
            s.code for s in FILING_SERVICES if not s.needs_service_line_row),
        "requires_new_service_line_row": sorted(
            s.code for s in FILING_SERVICES if s.needs_service_line_row),
    }
