"""READ-ONLY V4 folder-level identity resolution for unresolved TaxDome Drive documents.

SELECT-only. No INSERT/UPDATE/DELETE, no schema change, no migration, no ownership change,
no file move/delete/rename. Reuses the production app config (app.db engine/metadata) and the
existing deterministic resolver (app.importers.taxdome_drive.resolve_folder / suggest_people).

Goal: resolve each TaxDome SOURCE FOLDER once (rather than per document). Every unresolved
document in a folder shares the folder's owner, so a single confident folder decision makes all
of its documents assignable.

Evidence used per folder (whatever exists in production is discovered + used):
  - resolve_folder(): the existing migration/linkage resolver (authoritative)
  - canonical people (exact full-name == folder token) and households (name == folder token)
  - relationship_entities / businesses (exact name == folder token)
  - email evidence from document tags.author, matched to people via every email column found
  - alias / source-id / external-id evidence from any table carrying person_id + such a column
  - explicit source-contact / TaxDome identity linkage tables (person_id/household_id) if present
  - spouse / household membership (people.household_id) for joint-folder -> household routing

Safety rules (from the V2/V3 lessons):
  - The six V2 permanent rejects are never proposed and are excluded from safe counts.
  - Institution/payor names are matched against the FOLDER TOKEN only; an institution/payor name
    appearing inside a document FILENAME never overrides the folder owner.
  - Duplicate canonical names are NEVER split on name alone: a same-name folder is auto-assignable
    only if an independent corroborating signal (email / alias / explicit linkage) resolves it to
    exactly one person. Otherwise it is MANUAL_REVIEW.

Decision per folder: SAFE_AUTO_ASSIGN (high confidence) | MANUAL_REVIEW | NO_MATCH.

The script only reports. It writes nothing and creates no apply script.
"""
import json
import re
import sys

from sqlalchemy import func, select

from app.db import engine, metadata

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
EMAIL_HINTS = ("email", "e_mail", "mail")
ALIAS_HINTS = ("alias", "external_id", "source_id", "source_external_id", "identifier", "handle", "username")
LINK_TABLE_HINTS = ("source_contact", "contact", "taxdome", "identity", "linkage", "migration_link", "crosswalk")

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


def norm_email(s):
    return (s or "").strip().lower()


def main():
    discovery = {"email_sources": [], "alias_sources": [], "link_tables": []}
    with engine.connect() as conn:
        # provenance
        fps = conn.execute(
            select(dc.id).where(dc.id.in_(sorted(PERMANENT_REJECT)))
        ).scalars().all()
        print("PROVENANCE: found " + str(len(fps)) + "/" + str(len(PERMANENT_REJECT))
              + " V2 false-positive IDs (0 => NOT the expected production DB; verify before trusting output)")

        pred_list = [
            td_pred,
            dc.person_id.is_(None),
            dc.household_id.is_(None),
            dc.organization_id.is_(None),
        ]
        if "status" in dc:
            pred_list.append(dc.status != "deleted")
        total_docs = conn.execute(select(func.count()).select_from(documents).where(*pred_list)).scalar_one()

        folder_col = dc.tags["taxdome_folder"].astext
        rows = conn.execute(
            select(
                dc.id.label("document_id"),
                dc.original_name,
                folder_col.label("folder"),
                dc.tags["author"].astext.label("author"),
            )
            .where(*pred_list)
            .order_by(folder_col.nullsfirst(), dc.id)
        ).mappings().all()

        # canonical people + households
        ppl = conn.execute(
            select(
                people.c.id,
                people.c.full_name,
                (people.c.household_id if "household_id" in people.c else people.c.id).label("hh"),
            )
        ).mappings().all()
        by_name = {}
        for p in ppl:
            by_name.setdefault(norm(p["full_name"]), []).append(p["id"])
        pid_name = {p["id"]: p["full_name"] for p in ppl}
        pid_hh = {p["id"]: p["hh"] for p in ppl}
        members = {}
        for p in ppl:
            if p["hh"] is not None:
                members.setdefault(p["hh"], set()).add(p["id"])

        hh_name = {}
        hh_by_name = {}
        if households is not None:
            for h in conn.execute(select(households.c.id, households.c.name)).mappings():
                hh_name[h["id"]] = h["name"]
                hh_by_name.setdefault(norm(h["name"]), []).append(h["id"])

        inst_names = {}
        biz_names = {}
        etypes = set()
        if rel is not None:
            for e in conn.execute(select(rel.c.id, rel.c.name, rel.c.entity_type)).mappings():
                etypes.add(e["entity_type"])
                nm = norm(e["name"])
                t = (e["entity_type"] or "").lower()
                if any(k in t for k in ("instit", "payor", "payer", "employer", "bank", "gov", "school", "insur")):
                    inst_names[nm] = (e["id"], e["name"])
                else:
                    biz_names[nm] = (e["id"], e["name"])

        # --- generic discovery of email / alias / linkage evidence sources ---------------------
        email_to_pids = {}
        for col in people.c:
            if any(h in col.name.lower() for h in EMAIL_HINTS):
                discovery["email_sources"].append("people." + col.name)
                for r in conn.execute(select(people.c.id, col)).mappings():
                    ev = norm_email(r[col.name])
                    if ev and "@" in ev:
                        email_to_pids.setdefault(ev, set()).add(r["id"])

        alias_to_pids = {}
        link_folder_owner = {}  # normalized folder token -> ("person"/"household", id)
        for tname, tbl in metadata.tables.items():
            keys = set(tbl.c.keys())
            if "person_id" not in keys and "household_id" not in keys:
                continue
            low = tname.lower()
            # email columns on aux person tables
            if "person_id" in keys:
                for col in tbl.c:
                    if any(h in col.name.lower() for h in EMAIL_HINTS):
                        discovery["email_sources"].append(tname + "." + col.name)
                        try:
                            for r in conn.execute(select(tbl.c.person_id, col)).mappings():
                                ev = norm_email(r[col.name])
                                if ev and "@" in ev and r["person_id"] is not None:
                                    email_to_pids.setdefault(ev, set()).add(r["person_id"])
                        except Exception:  # noqa: BLE001
                            pass
                    if any(h in col.name.lower() for h in ALIAS_HINTS):
                        discovery["alias_sources"].append(tname + "." + col.name)
                        try:
                            for r in conn.execute(select(tbl.c.person_id, col)).mappings():
                                av = norm(str(r[col.name]) if r[col.name] is not None else "")
                                if av and r["person_id"] is not None:
                                    alias_to_pids.setdefault(av, set()).add(r["person_id"])
                        except Exception:  # noqa: BLE001
                            pass
            # explicit folder/account -> owner linkage tables
            if any(h in low for h in LINK_TABLE_HINTS):
                folder_like = [c for c in tbl.c
                               if any(k in c.name.lower() for k in ("folder", "account", "source_uri", "path", "name"))]
                if folder_like and ("person_id" in keys or "household_id" in keys):
                    discovery["link_tables"].append(tname)
                    try:
                        sel_cols = [tbl.c[c.name] for c in folder_like]
                        if "person_id" in keys:
                            sel_cols.append(tbl.c.person_id)
                        if "household_id" in keys:
                            sel_cols.append(tbl.c.household_id)
                        for r in conn.execute(select(*sel_cols)).mappings():
                            pid = r.get("person_id")
                            hid = r.get("household_id")
                            for c in folder_like:
                                key = norm(str(r[c.name]) if r[c.name] is not None else "")
                                if not key:
                                    continue
                                if hid is not None:
                                    link_folder_owner[key] = ("household", hid)
                                elif pid is not None:
                                    link_folder_owner[key] = ("person", pid)
                    except Exception:  # noqa: BLE001
                        pass

        # --- aggregate documents by folder -----------------------------------------------------
        folders = {}
        for r in rows:
            folder = r["folder"] or ""
            f = folders.setdefault(folder, {"doc_ids": [], "emails": set(), "names_in_docs": set()})
            f["doc_ids"].append(r["document_id"])
            au = norm_email(r["author"])
            if au and "@" in au:
                f["emails"].add(au)

        # --- classify each folder --------------------------------------------------------------
        report = []
        counts = {"SAFE_AUTO_ASSIGN": 0, "MANUAL_REVIEW": 0, "NO_MATCH": 0}
        safe_docs = 0
        fcache = {}

        for folder, f in folders.items():
            doc_ids = f["doc_ids"]
            non_reject_docs = [d for d in doc_ids if d not in PERMANENT_REJECT]
            top = folder.split("/", 1)[0].strip()
            ntop = norm(top)
            evidence = []

            inst_token = (ntop in inst_names) or any(re.search(r"\b" + re.escape(k) + r"\b", ntop) for k in INST_KW)

            if resolve_folder is not None and folder:
                if folder not in fcache:
                    try:
                        fcache[folder] = resolve_folder(conn, folder)
                    except Exception:  # noqa: BLE001
                        fcache[folder] = (None, None)
                rhh, rper = fcache[folder]
            else:
                rhh, rper = (None, None)

            name_pids = list(by_name.get(ntop, []))
            hh_hits = list(hh_by_name.get(ntop, []))
            biz_hit = biz_names.get(ntop)
            email_pids = set()
            for ev in f["emails"]:
                email_pids |= email_to_pids.get(ev, set())
            alias_pids = alias_to_pids.get(ntop, set())
            link_owner = link_folder_owner.get(ntop) or link_folder_owner.get(norm(folder))

            corrob = email_pids | alias_pids  # independent-of-name corroborating person signals

            decision = "NO_MATCH"
            confidence = "none"
            reason = "no deterministic owner"
            etype = eid = ename = None

            if link_owner is not None:
                etype, eid = link_owner
                ename = hh_name.get(eid) if etype == "household" else pid_name.get(eid)
                evidence.append("explicit_source_contact_linkage")
                decision, confidence, reason = "SAFE_AUTO_ASSIGN", "high", "explicit source-contact/identity linkage"
            elif rhh is not None:
                etype, eid, ename = "household", rhh, hh_name.get(rhh)
                evidence.append("resolve_folder:household")
                decision, confidence, reason = "SAFE_AUTO_ASSIGN", "high", "folder resolves to one household (existing resolver)"
            elif rper is not None and not inst_token:
                etype, eid, ename = "person", rper, pid_name.get(rper)
                evidence.append("resolve_folder:person")
                decision, confidence, reason = "SAFE_AUTO_ASSIGN", "high", "folder resolves to one person (existing resolver)"
            elif inst_token and not name_pids and not hh_hits:
                evidence.append("institution_folder_token")
                decision, confidence, reason = "NO_MATCH", "none", "folder token is an institution/payor, not an owner"
            elif hh_hits and len(hh_hits) == 1 and not inst_token:
                etype, eid, ename = "household", hh_hits[0], hh_name.get(hh_hits[0])
                evidence.append("household_name_exact")
                decision, confidence, reason = "SAFE_AUTO_ASSIGN", "high", "folder token == one household name (exact)"
            elif len(name_pids) == 1 and not inst_token:
                pid = name_pids[0]
                evidence.append("canonical_name_exact_unique")
                if corrob and pid in corrob:
                    etype, eid, ename = "person", pid, pid_name.get(pid)
                    evidence.append("corroborated_email_or_alias")
                    decision, confidence, reason = "SAFE_AUTO_ASSIGN", "high", "unique canonical name == folder, corroborated by email/alias"
                else:
                    etype, eid, ename = "person", pid, pid_name.get(pid)
                    decision, confidence, reason = "MANUAL_REVIEW", "medium", "unique canonical name == folder, no independent corroboration"
            elif len(name_pids) > 1 and not inst_token:
                # duplicate name: NEVER pick on name alone; require corroboration to a single person
                narrowed = [pid for pid in name_pids if pid in corrob]
                if len(narrowed) == 1:
                    etype, eid, ename = "person", narrowed[0], pid_name.get(narrowed[0])
                    evidence.append("duplicate_name_disambiguated_by_email_or_alias")
                    decision, confidence, reason = "SAFE_AUTO_ASSIGN", "high", "duplicate name disambiguated to one person by corroborating signal"
                else:
                    evidence.append("duplicate_name_no_disambiguation")
                    decision, confidence, reason = "MANUAL_REVIEW", "low", "multiple same-name people; corroboration did not resolve to one"
            elif biz_hit and not inst_token:
                etype, eid, ename = "organization", biz_hit[0], biz_hit[1]
                evidence.append("business_name_exact")
                decision, confidence, reason = "MANUAL_REVIEW", "medium", "folder token == business/company (verify it is the client, not a payor)"
            elif corrob and len(corrob) == 1:
                pid = next(iter(corrob))
                etype, eid, ename = "person", pid, pid_name.get(pid)
                evidence.append("email_or_alias_only_single")
                decision, confidence, reason = "MANUAL_REVIEW", "low", "single email/alias match but no folder-name match (author may be preparer)"

            # joint routing: if the folder clearly names two co-household members, prefer household
            if decision == "SAFE_AUTO_ASSIGN" and etype == "person" and eid is not None:
                hh = pid_hh.get(eid)
                if hh is not None and len(members.get(hh, set()) & (corrob | set(name_pids))) >= 2:
                    etype, eid, ename = "household", hh, hh_name.get(hh)
                    evidence.append("joint_two_members->household")
                    reason = reason + " (joint -> household)"

            competing = None
            if decision != "SAFE_AUTO_ASSIGN" and len(name_pids) > 1:
                competing = [{"person_id": pid, "name": pid_name.get(pid)} for pid in name_pids[:6]]

            counts[decision] += 1
            if decision == "SAFE_AUTO_ASSIGN":
                safe_docs += len(non_reject_docs)

            report.append({
                "folder": folder,
                "unresolved_docs": len(doc_ids),
                "assignable_docs_excl_rejects": len(non_reject_docs),
                "proposed_entity_type": etype,
                "proposed_entity_id": eid,
                "proposed_entity_name": ename,
                "evidence": evidence,
                "confidence": confidence,
                "reason": reason,
                "decision": decision,
                "competing": competing,
                "sample_doc_ids": doc_ids[:5],
            })

    report.sort(key=lambda x: ({"SAFE_AUTO_ASSIGN": 0, "MANUAL_REVIEW": 1, "NO_MATCH": 2}[x["decision"]],
                               -x["unresolved_docs"]))

    print("entity_type values in relationship_entities: " + str(sorted(etypes)))
    print("SCHEMA DISCOVERY:")
    print("  email_sources: " + str(sorted(set(discovery["email_sources"]))))
    print("  alias_sources: " + str(sorted(set(discovery["alias_sources"]))))
    print("  link_tables:   " + str(sorted(set(discovery["link_tables"]))))
    print("TOTAL UNRESOLVED DOCUMENTS: " + str(total_docs))
    print("TOTAL UNRESOLVED FOLDERS: " + str(len(folders)))
    print("FOLDER DECISION COUNTS:")
    for k in ("SAFE_AUTO_ASSIGN", "MANUAL_REVIEW", "NO_MATCH"):
        print("  " + k + ": " + str(counts[k]) + " folders")
    print("DOCUMENTS THAT WOULD BECOME SAFELY ASSIGNABLE (excl. permanent rejects): " + str(safe_docs))

    out = {
        "total_unresolved_documents": total_docs,
        "total_unresolved_folders": len(folders),
        "folder_decision_counts": counts,
        "documents_safely_assignable": safe_docs,
        "schema_discovery": {k: sorted(set(v)) for k, v in discovery.items()},
        "safe_auto_assign": [r for r in report if r["decision"] == "SAFE_AUTO_ASSIGN"][:500],
        "manual_review": [r for r in report if r["decision"] == "MANUAL_REVIEW"][:500],
        "no_match_rollup": [
            {"folder": r["folder"], "unresolved_docs": r["unresolved_docs"], "reason": r["reason"]}
            for r in report if r["decision"] == "NO_MATCH"
        ][:600],
    }
    print("=== BEGIN_V4_JSON ===")
    print(json.dumps(out, ensure_ascii=False, default=str))
    print("=== END_V4_JSON ===")
    return 0


sys.exit(main())
