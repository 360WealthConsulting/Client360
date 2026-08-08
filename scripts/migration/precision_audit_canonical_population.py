"""READ-ONLY precision audit of the canonical-population remediation.

Where the canonical-population preview reports COUNTS, this audit ENUMERATES the exact records behind each
category so a human can review precisely what a repair would touch before any apply: every proposed safe
promotion, existing-person link, business/trust candidate, ambiguous identity, and unresolved contact;
the exact duplicate canonical-people groups (with member ids + names); the household candidates (with both
member ids); and the exact linkage folders that become resolvable after the repair.

It reuses the canonical-population classifier (single source of truth) plus the repository name matchers,
and reads the linkage preview's exceptions.csv for folder/household enumeration.

STRICTLY READ-ONLY: SELECT only (+ session read-only). No inserts/updates/deletes/merges, no entity or
household creation, no document linkage, no storage_uri/document_sources changes, no apply. It may write
report CSVs to an output directory (report artifacts only — never production data).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

from app.importers.taxdome_drive import _folder_person_keys, _name_key
from app.services.migration.canonical_population import classify_contact
from scripts.migration.analyze_linkage_exceptions import read_exception_folders

ACTION_ORDER = ("safe_person_promotion", "existing_person_link", "business_company_candidate",
                "ambiguous_identity", "unresolved")


def contact_detail(sc, people_by_email, people_by_phone, email_counts, phone_counts, person_name):
    """Enumerate one unlinked source_contact with its action + candidate detail."""
    action, target, evidence, ambiguity = classify_contact(
        sc, people_by_email, people_by_phone, email_counts, phone_counts)
    ne, np = sc.get("normalized_email"), sc.get("normalized_phone")
    cand = sorted(set(people_by_email.get(ne, ())) | set(people_by_phone.get(np, ()))) if (ne or np) else []
    target_name = person_name.get(cand[0], "") if (action == "existing_person_link" and len(cand) == 1) else ""
    return {
        "source_contact_id": sc["id"], "source_system": sc["source_system"],
        "source_record_id": sc.get("source_record_id") or "", "source_name": sc.get("full_name") or "",
        "proposed_action": action, "proposed_canonical_target": target, "target_name": target_name,
        "evidence": evidence, "ambiguity_reason": ambiguity,
        "candidate_person_ids": ";".join(map(str, cand)),
        "candidate_names": "; ".join(person_name.get(i, "") for i in cand),
    }


def duplicate_groups(people_by_name, people_by_email, people_by_phone, person_name):
    """Enumerate canonical-people collisions (report only; nothing is merged)."""
    groups = []
    for kind, idx in (("name", people_by_name), ("email", people_by_email), ("phone", people_by_phone)):
        for key, ids in idx.items():
            if len(ids) > 1:
                members = sorted(set(ids))
                groups.append({"collision_type": kind, "key": key, "size": len(members),
                               "member_person_ids": ";".join(map(str, members)),
                               "member_names": "; ".join(person_name.get(i, "") for i in members)})
    groups.sort(key=lambda g: (-g["size"], g["collision_type"]))
    return groups


def household_candidates(folders, people_by_name, person_name):
    """Joint exception folders whose BOTH members are exactly one canonical person (deterministic)."""
    out = []
    for folder in folders:
        member_keys = _folder_person_keys(folder)
        if len(member_keys) <= 1:
            continue
        if all(len(people_by_name.get(k, [])) == 1 for k in member_keys):
            ids = [people_by_name[k][0] for k in member_keys]
            out.append({"folder": folder, "member_person_ids": ";".join(map(str, ids)),
                        "member_names": "; ".join(person_name.get(i, "") for i in ids)})
    return out


def resolvable_folders(folders, unique_existing, promotable_unique, business_names, people_by_name):
    """The exact linkage folders that become resolvable after the repair, with the resolution path."""
    out = []
    for folder in folders:
        fk = _name_key(folder)
        member_keys = _folder_person_keys(folder)
        joint = len(member_keys) > 1
        if joint:
            if all(len(people_by_name.get(k, [])) == 1 for k in member_keys):
                out.append({"folder": folder, "path": "household_derivation", "target": "household:new"})
            continue
        if fk and fk not in unique_existing and fk in promotable_unique:
            out.append({"folder": folder, "path": "person_promotion_or_link", "target": "person"})
        elif fk and fk in business_names:
            out.append({"folder": folder, "path": "business_canonicalization",
                        "target": "relationship_entities:business"})
    return out


def _load(engine):
    from sqlalchemy import select, text

    from app.db import metadata
    source_contacts = metadata.tables["source_contacts"]
    person_source_links = metadata.tables["person_source_links"]
    people = metadata.tables["people"]
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        linked = set(conn.execute(select(person_source_links.c.source_contact_id)).scalars())
        scs = [dict(m) for m in conn.execute(select(
            source_contacts.c.id, source_contacts.c.source_system, source_contacts.c.source_record_id,
            source_contacts.c.full_name, source_contacts.c.normalized_email,
            source_contacts.c.normalized_phone, source_contacts.c.raw_data)).mappings()]
        ppl = [dict(m) for m in conn.execute(select(
            people.c.id, people.c.full_name, people.c.normalized_email,
            people.c.normalized_phone)).mappings()]
    return linked, scs, ppl


def audit(preview_dir, engine=None, out_dir=None):
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    from collections import Counter

    linked, scs, ppl = _load(engine)
    people_by_email: dict = defaultdict(list)
    people_by_phone: dict = defaultdict(list)
    people_by_name: dict = defaultdict(list)
    person_name: dict = {}
    for p in ppl:
        person_name[p["id"]] = p["full_name"] or ""
        if p["normalized_email"]:
            people_by_email[p["normalized_email"]].append(p["id"])
        if p["normalized_phone"]:
            people_by_phone[p["normalized_phone"]].append(p["id"])
        k = _name_key(p["full_name"])
        if k:
            people_by_name[k].append(p["id"])

    unlinked = [s for s in scs if s["id"] not in linked]
    email_counts = Counter(s["normalized_email"] for s in unlinked if s["normalized_email"])
    phone_counts = Counter(s["normalized_phone"] for s in unlinked if s["normalized_phone"])

    details = [contact_detail(s, people_by_email, people_by_phone, email_counts, phone_counts, person_name)
               for s in unlinked]
    by_action: dict = defaultdict(list)
    for d in details:
        by_action[d["proposed_action"]].append(d)
    post_person_names: dict = defaultdict(int)
    business_names: set = set()
    for d in details:
        nk = _name_key(d["source_name"])
        if not nk:
            continue
        if d["proposed_action"] in ("safe_person_promotion", "existing_person_link"):
            post_person_names[nk] += 1
        elif d["proposed_action"] == "business_company_candidate":
            business_names.add(nk)

    dups = duplicate_groups(people_by_name, people_by_email, people_by_phone, person_name)

    folders = read_exception_folders(preview_dir)
    unique_existing = {k for k, v in people_by_name.items() if len(v) == 1}
    promotable_unique = {k for k, v in post_person_names.items() if v == 1}
    households = household_candidates(folders, people_by_name, person_name)
    resolvable = resolvable_folders(folders, unique_existing, promotable_unique, business_names, people_by_name)

    result = {
        "counts": {a: len(by_action.get(a, [])) for a in ACTION_ORDER}
        | {"linked": len(linked), "unlinked": len(unlinked),
           "duplicate_groups": len(dups), "household_candidates": len(households),
           "exception_folders": len(folders), "resolvable_folders": len(resolvable)},
        "by_action": by_action, "duplicate_groups": dups,
        "household_candidates": households, "resolvable_folders": resolvable,
    }
    if out_dir:
        _write_csvs(out_dir, result)
    return result


def _write_csvs(out_dir, result) -> None:
    os.makedirs(out_dir, exist_ok=True)

    def dump(name, rows, fields):
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    contact_fields = ["source_contact_id", "source_system", "source_record_id", "source_name",
                      "proposed_action", "proposed_canonical_target", "target_name", "evidence",
                      "ambiguity_reason", "candidate_person_ids", "candidate_names"]
    dump("precision_contacts.csv", [d for a in ACTION_ORDER for d in result["by_action"].get(a, [])],
         contact_fields)
    dump("precision_duplicate_groups.csv", result["duplicate_groups"],
         ["collision_type", "key", "size", "member_person_ids", "member_names"])
    dump("precision_household_candidates.csv", result["household_candidates"],
         ["folder", "member_person_ids", "member_names"])
    dump("precision_resolvable_folders.csv", result["resolvable_folders"], ["folder", "path", "target"])


def _print(result, samples=15) -> None:
    print("=== counts ===")
    for k, v in result["counts"].items():
        print(f"  {k}: {v}")
    for action in ACTION_ORDER:
        rows = result["by_action"].get(action, [])
        print(f"\n[{action}] {len(rows)} (showing up to {samples})")
        for r in rows[:samples]:
            extra = f" -> {r['target_name']}" if r["target_name"] else (
                f"  candidates: {r['candidate_names']}" if r["candidate_names"] else "")
            print(f"   #{r['source_contact_id']} {r['source_system']}  {r['source_name']}"
                  f"  [{r['proposed_canonical_target']}]{extra}")
    print(f"\n=== duplicate canonical-people groups: {len(result['duplicate_groups'])} "
          "(showing up to 15) ===")
    for g in result["duplicate_groups"][:15]:
        print(f"   {g['collision_type']} '{g['key']}' x{g['size']}: {g['member_names']}")
    print(f"\n=== household candidates: {len(result['household_candidates'])} ===")
    for h in result["household_candidates"]:
        print(f"   {h['folder']}  ->  {h['member_names']} ({h['member_person_ids']})")
    print(f"\n=== resolvable linkage folders: {len(result['resolvable_folders'])} ===")
    for r in result["resolvable_folders"]:
        print(f"   {r['folder']}  ->  {r['path']}  [{r['target']}]")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.precision_audit_canonical_population",
                                description="Read-only precision audit of canonical-population remediation.")
    p.add_argument("preview_dir", help="Linkage preview directory (with exceptions.csv).")
    p.add_argument("--out", default=None, help="Optional directory to write precision report CSVs.")
    args = p.parse_args(argv)
    if not os.path.isfile(os.path.join(args.preview_dir, "exceptions.csv")):
        print(f"ERROR: no exceptions.csv in {args.preview_dir}")
        return 2
    _print(audit(args.preview_dir, out_dir=args.out))
    print("\nPrecision audit complete (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
