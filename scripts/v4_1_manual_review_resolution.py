"""READ-ONLY V4.1 identity-resolution report for MANUAL_REVIEW TaxDome folders.

SELECT-only. No INSERT/UPDATE/DELETE, no schema change, no migration, no ownership change, no file
movement, no apply script. Focuses on the folders the fixed V4 report classifies MANUAL_REVIEW and
tries to resolve the folder OWNER once, using existing production evidence only.

It reuses the V4 rules (top-level TaxDome folder = owner identity; institution/payor decided by the
folder token, never by filenames/child paths; six V2 permanent rejects preserved and excluded from
eligible counts) and adds deeper corroboration for the ambiguous person cases:

  - exact/normalized canonical name
  - email + phone (discovered generically)
  - address + DOB (discovered generically; shown for side-by-side disambiguation)
  - household membership / spouse (people.household_id)
  - aliases / source IDs / external IDs
  - source-contact / TaxDome / Wealthbox / Drake identity tables (discovered generically)
  - existing document ownership (documents already owned by each candidate)

A candidate is corroborated for a folder only by a signal that ties THAT folder to the person
(folder author email, alias/source-id/external-id match, or a source-contact/identity record) --
never by fuzzy-name similarity alone. Duplicate-name candidates are shown side-by-side with all
distinguishing evidence.

Per folder: FOLDER, UNRESOLVED DOCS, ELIGIBLE DOCS (excl. permanent rejects), CANDIDATES (ids +
distinguishing evidence), BEST CANDIDATE, EVIDENCE SUPPORTING BEST CANDIDATE, CONFIDENCE, and
RECOMMENDATION in {SAFE_TO_CONFIRM, NEEDS_HUMAN_CHOICE, NO_MATCH}. Plus totals.
"""
import json
import re
import sys

from sqlalchemy import func, select

from app.db import engine, metadata

MAX_FOLDERS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 500

if "documents" not in metadata.tables:
    metadata.reflect(bind=engine)
documents = metadata.tables["documents"]
people = metadata.tables["people"]
households = metadata.tables.get("households")
rel = metadata.tables.get("relationship_entities")
doc_sources = metadata.tables.get("document_sources")
dc = documents.c

PERMANENT_REJECT = {4704, 4716, 4717, 17932, 22336, 22338}
INST_KW = ["university", "college", "school", "bank", "credit union", "insurance", "mortgage",
           "irs", "internal revenue", "social security", "department of", "dept of", "state of",
           "fidelity", "vanguard", "schwab", "edward jones", "wells fargo", "liberty university",
           "centra", "navient", "nelnet", "sallie mae", "navy federal", "american express",
           "capital one", "chase"]
BIZ_KW = ["llc", "inc", "incorporated", "corp", "company", " co", "associates", "enterprises",
          "partners", "group", "holdings", "services", "consulting", "ministries", "church"]
EMAIL_HINTS = ("email", "e_mail", "mail")
PHONE_HINTS = ("phone", "mobile", "cell", "tel", "fax")
ADDRESS_HINTS = ("address", "street", "city", "zip", "postal")
DOB_HINTS = ("dob", "date_of_birth", "birth")
ALIAS_HINTS = ("alias", "external_id", "source_id", "source_external_id", "identifier", "handle", "username")
IDENTITY_TABLE_HINTS = ("source_contact", "contact", "taxdome", "wealthbox", "drake", "identity",
                        "linkage", "migration_link", "crosswalk")

try:
    from app.importers.taxdome_drive import resolve_folder, suggest_people, taxdome_filter
    td_pred = taxdome_filter(documents)
except Exception as exc:  # noqa: BLE001
    print("NOTE: taxdome resolver import failed (" + str(exc) + "); using tag predicate + no resolver")
    resolve_folder = None
    suggest_people = None
    td_pred = dc.tags["source_system"].astext == "TaxDome Drive"


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def nemail(s):
    return (s or "").strip().lower()


def looks_like_person(tok):
    parts = [p for p in tok.split() if p]
    return len(parts) in (2, 3) and all(p.isalpha() for p in parts)


def top_level_owner(folder):
    """Top-level TaxDome client folder = owner identity. Splits on '/' and '\\', drops a leading
    'TaxDome' root; child paths/filenames are ignored so a payor name deeper in the path can never
    become the owner."""
    segs = [s for s in re.split(r"[/\\]", folder or "") if s.strip()]
    if segs and re.sub(r"[^a-z0-9]+", "", segs[0].lower()) in ("taxdome", "taxdomedrive"):
        segs = segs[1:]
    return segs[0].strip() if segs else ""


def eligible_docs(doc_ids):
    """Document ids minus the six permanent V2 rejects."""
    return [d for d in doc_ids if d not in PERMANENT_REJECT]


def choose_owner(candidates):
    """Decide from corroborated candidates. `candidates`: [{"person_id", "corroborated": bool}].

    SAFE_TO_CONFIRM only when exactly ONE candidate carries an independent (non-name-only)
    corroborating signal. Multiple corroborated, or a candidate with none, -> NEEDS_HUMAN_CHOICE.
    No candidates -> NO_MATCH. Fuzzy-name-only candidates are never corroborated, so they can never
    reach SAFE_TO_CONFIRM."""
    if not candidates:
        return ("NO_MATCH", None, "none")
    corrob = [c for c in candidates if c.get("corroborated")]
    if len(corrob) == 1:
        return ("SAFE_TO_CONFIRM", corrob[0]["person_id"], "high")
    if len(corrob) >= 2:
        return ("NEEDS_HUMAN_CHOICE", None, "multiple_corroborated")
    return ("NEEDS_HUMAN_CHOICE", None, "name_only_no_corroboration")


def safe_dumps(obj):
    return json.dumps(obj, ensure_ascii=True, default=str)


def aprint(s):
    print(str(s).encode("ascii", "backslashreplace").decode("ascii"))


def _norm_id(v):
    return norm(str(v)) if v is not None else ""


def _col_hit(colname, hints):
    """Match a column name to evidence hints by WHOLE token (single-word hints) or substring
    (multi-word hints), so e.g. 'cancelled_at' is not read as a phone ('cell') column and
    'checklist_state' is not read as an address column."""
    low = colname.lower()
    toks = set(re.split(r"[^a-z0-9]+", low))
    for h in hints:
        if ("_" in h) or (" " in h):
            if h in low:
                return True
        elif h in toks:
            return True
    return False


def main():
    with engine.connect() as conn:
        fps = conn.execute(select(dc.id).where(dc.id.in_(sorted(PERMANENT_REJECT)))).scalars().all()
        aprint("PROVENANCE: found " + str(len(fps)) + "/" + str(len(PERMANENT_REJECT))
               + " V2 false-positive IDs (0 => NOT the expected production DB; verify before trusting output)")

        pred_list = [td_pred, dc.person_id.is_(None), dc.household_id.is_(None), dc.organization_id.is_(None)]
        if "status" in dc:
            pred_list.append(dc.status != "deleted")
        total_docs = conn.execute(select(func.count()).select_from(documents).where(*pred_list)).scalar_one()

        folder_col = dc.tags["taxdome_folder"].astext
        rows = conn.execute(
            select(dc.id.label("did"), dc.original_name.label("name"),
                   folder_col.label("folder"), dc.tags["author"].astext.label("author"))
            .where(*pred_list).order_by(folder_col.nullsfirst(), dc.id)
        ).mappings().all()

        # --- canonical people + households ------------------------------------------------------
        ppl = conn.execute(
            select(people.c.id, people.c.full_name,
                   (people.c.household_id if "household_id" in people.c else people.c.id).label("hh"))
        ).mappings().all()
        by_name, pid_name, pid_hh, members = {}, {}, {}, {}
        for p in ppl:
            by_name.setdefault(norm(p["full_name"]), []).append(p["id"])
            pid_name[p["id"]] = p["full_name"]
            pid_hh[p["id"]] = p["hh"]
            if p["hh"] is not None:
                members.setdefault(p["hh"], set()).add(p["id"])
        hh_name, hh_by_name = {}, {}
        if households is not None:
            for h in conn.execute(select(households.c.id, households.c.name)).mappings():
                hh_name[h["id"]] = h["name"]
                hh_by_name.setdefault(norm(h["name"]), []).append(h["id"])
        inst_names, biz_names, etypes = {}, {}, set()
        if rel is not None:
            for e in conn.execute(select(rel.c.id, rel.c.name, rel.c.entity_type)).mappings():
                etypes.add(e["entity_type"])
                nm = norm(e["name"])
                t = (e["entity_type"] or "").lower()
                if any(k in t for k in ("instit", "payor", "payer", "employer", "bank", "gov", "school", "insur")):
                    inst_names[nm] = (e["id"], e["name"])
                else:
                    biz_names[nm] = (e["id"], e["name"])

        # --- discover per-person evidence: email/phone/address/dob/aliases + identity tables -----
        pid_email, pid_phone, pid_addr, pid_dob = {}, {}, {}, {}
        ident_to_pids = {}   # normalized identifier (email / alias / external id) -> {pids}  (folder corroboration)
        disc = {"email": [], "phone": [], "address": [], "dob": [], "alias": [], "identity_tables": []}

        def _index_people_cols(tbl, tname):
            for col in tbl.c:
                cn = col.name.lower()
                try:
                    if _col_hit(cn, EMAIL_HINTS):
                        disc["email"].append(tname + "." + col.name)
                        for r in conn.execute(select(tbl.c.person_id, col) if tname != "people"
                                              else select(people.c.id.label("person_id"), col)).mappings():
                            ev = nemail(r[col.name])
                            if ev and "@" in ev and r["person_id"] is not None:
                                pid_email.setdefault(r["person_id"], set()).add(ev)
                                ident_to_pids.setdefault(ev, set()).add(r["person_id"])
                    elif _col_hit(cn, PHONE_HINTS):
                        disc["phone"].append(tname + "." + col.name)
                        for r in conn.execute(select(tbl.c.person_id, col) if tname != "people"
                                              else select(people.c.id.label("person_id"), col)).mappings():
                            if r[col.name] is not None and r["person_id"] is not None:
                                pid_phone.setdefault(r["person_id"], set()).add(str(r[col.name]).strip())
                    elif _col_hit(cn, DOB_HINTS):
                        disc["dob"].append(tname + "." + col.name)
                        for r in conn.execute(select(tbl.c.person_id, col) if tname != "people"
                                              else select(people.c.id.label("person_id"), col)).mappings():
                            if r[col.name] is not None and r["person_id"] is not None:
                                pid_dob.setdefault(r["person_id"], set()).add(str(r[col.name]).strip())
                    elif _col_hit(cn, ADDRESS_HINTS):
                        disc["address"].append(tname + "." + col.name)
                        for r in conn.execute(select(tbl.c.person_id, col) if tname != "people"
                                              else select(people.c.id.label("person_id"), col)).mappings():
                            if r[col.name] is not None and r["person_id"] is not None:
                                pid_addr.setdefault(r["person_id"], set()).add(str(r[col.name]).strip())
                    elif _col_hit(cn, ALIAS_HINTS):
                        disc["alias"].append(tname + "." + col.name)
                        for r in conn.execute(select(tbl.c.person_id, col) if tname != "people"
                                              else select(people.c.id.label("person_id"), col)).mappings():
                            av = _norm_id(r[col.name])
                            if av and r["person_id"] is not None:
                                ident_to_pids.setdefault(av, set()).add(r["person_id"])
                except Exception:  # noqa: BLE001
                    pass

        _index_people_cols(people, "people")
        for tname, tbl in metadata.tables.items():
            keys = set(tbl.c.keys())
            if "person_id" not in keys or tname == "people":
                continue
            low = tname.lower()
            if any(h in low for h in IDENTITY_TABLE_HINTS) or any(
                    _col_hit(c.name, EMAIL_HINTS + ALIAS_HINTS + PHONE_HINTS + ADDRESS_HINTS + DOB_HINTS)
                    for c in tbl.c):
                if any(h in low for h in IDENTITY_TABLE_HINTS):
                    disc["identity_tables"].append(tname)
                _index_people_cols(tbl, tname)

        # existing document ownership (distinguishing evidence)
        owned_docs = {}
        for r in conn.execute(select(dc.person_id, func.count()).where(dc.person_id.isnot(None))
                              .group_by(dc.person_id)).all():
            owned_docs[r[0]] = r[1]

        # per-document source identifiers (folder-side corroboration keys)
        src_ids_by_doc = {}
        if doc_sources is not None:
            scols = [doc_sources.c.document_id]
            for cn in ("source_external_id", "source_uri", "source_path"):
                if cn in doc_sources.c:
                    scols.append(doc_sources.c[cn])
            for r in conn.execute(select(*scols)).mappings():
                vals = [_norm_id(r[c]) for c in r.keys() if c != "document_id" and r[c] is not None]
                if vals:
                    src_ids_by_doc.setdefault(r["document_id"], []).extend(vals)

        # --- group unresolved docs by folder ----------------------------------------------------
        folders = {}
        for r in rows:
            folder = r["folder"] or ""
            f = folders.setdefault(folder, {"docs": [], "emails": set()})
            f["docs"].append({"did": r["did"], "name": r["name"]})
            au = nemail(r["author"])
            if au and "@" in au:
                f["emails"].add(au)

        # --- process each folder: keep only V4 MANUAL_REVIEW cases, resolve deeper ---------------
        results = []
        for folder, f in folders.items():
            try:
                docs = f["docs"]
                top = top_level_owner(folder)
                ntop = norm(top)
                inst_token = (ntop in inst_names) or any(re.search(r"\b" + re.escape(k) + r"\b", ntop) for k in INST_KW)
                name_pids = list(by_name.get(ntop, []))
                hh_hits = list(hh_by_name.get(ntop, []))
                biz_hit = biz_names.get(ntop)

                # folder-side corroboration identifiers = author emails + doc source ids
                folder_keys = set(f["emails"])
                for d in docs:
                    folder_keys.update(src_ids_by_doc.get(d["did"], []))
                corrob_pids = set()
                for k in folder_keys:
                    corrob_pids |= ident_to_pids.get(k, set())

                # resolve_folder + explicit linkage already yield SAFE in V4 -> not MANUAL_REVIEW.
                rhh = rper = None
                if resolve_folder is not None and folder:
                    try:
                        rhh, rper = resolve_folder(conn, folder)
                    except Exception:  # noqa: BLE001
                        rhh, rper = None, None

                # Reproduce V4's decision to select ONLY the MANUAL_REVIEW folders.
                if rhh is not None or (rper is not None and not inst_token):
                    continue  # SAFE_AUTO_ASSIGN in V4
                if inst_token:
                    continue  # NO_MATCH (institution/payor) in V4
                if len(hh_hits) == 1:
                    continue  # SAFE_AUTO_ASSIGN household in V4
                if len(name_pids) == 1 and name_pids[0] in corrob_pids:
                    continue  # SAFE_AUTO_ASSIGN corroborated person in V4
                dup = len(name_pids) > 1
                if dup:
                    narrowed = [pid for pid in name_pids if pid in corrob_pids]
                    if len(narrowed) == 1:
                        continue  # SAFE_AUTO_ASSIGN disambiguated in V4
                # engine fuzzy suggestions (shown, never a confirm basis)
                engine_sugg = []
                if suggest_people is not None and folder:
                    try:
                        engine_sugg = [{"id": x.get("id"), "name": x.get("full_name")}
                                       for x in (suggest_people(conn, folder) or [])][:5]
                    except Exception:  # noqa: BLE001
                        engine_sugg = []
                # Only treat as MANUAL_REVIEW folders that have at least one candidate to weigh, or a
                # business/org token to verify; everything else is V4 NO_MATCH and out of scope here.
                if not name_pids and not biz_hit and not engine_sugg and not corrob_pids:
                    continue

                # --- build side-by-side candidates with distinguishing evidence -----------------
                cand_pids = list(dict.fromkeys(name_pids + sorted(corrob_pids, key=str)
                                               + [p["id"] for p in engine_sugg if p["id"]]))
                candidates = []
                for pid in cand_pids[:10]:
                    candidates.append({
                        "person_id": pid,
                        "name": pid_name.get(pid),
                        "household_id": pid_hh.get(pid),
                        "household_name": hh_name.get(pid_hh.get(pid)),
                        "emails": sorted(pid_email.get(pid, set()), key=str)[:3],
                        "phones": sorted(pid_phone.get(pid, set()), key=str)[:2],
                        "address": sorted(pid_addr.get(pid, set()), key=str)[:1],
                        "dob": sorted(pid_dob.get(pid, set()), key=str)[:1],
                        "existing_owned_documents": owned_docs.get(pid, 0),
                        "how_found": ("name_exact" if pid in name_pids else "")
                                     + ("+folder_corroboration" if pid in corrob_pids else "")
                                     + ("+fuzzy_suggestion" if pid in [p["id"] for p in engine_sugg] else ""),
                        "corroborated": pid in corrob_pids,
                    })

                recommendation, best_pid, confidence = choose_owner(
                    [{"person_id": c["person_id"], "corroborated": c["corroborated"]} for c in candidates])

                # Business/org-only folders: never auto-confirm (could be a payor) -> human choice.
                if best_pid is None and not any(c["corroborated"] for c in candidates) and biz_hit:
                    recommendation = "NEEDS_HUMAN_CHOICE"
                    confidence = "business_verify_client_vs_payor"

                best = next((c for c in candidates if c["person_id"] == best_pid), None)
                best_evidence = []
                if best is not None:
                    keys_hit = [k for k in folder_keys if best_pid in ident_to_pids.get(k, set())]
                    if any("@" in k for k in keys_hit):
                        best_evidence.append("folder author email matches this person")
                    if any("@" not in k for k in keys_hit):
                        best_evidence.append("folder document source-id/alias matches this person")
                    best_evidence.append("unique canonical name matches folder" if len(name_pids) == 1
                                         else "disambiguated among same-name people by the above signal")

                results.append({
                    "folder": folder,
                    "owner_token": top,
                    "unresolved_docs": len(docs),
                    "eligible_docs_excl_permanent_rejects": len(eligible_docs([d["did"] for d in docs])),
                    "contains_permanent_reject_docs": [d["did"] for d in docs if d["did"] in PERMANENT_REJECT],
                    "candidates": candidates,
                    "best_candidate": ({"person_id": best_pid, "name": pid_name.get(best_pid)} if best_pid else None),
                    "evidence_supporting_best": best_evidence,
                    "confidence": confidence,
                    "recommendation": recommendation,
                    "business_org_candidate": ({"id": biz_hit[0], "name": biz_hit[1]} if biz_hit else None),
                    "fuzzy_suggestions": engine_sugg,
                    "sample_documents": [{"id": d["did"], "name": d["name"]} for d in docs[:5]],
                })
            except Exception as exc:  # noqa: BLE001 -- one folder cannot end the report
                aprint("FOLDER: " + str(folder) + "  ERROR: " + repr(exc) + " -- skipped, continuing")
                results.append({"folder": folder, "error": repr(exc), "recommendation": "ERROR"})

        results.sort(key=lambda x: -x.get("unresolved_docs", 0))
        results = results[:MAX_FOLDERS]

        aprint("entity_type values in relationship_entities: " + str(sorted(etypes, key=str)))
        aprint("SCHEMA DISCOVERY: " + safe_dumps({k: sorted(set(v), key=str) for k, v in disc.items()}))
        aprint("TOTAL UNRESOLVED DOCUMENTS (all folders): " + str(total_docs))
        aprint("MANUAL_REVIEW FOLDERS ANALYZED: " + str(len(results)))
        aprint("")

        totals = {"manual_review_folders": len(results), "documents_represented": 0,
                  "folders_safe_to_confirm": 0, "documents_assignable_after_confirmation": 0,
                  "folders_needs_human_choice": 0, "folders_no_match": 0, "folders_errored": 0}
        for r in results:
            # Rendering one folder must never crash the whole report: every field access is null-safe
            # (.get with defaults + str()/safe_dumps coercion) and the body is isolated in try/except.
            # Error-result entries are printed for completeness (not silently dropped) and not counted.
            try:
                if "error" in r:
                    totals["folders_errored"] += 1
                    aprint("FOLDER: " + str(r.get("folder", "?")) + "  DATA ERROR: "
                           + str(r.get("error", "")) + "  -- shown for completeness; not counted")
                    aprint("")
                    continue
                rec = r.get("recommendation", "NEEDS_HUMAN_CHOICE")
                totals["documents_represented"] += int(r.get("unresolved_docs", 0) or 0)
                if rec == "SAFE_TO_CONFIRM":
                    totals["folders_safe_to_confirm"] += 1
                    totals["documents_assignable_after_confirmation"] += int(
                        r.get("eligible_docs_excl_permanent_rejects", 0) or 0)
                elif rec == "NEEDS_HUMAN_CHOICE":
                    totals["folders_needs_human_choice"] += 1
                elif rec == "NO_MATCH":
                    totals["folders_no_match"] += 1

                aprint("FOLDER: " + str(r.get("owner_token") or r.get("folder", "?")) + "  ("
                       + str(r.get("unresolved_docs", 0)) + " docs, "
                       + str(r.get("eligible_docs_excl_permanent_rejects", 0)) + " eligible)  => "
                       + str(rec) + " [" + str(r.get("confidence", "")) + "]")
                best = r.get("best_candidate")
                if best:
                    aprint("  best_candidate: person id=" + str(best.get("person_id"))
                           + " '" + str(best.get("name")) + "'")
                    aprint("  evidence_supporting_best: " + safe_dumps(r.get("evidence_supporting_best", [])))
                if r.get("business_org_candidate"):
                    aprint("  business_org_candidate (verify client vs payor): "
                           + safe_dumps(r.get("business_org_candidate")))
                aprint("  candidates: " + safe_dumps(r.get("candidates", [])))
                if r.get("fuzzy_suggestions"):
                    aprint("  fuzzy_suggestions (never a confirm basis): " + safe_dumps(r.get("fuzzy_suggestions")))
                if r.get("contains_permanent_reject_docs"):
                    aprint("  permanent_reject_docs (excluded): " + safe_dumps(r.get("contains_permanent_reject_docs")))
                aprint("  sample_documents: " + safe_dumps(r.get("sample_documents", [])))
                aprint("")
            except Exception as exc:  # noqa: BLE001 -- one folder's render can't end the report
                totals["folders_errored"] += 1
                aprint("FOLDER: " + str(r.get("folder") or r.get("owner_token", "?"))
                       + "  RENDER ERROR: " + repr(exc) + "  -- skipped, continuing")
                aprint("")
                continue

        aprint("TOTALS:")
        for k in ("manual_review_folders", "documents_represented", "folders_safe_to_confirm",
                  "documents_assignable_after_confirmation", "folders_needs_human_choice",
                  "folders_no_match", "folders_errored"):
            aprint("  " + k + ": " + str(totals[k]))

        aprint("=== BEGIN_V41_JSON ===")
        print(safe_dumps({"totals": totals, "folders": results}))
        aprint("=== END_V41_JSON ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
