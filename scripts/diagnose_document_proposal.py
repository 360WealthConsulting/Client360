"""READ-ONLY diagnostic for the unassigned-document owner-proposal engine.

Given one or more document IDs, prints the full extraction + signal + candidate breakdown so we can tell
whether a "No confident match" result is caused by:
  (A) extraction missing the useful identity content, or
  (B) the document genuinely containing no matchable identity evidence.

It writes NOTHING (read-only connection, no INSERT/UPDATE/DELETE), makes no ownership change, and never
prints a full SSN/TIN — any 9-digit or NNN-NN-NNNN sequence is masked to its last four before display.
It reuses the exact production code path: app.services.document_owner_proposal.extract_document_text,
build_match_indexes (incl. source-contact enrichment) and analyze_identity. The per-signal candidate map
and per-candidate scores are recomputed here for visibility from the SAME index + constants the engine
uses; the authoritative engine result is printed alongside so any divergence would be obvious.

Usage (from the app root, with app/.env providing DATABASE_URL):
    python scripts/diagnose_document_proposal.py 458 459 [--chars 1500]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from sqlalchemy import select

from app.db import documents, engine, person_source_links, source_contacts
from app.services import document_owner_proposal as dop

_SSN_DASH = re.compile(r"\b\d{3}[-\s]\d{2}[-\s](\d{4})\b")
_NUM9 = re.compile(r"(?<!\d)\d{9}(?!\d)")            # bare 9-digit SSN/EIN-like run
_NUM_LONG = re.compile(r"(?<!\d)\d{7,}(?!\d)")       # any long digit run -> keep last 4 only


def _sanitize(s: str) -> str:
    """Mask anything resembling a full SSN/TIN/long identifier down to its last four digits."""
    s = _SSN_DASH.sub(lambda m: f"***-**-{m.group(1)}", s)
    s = _NUM9.sub(lambda m: "*****" + m.group(0)[-4:], s)
    s = _NUM_LONG.sub(lambda m: "*" * (len(m.group(0)) - 4) + m.group(0)[-4:], s)
    return s


def _raw_name_phrases(text: str):
    """Recompute the raw capitalised name phrases the engine extracts (before canonical matching), so we
    can see names that appear in the document even when none matched an existing record."""
    full, first_last = set(), set()
    for m in dop._NAME_RE.finditer(text):
        toks = m.group(1).split()
        for size in (3, 2):
            for i in range(0, len(toks) - size + 1):
                w = toks[i:i + size]
                full.add(dop._norm(" ".join(w)))
                first_last.add((dop._norm(w[0]), dop._norm(w[-1])))
    for m in dop._LASTFIRST_RE.finditer(text):
        first_last.add((dop._norm(m.group(2)), dop._norm(m.group(1))))
    return full, first_last


def _person_sources(conn, pid):
    """Sanitised source-contact signals linked to a candidate person (why an email/phone matched)."""
    sc = source_contacts.c
    j = person_source_links.join(source_contacts, person_source_links.c.source_contact_id == sc.id)
    out = []
    for r in conn.execute(select(sc.source_system, sc.email, sc.phone, sc.raw_data)
                          .select_from(j).where(person_source_links.c.person_id == pid)).mappings():
        raw = r["raw_data"] if isinstance(r["raw_data"], dict) else {}
        out.append(f"[{r['source_system']}] email={r['email']!r} phone={_sanitize(str(r['phone']))!r} "
                   f"home_email={raw.get('home_email')!r} home_phone={_sanitize(str(raw.get('home_phone')))!r}")
    return out


def _reason(proposal, raw_full, raw_fl, idx, has_content):
    conf = proposal["confidence"]
    if conf in ("HIGH", "MEDIUM"):
        return f"reached {conf} (proposed owner set)"
    detected = bool(raw_full or raw_fl or proposal["extracted"]["emails"]
                    or proposal["extracted"]["phones"])
    matched_name = any(idx["name"].get(n) for n in raw_full) or any(idx["first_last"].get(p) for p in raw_fl)
    if not has_content:
        return "extraction produced no usable text (method reported empty content) -> cannot match"
    if not detected:
        return ("NO identity signals detected in the extracted text (only institution/context terms, "
                "if any) -> extraction is missing identity content OR the document has none (A vs B)")
    if proposal["extracted"]["emails"] or proposal["extracted"]["phones"]:
        if conf == "AMBIGUOUS":
            return "email/phone/name present but candidates tie with no distinguishing identifier"
        return "email/phone detected but did not resolve to exactly one canonical person"
    if (raw_full or raw_fl) and not matched_name:
        return ("name phrase(s) detected in content but NONE matched an existing canonical person "
                "(name not in Client360, or spelled differently) -> matching gap, not extraction")
    if conf == "AMBIGUOUS":
        return "name matched multiple people and no email/phone/address to disambiguate -> AMBIGUOUS"
    return "name-only match below the HIGH/MEDIUM threshold"


def diagnose_one(conn, did, nchars):
    row = conn.execute(select(
        documents.c.id, documents.c.original_name, documents.c.person_id, documents.c.household_id,
        documents.c.organization_id, documents.c.storage_uri, documents.c.storage_path, documents.c.tags,
    ).where(documents.c.id == did)).mappings().first()
    print("=" * 78)
    if row is None:
        print(f"DOCUMENT #{did}: NOT FOUND")
        return
    print(f"DOCUMENT #{did}  {row['original_name']}")
    owned = not (row["person_id"] is None and row["household_id"] is None and row["organization_id"] is None)
    reject = did in dop.PERMANENT_REJECT_DOCUMENT_IDS
    print(f"  eligibility : {'ALREADY OWNED / not eligible' if owned else 'eligible (owner all-NULL)'}"
          f"{'  [PERMANENT-REJECT]' if reject else ''}")
    path = None
    if row["storage_uri"] and Path(row["storage_uri"]).is_absolute():
        path = Path(row["storage_uri"])
    elif row["storage_path"]:
        path = Path(row["storage_path"])
    print(f"  storage_uri : {row['storage_uri']}")
    print(f"  storage_path: {row['storage_path']}")
    print(f"  resolved    : {path}  exists={bool(path and path.exists())}")
    folder = (row["tags"] or {}).get("taxdome_folder")
    print(f"  folder      : {folder!r}")

    text, method = dop.extract_document_text(conn, row, path)
    print(f"\n  EXTRACTION METHOD : {method}")
    print(f"  extracted length  : {len(text)} chars")
    print("  --- TEXT PREVIEW (sanitized, bounded) ---")
    preview = _sanitize(text[:nchars]).replace("\r", " ")
    lines = preview.splitlines() or ["(empty)"]
    for line in lines[:60]:
        print("    | " + line)
    if len(text) > nchars:
        print(f"    | ...(+{len(text) - nchars} more chars not shown)")

    idx = dop.build_match_indexes(conn)
    proposal = dop.analyze_identity(text, row["original_name"], folder, idx)
    ex = proposal["extracted"]
    raw_full, raw_fl = _raw_name_phrases(text)
    phones10 = sorted({p for p in (dop._phone10(m.group(0)) for m in dop._PHONE_RE.finditer(text)) if p})
    streets = sorted({dop._norm(m.group(0)) for m in dop._STREET_RE.finditer(text)})

    print("\n  DETECTED SIGNALS (from content):")
    print(f"    raw name phrases : {sorted(raw_full)[:12]}")
    print(f"    (first,last)     : {sorted(raw_fl)[:12]}")
    print(f"    emails           : {ex['emails']}")
    print(f"    phones (last4)   : {ex['phones']}")
    print(f"    zips             : {ex['zips']}")
    print(f"    street fragments : {streets[:8]}")
    print(f"    ssn/tin last4    : {ex['ssn_last4']}   (masked, last 4 only)")
    print(f"    institutions     : {ex['institutions']}   (CONTEXT only -- never an owner)")

    print("\n  CANDIDATE RECORDS PER SIGNAL:")
    any_cand = False
    for e in ex["emails"]:
        pids = sorted(idx["email"].get(e, set()))
        any_cand = any_cand or bool(pids)
        print(f"    email {e!r:44} -> people {pids}")
    for p in phones10:
        pids = sorted(idx["phone"].get(p, set()))
        any_cand = any_cand or bool(pids)
        print(f"    phone ...{p[-4:]}{'':39} -> people {pids}")
    for nm in sorted(raw_full):
        pids = idx["name"].get(nm, [])
        if pids:
            any_cand = True
            print(f"    name {nm!r:45} -> people {pids}")
    for pair in sorted(raw_fl):
        pids = idx["first_last"].get(pair, [])
        if pids:
            any_cand = True
            print(f"    (first,last) {str(pair):37} -> people {pids}")
    if not any_cand:
        print("    (no signal resolved to any canonical record)")

    cand_pids = set()
    for e in ex["emails"]:
        cand_pids |= idx["email"].get(e, set())
    for p in phones10:
        cand_pids |= idx["phone"].get(p, set())
    for nm in raw_full:
        cand_pids |= set(idx["name"].get(nm, []))
    for pair in raw_fl:
        cand_pids |= set(idx["first_last"].get(pair, []))
    if cand_pids:
        print("\n  LINKED SOURCE-CONTACT SIGNALS FOR CANDIDATES:")
        for pid in sorted(cand_pids):
            info = idx["pid"].get(pid, {})
            print(f"    #{pid} {info.get('name')!r}  canon_email={info.get('email')!r} "
                  f"canon_phone={_sanitize(str(info.get('phone')))!r}")
            for line in _person_sources(conn, pid):
                print(f"        {line}")

    print("\n  ENGINE RESULT (authoritative):")
    print(f"    confidence     : {proposal['confidence']}")
    print(f"    proposed owner : {proposal['proposed_entity_type']} "
          f"#{proposal['proposed_entity_id']} {proposal.get('proposed_entity_name')}")
    print(f"    evidence       : {proposal['evidence']}")
    print(f"    best candidates: {proposal['best_candidates']}")
    print(f"    REASON         : {_reason(proposal, raw_full, raw_fl, idx, bool(text.strip()))}")


def main():
    ap = argparse.ArgumentParser(description="Read-only owner-proposal diagnostic for one or more docs.")
    ap.add_argument("document_ids", nargs="+", type=int)
    ap.add_argument("--chars", type=int, default=1500, help="bounded text-preview length (default 1500)")
    args = ap.parse_args()
    with engine.connect() as conn:                  # read-only: never begin()/commit()
        for did in args.document_ids:
            diagnose_one(conn, did, args.chars)
    print("=" * 78)
    print("READ-ONLY: no data was written; no ownership changed.")


if __name__ == "__main__":
    main()
