"""One-time controlled apply of the STRICT-SAFE document-owner manifest. ALL-OR-NOTHING.

WHAT THIS WRITES, AND NOTHING ELSE
    documents.person_id / household_id / organization_id   (exactly one per row, NULL -> value)
    audit_events                                           (one 'document.ownership_resolved' per row)

It writes no proposal, no contact_type, no source availability, no OCR, no file, and it moves
nothing. It is deliberately NOT a general backfill: it applies exactly the immutable manifest it is
given and refuses anything else.

WHY IT DOES NOT CALL households.resolve_document_ownership DIRECTLY
    That function is the canonical single-document write path and this tool reproduces its write
    semantics EXACTLY -- the same atomic ``WHERE all-NULL AND NOT permanent-reject`` recheck inside
    the UPDATE, and the same ``document.ownership_resolved`` audit event. It cannot be called in a
    loop here because it opens its OWN transaction per document (``engine.begin()``), which would
    commit each document independently and make a partial apply possible. The requirement is
    all-or-nothing across the whole manifest, so every statement runs inside ONE transaction owned by
    this script, and the audit writer is enlisted into it via ``write_audit_event(conn=...)``.

SAFETY
    * the manifest's SHA256 must equal the expected digest -- the manifest is immutable input;
    * row count, applied=NO, and the owner-type census must match exactly;
    * duplicate or conflicting rows are refused before any lock is taken;
    * target rows are locked FOR UPDATE, then re-verified under the lock;
    * every candidate is re-evaluated against the DEPLOYED proposal engine at apply time;
    * ANY failing row aborts the whole run -- there is no partial apply and no "skip the bad ones";
    * --dry-run always rolls back, and is the default.

USAGE
    python scripts/apply_strict_safe_owner_manifest.py --manifest <path> --dry-run
    python scripts/apply_strict_safe_owner_manifest.py --manifest <path> --apply \\
        --confirm APPLY-STRICT-SAFE-162
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_, select, text

from app.connectors.microsoft365.sharepoint_content import client_folder_hint
from app.db import documents, engine, people, relationship_entities
from app.db import households as households_t
from app.security.audit import write_audit_event
from app.services.document_high_validation import evaluate_high
from app.services.document_owner_proposal import _norm, build_match_indexes
from app.services.households import PERMANENT_REJECT_DOCUMENT_IDS

#: The one manifest this tool exists to apply. Any other content is refused.
EXPECTED_SHA256 = "3eea23501da57618d6ff2a0dc405cde02230691fe10db160298e546d6c7ccc85"
EXPECTED_ROWS = 162
EXPECTED_TYPES = {"person": 59, "household": 103, "organization": 0}
CONFIRM_PHRASE = "APPLY-STRICT-SAFE-162"
SNAPSHOT_ROOT = Path(r"D:\Client360\Backups\DR")

_OWNER_COLUMN = {"person": "person_id", "household": "household_id",
                 "organization": "organization_id"}
_SPLIT = re.compile(r"\s*(?:&|,| and )\s*", re.I)


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path, *, expect_sha=EXPECTED_SHA256):
    """Read and structurally validate the manifest. Raises on anything unexpected."""
    digest = sha256_of(path)
    if expect_sha and digest != expect_sha:
        raise SystemExit(f"ABORT: manifest SHA256 {digest} != expected {expect_sha}")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(f"ABORT: manifest has {len(rows)} rows, expected {EXPECTED_ROWS}")
    not_no = [r for r in rows if (r.get("applied") or "").strip().upper() != "NO"]
    if not_no:
        raise SystemExit(f"ABORT: {len(not_no)} rows are not applied=NO")
    census = Counter(r["owner_type"] for r in rows)
    for kind, expected in EXPECTED_TYPES.items():
        if census.get(kind, 0) != expected:
            raise SystemExit(f"ABORT: owner-type census {dict(census)} != {EXPECTED_TYPES}")
    ids = [int(r["document_id"]) for r in rows]
    dupes = [d for d, n in Counter(ids).items() if n > 1]
    if dupes:
        raise SystemExit(f"ABORT: duplicate document_id rows: {sorted(dupes)[:10]}")
    conflicting = defaultdict(set)
    for r in rows:
        conflicting[int(r["document_id"])].add((r["owner_type"], int(r["owner_id"])))
    bad = {d: v for d, v in conflicting.items() if len(v) > 1}
    if bad:
        raise SystemExit(f"ABORT: conflicting owner rows for documents {sorted(bad)[:10]}")
    return rows, digest


def _folder_entities(hint, idx):
    """Canonical entities the CLIENT folder names: (people, households, organizations, unresolved)."""
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
    """Every safety check for ONE row. Returns a list of failures; empty means safe."""
    did = int(row["document_id"])
    otype, oid = row["owner_type"], int(row["owner_id"])
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

    if otype not in _OWNER_COLUMN:
        return [*fail, f"unknown owner_type {otype!r}"]
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
        fail.append(f"engine not HIGH-eligible (status={ev['status']}, "
                    f"confidence={prop.get('confidence')})")
    if (prop.get("proposed_entity_type"), prop.get("proposed_entity_id")) != (otype, oid):
        fail.append(f"engine proposes {prop.get('proposed_entity_type')} "
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
        members = idx["members"]
        owner_in = ((otype == "person" and (oid in f_ppl
                                            or any(oid in members.get(h, set()) for h in f_hh)))
                    or (otype == "household" and (oid in f_hh
                                                  or any(p in members.get(oid, set())
                                                         for p in f_ppl)))
                    or (otype == "organization" and oid in f_org))
        if not owner_in:
            fail.append(f"client folder names a different client ({hint})")
    return fail


def _load_documents(conn, ids):
    return {r["id"]: dict(r) for r in conn.execute(text("""
        select d.id, d.status, d.deleted_at, d.person_id, d.household_id, d.organization_id,
               d.original_name,
               (select min(s.source_path) from document_sources s
                 where s.document_id = d.id and s.source_system = 'SharePoint') sp,
               (select bool_or(s.available) from document_sources s
                 where s.document_id = d.id) avail,
               (select count(*) from document_sources s where s.document_id = d.id) nsrc
          from documents d
         where d.id = any(:ids)
         order by d.id
           for update of d"""), {"ids": ids}).mappings()}


def write_snapshot(rows, docs, digest, root=SNAPSHOT_ROOT):
    """Rollback snapshot: every ownership field that would change, as it stands NOW."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(root) / f"strict-safe-owner-apply-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rollback_snapshot_owner_assignments.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["document_id", "owner_type", "owner_id", "owner_name",
                    "prev_person_id", "prev_household_id", "prev_organization_id",
                    "prev_status", "prev_deleted_at"])
        for r in sorted(rows, key=lambda x: int(x["document_id"])):
            d = docs[int(r["document_id"])]
            w.writerow([r["document_id"], r["owner_type"], r["owner_id"], r["owner_name"],
                        d["person_id"] if d["person_id"] is not None else "",
                        d["household_id"] if d["household_id"] is not None else "",
                        d["organization_id"] if d["organization_id"] is not None else "",
                        d["status"], d["deleted_at"] or ""])
    meta = {"created_at": datetime.now(UTC).isoformat(), "manifest_sha256": digest,
            "rows": len(rows), "snapshot_sha256": sha256_of(path),
            "confirm_phrase": CONFIRM_PHRASE}
    (out / "manifest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return path, meta["snapshot_sha256"]


def run(manifest_path, *, apply_changes, confirm=None, expect_sha=EXPECTED_SHA256,
        snapshot_root=SNAPSHOT_ROOT, out=print):
    rows, digest = load_manifest(manifest_path, expect_sha=expect_sha)
    if apply_changes and confirm != CONFIRM_PHRASE:
        raise SystemExit(f"ABORT: --apply requires --confirm {CONFIRM_PHRASE}")
    out(f"manifest: {manifest_path}")
    out(f"  sha256={digest}  rows={len(rows)}  "
        f"types={dict(Counter(r['owner_type'] for r in rows))}")

    ids = sorted(int(r["document_id"]) for r in rows)
    report = {"valid": 0, "invalid": 0, "failures": [], "applied": 0, "audit_rows": 0,
              "snapshot": None, "snapshot_sha256": None, "committed": False}

    conn = engine.connect()
    trans = conn.begin()
    try:
        idx = build_match_indexes(conn)
        docs = _load_documents(conn, ids)          # FOR UPDATE: rows locked for the whole run
        out(f"  locked {len(docs)} document rows; "
            f"owner_eligible={len(idx['owner_eligible'])} staff={sorted(idx['staff'])}")

        for r in rows:
            fails = verify_row(conn, r, idx, docs.get(int(r["document_id"])))
            if fails:
                report["invalid"] += 1
                report["failures"].append({"document_id": int(r["document_id"]),
                                           "owner": f"{r['owner_type']} #{r['owner_id']}",
                                           "reasons": fails})
            else:
                report["valid"] += 1
        out(f"  verification: valid={report['valid']} invalid={report['invalid']}")
        for f in report["failures"][:20]:
            out(f"    INVALID #{f['document_id']} {f['owner']}: {'; '.join(f['reasons'])}")

        if report["invalid"]:
            raise RuntimeError(f"{report['invalid']} row(s) failed verification — "
                               "all-or-nothing: nothing will be applied")

        snap, snap_sha = write_snapshot(rows, docs, digest, root=snapshot_root)
        report["snapshot"], report["snapshot_sha256"] = str(snap), snap_sha
        out(f"  rollback snapshot: {snap}")
        out(f"  snapshot sha256:   {snap_sha}")

        for r in rows:
            did, otype, oid = int(r["document_id"]), r["owner_type"], int(r["owner_id"])
            col = _OWNER_COLUMN[otype]
            updated = conn.execute(documents.update().where(and_(
                documents.c.id == did,
                documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                documents.c.organization_id.is_(None),
                documents.c.id.notin_(tuple(sorted(PERMANENT_REJECT_DOCUMENT_IDS))),
            )).values(**{col: oid})).rowcount
            if updated != 1:
                raise RuntimeError(f"document {did}: atomic guard matched {updated} rows")
            report["applied"] += updated
            write_audit_event(
                action="document.ownership_resolved", entity_type="document", entity_id=did,
                actor_user_id=None, request_id=f"strict-safe-manifest:{digest[:12]}",
                metadata={"document_id": did, "destination": r["owner_name"],
                          "person_id": oid if otype == "person" else None,
                          "household_id": oid if otype == "household" else None,
                          "organization_id": oid if otype == "organization" else None,
                          "scope": "strict_safe_manifest",
                          "manifest_sha256": digest,
                          "previous_ownership_state": "all NULL (manifest apply)"},
                conn=conn)
            report["audit_rows"] += 1

        if report["applied"] != EXPECTED_ROWS:
            raise RuntimeError(f"applied {report['applied']} != {EXPECTED_ROWS}")
        still_null = conn.execute(text(
            "select count(*) from documents where id = any(:i) and person_id is null "
            "and household_id is null and organization_id is null"), {"i": ids}).scalar()
        if still_null:
            raise RuntimeError(f"{still_null} documents still unowned after apply")

        if apply_changes:
            trans.commit()
            report["committed"] = True
            out(f"  COMMITTED {report['applied']} assignments, "
                f"{report['audit_rows']} audit rows")
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
            "committed_at": datetime.now(UTC).isoformat(), "manifest_sha256": digest,
            "manifest_path": str(manifest_path), "rows_applied": report["applied"],
            "audit_rows": report["audit_rows"], "snapshot": report["snapshot"],
            "snapshot_sha256": report["snapshot_sha256"]}, indent=1), encoding="utf-8")
        out(f"  receipt: {receipt}")
        report["receipt"] = str(receipt)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--expect-sha", default=EXPECTED_SHA256)
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("ABORT: choose --dry-run or --apply, not both")
    if not args.apply:
        args.dry_run = True
    report = run(args.manifest, apply_changes=args.apply, confirm=args.confirm,
                 expect_sha=args.expect_sha)
    return 0 if (report["committed"] or not args.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())
