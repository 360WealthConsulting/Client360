"""READ-ONLY deterministic plan for the Drake joint-return / household canonicalization gap.

Systemic finding: Drake joint (MFJ) returns establish a taxpayer+spouse couple, but many couples have no
canonical Client360 household, and joint-return PDFs are owned by a single person instead of a shared
household. This planner classifies every distinct joint couple and identifies the DETERMINISTICALLY
provable joint documents — WITHOUT writing anything and WITHOUT name-similarity inference. The controlling
key is the stable Drake identifier hash and its provenance (``drake_identity.primary_person_id`` /
``drake_identity_match_candidates``), never a name.

Couple buckets (one per distinct {taxpayer_hash, spouse_hash} pair):
  already_correct_shared_household   both hashes -> canonical people already sharing one household
  single_person_multi_hash_no_action both hashes -> the SAME canonical person (self-couple / duplicate
                                     hashes): terminal, no couple, no household to form, no action
  both_canonical_safe_household      both hashes -> two DISTINCT canonical people, no shared household yet
  one_canonical_plus_promotable      one hash -> canonical person; the other is a unique unresolved Drake
                                     identity (no existing-person candidates) -> safe spouse promotion
  both_promotable                    neither hash canonical, both unique unresolved identities
  ambiguous_hold                     a hash maps to existing-person match candidates (needs human review)
  insufficient_provenance_hold       a spouse hash is missing, or an identity has no usable provenance

Document analysis is SEPARATE and deliberately narrow: the broad person-owned-joint-tax-doc count is NOT
an apply set. A document is reported as provably joint ONLY when its owner is a canonical person in a
deterministically establishable couple (the first three buckets) AND the document is a return document
whose tax year is a year that couple filed jointly per Drake.

STRICTLY READ-ONLY: SELECT only (+ session read-only). No person_id/household_id changes, no person/
household creation, no file movement, no storage_uri / document_sources changes, no linkage-exception
resolution, no folder_resolution_decisions writes.

Usage::
    python -m scripts.migration.plan_joint_household_remediation [--out <reconciliation.csv>]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict

_YEAR_RE = re.compile(r"20\d{2}")
_RETURN_DOC_TYPES = {"federal_return", "state_return"}

COUPLE_BUCKETS = (
    "already_correct_shared_household",
    "single_person_multi_hash_no_action",
    "both_canonical_safe_household",
    "one_canonical_plus_promotable",
    "both_promotable",
    "ambiguous_hold",
    "insufficient_provenance_hold",
)
# Buckets whose household is deterministically establishable (documents may be planned against them).
_ESTABLISHABLE = {"already_correct_shared_household", "both_canonical_safe_household",
                  "one_canonical_plus_promotable"}


# --------------------------------------------------------------------------- pure classification

def hash_status(h, id_index, cand_index):
    """Deterministic status of a single Drake identifier hash. Never uses names."""
    if not h:
        return "missing"
    ident = id_index.get(h)
    if ident is None:
        return "insufficient"                       # no drake_identity provenance for this hash
    if ident.get("primary_person_id"):
        return "canonical"                          # confirmed canonical person (drake identity review)
    if cand_index.get(h):
        return "ambiguous"                          # maps to existing-person candidate(s) -> human review
    if (ident.get("taxpayer_name") or "").strip():
        return "promotable"                         # unique unresolved identity with a name -> safe promote
    return "insufficient"


def classify_couple(tp_hash, sp_hash, id_index, cand_index, prow):
    """Classify one couple by its two stable Drake hashes. Returns (bucket, detail)."""
    ts = hash_status(tp_hash, id_index, cand_index)
    ss = hash_status(sp_hash, id_index, cand_index)
    tp_person = (id_index.get(tp_hash) or {}).get("primary_person_id")
    sp_person = (id_index.get(sp_hash) or {}).get("primary_person_id")
    detail = {"taxpayer_hash": tp_hash, "spouse_hash": sp_hash, "taxpayer_status": ts,
              "spouse_status": ss, "taxpayer_person_id": tp_person, "spouse_person_id": sp_person}

    statuses = {ts, ss}
    if "missing" in statuses or "insufficient" in statuses:
        return "insufficient_provenance_hold", detail
    if "ambiguous" in statuses:
        return "ambiguous_hold", detail
    if ts == "canonical" and ss == "canonical":
        tp_hh = (prow.get(tp_person) or {}).get("household_id")
        sp_hh = (prow.get(sp_person) or {}).get("household_id")
        detail["taxpayer_household_id"] = tp_hh
        detail["spouse_household_id"] = sp_hh
        # Degenerate "self-couple": both stable Drake hashes resolve to the SAME canonical person (one
        # person who appears as both taxpayer and spouse across years, or duplicate hashes). There is no
        # second person and no couple to house — this is terminal, no-action reporting (the authoritative
        # remediate_joint_households already no-ops it). It is NOT a both_canonical household candidate.
        if tp_person and sp_person and tp_person == sp_person:
            return "single_person_multi_hash_no_action", detail
        if tp_person and sp_person and tp_person != sp_person and tp_hh and sp_hh and tp_hh == sp_hh:
            return "already_correct_shared_household", detail
        return "both_canonical_safe_household", detail
    if "canonical" in statuses and "promotable" in statuses:
        return "one_canonical_plus_promotable", detail
    if ts == "promotable" and ss == "promotable":
        return "both_promotable", detail
    return "insufficient_provenance_hold", detail


def doc_year(doc):
    """Deterministic tax year for a document: Drake tags.tax_year, else a 20xx in the filename."""
    tags = doc.get("tags") if isinstance(doc.get("tags"), dict) else {}
    ty = (tags.get("tax_year") or "").strip() if isinstance(tags.get("tax_year"), str) else tags.get("tax_year")
    if ty and str(ty).isdigit():
        return int(ty)
    m = _YEAR_RE.search(doc.get("original_name") or "")
    return int(m.group(0)) if m else None


def is_return_document(doc):
    tags = doc.get("tags") if isinstance(doc.get("tags"), dict) else {}
    if tags.get("drake_doc_type") in _RETURN_DOC_TYPES:
        return True
    name = (doc.get("original_name") or "").lower()
    if doc.get("category") == "tax_document":
        return True
    return "1040" in name or "tax return" in name or "return" in name


# --------------------------------------------------------------------------- DB layer (read-only)

def _load(engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))

        def regclass(name):
            return conn.execute(text("SELECT to_regclass(:n)"), {"n": name}).scalar()

        joint = []
        id_index: dict = {}
        cand_index: dict = defaultdict(list)
        if regclass("public.drake_client_returns"):
            joint = [dict(m) for m in conn.execute(text(
                "SELECT id, tax_year, filing_status, taxpayer_identifier_hash, spouse_identifier_hash, "
                "taxpayer_first_name, taxpayer_last_name, spouse_first_name, spouse_last_name "
                "FROM drake_client_returns "
                "WHERE btrim(coalesce(spouse_first_name,'')) <> '' OR spouse_identifier_hash IS NOT NULL"
            )).mappings()]
        if regclass("public.drake_identity"):
            id_index = {m["identifier_hash"]: dict(m) for m in conn.execute(text(
                "SELECT identifier_hash, primary_person_id, taxpayer_name, spouse_name FROM drake_identity"
            )).mappings()}
        if regclass("public.drake_identity_match_candidates"):
            for m in conn.execute(text(
                "SELECT identifier_hash, person_id, status FROM drake_identity_match_candidates "
                "WHERE status IN ('pending','deferred')")).mappings():
                cand_index[m["identifier_hash"]].append(dict(m))

        people = {m["id"]: dict(m) for m in conn.execute(text(
            "SELECT id, household_id, first_name, last_name, full_name FROM people")).mappings()}
        docs = [dict(m) for m in conn.execute(text(
            "SELECT id, person_id, household_id, organization_id, original_name, category, storage_uri, "
            "tags FROM documents WHERE status <> 'deleted' AND person_id IS NOT NULL "
            "AND household_id IS NULL AND organization_id IS NULL")).mappings()]
    return joint, id_index, dict(cand_index), people, docs


def plan(engine=None):
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    joint, id_index, cand_index, prow, docs = _load(engine)

    # distinct couples keyed on the unordered pair of stable hashes; keep first-seen roles + all years.
    couples: dict = {}
    for r in joint:
        tp, sp = r["taxpayer_identifier_hash"], r["spouse_identifier_hash"]
        key = frozenset(h for h in (tp, sp) if h) or ("__name_only__", r["taxpayer_last_name"])
        c = couples.setdefault(key, {"tp": tp, "sp": sp, "years": set(), "has_both_hashes": bool(tp and sp)})
        if r.get("tax_year"):
            c["years"].add(int(r["tax_year"]))

    bucket_counts: Counter = Counter()
    couple_rows: list = []
    person_to_couple: dict = {}       # canonical person -> (bucket, years, spouse_person_id, hh)
    for _key, c in couples.items():
        if not c["has_both_hashes"]:
            bucket_counts["insufficient_provenance_hold"] += 1
            couple_rows.append({"bucket": "insufficient_provenance_hold", "reason": "missing spouse hash",
                                "years": sorted(c["years"])})
            continue
        bucket, detail = classify_couple(c["tp"], c["sp"], id_index, cand_index, prow)
        bucket_counts[bucket] += 1
        detail.update({"bucket": bucket, "years": sorted(c["years"])})
        couple_rows.append(detail)
        if bucket in _ESTABLISHABLE:
            for pid, other in ((detail.get("taxpayer_person_id"), detail.get("spouse_person_id")),
                               (detail.get("spouse_person_id"), detail.get("taxpayer_person_id"))):
                if pid:
                    person_to_couple[pid] = {
                        "bucket": bucket, "years": c["years"], "spouse_person_id": other,
                        "household_id": (prow.get(pid) or {}).get("household_id")}

    # BROAD survey (NOT the apply set): person-owned tax-ish documents whose owner is in an establishable
    # couple, for a couple MFJ year. This is permissive (category='tax_document' counts) — it is a survey
    # metric, deliberately NOT the deterministic re-ownership standard.
    doc_rows: list = []
    doc_bucket: Counter = Counter()
    for d in docs:
        cp = person_to_couple.get(d["person_id"])
        if cp is None:
            continue
        if not is_return_document(d):
            continue
        y = doc_year(d)
        if y is None or y not in cp["years"]:
            continue
        proposed_hh = cp["household_id"] or "new (pending household creation)"
        doc_bucket[cp["bucket"]] += 1
        doc_rows.append({
            "document_id": d["id"], "current_owner": f"person:{d['person_id']}",
            "proposed_household": proposed_hh, "tax_year": y, "couple_bucket": cp["bucket"],
            "evidence": (f"owner person {d['person_id']} is a canonical spouse in a Drake MFJ couple "
                         f"(bucket {cp['bucket']}); return document year {y} is a joint-filing year "
                         f"{sorted(cp['years'])}"),
            "storage_uri": d.get("storage_uri"),
            "relocation_required": True,   # owner area Clients -> Households changes the path projection
        })

    # STRICT, authoritative re-ownable count — delegate to the Stage B deterministic classifier so this
    # metric means EXACTLY what re-ownership means (established household, correct person/household
    # relationship, personal return, NOT business/entity, joint signature naming both members, MFJ year).
    # Single source of truth: never a second copy of the Stage B rules. Lazy import avoids a circular
    # import (joint_document_reownership imports this module).
    from app.services.migration import joint_document_reownership as _jd
    provable_joint_documents = _jd.preview(engine)["reownable"]

    return {
        "joint_returns": len(joint),
        "distinct_joint_couples": len(couples),
        "couple_bucket_counts": {b: bucket_counts.get(b, 0) for b in COUPLE_BUCKETS},
        "person_owned_joint_docs_total": len(docs),
        # broad survey (permissive) — NOT the apply set; renamed so it is never mistaken for the proof set
        "candidate_person_owned_tax_docs_in_couples": len(doc_rows),
        "candidate_person_owned_tax_docs_by_bucket": dict(doc_bucket),
        # strict, matches the Stage B deterministic re-ownership proof standard (authoritative)
        "provable_joint_documents": provable_joint_documents,
        "couple_rows": couple_rows, "doc_rows": doc_rows,
    }


# --------------------------------------------------------------------------- output

_CSV_FIELDS = ["document_id", "current_owner", "proposed_household", "tax_year", "couple_bucket",
               "evidence", "storage_uri", "relocation_required"]


def write_reconciliation(doc_rows, out_path):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for r in doc_rows:
            w.writerow({k: r.get(k, "") for k in _CSV_FIELDS})


def _print(res):
    print(f"joint_returns: {res['joint_returns']}   distinct_joint_couples: {res['distinct_joint_couples']}")
    print("\n=== couple classification (deterministic; stable Drake hashes only) ===")
    for b in COUPLE_BUCKETS:
        print(f"  {b}: {res['couple_bucket_counts'][b]}")
    print("\n=== joint-document analysis ===")
    print(f"  person_owned_joint_docs_total (all person-owned candidates): "
          f"{res['person_owned_joint_docs_total']}")
    print(f"  candidate_person_owned_tax_docs_in_couples (BROAD survey, NOT apply): "
          f"{res['candidate_person_owned_tax_docs_in_couples']}")
    for b, n in sorted(res["candidate_person_owned_tax_docs_by_bucket"].items()):
        print(f"      {b}: {n}")
    print(f"  provable_joint_documents (STRICT — Stage B deterministic re-ownership standard): "
          f"{res['provable_joint_documents']}")
    print("\nRead-only plan complete. No writes, no person/household/document changes, no file movement.")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.plan_joint_household_remediation",
                                description="Read-only deterministic Drake joint-return / household plan.")
    p.add_argument("--out", default=None, help="Write the provable-joint-documents reconciliation CSV here.")
    args = p.parse_args(argv)
    res = plan()
    if args.out:
        write_reconciliation(res["doc_rows"], args.out)
    _print(res)
    if args.out:
        print(f"\nprovable-joint-documents reconciliation written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
