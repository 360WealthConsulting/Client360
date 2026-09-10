#!/usr/bin/env python3
"""D7 Phase C — the scoped rollback. ALL-OR-NOTHING.

    python scripts/rollback_drake_identity_phase_c.py --rollback-manifest <json>
    python scripts/rollback_drake_identity_phase_c.py --rollback-manifest <json> --apply \\
        --confirm ROLLBACK-D7-PHASE-C-<rows>

WHAT IT REVERSES
----------------
Exactly what the recorded manifest says the apply did: it restores each removed ``drake_identity``
row from the full snapshot — every column, ``created_at`` and NULLs included — and deletes only the
``drake_business_identity`` ids that apply created. It never derives what to remove from the
identifier hash, because a row carrying that hash may since have been created or attributed by
somebody else; only the recorded id is ours to delete.

IT REFUSES ANYTHING BUILT ON TOP
---------------------------------
If a created identity has since been attributed to an entity, or acquired a trust level, or gained
an ``entity_source_links`` row, then reversing would destroy work done after this batch. Both abort.
The audit entries are not deleted: they are the record that the relocation happened, and a rollback
is another event rather than an erasure of the first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ADVISORY_LOCK_KEY = 0x0D7C0002


class Abort(SystemExit):
    """A gate refused. Always raised before any write, or before a commit."""


def confirm_phrase(rows: int) -> str:
    return f"ROLLBACK-D7-PHASE-C-{int(rows)}"


_RESTORE = """
    INSERT INTO drake_identity (identifier_hash, primary_person_id, first_year, last_year,
                                return_count, taxpayer_name, spouse_name, confidence, created_at)
    VALUES (:identifier_hash, :primary_person_id, :first_year, :last_year, :return_count,
            :taxpayer_name, :spouse_name, :confidence, CAST(:created_at AS timestamptz))
    RETURNING identifier_hash
"""


def run(manifest_path, *, apply_changes=False, confirm=None, expect_sha=None, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine

    path = Path(manifest_path)
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: rollback manifest SHA256 {digest} != approved {expect_sha}")
    payload = json.loads(blob.decode("utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        raise Abort("ABORT: rollback manifest records no rows")
    want = confirm_phrase(len(rows))
    out(f"rollback manifest: {path}\n  sha256: {digest}\n  rows  : {len(rows)}")
    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")

    created_ids = [int(r["created_business_identity_id"]) for r in rows]
    report = {"rows": len(rows), "restored": 0, "deleted": 0, "committed": False}

    connection = engine.connect()
    transaction = connection.begin()
    try:
        if connection.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only")
        connection.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        live = {int(r["id"]): dict(r) for r in connection.execute(text(
            "select id, identifier_hash, relationship_entity_id, trust_level, confirmation_source, "
            "evidence_method from drake_business_identity where id = any(:i) order by id for update"),
            {"i": created_ids}).mappings()}
        missing = sorted(set(created_ids) - set(live))
        if missing:
            raise Abort(f"ABORT: created identities {missing} no longer exist; refusing a partial "
                        "reversal")
        built_on = [i for i, r in live.items()
                    if r["relationship_entity_id"] is not None or r["trust_level"] is not None
                    or r["confirmation_source"] is not None or r["evidence_method"] is not None]
        if built_on:
            raise Abort(f"ABORT: identities {sorted(built_on)} have been attributed since this "
                        "batch ran; reversing would destroy later work")
        linked = connection.execute(text(
            "select count(*) from entity_source_links esl join drake_business_identity dbi "
            "on dbi.relationship_entity_id = esl.relationship_entity_id "
            "where dbi.id = any(:i)"), {"i": created_ids}).scalar()
        if linked:
            raise Abort(f"ABORT: {linked} entity_source_links depend on these identities")

        back = [r["identifier_hash"] for r in rows]
        clash = connection.execute(text(
            "select count(*) from drake_identity where identifier_hash = any(:h)"),
            {"h": back}).scalar()
        if clash:
            raise Abort(f"ABORT: {clash} identifiers are already present in drake_identity")

        if not apply_changes:
            raise Abort("DRY RUN — every gate passed; nothing written "
                        f"(pass --apply --confirm {want} to reverse)")

        for row in rows:
            snapshot = row["removed_drake_identity"]
            restored = connection.execute(text(_RESTORE), snapshot).scalar_one()
            if restored != row["identifier_hash"]:
                raise Abort("ABORT: restore wrote a different identifier than recorded")
            report["restored"] += 1
        deleted = connection.execute(text(
            "delete from drake_business_identity where id = any(:i) returning id"),
            {"i": created_ids}).fetchall()
        report["deleted"] = len(deleted)
        if report["deleted"] != len(created_ids):
            raise Abort(f"ABORT: deleted {report['deleted']} of {len(created_ids)} identities")
        if report["restored"] != len(rows):
            raise Abort("ABORT: restore count mismatch")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED — restored {report['restored']}, deleted {report['deleted']}")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="D7 Phase C scoped rollback")
    parser.add_argument("--rollback-manifest", required=True)
    parser.add_argument("--expect-sha")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    try:
        run(args.rollback_manifest, apply_changes=args.apply, confirm=args.confirm,
            expect_sha=args.expect_sha)
    except Abort as exc:
        print(exc)
        return 0 if str(exc).startswith("DRY RUN") else 2
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
