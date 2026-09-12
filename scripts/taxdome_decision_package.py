"""READ-ONLY human-readable decision package for the TaxDome ownership conflicts.

WHY
---
The conflict CSV is correct and unusable: it identifies clients by integer id. Nobody can decide
"household 252 or household 192" from that. This resolves the same rows into names, members, and the
corroborating evidence a person actually needs — phones, emails, addresses, Drake identities — and
states a recommendation with its reason, so the decision is a confirmation rather than an
investigation.

It writes ONLY into ``reports/taxdome_conflicts/``, which is gitignored precisely because these files
carry client names. Nothing here is committed and nothing here is written to the database.

READ-ONLY BY CONSTRUCTION
-------------------------
Same guard as the planner: the connection is opened through
:func:`scripts.plan_document_ownership.read_only_engine` and asserted read-only before a row is read,
so PostgreSQL itself refuses any write from this session.

FOUR SECTIONS
-------------
1. ``household_conflicts``  the folders where TaxDome and Client360 name different households, with
   both sides' members and overlapping contact evidence, and a recommendation of duplicate-household
   merge / ownership correction / genuine conflict.
2. ``person_org_conflicts`` personal folder vs. organization-owned documents, with filenames and
   years so each document can be called personal, business, or ambiguous.
3. ``missing_mappings``     the folders whose names match no canonical person, searched again across
   people, households, organizations, source contacts, emails, phones and Drake identities, split
   into "an existing profile almost certainly matches" and "genuinely absent".
4. ``ambiguous_names``      the folders whose names match several people, with the candidates and
   what distinguishes them.

It proposes. It creates no client, merges no household, and writes no ownership.
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
_TOKEN = re.compile(r"[A-Za-z]+")
_DROP = {"and", "the", "of", "jr", "sr", "ii", "iii"}


# --- lookups --------------------------------------------------------------------------------------

def _people(conn):
    rows = conn.execute(sa.text("""
        SELECT id, full_name, household_id, primary_email, primary_phone
          FROM people
    """)).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def _households(conn):
    rows = conn.execute(sa.text("SELECT id, name FROM households")).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def _organizations(conn):
    rows = conn.execute(sa.text(
        "SELECT id, name FROM relationship_entities")).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def _members_by_household(people):
    members = defaultdict(list)
    for person in people.values():
        if person["household_id"] is not None:
            members[int(person["household_id"])].append(person)
    return members


def _contact_evidence(conn, person_ids):
    """Emails, phones and addresses reachable from source contacts for a set of people."""
    if not person_ids:
        return {}
    rows = conn.execute(sa.text("""
        SELECT psl.person_id, sc.email, sc.phone, sc.address_line_1 AS address_line1,
               sc.city, sc.state, sc.postal_code
          FROM person_source_links psl
          JOIN source_contacts sc ON sc.id = psl.source_contact_id
         WHERE psl.person_id = ANY(:ids)
    """), {"ids": list(person_ids)}).mappings().all()
    by_person = defaultdict(lambda: {"emails": set(), "phones": set(), "addresses": set()})
    for r in rows:
        bucket = by_person[int(r["person_id"])]
        if r["email"]:
            bucket["emails"].add(str(r["email"]).strip().lower())
        if r["phone"]:
            digits = re.sub(r"\D", "", str(r["phone"]))[-10:]
            if len(digits) == 10:
                bucket["phones"].add(digits)
        if r["address_line1"]:
            bucket["addresses"].add(
                " ".join(str(x) for x in (r["address_line1"], r["city"], r["state"],
                                          r["postal_code"]) if x).strip().lower())
    return by_person


def _drake_identities(conn, person_ids):
    """Whether each person carries a Drake identity — the strongest corroboration available."""
    if not person_ids:
        return {}
    try:
        rows = conn.execute(sa.text("""
            SELECT person_id, count(*) AS n FROM drake_identity
             WHERE person_id = ANY(:ids) GROUP BY person_id
        """), {"ids": list(person_ids)}).all()
    except Exception:      # noqa: BLE001 — the table/column may not exist in every environment
        # A failed statement ABORTS the transaction, so swallowing it without a rollback poisons
        # every later query with "current transaction is aborted". Degrading gracefully means
        # leaving the connection usable, not just returning an empty dict.
        conn.rollback()
        return {}
    return {int(pid): int(n) for pid, n in rows}


def _merge_evidence(evidence, person_ids):
    out = {"emails": set(), "phones": set(), "addresses": set()}
    for pid in person_ids:
        got = evidence.get(pid)
        if got:
            for key in out:
                out[key] |= got[key]
    return out


def _label(person):
    return f"{person['full_name']} (person {person['id']})"


# --- section 1: household conflicts -----------------------------------------------------------------

def _household_conflicts(conn, rows, people, households, members, evidence, drake):
    out = []
    grouped = defaultdict(list)
    for r in rows:
        if r["cause"] != "conflict_household":
            continue
        # The stored side is NOT always a household: a folder that maps to a household frequently
        # finds the document filed on one of its members instead. Grouping on household id alone
        # dropped those rows on the floor, which is how this report first crashed.
        stored_kind, stored_id = (("household", r["stored_household_id"])
                                  if r["stored_household_id"] else
                                  ("person", r["stored_person_id"]) if r["stored_person_id"] else
                                  ("organization", r["stored_organization_id"]))
        grouped[(r["taxdome_folder"], r["proposed_entity_id"], stored_kind, stored_id)].append(r)

    for (folder, proposed_id, stored_kind, stored_id), items in sorted(
            grouped.items(), key=lambda kv: -len(kv[1])):
        proposed_id = int(proposed_id)
        stored_id = int(stored_id) if stored_id else None
        proposed_members = members.get(proposed_id, [])
        if stored_kind == "household":
            stored_members = members.get(stored_id, [])
        elif stored_kind == "person" and stored_id in people:
            stored_members = [people[stored_id]]
        else:
            stored_members = []
        proposed_ids = [int(p["id"]) for p in proposed_members]
        stored_ids = [int(p["id"]) for p in stored_members]

        a = _merge_evidence(evidence, proposed_ids)
        b = _merge_evidence(evidence, stored_ids)
        shared = {k: sorted(a[k] & b[k]) for k in a}
        overlap = sum(len(v) for v in shared.values())
        name_overlap = sorted({_key(p["full_name"]) for p in proposed_members}
                              & {_key(p["full_name"]) for p in stored_members})

        # Household NAMES are evidence in their own right, and on this corpus they are the strongest
        # available: "<Surname> Household" against "<First> & <First> <Surname> Household" is one
        # family under two labels, while both sides' member rows can carry a NULL full_name and tell
        # you nothing. Comparing only members called those pairs a "genuine conflict", which was
        # wrong. (Illustrative shapes only — no client name belongs in source.)
        proposed_name = (households.get(proposed_id) or {}).get("name")
        stored_name = ((households.get(stored_id) or {}).get("name") if stored_kind == "household"
                       else (people.get(stored_id) or {}).get("full_name"))
        surname_overlap = sorted(_surnames(proposed_name) & _surnames(stored_name))
        unnamed = sum(1 for p in (*proposed_members, *stored_members) if not p.get("full_name"))

        if overlap or name_overlap:
            recommendation = "duplicate-household merge"
            confidence = "high" if (overlap and name_overlap) else "medium"
            reason = (f"the two households share {overlap} contact value(s) and "
                      f"{len(name_overlap)} member name(s) — one family recorded twice")
        elif surname_overlap:
            recommendation = "duplicate-household merge"
            confidence = "medium"
            reason = (f"both names carry the surname {', '.join(surname_overlap)} "
                      f"({proposed_name!r} vs {stored_name!r})"
                      + (f"; {unnamed} member row(s) have no name, so member-level"
                         " corroboration is unavailable" if unnamed else ""))
        elif not stored_members:
            recommendation = "ownership correction"
            confidence = "medium"
            reason = "the stored side has no members; the TaxDome mapping is the better record"
        else:
            recommendation = "genuine conflict"
            confidence = "low"
            reason = "different names, different members, no shared contact evidence"

        out.append({
            "taxdome_folder": folder,
            "documents": len(items),
            "taxdome_household": {"id": proposed_id,
                                  "name": (households.get(proposed_id) or {}).get("name"),
                                  "members": [_label(p) for p in proposed_members],
                                  "drake_identities": sum(drake.get(i, 0) for i in proposed_ids)},
            "client360_owner": {
                "kind": stored_kind, "id": stored_id,
                "name": ((households.get(stored_id) or {}).get("name") if stored_kind == "household"
                         else (people.get(stored_id) or {}).get("full_name")),
                "members": [_label(p) for p in stored_members],
                "drake_identities": sum(drake.get(i, 0) for i in stored_ids)},
            "shared_evidence": shared,
            "shared_member_names": name_overlap,
            "category": f"household vs {stored_kind}",
            "recommendation": recommendation,
            "confidence": confidence,
            "reason": reason,
        })
    return out


def _key(name):
    return " ".join(sorted(t.lower() for t in _TOKEN.findall(name or "")
                           if t.lower() not in _DROP))


#: Words that appear in a household LABEL without identifying the family.
_HOUSEHOLD_NOISE = _DROP | {"household", "family", "trust", "revocable", "living", "estate"}


def _surnames(name):
    """The identifying tokens of a household or person label.

    Deliberately crude — a shared surname is a strong hint that two households are one family, and
    the alternative on this data is no hint at all, because the member rows can carry NULL names."""
    return {t.lower() for t in _TOKEN.findall(name or "")
            if t.lower() not in _HOUSEHOLD_NOISE and len(t) > 2}


# --- section 2: person vs organization ---------------------------------------------------------------

def _person_org_conflicts(conn, rows, people, organizations):
    items = [r for r in rows
             if r["cause"].startswith("conflict") and r["stored_organization_id"]]
    if not items:
        return []
    ids = [int(r["document_id"]) for r in items]
    docs = {int(r["id"]): dict(r) for r in conn.execute(sa.text("""
        SELECT id, original_name, category, classification, subcategory
          FROM documents WHERE id = ANY(:ids)
    """), {"ids": ids}).mappings()}

    out = []
    for r in items:
        doc = docs.get(int(r["document_id"]), {})
        name = doc.get("original_name") or ""
        year = next((m for m in re.findall(r"(?:19|20)\d{2}", name)), None)
        lowered = name.lower()
        business_words = ("1120", "1065", "941", "940", "w-3", "payroll", "llc", "corp",
                          "schedule c", "business", "invoice")
        personal_words = ("1040", "w-2", "w2", "1099", "1095", "8879", "id", "license")
        if any(w in lowered for w in business_words):
            appears = "business"
        elif any(w in lowered for w in personal_words):
            appears = "personal"
        else:
            appears = "ambiguous"
        person = people.get(int(r["proposed_entity_id"])) if r["proposed_entity_id"] else None
        out.append({
            "taxdome_folder": r["taxdome_folder"],
            "document_id": int(r["document_id"]),
            "filename": name,
            "category": doc.get("category"),
            "year": year,
            "appears": appears,
            "taxdome_person": _label(person) if person else None,
            "client360_organization": {
                "id": int(r["stored_organization_id"]),
                "name": (organizations.get(int(r["stored_organization_id"])) or {}).get("name")},
            "recommended_owner": ("keep with the organization" if appears == "business"
                                  else "move to the person" if appears == "personal"
                                  else "needs a human — filename does not say"),
        })
    return out


# --- section 3: missing mappings -----------------------------------------------------------------------

def _missing_mappings(conn, folders, people, households, organizations):
    """Search every identity surface for a folder name that resolve_folder could not place."""
    by_person_key = defaultdict(list)
    for person in people.values():
        by_person_key[_key(person["full_name"])].append(person)
    household_by_key = {_key(h["name"]): h for h in households.values() if h.get("name")}
    org_by_key = {_key(o["name"]): o for o in organizations.values() if o.get("name")}

    contacts = conn.execute(sa.text("""
        SELECT DISTINCT first_name, last_name, email FROM source_contacts
         WHERE last_name IS NOT NULL
    """)).mappings().all()
    contact_by_key = defaultdict(list)
    for c in contacts:
        contact_by_key[_key(f"{c['first_name'] or ''} {c['last_name'] or ''}")].append(dict(c))

    matched, absent = [], []
    for folder, doc_count in folders:
        key = _key(folder)
        surnames = {t.lower() for t in _TOKEN.findall(folder) if t.lower() not in _DROP}
        proposal = None

        if key in by_person_key and len(by_person_key[key]) == 1:
            proposal = {"kind": "person", "match": _label(by_person_key[key][0]),
                        "basis": "exact normalized name match on people"}
        elif key in household_by_key:
            h = household_by_key[key]
            proposal = {"kind": "household", "match": f"{h['name']} (household {h['id']})",
                        "basis": "exact normalized name match on households"}
        elif key in org_by_key:
            o = org_by_key[key]
            proposal = {"kind": "organization", "match": f"{o['name']} (organization {o['id']})",
                        "basis": "exact normalized name match on organizations"}
        elif key in contact_by_key:
            proposal = {"kind": "source_contact", "match": contact_by_key[key][0].get("email"),
                        "basis": "matches an imported source contact with no canonical person yet"}
        else:
            # Last resort: a single person sharing every surname token in the folder.
            candidates = [p for p in people.values()
                          if surnames and surnames <= {t.lower() for t in
                                                       _TOKEN.findall(p["full_name"] or "")}]
            if len(candidates) == 1:
                proposal = {"kind": "person", "match": _label(candidates[0]),
                            "basis": "unique person containing every surname token (spelling drift)"}

        record = {"taxdome_folder": folder, "documents": doc_count}
        if proposal:
            record.update(proposal)
            matched.append(record)
        else:
            absent.append(record)
    return matched, absent


# --- section 4: ambiguous names ------------------------------------------------------------------------

def _ambiguous(conn, folders, people, evidence, drake):
    by_key = defaultdict(list)
    for person in people.values():
        by_key[_key(person["full_name"])].append(person)

    out = []
    for folder, doc_count in folders:
        candidates = by_key.get(_key(folder), [])
        rows = []
        for person in candidates:
            pid = int(person["id"])
            got = evidence.get(pid, {"emails": set(), "phones": set(), "addresses": set()})
            rows.append({
                "person": _label(person),
                "household_id": person["household_id"],
                "emails": sorted(got["emails"])[:3],
                "phones": sorted(got["phones"])[:3],
                "has_address": bool(got["addresses"]),
                "drake_identities": drake.get(pid, 0),
            })
        out.append({"taxdome_folder": folder, "documents": doc_count,
                    "candidates": rows,
                    "distinguishing_evidence": (
                        "Drake identity present on exactly one candidate"
                        if sum(1 for r in rows if r["drake_identities"]) == 1
                        else "contact details differ" if any(r["emails"] or r["phones"] for r in rows)
                        else "no distinguishing evidence — needs a human")})
    return out


# --- run ------------------------------------------------------------------------------------------------

def run(*, out_dir: Path, database_url: str | None = None) -> dict:
    from app.importers.taxdome_drive import _folder_person_keys, _name_key

    csv_path = out_dir / "taxdome_ownership_conflicts.csv"
    if not csv_path.exists():
        raise SystemExit(f"{csv_path} not found — run scripts/report_taxdome_ownership_conflicts.py first")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))

    engine = read_only_engine(_database_url(database_url))
    with engine.connect() as conn:
        assert_read_only(conn)
        people = _people(conn)
        households = _households(conn)
        organizations = _organizations(conn)
        members = _members_by_household(people)
        evidence = _contact_evidence(conn, list(people))
        drake = _drake_identities(conn, list(people))

        household_conflicts = _household_conflicts(conn, rows, people, households, members,
                                                   evidence, drake)
        person_org = _person_org_conflicts(conn, rows, people, organizations)

        unresolved = Counter(r["taxdome_folder"] for r in rows if r["cause"] == "folder_unresolved")
        index = defaultdict(list)
        for person in people.values():
            index[_name_key(person["full_name"])].append(person)

        ambiguous_folders, missing_folders = [], []
        for folder, n in unresolved.items():
            keys = _folder_person_keys(folder)
            if any(len(index.get(k) or []) > 1 for k in keys):
                ambiguous_folders.append((folder, n))
            else:
                missing_folders.append((folder, n))

        matched, absent = _missing_mappings(conn, missing_folders, people, households, organizations)
        ambiguous = _ambiguous(conn, ambiguous_folders, people, evidence, drake)

    package = {
        "generated_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "writes_nothing": True,
        "household_conflicts": household_conflicts,
        "person_org_conflicts": person_org,
        "missing_mappings_with_proposal": matched,
        "missing_mappings_genuinely_absent": absent,
        "ambiguous_names": ambiguous,
        "counts": {
            "household_conflict_folders": len(household_conflicts),
            "household_conflict_documents": sum(h["documents"] for h in household_conflicts),
            "person_org_documents": len(person_org),
            "missing_with_proposal_folders": len(matched),
            "missing_with_proposal_documents": sum(m["documents"] for m in matched),
            "genuinely_absent_folders": len(absent),
            "genuinely_absent_documents": sum(m["documents"] for m in absent),
            "ambiguous_folders": len(ambiguous),
            "ambiguous_documents": sum(a["documents"] for a in ambiguous),
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "taxdome_decision_package.json"
    path.write_text(json.dumps(package, indent=2, sort_keys=True, default=str), encoding="utf-8")

    md = out_dir / "taxdome_decision_package.md"
    md.write_text(_render(package), encoding="utf-8")
    return package


def _render(p) -> str:
    lines = ["# TaxDome ownership decisions", "",
             f"Generated {p['generated_at']} — read-only, nothing written.", "",
             "## 1. Household conflicts", ""]
    for h in p["household_conflicts"]:
        lines += [f"### {h['taxdome_folder']} — {h['documents']} documents",
                  f"- **TaxDome says:** {h['taxdome_household']['name']} "
                  f"(household {h['taxdome_household']['id']}) — "
                  f"{', '.join(h['taxdome_household']['members']) or 'no members'}",
                  f"- **Client360 says:** {h['client360_owner']['name']} "
                  f"({h['client360_owner']['kind']} {h['client360_owner']['id']}) — "
                  f"{', '.join(h['client360_owner']['members']) or 'no members'}",
                  f"- **Shared evidence:** {h['shared_evidence']}",
                  f"- **Recommendation:** {h['recommendation']} ({h['confidence']}) — {h['reason']}",
                  ""]
    lines += ["## 2. Personal folder vs organization-owned documents", ""]
    for d in p["person_org_conflicts"]:
        lines += [f"- doc {d['document_id']} `{d['filename']}` ({d['appears']}, year {d['year']}) — "
                  f"TaxDome: {d['taxdome_person']}; stored: "
                  f"{d['client360_organization']['name']} → **{d['recommended_owner']}**"]
    lines += ["", "## 3. Unresolved folders with a proposed existing match", ""]
    for m in p["missing_mappings_with_proposal"]:
        lines += [f"- {m['taxdome_folder']} ({m['documents']} docs) → {m['match']} "
                  f"[{m['kind']}; {m['basis']}]"]
    lines += ["", "## 4. Unresolved folders with no match in Client360", ""]
    for m in p["missing_mappings_genuinely_absent"]:
        lines += [f"- {m['taxdome_folder']} ({m['documents']} docs)"]
    lines += ["", "## 5. Ambiguous names", ""]
    for a in p["ambiguous_names"]:
        lines += [f"### {a['taxdome_folder']} — {a['documents']} documents",
                  f"- {a['distinguishing_evidence']}"]
        for c in a["candidates"]:
            lines += [f"  - {c['person']} household={c['household_id']} "
                      f"drake={c['drake_identities']} emails={c['emails']}"]
        lines += [""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    package = run(out_dir=args.out_dir, database_url=args.database_url)
    counts = package["counts"]
    print("TAXDOME DECISION PACKAGE")
    for key, value in counts.items():
        print(f"  {key:<38}{value:>8,}")
    print(f"\nwritten to {args.out_dir}/taxdome_decision_package.(json|md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
