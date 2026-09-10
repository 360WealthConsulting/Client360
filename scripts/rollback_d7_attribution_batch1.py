#!/usr/bin/env python3
"""D7 machine attribution Batch 1 — the guarded rollback. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/rollback_d7_attribution_batch1.py --rollback-manifest <json>

    # writes, and only with every key turned at once
    python scripts/rollback_d7_attribution_batch1.py --rollback-manifest <json> \\
        --confirm ROLLBACK-D7-ATTRIB-BATCH1-<rows> --apply

WHAT IT REVERSES
----------------
For each recorded row: the ``drake_business_identity`` row returns to its exact unattributed
pre-image, and the ``entity_source_links`` rows this batch created are deleted **by the ids the
apply recorded** — never by re-deriving them from the identifier, which could match a link somebody
else made afterwards. Nothing cascades.

WHAT IT DOES NOT DO
-------------------
It does not delete the attribution audit entries. An audit trail records what happened, and this
reversal is another thing that happened, not an erasure of the first: one compensating event is
written per reversed row. That is the established contract here — the Phase C rollback keeps its
audit entries for the same reason.

WHEN IT REFUSES
---------------
If a row was attributed to a different entity since the apply, if its trust fields were changed by a
human approval, or if a recorded link no longer looks like the row the apply created, the whole
batch aborts. A rollback that would discard someone else's later decision is worse than no rollback.
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

BATCH_ID = "d7_machine_attribution_batch1"

#: Distinct from the apply's key, so a rollback cannot be mistaken for an apply by the lock alone.
ADVISORY_LOCK_KEY = 0x0D7A0002

ROLLBACK_AUDIT_ACTION = "drake.entity_attribution_machine_rolled_back"

#: Tables a rollback must not change. entity_source_links and drake_business_identity are the two it
#: may touch; everything else is fingerprinted before and after inside the transaction.
FORBIDDEN_TABLES = ("drake_identity", "person_source_links", "people", "relationship_entities",
                    "source_contacts", "drake_client_returns", "documents",
                    "drake_identity_match_candidates")

PRE_IMAGE_COLUMNS = ("relationship_entity_id", "trust_level", "confirmation_source",
                     "evidence_method")


class Abort(SystemExit):
    """A gate refused. Always raised before any write, or before a commit."""


def confirm_phrase(rows: int) -> str:
    return f"ROLLBACK-D7-ATTRIB-BATCH1-{int(rows)}"


def load_rollback_manifest(path: Path, *, expect_sha: str | None = None) -> dict:
    path = Path(path)
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: rollback manifest SHA256 {digest} != approved {expect_sha}")
    payload = json.loads(blob.decode("utf-8"))
    if payload.get("batch") != BATCH_ID:
        raise Abort(f"ABORT: rollback manifest is for {payload.get('batch')!r}, not {BATCH_ID!r}")
    rows = payload.get("rows") or []
    if not rows:
        raise Abort("ABORT: rollback manifest records no rows")
    ids = [int(r["dbi_id"]) for r in rows]
    if len(set(ids)) != len(ids):
        raise Abort("ABORT: rollback manifest contains a duplicate dbi_id")
    links = [i for r in rows for i in r["created_entity_source_link_ids"]]
    if len(set(links)) != len(links):
        raise Abort("ABORT: rollback manifest contains a duplicate link id")
    for row in rows:
        missing = [c for c in PRE_IMAGE_COLUMNS if c not in (row.get("dbi_pre_image") or {})]
        if missing:
            raise Abort(f"ABORT: dbi {row['dbi_id']} pre-image is missing {missing}")
    return payload


def _fingerprints(connection, text):
    out = {}
    for table in FORBIDDEN_TABLES:
        out[table] = connection.execute(text(
            f"select count(*)::text || '/' || coalesce(md5(string_agg(t::text, '~|~' "
            f"order by t::text)), '') from {table} t")).scalar()
    return out


def _revalidate(connection, text, row) -> None:
    """Refuse to undo anything that is no longer what this batch created."""
    dbi_id = int(row["dbi_id"])
    entity_id = int(row["target_entity_id"])
    post = row.get("dbi_post_image") or {}

    current = connection.execute(text(
        "select id, identifier_hash, relationship_entity_id, trust_level, confirmation_source, "
        "evidence_method, confirmed_by_user_id from drake_business_identity where id = :i "
        "for update"), {"i": dbi_id}).mappings().one_or_none()
    if current is None:
        raise Abort(f"ABORT: dbi {dbi_id} no longer exists")
    if current["identifier_hash"] != row["identifier_hash"]:
        raise Abort(f"ABORT: dbi {dbi_id} now carries a different identifier")
    if current["relationship_entity_id"] is None:
        raise Abort(f"ABORT: dbi {dbi_id} is already unattributed; nothing to reverse")
    if current["relationship_entity_id"] != entity_id:
        raise Abort(f"ABORT: dbi {dbi_id} is attributed to entity "
                    f"{current['relationship_entity_id']}, not {entity_id}; a later decision "
                    "would be discarded")
    for column in PRE_IMAGE_COLUMNS[1:]:
        if post.get(column) is not None and current[column] != post.get(column):
            raise Abort(f"ABORT: dbi {dbi_id} {column} is {current[column]!r}, not the "
                        f"{post.get(column)!r} this batch wrote; it changed since the apply")
    if current["confirmed_by_user_id"] is not None:
        raise Abort(f"ABORT: dbi {dbi_id} has since been confirmed by a user; "
                    "a human decision is not reversed by this tool")

    for link_id in row["created_entity_source_link_ids"]:
        link = connection.execute(text(
            "select id, relationship_entity_id, source_contact_id, confirmed_by_user_id "
            "from entity_source_links where id = :i for update"),
            {"i": int(link_id)}).mappings().one_or_none()
        if link is None:
            raise Abort(f"ABORT: link {link_id} recorded for dbi {dbi_id} no longer exists")
        if link["relationship_entity_id"] != entity_id:
            raise Abort(f"ABORT: link {link_id} now points at entity "
                        f"{link['relationship_entity_id']}, not {entity_id}")
        if int(link["source_contact_id"]) not in set(row["source_contact_ids"]):
            raise Abort(f"ABORT: link {link_id} is on a contact this batch did not name")
        if link["confirmed_by_user_id"] is not None:
            raise Abort(f"ABORT: link {link_id} has since been confirmed by a user")


def run(rollback_manifest, *, apply_changes=False, confirm=None, expect_sha=None, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.drake_machine_attribution import AUDIT_ACTION

    payload = load_rollback_manifest(Path(rollback_manifest), expect_sha=expect_sha)
    rows = payload["rows"]
    want = confirm_phrase(len(rows))
    expected_links = sum(len(r["created_entity_source_link_ids"]) for r in rows)

    out(f"rollback manifest: {rollback_manifest}")
    out(f"  batch  : {payload['batch']}")
    out(f"  rows   : {len(rows)}  links to delete: {expected_links}")
    out(f"  plan   : {payload.get('plan_digest')}")

    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")

    report = {"batch": BATCH_ID, "rows": len(rows), "reversed": 0, "links_deleted": 0,
              "audits_written": 0, "committed": False}

    connection = engine.connect()
    transaction = connection.begin()
    try:
        if connection.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; this rollback needs a writable session")
        connection.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        before_fp = _fingerprints(connection, text)
        before_links = connection.execute(
            text("select count(*) from entity_source_links")).scalar()
        before_attributed = connection.execute(text(
            "select count(*) from drake_business_identity "
            "where relationship_entity_id is not null")).scalar()
        before_dbi = connection.execute(
            text("select count(*) from drake_business_identity")).scalar()
        before_attrib_audits = connection.execute(text(
            "select count(*) from audit_events where action = :a"), {"a": AUDIT_ACTION}).scalar()

        for row in rows:
            _revalidate(connection, text, row)
        out(f"  revalidated {len(rows)}/{len(rows)} rows")

        if not apply_changes:
            raise Abort("DRY RUN — no writes (pass --apply with the confirmation phrase to write)")

        deleted = audits = 0
        for row in rows:
            dbi_id = int(row["dbi_id"])
            pre = row["dbi_pre_image"]
            connection.execute(text(
                "update drake_business_identity set relationship_entity_id = :e, "
                "trust_level = :t, confirmation_source = :s, evidence_method = :m, "
                "updated_at = now() where id = :i"),
                {"i": dbi_id, "e": pre["relationship_entity_id"], "t": pre["trust_level"],
                 "s": pre["confirmation_source"], "m": pre["evidence_method"]})
            for link_id in row["created_entity_source_link_ids"]:
                deleted += connection.execute(text(
                    "delete from entity_source_links where id = :i returning id"),
                    {"i": int(link_id)}).rowcount
            write_audit_event(
                action=ROLLBACK_AUDIT_ACTION,
                entity_type="drake_business_identity",
                entity_id=dbi_id,
                actor_user_id=None,               # unattended: the established NULL-actor contract
                request_id=f"rollback-d7-attrib-batch1-{dbi_id}",
                conn=connection,
                metadata={"batch": BATCH_ID, "identifier_hash": row["identifier_hash"],
                          "reversed_entity_id": int(row["target_entity_id"]),
                          "deleted_entity_source_link_ids":
                              row["created_entity_source_link_ids"],
                          "original_audit_event_id": row.get("audit_event_id"),
                          "unattended": True})
            audits += 1

        report["reversed"], report["links_deleted"], report["audits_written"] = \
            len(rows), deleted, audits

        # --- pre-commit invariants
        if deleted != expected_links:
            raise Abort(f"ABORT: deleted {deleted} links, expected {expected_links}")
        after_links = connection.execute(
            text("select count(*) from entity_source_links")).scalar()
        if after_links != before_links - expected_links:
            raise Abort(f"ABORT: entity_source_links is {after_links}, "
                        f"expected {before_links - expected_links}")
        after_dbi = connection.execute(
            text("select count(*) from drake_business_identity")).scalar()
        if after_dbi != before_dbi:
            raise Abort(f"ABORT: drake_business_identity count moved {before_dbi} -> {after_dbi}")
        after_attributed = connection.execute(text(
            "select count(*) from drake_business_identity "
            "where relationship_entity_id is not null")).scalar()
        if after_attributed != before_attributed - len(rows):
            raise Abort(f"ABORT: attributed is {after_attributed}, "
                        f"expected {before_attributed - len(rows)}")
        still = connection.execute(text(
            "select count(*) from drake_business_identity where id = any(:i) and ("
            "relationship_entity_id is not null or trust_level is not null "
            "or confirmation_source is not null or evidence_method is not null)"),
            {"i": [int(r["dbi_id"]) for r in rows]}).scalar()
        if still:
            raise Abort(f"ABORT: {still} rows did not return to the unattributed pre-image")
        after_attrib_audits = connection.execute(text(
            "select count(*) from audit_events where action = :a"), {"a": AUDIT_ACTION}).scalar()
        if after_attrib_audits != before_attrib_audits:
            raise Abort("ABORT: the original attribution audit entries must be retained")
        _check_forbidden(connection, text, before_fp)

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED — reversed {len(rows)}, deleted {deleted} links, "
            f"wrote {audits} compensating audit entries")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()
    return report


def _check_forbidden(connection, text, before_fp) -> None:
    after_fp = _fingerprints(connection, text)
    changed = [t for t in FORBIDDEN_TABLES if before_fp[t] != after_fp[t]]
    if changed:
        raise Abort(f"ABORT: forbidden table(s) changed: {changed}")


def main() -> int:
    parser = argparse.ArgumentParser(description="D7 machine attribution Batch 1 guarded rollback")
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
