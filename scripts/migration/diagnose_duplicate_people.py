"""READ-ONLY diagnosis of the canonical-people duplicate defect + link validation.

The precision audit found canonical-people duplicate groups in exact multiples (e.g. x26, x52). This
traces each duplicate group back through person_source_links -> source_contacts (source_system,
source_record_id, source_hash, source_file, imported_at) and the promotion match_method, to determine:
  * why the x26/x52 pattern exists (repeated source records -> name-only auto_promote -> a new person
    each time), and which import/promotion operation created them;
  * whether each group is a TRUE duplicate of one canonical person (deterministic evidence) or legitimate
    distinct records;
  * how many of the canonical people are EXCESS duplicates, and how many are deterministically collapsible;
  * the exact email/phone identity behind each proposed existing_person_link (to catch spouse/household
    shared-identity false links, e.g. Betty Philips -> PHILIPS, WILLIAM);
  * whether the safe_person_promotion set stays safe after duplicate cleanup.

Deterministic safe-collapse rule: a duplicate group whose members ALL trace to the same
(source_system, source_record_id) are the same underlying record re-promoted -> collapsible to one
canonical person. Groups without that evidence are left for review (never collapsed on name alone).

STRICTLY READ-ONLY: SELECT only (+ session read-only). No updates/merges/deletes of people, source
contacts, links, households, businesses, documents, or files. No apply.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from app.importers.taxdome_drive import _name_key
from app.services.migration.canonical_population import classify_contact


def _mask_email(v):
    if not v or "@" not in v:
        return v or ""
    local, _, dom = v.partition("@")
    return f"{local[:2]}***@{dom}"


def _mask_phone(v):
    return f"***{v[-4:]}" if v and len(v) >= 4 else (v or "")


def classify_name_group(record_keys, match_methods, size):
    """record_keys: set of (source_system, source_record_id) across the group's linked source_contacts."""
    known = {rk for rk in record_keys if rk[1]}                      # record_id present
    if size > 1 and len(known) == 1:
        return "same_source_record_reimport"                        # deterministically collapsible
    if match_methods and match_methods <= {"auto_promote"}:
        return "name_only_promotion"                                # name-only new-person each time
    return "mixed_review"


def _load(engine):
    from sqlalchemy import select, text

    from app.db import metadata
    people = metadata.tables["people"]
    psl = metadata.tables["person_source_links"]
    sc = metadata.tables["source_contacts"]
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        ppl = [dict(m) for m in conn.execute(select(
            people.c.id, people.c.full_name, people.c.normalized_email, people.c.normalized_phone,
            people.c.created_at)).mappings()]
        links = [dict(m) for m in conn.execute(select(
            psl.c.person_id, psl.c.source_contact_id, psl.c.match_method, psl.c.created_at)).mappings()]
        scs = {m["id"]: dict(m) for m in conn.execute(select(
            sc.c.id, sc.c.source_system, sc.c.source_record_id, sc.c.source_hash, sc.c.source_file,
            sc.c.imported_at, sc.c.normalized_email, sc.c.normalized_phone)).mappings()}
    return ppl, links, scs


def diagnose(engine=None):
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    ppl, links, scs = _load(engine)

    links_by_person: dict = defaultdict(list)
    for lk in links:
        links_by_person[lk["person_id"]].append(lk)

    # indexes
    by_name: dict = defaultdict(list)
    by_email: dict = defaultdict(list)
    by_phone: dict = defaultdict(list)
    person_name: dict = {}
    for p in ppl:
        person_name[p["id"]] = p["full_name"] or ""
        k = _name_key(p["full_name"])
        if k:
            by_name[k].append(p["id"])
        if p["normalized_email"]:
            by_email[p["normalized_email"]].append(p["id"])
        if p["normalized_phone"]:
            by_phone[p["normalized_phone"]].append(p["id"])

    # trace duplicate NAME groups
    name_groups = []
    class_counts: Counter = Counter()
    collapsible_excess = review_excess = 0
    for key, ids in by_name.items():
        if len(ids) <= 1:
            continue
        record_keys, match_methods, systems, files = set(), set(), set(), set()
        imported = []
        for pid in ids:
            for lk in links_by_person.get(pid, []):
                match_methods.add(lk["match_method"])
                s = scs.get(lk["source_contact_id"])
                if s:
                    record_keys.add((s["source_system"], s["source_record_id"]))
                    systems.add(s["source_system"])
                    files.add(s["source_file"])
                    if s["imported_at"]:
                        imported.append(s["imported_at"])
        cls = classify_name_group(record_keys, match_methods, len(ids))
        class_counts[cls] += 1
        if cls == "same_source_record_reimport":
            collapsible_excess += len(ids) - 1
        else:
            review_excess += len(ids) - 1
        name_groups.append({
            "name_key": key, "size": len(ids), "classification": cls,
            "member_person_ids": ids, "member_names": sorted({person_name[i] for i in ids}),
            "source_systems": sorted(systems), "match_methods": sorted(match_methods),
            "distinct_source_record_keys": len(record_keys), "distinct_source_files": len(files),
            "imported_at_min": min(imported).isoformat() if imported else "",
            "imported_at_max": max(imported).isoformat() if imported else "",
        })
    name_groups.sort(key=lambda g: -g["size"])

    # email / phone shared-identity groups (spouse/household collisions)
    def shared_groups(idx, kind):
        out = []
        for key, ids in idx.items():
            if len(ids) > 1:
                out.append({"kind": kind, "value": _mask_email(key) if kind == "email" else _mask_phone(key),
                            "size": len(ids), "member_person_ids": sorted(set(ids)),
                            "member_names": sorted({person_name[i] for i in set(ids)})})
        out.sort(key=lambda g: -g["size"])
        return out

    email_groups = shared_groups(by_email, "email")
    phone_groups = shared_groups(by_phone, "phone")

    return {
        "people_total": len(ppl),
        "duplicate_name_groups": len(name_groups),
        "name_group_classifications": dict(class_counts),
        "deterministically_collapsible_excess": collapsible_excess,
        "needs_review_excess": review_excess,
        "duplicate_email_groups": len(email_groups),
        "duplicate_phone_groups": len(phone_groups),
        "name_groups": name_groups, "email_groups": email_groups, "phone_groups": phone_groups,
        "_indexes": (by_email, by_phone, person_name),
    }


def validate_links(engine=None):
    """Recompute existing_person_link proposals and expose the exact email/phone identity behind each,
    flagging spouse/household shared-identity false links."""
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    from sqlalchemy import select, text

    from app.db import metadata
    sc = metadata.tables["source_contacts"]
    psl = metadata.tables["person_source_links"]
    people = metadata.tables["people"]
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        linked = set(conn.execute(select(psl.c.source_contact_id)).scalars())
        scs = [dict(m) for m in conn.execute(select(
            sc.c.id, sc.c.source_system, sc.c.full_name, sc.c.first_name, sc.c.last_name,
            sc.c.normalized_email, sc.c.normalized_phone, sc.c.raw_data)).mappings()]
        ppl = [dict(m) for m in conn.execute(select(
            people.c.id, people.c.full_name, people.c.last_name, people.c.first_name,
            people.c.normalized_email, people.c.normalized_phone)).mappings()]

    by_email: dict = defaultdict(list)
    by_phone: dict = defaultdict(list)
    prow: dict = {}
    for p in ppl:
        prow[p["id"]] = p
        if p["normalized_email"]:
            by_email[p["normalized_email"]].append(p["id"])
        if p["normalized_phone"]:
            by_phone[p["normalized_phone"]].append(p["id"])
    unlinked = [s for s in scs if s["id"] not in linked]
    email_counts = Counter(s["normalized_email"] for s in unlinked if s["normalized_email"])
    phone_counts = Counter(s["normalized_phone"] for s in unlinked if s["normalized_phone"])

    out = []
    for s in unlinked:
        action, target, _e, _a = classify_contact(s, by_email, by_phone, email_counts, phone_counts)
        if action != "existing_person_link":
            continue
        ne, np = s["normalized_email"], s["normalized_phone"]
        if ne and by_email.get(ne):
            matched_on, value, pid, share = "email", _mask_email(ne), by_email[ne][0], email_counts[ne]
        else:
            matched_on, value, pid, share = "phone", _mask_phone(np), by_phone[np][0], phone_counts[np]
        tp = prow[pid]
        c_last, c_first = (s.get("last_name") or "").strip().lower(), (s.get("first_name") or "").strip().lower()
        t_last, t_first = (tp.get("last_name") or "").strip().lower(), (tp.get("first_name") or "").strip().lower()
        same_last = (c_last == t_last) if (c_last and t_last) else None
        same_first = (c_first == t_first) if (c_first and t_first) else None
        # Verdict uses the SHARED plausibility rule (single source of truth) so the diagnostic and the
        # repair planner can never diverge.
        from app.services.migration.canonical_repair import plausible_link
        verdict = "plausible" if plausible_link(
            s.get("first_name"), s.get("last_name"), tp.get("first_name"), tp.get("last_name"), share
        ) else "suspect_household_shared"
        out.append({
            "source_contact_id": s["id"], "contact_name": s["full_name"], "target_person_id": pid,
            "target_person_name": tp["full_name"], "matched_on": matched_on, "identity_value": value,
            "contacts_sharing_identity": share, "same_last_name": same_last, "same_first_name": same_first,
            "verdict": verdict,
        })
    return out


def revalidate_promotions(engine=None):
    """Recompute safe_person_promotion and confirm each still has zero canonical candidate (incl.
    duplicates) — i.e. duplicate cleanup would not change its safety."""
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    from sqlalchemy import select, text

    from app.db import metadata
    sc = metadata.tables["source_contacts"]
    psl = metadata.tables["person_source_links"]
    people = metadata.tables["people"]
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        linked = set(conn.execute(select(psl.c.source_contact_id)).scalars())
        scs = [dict(m) for m in conn.execute(select(
            sc.c.id, sc.c.source_system, sc.c.full_name, sc.c.normalized_email,
            sc.c.normalized_phone, sc.c.raw_data)).mappings()]
        ppl = [dict(m) for m in conn.execute(select(
            people.c.normalized_email, people.c.normalized_phone)).mappings()]
    by_email, by_phone = defaultdict(list), defaultdict(list)
    for p in ppl:
        if p["normalized_email"]:
            by_email[p["normalized_email"]].append(1)
        if p["normalized_phone"]:
            by_phone[p["normalized_phone"]].append(1)
    unlinked = [s for s in scs if s["id"] not in linked]
    email_counts = Counter(s["normalized_email"] for s in unlinked if s["normalized_email"])
    phone_counts = Counter(s["normalized_phone"] for s in unlinked if s["normalized_phone"])
    safe = still_safe = 0
    for s in unlinked:
        action, *_ = classify_contact(s, by_email, by_phone, email_counts, phone_counts)
        if action != "safe_person_promotion":
            continue
        safe += 1
        ne, np = s["normalized_email"], s["normalized_phone"]
        if not by_email.get(ne) and not by_phone.get(np):
            still_safe += 1
    return {"safe_person_promotion": safe, "still_safe_after_dedup": still_safe,
            "would_change": safe - still_safe}


def _print(d, links, proms) -> None:
    print("=== duplicate defect summary ===")
    print(f"  people_total: {d['people_total']}")
    print(f"  duplicate_name_groups: {d['duplicate_name_groups']}  "
          f"classifications: {d['name_group_classifications']}")
    print(f"  deterministically_collapsible_excess: {d['deterministically_collapsible_excess']}")
    print(f"  needs_review_excess: {d['needs_review_excess']}")
    print(f"  duplicate_email_groups: {d['duplicate_email_groups']}  "
          f"duplicate_phone_groups: {d['duplicate_phone_groups']}")
    print("\n=== largest duplicate name groups (up to 20) ===")
    for g in d["name_groups"][:20]:
        print(f"   x{g['size']:>3} [{g['classification']}] {', '.join(g['member_names'])[:60]} | "
              f"systems={g['source_systems']} methods={g['match_methods']} "
              f"record_keys={g['distinct_source_record_keys']} files={g['distinct_source_files']} "
              f"imported {g['imported_at_min']}..{g['imported_at_max']}")
    print(f"\n=== existing_person_link proposals: {len(links)} ===")
    for r in links:
        print(f"   #{r['source_contact_id']} {r['contact_name']} -> {r['target_person_name']}"
              f"  on {r['matched_on']}={r['identity_value']} (contacts_sharing={r['contacts_sharing_identity']})"
              f"  [{r['verdict']}]")
    print(f"\n=== safe_person_promotion revalidation ===\n"
          f"  safe: {proms['safe_person_promotion']}  still_safe_after_dedup: {proms['still_safe_after_dedup']}"
          f"  would_change: {proms['would_change']}")


def main(argv=None) -> int:
    argparse.ArgumentParser(prog="python -m scripts.migration.diagnose_duplicate_people",
                            description="Read-only duplicate-people diagnosis + link validation.").parse_args(
        argv if argv is not None else sys.argv[1:])
    from app.db import engine
    d = diagnose(engine)
    _print(d, validate_links(engine), revalidate_promotions(engine))
    print("\nDiagnosis complete (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
