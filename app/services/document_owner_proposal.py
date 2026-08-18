"""READ-ONLY content-based owner proposal for genuinely-unassigned documents.

Extracts identity evidence from a document's ACTUAL content, matches it against existing canonical
people / households / businesses, and returns a ranked proposed owner with a confidence bucket and the
supporting evidence. It proposes only — it never assigns, never writes, and never modifies a source file.

Extraction by type (bounded; reuses existing infrastructure, no OCR engine required):
  * Excel (.xlsx/.xlsm): openpyxl cell text (reuses app.routes.documents.read_workbook_preview).
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
    people,
    person_source_links,
    relationship_entities,
    source_contacts,
)

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
_POINTS = {"email": 100, "phone": 90, "address": 60, "name": 40}


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
        from app.routes.documents import read_workbook_preview
        r = read_workbook_preview(path)
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
    for h in conn.execute(select(households.c.id, households.c.name)).mappings():
        idx["hh_name"][h["id"]] = h["name"]
    for e in conn.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                 relationship_entities.c.entity_type)).mappings():
        t = (e["entity_type"] or "").lower()
        if any(k in t for k in ("instit", "payor", "payer", "employer", "bank", "gov", "school", "insur")):
            idx["inst"].add(_norm(e["name"]))
        else:
            idx["biz"][_norm(e["name"])] = (e["id"], e["name"])
    return idx


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

def _confidence(sigs, unique_name):
    """Deterministic tier for one person candidate from its content signal set."""
    if "email" in sigs or "phone" in sigs:
        return "HIGH"                                   # exact email / phone = very strong
    if "name" in sigs and "address" in sigs:
        return "HIGH"                                   # taxpayer name + matching address = very strong
    if "name" in sigs and unique_name:
        return "MEDIUM"                                 # exact full name alone = plausible, inspect
    return "LOW"                                         # ambiguous / weak


def analyze_identity(text, filename, folder, idx):
    """Pure content analysis: extract identity evidence from `text`, score canonical candidates, and
    return a proposal. Folder/filename are NEVER scored (content wins). Returns proposed_entity_type/id/
    name, confidence (HIGH/MEDIUM/AMBIGUOUS/NO_MATCH), evidence[], competing[], best_candidates[],
    extracted{}."""
    ntext = _norm(text)
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

    def _add(pid, sig):
        signals.setdefault(pid, set()).add(sig)

    for e in emails:
        for pid in idx["email"].get(e, ()):
            _add(pid, "email")
    for ph in phones:
        for pid in idx["phone"].get(ph, ()):
            _add(pid, "phone")
    name_pid_counts = {}   # for uniqueness: how many people share a matched name
    for nm in full_names:
        if nm in idx["inst"]:
            continue                       # institution names are context, never a person
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
    for pid in list(signals):
        info = idx["pid"].get(pid, {})
        if (doc_zips & info.get("zips", set())) or any(
                s and (s in ds or ds in s) for s in info.get("streets", set()) for ds in doc_streets):
            _add(pid, "address")

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
        if "email" in sigs:
            ev.append(f"✓ email {info.get('email')} matched")
        if "phone" in sigs:
            ph = _phone10(info.get("phone"))
            ev.append(f"✓ phone ending {ph[-4:]} matched" if ph else "✓ phone matched")
        if "address" in sigs:
            ev.append("✓ address/ZIP matched")
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
        conf = _confidence(top_sig, unique_name)
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
    for nm in full_names:
        if nm in idx["biz"]:
            bid, bname = idx["biz"][nm]
            conf = "HIGH" if (doc_zips or doc_streets) else "MEDIUM"
            result.update({"proposed_entity_type": "organization", "proposed_entity_id": bid,
                           "proposed_entity_name": bname, "confidence": conf})
            result["evidence"] = [f"✓ business legal name '{bname}' found in document"
                                  + (" + address" if conf == "HIGH" else "")] + result["evidence"]
            return result

    return result


# --- orchestration -----------------------------------------------------------------------------

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
                                 documents.c.storage_uri, documents.c.storage_path, documents.c.tags)
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
        proposal = analyze_identity(text, row["original_name"], folder, indexes)
        proposal.update({"document_id": document_id, "filename": row["original_name"],
                         "source_folder": folder, "extraction_method": method, "eligible": True})
        if with_text:
            proposal["text"] = text
        return proposal
    finally:
        if conn is None:
            own.close()
