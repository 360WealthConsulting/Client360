"""READ-ONLY content-based owner proposal for genuinely-unassigned documents.

Extracts identity evidence from a document's ACTUAL content, matches it against existing canonical
people / households / businesses, and returns a ranked proposed owner with a confidence bucket and the
supporting evidence. It proposes only — it never assigns, never writes, and never modifies a source file.

Extraction by type (bounded; reuses existing infrastructure, no OCR engine required):
  * Excel (.xlsx/.xlsm): bounded openpyxl cell text.
  * PDF: native text layer via pypdf (no OCR of a text PDF); if the PDF has no text layer, fall back to
    any cached OCR text in the document_ocr table.
  * Images (incl. HEIC): cached OCR text from document_ocr if present; otherwise no content text
    (filename/folder are supporting evidence only) -> stays manual.
  * Plain text (.txt/.csv/.md/.log): read directly.
  * Anything else: cached OCR text if present, else unsupported (fails safe -> manual).

Safety: analyses only documents with person_id AND household_id AND organization_id all NULL. The six
permanent V2 rejects are never proposed as assignable. Employer / payor / institution names are treated
as CONTEXT only and can never be the proposed owner (Liberty University / Wells Fargo protection).
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select

from app.db import (
    documents,
    engine,
    households,
    metadata,
    people,
    person_source_links,
    relationship_entities,
    source_contacts,
)

document_sources = metadata.tables["document_sources"]
#: Resolved through metadata (like document_sources) so a deployment without the table still imports.
users_table = metadata.tables.get("users")

PERMANENT_REJECT_DOCUMENT_IDS = frozenset({4704, 4716, 4717, 17932, 22336, 22338})

_MAX_TEXT_CHARS = 20000
_MAX_PDF_PAGES = 15
_MAX_EXCEL_ROWS = 120

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)")
# Capitalised / ALL-CAPS name-like phrases of 2-3 tokens (e.g. "Mary Hardy", "JENKINS RANDALL L").
# A token may be a single letter so a middle initial ("MARY A HARDY") does not break the phrase.
_NAME_RE = re.compile(r"\b([A-Z][A-Za-z'’.\-]*(?:\s+[A-Z][A-Za-z'’.\-]*){1,2})\b")
_INST_KW = ("university", "college", "bank", "credit union", "insurance", "mortgage", "irs",
            "internal revenue", "wells fargo", "liberty university", "fidelity", "vanguard",
            "navient", "nelnet", "department of", "state of")

#: NO NAME LIST DECIDES OWNERSHIP.
#:
#: An earlier revision carried a ``_COUNTERPARTY_NAMES`` blacklist. It is gone, and deliberately so:
#: it collided with a real human client — person 5583 is named "Edward Jones", is a Wealthbox
#: ``type=Person`` with first_name/last_name populated, and files 1040s with the firm as taxpayer —
#: while still missing every institution whose name looks ordinary ("Sterling Meridian Partners").
#: A brand list is the wrong shape for this problem in both directions.
#:
#: Ownership is now decided POSITIVELY: a candidate may reach HIGH only if the record can be shown to
#: be an eligible client owner from authoritative deployed evidence (see ``_mark_owner_eligibility``).
#: Authoritative positive client evidence beats any name heuristic. ``_INST_KW`` survives for its
#: ORIGINAL job only — annotating institution mentions in document text as context — and no longer
#: decides whether anyone may own a document.

#: A ZIP is shared by everyone in a town — production has one ZIP spanning 98 distinct people — so a
#: ZIP match is corroboration of PLACE, never of identity. Street matches are kept separate and do
#: corroborate. Anything at or above this many distinct people makes a value non-identifying.
_SHARED_VALUE_MIN_PEOPLE = 2

#: Minimum normalized-name length before a canonical entity name may be matched INSIDE a SharePoint
#: library root. Equality is always allowed; containment needs this much name so a short token cannot
#: sweep an unrelated business into the firm's own records.
_MIN_FIRM_ROOT_MATCH = 8

#: Most distinct source contacts a mail domain may carry and still be read as the practice's OWN
#: domain. Above this it is a public provider or an employer domain, and treating it as the firm's
#: would suppress every organization that shares it. See ``_firm_mail_domains`` for the production
#: measurements that place this threshold.
_MAX_FIRM_DOMAIN_HOLDERS = 25


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _phone10(s):
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else ""


def _ext(name):
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


_ZIP_RE = re.compile(r"(?<!\d)(\d{5})(?:-\d{4})?(?!\d)")
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\- ]{2,40}?\b(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|"
    r"boulevard|blvd|court|ct|way|place|pl|circle|cir|highway|hwy|parkway|pkwy|terrace|ter|"
    r"trail|trl)\b", re.IGNORECASE)
_SSN_RE = re.compile(r"\b\d{3}[-\s]\d{2}[-\s](\d{4})\b")          # captures LAST FOUR only
# 'Last, First' in ANY case (PDF extraction often lowercases names).
_LASTFIRST_RE = re.compile(r"\b([A-Za-z][A-Za-z'’\-]+),\s+([A-Za-z][A-Za-z'’\-]+)\b")
# Split digit<->letter runs so PDF gluing like "2022mary" becomes "2022 mary".
_DIGIT_ALPHA_RE = re.compile(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)")
# Tax/document labels that precede a taxpayer/recipient name — used as corroborating EVIDENCE only,
# never a hard-coded owner rule and never a scoring change.
_NAME_LABELS = frozenset({"dear", "recipient", "taxpayer", "employee", "applicant", "insured",
                          "policyholder", "holder", "covered", "individual", "primary", "borrower",
                          "member", "client", "name", "spouse", "for"})

# Deterministic evidence weights (document CONTENT only; folder/filename are never scored).
# ``zip`` is split out of the old ``address`` signal and scored far lower: matching a town's ZIP is
# not evidence of who owns a document. ``street`` is the part of an address that identifies.
_POINTS = {"email": 100, "phone": 90, "street": 60, "name": 40, "zip": 10}

#: Signals that can corroborate a named candidate up to HIGH — but only when the matched VALUE is not
#: shared across people. ``zip`` is deliberately absent.
_OWNER_SPECIFIC = frozenset({"email", "phone", "street"})


def _valid_nanp(d):
    """A 10-digit run is a plausible US phone only if the area code and exchange start 2-9 (NANP). This
    keeps arbitrary numeric form/application IDs from being read as phone numbers, without touching how
    canonical phones are indexed (real phones still pass)."""
    return len(d) == 10 and d[0] in "23456789" and d[3] in "23456789"


def _content_name_candidates(text):
    """Generate (full_names, first_last_pairs, labeled_pairs) from the WHOLE content, CASE-INSENSITIVELY.

    Names in a document need NOT be capitalised — PDF text extraction frequently lowercases taxpayer
    names ("dear mary hardy"). We tokenise the normalised content on token boundaries and emit 2-3 token
    windows plus 'Last, First' forms; the caller looks each window up in the canonical name / (first,last)
    indexes, so ONLY real canonical names become candidates (generic boilerplate like "affordable care"
    matches nothing). Bounded by the already-capped text length. `labeled_pairs` are (first,last) windows
    immediately preceded by a tax/document label word (Dear/Recipient/Taxpayer/...), used as evidence."""
    toks = _norm(_DIGIT_ALPHA_RE.sub(" ", text)).split()
    full, first_last, labeled = set(), set(), set()
    n = len(toks)
    for i in range(n - 1):
        a, b = toks[i], toks[i + 1]
        full.add(f"{a} {b}")
        first_last.add((a, b))
        if i > 0 and toks[i - 1] in _NAME_LABELS:
            labeled.add((a, b))
        if i + 2 < n:
            c = toks[i + 2]
            full.add(f"{a} {b} {c}")
            first_last.add((a, c))              # first + last with a middle name/initial between
            if i > 0 and toks[i - 1] in _NAME_LABELS:
                labeled.add((a, c))
    for m in _LASTFIRST_RE.finditer(text):
        first_last.add((m.group(2).lower(), m.group(1).lower()))
    return full, first_last, labeled


def _zip5(s):
    d = re.sub(r"\D", "", s or "")
    return d[:5] if len(d) >= 5 else ""


def _placeholder_name(full):
    """A canonical name is placeholder-quality when it is blank or every token is a single character
    (e.g. 'A B', 'T T', 'C D', 'D F'). Real short names ('Al Vo', 'Ed Ng', 'Bo Li') have 2+ char tokens
    and are NOT flagged, so legitimate short names are never excluded."""
    toks = _norm(full).split()
    return (not toks) or all(len(t) == 1 for t in toks)


# Native-extracted text shorter than this on an OCR-capable document triggers the OCR fallback.
_MIN_NATIVE_CHARS = 20


# --- text extraction ---------------------------------------------------------------------------

def _ocr_cache_text(conn, document_id):
    from app.db import document_ocr
    try:
        return conn.execute(select(document_ocr.c.text).where(
            document_ocr.c.document_id == document_id).order_by(document_ocr.c.id.desc()).limit(1)).scalar() or ""
    except Exception:  # noqa: BLE001
        return ""


def _pdf_text(path):
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        out = []
        for page in reader.pages[:_MAX_PDF_PAGES]:
            out.append(page.extract_text() or "")
            if sum(len(x) for x in out) >= _MAX_TEXT_CHARS:
                break
        return "\n".join(out)
    except Exception:  # noqa: BLE001
        return ""


def _excel_text(path):
    try:
        from app.services.workbook_preview import PREVIEW_MAX_COLS, read_workbook_preview
        r = read_workbook_preview(
            path,
            max_rows=_MAX_EXCEL_ROWS,
            max_cols=PREVIEW_MAX_COLS,
        )
        if r.get("error"):
            return ""
        parts = []
        for row in r.get("rows", [])[:_MAX_EXCEL_ROWS]:
            parts.append(" ".join(str(c) for c in row if c))
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


_DOCX_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(path):
    """Native text from a .docx (Office Open XML) using the standard library only (zipfile + XML) — no new
    dependency. Returns the paragraph text, or "" on a corrupt/unreadable file (fails safe)."""
    import zipfile
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(str(path)) as z:
            data = z.read("word/document.xml")
        root = ET.fromstring(data)
    except Exception:  # noqa: BLE001 — corrupt zip / missing part / bad XML
        return ""
    paras = []
    for p in root.iter(f"{_DOCX_W}p"):
        run = "".join(t.text for t in p.iter(f"{_DOCX_W}t") if t.text)
        if run.strip():
            paras.append(run)
    return "\n".join(paras)


def _ics_text(path):
    """Human/identity-bearing fields from an iCalendar (.ics): SUMMARY, DESCRIPTION, LOCATION, and the
    ORGANIZER/ATTENDEE names (CN=) + emails (mailto:). Standard library only; "" on failure."""
    try:
        raw = path.read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for line in raw.splitlines():                          # RFC-5545 line unfolding
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    out = []
    for line in lines:
        if ":" not in line:
            continue
        prop, val = line.split(":", 1)
        name = prop.split(";", 1)[0].upper()
        if name in ("SUMMARY", "DESCRIPTION", "LOCATION", "ORGANIZER", "ATTENDEE", "CONTACT", "COMMENT"):
            out.append(val.replace("mailto:", " ").strip())
            m = re.search(r"CN=([^;:]+)", prop)
            if m:
                out.append(m.group(1).strip())
    return "\n".join(out)


def _html_to_text(html_str):
    import html as _html
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_str)
    s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return _html.unescape(s)


def _eml_text(path):
    """From/To/Cc/Subject/Date + the plain-text (or HTML-converted) body + attachment FILENAMES from a
    .eml (RFC-822) message. Standard library only; "" on failure. Attachment CONTENTS are not parsed."""
    import email
    from email import policy
    try:
        with open(str(path), "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=policy.default)
    except Exception:  # noqa: BLE001 — malformed message
        return ""
    parts = [f"{h}: {msg.get(h)}" for h in ("From", "To", "Cc", "Subject", "Date") if msg.get(h)]
    body = ""
    try:
        b = msg.get_body(preferencelist=("plain", "html"))
        if b is not None:
            content = b.get_content()
            body = _html_to_text(content) if b.get_content_type() == "text/html" else content
    except Exception:  # noqa: BLE001
        body = ""
    if body and body.strip():
        parts.append(body.strip())
    try:
        atts = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    except Exception:  # noqa: BLE001
        atts = []
    if atts:
        parts.append("Attachments: " + ", ".join(atts))
    return "\n".join(parts)[:_MAX_TEXT_CHARS]


def _xls_text(path):
    """Legacy .xls (BIFF/OLE2) workbook cell text via xlrd, IF xlrd is installed. Guarded: without xlrd,
    or on a non-xls / corrupt file, returns "" (stays unsupported) — the smallest safe legacy-Excel
    support. xlrd 2.x reads ONLY .xls (never .xlsx), so it cannot collide with the openpyxl .xlsx path."""
    try:
        import xlrd
    except ImportError:
        return ""                                          # dependency not installed -> fail safe
    try:
        book = xlrd.open_workbook(str(path))
    except Exception:  # noqa: BLE001 — not a genuine .xls / corrupt
        return ""
    parts = []
    for sheet in book.sheets():
        parts.append(str(sheet.name))
        for r in range(min(sheet.nrows, _MAX_EXCEL_ROWS)):
            vals = [str(sheet.cell_value(r, c)) for c in range(sheet.ncols)
                    if str(sheet.cell_value(r, c)).strip()]
            if vals:
                parts.append(" ".join(vals))
        if sum(len(p) for p in parts) >= _MAX_TEXT_CHARS:
            break
    return "\n".join(parts)[:_MAX_TEXT_CHARS]


def _live_ocr(conn, document_id):
    """Trigger the EXISTING production OCR backend for one document (populates the document_ocr cache),
    then return its text. Reuses app.services.document_ocr.run_ocr + ocr_backend — it does NOT introduce a
    second OCR/matching engine. Fails safe to manual review (returns "") so ingestion is never blocked,
    but ALWAYS records a truthful OCR state: if the engine/libraries are unavailable the document is left
    in a retryable 'failed (OCR backend unavailable)' state — NOT silently stateless (which previously made
    scanned/image documents look like they had 'no usable native text')."""
    from app.services.document_ocr import live_ocr_observer, record_ocr_unavailable, run_ocr
    try:
        from app.services.ocr_backend import build_production_extractor
        extractor = build_production_extractor()          # raises OcrBackendUnavailable if not installed
    except Exception as exc:  # noqa: BLE001 — backend/libs/config missing: record state, don't drop it
        record_ocr_unavailable(document_id, str(exc))
        return ""
    # Route this single-document re-OCR through the SAME hardened subprocess isolation the operational
    # runner uses (commit 186badf): a pathological/hanging document is killed by the hard wall-clock
    # timeout and control returns here, so the SharePoint ``_ocr_documents`` loop proceeds to the next
    # document instead of freezing the whole baseline. Reuses the runner's production factory ref and its
    # OCR_SUBPROCESS_ISOLATION check — no second config mechanism, no duplicated dotted string. Queue,
    # scope, cache, and reprocess semantics are UNCHANGED: still exactly ONE document, mode='reprocess',
    # batch_size=1. When isolation is intentionally disabled the in-process path is preserved verbatim.
    from app.jobs.ocr_runner import _PRODUCTION_FACTORY, _isolation_enabled
    isolate = _isolation_enabled()
    # When the SharePoint baseline loop is active it publishes a heartbeat observer here; forwarding it to
    # run_ocr keeps the baseline status heartbeat alive DURING a long isolated OCR document (reusing the
    # existing observer → run_ocr → subprocess-isolation on_heartbeat plumbing). None for every other caller.
    observer = live_ocr_observer.get()
    try:
        run_ocr(document_ids=[document_id], extractor=extractor, mode="reprocess", batch_size=1,
                isolate=isolate, factory_ref=(_PRODUCTION_FACTORY if isolate else None),
                observer=observer)
    except Exception:  # noqa: BLE001 — a per-run failure is already recorded as state by run_ocr/_ocr_one
        return ""
    return _ocr_cache_text(conn, document_id)


def extract_document_text(conn, row, path, *, ocr=False):
    """Return (text, method). Bounded and read-only w.r.t. documents/ownership. `row` is a documents
    mapping; `path` a Path/None. Strategy: native extraction first (Excel cells, PDF text layer,
    plaintext); if that yields no adequate text on an OCR-capable document and ``ocr=True``, fall back to
    the existing OCR backend (cache first, then a live OCR run) and use that text. ``method`` is one of
    excel / pdf_text / plaintext / ocr / ocr_cache / pdf_no_text / image_no_text / unsupported / none."""
    ext = _ext(row["original_name"])
    text, method = "", "none"

    def _ocr_or_cache(fail_method):
        cached = _ocr_cache_text(conn, row["id"])
        if cached.strip():
            return cached, "ocr_cache"
        if ocr:
            live = _live_ocr(conn, row["id"])
            if live.strip():
                return live, "ocr"
        return "", fail_method

    if ext in {"xlsx", "xlsm"} and path is not None and path.exists():
        text, method = _excel_text(path), "excel"
    elif ext == "docx" and path is not None and path.exists():
        text = _docx_text(path)
        method = "docx" if text.strip() else "unsupported"
    elif ext == "ics" and path is not None and path.exists():
        text = _ics_text(path)
        method = "ics" if text.strip() else "unsupported"
    elif ext == "eml" and path is not None and path.exists():
        text = _eml_text(path)
        method = "eml" if text.strip() else "unsupported"
    elif ext == "xls" and path is not None and path.exists():
        text = _xls_text(path)
        method = "xls" if text.strip() else "unsupported"
    elif ext == "pdf" and path is not None and path.exists():
        text = _pdf_text(path)
        if len(text.strip()) >= _MIN_NATIVE_CHARS:
            method = "pdf_text"                            # good native text layer — do NOT OCR needlessly
        else:
            text, method = _ocr_or_cache("pdf_no_text")    # image-only/scanned PDF → OCR fallback
    elif ext in {"txt", "csv", "md", "log"} and path is not None and path.exists():
        try:
            text, method = path.read_text(errors="replace")[:_MAX_TEXT_CHARS], "plaintext"
        except Exception:  # noqa: BLE001
            text, method = "", "none"
    elif ext in {"png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "heic", "heif"}:
        text, method = _ocr_or_cache("image_no_text")
    else:
        text, method = _ocr_or_cache("unsupported")
    return (text or "")[:_MAX_TEXT_CHARS], method


# --- canonical match indexes -------------------------------------------------------------------

def build_match_indexes(conn):
    """Build read-only lookup indexes from existing canonical data (people/households/businesses):
    exact email/phone/full-name, a (first, last) name index (to match names carrying a middle name or
    written 'Last, First'), and each person's ZIPs/streets for address corroboration."""
    idx = {"email": {}, "phone": {}, "name": {}, "first_last": {}, "pid": {}, "members": {},
           "hh_name": {}, "biz": {}, "inst": set()}
    pc = people.c
    sel = [pc.id, pc.full_name]
    for name in ("primary_email", "normalized_email", "primary_phone", "normalized_phone",
                 "household_id", "address_line_1", "city", "postal_code"):
        if name in pc:
            sel.append(pc[name])
    for r in conn.execute(select(*sel)).mappings():
        pid, full = r["id"], r["full_name"]
        zips = {z for z in (_zip5(r.get("postal_code")),) if z}
        streets = {s for s in (_norm(r.get("address_line_1")),) if s}
        idx["pid"][pid] = {"name": full, "email": r.get("primary_email"),
                           "phone": r.get("primary_phone"), "household_id": r.get("household_id"),
                           "zips": zips, "streets": streets}
        nfull = _norm(full)
        if not _placeholder_name(full):
            # Placeholder canonical records ("A B", "T T", "C D") must not produce an owner from NAME
            # alone. They stay in the email/phone indexes below — a real identifier match is legitimate
            # regardless of name quality — but are excluded from the name / first-last indexes.
            idx["name"].setdefault(nfull, []).append(pid)
            toks = nfull.split()
            if len(toks) >= 2:
                idx["first_last"].setdefault((toks[0], toks[-1]), []).append(pid)
        for e in (r.get("primary_email"), r.get("normalized_email")):
            e = (e or "").strip().lower()
            if e and "@" in e:
                idx["email"].setdefault(e, set()).add(pid)
        for ph in (_phone10(r.get("primary_phone")), _phone10(r.get("normalized_phone"))):
            if ph:
                idx["phone"].setdefault(ph, set()).add(pid)
        if r.get("household_id") is not None:
            idx["members"].setdefault(r["household_id"], set()).add(pid)
    _augment_from_source_contacts(conn, idx)
    _mark_shared_values(idx)
    _mark_owner_eligibility(conn, idx)
    for h in conn.execute(select(households.c.id, households.c.name)).mappings():
        idx["hh_name"][h["id"]] = h["name"]
    for e in conn.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                 relationship_entities.c.entity_type)).mappings():
        t = (e["entity_type"] or "").lower()
        if any(k in t for k in ("instit", "payor", "payer", "employer", "bank", "gov", "school", "insur")):
            idx["inst"].add(_norm(e["name"]))
        else:
            idx["biz"][_norm(e["name"])] = (e["id"], e["name"])
    _mark_org_eligibility(conn, idx)
    return idx


def _mark_shared_values(idx):
    """Derive, from the canonical data itself, which evidence values cannot identify an owner.

    ``shared`` — an email / phone / street / ZIP held by two or more DISTINCT people. A firm's office
    number on letterhead, a shared household line, a practice address: none of these say WHOSE
    document this is, so they may corroborate context but never establish ownership. This is computed
    from the indexes rather than configured, so it needs no maintenance and no per-person exception.

    An earlier revision also derived a ``contactless`` set here and capped those candidates below
    HIGH, on the theory that a bare-name row with no contact details is probably an institution. That
    was a negative heuristic and it is gone — it was wrong in both directions. 1,103 production people
    carry no contact details and most are real clients with sparse records, while "Edward Jones" the
    client (person 5583, three 1040s as taxpayer) was caught by it. Eligibility is now decided
    positively in ``_mark_owner_eligibility``, which admits the sparse clients on their Drake or
    household evidence and never consults a name.
    """
    # Email and phone are counted from their own INDEXES, which are the authoritative view: they
    # already fold in normalized_* columns and everything _augment_from_source_contacts added, so a
    # value reachable by several people is visible here even when each person's own row shows one.
    counts = {"street": {}, "zip": {}}
    for pid, info in idx["pid"].items():
        for s in info.get("streets", ()):
            counts["street"].setdefault(s, set()).add(pid)
        for z in info.get("zips", ()):
            counts["zip"].setdefault(z, set()).add(pid)
    idx["shared"] = {kind: {v for v, pids in m.items() if len(pids) >= _SHARED_VALUE_MIN_PEOPLE}
                     for kind, m in counts.items()}
    for kind in ("email", "phone"):
        idx["shared"][kind] = {v for v, pids in (idx.get(kind) or {}).items()
                               if len(pids) >= _SHARED_VALUE_MIN_PEOPLE}


def _mark_owner_eligibility(conn, idx):
    """Which canonical people may own a document — decided POSITIVELY, never by name.

    A record is owner-eligible only on evidence that a HUMAN AT THE FIRM created deliberately, and
    that says "client" rather than merely "exists":

      * a Drake return role of ``taxpayer`` or ``spouse`` — the person is ON a filed return, and for
        an organization the return type (1065 / 1120S) says it is a client entity;
      * a CRM (Wealthbox) ``contact_type`` beginning "Client" — an explicit lifecycle stage;
      * the canonical ``people.contact_type`` beginning "Client" — the field the planned backfill
        populates. Reading it here means that backfill takes effect with no further code change.

    THREE candidate signals were tested against production and REJECTED:

    ``existing document ownership`` — circular and unprovable. Exactly ONE of 29,896 owned documents
    carries an ownership-resolution audit event, so for the rest there is no record of who decided
    the linkage or why. Of the 213 people this signal alone would have admitted, 124 rest on importer
    linkage with no folder anchor recorded and 23 on unknown/legacy links. Letting those bootstrap
    future HIGH proposals would let a past mistake authorise its own repetition.

    ``household membership`` — disproven, not merely unproven. Of the 122 people it alone would
    admit, 113 sit in households containing NO member with any client evidence at all, and 5 are
    explicitly CRM prospects. A household groups related people; it does not assert that they are
    clients. Members who ARE clients carry Drake or CRM evidence anyway (456 of 596 do).

    ``Wealthbox type=Person`` — says a row is a human, not that they are a client. It is what
    inflated an earlier census to 7,611 "client persons" while 3,398 of those were CRM prospects.

    Everything without positive evidence is simply NOT eligible — employers and institutions captured
    from the CRM as related organizations (Carilion, XPO, Wells Fargo, Liberty University) never
    qualify, and nobody had to name them. Not-eligible is an absence of proof, NOT an assertion that
    the record is a counterparty. Firm staff are excluded separately: an internal identity is not a
    client owner.

    Deliberately NOT evidence: institution-like or business-like names, phone, ZIP, address, email
    domain, or Wealthbox ``type=Organization`` on its own — a legitimate client business has exactly
    that shape.
    """
    eligible, staff = set(), set()
    firm_domains = set()
    if users_table is not None:
        try:
            firm_domains = {e.split("@", 1)[1].lower()
                            for (e,) in conn.execute(select(users_table.c.email))
                            if e and "@" in e and "example" not in e.lower()}
        except Exception:  # noqa: BLE001 — users table shape is deployment-dependent
            firm_domains = set()

    # Canonical contact_type is the only per-person column consulted. Household membership and
    # existing document ownership are deliberately absent — see the docstring for the production
    # measurements that rejected them.
    for pid, ct, em, nem in conn.execute(select(people.c.id, people.c.contact_type,
                                                people.c.primary_email, people.c.normalized_email)):
        if pid not in idx["pid"]:
            continue
        if str(ct or "").strip().lower().startswith("client"):
            eligible.add(pid)
        for e in (em, nem):
            e = (e or "").strip().lower()
            if e and "@" in e and e.split("@", 1)[1] in firm_domains:
                staff.add(pid)

    sc = source_contacts.c
    j = person_source_links.join(source_contacts, person_source_links.c.source_contact_id == sc.id)
    for r in conn.execute(select(person_source_links.c.person_id, sc.source_system, sc.email,
                                 sc.normalized_email, sc.raw_data).select_from(j)).mappings():
        pid = r["person_id"]
        if pid not in idx["pid"]:
            continue
        raw = r["raw_data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = {}
        raw = raw if isinstance(raw, dict) else {}
        if str(raw.get("role") or "").strip().lower() in ("taxpayer", "spouse"):
            eligible.add(pid)
        if str(raw.get("contact_type") or "").strip().lower().startswith("client"):
            eligible.add(pid)
        for e in (r["email"], r["normalized_email"]):
            e = (e or "").strip().lower()
            if e and "@" in e and e.split("@", 1)[1] in firm_domains:
                staff.add(pid)

    idx["staff"] = staff
    idx["owner_eligible"] = eligible - staff


def _firm_mail_domains(conn) -> set:
    """Mail domains that authoritatively belong to the PRACTICE.

    "A domain some ``users`` row happens to use" is not sufficient, and treating it as sufficient is
    a live suppression hazard: a single staff account registered on a public provider would mark
    every organization sharing that provider as the firm itself and bar it from ever owning a
    document. Measured against production, one ``users`` row on gmail.com would have suppressed 22
    organizations; on yahoo.com, 4.

    So a candidate domain must also be NARROWLY HELD — a practice domain appears on a handful of
    records, a public provider appears across the client base. Production separates the two cleanly:

        360wealthconsulting.com (the firm)        4 distinct source contacts
        msn.com   (smallest public provider seen) 45
        liberty.edu (an employer domain)         101
        yahoo.com                                683
        gmail.com                              3,025

    ``_MAX_FIRM_DOMAIN_HOLDERS`` sits in the gap with an order of magnitude of headroom on each
    side, so the rule needs no provider blacklist and no maintenance. A domain that outgrows the
    threshold simply stops being treated as firm-exclusive, which fails OPEN into ordinary
    eligibility rather than into silent suppression.
    """
    if users_table is None:
        return set()
    try:
        candidates = {e.split("@", 1)[1].lower()
                      for (e,) in conn.execute(select(users_table.c.email))
                      if e and "@" in e and "example" not in e.lower()}
    except Exception:  # noqa: BLE001 — users table shape is deployment-dependent
        return set()
    if not candidates:
        return set()

    holders: dict[str, set] = {}
    for cid, em, nem in conn.execute(select(source_contacts.c.id, source_contacts.c.email,
                                            source_contacts.c.normalized_email)):
        for e in (em, nem):
            e = (e or "").strip().lower()
            if e and "@" in e:
                d = e.split("@", 1)[1]
                if d in candidates:
                    holders.setdefault(d, set()).add(cid)
    return {d for d in candidates
            if len(holders.get(d, ())) <= _MAX_FIRM_DOMAIN_HOLDERS}


def _org_contacts(conn, idx):
    """Map each canonical business/organization entity to the source_contacts that evidence it.

    Two links, because production carries both shapes:

    * ``relationship_entities.details.source_contact_ids`` — written by the canonical repair /
      population runs for entities built from structured provenance;
    * an exact normalized NAME match against ``source_contacts.full_name`` — the fallback for
      entities created by later remediation passes, which recorded no contact ids at all.

    The name fallback is deliberately included even though it also reaches the FIRM's own entities.
    Suppressing the firm is the job of ``_mark_org_eligibility``'s firm detection, not of hiding the
    firm's evidence: an entity whose eligibility we cannot see is an entity we cannot reason about.
    """
    by_name: dict[str, list] = {}
    rows = {}
    for r in conn.execute(select(source_contacts.c.id, source_contacts.c.source_system,
                                 source_contacts.c.full_name, source_contacts.c.email,
                                 source_contacts.c.normalized_email, source_contacts.c.phone,
                                 source_contacts.c.normalized_phone,
                                 source_contacts.c.postal_code,
                                 source_contacts.c.raw_data)).mappings():
        rows[r["id"]] = r
        n = _norm(r["full_name"])
        if n:
            by_name.setdefault(n, []).append(r)

    out: dict[int, list] = {}
    for e in conn.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                 relationship_entities.c.details)).mappings():
        details = e["details"]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                details = {}
        details = details if isinstance(details, dict) else {}
        found = []
        for cid in (details.get("source_contact_ids") or []):
            try:
                r = rows.get(int(cid))
            except (TypeError, ValueError):
                r = None
            if r is not None:
                found.append(r)
        if not found:
            found = list(by_name.get(_norm(e["name"]), ()))
        out[e["id"]] = found
    return out


def _mark_org_eligibility(conn, idx):
    """Which canonical ORGANIZATIONS may own a document, and which are the firm's own records.

    This is the organization counterpart of :func:`_mark_owner_eligibility`, built to the same
    fail-closed standard. Before it existed the organization branch of :func:`analyze_identity`
    emitted HIGH on nothing more than "this business name appears in the text, and SOME address or
    ZIP appears somewhere in the text" — no eligibility test, no name-uniqueness test, and no
    requirement that the corroborating identifier belong to the proposed business.

    The production audit that motivated this found the accounting firm's own entity attracting 544
    client documents across 108 DIFFERENT client folders: 1099s and payroll summaries for the firm's
    clients, matched because the firm's legal name and address sit on the letterhead as PREPARER.
    That is the same letterhead failure the person path already fixed for the office phone.

    Four index products, all derived from deployed data — no entity id, firm name or brand list
    appears anywhere in this module:

    ``org_eligible``   the business is ON a filed return as ``taxpayer``/``spouse`` (Drake), or a CRM
                       record marks it ``contact_type`` "Client*". Existence in
                       ``relationship_entities`` proves only that somebody recorded the name.
    ``firm_entities``  the firm's OWN records. Detected two ways, both authoritative: the entity's
                       contact email sits on a domain that a ``users`` row also uses (the firm's own
                       mail domain), or the entity's name matches a SharePoint LIBRARY ROOT — the
                       top-level folders of the document library are the practice's own, never a
                       client's. A firm entity is never owner-eligible, even though the firm does
                       file its own returns and therefore does carry taxpayer-role evidence.
    ``org_ident``      the identifiers that actually BELONG to each business, so corroboration can be
                       checked against the proposed entity instead of against the whole document.
    ``org_shared``     identifier values reachable from two or more distinct businesses — a practice
                       switchboard, a shared mailbox, a town ZIP. These may never corroborate.
    """
    firm_domains = _firm_mail_domains(conn)

    # The document library's TOP-LEVEL folders are the practice's own containers. Any canonical
    # entity whose name is one of them is the firm itself, not a client.
    firm_roots = set()
    try:
        for (p,) in conn.execute(
                select(document_sources.c.source_path)
                .where(document_sources.c.source_path.like("%root:/%")).distinct()):
            seg = str(p).split("root:/", 1)[1].split("/")[0]
            n = _norm(seg)
            if n:
                firm_roots.add(n)
                # "360 tax solutions llc" as a root must also catch the entity "360 Tax Solutions".
                firm_roots.add(re.sub(r"\b(llc|inc|corp|corporation|pc|pllc|lp|llp|co)\b", "",
                                      n).strip())
    except Exception:  # noqa: BLE001 — provenance shape is deployment-dependent
        firm_roots = set()

    contacts = _org_contacts(conn, idx)
    eligible, firm, ident = set(), set(), {}
    phone_owners: dict[str, set] = {}
    email_owners: dict[str, set] = {}
    zip_owners: dict[str, set] = {}

    for e in conn.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                 relationship_entities.c.entity_type,
                                 relationship_entities.c.active)).mappings():
        eid, nm = e["id"], e["name"]
        norm_name = _norm(nm)
        phones, emails, zips = set(), set(), set()
        is_eligible = False
        for r in contacts.get(eid, ()):
            raw = r["raw_data"]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    raw = {}
            raw = raw if isinstance(raw, dict) else {}
            role = str(raw.get("role") or "").strip().lower()
            ctype = str(raw.get("contact_type") or "").strip().lower()
            if role in ("taxpayer", "spouse") or ctype.startswith("client"):
                is_eligible = True
            for ph in (_phone10(r["phone"]), _phone10(r["normalized_phone"])):
                if ph:
                    phones.add(ph)
            for em in (r["email"], r["normalized_email"]):
                em = (em or "").strip().lower()
                if em and "@" in em:
                    emails.add(em)
                    if em.split("@", 1)[1] in firm_domains:
                        firm.add(eid)
            z = _zip5(r["postal_code"])
            if z:
                zips.add(z)

        # A short token would match a root by accident ("360" inside "360 tax solutions llc"), so
        # containment needs enough name to be meaningful. Equality always counts.
        if norm_name and (norm_name in firm_roots
                          or (len(norm_name) >= _MIN_FIRM_ROOT_MATCH
                              and any(root and norm_name in root for root in firm_roots if root))):
            firm.add(eid)
        if is_eligible and e["active"] is not False:
            eligible.add(eid)
        ident[eid] = {"phones": phones, "emails": emails, "zips": zips}
        for ph in phones:
            phone_owners.setdefault(ph, set()).add(eid)
        for em in emails:
            email_owners.setdefault(em, set()).add(eid)
        for z in zips:
            zip_owners.setdefault(z, set()).add(eid)

    idx["firm_entities"] = firm
    idx["org_eligible"] = eligible - firm
    idx["org_ident"] = ident
    idx["org_shared"] = {
        "phone": {v for v, o in phone_owners.items() if len(o) >= _SHARED_VALUE_MIN_PEOPLE}
                 | {p for p in (idx.get("shared") or {}).get("phone", set())},
        "email": {v for v, o in email_owners.items() if len(o) >= _SHARED_VALUE_MIN_PEOPLE}
                 | {p for p in (idx.get("shared") or {}).get("email", set())},
        # A ZIP is a town. It is never identifying for an organization either.
        "zip": set(zip_owners),
    }
    counts: dict[str, set] = {}
    for e_norm, (bid, _bname) in idx["biz"].items():
        counts.setdefault(e_norm, set()).add(bid)
    idx["org_name_counts"] = {k: len(v) for k, v in counts.items()}


def _org_folder_anchor(folder, name) -> bool:
    """Does the SOURCE FOLDER structurally anchor this document to ``name``?

    Only the CLIENT folder counts, resolved by the importer's own ``client_folder_hint``. That helper
    already fails closed above the client level, so a firm root, a service-line folder, a status
    folder, a bare year and a numeric id all yield no anchor and therefore no boost. A generic
    drop-box segment ("WEB UPLOAD", "Scans") survives as a folder NAME but cannot match a business
    legal name, so it grants nothing here either.
    """
    if not folder or not name:
        return False
    try:
        from app.connectors.microsoft365.sharepoint_content import client_folder_hint
    except Exception:  # noqa: BLE001 — connector optional in some deployments
        return False
    hint = client_folder_hint(str(folder))
    if not hint:
        return False
    a, b = _norm(hint), _norm(name)
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def _phrase_in(phrase: str, normalized: str) -> bool:
    """Whole-word / whole-phrase containment over an ALREADY-normalised name.

    ``_norm`` reduces to lowercase words separated by single spaces, so padding both sides with a
    space makes ``" bank "`` match "todd bank head" but not "bankhead". Substring matching — which
    is what this replaces — flagged seven real clients as institutions: ``irs`` inside *K-irs-ten*
    and *Ha-irs-ton*, ``bank`` inside *Banker*, *Eubank*, *Bankhead*, *Brockbank* and *Eubanks*.
    """
    return f" {phrase} " in f" {normalized} "


def looks_like_institution_name(name) -> bool:
    """ADVISORY ONLY: does this name READ like an institution? It never decides ownership.

    This is context for a human reviewer, not a safety control, and nothing in the confidence path
    consults it — ``test_institution_name_check_does_not_gate_confidence`` pins that. A name cannot be
    the control in either direction: person 5583 is a real 1040-filing client named "Edward Jones",
    while "Sterling Meridian Partners" reads as perfectly ordinary. Whether a record may own a
    document is decided positively in ``_mark_owner_eligibility`` from Drake, household, ownership and
    CRM evidence, and that decision ignores the name entirely.

    Matching is whole-word only. Substring matching — what this replaced — flagged seven real clients
    as institutions: ``irs`` inside *Kirsten* and *Hairston*, and ``bank`` inside *Banker*, *Eubank*,
    *Bankhead*, *Brockbank* and *Eubanks*.
    """
    n = _norm(name)
    if not n:
        return False
    return any(_phrase_in(k, n) for k in _INST_KW)


def _augment_from_source_contacts(conn, idx):
    """Union each canonical person's LINKED source-contact emails/phones/addresses into the match index.

    In this data model the canonical `people` contact columns are largely NULL — the real emails, phones
    and addresses were captured on `source_contacts` and connected through `person_source_links` (the
    MDM canonical-field backfill is a separate, deferred task). Reading only the canonical columns left
    the strongest disambiguating signals — email / phone / address — blind for almost the whole
    population, so proposals collapsed to name-only. This is a READ-ONLY index enrichment: it writes no
    canonical data, is source-agnostic (every linked source_system contributes equally), and only
    enriches people already present in the canonical index (it never invents a new owner)."""
    sc = source_contacts.c
    j = person_source_links.join(source_contacts, person_source_links.c.source_contact_id == sc.id)
    cols = [person_source_links.c.person_id, sc.email, sc.normalized_email, sc.phone,
            sc.normalized_phone, sc.address_line_1, sc.postal_code, sc.raw_data]
    for r in conn.execute(select(*cols).select_from(j)).mappings():
        pid = r["person_id"]
        info = idx["pid"].get(pid)
        if info is None:
            continue                                   # only enrich known canonical people
        raw = r["raw_data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = {}
        raw = raw if isinstance(raw, dict) else {}
        for e in (r["email"], r["normalized_email"], raw.get("home_email"), raw.get("work_email")):
            e = (e or "").strip().lower()
            if e and "@" in e:
                idx["email"].setdefault(e, set()).add(pid)
                if not info.get("email"):
                    info["email"] = e
        for ph in (_phone10(r["phone"]), _phone10(r["normalized_phone"]),
                   _phone10(raw.get("home_phone")), _phone10(raw.get("work_phone"))):
            if ph:
                idx["phone"].setdefault(ph, set()).add(pid)
                if not info.get("phone"):
                    info["phone"] = ph
        z = _zip5(r["postal_code"])
        if z:
            info["zips"].add(z)
        st = _norm(r["address_line_1"])
        if st:
            info["streets"].add(st)


# --- analysis ----------------------------------------------------------------------------------

def _confidence(sigs, unique_name, *, tax_document=False, shared=frozenset(), owner_eligible=False):
    """Deterministic tier for one person candidate from its content signal set.

    HIGH REQUIRES OWNER-POSITIVE IDENTITY: the person's NAME in the document, corroborated by a value
    that belongs to them and to nobody else. Contact data on its own does not establish ownership, no
    matter how strong the identifier feels — a document carrying an office phone and a town ZIP names
    nobody. That single rule is what previously allowed one person to collect 2,157 HIGH proposals,
    1,707 of them from ``address/ZIP + phone`` with no name match at all and 160 from phone alone.

    ``shared``        signal names whose matched VALUE is held by two or more people (see
                       ``_mark_shared_values``). They are stripped before corroboration, so a
                       shared office line corroborates nothing.
    ``owner_eligible`` the candidate was PROVEN to be a client owner by ``_mark_owner_eligibility``
                       (Drake taxpayer/spouse, household membership, existing document ownership, or a
                       CRM "Client-*" contact_type). Without that proof HIGH is unreachable, whatever
                       the document says. This is what keeps the 50 employer/institution organizations
                       — and every other unclassified record — out of HIGH without naming any of them.
    """
    corroborating = (sigs & _OWNER_SPECIFIC) - set(shared)
    has_name = "name" in sigs

    if not has_name:
        # No name in the document: contact evidence is a lead, never a conclusion.
        return "MEDIUM" if corroborating else "LOW"
    if not owner_eligible:
        # Nothing in the deployed data shows this record is a client whose documents we file, so it
        # cannot be a CONFIDENT owner and HIGH is off the table. It is NOT buried, though: the row is
        # still surfaced at the ordinary name-only tier for a human to judge, which is exactly what a
        # real-but-unclassified client needs. Eligibility is proven, never assumed — and never denied
        # on the strength of a name.
        return "MEDIUM" if (corroborating or unique_name) else "LOW"
    if tax_document:
        # Tax returns carry taxpayer, spouse, preparer, ERO, representative and signer names, so a
        # name needs an owner-specific identifier before it can be trusted.
        return "HIGH" if corroborating else "LOW"
    if corroborating:
        return "HIGH"
    if unique_name:
        return "MEDIUM"
    return "LOW"


def analyze_identity(text, filename, folder, idx, *, tax_document=False):
    """Pure content analysis: extract identity evidence from `text`, score canonical candidates, and
    return a proposal. Folder/filename are NEVER scored (content wins). Returns proposed_entity_type/id/
    name, confidence (HIGH/MEDIUM/AMBIGUOUS/NO_MATCH), evidence[], competing[], best_candidates[],
    extracted{}."""
    ntext = _norm(text)

    # Tax documents routinely contain preparer / ERO / accounting-firm identity.
    # Treat contact identifiers as corroboration only on these documents.
    # NOTE: a bare "internal revenue service" mention is deliberately NOT a marker. It appears on every
    # IRS-adjacent INFORMATIONAL form (1095-A, 1098, 1099) — documents that carry no preparer, ERO,
    # spouse or signer name, and so none of the role ambiguity this gate exists to guard against. Gating
    # on it suppressed a strong labeled recipient name on exactly those forms. The markers below are
    # specific to an actual tax RETURN or to preparer/ERO identity, which is the real risk.
    tax_markers = (
        "form 1040",
        "form 1040x",
        "form 1120",
        "form 1120s",
        "form 1065",
        "form 1041",
        "paid preparer",
        "preparer s signature",
        "preparer signature",
        "electronic return originator",
        "ero firm name",
        "ptin",
        "taxpayer s pin",
        "taxpayer pin",
    )
    tax_document = tax_document or any(marker in ntext for marker in tax_markers)
    emails = sorted({m.group(0).lower() for m in _EMAIL_RE.finditer(text)})
    phones = sorted({p for p in (_phone10(m.group(0)) for m in _PHONE_RE.finditer(text))
                     if p and _valid_nanp(p)})            # reject numeric form/application IDs
    doc_zips = {m.group(1) for m in _ZIP_RE.finditer(text)}
    doc_streets = {_norm(m.group(0)) for m in _STREET_RE.finditer(text)}
    ssn_last4 = sorted({m.group(1) for m in _SSN_RE.finditer(text)})   # last four only; never full

    # Name candidates: CASE-INSENSITIVE 2-3 token windows over the whole content plus 'Last, First'
    # forms (PDF extraction often lowercases names). Only windows present in the canonical name /
    # (first,last) indexes below become candidates, so boilerplate matches nothing.
    full_names, first_last_pairs, labeled_pairs = _content_name_candidates(text)

    institutions = sorted({nm for nm in full_names if nm in idx["inst"]}
                          | {k for k in _INST_KW if k in ntext})

    signals, name_for, labeled_pids = {}, {}, set()
    #: signal names whose matched VALUE is shared across people, per candidate. Tracked so a shared
    #: office phone or a town ZIP can still be REPORTED as context while never corroborating to HIGH.
    shared_sigs: dict[int, set] = {}
    shared_idx = idx.get("shared") or {}

    def _add(pid, sig, *, value=None, kind=None):
        signals.setdefault(pid, set()).add(sig)
        if value is not None and value in (shared_idx.get(kind or sig) or ()):
            shared_sigs.setdefault(pid, set()).add(sig)

    for e in emails:
        for pid in idx["email"].get(e, ()):
            _add(pid, "email", value=e, kind="email")
    for ph in phones:
        for pid in idx["phone"].get(ph, ()):
            _add(pid, "phone", value=ph, kind="phone")
    name_pid_counts = {}   # for uniqueness: how many people share a matched name
    for nm in full_names:
        if nm in idx["inst"]:
            # ``idx["inst"]`` is STRUCTURAL: names of relationship_entities the firm actually recorded
            # as employers/payors/institutions. A brand blacklist used to be OR-ed in here, and it is
            # what silently erased person 5583 — a real 1040-filing client named "Edward Jones" never
            # received a name signal at all. Only recorded entities suppress a name now.
            continue
        parts = nm.split()
        for pid in idx["name"].get(nm, ()):
            _add(pid, "name")
            name_for[pid] = idx["pid"].get(pid, {}).get("name")
            name_pid_counts[pid] = len(idx["name"].get(nm, []))
            if (parts[0], parts[-1]) in labeled_pairs:
                labeled_pids.add(pid)
    for pair in first_last_pairs:
        if " ".join(pair) in idx["inst"]:
            continue
        for pid in idx["first_last"].get(pair, ()):
            _add(pid, "name")
            name_for[pid] = idx["pid"].get(pid, {}).get("name")
            name_pid_counts.setdefault(pid, len(idx["first_last"].get(pair, [])))
            if pair in labeled_pairs:
                labeled_pids.add(pid)

    # Address corroboration for already-surfaced candidates (never surfaces new candidates alone).
    # ZIP and STREET are recorded separately: a ZIP is a town (production has one spanning 98 people)
    # and cannot identify an owner, whereas a street match can.
    for pid in list(signals):
        info = idx["pid"].get(pid, {})
        for z in (doc_zips & info.get("zips", set())):
            _add(pid, "zip", value=z, kind="zip")
        for s in info.get("streets", set()):
            if s and any(s in ds or ds in s for ds in doc_streets):
                _add(pid, "street", value=s, kind="street")

    # score + evidence
    def _score(sigs):
        return sum(_POINTS[s] for s in sigs)

    def _evidence_for(pid):
        sigs = signals[pid]
        info = idx["pid"].get(pid, {})
        ev = []
        if "name" in sigs:
            label = " (after a taxpayer/recipient label)" if pid in labeled_pids else ""
            ev.append(f"✓ exact name '{info.get('name')}'{label}")
        sh = shared_sigs.get(pid, set())
        note = " (shared value — context only)"
        if "email" in sigs:
            ev.append(f"✓ email {info.get('email')} matched" + (note if "email" in sh else ""))
        if "phone" in sigs:
            ph = _phone10(info.get("phone"))
            base = f"✓ phone ending {ph[-4:]} matched" if ph else "✓ phone matched"
            ev.append(base + (note if "phone" in sh else ""))
        if "street" in sigs:
            ev.append("✓ street address matched" + (note if "street" in sh else ""))
        if "zip" in sigs:
            ev.append("• ZIP matched (a ZIP is a town, not an owner — context only)")
        return ev

    extracted = {"emails": emails[:8], "phones": [f"...{p[-4:]}" for p in phones[:8]],
                 "names": sorted(name_for.values())[:8], "zips": sorted(doc_zips)[:8],
                 "ssn_last4": [f"***-**-{d}" for d in ssn_last4[:4]], "institutions": institutions[:8]}
    result = {"proposed_entity_type": None, "proposed_entity_id": None, "proposed_entity_name": None,
              "confidence": "NO_MATCH", "evidence": [], "competing": [], "best_candidates": [],
              "extracted": extracted}
    if institutions:
        result["evidence"].append("context only (not an owner): " + ", ".join(institutions[:4]))

    # Household (joint): two or more DISTINCT co-household members named -> household (very strong).
    named_pids = {pid for pid, s in signals.items() if "name" in s}
    for hh, mem in idx["members"].items():
        present = mem & named_pids
        if len(present) >= 2:
            names = ", ".join(idx["pid"].get(p, {}).get("name") or f"#{p}" for p in sorted(present))
            result.update({"proposed_entity_type": "household", "proposed_entity_id": hh,
                           "proposed_entity_name": idx["hh_name"].get(hh), "confidence": "HIGH"})
            result["evidence"] = [f"✓ two household members named: {names}"] + result["evidence"]
            result["competing"] = [{"person_id": p, "name": idx["pid"].get(p, {}).get("name")}
                                   for p in sorted(present)]
            return result

    if signals:
        ranked = sorted(signals.items(), key=lambda kv: (-_score(kv[1]), kv[0]))
        top_pid, top_sig = ranked[0]
        top_score = _score(top_sig)
        tied = [pid for pid, s in ranked if _score(s) == top_score]
        unique_name = name_pid_counts.get(top_pid, 2) == 1
        # HIGH is reserved for records positively shown to be client owners. Absence of that proof
        # caps the tier; it never asserts the record is a counterparty.
        owner_eligible = top_pid in (idx.get("owner_eligible") or ())
        conf = _confidence(top_sig, unique_name, tax_document=tax_document,
                           shared=shared_sigs.get(top_pid, frozenset()),
                           owner_eligible=owner_eligible)
        # If the leader has only a (possibly duplicated) name and there is a genuine tie, it is ambiguous.
        if len(tied) > 1 and top_sig == {"name"}:
            result.update({"confidence": "AMBIGUOUS"})
            result["best_candidates"] = [{"person_id": p, "name": idx["pid"].get(p, {}).get("name"),
                                          "confidence": "LOW"} for p in tied[:6]]
            result["evidence"] = ["multiple candidates share this name; stronger evidence needed"] \
                + result["evidence"]
            return result
        if conf in ("HIGH", "MEDIUM"):
            result.update({"proposed_entity_type": "person", "proposed_entity_id": top_pid,
                           "proposed_entity_name": idx["pid"].get(top_pid, {}).get("name"),
                           "confidence": conf})
            result["evidence"] = _evidence_for(top_pid) + result["evidence"]
            return result
        # LOW leader -> not a recommendation; expose best candidates for manual choice.
        result.update({"confidence": "AMBIGUOUS" if len(tied) > 1 else "NO_MATCH"})
        result["best_candidates"] = [{"person_id": p, "name": idx["pid"].get(p, {}).get("name"),
                                      "confidence": "LOW"} for p, _s in ranked[:6]]
        return result

    # Business legal name in content (non-institution canonical business).
    # On tax documents a preparer / ERO firm name can appear throughout the
    # return, so fail closed until role-aware entity parsing identifies the
    # taxpayer/entity block specifically.
    if not tax_document:
        matched = [nm for nm in full_names if nm in idx["biz"]]
        # Several distinct businesses named in one document is a judgement call, never an automatic
        # proposal — the same rule the person path applies to a tied name.
        if len({idx["biz"][nm][0] for nm in matched}) > 1:
            names = ", ".join(sorted(idx["biz"][nm][1] for nm in matched)[:4])
            result.update({"confidence": "AMBIGUOUS"})
            result["evidence"] = [f"several canonical businesses named in this document: {names}"] \
                + result["evidence"]
            return result
        for nm in matched:
            bid, bname = idx["biz"][nm]
            org_ident = (idx.get("org_ident") or {}).get(bid) or {}
            org_shared = idx.get("org_shared") or {}
            reasons, blocks = [f"✓ business legal name '{bname}' found in document"], []

            # (1) the name must identify ONE business.
            if (idx.get("org_name_counts") or {}).get(nm, 1) > 1:
                blocks.append("the name is carried by more than one canonical business")

            # (2) the firm's own records never own a client's paperwork. The firm appears on
            #     prepared documents as PREPARER; that is provenance, not ownership.
            if bid in (idx.get("firm_entities") or ()):
                blocks.append("this is the firm's own entity (preparer/self), not a client owner")

            # (3) positive owner eligibility, exactly as the person path requires.
            if bid not in (idx.get("org_eligible") or ()):
                blocks.append("no authoritative client evidence (no taxpayer/spouse return role, "
                              "no CRM Client contact_type)")

            # (4) corroboration must BELONG to this business and must not be a shared value.
            #     A ZIP or a street lifted from anywhere in the document proves nothing about which
            #     business owns it — that was the defect this replaces.
            own_phone = sorted((org_ident.get("phones") or set()) & set(phones)
                               - set(org_shared.get("phone") or ()))
            own_email = sorted((org_ident.get("emails") or set()) & set(emails)
                               - set(org_shared.get("email") or ()))
            folder_anchor = _org_folder_anchor(folder, bname)
            if own_phone:
                reasons.append(f"✓ the business's own phone matched (...{own_phone[0][-4:]})")
            if own_email:
                reasons.append("✓ the business's own email matched")
            if folder_anchor:
                reasons.append("✓ the source client folder anchors to this business")
            shared_hit = sorted((org_ident.get("phones") or set()) & set(phones)
                                & set(org_shared.get("phone") or ()))
            if shared_hit:
                reasons.append("• a phone matched but is shared across businesses "
                               "(context only, never an owner)")
            if (org_ident.get("zips") or set()) & set(doc_zips):
                reasons.append("• ZIP matched (a ZIP is a town, not an owner — context only)")

            corroborated = bool(own_phone or own_email or folder_anchor)
            if not corroborated:
                blocks.append("no identifier belonging to this business corroborates it "
                              "(an address or ZIP found elsewhere in the document is not evidence "
                              "about this business)")

            conf = "HIGH" if not blocks else "MEDIUM"
            if blocks:
                reasons.append("held below HIGH: " + "; ".join(blocks))
            result.update({"proposed_entity_type": "organization", "proposed_entity_id": bid,
                           "proposed_entity_name": bname, "confidence": conf})
            result["evidence"] = reasons + result["evidence"]
            return result

    return result


# --- orchestration -----------------------------------------------------------------------------

#: Filed values that mean "this is tax paperwork", read from the columns the platform already fills.
_TAX_FILING_VALUES = frozenset({"tax_document", "tax document", "tax", "tax_return", "tax return",
                                "tax_form", "tax form", "signature_document", "signature document",
                                "e_file", "efile", "e-file"})


def is_tax_document(row, *, drake_source=False) -> bool:
    """Whether the preparer/ERO/shared-contact rules apply — INDEPENDENT of the source system.

    The guard used to be ``tax_document=drake_source``, so it was live only for Drake-sourced rows.
    Every SharePoint document — including actual returns — was analysed with the guard OFF, which is
    how preparer and firm contact details on tax paperwork were allowed to score as owner evidence.

    Source is now one input among several. The filed category / classification / subcategory are the
    platform's own answer to "what kind of document is this" and are used first; ``analyze_identity``
    still applies its content markers ("form 1040", "paid preparer", "ero firm name", …) on top, so a
    return with no filed category is caught by content. Filename is never the sole basis.
    """
    if drake_source:
        return True
    for key in ("classification", "category", "subcategory"):
        value = (row.get(key) if hasattr(row, "get") else None) or ""
        value = str(value).strip().lower()
        if value and (value in _TAX_FILING_VALUES or "tax" in value):
            return True
    return False


#: A single entity legitimately owns many documents, so a raw count is not a defect signal. What is
#: never legitimate is many CONFIDENT proposals whose evidence carries no owner-positive name — the
#: shape that produced 2,157 HIGH proposals for one person. This is the ratio at which that pattern
#: is reported for review.
_MASS_MATCH_MIN_PROPOSALS = 50
_MASS_MATCH_NAMELESS_RATIO = 0.5


def mass_match_tripwire(proposals, *, min_proposals=_MASS_MATCH_MIN_PROPOSALS,
                        nameless_ratio=_MASS_MATCH_NAMELESS_RATIO) -> list[dict]:
    """Entities whose HIGH proposals look like a mass match rather than many real documents.

    A LAST-RESORT REVIEW SIGNAL, not an enforcement point: it returns a list, holds nothing, assigns
    nothing and deletes nothing. A bulk-apply caller is expected to withhold the flagged entities and
    put them in front of a person. It is deliberately not a cap — a client with 400 genuine documents
    trips nothing, because the test is the QUALITY of the evidence (what share of the proposals name
    the owner at all), not the volume.

    ``proposals`` is any iterable of proposal dicts carrying ``proposed_entity_id``, ``confidence``
    and ``evidence``.
    """
    per_entity: dict = {}
    for p in proposals:
        if (p.get("confidence") or "").upper() != "HIGH":
            continue
        key = (p.get("proposed_entity_type"), p.get("proposed_entity_id"))
        if key[1] is None:
            continue
        bucket = per_entity.setdefault(key, {"total": 0, "nameless": 0,
                                             "name": p.get("proposed_entity_name")})
        bucket["total"] += 1
        if not any("exact name" in str(e).lower() or "members named" in str(e).lower()
                   for e in (p.get("evidence") or ())):
            bucket["nameless"] += 1
    flagged = []
    for (etype, eid), b in per_entity.items():
        if b["total"] >= min_proposals and b["nameless"] / b["total"] >= nameless_ratio:
            flagged.append({"entity_type": etype, "entity_id": eid, "entity_name": b["name"],
                            "high_proposals": b["total"], "without_name_evidence": b["nameless"],
                            "reason": "mass match on evidence that never names the owner"})
    return sorted(flagged, key=lambda f: -f["high_proposals"])


def propose_document_owner(document_id, *, conn=None, idx=None, with_text=False, ocr=False):
    """Read-only proposal for one document. Never writes. Returns a proposal dict; for ineligible or
    permanent-reject documents returns {eligible: False, reason: ...} with no assignable proposal.

    ``with_text=True`` includes the (bounded) extracted text under a ``"text"`` key so a caller (the
    ingestion pipeline) can classify the document without re-extracting; the caller must not persist that
    raw text — persisted evidence stays sanitized/masked."""
    own = conn if conn is not None else engine.connect()
    try:
        row = own.execute(select(documents.c.id, documents.c.original_name, documents.c.person_id,
                                 documents.c.household_id, documents.c.organization_id,
                                 documents.c.storage_uri, documents.c.storage_path, documents.c.tags,
                                 documents.c.category, documents.c.classification,
                                 documents.c.subcategory)
                          .where(documents.c.id == document_id)).mappings().first()
        if row is None:
            return {"document_id": document_id, "eligible": False, "reason": "not_found"}
        if document_id in PERMANENT_REJECT_DOCUMENT_IDS:
            return {"document_id": document_id, "eligible": False, "reason": "permanent_reject"}
        if not (row["person_id"] is None and row["household_id"] is None and row["organization_id"] is None):
            return {"document_id": document_id, "eligible": False, "reason": "already_owned"}
        from pathlib import Path
        path = None
        if row["storage_uri"] and Path(row["storage_uri"]).is_absolute():
            path = Path(row["storage_uri"])
        elif row["storage_path"]:
            path = Path(row["storage_path"])
        text, method = extract_document_text(own, row, path, ocr=ocr)
        indexes = idx if idx is not None else build_match_indexes(own)
        folder = (row["tags"] or {}).get("taxdome_folder")
        drake_source = own.execute(
            select(document_sources.c.id)
            .where(document_sources.c.document_id == document_id)
            .where(document_sources.c.source_system == "Drake")
            .limit(1)
        ).first() is not None

        proposal = None

        if drake_source:
            from app.services.drake_document_owner import (
                propose_drake_document_owner,
            )

            proposal = propose_drake_document_owner(
                document_id,
                conn=own,
            )

        if proposal is None:
            proposal = analyze_identity(
                text,
                row["original_name"],
                folder,
                indexes,
                tax_document=is_tax_document(row, drake_source=drake_source),
            )
        proposal.update({"document_id": document_id, "filename": row["original_name"],
                         "source_folder": folder, "extraction_method": method, "eligible": True})
        if with_text:
            proposal["text"] = text
        return proposal
    finally:
        if conn is None:
            own.close()
