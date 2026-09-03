"""View model for the staff Client/Household Documents screen.

PURE PRESENTATION over rows the document platform already returned. This module reads no database,
writes nothing, and creates no second document system: every row it shapes came from
``document_platform.relationships.client_documents`` and every value it shows is either a real
column, a real ``tags`` key, or a derivation from the EXISTING ``document_naming`` primitives —
labelled as derived wherever it is.

The honesty rules that shape it, in order of how often they bite:

* **Nothing is invented.** The production census for the White household is the design constraint:
  ``classification`` is NULL on all 291 documents, ``subcategory`` is NULL, the Knowledge layer has
  classified none of them, and only 3 carry a ``tax_year`` tag. A screen that renders those as
  blanks is useless; a screen that fabricates them is worse. So a missing type or year is DERIVED
  by ``document_naming.resolve_document_type`` / ``extract_year`` — the same deterministic,
  filename-only helpers the naming preview already trusts — and every derived value carries its
  confidence and is flagged ``*_derived`` so the UI can mark it as inferred rather than filed.
* **Raw source internals are never the primary label.** SharePoint/Graph ids, hashes and storage
  paths are provenance. The staff-facing name is ``document_naming.document_display_name``; the
  original filename is the secondary line; ids appear only in the detail panel.
* **Where a document is FILED is shown, never guessed.** ``related_to`` reports the row's actual
  anchor. A household document reads "Household" on both spouses' screens — it is not silently
  attributed to whichever person is being viewed.

Category tabs come from ``documents.classification`` (the platform's own declared taxonomy) when it
is set, and fall back to the derived document type when it is not. One bucket per document, so the
tab counts sum to the total.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

from app.services.document_naming import (
    document_display_name,
    extract_year,
    resolve_document_type,
    type_label,
)

#: The category tabs, in display order. ``key`` is the query value; ``all`` is the unfiltered view.
TABS = (
    {"key": "all", "label": "All Documents", "icon": "▤"},
    {"key": "tax", "label": "Tax", "icon": "▣"},
    {"key": "investments", "label": "Investments", "icon": "▦"},
    {"key": "insurance", "label": "Insurance", "icon": "◈"},
    {"key": "estate", "label": "Estate / Legal", "icon": "◉"},
    {"key": "business", "label": "Business", "icon": "▧"},
    {"key": "other", "label": "Other", "icon": "○"},
)
_TAB_KEYS = frozenset(t["key"] for t in TABS)

#: Sort sentinel for rows carrying no date at all — they sort last and this is never rendered.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: ``documents.classification`` -> tab. The platform's own taxonomy wins whenever it is populated.
_CLASSIFICATION_TAB = {
    "tax": "tax",
    "investment": "investments", "retirement": "investments",
    "insurance": "insurance", "benefits": "insurance",
    "estate": "estate", "legal": "estate",
    "operations": "business", "hr": "business",
    "compliance": "other", "client": "other", "marketing": "other",
    "internal": "other", "archived": "other",
}

#: Derived ``document_naming`` type code -> tab, used only when ``classification`` is NULL.
#: A document lands in exactly one bucket, so the entity returns (1120/1120S/1065/941/payroll/
#: Schedule C) are filed under Business rather than Tax: staff look for them beside the entity,
#: and duplicating them across two tabs would make the counts lie.
_TYPE_TAB = {
    "1040": "tax", "1040-X": "tax", "1041": "tax", "8879": "tax", "W-2": "tax",
    "K-1": "tax", "1095-A": "tax", "1095-C": "tax", "1098-T": "tax",
    "1099": "tax", "1099-R": "tax", "1099-K": "tax", "1099-INT": "tax",
    "1099-DIV": "tax", "1099-NEC": "tax", "1099-SA": "tax",
    "organizer": "tax", "tax_return": "tax", "tax_documents": "tax",
    "irs_notice": "tax", "state_notice": "tax", "year_end_tax_package": "tax",
    "signature_documents": "tax", "mortgage_interest": "tax",
    "brokerage_statement": "investments", "bank_statement": "investments",
    "financial_statement": "investments",
    "insurance_policy": "insurance", "benefits_enrollment": "insurance",
    "trust_document": "estate", "estate_document": "estate",
    "drivers_license": "estate", "passport": "estate",
    "1120": "business", "1120S": "business", "1120-X": "business",
    "1065": "business", "1065-X": "business", "941": "business",
    "payroll_summary": "business", "schedule_c": "business",
    "engagement_letter": "business",
}

#: File-type icon buckets. Extension first (it is what the user recognises), MIME as the fallback.
_EXT_KIND = {
    "pdf": "pdf",
    "xlsx": "sheet", "xlsm": "sheet", "xls": "sheet", "csv": "sheet",
    "doc": "doc", "docx": "doc", "rtf": "doc", "txt": "doc", "odt": "doc",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "bmp": "image",
    "tif": "image", "tiff": "image", "webp": "image", "heic": "image", "heif": "image",
    "zip": "archive", "7z": "archive", "rar": "archive",
    "msg": "mail", "eml": "mail",
}
_KIND_GLYPH = {"pdf": "PDF", "sheet": "XLS", "doc": "DOC", "image": "IMG",
               "archive": "ZIP", "mail": "EML", "file": "FILE"}

#: ``review_status`` values that put a document on the Needs Review worklist. ``not_required`` and
#: ``approved`` are settled states; anything else is outstanding work.
_SETTLED_REVIEW = frozenset({"not_required", "none", "approved", "complete", "completed", ""})

#: How recent "Recent" means. A presentation window over the date the row already carries
#: (``sort_date`` — the SOURCE date, not the ingest timestamp), so the rail's Recent view is the
#: same rows the Date column shows, filtered. Nothing is read or written to produce it.
RECENT_DAYS = 45

#: Staff-facing wording for the ``review_status`` values that put a document on the worklist. An
#: unrecognised value is title-cased rather than mapped to an invented state — the screen reports
#: the status the row carries, it does not decide what the status means.
_REVIEW_LABELS = {
    "pending": "Pending review",
    "in_review": "In review",
    "needs_review": "Needs review",
    "flagged": "Flagged",
    "rejected": "Rejected",
    "hold": "On hold",
}
_APPROVED_REVIEW = frozenset({"approved", "complete", "completed"})

#: Sortable columns. Every key reads a field the row already carries; there is no second query and
#: no derived ordering that the visible cells cannot explain. ``date`` keeps the screen's original
#: newest-first default, so an unsorted screen is byte-identical to what it was.
_SORT_KEYS = {
    "name": lambda r: (r.get("name") or "").lower(),
    "type": lambda r: (r.get("type_text") or "").lower(),
    "year": lambda r: (r.get("year") or ""),
    "related": lambda r: ((r.get("related_to") or {}).get("label") or "").lower(),
    "source": lambda r: (r.get("source") or "").lower(),
    "status": lambda r: ((r.get("status") or {}).get("label") or "").lower(),
}
#: Columns that read best newest/highest first when the user first clicks them.
_SORT_DESC_FIRST = frozenset({"date", "year"})
SORTS = ("date", "name", "type", "year", "related", "source", "status")


def _file_kind(row) -> str:
    name = row.get("original_name") or row.get("name") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in _EXT_KIND:
        return _EXT_KIND[ext]
    mime = (row.get("content_type") or "").lower()
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if "spreadsheet" in mime or "excel" in mime:
        return "sheet"
    if "word" in mime or mime.startswith("text/"):
        return "doc"
    if "zip" in mime or "compressed" in mime:
        return "archive"
    return "file"


def _related_to(row, *, member_names, household_name):
    """Where the document is FILED — never a guess about who it is "really" about.

    Reads the row's own anchor. Household-anchored documents say "Household" on every screen,
    including a single member's, because that is where they live.
    """
    if row.get("organization_id"):
        return {"label": "Business", "kind": "organization", "id": row["organization_id"]}
    if row.get("household_id"):
        return {"label": household_name or "Household", "kind": "household",
                "id": row["household_id"]}
    pid = row.get("person_id")
    if pid:
        return {"label": member_names.get(pid) or f"Person {pid}", "kind": "person", "id": pid}
    return {"label": "Unfiled", "kind": "none", "id": None}


def _needs_review(row) -> bool:
    return str(row.get("review_status") or "").strip().lower() not in _SETTLED_REVIEW


def _status(row, needs_review: bool) -> dict:
    """The row's REVIEW state, in staff language — never a filing verdict the row does not carry.

    A document whose ``review_status`` is NULL has not been reviewed and has not been approved; it
    is simply not on a worklist. That reads as "—", not as "Filed": inventing a settled-looking
    state for the majority of rows would be the screen asserting something no column says.
    """
    raw = str(row.get("review_status") or "").strip().lower()
    if needs_review:
        return {"label": _REVIEW_LABELS.get(raw, raw.replace("_", " ").capitalize() or "Needs review"),
                "kind": "review"}
    if raw in _APPROVED_REVIEW:
        return {"label": "Approved", "kind": "ok"}
    if raw == "not_required":
        return {"label": "Not required", "kind": "muted"}
    return {"label": "—", "kind": "none"}


# ------------------------------------------------------------------------------------------------
# Review reasons — WHY a document is unsettled, in TWO TIERS
# ------------------------------------------------------------------------------------------------
#
# One flat list was the wrong shape for this data. On a real client the per-document problems run to
# a couple of dozen while the bulk metadata gaps run to hundreds — the White household has 291
# documents with ``classification`` NULL on every one, so "no filed type" and "no filed year" fire on
# essentially the whole file. Merged into one list, the handful of documents genuinely waiting on a
# person is invisible, and "Needs review" stops meaning anything.
#
#   ACTIONABLE   one document, one decision, a person resolves it now.
#   INCOMPLETE   a field nobody has filled in yet — resolved in bulk by a backfill or a rule.
#                Counted and filterable, never allowed to drown the tier above.
#
# Both tiers are real, recorded states. They are simply different work.
#
# Every reason is declared once, in the table below, and its tier decides which section it lands in —
# so adding a reason never means remembering to place it in a rail, a panel and a chip list
# separately. ``basis`` names the backend state in the backend's OWN words, so a reader can tell a
# recorded column from a derived judgement without reading this file.
#
# What is deliberately NOT here (see EXCLUDED_NOTES, which states both in the UI):
#
# * **Ownership proposals.** HIGH/HOLD ``document_facts`` owner proposals are resolved in Admin →
#   Document Management / Unassigned Documents, BEFORE a document reaches a client. No reason below
#   reads a proposal, and none of them decides an owner. ``owner_missing`` reports the ABSENCE of a
#   stored anchor; it never reads a name out of a filename and calls that identity.
# * **Knowledge-layer classification.** The classifier has only ever run over unassigned documents,
#   so counting "unclassified" would flag virtually every client document forever — reporting a
#   pipeline coverage gap as though it were a decision someone owes.

ACTIONABLE = "actionable"
INCOMPLETE = "incomplete"

#: Section heading per tier. A reason's heading follows from its tier alone.
TIER_LABELS = {ACTIONABLE: "Needs review", INCOMPLETE: "Incomplete metadata"}

#: OCR states the pipeline writes, mirrored onto ``documents.ocr_status`` (ADR-072, migration
#: dococr01) and constrained by ``ck_document_ocr_status``. The split is the point: a retryable
#: failure is somebody's next action, while 'unsupported' means no text will ever be extracted and
#: there is nothing to retry — so they cannot share a tier.
OCR_RETRYABLE = ("failed", "timed_out")
OCR_TERMINAL = ("unsupported",)


def _no_available_source(row) -> bool:
    """Every source reference this document has reports its copy gone.

    Written by ``document_sources.mark_source_unavailable``, so it is a recorded state rather than an
    inference. A document with NO source references at all is a direct upload and is never flagged —
    absence of a source is not a missing source.
    """
    sources = row.get("sources") or []
    return bool(sources) and not any(s.get("available") for s in sources)


def _filed_category(row):
    """The filing category as the platform displays it everywhere else — ``classification`` when it is
    set, else ``category``. One spelling, so this screen cannot disagree with the document platform
    about whether a document has been categorised (see ``document_platform.service``)."""
    return (row.get("classification") or row.get("category") or "").strip() or None


def _year_evidence(row) -> str:
    return "; ".join(row.get("tax_year_evidence") or [])


@dataclass(frozen=True)
class ReviewReason:
    key: str
    #: The staff-facing heading for this reason.
    what: str
    #: The backend state in the backend's own words.
    basis: str
    #: Styling hook, and the existing panel's ``reason--{kind}`` class.
    kind: str
    tier: str
    test: Callable[[dict], bool]
    #: Optional per-row detail — the version reviewer's own reason, the tax-year evidence.
    detail: Callable[[dict], str | None] = lambda row: None


#: DECLARATION ORDER IS DISPLAY ORDER, and actionable reasons come first everywhere.
REVIEW_REASONS: tuple[ReviewReason, ...] = (
    ReviewReason(
        "review_requested", "Marked for review",
        "review_status is outside the settled set", "status", ACTIONABLE,
        lambda row: bool(row.get("needs_review")),
        lambda row: (f"The document's review status is “{row.get('review_status')}”."
                     if row.get("review_status")
                     else "The document carries an unsettled review status.")),
    ReviewReason(
        "owner_missing", "No owner on file",
        "the row carries no person, household or business anchor", "owner", ACTIONABLE,
        lambda row: (row.get("related_to") or {}).get("kind") == "none",
        lambda row: "This document is not anchored to a person, household or business. "
                    "Ownership is never inferred from the filename here."),
    ReviewReason(
        "version_ambiguous", "Look-alike not resolved",
        "needs_version_review — source identity could not confirm two copies are the same file",
        "version", ACTIONABLE,
        lambda row: bool(row.get("needs_version_review")),
        lambda row: row.get("version_review_reason")
                    or "Another document shares this filename and the source could not confirm "
                       "they are the same file. Both are kept."),
    ReviewReason(
        "tax_year_conflict", "Tax year unresolved",
        "infer_tax_year reports 'conflict' — the evidence disagrees", "year", ACTIONABLE,
        lambda row: row.get("tax_year_confidence") == "conflict",
        lambda row: ("The filename and the source folder name different years, so no year is filed "
                     "and none is proposed. " + _year_evidence(row)).strip()),
    ReviewReason(
        "ocr_failed", "Text extraction failed",
        "ocr_status is 'failed' or 'timed_out' — retryable", "ocr", ACTIONABLE,
        lambda row: (row.get("ocr_status") or "") in OCR_RETRYABLE,
        lambda row: f"Extraction ran and produced no text (ocr_status is "
                    f"“{row.get('ocr_status')}”). It can be retried."),
    ReviewReason(
        "source_missing", "Source copy missing",
        "every source reference for this document is unavailable", "source", ACTIONABLE,
        _no_available_source,
        lambda row: "The source system no longer holds a copy: "
                    + (", ".join(sorted({s.get("source_system") or "?"
                                         for s in (row.get("sources") or [])})) or "?")
                    + ". The canonical record is kept."),
    ReviewReason(
        "duplicate", "Identical copy exists",
        "another row in this client's file shares this checksum", "duplicate", ACTIONABLE,
        lambda row: bool(row.get("is_duplicate")),
        lambda row: f"{row.get('duplicate_count')} rows in this client's file share this "
                    "document's checksum."),
    ReviewReason(
        "type_missing", "Document type is a proposal",
        "no filed type — the type shown is read from the filename", "type", INCOMPLETE,
        lambda row: bool(row.get("type_derived")),
        lambda row: f"Derived from the "
                    f"{(row.get('type_basis') or 'filename').replace('_', ' ')} — the document "
                    "carries no filed type."),
    ReviewReason(
        "year_derived", "Tax year is a proposal",
        "no filed tax year — the year shown is read from the filename or the source folder",
        "year", INCOMPLETE,
        # Never alongside ``tax_year_conflict``: on a conflicting row ``shape_row``'s last-resort
        # filename fallback still yields a year, and reporting it as a plain proposal would
        # contradict the actionable reason sitting directly above it.
        lambda row: bool(row.get("year_derived")) and row.get("tax_year_confidence") != "conflict",
        lambda row: ("Read from " + _year_evidence(row) if _year_evidence(row)
                     else "Read from the filename") + " — not a filed value."),
    ReviewReason(
        "year_missing", "No tax year",
        "no year in the filename or the source folder path", "year", INCOMPLETE,
        lambda row: not row.get("year") and row.get("tax_year_confidence") != "conflict"),
    ReviewReason(
        "category_missing", "Category not set",
        "neither classification nor category is filed", "category", INCOMPLETE,
        lambda row: _filed_category(row) is None),
    ReviewReason(
        "ocr_unsupported", "Text not extractable",
        "ocr_status is 'unsupported' — no text will ever be extracted", "ocr", INCOMPLETE,
        lambda row: (row.get("ocr_status") or "") in OCR_TERMINAL,
        lambda row: "This file has no text layer, so extraction will never produce one. "
                    "There is nothing to retry."),
)

REVIEW_REASONS_BY_KEY = {r.key: r for r in REVIEW_REASONS}

#: Surfaced on the screen so the deliberate omissions are STATED rather than silently absent. A
#: reviewer who reads "Needs Review: 17" is entitled to know what that number leaves out and where
#: the excluded work actually lives.
EXCLUDED_NOTES: tuple[tuple[str, str], ...] = (
    ("Ownership proposals",
     "HIGH and HOLD ownership proposals are resolved in Admin → Document Management → Unassigned "
     "Documents, before a document reaches a client. They are never counted here, and nothing on "
     "this screen reads one."),
    ("Knowledge classification",
     "The Knowledge classifier has only run over unassigned documents, so an unclassified client "
     "document is a pipeline gap, not a decision anyone owes. It is shown per document and never "
     "counted as review."),
)


def review_reasons_for(row) -> list:
    """Every review reason this row carries, actionable first.

    ``row`` is a SHAPED row (or the equivalent mapping): the reasons read the derived flags
    ``shape_row`` produces — ``related_to``, ``needs_review``, ``type_derived``, ``year_derived`` —
    alongside the enrichment's own columns.

    Nothing here decides ownership, settles a year, or promotes a derived value. The point of the
    list is the opposite: to say out loud which of the row's visible cells are proposals rather than
    filed facts, so a reviewer is never led to treat a filename reading as an identity.
    """
    out = []
    for reason in REVIEW_REASONS:
        try:
            hit = bool(reason.test(row))
        except Exception:      # noqa: BLE001 — one malformed row must never blank the screen
            hit = False
        if hit:
            out.append({"key": reason.key, "what": reason.what, "basis": reason.basis,
                        "kind": reason.kind, "tier": reason.tier, "detail": reason.detail(row)})
    return out


def _tab_for(row, type_code) -> tuple[str, str]:
    """(tab key, how it was decided). Stored classification wins; the derived type is the fallback."""
    classification = (row.get("classification") or "").strip().lower()
    if classification:
        return _CLASSIFICATION_TAB.get(classification, "other"), "classification"
    if type_code and type_code in _TYPE_TAB:
        return _TYPE_TAB[type_code], "derived_type"
    return "other", "unclassified"


def _size_label(size_bytes) -> str:
    try:
        n = int(size_bytes or 0)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    for unit, cut in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= cut:
            value = n / cut
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{n} B"


def _is_recent(when, now) -> bool:
    """Whether `when` falls inside the Recent window, without ever raising on a mixed-awareness
    comparison. ``sort_date`` is aware when it came from a source timestamp but is the raw database
    value when it fell back to ``updated_at``/``created_at``, and those two cannot be subtracted.
    An unknown date is simply not recent — the rail loses a row, never the page."""
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (now - when).days <= RECENT_DAYS


def _as_datetime(value):
    """A datetime for a value that may already be one, an ISO-8601 string, or nothing.

    Always timezone-aware, because `sort_date` mixes these with database timestamps and comparing an
    aware datetime with a naive one raises. A source timestamp that carries no offset is read as UTC,
    which is what the ingestion writes.

    Returns None for anything unparseable rather than raising: a malformed source timestamp must
    cost the row its Date cell, never the whole screen.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def shape_row(row, *, member_names, household_name, now=None):
    """One document row in the shape the screen renders. Adds derived presentation fields to the
    row it was given and returns a NEW dict — the caller's row object is never mutated."""
    name = row.get("name") or document_display_name(row) or f"Document {row.get('id')}"
    original = row.get("original_name") or ""

    # --- type: the stored value when there is one, else the deterministic filename derivation.
    stored_type = row.get("document_type") or row.get("subcategory")
    if stored_type:
        type_code, type_text = stored_type, str(stored_type).replace("_", " ")
        type_conf, type_derived = row.get("classification_confidence"), False
        type_basis = "filed"
    else:
        match = resolve_document_type(row.get("category"), original)
        type_code = match.code if match.code != "unknown" else None
        type_text = type_label(type_code)
        type_conf = match.confidence if type_code else None
        type_derived = type_code is not None
        type_basis = match.source if type_code else None

    # --- year: the tag when filed, else the year in the filename.
    #
    # ``row["tax_year"]`` is NOT proof the year was filed. The Documents-tab enrichment
    # (client360.sections._attach_classification) already runs document_tax_year.infer_tax_year and
    # puts its PROPOSAL in that key, flagged by ``tax_year_inferred``. Reading the key alone would
    # present every inferred year as a recorded fact — the one thing that inference layer exists to
    # avoid — so the flag decides, and only falls back to this module's own derivation for callers
    # that did not enrich (the screen is also built directly from client_documents rows).
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    stored_year = row.get("tax_year") or tags.get("tax_year") or tags.get("year")
    if stored_year:
        year, year_derived = str(stored_year), bool(row.get("tax_year_inferred"))
    else:
        derived = extract_year(original)
        year, year_derived = (str(derived) if derived else None), derived is not None

    tab, tab_basis = _tab_for(row, type_code)
    kind = _file_kind(row)
    # The date staff care about is the document's date AT THE SOURCE. Every migrated document shares
    # one ingest timestamp, so ``updated_at`` reads as meaningless in a Date column. The source date
    # arrives from the enrichment as an ISO-8601 STRING, so it is parsed rather than assumed to be a
    # datetime — mixing the two would raise the moment the rows are sorted by date.
    when = _as_datetime(row.get("source_modified_at") or row.get("source_created_at"))         or row.get("updated_at") or row.get("created_at")

    related_to = _related_to(row, member_names=member_names, household_name=household_name)
    needs_review = _needs_review(row)
    # "Recent" is a window over the date the Date column already shows, so the rail view and the
    # cell can never disagree. `now` is injectable purely so a caller shaping a whole page uses one
    # clock for every row.
    now = now or datetime.now(UTC)

    # The reason table reads the DERIVED flags alongside the row's own columns, so it is given a
    # row that already carries them. Building the probe here (rather than passing six arguments)
    # is what lets a test call ``review_reasons_for`` with a plain dict and get the same answer the
    # screen gets.
    probe = {**row, "related_to": related_to, "needs_review": needs_review, "year": year,
             "type_derived": type_derived, "type_basis": type_basis, "year_derived": year_derived}
    reasons = review_reasons_for(probe)

    return {
        **row,
        "name": name,
        "original_name": original,
        "file_kind": kind,
        "file_glyph": _KIND_GLYPH.get(kind, "FILE"),
        "type_code": type_code,
        "type_text": type_text,
        "type_confidence": type_conf,
        "type_derived": type_derived,
        "type_basis": type_basis,
        "year": year,
        "year_derived": year_derived,
        "related_to": related_to,
        "needs_review": needs_review,
        "status": _status(row, needs_review),
        # All three are shipped: the flat list for anything that just wants to count, and the two
        # tiers pre-split so no template has to know which reason belongs where.
        "review_reasons": reasons,
        "actionable": [r for r in reasons if r["tier"] == ACTIONABLE],
        "incomplete": [r for r in reasons if r["tier"] == INCOMPLETE],
        "tab": tab,
        "tab_basis": tab_basis,
        "sort_date": when,
        "is_recent": _is_recent(when, now),
        "date_label": when.strftime("%b %d, %Y") if when else "—",
        "size_label": _size_label(row.get("size_bytes")),
    }


def _matches(row, *, q, tab, year, type_code, related, needs_review, recent=False,
             incomplete=False, flag=None) -> bool:
    if tab and tab != "all" and row["tab"] != tab:
        return False
    if recent and not row.get("is_recent"):
        return False
    if incomplete and not row.get("incomplete"):
        return False
    # An UNRECOGNISED reason key is a broken link (a renamed reason, a stale bookmark), not an empty
    # queue, so it falls through to everything rather than rendering a convincing "0 documents".
    # A year/type/related value that matches nothing is different: those are open value spaces, and
    # "no documents like that" is the true answer rather than a sign the link is wrong.
    if flag and flag in REVIEW_REASONS_BY_KEY and not any(
            r["key"] == flag for r in row.get("review_reasons") or ()):
        return False
    if year and (row.get("year") or "") != year:
        return False
    if type_code and (row.get("type_code") or "") != type_code:
        return False
    if related and f"{row['related_to']['kind']}:{row['related_to']['id']}" != related:
        return False
    if needs_review and not row["needs_review"]:
        return False
    if q:
        # Name, original filename, type, year and source — the fields a staff member can SEE.
        # Storage paths and hashes are deliberately not searchable here; they are provenance,
        # and matching them would return rows with no visible reason for matching.
        hay = " ".join(str(x) for x in (
            row.get("name"), row.get("original_name"), row.get("type_text"),
            row.get("year"), row.get("source"), row["related_to"]["label"]) if x).lower()
        if q not in hay:
            return False
    return True


def _related_options(shaped):
    """Distinct Related To values present in this client's documents, households first."""
    seen = {}
    for r in shaped:
        rel = r["related_to"]
        key = f"{rel['kind']}:{rel['id']}"
        seen.setdefault(key, {"key": key, "label": rel["label"], "kind": rel["kind"]})
    order = {"household": 0, "person": 1, "organization": 2, "none": 3}
    return sorted(seen.values(), key=lambda o: (order.get(o["kind"], 9), o["label"]))


def build(rows, *, member_names=None, household_name=None, q=None, tab="all", year=None,
          type_code=None, related=None, needs_review=False, recent=False, incomplete=False,
          flag=None, sort="date", direction=None, page=1, page_size=25):
    """The whole screen: shaped rows, tab counts, facet options and one page of results.

    Filtering happens over the shaped rows rather than in SQL because the type and year a staff
    member filters on are frequently DERIVED (see the module docstring) and so do not exist as
    columns to filter. The document set for one client is bounded — a few hundred rows — so this
    is a list comprehension, not a query planner problem.
    """
    member_names = member_names or {}
    q = (q or "").strip().lower() or None
    tab = normalize_tab(tab)
    sort, direction = normalize_sort(sort, direction)
    recent, incomplete = bool(recent), bool(incomplete)
    # An unrecognised reason key is dropped rather than filtered on, so a renamed reason or a stale
    # bookmark shows the whole file instead of a convincing "0 documents" — and the dead key is not
    # then carried along in every link the screen builds.
    flag = flag if flag in REVIEW_REASONS_BY_KEY else None
    # Superseded rows do not get their own line. When ONE source file has been re-synced, the
    # enrichment (client360.sections._attach_version_family) marks the earlier copies
    # ``is_current_version = False``; listing them beside the latest would show the same document
    # three times and make every count wrong. Grouping is by SOURCE IDENTITY only, never by
    # filename, so two documents that merely share a name are both current and both listed here.
    # A row that was never enriched carries no flag and is treated as current, never dropped.
    rows = [r for r in rows if r.get("is_current_version", True) is not False]
    now = datetime.now(UTC)
    shaped = [shape_row(r, member_names=member_names, household_name=household_name, now=now)
              for r in rows]

    # Tab counts are computed against every OTHER active filter, so the number on a tab is what
    # that tab will actually show when clicked.
    def _count(tab_key):
        return sum(1 for r in shaped if _matches(
            r, q=q, tab=tab_key, year=year, type_code=type_code, related=related,
            needs_review=needs_review, recent=recent, incomplete=incomplete, flag=flag))

    tabs = [{**t, "count": _count(t["key"]), "active": t["key"] == tab} for t in TABS]

    matched = [r for r in shaped if _matches(
        r, q=q, tab=tab, year=year, type_code=type_code, related=related,
        needs_review=needs_review, recent=recent, incomplete=incomplete, flag=flag)]
    _sort_rows(matched, sort, direction)

    total = len(matched)
    page_size = max(1, min(200, int(page_size or 25)))
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = max(1, min(int(page or 1), pages))
    start = (page - 1) * page_size
    window = matched[start:start + page_size]

    return {
        "rows": window,
        "tabs": tabs,
        "total": total,
        "total_all": len(shaped),
        # ``needs_review_count`` keeps its established meaning — documents carrying an unsettled
        # review_status — because that is what the Needs Review worklist has always been and what
        # every link into it already means. The tier counts sit BESIDE it rather than redefining it.
        "needs_review_count": sum(1 for r in shaped if r["needs_review"]),
        "recent_count": sum(1 for r in shaped if r["is_recent"]),
        "actionable_count": sum(1 for r in shaped if r["actionable"]),
        "incomplete_count": sum(1 for r in shaped if r["incomplete"]),
        "page": page, "pages": pages, "page_size": page_size,
        "range_start": start + 1 if total else 0,
        "range_end": start + len(window),
        # Facets are built from the FULL set, not the filtered one, so choosing a year never
        # empties the type list and strands the user with no way back.
        "years": sorted({r["year"] for r in shaped if r["year"]}, reverse=True),
        "types": sorted({(r["type_code"], r["type_text"]) for r in shaped
                         if r["type_code"] and r["type_text"]}, key=lambda t: t[1]),
        "related_options": _related_options(shaped),
        "filters": {"q": q or "", "tab": tab, "year": year or "",
                    "type": type_code or "", "related": related or "",
                    "needs_review": bool(needs_review), "recent": recent,
                    "incomplete": incomplete, "flag": flag or "",
                    "sort": sort, "direction": direction},
        # The rail's current view. One of them is always on, so the left navigation always shows
        # the user where they are instead of leaving every entry looking unselected. A `flag` drill
        # -down is its own view, so the reason the user clicked is the entry that reads as selected.
        "view": (f"flag:{flag}" if flag in REVIEW_REASONS_BY_KEY else
                 "review" if needs_review else
                 "incomplete" if incomplete else
                 "recent" if recent else "all"),
        # One counted rail entry per reason, grouped by tier. Zero-count entries are kept: a
        # reviewer needs to see that a queue is EMPTY, which is a different fact from the queue not
        # existing. Counts are computed against the other active filters, exactly like the tabs.
        "reason_views": _reason_views(shaped, q=q, tab=tab, year=year, type_code=type_code,
                                      related=related, recent=recent),
        "excluded_notes": EXCLUDED_NOTES,
        # Column headers, pre-resolved: the link each one points at and the arrow it shows. Built
        # here so the template never re-derives which direction a second click should produce.
        "sort_columns": _sort_columns(sort, direction),
        # Pre-built so the template never has to concatenate a query string by hand (and never has
        # to remember to url-encode a client's name in a Related To value).
        "query_keep": _query_keep(q, year, type_code, related, needs_review, recent,
                                  sort, direction, incomplete, flag),
        "query_keep_no_review": _query_keep(q, year, type_code, related, False, recent,
                                            sort, direction, incomplete, flag),
        # The filter state WITHOUT the view flags, for the rail: switching to Needs Review,
        # Incomplete, Recent or one reason must keep the search and the facets, not carry the
        # previous view along with them.
        "query_keep_no_view": _query_keep(q, year, type_code, related, False, False,
                                          sort, direction, False, None),
        # And without the sort, for the column headers, which set it themselves.
        "query_keep_no_sort": _query_keep(q, year, type_code, related, needs_review, recent,
                                          None, None, incomplete, flag),
        "page_numbers": _page_numbers(page, pages),
    }


def _reason_views(shaped, **active) -> list:
    """``[{tier, label, reasons: [{key, what, basis, kind, count}]}]`` — the rail's reason drill-downs.

    Built from the SAME table the rows were flagged from, so a reason can never appear as a chip on
    a document and be missing from the navigation (or vice versa).

    The list is keyed ``reasons`` and NOT ``items``: Jinja resolves ``group.items`` to the dict's own
    built-in method before it ever looks for a key of that name, so an ``items`` key renders as a
    bound method and the loop dies with "object is not iterable".
    """
    out = []
    for tier, label in TIER_LABELS.items():
        reasons = []
        for reason in REVIEW_REASONS:
            if reason.tier != tier:
                continue
            reasons.append({
                "key": reason.key, "what": reason.what, "basis": reason.basis,
                "kind": reason.kind, "tier": tier,
                "count": sum(1 for r in shaped
                             if _matches(r, needs_review=False, flag=reason.key, **active)),
            })
        out.append({"tier": tier, "label": label, "reasons": reasons})
    return out


def _query_keep(q, year, type_code, related, needs_review, recent=False,
                sort=None, direction=None, incomplete=False, flag=None) -> str:
    """The active filters as a `&`-prefixed query fragment, for links that change ONE other thing
    (a category tab, a page, the sort) and must preserve everything else. Empty when nothing is set.

    The default sort is deliberately omitted rather than written out, so an untouched screen keeps
    the clean URL it has always had and every existing link into it still describes the same view.
    """
    pairs = [(k, v) for k, v in (("dq", q), ("dyear", year), ("dtype", type_code),
                                 ("related", related)) if v]
    if needs_review:
        pairs.append(("review", "1"))
    if incomplete:
        pairs.append(("dincomplete", "1"))
    if flag:
        pairs.append(("dflag", flag))
    if recent:
        pairs.append(("drecent", "1"))
    if sort and (sort, direction) != ("date", "desc"):
        pairs.append(("dsort", sort))
        if direction:
            pairs.append(("ddir", direction))
    return ("&" + urlencode(pairs)) if pairs else ""


def _sort_rows(rows, sort, direction) -> None:
    """Order the matched rows in place. Presentation only: it reorders the SAME rows the filters
    already produced and can neither add nor remove one.

    Three stable passes rather than one composite key, because two of the rules must hold in BOTH
    directions and a single `reverse=True` would invert them:
      1. newest-first, as the tie-break, so equal values keep a meaningful order;
      2. the chosen column;
      3. rows with no value in that column go LAST either way — an empty cell is not a value that
         belongs at either end of the list.
    """
    reverse = direction == "desc"
    # `sort_date` is timezone-aware for every row that has one; rows with no date at all sort last
    # rather than raising on a None comparison.
    rows.sort(key=lambda r: (r["sort_date"] is not None, r["sort_date"] or _EPOCH), reverse=True)
    if sort == "date":
        if not reverse:
            rows.sort(key=lambda r: r["sort_date"] or _EPOCH)
        rows.sort(key=lambda r: r["sort_date"] is None)
        return
    key = _SORT_KEYS[sort]
    rows.sort(key=key, reverse=reverse)
    rows.sort(key=lambda r: not key(r))


#: Column key -> the header staff read. The Document column sorts by display name, which is the
#: name the cell actually shows (``document_naming.document_display_name``), not the filename.
_SORT_LABELS = {"name": "Document", "type": "Type", "year": "Year", "related": "Related To",
                "date": "Date", "source": "Source", "status": "Status"}


def _sort_columns(sort, direction) -> list:
    """One entry per sortable column: its key, header, whether it is the active sort, and the
    direction a click should ask for next."""
    out = []
    # THIS ORDER IS THE TABLE'S COLUMN ORDER. The template renders one <th> per entry and its own
    # <td>s separately, so a mismatch here silently puts every header over the wrong column.
    for key in ("name", "type", "year", "related", "source", "date", "status"):
        active = key == sort
        default = "desc" if key in _SORT_DESC_FIRST else "asc"
        nxt = ("asc" if direction == "desc" else "desc") if active else default
        out.append({"key": key, "label": _SORT_LABELS[key], "active": active,
                    "direction": direction if active else None, "next": nxt})
    return out


def normalize_sort(sort, direction) -> tuple:
    """A (column, direction) pair that is always valid. An unknown column falls back to the
    screen's original newest-first date order, so a hand-edited URL cannot produce an empty or
    differently-filtered screen."""
    sort = sort if sort in SORTS else "date"
    if direction not in ("asc", "desc"):
        direction = "desc" if sort in _SORT_DESC_FIRST else "asc"
    return sort, direction


def _page_numbers(page: int, pages: int, window: int = 2) -> list[int]:
    """Page numbers to render, with 0 standing for an elided run. Always shows the first and last
    page plus a window around the current one, so a 12-page client does not get 12 buttons."""
    if pages <= 7:
        return list(range(1, pages + 1))
    wanted = {1, pages} | {p for p in range(page - window, page + window + 1) if 1 <= p <= pages}
    out, previous = [], 0
    for p in sorted(wanted):
        if previous and p - previous > 1:
            out.append(0)
        out.append(p)
        previous = p
    return out


def normalize_tab(value) -> str:
    return value if value in _TAB_KEYS else "all"
