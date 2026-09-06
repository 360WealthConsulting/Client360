"""READ-ONLY retrospective: would the Drake carry-over rule have reproduced the owners humans
already chose? Writes nothing, ever.

WHY THIS EXISTS
    The tuned contradiction policy (ignore verified firm staff / firm-self entities) changes the
    strict-safe population of the 33 new Drake documents from 4 to 11. Four hand-inspected documents
    are not evidence that a rule is safe. This scores the rule against the ~790 Drake documents that
    already carry a human-approved owner and reports, per policy, how often the rule would have
    produced that same owner — and, critically, how often the TUNED policy clears a document the
    strict policy blocked and gets it WRONG.

    A single materially wrong newly-cleared document is a reason to reconsider the policy, not to
    tune around it, so wrong cases are reported individually with their full evidence.

NON-CIRCULARITY
    ``evaluate(..., retrospective=True)`` excludes the target from its own sibling set, so a
    document's own ownership can never contribute to the evidence meant to predict it. It skips only
    the all-NULL rule; every other rule runs exactly as it would in production.

USAGE
    python scripts/retrospect_drake_sibling_ownership.py [--limit N] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.db import documents, engine, metadata  # noqa: E402
from app.services.document_owner_proposal import build_match_indexes  # noqa: E402
from app.services.drake_sibling_ownership import evaluate, owner_of  # noqa: E402

document_sources = metadata.tables["document_sources"]

#: Documents created by the first repaired Drake sync are the POPULATION UNDER TEST, not history.
FIRST_SYNC_AT = "2026-09-05 22:53:00-04"


def historical_owned(conn, *, limit=None):
    stmt = (select(documents.c.id, documents.c.person_id, documents.c.household_id,
                   documents.c.organization_id)
            .select_from(documents.join(document_sources,
                                        document_sources.c.document_id == documents.c.id))
            .where(document_sources.c.source_system == "Drake",
                   documents.c.status != "deleted",
                   documents.c.archived.is_(False),
                   documents.c.created_at < text(f"timestamptz '{FIRST_SYNC_AT}'"))
            .where((documents.c.person_id.isnot(None)) | (documents.c.household_id.isnot(None))
                   | (documents.c.organization_id.isnot(None)))
            .order_by(documents.c.id))
    if limit:
        stmt = stmt.limit(limit)
    return conn.execute(stmt).mappings().all()


def run(*, limit=None, out=None, log=print):
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        log(f"transaction_read_only = {conn.execute(text('SHOW transaction_read_only')).scalar()}")
        idx = build_match_indexes(conn)
        docs = historical_owned(conn, limit=limit)
        log(f"HISTORICAL_DRAKE_OWNED_TOTAL = {len(docs)}")

        tally = {"eligible_for_retrospective": 0, "no_sibling": 0, "multi_sibling": 0,
                 "owner_ineligible": 0, "other_excluded": 0}
        pol = {p: Counter() for p in ("strict", "tuned")}
        newly = {"correct": [], "wrong": [], "ambiguous": []}
        for n, d in enumerate(docs, 1):
            if n % 100 == 0:
                log(f"  ...{n}/{len(docs)}")
            actual = owner_of((d["person_id"], d["household_id"], d["organization_id"]))
            v = evaluate(conn, d["id"], idx, exclude_firm=True, retrospective=True)
            reasons = set(v["reasons"])
            if "no_owned_sibling" in reasons:
                tally["no_sibling"] += 1
                continue
            if "sibling_owners_disagree" in reasons:
                tally["multi_sibling"] += 1
                continue
            if reasons & {"owner_not_eligible", "owner_is_firm_staff_or_entity"}:
                tally["owner_ineligible"] += 1
                continue
            if reasons - {"contradicted"}:
                tally["other_excluded"] += 1
                continue
            tally["eligible_for_retrospective"] += 1
            cand = tuple(v["candidate"])
            for policy, cons in (("strict", v["contradictions_strict"]),
                                 ("tuned", v["contradictions_tuned"])):
                if cons:
                    pol[policy]["block"] += 1
                else:
                    pol[policy]["pass"] += 1
                    pol[policy]["match" if cand == actual else "differs"] += 1
            if v["contradictions_tuned"] or not v["contradictions_strict"]:
                continue                       # not newly cleared by the tuning
            rec = {"document_id": d["id"], "client_id": v["client_id"],
                   "sibling_derived": list(cand), "actual": list(actual),
                   "siblings": v["siblings"], "firm_signals_removed": v["firm_excluded"],
                   "strict_contradictions": v["contradictions_strict"],
                   "remaining_after_exclusion": v["contradictions_tuned"],
                   "engine": v["engine"], "text_len": v["text_len"]}
            newly["correct" if cand == actual else "wrong"].append(rec)

        result = {"total": len(docs), "tally": tally,
                  "strict": dict(pol["strict"]), "tuned": dict(pol["tuned"]),
                  "newly_cleared": {k: len(v) for k, v in newly.items()}, "newly": newly}
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="retrospect_drake_sibling_ownership")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", dest="out", default=None)
    a = ap.parse_args(argv)
    r = run(limit=a.limit, out=a.out)
    t, s, u = r["tally"], r["strict"], r["tuned"]
    print("\n=== RETROSPECTIVE ===")
    print(f"  HISTORICAL_DRAKE_OWNED_TOTAL            = {r['total']}")
    print(f"  HISTORICAL_ELIGIBLE_FOR_RETROSPECTIVE   = {t['eligible_for_retrospective']}")
    print(f"  HISTORICAL_EXCLUDED_NO_SIBLING          = {t['no_sibling']}")
    print(f"  HISTORICAL_EXCLUDED_MULTI_OWNER_SIBLING = {t['multi_sibling']}")
    print(f"  HISTORICAL_EXCLUDED_OWNER_INELIGIBLE    = {t['owner_ineligible']}")
    print(f"  HISTORICAL_EXCLUDED_OTHER               = {t['other_excluded']}")
    for name, p in (("STRICT", s), ("TUNED", u)):
        print(f"\n  {name}_WOULD_PASS                 = {p.get('pass', 0)}")
        print(f"  {name}_WOULD_BLOCK                = {p.get('block', 0)}")
        print(f"  {name}_PASS_MATCHES_ACTUAL_OWNER  = {p.get('match', 0)}")
        print(f"  {name}_PASS_DIFFERS_FROM_ACTUAL   = {p.get('differs', 0)}")
    nc = r["newly_cleared"]
    print(f"\n  TUNED_NEWLY_CLEARED           = {nc['correct'] + nc['wrong'] + nc['ambiguous']}")
    print(f"  TUNED_NEWLY_CLEARED_CORRECT   = {nc['correct']}")
    print(f"  TUNED_NEWLY_CLEARED_WRONG     = {nc['wrong']}")
    print(f"  TUNED_NEWLY_CLEARED_AMBIGUOUS = {nc['ambiguous']}")
    for rec in r["newly"]["wrong"]:
        print(f"\n  *** WRONG: {json.dumps(rec, default=str)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
