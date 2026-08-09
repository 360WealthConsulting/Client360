"""READ-ONLY plan of the NEXT deterministic canonical/linkage batch.

The canonical-population audit (``scripts.migration.audit_canonical_population``) classifies every
unresolved linkage-exception folder by NAME-KEY evidence. This planner takes the three name-key buckets
that MIGHT be deterministically resolvable —

    source_exists_not_promoted          folder's exact name exists in source_contacts, no canonical person
    source_alternate_name_not_promoted  a source_contact exists under a differing name representation
    canonical_alternate_name            a canonical person exists under a differing name representation

— and decides, PER FOLDER, whether there is EXACTLY ONE deterministic canonical outcome using STABLE
SOURCE IDENTITY / PROVENANCE (unique non-shared email / phone, business raw_data entity signal, shared
Drake source_record_id / EIN), NEVER fuzzy name similarity. A folder that matches only by name — with no
corroborating stable identity — is held as ambiguous, on purpose.

Deterministic outcomes (each folder gets exactly one):

    safe_person_promotion        one anchoring source_contact, unique non-shared email/phone identity, no
                                 existing person by that identity            -> create person + link
    existing_person_link         anchoring source_contact's stable identity maps to EXACTLY ONE existing
                                 person (plausible_link gate)                -> link source_contact
    alternate_name_person_link   canonical_alternate_name folder whose alternate-token person is CONFIRMED
                                 by a stable-identity join from a folder-named source_contact
                                                                             -> link folder docs -> person
    <kind>_canonicalization      anchoring source_contact(s) carry a business/trust raw_data entity signal
                                 (deduped to one entity)                     -> relationship_entities
    held_ambiguous               no single deterministic outcome (name-only, >1 contact, >1 person,
                                 shared identity, or no identity anchor)     -> excluded from the batch

The safe_person_promotion / existing_person_link / <kind>_canonicalization rows are emitted in the SAME
schema that ``app.services.migration.canonical_repair.load_approved_set`` reads, so this batch's
reconciliation.csv is directly usable as the FROZEN ``--approved`` set for the existing guarded
CanonicalRepairJob APPLY. ``alternate_name_person_link`` rows drive the document-folder link path and are
reported separately (no apply is built here).

STRICTLY READ-ONLY: SELECT only (+ session read-only as defense in depth). No promotion, linking, entity /
household creation, merge, document linkage, storage_uri / document_sources change, or apply. No file
movement; the 17,042 already-relocated documents are never touched.

Usage::
    python -m scripts.migration.plan_next_linkage_batch <linkage-preview-dir> [--search VAL6] \
        [--out <reconciliation.csv>]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

from app.importers.taxdome_drive import _folder_person_keys, _name_key
from app.services.migration.canonical_population import business_kind
from app.services.migration.canonical_repair import plausible_link
from scripts.migration.analyze_linkage_exceptions import read_exception_folders
from scripts.migration.audit_canonical_population import build_name_index, classify_folder

#: Only these audit dispositions are in scope for this batch (per the phase directive).
TARGET_DISPOSITIONS = (
    "source_exists_not_promoted",
    "source_alternate_name_not_promoted",
    "canonical_alternate_name",
)

#: Outcomes, in report order.
OUTCOMES = (
    "safe_person_promotion",
    "existing_person_link",
    "alternate_name_person_link",
    "business_canonicalization",
    "trust_canonicalization",
    "estate_canonicalization",
    "held_ambiguous",
)

#: raw_data keys that carry a stable cross-source identity (compared verbatim, case-insensitive keys).
_STABLE_ID_KEYS = ("ein", "fein", "tin", "entity_id", "client_id", "drake_id",
                   "account", "account_number", "return_id")


def _tokset(name) -> frozenset:
    return frozenset(_name_key(name).split())


def _alt_candidates(folder_tokens, token_list):
    """Entities whose tokens are a subset/superset of the folder's (>=2 tokens, to avoid single-token
    collisions). Used ONLY to FIND alternate-name candidates; a match never resolves a folder on its own —
    a single candidate must still be confirmed by stable identity below."""
    out = []
    if len(folder_tokens) < 2:
        return out
    for toks, obj in token_list:
        if toks and (folder_tokens <= toks or toks <= folder_tokens):
            out.append(obj)
    return out


def stable_ids(source_record_id, raw_data) -> set:
    """The stable identity tokens for a source record: its source_record_id plus any EIN / entity-id /
    client-id style values in raw_data. Used for provenance joins (e.g. VAL6), never name similarity."""
    ids = set()
    if source_record_id:
        ids.add(f"srid:{str(source_record_id).strip().lower()}")
    if isinstance(raw_data, dict):
        lower = {str(k).strip().lower(): v for k, v in raw_data.items()}
        for k in _STABLE_ID_KEYS:
            v = lower.get(k)
            if v not in (None, "", []):
                ids.add(f"{k}:{str(v).strip().lower()}")
    return ids


# --------------------------------------------------------------------------- pure resolver

def build_indexes(unlinked_scs, people):
    """Build the identity/name indexes the resolver needs. Pure — no I/O.

    unlinked_scs: list of source_contact dicts (id, source_system, source_record_id, full_name,
                  normalized_email, normalized_phone, raw_data).
    people:       list of person dicts (id, full_name, first_name, last_name, normalized_email,
                  normalized_phone).
    """
    sc_by_key: dict[str, list] = defaultdict(list)
    sc_tokens: list = []
    for s in unlinked_scs:
        k = _name_key(s.get("full_name"))
        if k:
            sc_by_key[k].append(s)
        t = _tokset(s.get("full_name"))
        if t:
            sc_tokens.append((t, s))

    people_by_key: dict[str, list] = defaultdict(list)
    people_tokens: list = []
    people_by_email: dict[str, list] = defaultdict(list)
    people_by_phone: dict[str, list] = defaultdict(list)
    prow: dict = {}
    for p in people:
        prow[p["id"]] = p
        k = _name_key(p.get("full_name"))
        if k:
            people_by_key[k].append(p["id"])
        t = _tokset(p.get("full_name"))
        if t:
            people_tokens.append((t, p))
        if p.get("normalized_email"):
            people_by_email[p["normalized_email"]].append(p["id"])
        if p.get("normalized_phone"):
            people_by_phone[p["normalized_phone"]].append(p["id"])

    email_counts = Counter(s["normalized_email"] for s in unlinked_scs if s.get("normalized_email"))
    phone_counts = Counter(s["normalized_phone"] for s in unlinked_scs if s.get("normalized_phone"))
    return {
        "sc_by_key": sc_by_key, "sc_tokens": sc_tokens,
        "people_by_key": people_by_key, "people_tokens": people_tokens,
        "people_by_email": people_by_email, "people_by_phone": people_by_phone,
        "prow": prow, "email_counts": email_counts, "phone_counts": phone_counts,
    }


def _identity_person(sc, idx):
    """Existing person(s) the source_contact ties to by stable email/phone identity. Returns a set of ids."""
    ne, np = sc.get("normalized_email"), sc.get("normalized_phone")
    cand = set(idx["people_by_email"].get(ne, ())) | set(idx["people_by_phone"].get(np, ()))
    return cand


def _shared_identity(sc, idx) -> bool:
    ne, np = sc.get("normalized_email"), sc.get("normalized_phone")
    return (bool(ne) and idx["email_counts"].get(ne, 0) > 1) or \
           (bool(np) and idx["phone_counts"].get(np, 0) > 1)


def _classify_single_contact(sc, idx, *, disposition):
    """Deterministic outcome for a folder anchored to exactly ONE source_contact ``sc``."""
    kind = business_kind(sc.get("source_system"), sc.get("raw_data"))
    if kind:
        return f"{kind}_canonicalization", sc, f"relationship_entities:{kind}", \
            f"{sc.get('source_system')} raw_data entity signal"

    people_hit = _identity_person(sc, idx)
    if len(people_hit) > 1:
        return "held_ambiguous", sc, "", f"{len(people_hit)} existing people share this identity"
    if len(people_hit) == 1:
        pid = next(iter(people_hit))
        tp = idx["prow"].get(pid, {})
        share = idx["email_counts"].get(sc.get("normalized_email"), 0) or \
            idx["phone_counts"].get(sc.get("normalized_phone"), 0)
        if not plausible_link(sc.get("first_name"), sc.get("last_name"),
                              tp.get("first_name"), tp.get("last_name"), share):
            return "held_ambiguous", sc, "", "identity maps to a person but names are provably different"
        # canonical_alternate_name = the person exists under a differing NAME representation; the other two
        # dispositions have no existing person by name, so an identity match is a plain existing-person link.
        if disposition == "canonical_alternate_name":
            return "alternate_name_person_link", sc, f"person:{pid}", \
                "stable email/phone identity confirms the alternate-name person"
        return "existing_person_link", sc, f"person:{pid}", \
            "unique non-shared email/phone match to an existing person"

    if _shared_identity(sc, idx):
        return "held_ambiguous", sc, "", "email/phone shared with another unlinked contact"
    if sc.get("normalized_email") or sc.get("normalized_phone"):
        return "safe_person_promotion", sc, "person:new", "unique non-shared email/phone identity"
    return "held_ambiguous", sc, "", "name-only source contact — no stable identity (never guessed)"


def resolve_folder(folder, disposition, idx):
    """Decide the single deterministic outcome for one target folder. Pure — no I/O.

    Returns a reconciliation row dict. ``held_ambiguous`` means no deterministic canonical outcome."""
    fk = _name_key(folder)
    member_keys = _folder_person_keys(folder)

    def row(outcome, sc, target, evidence):
        return {
            "category": outcome, "source_folder": folder, "disposition": disposition,
            "source_contact_id": (sc or {}).get("id", "") if sc else "",
            "source_system": (sc or {}).get("source_system", "") if sc else "",
            "source_record_id": (sc or {}).get("source_record_id", "") if sc else "",
            "source_name": (sc or {}).get("full_name", "") if sc else folder,
            "proposed_target": target, "evidence": evidence,
        }

    # Joint folders are out of scope for these single-name buckets (they live in joint_* dispositions).
    if len(member_keys) > 1:
        return row("held_ambiguous", None, "", "joint folder — out of this batch's scope")

    # 1) anchor by the folder's LITERAL name in source_contacts (strongest: exact provenance).
    exact = idx["sc_by_key"].get(fk, [])
    if len(exact) == 1:
        return row(*_classify_single_contact(exact[0], idx, disposition=disposition))
    if len(exact) > 1:
        kinds = {business_kind(s.get("source_system"), s.get("raw_data")) for s in exact}
        if len(kinds) == 1 and None not in kinds:
            kind = next(iter(kinds))
            return row(f"{kind}_canonicalization", exact[0], f"relationship_entities:{kind}",
                       f"{len(exact)} source records deduped to one {kind}")
        return row("held_ambiguous", None, "", f"{len(exact)} distinct source contacts share this name")

    # 2) no literal anchor -> alternate name. A SINGLE alternate source_contact, confirmed by identity.
    alt_scs = _alt_candidates(set(fk.split()), idx["sc_tokens"])
    if len(alt_scs) == 1:
        return row(*_classify_single_contact(alt_scs[0], idx, disposition=disposition))
    if len(alt_scs) > 1:
        return row("held_ambiguous", None, "", f"{len(alt_scs)} alternate-name source contacts (fuzzy)")

    # 3) canonical_alternate_name with no source anchor: token overlap to a person is FUZZY-ONLY -> held.
    alt_people = _alt_candidates(set(fk.split()), idx["people_tokens"])
    if disposition == "canonical_alternate_name" and len(alt_people) == 1:
        return row("held_ambiguous", None, "",
                   "alternate-name person by tokens only — no stable-identity anchor (not guessed)")
    return row("held_ambiguous", None, "", "no deterministic identity anchor")


# --------------------------------------------------------------------------- DB layer (read-only)

def _load(engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))     # defense in depth
        linked = set(r[0] for r in conn.execute(text(
            "SELECT source_contact_id FROM person_source_links")).all())
        scs = [dict(m) for m in conn.execute(text(
            "SELECT id, source_system, source_record_id, full_name, first_name, last_name, "
            "normalized_email, normalized_phone, raw_data FROM source_contacts")).mappings()]
        people = [dict(m) for m in conn.execute(text(
            "SELECT id, full_name, first_name, last_name, normalized_email, normalized_phone "
            "FROM people")).mappings()]
        entities = [dict(m) for m in conn.execute(text(
            "SELECT id, entity_type, name, details FROM relationship_entities "
            "WHERE person_id IS NULL AND household_id IS NULL AND active IS TRUE")).mappings()]
    unlinked = [s for s in scs if s["id"] not in linked]
    return unlinked, people, entities


def _selected_folders(preview_dir, unlinked, people):
    """Reproduce the audit disposition for each exception folder and keep only the 3 target buckets."""
    people_key, people_tok = build_name_index(
        [(p["id"], p["full_name"], p.get("first_name"), p.get("last_name")) for p in people])
    sc_key, sc_tok = build_name_index(
        [(s["id"], s["full_name"], s.get("first_name"), s.get("last_name")) for s in unlinked])
    selected = []
    for folder in read_exception_folders(preview_dir):
        disp = classify_folder(folder, people_key, people_tok, sc_key, sc_tok)
        if disp in TARGET_DISPOSITIONS:
            selected.append((folder, disp))
    return selected


def plan(preview_dir, engine=None):
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    unlinked, people, entities = _load(engine)
    idx = build_indexes(unlinked, people)
    selected = _selected_folders(preview_dir, unlinked, people)

    rows = [resolve_folder(folder, disp, idx) for folder, disp in selected]
    outcome_counts: Counter = Counter(r["category"] for r in rows)
    by_disposition: dict = defaultdict(Counter)
    for (_folder, disp), r in zip(selected, rows, strict=True):
        by_disposition[disp][r["category"]] += 1
    return {
        "target_folders": len(selected),
        "by_disposition": {d: dict(c) for d, c in by_disposition.items()},
        "outcome_counts": {o: outcome_counts.get(o, 0) for o in OUTCOMES},
        "rows": rows, "entities": entities, "unlinked": unlinked,
    }


def trace_entity(term, unlinked, entities):
    """Provenance trace for a term (e.g. VAL6): does a folder-named source_contact share a STABLE Drake
    identity (source_record_id / EIN / entity-id) with an existing canonical business, or is the only link
    string similarity? Never resolves on name similarity alone."""
    tl = term.strip().lower()
    hit_scs = [s for s in unlinked if tl in (s.get("full_name") or "").lower()
               or tl in str(s.get("raw_data") or "").lower()]
    hit_entities = [e for e in entities if tl in (e.get("name") or "").lower()]

    entity_ids = set()
    entity_scids = set()
    for e in hit_entities:
        det = e.get("details") if isinstance(e.get("details"), dict) else {}
        for scid in det.get("source_contact_ids", []) or []:
            entity_scids.add(scid)
        for srid in det.get("source_record_ids", []) or []:
            if srid:
                entity_ids.add(f"srid:{str(srid).strip().lower()}")

    findings = []
    for s in hit_scs:
        sids = stable_ids(s.get("source_record_id"), s.get("raw_data"))
        shared = sids & entity_ids
        by_contact_ref = s["id"] in entity_scids
        if shared or by_contact_ref:
            verdict, why = "safe_map", (
                f"shared stable identity {sorted(shared)}" if shared
                else "source_contact already recorded in the entity's provenance")
        else:
            verdict, why = "held_string_similarity_only", (
                "name contains the term but NO shared Drake source_record_id/EIN/entity-id with the "
                "existing entity — string similarity is not identity")
        findings.append({"source_contact_id": s["id"], "source_name": s.get("full_name"),
                         "source_system": s.get("source_system"),
                         "source_record_id": s.get("source_record_id"),
                         "stable_ids": sorted(sids), "verdict": verdict, "evidence": why})
    return {"term": term, "matched_entities": [(e["id"], e["name"]) for e in hit_entities],
            "entity_provenance_ids": sorted(entity_ids), "findings": findings}


# --------------------------------------------------------------------------- output

_CSV_FIELDS = ["category", "source_folder", "disposition", "source_contact_id", "source_system",
               "source_record_id", "source_name", "proposed_target", "evidence"]


def write_reconciliation(rows, out_path):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _CSV_FIELDS})


def _print(res, traces) -> None:
    print(f"=== next deterministic batch — {res['target_folders']} in-scope folders "
          f"(source_exists_not_promoted + source_alternate_name_not_promoted + canonical_alternate_name) ===")
    print("\n=== outcome counts (stable identity/provenance only; never fuzzy name) ===")
    labels = {
        "safe_person_promotion": "safe new person promotions",
        "existing_person_link": "safe links to existing people",
        "alternate_name_person_link": "safe alternate-name links to existing canonical people",
        "business_canonicalization": "business entity candidates",
        "trust_canonicalization": "trust entity candidates",
        "estate_canonicalization": "estate entity candidates",
        "held_ambiguous": "still ambiguous (held — excluded from the batch)",
    }
    for o in OUTCOMES:
        print(f"  {labels[o]}: {res['outcome_counts'][o]}")
    biz = sum(res["outcome_counts"][o] for o in
              ("business_canonicalization", "trust_canonicalization", "estate_canonicalization"))
    deterministic = sum(res["outcome_counts"][o] for o in OUTCOMES if o != "held_ambiguous")
    print(f"\n  business/entity candidates (all kinds): {biz}")
    print(f"  total deterministic (batch): {deterministic}   held_ambiguous: "
          f"{res['outcome_counts']['held_ambiguous']}")

    print("\n=== outcomes by source disposition ===")
    for disp in TARGET_DISPOSITIONS:
        if disp in res["by_disposition"]:
            print(f"  [{disp}]")
            for o, n in sorted(res["by_disposition"][disp].items()):
                print(f"      {o}: {n}")

    for tr in traces:
        print(f"\n=== provenance trace: {tr['term']!r} ===")
        print(f"  matched existing entities: {tr['matched_entities']}")
        print(f"  entity provenance stable ids: {tr['entity_provenance_ids']}")
        for fnd in tr["findings"]:
            print(f"  - sc#{fnd['source_contact_id']} {fnd['source_name']!r} [{fnd['source_system']}] "
                  f"srid={fnd['source_record_id']}")
            print(f"      stable_ids={fnd['stable_ids']}")
            print(f"      VERDICT: {fnd['verdict']} — {fnd['evidence']}")
    print("\nPlan complete (read-only). No writes, no file movement, no storage_uri/document_sources "
          "changes; the 17,042 already-relocated documents are untouched.")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.plan_next_linkage_batch",
                                description="Read-only plan of the next deterministic canonical/linkage batch.")
    p.add_argument("preview_dir", help="Linkage-remediation preview directory (with exceptions.csv).")
    p.add_argument("--search", action="append", default=[], help="Provenance trace term(s), e.g. 'VAL6'.")
    p.add_argument("--out", default=None, help="Write the frozen batch reconciliation.csv to this path.")
    args = p.parse_args(argv)
    if not os.path.isfile(os.path.join(args.preview_dir, "exceptions.csv")):
        print(f"ERROR: no exceptions.csv in {args.preview_dir}")
        return 2

    res = plan(args.preview_dir)
    out_path = args.out or os.path.join(args.preview_dir, "next_batch_reconciliation.csv")
    write_reconciliation(res["rows"], out_path)

    traces = [trace_entity(term, res["unlinked"], res["entities"])
              for term in (args.search or ["VAL6"])]
    _print(res, traces)
    print(f"\nfrozen batch reconciliation written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
