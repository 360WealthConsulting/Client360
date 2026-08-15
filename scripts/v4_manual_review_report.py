"""READ-ONLY manual-review report for the remaining unresolved TaxDome Drive folders.

SELECT-only. No INSERT/UPDATE/DELETE, no schema change, no migration, no ownership change,
no file movement. This is a REPORT, not a matcher: it makes no assignment decisions and applies
no scoring framework. For each unresolved folder it surfaces the raw identity evidence already in
Client360 and a lightweight descriptive label, so a human can decide the owner.

Sorted by unresolved-document count descending; prints the largest N folders (default 20; pass an
integer arg to change). Preserves the six V2 permanent rejects (flagged, never recommended) and
matches institution/payor names against the FOLDER TOKEN only (filenames never override the owner).

Per folder it shows: folder, unresolved doc count, candidate canonical people (exact + engine
suggestions), candidate household(s), candidate business/organization(s), source/contact/linkage
evidence, email/phone evidence, sample document IDs/names, a descriptive type label
(PERSON / HOUSEHOLD / BUSINESS / INSTITUTION_OR_PAYOR / TEST_JUNK / UNKNOWN), and either a concise
recommended owner (when evidence supports one) or exactly what human decision is needed.
"""
import json
import re
import sys

from sqlalchemy import func, select

from app.db import engine, metadata

TOP_N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 20

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
JUNK_KW = ["test", "sample", "demo", "misc", "unsorted", "temp", "untitled", "new folder",
           "delete", "scratch", "example", "dummy"]
EMAIL_HINTS = ("email", "e_mail", "mail")
PHONE_HINTS = ("phone", "mobile", "cell", "tel", "fax")
ALIAS_HINTS = ("alias", "external_id", "source_id", "source_external_id", "identifier", "handle", "username")
LINK_HINTS = ("source_contact", "contact", "taxdome", "identity", "linkage", "migration_link", "crosswalk")

try:
    from app.importers.taxdome_drive import resolve_folder, suggest_people, taxdome_filter
    td_pred = taxdome_filter(documents)
except Exception as exc:  # noqa: BLE001
    print("NOTE: taxdome resolver import failed (" + str(exc) + "); using tag predicate + no folder resolution")
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
    """The TOP-LEVEL TaxDome client folder = the owner context. Splits on both '/' and '\\', drops a
    leading 'TaxDome'/'TaxDome Drive' root segment, and returns the first client segment. Child paths
    and document filenames are intentionally ignored, so an institution/payor name appearing deeper in
    the path (e.g. .../Client uploaded documents/Centra W2.pdf) can never become the owner identity."""
    segs = [s for s in re.split(r"[/\\]", folder or "") if s.strip()]
    if segs and re.sub(r"[^a-z0-9]+", "", segs[0].lower()) in ("taxdome", "taxdomedrive"):
        segs = segs[1:]
    return segs[0].strip() if segs else ""


def safe_dumps(obj):
    # ASCII-only JSON so a non-UTF-8 stdout (e.g. Windows cp1252 when piped to Tee-Object) can never
    # raise UnicodeEncodeError while printing folder evidence. default=str covers sets/Decimals/etc.
    return json.dumps(obj, ensure_ascii=True, default=str)


def aprint(s):
    # Encoding-proof print: any non-ASCII byte in a folder name / candidate / filename is
    # backslash-escaped rather than crashing the whole report on a non-UTF-8 console.
    print(str(s).encode("ascii", "backslashreplace").decode("ascii"))


def main():
    with engine.connect() as conn:
        fps = conn.execute(select(dc.id).where(dc.id.in_(sorted(PERMANENT_REJECT)))).scalars().all()
        print("PROVENANCE: found " + str(len(fps)) + "/" + str(len(PERMANENT_REJECT))
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

        # canonical reference data
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

        # discover email/phone/alias/linkage evidence generically (read-only)
        email_to_pids, pid_email, pid_phone, alias_to_pids, link_owner = {}, {}, {}, {}, {}
        disc = {"email": [], "phone": [], "alias": [], "link": []}
        for col in people.c:
            cn = col.name.lower()
            if any(h in cn for h in EMAIL_HINTS):
                disc["email"].append("people." + col.name)
                for r in conn.execute(select(people.c.id, col)).mappings():
                    ev = nemail(r[col.name])
                    if ev and "@" in ev:
                        email_to_pids.setdefault(ev, set()).add(r["id"])
                        pid_email.setdefault(r["id"], set()).add(ev)
            if any(h in cn for h in PHONE_HINTS):
                disc["phone"].append("people." + col.name)
                for r in conn.execute(select(people.c.id, col)).mappings():
                    pv = str(r[col.name]).strip() if r[col.name] is not None else ""
                    if pv:
                        pid_phone.setdefault(r["id"], set()).add(pv)
        for tname, tbl in metadata.tables.items():
            keys = set(tbl.c.keys())
            low = tname.lower()
            if "person_id" in keys:
                for col in tbl.c:
                    cn = col.name.lower()
                    try:
                        if any(h in cn for h in EMAIL_HINTS):
                            disc["email"].append(tname + "." + col.name)
                            for r in conn.execute(select(tbl.c.person_id, col)).mappings():
                                ev = nemail(r[col.name])
                                if ev and "@" in ev and r["person_id"] is not None:
                                    email_to_pids.setdefault(ev, set()).add(r["person_id"])
                                    pid_email.setdefault(r["person_id"], set()).add(ev)
                        elif any(h in cn for h in PHONE_HINTS):
                            disc["phone"].append(tname + "." + col.name)
                            for r in conn.execute(select(tbl.c.person_id, col)).mappings():
                                if r[col.name] is not None and r["person_id"] is not None:
                                    pid_phone.setdefault(r["person_id"], set()).add(str(r[col.name]).strip())
                        elif any(h in cn for h in ALIAS_HINTS):
                            disc["alias"].append(tname + "." + col.name)
                            for r in conn.execute(select(tbl.c.person_id, col)).mappings():
                                av = norm(str(r[col.name]) if r[col.name] is not None else "")
                                if av and r["person_id"] is not None:
                                    alias_to_pids.setdefault(av, set()).add(r["person_id"])
                    except Exception:  # noqa: BLE001
                        pass
            if any(h in low for h in LINK_HINTS) and ("person_id" in keys or "household_id" in keys):
                folder_like = [c for c in tbl.c if any(k in c.name.lower()
                               for k in ("folder", "account", "source_uri", "path", "name"))]
                if folder_like:
                    disc["link"].append(tname)
                    try:
                        sel = [tbl.c[c.name] for c in folder_like]
                        if "person_id" in keys:
                            sel.append(tbl.c.person_id)
                        if "household_id" in keys:
                            sel.append(tbl.c.household_id)
                        for r in conn.execute(select(*sel)).mappings():
                            pid, hid = r.get("person_id"), r.get("household_id")
                            for c in folder_like:
                                k = norm(str(r[c.name]) if r[c.name] is not None else "")
                                if not k:
                                    continue
                                if hid is not None:
                                    link_owner[k] = ("household", hid)
                                elif pid is not None:
                                    link_owner[k] = ("person", pid)
                    except Exception:  # noqa: BLE001
                        pass

        # document_sources rows per document (source/contact evidence)
        src_by_doc = {}
        if doc_sources is not None:
            scols = [doc_sources.c.document_id]
            for cn in ("source_system", "source_uri", "source_path", "source_external_id"):
                if cn in doc_sources.c:
                    scols.append(doc_sources.c[cn])
            for r in conn.execute(select(*scols)).mappings():
                src_by_doc.setdefault(r["document_id"], []).append(
                    {k: r[k] for k in r.keys() if k != "document_id"})

        # group by folder
        folders = {}
        for r in rows:
            folder = r["folder"] or ""
            f = folders.setdefault(folder, {"docs": [], "emails": set()})
            f["docs"].append({"did": r["did"], "name": r["name"]})
            au = nemail(r["author"])
            if au and "@" in au:
                f["emails"].add(au)

        ordered = sorted(folders.items(), key=lambda kv: -len(kv[1]["docs"]))[:TOP_N]

        aprint("entity_type values in relationship_entities: " + str(sorted(etypes)))
        aprint("SCHEMA DISCOVERY: email=" + str(sorted(set(disc["email"]))) + " phone="
               + str(sorted(set(disc["phone"]))) + " alias=" + str(sorted(set(disc["alias"])))
               + " link=" + str(sorted(set(disc["link"]))))
        aprint("TOTAL UNRESOLVED DOCUMENTS: " + str(total_docs) + " | TOTAL UNRESOLVED FOLDERS: "
               + str(len(folders)) + " | SHOWING TOP " + str(len(ordered)) + " BY DOC COUNT")

        report = []
        for folder, f in ordered:
            # One bad folder (odd encoding, unexpected evidence shape, etc.) must never terminate the
            # whole report: collect + render each folder inside try/except, print an explicit ERROR and
            # continue. Matching rules and output fields are unchanged.
            try:
                docs = f["docs"]
                # OWNER IDENTITY = the TOP-LEVEL TaxDome client folder only. Institution/payor words
                # occurring inside child paths or document FILENAMES must NEVER classify the owning
                # folder. Split on both separators and drop a leading "TaxDome" root segment.
                top = top_level_owner(folder)
                ntop = norm(top)

                # inst_token is derived from the FOLDER TOKEN identity ONLY (never filenames/child paths).
                inst_token = (ntop in inst_names) or any(re.search(r"\b" + re.escape(k) + r"\b", ntop) for k in INST_KW)
                name_pids = list(by_name.get(ntop, []))
                hh_hits = list(hh_by_name.get(ntop, []))
                biz_hit = biz_names.get(ntop)
                engine_sugg = []
                if suggest_people is not None and folder:
                    try:
                        engine_sugg = [{"id": x.get("id"), "name": x.get("full_name")}
                                       for x in (suggest_people(conn, folder) or [])][:5]
                    except Exception:  # noqa: BLE001
                        engine_sugg = []
                lk = link_owner.get(ntop) or link_owner.get(norm(folder))
                rhh = rper = None
                if resolve_folder is not None and folder:
                    try:
                        rhh, rper = resolve_folder(conn, folder)
                    except Exception:  # noqa: BLE001
                        rhh, rper = None, None

                folder_emails = sorted(f["emails"])
                email_pids = set()
                for ev in folder_emails:
                    email_pids |= email_to_pids.get(ev, set())
                alias_pids = alias_to_pids.get(ntop, set())
                corrob = email_pids | alias_pids  # independent-of-name corroborating person signals
                cand_ids = list(dict.fromkeys(name_pids + [p["id"] for p in engine_sugg if p["id"]] + sorted(corrob)))
                candidates = []
                for pid in cand_ids[:8]:
                    candidates.append({
                        "person_id": pid,
                        "name": pid_name.get(pid),
                        "household_id": pid_hh.get(pid),
                        "emails": sorted(pid_email.get(pid, set()))[:3],
                        "phones": sorted(pid_phone.get(pid, set()))[:2],
                        "match": ("name_exact" if pid in name_pids else "")
                                 + ("+email" if pid in email_pids else "")
                                 + ("+alias" if pid in alias_pids else "")
                                 + ("+engine" if pid in [p["id"] for p in engine_sugg] else ""),
                    })

                src_ev = []
                for d in docs[:5]:
                    for s in src_by_doc.get(d["did"], []):
                        src_ev.append(s)

                reject_docs = [d["did"] for d in docs if d["did"] in PERMANENT_REJECT]
                assignable_count = len([d for d in docs if d["did"] not in PERMANENT_REJECT])

                # Descriptive label -- FOLDER IDENTITY ONLY. Documents inside the folder (incl. permanent
                # rejects or institution-named files) never change the owning folder's label.
                if inst_token:
                    label = "INSTITUTION_OR_PAYOR"
                elif not ntop or ntop.isdigit() or any(k in ntop for k in JUNK_KW):
                    label = "TEST_JUNK"
                elif hh_hits or " and " in (" " + ntop + " ") or "&" in top:
                    label = "HOUSEHOLD"
                elif biz_hit or any(k in (" " + ntop + " ") for k in BIZ_KW):
                    label = "BUSINESS"
                elif name_pids or looks_like_person(ntop):
                    label = "PERSON"
                else:
                    label = "UNKNOWN"

                # Decision bucket from folder identity + existing corroborating evidence.
                etype = eid = ename = None
                evidence = []
                competing = None
                if lk is not None:
                    etype, eid = lk
                    ename = hh_name.get(eid) if etype == "household" else pid_name.get(eid)
                    evidence.append("explicit source-contact/identity linkage")
                    decision = "SAFE_AUTO_ASSIGN"
                    reason = "explicit source-contact/identity linkage maps this folder to one owner"
                elif rhh is not None:
                    etype, eid, ename = "household", rhh, hh_name.get(rhh)
                    evidence.append("resolve_folder -> household")
                    decision = "SAFE_AUTO_ASSIGN"
                    reason = "TaxDome folder resolves to one household (existing resolver)"
                elif rper is not None and not inst_token:
                    etype, eid, ename = "person", rper, pid_name.get(rper)
                    evidence.append("resolve_folder -> person")
                    decision = "SAFE_AUTO_ASSIGN"
                    reason = "TaxDome folder resolves to one person (existing resolver)"
                elif inst_token:
                    decision = "NO_MATCH"
                    reason = "folder identity is an institution/payor, not a client owner"
                elif len(hh_hits) == 1:
                    etype, eid, ename = "household", hh_hits[0], hh_name.get(hh_hits[0])
                    evidence.append("folder token == household name (exact)")
                    decision = "SAFE_AUTO_ASSIGN"
                    reason = "folder token exactly matches one household"
                elif len(name_pids) == 1 and name_pids[0] in corrob:
                    pid = name_pids[0]
                    etype, eid, ename = "person", pid, pid_name.get(pid)
                    evidence.append("unique canonical name == folder token")
                    evidence.append("corroborated by " + ("email" if pid in email_pids else "alias"))
                    decision = "SAFE_AUTO_ASSIGN"
                    reason = "unique canonical name matches folder AND is corroborated by an independent signal"
                elif len(name_pids) == 1:
                    pid = name_pids[0]
                    etype, eid, ename = "person", pid, pid_name.get(pid)
                    competing = [{"person_id": pid, "name": pid_name.get(pid)}]
                    decision = "MANUAL_REVIEW"
                    reason = ("unique canonical name matches folder but NO independent corroboration "
                              "(email/phone/alias/linkage) found; confirm before assigning")
                elif len(name_pids) > 1:
                    narrowed = [pid for pid in name_pids if pid in corrob]
                    if len(narrowed) == 1:
                        etype, eid, ename = "person", narrowed[0], pid_name.get(narrowed[0])
                        evidence.append("duplicate name disambiguated to one person by email/alias")
                        decision = "SAFE_AUTO_ASSIGN"
                        reason = "duplicate canonical name resolved to exactly one person by a corroborating signal"
                    else:
                        competing = [{"person_id": pid, "name": pid_name.get(pid),
                                      "household_id": pid_hh.get(pid),
                                      "emails": sorted(pid_email.get(pid, set()))[:2]} for pid in name_pids[:8]]
                        decision = "MANUAL_REVIEW"
                        reason = ("multiple same-name people; corroboration did not resolve to exactly one -- "
                                  "pick using SSN/DOB/address/email/linkage, never on name alone")
                elif biz_hit is not None:
                    etype, eid, ename = "organization", biz_hit[0], biz_hit[1]
                    competing = [{"organization_id": biz_hit[0], "name": biz_hit[1]}]
                    decision = "MANUAL_REVIEW"
                    reason = "folder token matches a business/organization/trust; confirm it is the client owner (not a payor)"
                elif len(corrob) == 1:
                    pid = next(iter(corrob))
                    etype, eid, ename = "person", pid, pid_name.get(pid)
                    competing = [{"person_id": pid, "name": pid_name.get(pid)}]
                    decision = "MANUAL_REVIEW"
                    reason = "only an email/alias match (no folder-name match); the author may be a preparer -- verify"
                elif engine_sugg:
                    competing = engine_sugg
                    decision = "MANUAL_REVIEW"
                    reason = "only fuzzy name suggestions; verify identity before assigning"
                else:
                    decision = "NO_MATCH"
                    reason = "no credible canonical owner from folder identity or existing evidence"

                # Joint routing: a person whose household has >=2 members present in the evidence -> household.
                if decision == "SAFE_AUTO_ASSIGN" and etype == "person" and eid is not None:
                    hh = pid_hh.get(eid)
                    if hh is not None and len(members.get(hh, set()) & (corrob | set(name_pids))) >= 2:
                        etype, eid, ename = "household", hh, hh_name.get(hh)
                        evidence.append("two co-household members present -> household")

                entry = {
                    "folder": folder,
                    "top_level_owner_token": top,
                    "unresolved_docs": len(docs),
                    "assignable_docs_excl_permanent_rejects": assignable_count,
                    "contains_permanent_reject_docs": reject_docs,
                    "decision": decision,
                    "label": label,
                    "proposed_entity_type": etype,
                    "proposed_entity_id": eid,
                    "proposed_entity_name": ename,
                    "corroborating_evidence": evidence,
                    "reason": reason,
                    "competing_candidates": competing,
                    "candidate_people": candidates,
                    "candidate_households": [{"id": h, "name": hh_name.get(h)} for h in hh_hits[:5]],
                    "candidate_business_org": ([{"id": biz_hit[0], "name": biz_hit[1]}] if biz_hit else []),
                    "engine_name_suggestions": engine_sugg,
                    "source_contact_linkage": ({"type": lk[0], "id": lk[1]} if lk else None),
                    "folder_author_emails": folder_emails[:5],
                    "document_source_evidence": src_ev[:5],
                    "sample_documents": [{"id": d["did"], "name": d["name"]} for d in docs[:5]],
                }
                report.append(entry)

                aprint("")
                aprint("FOLDER: " + top + "  (" + str(len(docs)) + " docs)  LABEL=" + label
                       + "  DECISION=" + decision)
                if etype is not None:
                    aprint("  proposed_owner: " + etype + " id=" + str(eid) + " '" + str(ename) + "'")
                aprint("  reason: " + reason)
                if evidence:
                    aprint("  corroborating_evidence: " + safe_dumps(evidence))
                if competing:
                    aprint("  competing_candidates: " + safe_dumps(competing))
                if reject_docs:
                    aprint("  contains_permanent_reject_docs (excluded from assignable count): " + safe_dumps(reject_docs))
                aprint("  candidate_people: " + safe_dumps(candidates))
                aprint("  candidate_households: " + safe_dumps(entry["candidate_households"]))
                aprint("  candidate_business_org: " + safe_dumps(entry["candidate_business_org"]))
                aprint("  source_contact_linkage: " + safe_dumps(entry["source_contact_linkage"]))
                aprint("  folder_author_emails: " + safe_dumps(folder_emails[:5]))
                aprint("  document_source_evidence: " + safe_dumps(src_ev[:5]))
                aprint("  sample_documents: " + safe_dumps(entry["sample_documents"]))
            except Exception as exc:  # noqa: BLE001 -- isolate one folder's failure; keep going
                aprint("")
                aprint("FOLDER: " + str(folder) + "  ERROR: " + repr(exc)
                       + "  -- evidence/render failed for this folder; skipped, continuing")
                report.append({"folder": folder, "error": repr(exc)})
                continue

    decision_counts = {"SAFE_AUTO_ASSIGN": 0, "MANUAL_REVIEW": 0, "NO_MATCH": 0, "ERROR": 0}
    safe_assignable_docs = 0
    for r in report:
        d = r.get("decision", "ERROR")
        decision_counts[d] = decision_counts.get(d, 0) + 1
        if d == "SAFE_AUTO_ASSIGN":
            safe_assignable_docs += r.get("assignable_docs_excl_permanent_rejects", 0)
    aprint("")
    aprint("DECISION SUMMARY (over " + str(len(report)) + " shown folders):")
    for k in ("SAFE_AUTO_ASSIGN", "MANUAL_REVIEW", "NO_MATCH", "ERROR"):
        aprint("  " + k + ": " + str(decision_counts[k]) + " folders")
    aprint("DOCUMENTS SAFELY ASSIGNABLE (SAFE_AUTO_ASSIGN, excl. permanent rejects): " + str(safe_assignable_docs))

    aprint("=== BEGIN_V4_REVIEW_JSON ===")
    print(safe_dumps({"total_unresolved_documents": total_docs, "total_unresolved_folders": len(folders),
                      "shown": len(report), "decision_counts": decision_counts,
                      "documents_safely_assignable": safe_assignable_docs, "folders": report}))
    aprint("=== END_V4_REVIEW_JSON ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
