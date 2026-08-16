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

import re

from sqlalchemy import select

from app.db import documents, engine, households, people, relationship_entities

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
_LASTFIRST_RE = re.compile(r"\b([A-Z][A-Za-z'’\-]+),\s+([A-Z][A-Za-z'’\-]+)\b")

# Deterministic evidence weights (document CONTENT only; folder/filename are never scored).
_POINTS = {"email": 100, "phone": 90, "address": 60, "name": 40}


def _zip5(s):
    d = re.sub(r"\D", "", s or "")
    return d[:5] if len(d) >= 5 else ""


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


def extract_document_text(conn, row, path):
    """Return (text, method). Bounded and read-only. `row` is a documents mapping; `path` a Path/None."""
    ext = _ext(row["original_name"])
    text, method = "", "none"
    if ext in {"xlsx", "xlsm"} and path is not None and path.exists():
        text, method = _excel_text(path), "excel"
    elif ext == "pdf" and path is not None and path.exists():
        text = _pdf_text(path)
        method = "pdf_text" if text.strip() else "pdf_no_text"
        if not text.strip():
            text = _ocr_cache_text(conn, row["id"])
            method = "ocr_cache" if text.strip() else "pdf_no_text"
    elif ext in {"txt", "csv", "md", "log"} and path is not None and path.exists():
        try:
            text, method = path.read_text(errors="replace")[:_MAX_TEXT_CHARS], "plaintext"
        except Exception:  # noqa: BLE001
            text, method = "", "none"
    elif ext in {"png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "heic", "heif"}:
        text = _ocr_cache_text(conn, row["id"])
        method = "ocr_cache" if text.strip() else "image_no_text"
    else:
        text = _ocr_cache_text(conn, row["id"])
        method = "ocr_cache" if text.strip() else "unsupported"
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
    phones = sorted({p for p in (_phone10(m.group(0)) for m in _PHONE_RE.finditer(text)) if p})
    doc_zips = {m.group(1) for m in _ZIP_RE.finditer(text)}
    doc_streets = {_norm(m.group(0)) for m in _STREET_RE.finditer(text)}
    ssn_last4 = sorted({m.group(1) for m in _SSN_RE.finditer(text)})   # last four only; never full

    # Name phrases: 2-3 token capitalised windows (so a name is not lost inside a longer run) plus
    # 'Last, First' forms. Each maps to full-name and (first,last) indexes.
    full_names, first_last_pairs = set(), set()
    for m in _NAME_RE.finditer(text):
        toks = m.group(1).split()
        for size in (3, 2):
            for i in range(0, len(toks) - size + 1):
                w = toks[i:i + size]
                full_names.add(_norm(" ".join(w)))
                first_last_pairs.add((_norm(w[0]), _norm(w[-1])))
    for m in _LASTFIRST_RE.finditer(text):
        first_last_pairs.add((_norm(m.group(2)), _norm(m.group(1))))

    institutions = sorted({nm for nm in full_names if nm in idx["inst"]}
                          | {k for k in _INST_KW if k in ntext})

    signals, name_for = {}, {}

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
        for pid in idx["name"].get(nm, ()):
            _add(pid, "name")
            name_for[pid] = idx["pid"].get(pid, {}).get("name")
            name_pid_counts[pid] = len(idx["name"].get(nm, []))
    for pair in first_last_pairs:
        if " ".join(pair) in idx["inst"]:
            continue
        for pid in idx["first_last"].get(pair, ()):
            _add(pid, "name")
            name_for[pid] = idx["pid"].get(pid, {}).get("name")
            name_pid_counts.setdefault(pid, len(idx["first_last"].get(pair, [])))

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
            ev.append(f"✓ exact name '{info.get('name')}'")
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

def propose_document_owner(document_id, *, conn=None, idx=None):
    """Read-only proposal for one document. Never writes. Returns a proposal dict; for ineligible or
    permanent-reject documents returns {eligible: False, reason: ...} with no assignable proposal."""
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
        text, method = extract_document_text(own, row, path)
        indexes = idx if idx is not None else build_match_indexes(own)
        folder = (row["tags"] or {}).get("taxdome_folder")
        proposal = analyze_identity(text, row["original_name"], folder, indexes)
        proposal.update({"document_id": document_id, "filename": row["original_name"],
                         "source_folder": folder, "extraction_method": method, "eligible": True})
        return proposal
    finally:
        if conn is None:
            own.close()
