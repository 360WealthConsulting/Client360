"""READ-ONLY canonical-entity population audit.

The linkage diagnostic only checked ``people`` name-keys. This audit widens the search to the raw import
layer (``source_contacts`` + ``person_source_links`` + ``match_queue``) and to alternate name
representations, to determine whether the exception folders are TRULY absent from Client360 or merely
absent from the fields the diagnostic examined — and to explain the people/household/organization
population itself.

It answers, with COUNTS:
  * provenance of canonical people (via person_source_links -> source_contacts.source_system) and why
    households/organizations are effectively unpopulated;
  * a disposition for every exception folder:
      canonical_ambiguous               - exact people name-key exists but maps to >1 people
      canonical_unique_resolver_gap     - exact unique people match (resolver should have linked it)
      source_exists_not_promoted        - a source_contacts record matches, but no canonical person
      canonical_alternate_name          - a person exists under a differing name representation
      source_alternate_name_not_promoted- a source_contacts record exists under a differing name
      joint_partial_uncanonicalized     - joint folder, at least one member exists in people/source
      joint_absent                      - joint folder, no member found anywhere
      truly_absent                      - no evidence in people OR source_contacts
  * a business-like overlay (corporate-suffix folders) and a targeted term search (e.g. VAL6, INC).

READ-ONLY: SELECT only; sets the session read-only as defense in depth. It never writes a row, creates an
entity, links a document, touches storage_uri/document_sources, or runs remediation apply.

Usage::
    python -m scripts.migration.audit_canonical_population <preview-directory>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict

from app.importers.taxdome_drive import _folder_person_keys, _name_key
from scripts.migration.analyze_linkage_exceptions import read_exception_folders

# Corporate-suffix / business-name signals (overlay only; never used to auto-link).
_BUSINESS_RE = re.compile(r"\b(inc|llc|l\.l\.c|co|corp|ltd|company|group|associates|assoc|partners|"
                          r"holdings|enterprises|services|heating|plumbing|construction|realty|"
                          r"insurance|financial|capital|trust|foundation|farms|properties)\b", re.I)

DISPOSITIONS = (
    "canonical_ambiguous",
    "canonical_unique_resolver_gap",
    "source_exists_not_promoted",
    "canonical_alternate_name",
    "source_alternate_name_not_promoted",
    "joint_partial_uncanonicalized",
    "joint_absent",
    "truly_absent",
)


def _tokset(name) -> frozenset:
    return frozenset(_name_key(name).split())


def build_name_index(rows):
    """rows: iterable of (id, full_name, first_name, last_name). Returns (key_index, token_list)."""
    key_index: dict[str, list[int]] = defaultdict(list)
    token_list: list[tuple[frozenset, int]] = []
    for rid, full, first, last in rows:
        key = _name_key(full) or _name_key(f"{first or ''} {last or ''}")
        toks = _tokset(full) or _tokset(f"{first or ''} {last or ''}")
        if key:
            key_index[key].append(rid)
        if toks:
            token_list.append((toks, rid))
    return key_index, token_list


def _alt_hit(folder_tokens, token_list) -> bool:
    """True if the folder's tokens are a subset/superset of some entity's tokens (>=2 tokens, to avoid
    matching on a single common token). Diagnostic heuristic only — never used to link."""
    if len(folder_tokens) < 2:
        return False
    for toks, _ in token_list:
        if toks and (folder_tokens <= toks or toks <= folder_tokens):
            return True
    return False


def classify_folder(folder, people_key, people_tok, sc_key, sc_tok) -> str:
    fk = _name_key(folder)
    ftoks = set(fk.split())
    member_keys = _folder_person_keys(folder)
    joint = len(member_keys) > 1
    if fk and fk in people_key:
        return "canonical_ambiguous" if len(people_key[fk]) > 1 else "canonical_unique_resolver_gap"
    if fk and fk in sc_key:
        return "source_exists_not_promoted"
    if joint:
        found = any((k in people_key or k in sc_key) for k in member_keys)
        return "joint_partial_uncanonicalized" if found else "joint_absent"
    if _alt_hit(ftoks, people_tok):
        return "canonical_alternate_name"
    if _alt_hit(ftoks, sc_tok):
        return "source_alternate_name_not_promoted"
    return "truly_absent"


def classify_all(folders, people_key, people_tok, sc_key, sc_tok):
    pat: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    business = []
    for folder in folders:
        tag = classify_folder(folder, people_key, people_tok, sc_key, sc_tok)
        pat[tag] += 1
        if len(examples[tag]) < 12:
            examples[tag].append(folder)
        if _BUSINESS_RE.search(folder or ""):
            business.append(folder)
    return pat, examples, business


# --------------------------------------------------------------------------- DB layer (read-only)

def _load(engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))     # defense in depth
        people = conn.execute(text("SELECT id, full_name, first_name, last_name FROM people")).all()
        sc = conn.execute(text("SELECT id, full_name, first_name, last_name FROM source_contacts")).all()
        prov = {
            "people_total": conn.execute(text("SELECT count(*) FROM people")).scalar_one(),
            "people_linked_to_source": conn.execute(text(
                "SELECT count(DISTINCT person_id) FROM person_source_links")).scalar_one(),
            "households": conn.execute(text("SELECT count(*) FROM households")).scalar_one(),
            "standalone_orgs": conn.execute(text(
                "SELECT count(*) FROM relationship_entities "
                "WHERE person_id IS NULL AND household_id IS NULL AND active IS TRUE")).scalar_one(),
            "source_contacts_by_system": conn.execute(text(
                "SELECT source_system, count(*) FROM source_contacts GROUP BY 1 ORDER BY 2 DESC")).all(),
            "people_by_source_system": conn.execute(text(
                "SELECT sc.source_system, count(DISTINCT psl.person_id) "
                "FROM person_source_links psl JOIN source_contacts sc ON sc.id = psl.source_contact_id "
                "GROUP BY 1 ORDER BY 2 DESC")).all(),
            "match_queue_by_status": conn.execute(text(
                "SELECT status, count(*) FROM match_queue GROUP BY 1 ORDER BY 2 DESC")).all(),
        }
    return people, sc, prov


def search_term(engine, term):
    """Read-only search for a term across people + source_contacts (for targeted cases like VAL6, INC)."""
    from sqlalchemy import text
    like = f"%{term}%"
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        ppl = conn.execute(text("SELECT id, full_name FROM people WHERE full_name ILIKE :q"),
                           {"q": like}).all()
        scs = conn.execute(text(
            "SELECT id, full_name, source_system, left(raw_data::text, 200) "
            "FROM source_contacts WHERE full_name ILIKE :q OR raw_data::text ILIKE :q"),
            {"q": like}).all()
    return {"people": ppl, "source_contacts": scs}


def audit(preview_dir, engine=None):
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    people, sc, prov = _load(engine)
    people_key, people_tok = build_name_index(people)
    sc_key, sc_tok = build_name_index(sc)
    folders = read_exception_folders(preview_dir)
    pat, examples, business = classify_all(folders, people_key, people_tok, sc_key, sc_tok)
    # E: candidate counts for exact-people-key folders
    would_match = {f: len(people_key[_name_key(f)]) for f in folders
                   if _name_key(f) and _name_key(f) in people_key}
    return {"provenance": prov, "folders_analyzed": len(folders), "dispositions": pat,
            "examples": examples, "business_like_folders": business, "would_match_candidates": would_match,
            "index_sizes": {"people_keys": len(people_key), "source_contact_keys": len(sc_key)}}


def _print(result) -> None:
    p = result["provenance"]
    print("=== provenance ===")
    print(f"  people_total: {p['people_total']}   linked_to_source_contacts: {p['people_linked_to_source']}")
    print(f"  households: {p['households']}   standalone_orgs: {p['standalone_orgs']}")
    print("  source_contacts by source_system:")
    for sys_, n in p["source_contacts_by_system"]:
        print(f"    {sys_}: {n}")
    print("  canonical people by source_system:")
    for sys_, n in p["people_by_source_system"]:
        print(f"    {sys_}: {n}")
    print("  match_queue by status:")
    for st, n in p["match_queue_by_status"]:
        print(f"    {st}: {n}")
    print(f"\n=== {result['folders_analyzed']} exception folders — dispositions ===")
    for tag in DISPOSITIONS:
        if result["dispositions"].get(tag):
            print(f"  {tag}: {result['dispositions'][tag]}")
    print(f"\n  business-like folders (corporate-suffix overlay): {len(result['business_like_folders'])}")
    print(f"  would_match exact-key folders (E): {len(result['would_match_candidates'])} "
          f"(candidate counts: {sorted(set(result['would_match_candidates'].values()))})")
    print("\n=== examples by disposition ===")
    for tag in DISPOSITIONS:
        if result["examples"].get(tag):
            print(f"[{tag}]")
            for ex in result["examples"][tag]:
                print(f"   {ex}")
    if result["business_like_folders"]:
        print("[business_like_folders]")
        for ex in result["business_like_folders"][:12]:
            print(f"   {ex}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.audit_canonical_population",
                                description="Read-only canonical-entity population audit.")
    p.add_argument("preview_dir", help="Preview directory containing exceptions.csv")
    p.add_argument("--search", action="append", default=[], help="Extra term(s) to search (e.g. 'VAL6').")
    args = p.parse_args(argv)
    if not os.path.isfile(os.path.join(args.preview_dir, "exceptions.csv")):
        print(f"ERROR: no exceptions.csv in {args.preview_dir}")
        return 2
    result = audit(args.preview_dir)
    _print(result)
    from app.db import engine
    for term in args.search or ["VAL6"]:
        hits = search_term(engine, term)
        print(f"\n=== term search: {term!r} ===")
        print(f"  people: {list(hits['people'])}")
        print(f"  source_contacts: {list(hits['source_contacts'])}")
    print("\nAudit complete (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
