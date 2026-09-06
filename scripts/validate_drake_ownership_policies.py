"""READ-ONLY comparison of candidate Drake ownership policies. Writes nothing, changes no policy.

This is an ANALYSIS tool, not an implementation. ``app/services/drake_sibling_ownership.py`` is left
exactly as it is; the policy variants below are computed here so three rules can be measured against
the same historical population before any of them is adopted.

RULE 7 ON HISTORICAL DOCUMENTS
    ``propose_document_owner`` refuses an already-owned document before it reaches analysis, so the
    first retrospective could not test "no HIGH proposal for a different owner" on any historical
    row — the guard that actually blocks the joint return 121833. ``ungated_proposal`` below reaches
    the SAME primitives the engine uses (``propose_drake_document_owner`` first, then
    ``analyze_identity``) without the ownership gate. It is deliberately a thin re-composition of the
    engine's own calls rather than a second engine: no scoring, no thresholds and no evidence rules
    are restated here.

JOINT_HOUSEHOLD_GUARD_V1 (candidate, not adopted)
    For a sibling-derived PERSON candidate P: if the document also names a person Q who is an ACTIVE
    MEMBER OF P'S OWN EXISTING HOUSEHOLD, the document is describing both members of that household,
    and picking one of them automatically is a granularity guess. Automatic person assignment HOLDS
    unless an independent HIGH engine proposal confirms P.

    Verified firm staff never count as Q. Households are read from existing membership only — never
    inferred from a shared surname. The guard only BLOCKS person assignment; it never assigns the
    household instead.

USAGE
    python scripts/validate_drake_ownership_policies.py [--limit N] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.db import documents, engine, metadata  # noqa: E402
from app.services import drake_sibling_ownership as dso  # noqa: E402
from app.services.document_owner_proposal import (  # noqa: E402
    analyze_identity,
    build_match_indexes,
    extract_document_text,
    is_tax_document,
)

document_sources = metadata.tables["document_sources"]
FIRST_SYNC_AT = "2026-09-05 22:53:00-04"

NO_HIGH = "NO_HIGH_PROPOSAL"
HIGH_SAME = "HIGH_SAME_OWNER"
HIGH_DIFF = "HIGH_DIFFERENT_OWNER"


def ungated_proposal(conn, document_id, row, idx, text_value, folder, drake_source):
    """The engine's own proposal for a document, with the ``already_owned`` gate skipped.

    Reuses ``propose_drake_document_owner`` then ``analyze_identity`` — the exact two calls
    ``propose_document_owner`` makes — so this measures the deployed engine, not a copy of it."""
    proposal = None
    if drake_source:
        from app.services.drake_document_owner import propose_drake_document_owner
        try:
            proposal = propose_drake_document_owner(document_id, conn=conn)
        except Exception:  # noqa: BLE001 — analysis must never fail on one document
            proposal = None
    if proposal is None:
        proposal = analyze_identity(text_value, row["original_name"], folder, idx,
                                    tax_document=is_tax_document(row, drake_source=drake_source))
    return proposal


def rule7(proposal, candidate):
    if (proposal or {}).get("confidence") != "HIGH":
        return NO_HIGH
    got = (proposal.get("proposed_entity_type"), proposal.get("proposed_entity_id"))
    return HIGH_SAME if got == tuple(candidate) else HIGH_DIFF


def joint_household_guard(candidate, signals, idx, r7):
    """JOINT_HOUSEHOLD_GUARD_V1. Returns (blocks: bool, second_member: int|None)."""
    ptype, pid = candidate
    if ptype != "person":
        return False, None
    household = (idx["pid"].get(pid) or {}).get("household_id")
    if not household:
        return False, None
    members = idx["members"].get(household, set())
    staff = set(idx.get("staff") or ())
    named = {p for p, s in signals["sig"].items() if "name" in s}
    others = (named & members) - {pid} - staff
    if not others:
        return False, None
    if r7 == HIGH_SAME:                       # an independent HIGH confirmation overrides the hold
        return False, sorted(others)[0]
    return True, sorted(others)[0]


def _row(conn, did):
    return conn.execute(
        select(documents.c.id, documents.c.original_name, documents.c.person_id,
               documents.c.household_id, documents.c.organization_id, documents.c.status,
               documents.c.archived, documents.c.storage_uri, documents.c.storage_path,
               documents.c.tags, documents.c.category, documents.c.classification,
               documents.c.subcategory).where(documents.c.id == did)).mappings().first()


def score(conn, did, idx, *, retrospective):
    """Everything three policies need for one document. Pure measurement."""
    row = _row(conn, did)
    src = dso.drake_source(conn, did)
    if src is None or not src["available"] or not dso.CLIENT_ID_RE.match(
            str(src["source_external_id"] or "")):
        return {"skip": "no_source_or_client_id"}
    cid = src["source_external_id"]
    tuples, sibs = dso.sibling_owners(conn, cid, exclude_document_id=did)
    if not tuples:
        return {"skip": "no_owned_sibling"}
    if len(tuples) > 1:
        return {"skip": "sibling_owners_disagree"}
    candidate = dso.owner_of(next(iter(tuples)))
    ok, why = dso.owner_is_eligible(candidate, idx)
    if not ok:
        return {"skip": why}
    if not retrospective and any(row[c] for c in ("person_id", "household_id", "organization_id")):
        return {"skip": "already_owned"}

    path = None
    if row["storage_uri"] and Path(row["storage_uri"]).is_absolute():
        path = Path(row["storage_uri"])
    elif row["storage_path"]:
        path = Path(row["storage_path"])
    text_value, method = extract_document_text(conn, row, path, ocr=False)
    from app.services import document_high_validation as hv
    sig, households, orgs = hv._doc_signals(text_value, idx)
    folder = (row["tags"] or {}).get("taxdome_folder")
    signals = {"sig": sig, "households": households, "orgs": orgs, "folder": folder,
               "method": method, "text_len": len(text_value or "")}

    strict, excluded = dso.contradictions(candidate, signals, idx, exclude_firm=False)
    tuned, _ = dso.contradictions(candidate, signals, idx, exclude_firm=True)
    proposal = ungated_proposal(conn, did, row, idx, text_value, folder,
                                src is not None)
    r7 = rule7(proposal, candidate)
    guard_blocks, second = joint_household_guard(candidate, signals, idx, r7)
    actual = None
    if any(row[c] for c in ("person_id", "household_id", "organization_id")):
        actual = dso.owner_of((row["person_id"], row["household_id"], row["organization_id"]))
    return {"skip": None, "document_id": did, "filename": row["original_name"], "client_id": cid,
            "candidate": candidate, "actual": actual, "siblings": sibs,
            "strict": strict, "tuned": tuned, "firm_excluded": excluded, "rule7": r7,
            "engine_proposal": [(proposal or {}).get("proposed_entity_type"),
                                (proposal or {}).get("proposed_entity_id"),
                                (proposal or {}).get("confidence")],
            "guard_blocks": guard_blocks, "guard_second_member": second,
            "signals": {"named": sorted(p for p, s in sig.items() if "name" in s),
                        "strong": sorted(p for p, s in sig.items() if s & {"email", "phone"}),
                        "households": sorted(households), "orgs": sorted(orgs)},
            "text_len": signals["text_len"]}


def policies(s):
    """A -> strict, B -> tuned, C -> tuned + rule 7 + joint guard. Each returns pass/hold."""
    a = not s["strict"]
    b = not s["tuned"]
    c = b and s["rule7"] != HIGH_DIFF and not s["guard_blocks"]
    return {"A": a, "B": b, "C": c}


def historical_ids(conn, limit=None):
    stmt = (select(documents.c.id)
            .select_from(documents.join(document_sources,
                                        document_sources.c.document_id == documents.c.id))
            .where(document_sources.c.source_system == "Drake",
                   documents.c.status != "deleted", documents.c.archived.is_(False),
                   documents.c.created_at < text(f"timestamptz '{FIRST_SYNC_AT}'"))
            .where((documents.c.person_id.isnot(None)) | (documents.c.household_id.isnot(None))
                   | (documents.c.organization_id.isnot(None)))
            .distinct().order_by(documents.c.id))
    if limit:
        stmt = stmt.limit(limit)
    return [r[0] for r in conn.execute(stmt)]


def new33_ids(conn):
    return [r[0] for r in conn.execute(
        select(documents.c.id)
        .select_from(documents.join(document_sources,
                                    document_sources.c.document_id == documents.c.id))
        .where(document_sources.c.source_system == "Drake",
               documents.c.status != "deleted", documents.c.archived.is_(False),
               documents.c.created_at >= text(f"timestamptz '{FIRST_SYNC_AT}'"))
        .distinct().order_by(documents.c.id))]


def run(limit=None, out=None, log=print):
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        log(f"transaction_read_only = {conn.execute(text('SHOW transaction_read_only')).scalar()}")
        idx = build_match_indexes(conn)

        hist, skips = [], Counter()
        ids = historical_ids(conn, limit)
        log(f"historical owned Drake documents = {len(ids)}")
        for n, did in enumerate(ids, 1):
            if n % 100 == 0:
                log(f"  ...{n}/{len(ids)}")
            s = score(conn, did, idx, retrospective=True)
            if s["skip"]:
                skips[s["skip"]] += 1
                continue
            s["policies"] = policies(s)
            hist.append(s)

        new33 = []
        for did in new33_ids(conn):
            s = score(conn, did, idx, retrospective=False)
            if not s.get("skip"):
                s["policies"] = policies(s)
            new33.append(s if s.get("skip") is None else {**s, "document_id": did})

    return {"historical": hist, "skips": dict(skips), "new33": new33,
            "historical_total": len(ids)}


def summarize(r, log=print):
    hist = r["historical"]
    log("\n=== POLICY COMPARISON (historical, retrospectively eligible) ===")
    log(f"  eligible population = {len(hist)}   skips = {r['skips']}")
    out = {}
    for key, name in (("A", "STRICT"), ("B", "TUNED"), ("C", "POLICY_C")):
        p = [s for s in hist if s["policies"][key]]
        match = [s for s in p if s["candidate"] == s["actual"]]
        diff = [s for s in p if s["candidate"] != s["actual"]]
        out[key] = {"pass": len(p), "match": len(match), "differs": len(diff),
                    "held": len(hist) - len(p),
                    "differ_docs": [{"document_id": s["document_id"], "filename": s["filename"],
                                     "client_id": s["client_id"], "candidate": s["candidate"],
                                     "actual": s["actual"], "siblings": s["siblings"],
                                     "strict": s["strict"], "tuned": s["tuned"],
                                     "rule7": s["rule7"], "engine": s["engine_proposal"],
                                     "signals": s["signals"]} for s in diff]}
        log(f"\n  {name}_PASS            = {out[key]['pass']}")
        log(f"  {name}_MATCH_ACTUAL    = {out[key]['match']}")
        log(f"  {name}_DIFFERS_ACTUAL  = {out[key]['differs']}")
        log(f"  {name}_AMBIGUOUS_OR_HELD = {out[key]['held']}")
        for d in out[key]["differ_docs"]:
            log(f"    DIFFERS doc={d['document_id']} cand={d['candidate']} actual={d['actual']} "
                f"rule7={d['rule7']} engine={d['engine']}")
            log(f"       {d['filename'][:64]!r} siblings={d['siblings']}")
            log(f"       strict={d['strict']} tuned={d['tuned']} signals={d['signals']}")

    tuned_pass = [s for s in hist if s["policies"]["B"]]
    r7_blocked = [s for s in tuned_pass if s["rule7"] == HIGH_DIFF]
    guard_blocked = [s for s in tuned_pass if s["rule7"] != HIGH_DIFF and s["guard_blocks"]]
    log(f"\n  STRICT_PASS_BEFORE_RULE7   = {out['A']['pass']}")
    log(f"  TUNED_PASS_BEFORE_RULE7    = {out['B']['pass']}")
    log(f"  RULE7_DIFFERENT_OWNER_BLOCKS (within tuned pass) = {len(r7_blocked)}")
    for s in r7_blocked:
        log(f"    R7 doc={s['document_id']} cand={s['candidate']} engine={s['engine_proposal']} "
            f"actual={s['actual']} would_have_been={'CORRECT' if s['candidate']==s['actual'] else 'WRONG'}")
    log(f"  JOINT_GUARD_BLOCKS = {len(guard_blocked)}")
    fn = [s for s in guard_blocked if s["candidate"] == s["actual"]]
    tp = [s for s in guard_blocked if s["candidate"] != s["actual"]]
    log(f"    would have been CORRECT person (false negative) = {len(fn)}")
    log(f"    would have been WRONG granularity (caught)      = {len(tp)}")
    for s in tp:
        log(f"      CAUGHT doc={s['document_id']} cand={s['candidate']} actual={s['actual']} "
            f"second_member={s['guard_second_member']}")
    out["rule7_blocks"] = len(r7_blocked)
    out["guard_blocks"] = len(guard_blocked)
    out["guard_false_negatives"] = len(fn)
    out["guard_true_positives"] = len(tp)
    out["guard_fn_docs"] = [s["document_id"] for s in fn]

    log("\n=== THE 33 UNDER EACH POLICY ===")
    n = r["new33"]
    for key, name in (("A", "STRICT"), ("B", "TUNED"), ("C", "POLICY_C")):
        elig = [s for s in n if not s.get("skip") and s["policies"][key]]
        log(f"  {name}: eligible={len(elig)} -> {sorted(s['document_id'] for s in elig)}")
    out["new33"] = {k: sorted(s["document_id"] for s in n
                              if not s.get("skip") and s["policies"][k]) for k in "ABC"}
    out["new33_skips"] = Counter(s["skip"] for s in n if s.get("skip"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate_drake_ownership_policies")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", dest="out", default=None)
    a = ap.parse_args(argv)
    r = run(limit=a.limit)
    s = summarize(r)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"summary": s, "detail": r}, fh, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
