"""Canonical document display names — PURE, deterministic, read-only.

Composes ``YEAR - DOCUMENT TYPE - OWNER`` (plus a preserved filename qualifier when the original name
carries detail the first three parts do not) from fields Client360 already has: ``original_name``,
``category``, and the person/household/organization owner. No database access, no OCR, no source-file
access, no I/O of any kind — every function here is a pure transformation of its arguments, so the
result is reproducible and unit-testable without a database.

Nothing in this module renames anything. It computes a *candidate* display name; deciding whether that
candidate is an improvement is the preview's job (see ``document_normalization_preview``).

Design constraint from the real production census: only 2% of trusted filenames are generic, so the
existing names are mostly INFORMATIVE. This module is therefore deliberately conservative — it
preserves a residual qualifier rather than discarding detail, and it reports when the original name is
richer than the candidate so the caller can leave it alone.
"""
from __future__ import annotations

import re

from app.services.document_classification import classify_document

#: Type code -> the human label used inside a display name.
DISPLAY_LABELS = {
    "1040": "Form 1040", "1041": "Form 1041", "1065": "Form 1065",
    "1120": "Form 1120", "1120S": "Form 1120S", "8879": "Form 8879",
    "W-2": "W-2", "1099": "1099", "1095-A": "Form 1095-A", "1095-C": "Form 1095-C",
    "K-1": "K-1", "941": "Form 941",
    "brokerage_statement": "Brokerage Statement", "bank_statement": "Bank Statement",
    "irs_notice": "IRS Notice", "state_notice": "State Notice",
    "drivers_license": "Driver's License", "passport": "Passport",
    "organizer": "Tax Organizer", "engagement_letter": "Engagement Letter",
    "insurance_policy": "Insurance Policy", "benefits_enrollment": "Benefits Enrollment",
    "trust_document": "Trust Document", "estate_document": "Estate Document",
    "financial_statement": "Financial Statement",
    "payroll_summary": "Payroll Summary", "tax_return": "Tax Return",
    "tax_documents": "Tax Documents",
}

# Filename-only patterns, ordered most-specific first. These run BEFORE the shared classifier for the
# forms the production census showed are common and that the shared rules either miss (941, 1095-C,
# 8879-S, payroll, "tax docs") or would classify too coarsely. Each is anchored on a token boundary so
# "1120S" never matches inside "11205" and "941" never matches inside a phone number or a zip+4.
_TOKEN = r"(?<![0-9A-Za-z])"
_END = r"(?![0-9A-Za-z])"
_FILENAME_RULES: list[tuple[str, list[str], float]] = [
    ("8879", [rf"{_TOKEN}8879[-\s_]?s{_END}", rf"{_TOKEN}8879{_END}"], 0.93),
    ("1120S", [rf"{_TOKEN}1120[-\s_]?s{_END}"], 0.93),
    ("1120", [rf"{_TOKEN}1120{_END}"], 0.92),
    ("1065", [rf"{_TOKEN}1065{_END}"], 0.92),
    ("1041", [rf"{_TOKEN}1041{_END}"], 0.92),
    ("1040", [rf"{_TOKEN}1040{_END}"], 0.92),
    ("941", [rf"{_TOKEN}941{_END}", r"quarterly\s+federal\s+tax\s+return"], 0.9),
    ("1095-C", [rf"{_TOKEN}1095[-\s_]?c{_END}"], 0.92),
    ("1095-A", [rf"{_TOKEN}1095[-\s_]?a{_END}"], 0.92),
    ("K-1", [rf"{_TOKEN}k[-\s_]?1{_END}", r"schedule\s*k[-\s_]?1"], 0.9),
    ("W-2", [rf"{_TOKEN}w[-\s_]?2{_END}", r"wage\s+and\s+tax\s+statement"], 0.9),
    ("1099", [rf"{_TOKEN}1099(?:[-\s_]?(?:int|div|b|misc|nec|r|g|k|s))?{_END}"], 0.9),
    ("organizer", [r"organizer"], 0.9),
    ("payroll_summary", [r"payroll\s*(?:summary|report|register|journal)?", r"\bpay\s*roll\b"], 0.85),
    ("tax_return", [r"tax\s*return", r"\breturn\s*copy\b", r"\bfiled\s*return\b"], 0.8),
    ("tax_documents", [r"tax\s*docs?\b", r"tax\s*documents?\b", r"tax\s*source\s*docs?"], 0.75),
]

#: ``documents.category`` values specific enough to BE the document type. Vague buckets such as
#: "tax", "general", "client", "misc" are intentionally absent — they say a domain, not a document.
_CATEGORY_TO_TYPE = {
    "w2": "W-2", "w-2": "W-2", "1099": "1099", "1040": "1040", "1120s": "1120S",
    "1120": "1120", "1065": "1065", "1041": "1041", "8879": "8879", "k1": "K-1", "k-1": "K-1",
    "941": "941", "1095c": "1095-C", "1095-c": "1095-C", "1095a": "1095-A", "1095-a": "1095-A",
    "organizer": "organizer", "tax_organizer": "organizer",
    "payroll": "payroll_summary", "payroll_summary": "payroll_summary",
    "tax_return": "tax_return", "tax_documents": "tax_documents",
    "engagement_letter": "engagement_letter", "insurance_policy": "insurance_policy",
    "insurance": "insurance_policy", "brokerage_statement": "brokerage_statement",
    "bank_statement": "bank_statement", "trust_document": "trust_document",
    "estate_document": "estate_document", "financial_statement": "financial_statement",
    "benefits_enrollment": "benefits_enrollment", "benefits_census": "benefits_enrollment",
}
#: Categories that exist but are too broad to name a document by.
VAGUE_CATEGORIES = frozenset({"tax", "general", "client", "misc", "other", "document", "documents",
                              "uncategorized", "portal_request", "upload"})

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_YEAR_AFFIX_RE = re.compile(r"[A-Za-z]{1,3}(?:19|20)\d{2}|(?:19|20)\d{2}[A-Za-z]{1,6}")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_GENERIC_RE = re.compile(
    r"^(doc|document|scan|scanned|image|img|file|untitled|new|copy|attachment|d|x|temp|tmp)"
    r"[ _\-]*\d*$", re.I)
_CAMERA_RE = re.compile(r"^(img|dsc|photo|scan|screenshot)[ _\-]?\d{2,}$", re.I)

MAX_NAME_LEN = 150
CURRENT_YEAR_CEILING = 2100


def strip_extension(filename: str | None) -> str:
    return _EXT_RE.sub("", (filename or "").strip())


def extension_of(filename: str | None) -> str:
    m = _EXT_RE.search((filename or "").strip())
    return m.group(0) if m else ""


def sanitize(part: str | None) -> str:
    """One display-name component, safe for NTFS/SharePoint. Never returns a bare separator."""
    s = _ILLEGAL.sub(" ", part or "")
    s = re.sub(r"\s+", " ", s).strip().strip("-").strip()
    return s.rstrip(".").strip()


def is_generic_filename(filename: str | None) -> bool:
    """True when the filename carries no information (scan/camera/export artifact or too short)."""
    stem = strip_extension(filename)
    return bool(_GENERIC_RE.match(stem) or _CAMERA_RE.match(stem) or len(stem.strip()) <= 3)


def extract_year(filename: str | None) -> int | None:
    """Document year from the ORIGINAL FILENAME ONLY (effective_date is empty in production).

    Takes the LAST plausible 4-digit year in the name: exports commonly lead with a client or job
    number and trail with the tax year (``12345 Pullen 1040 2024.pdf``). Rejects anything outside
    1900..2100 and any run of digits longer than four, so an account number cannot masquerade as a year.
    """
    found = []
    for token in re.split(r"[^0-9A-Za-z]+", strip_extension(filename)):
        if not token:
            continue
        # A year is only credible as its own token ("2024"), or with a short alphabetic affix
        # ("FY2024", "2024Taxes"). Inside a longer alphanumeric run it is almost always part of a
        # hash or export id -- "c4aa9e2000" is not the year 2000.
        if not (_YEAR_TOKEN_RE.fullmatch(token) or _YEAR_AFFIX_RE.fullmatch(token)):
            continue
        for y in _YEAR_RE.findall(token):
            if 1900 <= int(y) <= CURRENT_YEAR_CEILING:
                found.append(int(y))
    return found[-1] if found else None


def resolve_document_type(category: str | None, filename: str | None) -> tuple[str, float, str]:
    """``(type_code, confidence, source)`` from existing category, then filename patterns, then the
    shared classifier. ``("unknown", 0.0, "none")`` when nothing matches."""
    cat = (category or "").strip().lower().replace(" ", "_")
    if cat and cat not in VAGUE_CATEGORIES:
        mapped = _CATEGORY_TO_TYPE.get(cat) or _CATEGORY_TO_TYPE.get(cat.replace("_", ""))
        if mapped:
            return mapped, 0.95, "category"

    stem = strip_extension(filename).lower()
    for type_code, patterns, conf in _FILENAME_RULES:
        for pat in patterns:
            if re.search(pat, stem):
                return type_code, conf, "filename_pattern"

    # Shared deterministic classifier, filename only — never OCR, never the source file.
    doc_type, conf = classify_document(filename, None)
    if doc_type != "unknown":
        return doc_type, conf, "classifier"
    return "unknown", 0.0, "none"


def type_label(type_code: str | None) -> str | None:
    if not type_code or type_code == "unknown":
        return None
    return DISPLAY_LABELS.get(type_code, type_code.replace("_", " ").title())


def residual_qualifier(filename: str | None, *, year: int | None, type_code: str | None,
                       entity: str | None) -> str | None:
    """Detail in the original filename that YEAR - TYPE - OWNER does not already carry.

    Only 2% of production filenames are generic, so the originals usually hold real information —
    an employer, a custodian, a quarter, a spouse's copy. Everything already represented by the
    year, the type label, or the owner name is removed; whatever survives and still looks meaningful
    is returned so the caller can append it instead of discarding it.
    """
    stem = sanitize(strip_extension(filename))
    if not stem:
        return None
    residue = stem
    if year:
        residue = re.sub(rf"(?<!\d){year}(?!\d)", " ", residue)
    # Strip every way the type may be spelled in a filename: the label ("W-2"), the code, the code
    # with separators removed ("w2", "1095c", "k1"), and the underscore form ("payroll summary").
    # Without the separator-free variants a name like "w2 2024 copy.pdf" keeps "w2" as a bogus
    # qualifier and two otherwise identical documents stop colliding.
    variants = set()
    for token in filter(None, [type_label(type_code), type_code,
                               (type_code or "").replace("_", " ")]):
        variants.add(token)
        variants.add(re.sub(r"[^0-9A-Za-z]", "", token))
    for token in sorted(filter(None, variants), key=len, reverse=True):
        residue = re.sub(rf"{_TOKEN}{re.escape(token)}{_END}", " ", residue, flags=re.I)
    for word in re.split(r"[\s_\-]+", entity or ""):
        if len(word) > 2:
            residue = re.sub(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", " ", residue, flags=re.I)
    residue = re.sub(r"[\s_\-]+", " ", residue).strip(" -_,.")
    # Drop residue that is only punctuation, a bare number (job/client export ids), or a stray letter.
    if len(residue) < 3 or re.fullmatch(r"[\d\W_]+", residue) or _GENERIC_RE.match(residue):
        return None
    return sanitize(residue)


def canonical_display_name(*, year, type_code, entity, qualifier=None) -> str | None:
    """``YEAR - TYPE - OWNER[ - QUALIFIER]``. Segments with no value are omitted, never padded with
    placeholders. Returns ``None`` when neither a type nor a qualifier is known — a bare
    ``2024 - Norman Pullen`` says nothing useful about the document. Never contains a document id."""
    label = type_label(type_code)
    entity = sanitize(entity)
    qualifier = sanitize(qualifier) if qualifier else None
    if not label and not qualifier:
        return None
    parts = [str(year) if year else None, label, entity or None, qualifier]
    name = " - ".join(p for p in parts if p)
    if len(name) > MAX_NAME_LEN:        # trim the tail (qualifier) first; the head must survive
        head = " - ".join(p for p in [str(year) if year else None, label, entity or None] if p)
        name = head[:MAX_NAME_LEN].rstrip(" -") if len(head) > MAX_NAME_LEN else head
    return name or None
