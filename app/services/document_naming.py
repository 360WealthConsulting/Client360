"""Canonical document display names — PURE, deterministic, read-only.

Composes ``YEAR - DOCUMENT TYPE - OWNER`` (plus a preserved filename qualifier when the original name
carries detail the first three parts do not) from fields Client360 already has: ``original_name``,
``category``, and the person/household/organization owner. No database access, no OCR, no source-file
access, no I/O of any kind — every function here is a pure transformation of its arguments.

Nothing here renames anything. It computes a *candidate* display name; deciding whether that candidate
is an improvement is the preview's job (see ``document_normalization_preview``).

Design constraint from the production census: only 2% of trusted filenames are generic, so existing
names are mostly INFORMATIVE. This module preserves a residual qualifier rather than discarding
detail, and it reports the signals (version markers, multiple forms, ambiguous years) that must stop a
candidate from being treated as safe.

Type recognition returns the TEXT IT MATCHED so the qualifier pass can remove the form reference as a
complete semantic unit. Guessing spelling variants instead is what left debris like ``8879S``,
``1099INT`` and a stray ``R`` in earlier candidates.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from app.services.document_classification import classify_document
from app.services.document_name_safety import is_safe, scrub

#: Type code -> the human label used inside a display name.
DISPLAY_LABELS = {
    "1040": "Form 1040", "1041": "Form 1041", "1065": "Form 1065",
    "1120": "Form 1120", "1120S": "Form 1120S", "8879": "Form 8879",
    # Amended returns are DIFFERENT documents from the originals and must never display as them.
    "1040-X": "Form 1040-X", "1065-X": "Form 1065-X", "1120-X": "Form 1120-X",
    "W-2": "W-2", "941": "Form 941", "K-1": "K-1",
    "1095-A": "Form 1095-A", "1095-C": "Form 1095-C", "1098-T": "Form 1098-T",
    # 1099 subtypes are distinct documents, not flavours of one — a 1099-R and a 1099-K for the same
    # person in the same year are different filings and must not collapse to one name.
    "1099": "1099", "1099-R": "1099-R", "1099-K": "1099-K", "1099-INT": "1099-INT",
    "1099-DIV": "1099-DIV", "1099-NEC": "1099-NEC", "1099-SA": "1099-SA",
    "brokerage_statement": "Brokerage Statement", "bank_statement": "Bank Statement",
    "irs_notice": "IRS Notice", "state_notice": "State Notice",
    "drivers_license": "Driver's License", "passport": "Passport",
    "organizer": "Tax Organizer", "engagement_letter": "Engagement Letter",
    "insurance_policy": "Insurance Policy", "benefits_enrollment": "Benefits Enrollment",
    "trust_document": "Trust Document", "estate_document": "Estate Document",
    "financial_statement": "Financial Statement",
    "payroll_summary": "Payroll Summary", "tax_return": "Tax Return",
    "tax_documents": "Tax Documents",
    "schedule_c": "Schedule C", "signature_documents": "Signature Documents",
    "year_end_tax_package": "Year End Tax Package",
    "mortgage_interest": "Mortgage Interest Statement",
}

_TOKEN = r"(?<![0-9A-Za-z])"
_END = r"(?![0-9A-Za-z])"

# Ordered most-specific first. Every pattern is token-bounded so "1120S" never matches inside "11205".
# 1099 subtypes precede generic 1099; 8879-S precedes 8879.
_FILENAME_RULES: list[tuple[str, list[str], float]] = [
    # -X amended variants FIRST: "1040X" must never fall through to the plain 1040 rule. Limited to
    # the base forms this classifier already recognises that have a real -X amended form (1040-X,
    # 1065-X, 1120-X). 1041 and 1120-S are deliberately absent -- they are amended by checking a box
    # on the original form, so there is no -X suffix to match and inventing one would be a guess.
    ("1040-X", [rf"{_TOKEN}1040[-\s_]?x{_END}"], 0.93),
    ("1065-X", [rf"{_TOKEN}1065[-\s_]?x{_END}"], 0.93),
    ("1120-X", [rf"{_TOKEN}1120[-\s_]?x{_END}"], 0.93),
    ("8879", [rf"{_TOKEN}8879[-\s_]?s{_END}", rf"{_TOKEN}8879{_END}"], 0.93),
    ("1120S", [rf"{_TOKEN}1120[-\s_]?s{_END}"], 0.93),
    ("1120", [rf"{_TOKEN}1120{_END}"], 0.92),
    ("1065", [rf"{_TOKEN}1065{_END}"], 0.92),
    ("1041", [rf"{_TOKEN}1041{_END}"], 0.92),
    ("1040", [rf"{_TOKEN}1040{_END}"], 0.92),
    ("941", [rf"{_TOKEN}941{_END}", r"quarterly\s+federal\s+tax\s+return"], 0.9),
    ("1095-C", [rf"{_TOKEN}1095[-\s_]?c{_END}"], 0.92),
    ("1095-A", [rf"{_TOKEN}1095[-\s_]?a{_END}"], 0.92),
    ("1098-T", [rf"{_TOKEN}1098[-\s_]?t{_END}"], 0.92),
    ("1099-INT", [rf"{_TOKEN}1099[-\s_]?int{_END}"], 0.93),
    ("1099-DIV", [rf"{_TOKEN}1099[-\s_]?div{_END}"], 0.93),
    ("1099-NEC", [rf"{_TOKEN}1099[-\s_]?nec{_END}"], 0.93),
    ("1099-SA", [rf"{_TOKEN}1099[-\s_]?sa{_END}"], 0.93),
    ("1099-R", [rf"{_TOKEN}1099[-\s_]?r{_END}"], 0.93),
    ("1099-K", [rf"{_TOKEN}1099[-\s_]?k{_END}"], 0.93),
    ("1099", [rf"{_TOKEN}1099(?:[-\s_]?(?:b|misc|g|s))?{_END}"], 0.9),
    ("K-1", [rf"{_TOKEN}k[-\s_]?1{_END}", r"schedule\s*k[-\s_]?1"], 0.9),
    ("W-2", [rf"{_TOKEN}w[-\s_]?2{_END}", r"wage\s+and\s+tax\s+statement"], 0.9),
    ("schedule_c", [r"schedule\s*c(?![a-z])", rf"{_TOKEN}sch\s*c(?![a-z])",
                    r"income\s*(?:and\s*|&\s*)?expense\s*worksheet"], 0.9),
    # "DL" alone is too weak (initials, abbreviations); require the front/back qualifier or the phrase.
    # "DL" is matched by LOOKAHEAD so front/back is required but NOT consumed -- consuming it made
    # "DL Front" and "DL Back" normalise to the same candidate and collide.
    ("drivers_license", [r"driver'?s?\s*licen[sc]e",
                         rf"{_TOKEN}dl(?=[\s\-_]*(?:front|back){_END})"], 0.9),
    ("year_end_tax_package", [r"year[\s\-_]*end\s*tax\s*package"], 0.9),
    ("signature_documents", [rf"signature\s*doc(?:ument)?s?{_END}"], 0.88),
    ("mortgage_interest", [r"mortgage\s*interest(?:\s*statement)?"], 0.88),
    ("organizer", [r"organizer"], 0.9),
    ("payroll_summary", [r"payroll\s*(?:summary|report|register|journal)?", r"\bpay\s*roll\b"], 0.85),
    ("tax_return", [r"tax\s*return", r"\breturn\s*copy\b", r"\bfiled\s*return\b"], 0.8),
    ("tax_documents", [r"tax\s*docs?\b", r"tax\s*documents?\b", r"tax\s*source\s*docs?"], 0.75),
]

#: ``documents.category`` values specific enough to BE the document type.
_CATEGORY_TO_TYPE = {
    "w2": "W-2", "w-2": "W-2", "1040": "1040", "1120s": "1120S",
    "1120": "1120", "1065": "1065", "1041": "1041", "8879": "8879", "k1": "K-1", "k-1": "K-1",
    "941": "941", "1095c": "1095-C", "1095-c": "1095-C", "1095a": "1095-A", "1095-a": "1095-A",
    "1098t": "1098-T", "1098-t": "1098-T",
    "1040x": "1040-X", "1040-x": "1040-X", "1065x": "1065-X", "1065-x": "1065-X",
    "1120x": "1120-X", "1120-x": "1120-X",
    "1099": "1099", "1099r": "1099-R", "1099-r": "1099-R", "1099k": "1099-K", "1099-k": "1099-K",
    "1099int": "1099-INT", "1099-int": "1099-INT", "1099div": "1099-DIV", "1099-div": "1099-DIV",
    "1099nec": "1099-NEC", "1099-nec": "1099-NEC", "1099sa": "1099-SA", "1099-sa": "1099-SA",
    "organizer": "organizer", "tax_organizer": "organizer",
    "payroll": "payroll_summary", "payroll_summary": "payroll_summary",
    "tax_return": "tax_return", "tax_documents": "tax_documents",
    "engagement_letter": "engagement_letter", "insurance_policy": "insurance_policy",
    "insurance": "insurance_policy", "brokerage_statement": "brokerage_statement",
    "bank_statement": "bank_statement", "trust_document": "trust_document",
    "estate_document": "estate_document", "financial_statement": "financial_statement",
    "benefits_enrollment": "benefits_enrollment", "benefits_census": "benefits_enrollment",
    "schedule_c": "schedule_c", "drivers_license": "drivers_license",
    "mortgage_interest": "mortgage_interest",
}
#: Categories that exist but are too broad to name a document by.
VAGUE_CATEGORIES = frozenset({"tax", "general", "client", "misc", "other", "document", "documents",
                              "uncategorized", "portal_request", "upload"})

# Version / amendment / workflow-selection semantics. Their presence changes WHICH document this is,
# so a candidate that cannot represent them must not be called safe.
_VERSION_MARKERS = (
    r"amend(?:ed|ment)?", r"revis(?:ed|ion)", r"correct(?:ed|ion)", r"supersed(?:e|ed)",
    r"reissued?", r"voided?", r"draft", r"duplicate", r"dupe",
    r"use\s*th[ie]s\s*one", r"usethisone", r"do\s*not\s*use", r"donotuse",
    r"v\d{1,2}", r"ver\d{0,2}", r"version\s*\d{0,2}", r"rev\d{1,2}",
)
_VERSION_RE = re.compile(rf"{_TOKEN}(?:{'|'.join(_VERSION_MARKERS)}){_END}", re.I)

#: Filename filler that carries no document meaning. Dropped from the qualifier, never REVIEW-forcing.
_NOISE_TOKENS = frozenset({
    "tax", "taxes", "doc", "docs", "document", "documents", "scan", "scanned", "scans",
    "copy", "file", "files", "final", "for", "the", "and", "of", "a", "an", "misc", "new",
    "image", "img", "pdf", "jpg", "jpeg", "png", "upload", "uploaded", "client", "attachment",
})

#: Families used for the "more than one materially different form" check. Coarse descriptors such as
#: tax_return / tax_documents are deliberately excluded — they describe a bundle, not a second form.
_FORM_FAMILY = {
    "1040": "1040", "1041": "1041", "1065": "1065", "1120": "1120", "1120S": "1120",
    "1040-X": "1040", "1065-X": "1065", "1120-X": "1120",
    "8879": "8879", "941": "941", "1095-A": "1095", "1095-C": "1095", "K-1": "K-1", "W-2": "W-2",
    "1099": "1099", "1099-R": "1099", "1099-K": "1099", "1099-INT": "1099",
    "1099-DIV": "1099", "1099-NEC": "1099", "1099-SA": "1099",
    "schedule_c": "schedule_c", "organizer": "organizer",
}

#: type code -> every pattern for that type. The qualifier pass strips ALL of them, not only the one
#: that matched, so "Schedule C Income Expense Worksheet" does not keep the worksheet phrase.
_PATTERNS_BY_TYPE: dict[str, list[str]] = {}
for _code, _pats, _c in _FILENAME_RULES:
    _PATTERNS_BY_TYPE.setdefault(_code, []).extend(_pats)

# Scanner/camera exports: a prefix followed by nothing but a date-time stamp. The timestamp is when
# the page was scanned, NOT the document year, and it is not a meaningful qualifier.
_SCANNER_PREFIX = re.compile(
    # NOT \b: an underscore is a word character, so "scan_Mar-05..." would not match a \b boundary.
    r"^(?:adobe\s*scan|camscanner|scan(?:ned)?|img|image|photo|doc(?:ument)?|capture)(?![A-Za-z0-9])",
    re.I)
_MONTH_WORD = re.compile(
    r"(?i)(?<![a-z])(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?![a-z])")
_DATETIME_ONLY = re.compile(r"^[\d\s_\-.:()]*$")

#: A filesystem duplicate marker: "(2)", "copy 2", "- Copy". Preserved only when dropping it would
#: make two documents collide; never used as a routine qualifier.
_DUP_SUFFIX = re.compile(r"(?:\(\s*(\d{1,3})\s*\)|(?:-\s*)?copy(?:\s*\(?\s*(\d{1,3})\s*\)?)?)\s*$",
                         re.I)
_INSTRUCTIONS_RE = re.compile(r"(?i)(?<![a-z])instruction(?:s)?(?![a-z])")

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_YEAR_AFFIX_RE = re.compile(r"[A-Za-z]{1,3}(?:19|20)\d{2}|(?:19|20)\d{2}[A-Za-z]{1,6}")
# Full dates in a filename: 2024-04-02, 04-02-2024, 20240402. Stripped whole so "04 02" never survives.
_DATE_RE = re.compile(
    r"(?<!\d)(?:(?:19|20)\d{2}[-_./](?:0?[1-9]|1[0-2])[-_./](?:0?[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|1[0-2])[-_./](?:0?[1-9]|[12]\d|3[01])[-_./](?:19|20)\d{2}"
    r"|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))(?!\d)")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_GENERIC_RE = re.compile(
    r"^(doc|document|scan|scanned|image|img|file|untitled|new|copy|attachment|d|x|temp|tmp)"
    r"[ _\-]*\d*$", re.I)
_CAMERA_RE = re.compile(r"^(img|dsc|photo|scan|screenshot)[ _\-]?\d{2,}$", re.I)

MAX_NAME_LEN = 150
CURRENT_YEAR_CEILING = 2100


class TypeMatch(NamedTuple):
    """Resolved document type plus the exact filename text that produced it (``None`` when the type
    came from ``category`` or the shared classifier, which report no span)."""

    code: str
    confidence: float
    source: str
    matched_text: str | None = None


def _has_content(text: str) -> bool:
    """True when the text contains at least one alphanumeric character.

    A display name of ``"..."`` or ``"///"`` is technically free of sensitive identifiers but names
    nothing; such a value is skipped so the next candidate (normally ``original_name``) is used.
    """
    return any(c.isalnum() for c in text or "")


def _is_meaningful(text: str) -> bool:
    """True when what survived a scrub still NAMES something.

    Requires at least three alphanumerics AND at least one letter: a residue of bare digits (a year,
    a fragment of the identifier's neighbours) is not a name, and falling through to a constructed
    ``year - type - owner`` label is strictly more informative than emitting it.
    """
    alnum = [c for c in (text or "") if c.isalnum()]
    return len(alnum) >= 3 and any(c.isalpha() for c in alnum)


def _row_get(row):
    """Uniform accessor for a mapping row or an ORM object."""
    return row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))


def _constructed_label(row, owner=None) -> str | None:
    """A label built ONLY from fields that cannot carry free text: year, document type, owner.

    Used when neither the stored display name nor the original filename can be shown safely. These
    fields are structured — a tax year, a mapped category, a resolved owner name — so the result is
    safe by construction rather than by inspection.
    """
    get = _row_get(row)
    year = None
    tags = get("tags")
    if isinstance(tags, dict):
        raw = tags.get("tax_year") or tags.get("year")
        if raw is not None and str(raw).strip().isdigit():
            year = str(raw).strip()
    if not year:
        effective = get("effective_date")
        year = str(effective.year) if hasattr(effective, "year") else None
    category = (get("category") or "").strip().lower().replace(" ", "_")
    label = type_label(_CATEGORY_TO_TYPE.get(category)) if category not in VAGUE_CATEGORIES else None
    parts = [p for p in (year, label, sanitize(owner) if owner else None) if p]
    return " - ".join(parts) if parts else None


def safe_document_label(row, *, owner=None) -> str:
    """The user-facing label for a document, guaranteed to carry no sensitive identifier.

    Precedence, first safe result wins:

    1. ``display_name`` when it is already safe,
    2. ``display_name`` with sensitive spans scrubbed out, when something meaningful survives,
    3. ``original_name`` under the same two rules,
    4. a label constructed from structured fields (``year - type - owner``),
    5. ``Document <id>``.

    An UNSAFE original filename is never emitted — that is the point of step 4. A document with
    neither a display name nor an original name returns "" (nothing recorded, nothing to protect).

    This reads only; ``original_name``, ``stored_name``, ``storage_path``, ``storage_uri``,
    ``sha256`` and ``tags`` are never modified, and the physical file is untouched.
    """
    if row is None:
        return ""
    get = _row_get(row)
    display = (get("display_name") or "").strip()
    original = (get("original_name") or "").strip()
    if not display and not original:
        return ""
    for candidate in (display, original):
        if not candidate or not _has_content(candidate):
            continue
        if is_safe(candidate):
            return candidate
        # Scrub the STEM only. Scrubbing the whole filename can reduce "123-45-6789.pdf" to the bare
        # extension "pdf", which is not a name -- it is what is left after the name was removed.
        cleaned = scrub(strip_extension(candidate))
        if cleaned and is_safe(cleaned) and _is_meaningful(cleaned):
            return cleaned
    built = _constructed_label(row, owner)
    if built:
        return built
    doc_id = get("id")
    return f"Document {doc_id}" if doc_id is not None else "Document"


def document_display_name(row) -> str:
    """What staff should SEE for a document: the canonical ``display_name`` when it is set and safe,
    otherwise the original filename, otherwise a safely constructed label.

    Falling back to ``original_name`` is the normal case, not an error — most documents never receive
    a display_name. This never hides provenance: detail views read ``original_name`` directly, and the
    physical file is always located by ``storage_path`` / ``storage_uri``, never by either name.

    Since 0.13.0 the result is additionally gated by ``document_name_safety``, so a display name that
    was written before that gate existed (or by any future writer) cannot surface an SSN, EIN,
    account, routing, card, policy/member identifier or labelled date of birth to a user.
    """
    return safe_document_label(row)


_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")


def document_delivery_filename(row, *, owner=None) -> str:
    """The filename a document is DELIVERED under (download, and mail attachments).

    The safe label from :func:`safe_document_label` with the original file's extension preserved. The
    extension is taken from ``original_name`` and appended only when the delivered base does not
    already end with it, so it is never duplicated.

    This changes the label on the response, nothing else: the bytes served, ``storage_path`` /
    ``storage_uri`` used to locate them, ``sha256``, ``stored_name`` and ``original_name`` are all
    untouched, and the caller's authorization is unaffected.

    Hardened for header and path safety: control characters (including CR/LF), path separators and
    quoting characters are stripped, only the basename survives, and traversal segments cannot leak —
    so the delivered name can neither inject a response header nor describe a filesystem location.
    Hardened for disclosure: the emitted base is re-checked and replaced with ``Document <id>`` if
    anything sensitive would otherwise survive. A delivered name NEVER falls back to an unsafe
    original filename.
    """
    if row is None:
        return ""
    get = _row_get(row)
    original = (get("original_name") or "").strip()
    doc_id = get("id")
    fallback = f"Document {doc_id}" if doc_id is not None else "document"

    base = safe_document_label(row, owner=owner)
    if not base:
        return ""

    # Basename only — a delivered name must never describe a path.
    base = re.split(r"[\\/]", base)[-1]
    base = _UNSAFE_FILENAME.sub(" ", base)
    base = re.sub(r"\s+", " ", base).strip().strip(".").strip()
    # Final gate. Stripping characters cannot introduce an identifier, but the emitted name is
    # re-checked so that "safe" is a property of what actually leaves the process, not an inference.
    if not base or set(base) <= {"."} or not is_safe(base):
        base = fallback

    ext = extension_of(original)
    if ext and not base.lower().endswith(ext.lower()):
        base = f"{base}{ext}"
    return base


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
    return bool(_GENERIC_RE.match(stem) or _CAMERA_RE.match(stem) or len(stem.strip()) <= 3
                or is_scanner_filename(filename))


def is_scanner_filename(filename: str | None) -> bool:
    """True for scanner/camera exports whose whole name is a prefix plus a date-time stamp, e.g.
    ``scan_Mar-05-2022_22-36-35.pdf``. The stamp records when the page was scanned, so it is neither
    document-year evidence nor a meaningful qualifier."""
    stem = strip_extension(filename).strip()
    if not _SCANNER_PREFIX.match(stem):
        return False
    rest = _SCANNER_PREFIX.sub("", stem, count=1)
    rest = _MONTH_WORD.sub(" ", rest)
    return bool(rest.strip(" _-.:")) and _DATETIME_ONLY.fullmatch(rest) is not None


def duplicate_suffix(filename: str | None) -> str | None:
    """The filesystem duplicate marker at the end of a filename, normalised: ``(2)``, ``Copy``."""
    m = _DUP_SUFFIX.search(strip_extension(filename).strip())
    if not m:
        return None
    number = m.group(1) or m.group(2)
    return f"({number})" if number else "Copy"


def is_ordinal_duplicate_suffix(suffix: str | None) -> bool:
    """True for a NUMBERED duplicate marker such as ``(2)`` or ``(3)``.

    An ordinal says WHICH copy this is, so it is self-disambiguating filename evidence and is kept
    unconditionally. A bare ``Copy`` carries no ordinal, distinguishes nothing on its own, and is
    therefore preserved only when it actually resolves a collision.
    """
    return bool(suffix) and re.fullmatch(r"\(\d{1,3}\)", suffix) is not None


def mentions_instructions(filename: str | None) -> bool:
    """The filename says "instructions" — an instructions packet is NOT the form it describes."""
    return bool(_INSTRUCTIONS_RE.search(strip_extension(filename)))


def detect_foreign_person_token(filename: str | None, *, owner_name: str | None,
                                known_first_names) -> str | None:
    """The leading filename token when it is a first name Client360 KNOWS belongs to a person and is
    not part of the owner's name — e.g. ``Ron 2021 W2.pdf`` filed under someone else.

    Deliberately conservative: only the LEADING token is considered, and it must appear in the set of
    first names already present in ``people``. That is what keeps custodians and employers such as
    "Fidelity" or "Vanguard" from being mistaken for people — no name dictionary, no guessing. The
    caller supplies the set; this function stays pure.
    """
    stem = strip_extension(filename).strip()
    tokens = [x for x in re.split(r"[\s_\-]+", stem) if x]
    if not tokens:
        return None
    lead = tokens[0].strip(".,()")
    if not lead.isalpha() or not (2 <= len(lead) <= 15):
        return None
    # "Johnson & Wales" is an institution, not a person called Johnson. A leading token joined by
    # "&"/"and" is part of a compound organisation name.
    if len(tokens) > 1 and tokens[1].strip(".,").lower() in ("&", "and", "+"):
        return None
    low = lead.lower()
    if low in _NOISE_TOKENS or low not in known_first_names:
        return None
    owner_tokens = {w.lower() for w in re.split(r"[\s_\-]+", owner_name or "") if w}
    if low in owner_tokens or any(low in w or w in low for w in owner_tokens if len(w) > 2):
        return None
    return lead


def extract_years(filename: str | None) -> list[int]:
    """Every plausible year in the filename, in order. A 4-digit run inside a longer alphanumeric
    token is a hash fragment, not a year — ``c4aa9e2000`` is not the year 2000. A scanner timestamp
    is not a year either."""
    if is_scanner_filename(filename):
        return []
    found = []
    for token in re.split(r"[^0-9A-Za-z]+", strip_extension(filename)):
        if not token:
            continue
        if not (_YEAR_TOKEN_RE.fullmatch(token) or _YEAR_AFFIX_RE.fullmatch(token)):
            continue
        for y in _YEAR_RE.findall(token):
            if 1900 <= int(y) <= CURRENT_YEAR_CEILING:
                found.append(int(y))
    return found


def extract_year(filename: str | None) -> int | None:
    """The document year. Takes the LAST plausible year: exports commonly lead with a client or job
    number and trail with the tax year."""
    years = extract_years(filename)
    return years[-1] if years else None


def has_ambiguous_year(filename: str | None) -> bool:
    """More than one DISTINCT year in the filename — which one names the document is a judgement call."""
    return len(set(extract_years(filename))) > 1


def detect_version_markers(filename: str | None) -> list[str]:
    """Amendment / revision / workflow-selection markers present in the filename."""
    return [m.group(0) for m in _VERSION_RE.finditer(strip_extension(filename))]


def detect_form_families(filename: str | None) -> set[str]:
    """Distinct SPECIFIC form families mentioned. Two or more means the filename names more than one
    materially different document (e.g. "1040 and K-1 and 8879")."""
    stem = strip_extension(filename).lower()
    families = set()
    for type_code, patterns, _ in _FILENAME_RULES:
        family = _FORM_FAMILY.get(type_code)
        if not family:
            continue
        if any(re.search(p, stem) for p in patterns):
            families.add(family)
    return families


def resolve_document_type(category: str | None, filename: str | None) -> TypeMatch:
    """Type from existing category, then filename patterns, then the shared classifier."""
    cat = (category or "").strip().lower().replace(" ", "_")
    if cat and cat not in VAGUE_CATEGORIES:
        mapped = _CATEGORY_TO_TYPE.get(cat) or _CATEGORY_TO_TYPE.get(cat.replace("_", ""))
        if mapped:
            return TypeMatch(mapped, 0.95, "category", None)

    stem = strip_extension(filename).lower()
    for type_code, patterns, conf in _FILENAME_RULES:
        for pat in patterns:
            hit = re.search(pat, stem)
            if hit:
                return TypeMatch(type_code, conf, "filename_pattern", hit.group(0))

    # Shared deterministic classifier, filename only — never OCR, never the source file.
    doc_type, conf = classify_document(filename, None)
    if doc_type != "unknown":
        return TypeMatch(doc_type, conf, "classifier", None)
    return TypeMatch("unknown", 0.0, "none", None)


def type_label(type_code: str | None) -> str | None:
    if not type_code or type_code == "unknown":
        return None
    return DISPLAY_LABELS.get(type_code, type_code.replace("_", " ").title())


def residual_qualifier(filename: str | None, *, year: int | None, type_code: str | None,
                       entity: str | None, matched_text: str | None = None) -> str | None:
    """Detail in the original filename that YEAR - TYPE - OWNER does not already carry.

    Removes, in order: the exact text the type matcher matched (as a complete unit), other spellings
    of the type, whole dates, years, the owner's name tokens, and pure filler. Whatever survives is
    real provenance — an employer, payer, custodian, spouse or account reference — and is returned so
    the caller can append it rather than throw it away.
    """
    # A scanner export is a timestamp, not provenance — it never yields a qualifier.
    if is_scanner_filename(filename):
        return None
    residue = sanitize(strip_extension(filename))
    # Remove sensitive identifiers -- label AND value together -- BEFORE any other processing, so a
    # partially-stripped SSN or account number can never survive as "residual detail". Surrounding
    # non-sensitive wording is preserved: "Acct 4471002983 Chase" keeps "Chase".
    residue = scrub(residue)
    if not residue:
        return None
    # A trailing "(2)" / "- Copy" is filler by default. The preview re-attaches it via
    # duplicate_suffix() ONLY when its absence would make two documents collide.
    residue = _DUP_SUFFIX.sub(" ", residue)

    # 1. The matched form reference, removed whole. This is what stops "8879S" -> "S",
    #    "1099INT" -> "INT" and "1099-R" -> "R".
    if matched_text:
        residue = re.sub(re.escape(matched_text), " ", residue, flags=re.I)

    # 1b. Every OTHER pattern for the same type. A type can be reached by any of its patterns, so
    #     "Schedule C Income Expense Worksheet" matches on "schedule c" and would otherwise keep the
    #     worksheet phrase as a qualifier.
    for pattern in _PATTERNS_BY_TYPE.get(type_code or "", []):
        residue = re.sub(pattern, " ", residue, flags=re.I)

    # 2. Other spellings of the same type: label, code, and separator-free forms.
    variants = set()
    for token in filter(None, [type_label(type_code), type_code, (type_code or "").replace("_", " ")]):
        variants.add(token)
        variants.add(re.sub(r"[^0-9A-Za-z]", "", token))
    for token in sorted(filter(None, variants), key=len, reverse=True):
        residue = re.sub(rf"{_TOKEN}{re.escape(token)}{_END}", " ", residue, flags=re.I)

    # 3. Whole dates before bare years, so "2024-04-02" cannot leave "04 02" behind.
    residue = _DATE_RE.sub(" ", residue)
    for y in set(extract_years(filename)):
        residue = re.sub(rf"(?<!\d){y}(?!\d)", " ", residue)
    if year:
        residue = re.sub(rf"(?<!\d){year}(?!\d)", " ", residue)

    # 4. The owner's own name adds nothing the OWNER segment does not already say.
    for word in re.split(r"[\s_\-]+", entity or ""):
        if len(word) > 2:
            residue = re.sub(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", " ", residue, flags=re.I)

    # 5. Filler words ("Tax_", "for", "copy") — dropped, but never anything unrecognised.
    kept = [w for w in re.split(r"[\s_\-]+", residue) if w and w.lower().strip(".,") not in _NOISE_TOKENS]
    residue = re.sub(r"[\s_\-]+", " ", " ".join(kept)).strip(" -_,.")

    if len(residue) < 3 or re.fullmatch(r"[\d\W_]+", residue) or _GENERIC_RE.match(residue):
        return None
    return sanitize(residue)


def canonical_display_name(*, year, type_code, entity, qualifier=None) -> str | None:
    """``YEAR - TYPE - OWNER[ - QUALIFIER]``. Segments with no value are omitted, never padded with
    placeholders. Returns ``None`` when neither a type nor a qualifier is known. Never contains a
    document id."""
    label = type_label(type_code)
    entity = sanitize(entity)
    qualifier = sanitize(qualifier) if qualifier else None
    if not label and not qualifier:
        return None
    parts = [str(year) if year else None, label, entity or None, qualifier]
    name = " - ".join(p for p in parts if p)
    if len(name) > MAX_NAME_LEN:
        head = " - ".join(p for p in [str(year) if year else None, label, entity or None] if p)
        name = head[:MAX_NAME_LEN].rstrip(" -") if len(head) > MAX_NAME_LEN else head
    return name or None
