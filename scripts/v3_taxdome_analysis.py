"""READ-ONLY V3 analysis of unresolved TaxDome Drive documents (production).

SELECT-only. No INSERT/UPDATE/DELETE, no schema change, no migration, no ownership change,
no file move/delete/rename. Reuses the production app config (app.db engine/metadata) and the
SAME deterministic resolver the V2 apply used (app.importers.taxdome_drive.resolve_folder).

Classifies each unresolved document into exactly one of:
  A SAFE_PERSON               B SAFE_HOUSEHOLD            C SAFE_BUSINESS_OR_ORGANIZATION
  D AMBIGUOUS_MANUAL_REVIEW   E INSTITUTION_OR_PAYOR_NOT_OWNER   F NO_MATCH

Guards baked in (from V2 lessons):
  - Institution/payor names (from relationship_entities + a conservative keyword list) never
    become a person/org owner; with no owning folder they classify as E.
  - Joint/spouse documents (two co-household members present) never classify as SAFE_PERSON.
  - A name merely appearing in a filename is evidence/competing only, never an assignment basis.
  - The six V2 permanent-reject document IDs are hard-forced to E and never proposed.

Run on production at C:\\Client360 via the app venv; it prints a compact report + a JSON block.
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

try:
    from app.importers.taxdome_drive import resolve_folder, taxdome_filter
    td_pred = taxdome_filter(documents)
except Exception as exc:  # noqa: BLE001
    print("NOTE: taxdome resolver import failed (" + str(exc) + "); using tag predicate + no folder resolution")
    resolve_folder = None
    td_pred = dc.tags["source_system"].astext == "TaxDome Drive"


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def main():
    with engine.connect() as conn:
        fps = conn.execute(
            select(dc.id, dc.person_id, dc.household_id, dc.organization_id)
            .where(dc.id.in_(sorted(PERMANENT_REJECT)))
        ).mappings().all()
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
        total = conn.execute(select(func.count()).select_from(documents).where(*pred_list)).scalar_one()

        folder_col = dc.tags["taxdome_folder"].astext
        rows = conn.execute(
            select(
                dc.id.label("document_id"),
                dc.original_name,
                folder_col.label("folder"),
                (dc.storage_path if "storage_path" in dc else dc.id).label("storage_path"),
                dc.tags["author"].astext.label("author"),
            )
            .where(*pred_list)
            .order_by(folder_col.nullsfirst(), dc.id)
        ).mappings().all()

        ppl = conn.execute(
            select(
                people.c.id,
                people.c.full_name,
                (people.c.household_id if "household_id" in people.c else people.c.id).label("hh"),
            )
        ).mappings().all()
        by_name = {}
        for p in ppl:
            by_name.setdefault(norm(p["full_name"]), []).append(p)
        pid_name = {p["id"]: p["full_name"] for p in ppl}
        members = {}
        if "household_id" in people.c:
            for p in ppl:
                if p["hh"] is not None:
                    members.setdefault(p["hh"], set()).add(p["id"])

        hh_name = {}
        if households is not None:
            for h in conn.execute(select(households.c.id, households.c.name)).mappings():
                hh_name[h["id"]] = h["name"]

        inst_names = {}
        biz_names = {}
        etypes = set()
        if rel is not None:
            for e in conn.execute(select(rel.c.id, rel.c.name, rel.c.entity_type)).mappings():
                etypes.add(e["entity_type"])
                nm = norm(e["name"])
                t = (e["entity_type"] or "").lower()
                if any(k in t for k in ("instit", "payor", "payer", "employer", "bank", "gov", "school", "insur")):
                    inst_names[nm] = (e["id"], e["name"], e["entity_type"])
                else:
                    biz_names[nm] = (e["id"], e["name"], e["entity_type"])

        src = {}
        if doc_sources is not None:
            for s in conn.execute(
                select(doc_sources.c.document_id, doc_sources.c.source_system, doc_sources.c.source_path)
            ).mappings():
                src.setdefault(s["document_id"], []).append({"sys": s["source_system"], "path": s["source_path"]})

        fcache = {}
        safe = []
        ambiguous = []
        rollup = {}
        counts = {
            "A_SAFE_PERSON": 0,
            "B_SAFE_HOUSEHOLD": 0,
            "C_SAFE_BUSINESS_OR_ORGANIZATION": 0,
            "D_AMBIGUOUS_MANUAL_REVIEW": 0,
            "E_INSTITUTION_OR_PAYOR_NOT_OWNER": 0,
            "F_NO_MATCH": 0,
        }

        for r in rows:
            did = r["document_id"]
            folder = r["folder"] or ""
            top = folder.split("/", 1)[0].strip()
            ntop = norm(top)
            hay = ntop + " " + norm(r["original_name"])

            if resolve_folder is not None and folder:
                if folder not in fcache:
                    try:
                        fcache[folder] = resolve_folder(conn, folder)
                    except Exception:  # noqa: BLE001
                        fcache[folder] = (None, None)
                rhh, rper = fcache[folder]
            else:
                rhh, rper = (None, None)

            inst_hit = (ntop in inst_names) \
                or any(re.search(r"\b" + re.escape(k) + r"\b", hay) for k in INST_KW) \
                or any(re.search(r"\b" + re.escape(nm) + r"\b", hay) for nm in inst_names)
            biz_exact = biz_names.get(ntop)
            exact_people = by_name.get(ntop, [])
            in_str = {
                p["id"]
                for plist in by_name.values()
                for p in plist
                if norm(pid_name[p["id"]]) and re.search(r"\b" + re.escape(norm(pid_name[p["id"]])) + r"\b", hay)
            }
            joint = any(len(in_str & m) >= 2 for m in members.values()) if len(in_str) >= 2 else False

            if did in PERMANENT_REJECT:
                cls = "E_INSTITUTION_OR_PAYOR_NOT_OWNER"
                why = "V2 permanent reject"
            elif rhh and not (inst_hit and not rper):
                cls = "B_SAFE_HOUSEHOLD"
                why = "joint->household" if joint else "folder resolves to one household"
                safe.append({
                    "document_id": did,
                    "class": cls,
                    "entity_type": "household",
                    "entity_id": rhh,
                    "entity_name": hh_name.get(rhh),
                    "folder": folder,
                    "reason": why,
                    "household_members": sorted(members.get(rhh, []))[:8],
                })
            elif rper and not inst_hit and not joint and len(exact_people) <= 1:
                cls = "A_SAFE_PERSON"
                why = "folder resolves to one unambiguous person"
                safe.append({
                    "document_id": did,
                    "class": cls,
                    "entity_type": "person",
                    "entity_id": rper,
                    "entity_name": pid_name.get(rper),
                    "folder": folder,
                    "reason": why,
                })
            elif biz_exact and not inst_hit and not rper and not rhh:
                cls = "C_SAFE_BUSINESS_OR_ORGANIZATION"
                why = "folder token == business/company client (exact)"
                safe.append({
                    "document_id": did,
                    "class": cls,
                    "entity_type": "organization",
                    "entity_id": biz_exact[0],
                    "entity_name": biz_exact[1],
                    "folder": folder,
                    "reason": why,
                })
            elif joint or len(exact_people) > 1 or (exact_people and inst_hit) or (rper and inst_hit):
                cls = "D_AMBIGUOUS_MANUAL_REVIEW"
                why = "competing/mixed evidence"
                comp = [{"person_id": p["id"], "name": pid_name[p["id"]]} for p in exact_people][:5]
                if inst_hit and ntop in inst_names:
                    comp.append({"institution": inst_names[ntop][1]})
                ambiguous.append({
                    "document_id": did,
                    "folder": folder,
                    "reason": why,
                    "people_in_string": sorted(in_str)[:6],
                    "competing": comp,
                    "resolve_folder": {"hh": rhh, "person": rper},
                })
            elif inst_hit:
                cls = "E_INSTITUTION_OR_PAYOR_NOT_OWNER"
                why = "institution/payor name dominant, no owner folder"
            else:
                cls = "F_NO_MATCH"
                why = "no deterministic owner"

            counts[cls] += 1
            if cls in ("E_INSTITUTION_OR_PAYOR_NOT_OWNER", "F_NO_MATCH"):
                key = (cls, folder)
                b = rollup.setdefault(key, {"class": cls, "folder": folder, "count": 0, "sample_ids": []})
                b["count"] += 1
                if len(b["sample_ids"]) < 3:
                    b["sample_ids"].append(did)

    print("entity_type values in relationship_entities: " + str(sorted(etypes)))
    print("TOTAL UNRESOLVED: " + str(total))
    print("CLASSIFICATION COUNTS:")
    for k in ("A_SAFE_PERSON", "B_SAFE_HOUSEHOLD", "C_SAFE_BUSINESS_OR_ORGANIZATION",
              "D_AMBIGUOUS_MANUAL_REVIEW", "E_INSTITUTION_OR_PAYOR_NOT_OWNER", "F_NO_MATCH"):
        print("  " + k + ": " + str(counts[k]))
    out = {
        "total_unresolved": total,
        "counts": counts,
        "safe_candidates_A_B_C": safe[:500],
        "ambiguous_D": ambiguous[:500],
        "institution_and_nomatch_rollup_E_F": sorted(
            rollup.values(), key=lambda x: (x["class"], -x["count"])
        )[:400],
    }
    print("=== BEGIN_V3_JSON ===")
    print(json.dumps(out, ensure_ascii=False, default=str))
    print("=== END_V3_JSON ===")
    return 0


sys.exit(main())
