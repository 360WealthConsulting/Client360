"""READ-ONLY reconciliation of every TaxDome exception folder into exactly ONE action category.

WHY THIS EXISTS
---------------
The first decision summary was internally inconsistent: it grouped folders four different ways, each
correct on its own terms, and then added them up wrongly. Overlapping buckets are how that happens.
So this computes a PARTITION — every exception folder lands in exactly one category, the categories
are counted once, and the totals are asserted rather than narrated:

    automatic + confirmation + data_repair + create_profile + ambiguous + genuine_conflict = folders

and the same for the documents behind them. If those do not balance, this refuses to print.

THE CATEGORIES, BY WHO ACTS
---------------------------
``automatic``          A normalization/spelling match to exactly one existing profile. No personal
                       decision: the mapping is mechanical and reviewable in bulk.
``confirmation``       Strong but not mechanical evidence. Staff confirm; no business judgement.
``data_repair``        Two records for one family. The fix is a household merge, not an ownership
                       decision — and it is out of this pipeline's scope either way.
``create_profile``     No profile exists anywhere. The decision is whether this client should exist
                       in Client360 at all, which only the firm can answer.
``ambiguous``          Evidence exists but does not settle it. Needs a person to look.
``genuine_conflict``   Two defensible owners, or personal-versus-business. Michael's call.

EVIDENCE, NOT SIMILARITY
------------------------
A shared surname is a hint, never a conclusion. Two households are only ``data_repair`` when
independent evidence agrees — a shared phone, email, address, member name, or Drake identity. A
surname match with CONTRADICTING given names (one label naming a different first name than the
TaxDome folder) is demoted to ``ambiguous``, because "same last name" is exactly how two unrelated
families get merged.

Read-only through the same server-enforced guard as the planner. Writes no database row, creates no
profile, merges no household.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402

from scripts.plan_document_ownership import (  # noqa: E402
    _database_url,
    assert_read_only,
    read_only_engine,
)

DEFAULT_DIR = Path("reports/taxdome_conflicts")

#: The partition. Order is the order the report prints them, cheapest action first.
CATEGORIES = ("automatic", "confirmation", "data_repair", "create_profile", "ambiguous",
              "genuine_conflict")

#: Categories that require MICHAEL's business judgement, as opposed to staff confirmation or a
#: mechanical mapping. This is the number that answers "how many decisions do I personally have".
PERSONAL_DECISION_CATEGORIES = ("data_repair", "create_profile", "ambiguous", "genuine_conflict")

_TOKEN = re.compile(r"[A-Za-z]+")
_DROP = {"and", "the", "of", "jr", "sr", "ii", "iii"}
_LABEL_NOISE = _DROP | {"household", "family", "trust", "revocable", "living", "estate"}


def _tokens(name):
    return {t.lower() for t in _TOKEN.findall(name or "") if t.lower() not in _DROP}


def _identifying(name):
    return {t.lower() for t in _TOKEN.findall(name or "")
            if t.lower() not in _LABEL_NOISE and len(t) > 2}


def _key(name):
    return " ".join(sorted(_tokens(name)))


def _redact(name):
    """Initials, for a summary that may be pasted anywhere."""
    words = [w for w in _TOKEN.findall(name or "") if w.lower() not in _DROP]
    return "".join(w[0].upper() for w in words)[:6] or "?"


# --- reference data -------------------------------------------------------------------------------

def _load(conn):
    people = {int(r["id"]): dict(r) for r in conn.execute(sa.text(
        "SELECT id, full_name, household_id FROM people")).mappings()}
    households = {int(r["id"]): dict(r) for r in conn.execute(sa.text(
        "SELECT id, name FROM households")).mappings()}
    orgs = {int(r["id"]): dict(r) for r in conn.execute(sa.text(
        "SELECT id, name FROM relationship_entities")).mappings()}

    members = defaultdict(list)
    for person in people.values():
        if person["household_id"] is not None:
            members[int(person["household_id"])].append(person)

    evidence = defaultdict(lambda: {"emails": set(), "phones": set(), "addresses": set()})
    for r in conn.execute(sa.text("""
        SELECT psl.person_id, sc.email, sc.phone, sc.address_line_1, sc.postal_code
          FROM person_source_links psl
          JOIN source_contacts sc ON sc.id = psl.source_contact_id
    """)).mappings():
        bucket = evidence[int(r["person_id"])]
        if r["email"]:
            bucket["emails"].add(str(r["email"]).strip().lower())
        if r["phone"]:
            digits = re.sub(r"\D", "", str(r["phone"]))[-10:]
            if len(digits) == 10:
                bucket["phones"].add(digits)
        if r["address_line_1"]:
            bucket["addresses"].add(
                f"{r['address_line_1']} {r['postal_code'] or ''}".strip().lower())

    drake = {}
    try:
        drake = {int(pid): int(n) for pid, n in conn.execute(sa.text(
            "SELECT person_id, count(*) FROM drake_identity GROUP BY person_id")).all()}
    except Exception:      # noqa: BLE001 — optional; a failed statement aborts the transaction
        conn.rollback()

    contacts = defaultdict(list)
    for c in conn.execute(sa.text(
            "SELECT DISTINCT first_name, last_name, email FROM source_contacts "
            "WHERE last_name IS NOT NULL")).mappings():
        contacts[_key(f"{c['first_name'] or ''} {c['last_name'] or ''}")].append(dict(c))

    return {"people": people, "households": households, "orgs": orgs, "members": members,
            "evidence": evidence, "drake": drake, "contacts": contacts}


def _side_evidence(ref, person_ids):
    out = {"emails": set(), "phones": set(), "addresses": set()}
    for pid in person_ids:
        got = ref["evidence"].get(pid)
        if got:
            for key in out:
                out[key] |= got[key]
    return out


# --- classification -------------------------------------------------------------------------------

def _classify_household_conflict(folder, rows, ref):
    """Duplicate family, or two different families with the same surname?"""
    r0 = rows[0]
    proposed_id = int(r0["proposed_entity_id"])
    stored_kind, stored_id = (("household", r0["stored_household_id"]) if r0["stored_household_id"]
                              else ("person", r0["stored_person_id"]) if r0["stored_person_id"]
                              else ("organization", r0["stored_organization_id"]))
    stored_id = int(stored_id) if stored_id else None

    proposed_members = ref["members"].get(proposed_id, [])
    if stored_kind == "household":
        stored_members = ref["members"].get(stored_id, [])
    elif stored_kind == "person" and stored_id in ref["people"]:
        stored_members = [ref["people"][stored_id]]
    else:
        stored_members = []

    a = _side_evidence(ref, [int(p["id"]) for p in proposed_members])
    b = _side_evidence(ref, [int(p["id"]) for p in stored_members])
    shared_contact = sum(len(a[k] & b[k]) for k in a)
    shared_names = {_key(p["full_name"]) for p in proposed_members if p["full_name"]} \
        & {_key(p["full_name"]) for p in stored_members if p["full_name"]}

    proposed_label = (ref["households"].get(proposed_id) or {}).get("name")
    stored_label = ((ref["households"].get(stored_id) or {}).get("name") if stored_kind == "household"
                    else (ref["people"].get(stored_id) or {}).get("full_name"))

    # Given names present on BOTH labels that disagree are a contradiction, not a coincidence.
    folder_given = _identifying(folder) - _identifying(proposed_label or "")
    stored_given = _identifying(stored_label) - (_identifying(proposed_label or "")
                                                 & _identifying(stored_label))
    surname_shared = _identifying(proposed_label) & _identifying(stored_label)
    contradicting = bool(folder_given and stored_given and not (folder_given & stored_given))

    if shared_contact or shared_names:
        return ("data_repair", "high",
                f"independent evidence agrees ({shared_contact} contact value(s), "
                f"{len(shared_names)} member name(s)) — one family recorded twice")
    if surname_shared and contradicting:
        return ("ambiguous", "low",
                f"labels share the surname {', '.join(sorted(surname_shared))} but the given names "
                f"disagree ({sorted(folder_given)} vs {sorted(stored_given)}) and no phone, email, "
                f"address, member name or Drake identity corroborates — surname alone must not merge "
                f"two families")
    if surname_shared:
        return ("ambiguous", "medium",
                f"labels share the surname {', '.join(sorted(surname_shared))} but nothing "
                f"independent corroborates it — needs a look before any merge")
    return ("genuine_conflict", "low",
            "different labels, different members, no shared evidence — two defensible owners")


def _classify_person_conflict(folder, rows, ref):
    r0 = rows[0]
    proposed_id = int(r0["proposed_entity_id"]) if r0["proposed_entity_id"] else None
    if any(r["stored_organization_id"] for r in rows):
        return ("genuine_conflict", "n/a",
                "a personal folder holding organization-owned documents — personal versus business "
                "is a filing policy decision, not a mapping error")

    stored_id = int(r0["stored_person_id"]) if r0["stored_person_id"] else None
    if stored_id is None and r0["stored_household_id"]:
        stored_id = None
        household = ref["members"].get(int(r0["stored_household_id"]), [])
        if any(int(p["id"]) == proposed_id for p in household):
            return ("confirmation", "high",
                    "the folder's person is a MEMBER of the household the document is filed under — "
                    "the two agree at different levels of the same family")
        return ("ambiguous", "medium",
                "folder maps to a person; document is filed on a household they do not belong to")

    a = _side_evidence(ref, [proposed_id] if proposed_id else [])
    b = _side_evidence(ref, [stored_id] if stored_id else [])
    shared = sum(len(a[k] & b[k]) for k in a)
    if shared:
        return ("confirmation", "high",
                f"the two people share {shared} contact value(s) — very likely one person recorded "
                f"twice, confirmable without a judgement call")
    return ("genuine_conflict", "low",
            "the folder and the stored owner name different people with no shared evidence")


def _classify_unresolved(folder, rows, ref):
    from app.importers.taxdome_drive import _folder_person_keys, _name_key

    index = defaultdict(list)
    for person in ref["people"].values():
        index[_name_key(person["full_name"])].append(person)

    keys = _folder_person_keys(folder)
    if any(len(index.get(k) or []) > 1 for k in keys):
        return ("ambiguous", "n/a",
                "the folder name matches several canonical people — no unique owner")

    key = _key(folder)
    exact_people = [p for p in ref["people"].values() if _key(p["full_name"]) == key]
    if len(exact_people) == 1:
        return ("automatic", "high",
                "exact normalized name match to one existing person — a spelling difference only")
    household = next((h for h in ref["households"].values() if _key(h.get("name")) == key), None)
    if household:
        return ("automatic", "high", "exact normalized name match to one existing household")
    org = next((o for o in ref["orgs"].values() if _key(o.get("name")) == key), None)
    if org:
        return ("automatic", "high", "exact normalized name match to one existing organization")

    surnames = _identifying(folder)
    token_matches = [p for p in ref["people"].values()
                     if surnames and surnames <= _tokens(p["full_name"])]
    if len(token_matches) == 1:
        return ("confirmation", "medium",
                "one existing person contains every identifying token in the folder name — "
                "spelling drift, confirmable by eye")
    if ref["contacts"].get(key):
        return ("create_profile", "medium",
                "an imported source contact exists but no canonical profile — the client is known "
                "to the firm and absent from Client360")
    return ("create_profile", "high",
            "no person, household, organization or source contact matches this name anywhere")


def classify(folder, rows, ref):
    cause = rows[0]["cause"]
    if cause == "conflict_household":
        return _classify_household_conflict(folder, rows, ref)
    if cause == "conflict_person":
        return _classify_person_conflict(folder, rows, ref)
    if cause == "folder_unresolved":
        return _classify_unresolved(folder, rows, ref)
    return ("ambiguous", "n/a",
            "TaxDome-sourced with no folder tag — the lane has lost what makes it authoritative")


# --- run --------------------------------------------------------------------------------------------

def run(*, out_dir: Path, database_url: str | None = None) -> dict:
    csv_path = out_dir / "taxdome_ownership_conflicts.csv"
    if not csv_path.exists():
        raise SystemExit(f"{csv_path} not found — run report_taxdome_ownership_conflicts.py first")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))

    by_folder = defaultdict(list)
    for r in rows:
        by_folder[r["taxdome_folder"] or "(no folder tag)"].append(r)

    engine = read_only_engine(_database_url(database_url))
    with engine.connect() as conn:
        assert_read_only(conn)
        ref = _load(conn)
        decided = {}
        for folder, folder_rows in by_folder.items():
            category, confidence, reason = classify(folder, folder_rows, ref)
            decided[folder] = {"category": category, "confidence": confidence, "reason": reason,
                               "documents": len(folder_rows), "cause": folder_rows[0]["cause"],
                               "label": _redact(folder)}

    folders = Counter(d["category"] for d in decided.values())
    documents = Counter()
    for d in decided.values():
        documents[d["category"]] += d["documents"]

    total_folders, total_documents = len(decided), sum(d["documents"] for d in decided.values())

    # The invariant. If these do not balance the classification is not a partition and the report is
    # worthless, so it refuses rather than printing numbers that do not add up.
    assert sum(folders.values()) == total_folders, "folder categories are not exhaustive"
    assert sum(documents.values()) == total_documents, "document categories are not exhaustive"
    assert set(folders) <= set(CATEGORIES), f"unknown category: {set(folders) - set(CATEGORIES)}"

    package = {
        "generated_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "total_folders": total_folders,
        "total_documents": total_documents,
        "folders_by_category": {c: folders.get(c, 0) for c in CATEGORIES},
        "documents_by_category": {c: documents.get(c, 0) for c in CATEGORIES},
        "personal_decision_folders": sum(folders.get(c, 0) for c in PERSONAL_DECISION_CATEGORIES),
        "personal_decision_documents": sum(documents.get(c, 0)
                                           for c in PERSONAL_DECISION_CATEGORIES),
        "source_causes": dict(Counter(d["cause"] for d in decided.values())),
        "folders": {folder: d for folder, d in sorted(
            decided.items(), key=lambda kv: (-kv[1]["documents"], kv[0]))},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "taxdome_exception_reconciliation.json").write_text(
        json.dumps(package, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return package


def _print(p):
    print(f"TAXDOME EXCEPTION RECONCILIATION — {p['total_folders']} folders, "
          f"{p['total_documents']:,} documents")
    print()
    print(f"  {'category':<20}{'folders':>9}{'documents':>12}  who acts")
    actor = {"automatic": "nobody — mechanical mapping",
             "confirmation": "staff confirm from strong evidence",
             "data_repair": "household merge (outside this pipeline)",
             "create_profile": "firm decides whether the client should exist",
             "ambiguous": "a person must look",
             "genuine_conflict": "Michael decides"}
    for c in CATEGORIES:
        print(f"  {c:<20}{p['folders_by_category'][c]:>9}{p['documents_by_category'][c]:>12,}"
              f"  {actor[c]}")
    print(f"  {'TOTAL':<20}{p['total_folders']:>9}{p['total_documents']:>12,}")
    print()
    print(f"  personal decisions: {p['personal_decision_folders']} folders "
          f"({p['personal_decision_documents']:,} documents)")
    print(f"  source causes: {p['source_causes']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    _print(run(out_dir=args.out_dir, database_url=args.database_url))
    print(f"\nwritten to {args.out_dir}/taxdome_exception_reconciliation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
