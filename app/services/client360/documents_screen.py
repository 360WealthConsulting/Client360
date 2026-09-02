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


def shape_row(row, *, member_names, household_name):
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
        "related_to": _related_to(row, member_names=member_names, household_name=household_name),
        "needs_review": _needs_review(row),
        "tab": tab,
        "tab_basis": tab_basis,
        "sort_date": when,
        "date_label": when.strftime("%b %d, %Y") if when else "—",
        "size_label": _size_label(row.get("size_bytes")),
    }


def _matches(row, *, q, tab, year, type_code, related, needs_review) -> bool:
    if tab and tab != "all" and row["tab"] != tab:
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
          type_code=None, related=None, needs_review=False, page=1, page_size=25):
    """The whole screen: shaped rows, tab counts, facet options and one page of results.

    Filtering happens over the shaped rows rather than in SQL because the type and year a staff
    member filters on are frequently DERIVED (see the module docstring) and so do not exist as
    columns to filter. The document set for one client is bounded — a few hundred rows — so this
    is a list comprehension, not a query planner problem.
    """
    member_names = member_names or {}
    q = (q or "").strip().lower() or None
    tab = normalize_tab(tab)
    # Superseded rows do not get their own line. When ONE source file has been re-synced, the
    # enrichment (client360.sections._attach_version_family) marks the earlier copies
    # ``is_current_version = False``; listing them beside the latest would show the same document
    # three times and make every count wrong. Grouping is by SOURCE IDENTITY only, never by
    # filename, so two documents that merely share a name are both current and both listed here.
    # A row that was never enriched carries no flag and is treated as current, never dropped.
    rows = [r for r in rows if r.get("is_current_version", True) is not False]
    shaped = [shape_row(r, member_names=member_names, household_name=household_name) for r in rows]

    # Tab counts are computed against every OTHER active filter, so the number on a tab is what
    # that tab will actually show when clicked.
    def _count(tab_key):
        return sum(1 for r in shaped if _matches(
            r, q=q, tab=tab_key, year=year, type_code=type_code, related=related,
            needs_review=needs_review))

    tabs = [{**t, "count": _count(t["key"]), "active": t["key"] == tab} for t in TABS]

    matched = [r for r in shaped if _matches(
        r, q=q, tab=tab, year=year, type_code=type_code, related=related,
        needs_review=needs_review)]
    # Newest first. `sort_date` is timezone-aware for every row that has one; rows with no date at
    # all sort last rather than raising on a None comparison.
    matched.sort(key=lambda r: (r["sort_date"] is not None, r["sort_date"] or _EPOCH), reverse=True)

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
        "needs_review_count": sum(1 for r in shaped if r["needs_review"]),
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
                    "needs_review": bool(needs_review)},
        # Pre-built so the template never has to concatenate a query string by hand (and never has
        # to remember to url-encode a client's name in a Related To value).
        "query_keep": _query_keep(q, year, type_code, related, needs_review),
        "query_keep_no_review": _query_keep(q, year, type_code, related, False),
        "page_numbers": _page_numbers(page, pages),
    }


def _query_keep(q, year, type_code, related, needs_review) -> str:
    """The active filters as a `&`-prefixed query fragment, for links that change ONE other thing
    (a category tab, a page) and must preserve everything else. Empty when nothing is filtered."""
    pairs = [(k, v) for k, v in (("dq", q), ("dyear", year), ("dtype", type_code),
                                 ("related", related)) if v]
    if needs_review:
        pairs.append(("review", "1"))
    return ("&" + urlencode(pairs)) if pairs else ""


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
