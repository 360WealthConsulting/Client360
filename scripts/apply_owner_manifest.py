"""Generic, fail-closed apply for an APPROVED document-ownership manifest. ALL-OR-NOTHING.

WHAT THIS WRITES, AND NOTHING ELSE
    documents.person_id / household_id / organization_id  (exactly one per row, NULL -> value)
    audit_events                                          (one 'document.ownership_resolved' per row)

No proposal, classification, naming, tax year, category, OCR, source or contact_type is touched, and
no file is moved.

WHY THIS EXISTS SEPARATELY FROM apply_strict_safe_owner_manifest.py
    That script is the historical record of a completed production write: its constants ARE the
    approval for that batch, and its rollback path still has to load the DR snapshot it produced.
    Editing it would invalidate an audit trail. This is a new tool, parameterised, and the old one
    is left untouched.

WHY THE EXPECTATIONS ARE REQUIRED ARGUMENTS RATHER THAN DERIVED
    Deriving the row count and census from the file and then checking the file against them proves
    nothing -- it always agrees. The digest, the row count and the per-type census are what a human
    APPROVED, so they must be supplied from outside the file and the file must match them. There are
    no defaults; omitting any of them refuses the run.

WHY households.resolve_document_ownership IS NOT CALLED IN A LOOP
    It is the canonical single-document write path and this reproduces its write semantics exactly:
    the same atomic ``WHERE all-NULL AND NOT permanent-reject`` recheck inside the UPDATE and the
    same audit action. It opens its OWN transaction per document, so looping it would commit each
    row independently and make a partial apply possible. Everything here runs in ONE transaction and
    the audit writer is enlisted into it via ``write_audit_event(conn=...)``.

USAGE
    python scripts/apply_owner_manifest.py --manifest <path> --expect-sha256 <hex> \\
        --expect-rows <n> --expect-person <n> --expect-household <n> --expect-organization <n> \\
        --batch-id <slug> --dry-run

    python scripts/apply_owner_manifest.py ... --batch-id <slug> --actor-user-id <id> \\
        --apply --confirm APPLY-<BATCH-ID>-<ROWS>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, NOT the repository root, so `app` is not
# importable and the documented command dies at import time. Same bootstrap scripts/demo.py uses.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import and_, select, text  # noqa: E402

from app.connectors.microsoft365.sharepoint_content import client_folder_hint  # noqa: E402
from app.db import documents, engine, people, relationship_entities  # noqa: E402
from app.db import households as households_t  # noqa: E402
from app.security.audit import write_audit_event  # noqa: E402
from app.services.document_high_validation import evaluate_high  # noqa: E402
from app.services.document_owner_proposal import _norm, build_match_indexes  # noqa: E402
from app.services.households import PERMANENT_REJECT_DOCUMENT_IDS  # noqa: E402

SNAPSHOT_ROOT = Path(r"D:\Client360\Backups\DR")

_OWNER_COLUMN = {"person": "person_id", "household": "household_id",
                 "organization": "organization_id"}
#: Manifests have been produced with two column vocabularies. Both are accepted; nothing is guessed
#: beyond these exact names, and a manifest carrying neither is refused.
_TYPE_COLS = ("owner_type", "proposed_entity_type", "proposed_owner_type")
_ID_COLS = ("owner_id", "proposed_entity_id", "proposed_owner_id")
_NAME_COLS = ("owner_name", "proposed_entity_name", "proposed_owner_name")
_SPLIT = re.compile(r"\s*(?:&|,| and )\s*", re.I)


def confirm_phrase(batch_id: str, rows: int) -> str:
    """Unique per batch, so a phrase cannot be reused from a previous run."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(batch_id)).strip("-").upper()
    return f"APPLY-{slug}-{rows}"


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _col(row, names, what):
    for n in names:
        if n in row and str(row[n]).strip() != "":
            return row[n]
    raise SystemExit(f"ABORT: manifest row has no {what} column (looked for {list(names)})")


def load_manifest(path, *, expect_sha, expect_rows, expect_census):
    """Read and structurally validate. Every expectation is supplied by the caller, never derived."""
    if not expect_sha:
        raise SystemExit("ABORT: --expect-sha256 is required")
    if expect_rows is None:
        raise SystemExit("ABORT: --expect-rows is required")
    for k, v in expect_census.items():
        if v is None:
            raise SystemExit(f"ABORT: --expect-{k} is required")
    if sum(expect_census.values()) != expect_rows:
        raise SystemExit(f"ABORT: census {expect_census} sums to {sum(expect_census.values())}, "
                         f"not --expect-rows {expect_rows}")

    digest = sha256_of(path)
    if digest != expect_sha:
        raise SystemExit(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")
    with open(path, newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    if len(raw) != expect_rows:
        raise SystemExit(f"ABORT: manifest has {len(raw)} rows, approved {expect_rows}")

    rows = []
    for r in raw:
        if (r.get("applied") or "").strip().upper() != "NO":
            raise SystemExit("ABORT: a manifest row is not applied=NO")
        rows.append({
            "document_id": int(_col(r, ("document_id",), "document_id")),
            "owner_type": str(_col(r, _TYPE_COLS, "owner type")).strip(),
            "owner_id": int(_col(r, _ID_COLS, "owner id")),
            "owner_name": str(_col(r, _NAME_COLS, "owner name")).strip(),
        })
    census = Counter(r["owner_type"] for r in rows)
    if {k: census.get(k, 0) for k in expect_census} != expect_census:
        raise SystemExit(f"ABORT: census {dict(census)} != approved {expect_census}")
    bad_type = [r for r in rows if r["owner_type"] not in _OWNER_COLUMN]
    if bad_type:
        raise SystemExit(f"ABORT: unknown owner_type on {len(bad_type)} row(s)")

    ids = [r["document_id"] for r in rows]
    dupes = [d for d, n in Counter(ids).items() if n > 1]
    if dupes:
        raise SystemExit(f"ABORT: duplicate document_id rows: {sorted(dupes)[:10]}")
    conflict = defaultdict(set)
    for r in rows:
        conflict[r["document_id"]].add((r["owner_type"], r["owner_id"]))
    bad = {d: v for d, v in conflict.items() if len(v) > 1}
    if bad:
        raise SystemExit(f"ABORT: conflicting owner rows for documents {sorted(bad)[:10]}")
    return rows, digest


def _folder_entities(hint, idx):
    if not hint:
        return set(), set(), set(), True
    n = _norm(hint)
    ppl, hhs, orgs = set(), set(), set()
    biz = {k: v[0] for k, v in idx["biz"].items()}
    if n in biz:
        orgs.add(biz[n])
    for hid, nm in idx["hh_name"].items():
        hn = _norm(nm)
        if hn and (hn == n or n.startswith(hn) or hn.startswith(n)):
            hhs.add(hid)
    parts = [p for p in _SPLIT.split(hint) if p.strip()]
    if parts:
        surname = _norm(parts[0]).split()[-1] if _norm(parts[0]) else ""
        givens = [_norm(p) for p in parts[1:]]
        for pid, info in idx["pid"].items():
            toks = _norm(info.get("name")).split()
            if not toks:
                continue
            if surname and toks[-1] == surname and (not givens or toks[0] in givens):
                ppl.add(pid)
            elif " ".join(toks) == n:
                ppl.add(pid)
    return ppl, hhs, orgs, not (ppl or hhs or orgs)


def verify_row(conn, row, idx, doc):
    """Every safety check for ONE row, run INSIDE the transaction under the row lock.

    Returns a list of failures. A non-empty list aborts the entire batch -- it is never demoted to
    a skip, because "apply the good ones" is exactly how a manifest stops meaning what was approved.
    """
    did, otype, oid = row["document_id"], row["owner_type"], row["owner_id"]
    fail = []
    if doc is None:
        return ["document not found"]
    if doc["status"] == "deleted" or doc["deleted_at"] is not None:
        fail.append("document is not active")
    if any(doc[c] is not None for c in ("person_id", "household_id", "organization_id")):
        fail.append("document already has an owner")
    if did in PERMANENT_REJECT_DOCUMENT_IDS:
        fail.append("permanent reject document")
    if doc["nsrc"] and not doc["avail"]:
        fail.append("no available source")

    table = {"person": people, "household": households_t,
             "organization": relationship_entities}[otype]
    if conn.execute(select(table.c.id).where(table.c.id == oid)).scalar() is None:
        fail.append(f"{otype} #{oid} no longer exists")
    if otype == "person":
        if oid not in idx["owner_eligible"]:
            fail.append("person is not owner-eligible")
        if oid in idx["staff"]:
            fail.append("person is staff")
    if otype == "organization":
        if oid not in idx["org_eligible"]:
            fail.append("organization is not owner-eligible")
        if oid in idx["firm_entities"]:
            fail.append("organization is a firm/self entity")

    ev = evaluate_high(conn, did, idx, ocr=False)
    prop = ev.get("proposal") or {}
    if ev["status"] != "eligible":
        fail.append(f"engine not HIGH-eligible ({ev['status']}/{prop.get('confidence')})")
    if (prop.get("proposed_entity_type"), prop.get("proposed_entity_id")) != (otype, oid):
        fail.append(f"engine proposes {prop.get('proposed_entity_type')}"
                    f"#{prop.get('proposed_entity_id')}")
    if ev.get("contradictions"):
        fail.append("contradiction:" + ",".join(ev["contradictions"]))
    evidence = [e.lower() for e in (prop.get("evidence") or [])]
    if any("shared across businesses" in e for e in evidence):
        fail.append("shared-identifier corroboration")
    if otype == "organization" and not any(
            ("own phone" in e or "own email" in e or "client folder anchors" in e)
            for e in evidence):
        fail.append("organization lacks its own corroboration")

    hint = client_folder_hint(doc["sp"] or "")
    f_ppl, f_hh, f_org, unresolved = _folder_entities(hint, idx)
    if hint and not unresolved:
        m = idx["members"]
        owner_in = ((otype == "person" and (oid in f_ppl
                                            or any(oid in m.get(h, set()) for h in f_hh)))
                    or (otype == "household" and (oid in f_hh
                                                  or any(p in m.get(oid, set()) for p in f_ppl)))
                    or (otype == "organization" and oid in f_org))
        if not owner_in:
            fail.append(f"client folder names a different client ({hint})")
    return fail


def _lock_documents(conn, ids):
    return {r["id"]: dict(r) for r in conn.execute(text("""
        select d.id, d.status, d.deleted_at, d.person_id, d.household_id, d.organization_id,
               d.original_name,
               (select min(s.source_path) from document_sources s
                 where s.document_id = d.id and s.source_system = 'SharePoint') sp,
               (select bool_or(s.available) from document_sources s
                 where s.document_id = d.id) avail,
               (select count(*) from document_sources s where s.document_id = d.id) nsrc
          from documents d where d.id = any(:ids) order by d.id for update of d"""),
        {"ids": ids}).mappings()}


def _non_target_fingerprint(conn, ids):
    return conn.execute(text("""
        select md5(string_agg(id::text||'|'||coalesce(person_id::text,'')||'|'||
                              coalesce(household_id::text,'')||'|'||
                              coalesce(organization_id::text,''), E'\n' order by id))
          from documents where id <> all(:i)"""), {"i": ids}).scalar()


def write_snapshot(rows, docs, digest, batch_id, root):
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(batch_id)).strip("-").lower()
    out = Path(root) / f"owner-apply-{slug}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rollback_snapshot_owner_assignments.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["document_id", "owner_type", "owner_id", "owner_name",
                    "prev_person_id", "prev_household_id", "prev_organization_id",
                    "prev_status", "prev_deleted_at"])
        for r in sorted(rows, key=lambda x: x["document_id"]):
            d = docs[r["document_id"]]
            w.writerow([r["document_id"], r["owner_type"], r["owner_id"], r["owner_name"],
                        d["person_id"] if d["person_id"] is not None else "",
                        d["household_id"] if d["household_id"] is not None else "",
                        d["organization_id"] if d["organization_id"] is not None else "",
                        d["status"], d["deleted_at"] or ""])
    meta = {"created_at": datetime.now(UTC).isoformat(), "batch_id": batch_id,
            "manifest_sha256": digest, "rows": len(rows),
            "snapshot_sha256": sha256_of(path)}
    (out / "manifest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return path, meta["snapshot_sha256"]


def run(manifest_path, *, batch_id, expect_sha, expect_rows, expect_census,
        apply_changes=False, confirm=None, actor_user_id=None,
        snapshot_root=SNAPSHOT_ROOT, out=print):
    rows, digest = load_manifest(manifest_path, expect_sha=expect_sha, expect_rows=expect_rows,
                                 expect_census=expect_census)
    want = confirm_phrase(batch_id, len(rows))
    if apply_changes:
        if confirm != want:
            raise SystemExit(f"ABORT: --apply requires --confirm {want}")
        if actor_user_id is None:
            raise SystemExit("ABORT: --apply requires --actor-user-id; ownership resolution "
                             "records a human decision and must name the operator")

    out(f"manifest: {manifest_path}")
    out(f"  batch={batch_id}  sha256={digest}  rows={len(rows)}  census={dict(expect_census)}")
    ids = sorted(r["document_id"] for r in rows)
    report = {"valid": 0, "invalid": 0, "failures": [], "applied": 0, "audit_rows": 0,
              "snapshot": None, "snapshot_sha256": None, "committed": False,
              "confirm_phrase": want, "batch_id": batch_id, "manifest_sha256": digest}

    conn = engine.connect()
    trans = conn.begin()
    try:
        idx = build_match_indexes(conn)
        docs = _lock_documents(conn, ids)                       # guard 8: lock before checking
        fp_before = _non_target_fingerprint(conn, ids)
        out(f"  locked {len(docs)} rows; owner_eligible={len(idx['owner_eligible'])}")

        for r in rows:
            fails = verify_row(conn, r, idx, docs.get(r["document_id"]))
            if fails:
                report["invalid"] += 1
                report["failures"].append({"document_id": r["document_id"], "reasons": fails})
            else:
                report["valid"] += 1
        out(f"  verification: valid={report['valid']} invalid={report['invalid']}")
        for f in report["failures"][:20]:
            out(f"    INVALID #{f['document_id']}: {'; '.join(f['reasons'])}")
        if report["invalid"]:
            raise RuntimeError(f"{report['invalid']} row(s) failed verification — all-or-nothing, "
                               "nothing is applied and no row is skipped")

        snap, snap_sha = write_snapshot(rows, docs, digest, batch_id, snapshot_root)
        report["snapshot"], report["snapshot_sha256"] = str(snap), snap_sha
        out(f"  rollback snapshot: {snap}")
        out(f"  snapshot sha256:   {snap_sha}")

        written = set()
        for r in rows:
            did, otype, oid = r["document_id"], r["owner_type"], r["owner_id"]
            col = _OWNER_COLUMN[otype]
            got = conn.execute(documents.update().where(and_(
                documents.c.id == did,
                documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                documents.c.organization_id.is_(None),
                documents.c.id.notin_(tuple(sorted(PERMANENT_REJECT_DOCUMENT_IDS))),
            )).values(**{col: oid}).returning(documents.c.id)).scalars().all()
            if len(got) != 1:
                raise RuntimeError(f"document {did}: atomic guard matched {len(got)} rows")
            written.update(got)
            report["applied"] += 1
            write_audit_event(
                action="document.ownership_resolved", entity_type="document", entity_id=did,
                actor_user_id=actor_user_id,
                request_id=f"owner-manifest:{batch_id}:{digest[:12]}",
                metadata={"document_id": did,
                          "destination": {"entity_type": otype, "entity_id": oid,
                                          "entity_name": r["owner_name"]},
                          "owner_type": otype,
                          "person_id": oid if otype == "person" else None,
                          "household_id": oid if otype == "household" else None,
                          "organization_id": oid if otype == "organization" else None,
                          "scope": "owner_manifest", "batch_id": batch_id,
                          "manifest_sha256": digest,
                          "previous_ownership_state": "all NULL (manifest apply)"},
                conn=conn)
            report["audit_rows"] += 1

        if written != set(ids):                                  # guard 13: exact set equality
            raise RuntimeError(f"written set != manifest set "
                               f"(missing {len(set(ids) - written)}, extra {len(written - set(ids))})")
        still = conn.execute(text(
            "select count(*) from documents where id = any(:i) and person_id is null "
            "and household_id is null and organization_id is null"), {"i": ids}).scalar()
        if still:
            raise RuntimeError(f"{still} targets still unowned after apply")
        for r in rows:                                           # guard 14: exact owner equality
            col = _OWNER_COLUMN[r["owner_type"]]
            got = conn.execute(text(
                "select person_id, household_id, organization_id from documents where id=:i"),
                {"i": r["document_id"]}).mappings().one()
            others = [c for c in ("person_id", "household_id", "organization_id") if c != col]
            if got[col] != r["owner_id"] or any(got[c] is not None for c in others):
                raise RuntimeError(f"document {r['document_id']}: post-write owner mismatch")
        if _non_target_fingerprint(conn, ids) != fp_before:       # guard 15
            raise RuntimeError("non-target ownership changed")
        out("  post-write checks: set equality, owner equality, non-target fingerprint all OK")

        if apply_changes:
            trans.commit()
            report["committed"] = True
            out(f"  COMMITTED {report['applied']} assignments, {report['audit_rows']} audit rows")
        else:
            trans.rollback()
            out(f"  DRY RUN: rolled back. Would assign {report['applied']} documents "
                f"and write {report['audit_rows']} audit rows.")
    except Exception:
        if not report["committed"]:
            trans.rollback()
            out("  ROLLED BACK — no change persisted")
        raise
    finally:
        conn.close()

    if report["committed"]:
        receipt = Path(report["snapshot"]).parent / "apply_receipt.json"
        receipt.write_text(json.dumps({
            "committed_at": datetime.now(UTC).isoformat(), "batch_id": batch_id,
            "manifest_path": str(manifest_path), "manifest_sha256": digest,
            "rows_applied": report["applied"], "audit_rows": report["audit_rows"],
            "actor_user_id": actor_user_id, "snapshot": report["snapshot"],
            "snapshot_sha256": report["snapshot_sha256"]}, indent=1), encoding="utf-8")
        out(f"  receipt: {receipt}")
        report["receipt"] = str(receipt)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--expect-sha256", required=True)
    ap.add_argument("--expect-rows", required=True, type=int)
    ap.add_argument("--expect-person", required=True, type=int)
    ap.add_argument("--expect-household", required=True, type=int)
    ap.add_argument("--expect-organization", required=True, type=int)
    ap.add_argument("--actor-user-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    report = run(args.manifest, batch_id=args.batch_id, expect_sha=args.expect_sha256,
                 expect_rows=args.expect_rows,
                 expect_census={"person": args.expect_person,
                                "household": args.expect_household,
                                "organization": args.expect_organization},
                 apply_changes=args.apply, confirm=args.confirm,
                 actor_user_id=args.actor_user_id)
    return 0 if (report["committed"] or not args.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())
