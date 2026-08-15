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
_NAME_RE = re.compile(r"\b([A-Z][A-Za-z'’.\-]+(?:\s+[A-Z][A-Za-z'’.\-]+){1,2})\b")
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
    """Build read-only lookup indexes from existing canonical data (people/households/businesses)."""
    idx = {"email": {}, "phone": {}, "name": {}, "pid": {}, "members": {}, "hh_name": {},
           "biz": {}, "inst": set()}
    pcols = people.c
    for r in conn.execute(select(
            pcols.id, pcols.full_name,
            pcols.primary_email if "primary_email" in pcols else pcols.id,
            pcols.normalized_email if "normalized_email" in pcols else pcols.id,
            pcols.primary_phone if "primary_phone" in pcols else pcols.id,
            (pcols.household_id if "household_id" in pcols else pcols.id).label("hh"))).mappings():
        pid = r["id"]
        idx["pid"][pid] = {"name": r["full_name"], "email": r.get("primary_email"),
                           "phone": r.get("primary_phone"), "household_id": r["hh"]}
        idx["name"].setdefault(_norm(r["full_name"]), []).append(pid)
        for e in (r.get("primary_email"), r.get("normalized_email")):
            e = (e or "").strip().lower()
            if e and "@" in e:
                idx["email"].setdefault(e, set()).add(pid)
        ph = _phone10(r.get("primary_phone"))
        if ph:
            idx["phone"].setdefault(ph, set()).add(pid)
        if r["hh"] is not None:
            idx["members"].setdefault(r["hh"], set()).add(pid)
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

def analyze_identity(text, filename, folder, idx):
    """Pure content analysis: extract identity evidence from `text` and rank a proposed owner. Returns a
    dict with proposed_entity_type/id/name, confidence, evidence[], competing[], and extracted{}."""
    ntext = _norm(text)
    emails = sorted({m.group(0).lower() for m in _EMAIL_RE.finditer(text)})
    phones = sorted({p for p in (_phone10(m.group(0)) for m in _PHONE_RE.finditer(text)) if p})
    # Capitalised runs can absorb adjacent label words ("Recipient MARY HARDY"), so look up every
    # 2- and 3-token contiguous window of each run against the canonical name index, not the whole run.
    name_phrases = set()
    for m in _NAME_RE.finditer(text):
        toks = m.group(1).split()
        for size in (3, 2):
            for i in range(0, len(toks) - size + 1):
                name_phrases.add(_norm(" ".join(toks[i:i + size])))

    # institution/payor CONTEXT (never an owner)
    institutions = sorted({nm for nm in name_phrases if nm in idx["inst"]}
                          | {k for k in _INST_KW if k in ntext})

    signals = {}   # pid -> set of signals

    def _add(pid, sig):
        signals.setdefault(pid, set()).add(sig)

    for e in emails:
        for pid in idx["email"].get(e, ()):
            _add(pid, "email")
    for ph in phones:
        for pid in idx["phone"].get(ph, ()):
            _add(pid, "phone")
    name_hits = {}   # norm name -> [pids]
    for nm in name_phrases:
        if nm in idx["inst"]:
            continue                       # institution names are context, never a person
        pids = idx["name"].get(nm)
        if pids:
            name_hits[nm] = pids
            for pid in pids:
                _add(pid, "name")

    evidence, competing = [], []
    for e in emails[:5]:
        owners = ", ".join(f"#{p}" for p in sorted(idx['email'].get(e, ()))) or "(no canonical match)"
        evidence.append(f"email {e} maps to {owners}")
    for ph in phones[:5]:
        owners = ", ".join(f"#{p}" for p in sorted(idx['phone'].get(ph, ()))) or "(no canonical match)"
        evidence.append(f"phone {ph} maps to {owners}")
    for nm, pids in list(name_hits.items())[:6]:
        evidence.append(f"name '{nm}' matches " + ", ".join(f"#{p}" for p in pids))
    for inst in institutions[:5]:
        evidence.append(f"institution/payor '{inst}' present (context only, not an owner)")

    extracted = {"emails": emails[:8], "phones": phones[:8],
                 "names": sorted(name_hits.keys())[:8], "institutions": institutions[:8]}
    result = {"proposed_entity_type": None, "proposed_entity_id": None, "proposed_entity_name": None,
              "confidence": "NO_MATCH", "evidence": evidence, "competing": competing,
              "extracted": extracted}

    # Household (joint): two or more DISTINCT co-household members named in the content.
    named_pids = {p for pids in name_hits.values() for p in pids} | set(signals)
    for hh, mem in idx["members"].items():
        present = mem & named_pids
        if len(present) >= 2:
            result.update({"proposed_entity_type": "household", "proposed_entity_id": hh,
                           "proposed_entity_name": idx["hh_name"].get(hh),
                           "confidence": "HIGH_CONFIDENCE"})
            result["evidence"].insert(0, f"two household members named in document map to household #{hh}")
            result["competing"] = [{"person_id": p, "name": idx["pid"].get(p, {}).get("name")}
                                   for p in sorted(present)]
            return result

    if signals:
        # rank people by number of independent signals
        ranked = sorted(signals.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        top_pid, top_sig = ranked[0]
        tied = [pid for pid, s in ranked if len(s) == len(top_sig)]
        strong = {"email", "phone"} & top_sig
        if len(top_sig) >= 2:
            conf = "HIGH_CONFIDENCE"
        elif strong:
            conf = "REVIEW_RECOMMENDED"        # single strong (email/phone) field
        else:
            conf = "REVIEW_RECOMMENDED"        # single exact-name field (never HIGH on name alone)
        # ambiguity: more than one person tied on the same signal strength, or a duplicated name-only
        name_only_dupe = (top_sig == {"name"}
                          and any(len(idx["name"].get(nm, [])) > 1 for nm in name_hits))
        if len(tied) > 1 or name_only_dupe:
            result.update({"confidence": "AMBIGUOUS", "proposed_entity_type": None})
            result["competing"] = [{"person_id": p, "name": idx["pid"].get(p, {}).get("name"),
                                    "signals": sorted(signals[p])} for p in tied[:6]] or [
                                   {"person_id": p, "name": idx["pid"].get(p, {}).get("name")}
                                   for nm in name_hits for p in idx["name"].get(nm, [])][:6]
            return result
        info = idx["pid"].get(top_pid, {})
        result.update({"proposed_entity_type": "person", "proposed_entity_id": top_pid,
                       "proposed_entity_name": info.get("name"), "confidence": conf})
        return result

    # Business legal name in content (non-institution canonical business).
    for nm in name_phrases:
        if nm in idx["biz"]:
            bid, bname = idx["biz"][nm]
            result.update({"proposed_entity_type": "organization", "proposed_entity_id": bid,
                           "proposed_entity_name": bname, "confidence": "REVIEW_RECOMMENDED"})
            result["evidence"].insert(0, f"business legal name '{bname}' (#{bid}) found in document")
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
