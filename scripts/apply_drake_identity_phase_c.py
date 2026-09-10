#!/usr/bin/env python3
"""D7 Phase C — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_drake_identity_phase_c.py --manifest <frozen csv>

    # writes, and only with every key turned at once
    python scripts/apply_drake_identity_phase_c.py --manifest <frozen csv> \\
        --confirm APPLY-D7-PHASE-C-<rows> --apply

WHAT IT WRITES
--------------
For each frozen identifier: one ``drake_business_identity`` row (typed, unattributed), the deletion
of the corresponding ``drake_identity`` row, and one unattended audit entry. Nothing else. The
service refuses anything it cannot prove, and a single refusal aborts the whole batch.

WHY THE ROLLBACK MANIFEST IS BUILT BEFORE THE COMMIT
-----------------------------------------------------
This is the first batch in this codebase that DELETES a domain row, and
``drake_business_identity.id`` is generated on insert. So the information needed to reverse the
operation — which new ids to remove, and the complete prior row to restore — only exists inside the
transaction, after the writes and before the commit. It is captured there, written to disk and
hashed while the transaction can still be rolled back. A receipt written after the commit would be a
record of something already irreversible.

The rollback restores every column of the removed row, ``created_at`` and NULLs included, so a
reversal is byte-identical rather than approximate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_ROOT = REPO_ROOT / "var" / "drake_identity_phase_c"

#: Serialised against itself, so two Phase C applies cannot interleave.
ADVISORY_LOCK_KEY = 0x0D7C0001

#: Tables this batch must not change. Fingerprinted before and after, inside the transaction.
FORBIDDEN_TABLES = ("people", "person_source_links", "relationship_entities",
                    "drake_client_returns", "source_contacts", "entity_source_links",
                    "drake_identity_match_candidates", "documents")

MANIFEST_COLUMNS = ("identifier_hash", "primary_person_id", "first_year", "last_year",
                    "return_count", "taxpayer_name", "spouse_name", "confidence", "created_at",
                    "expected_psl_ids", "cohort")


class Abort(SystemExit):
    """A gate refused. Always raised before any write, or before a commit."""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def confirm_phrase(rows: int) -> str:
    return f"APPLY-D7-PHASE-C-{int(rows)}"


def load_manifest(path: Path, *, expect_sha: str | None) -> list[dict]:
    digest = sha256_of(path)
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    missing = [c for c in MANIFEST_COLUMNS if rows and c not in rows[0]]
    if missing:
        raise Abort(f"ABORT: manifest is missing columns {missing}")
    if not rows:
        raise Abort("ABORT: manifest is empty")
    hashes = [r["identifier_hash"] for r in rows]
    if len(set(hashes)) != len(hashes):
        raise Abort("ABORT: manifest contains a duplicate identifier_hash")
    return rows


def _request(row, phase_c):
    frozen = {c: (None if row[c] == "" else row[c])
              for c in phase_c.IDENTITY_COLUMNS}
    psl = tuple(int(x) for x in (row["expected_psl_ids"] or "").split("|") if x.strip())
    return phase_c.RelocationRequest(identifier_hash=row["identifier_hash"],
                                     frozen_row=frozen, expected_psl_ids=psl)


def _fingerprints(connection, text):
    out = {}
    for table in FORBIDDEN_TABLES:
        out[table] = connection.execute(text(
            f"select count(*)::text || '/' || coalesce(md5(string_agg(t::text, '~|~' "
            f"order by t::text)), '') from {table} t")).scalar()
    return out


def run(manifest_path, *, apply_changes=False, confirm=None, expect_sha=None,
        output_root=REPORT_ROOT, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.services import drake_identity_phase_c as phase_c

    manifest_path = Path(manifest_path)
    rows = load_manifest(manifest_path, expect_sha=expect_sha)
    want = confirm_phrase(len(rows))
    digest = phase_c.plan_digest([
        {"identifier_hash": r["identifier_hash"],
         "frozen_row": {c: (None if r[c] == "" else r[c]) for c in phase_c.IDENTITY_COLUMNS},
         "expected_psl_ids": [int(x) for x in (r["expected_psl_ids"] or "").split("|") if x.strip()]}
        for r in rows])

    out(f"frozen manifest: {manifest_path}")
    out(f"  sha256 : {sha256_of(manifest_path)}")
    out(f"  rows   : {len(rows)}  ({dict(_census(rows))})")
    out(f"  digest : {digest}")

    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(output_root) / f"d7-phase-c-{stamp}"
    report = {"batch": phase_c.BATCH_ID, "rows": len(rows), "manifest": str(manifest_path),
              "manifest_sha256": sha256_of(manifest_path), "plan_digest": digest,
              "relocated": 0, "committed": False, "refusals": [], "report_dir": str(out_dir),
              "rollback_manifest": None, "rollback_manifest_sha256": None}

    connection = engine.connect()
    transaction = connection.begin()
    try:
        if connection.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; this apply needs a writable session")
        connection.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        before_fp = _fingerprints(connection, text)
        before_counts = {
            "drake_identity": connection.execute(
                text("select count(*) from drake_identity")).scalar(),
            "drake_business_identity": connection.execute(
                text("select count(*) from drake_business_identity")).scalar(),
            "audit_events": connection.execute(
                text("select count(*) from audit_events")).scalar(),
        }
        out(f"  before: {before_counts}")

        if not apply_changes:
            # Prove every gate without writing: the service is the authority, so each request is
            # validated by the same code the apply would run, inside a transaction that is discarded.
            refusals = []
            for row in rows:
                savepoint = connection.begin_nested()
                try:
                    phase_c.relocate_identity(connection, _request(row, phase_c))
                except phase_c.RelocationRefused as exc:
                    refusals.append({"identifier_hash": row["identifier_hash"], "code": exc.code,
                                     "message": exc.message})
                finally:
                    savepoint.rollback()
            report["refusals"] = refusals
            if refusals:
                out(f"  DRY RUN — {len(refusals)} of {len(rows)} would be REFUSED:")
                for r in refusals[:10]:
                    out(f"    {r['identifier_hash'][:12]}..  {r['code']}: {r['message'][:90]}")
            else:
                out(f"  DRY RUN — all {len(rows)} would relocate; nothing written")
            raise Abort("DRY RUN — no writes (pass --apply with the confirmation phrase to write)")

        results = []
        for row in rows:
            try:
                results.append(phase_c.relocate_identity(connection, _request(row, phase_c)))
            except phase_c.RelocationRefused as exc:
                raise Abort(f"ABORT: {row['identifier_hash']} refused — {exc}") from exc
        report["relocated"] = len(results)
        out(f"  relocated {len(results)} identities")

        # --- pre-commit invariants
        after_counts = {
            "drake_identity": connection.execute(
                text("select count(*) from drake_identity")).scalar(),
            "drake_business_identity": connection.execute(
                text("select count(*) from drake_business_identity")).scalar(),
            "audit_events": connection.execute(
                text("select count(*) from audit_events")).scalar(),
        }
        expected = {
            "drake_identity": before_counts["drake_identity"] - len(rows),
            "drake_business_identity": before_counts["drake_business_identity"] + len(rows),
            "audit_events": before_counts["audit_events"] + len(rows),
        }
        if after_counts != expected:
            raise Abort(f"ABORT: counts {after_counts} != expected {expected}")

        after_fp = _fingerprints(connection, text)
        changed = [t for t in FORBIDDEN_TABLES if before_fp[t] != after_fp[t]]
        if changed:
            raise Abort(f"ABORT: forbidden table(s) changed: {changed}")

        gone = connection.execute(text(
            "select count(*) from drake_identity where identifier_hash = any(:h)"),
            {"h": [r["identifier_hash"] for r in rows]}).scalar()
        if gone:
            raise Abort(f"ABORT: {gone} frozen identities remain in drake_identity")
        unattributed = connection.execute(text(
            "select count(*) from drake_business_identity where id = any(:i) "
            "and (relationship_entity_id is not null or trust_level is not null "
            "or confirmation_source is not null or evidence_method is not null)"),
            {"i": [r.business_identity_id for r in results]}).scalar()
        if unattributed:
            raise Abort(f"ABORT: {unattributed} new rows are not unattributed")

        # --- rollback capture, written and hashed BEFORE the commit
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "batch": phase_c.BATCH_ID, "created_at": datetime.now(UTC).isoformat(),
            "manifest_sha256": report["manifest_sha256"], "plan_digest": digest,
            "rows": [{
                "identifier_hash": r.identifier_hash,
                "created_business_identity_id": r.business_identity_id,
                "audit_event_id": r.audit_event_id,
                "cohort": r.cohort,
                "removed_drake_identity": {
                    c: (r.removed_row[c].isoformat()
                        if hasattr(r.removed_row[c], "isoformat") else r.removed_row[c])
                    for c in phase_c.IDENTITY_COLUMNS},
            } for r in results],
        }
        blob = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
        rollback_path = out_dir / "phase_c_rollback_manifest.json"
        rollback_path.write_bytes(blob)
        report["rollback_manifest"] = str(rollback_path)
        report["rollback_manifest_sha256"] = hashlib.sha256(blob).hexdigest()
        out(f"  rollback manifest: {rollback_path}")
        out(f"  rollback sha256  : {report['rollback_manifest_sha256']}  (written BEFORE commit)")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED {len(results)} relocations")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    (out_dir / "receipt.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _census(rows):
    counts: dict = {}
    for row in rows:
        counts[row.get("cohort") or "?"] = counts.get(row.get("cohort") or "?", 0) + 1
    return sorted(counts.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="D7 Phase C guarded apply")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expect-sha")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output-root", default=str(REPORT_ROOT))
    args = parser.parse_args()
    try:
        run(args.manifest, apply_changes=args.apply, confirm=args.confirm,
            expect_sha=args.expect_sha, output_root=args.output_root)
    except Abort as exc:
        print(exc)
        return 0 if str(exc).startswith("DRY RUN") else 2
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
