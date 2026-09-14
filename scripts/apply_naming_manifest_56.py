"""Apply one hash-pinned Client360 display-name manifest atomically.

Dry-run is the default. This runner changes only documents.display_name and writes the
corresponding audit events in the same database transaction. It never renames, moves,
opens, or otherwise changes a physical file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import bindparam, select, text

from app.db import documents, engine, users
from app.security.audit import write_audit_event
from app.services import document_name_safety as safety
from app.services.document_normalization_preview import build_preview

DEFAULT_ROOT = Path(r"C:\Client360Data\filing-inventory")
EXPECTED_MANIFEST_SHA = "7e0a3e670ffeabcd6a94d4d4e668d4f786fcbbb2023877cee9088ed6b02a7d7d"
EXPECTED_OWNER_SHA = "91ff089c79fb47afb910cc8a24a667a201b2884ca6e2fa9e09b794e6e2540475"
EXPECTED_PRIOR_PAYLOAD = "f71b2acc126fc36744ab6aa578e6dff5687f24b859733cb1a40d7fe2031d52fd"
EXPECTED_PINNED_PAYLOAD = "2d95638d960a20fb01ecd633b75d5f7912be8bf2b6fd90d347effd813181ceed"
AUTHORIZATION_PHRASE = f"APPLY-CLIENT360-DISPLAY-NAMES-56-{EXPECTED_PINNED_PAYLOAD}"
OWNER_COLUMNS = ("person_id", "household_id", "organization_id")


class Refused(RuntimeError):
    """A guard refused the run before commit."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_authority(root: Path):
    manifest_path = root / "naming-approval-manifest.json"
    owner_path = root / "naming-owner-ids-56.json"
    if not manifest_path.is_file() or not owner_path.is_file():
        raise Refused("required manifest or owner snapshot is missing")
    if _sha256(manifest_path) != EXPECTED_MANIFEST_SHA:
        raise Refused("approval manifest SHA-256 mismatch")
    if _sha256(owner_path) != EXPECTED_OWNER_SHA:
        raise Refused("owner snapshot SHA-256 mismatch")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = json.loads(owner_path.read_text(encoding="utf-8"))
    if manifest.get("approval_payload_sha256") != EXPECTED_PRIOR_PAYLOAD:
        raise Refused("approval payload SHA-256 mismatch")
    if snapshot.get("source_manifest_payload_sha256") != EXPECTED_PRIOR_PAYLOAD:
        raise Refused("owner snapshot belongs to another approval manifest")

    approval_rows = manifest.get("approvals") or []
    owner_rows = snapshot.get("rows") or []
    approvals = {int(row["document_id"]): row for row in approval_rows}
    owners = {int(row["document_id"]): row for row in owner_rows}
    if (
        len(approval_rows) != 56
        or len(owner_rows) != 56
        or len(approvals) != 56
        or len(owners) != 56
        or set(approvals) != set(owners)
    ):
        raise Refused("exact 56-document authority set differs")

    pinned = []
    for document_id in sorted(approvals):
        approval = approvals[document_id]
        owner = owners[document_id]
        if sum(owner.get(column) is not None for column in OWNER_COLUMNS) != 1:
            raise Refused(f"document {document_id} does not have exactly one owner ID")
        if owner.get("expected_current_display_name") != approval.get(
            "expected_current_display_name"
        ):
            raise Refused(f"document {document_id} snapshot display name differs")
        approved_name = (approval.get("approved_display_name") or "").strip()
        if not approved_name or safety.scan(approved_name):
            raise Refused(f"document {document_id} approved name is empty or unsafe")
        pinned.append(
            {
                **approval,
                "expected_person_id": owner.get("person_id"),
                "expected_household_id": owner.get("household_id"),
                "expected_organization_id": owner.get("organization_id"),
            }
        )

    payload = json.dumps(
        pinned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    actual_payload_sha = hashlib.sha256(payload).hexdigest()
    if actual_payload_sha != EXPECTED_PINNED_PAYLOAD:
        raise Refused(f"owner-pinned payload SHA-256 mismatch: {actual_payload_sha}")
    return approvals, owners


def _check_live_preview(approvals):
    report = build_preview()
    by_id = {int(row["document_id"]): row for row in report["rows"]}
    for document_id in sorted(approvals):
        row = by_id.get(document_id)
        if row is None:
            raise Refused(f"document {document_id} is absent from the live naming preview")
        if row.get("bucket") != "SAFE":
            raise Refused(
                f"document {document_id} live bucket is {row.get('bucket')!r}, not SAFE"
            )
        if row.get("collision"):
            raise Refused(f"document {document_id} has a live resolver collision")


def _check_locked_rows(rows, approvals, owners):
    if len(rows) != 56:
        found = {int(row["id"]) for row in rows}
        raise Refused(f"missing documents: {sorted(set(approvals) - found)}")
    current = {int(row["id"]): row for row in rows}
    for document_id in sorted(approvals):
        row = current[document_id]
        owner = owners[document_id]
        # The reviewed export represented both NULL and empty string as blank. The existing
        # production apply service writes only NULL names, so this batch deliberately does too.
        if row["display_name"] is not None:
            raise Refused(f"document {document_id} display_name is no longer NULL")
        for column in OWNER_COLUMNS:
            if row[column] != owner.get(column):
                raise Refused(f"document {document_id} {column} changed")
    return current


def _check_collisions(collision_rows, approvals, owners):
    seen = {}
    for document_id in sorted(approvals):
        approval = approvals[document_id]
        owner = owners[document_id]
        owner_key = tuple(owner.get(column) for column in OWNER_COLUMNS)
        key = (owner_key, approval["approved_display_name"].casefold())
        if key in seen:
            raise Refused(
                f"documents {seen[key]} and {document_id} duplicate a name for one owner"
            )
        seen[key] = document_id

    for row in collision_rows:
        existing_id = int(row["id"])
        for document_id, approval in approvals.items():
            if existing_id == document_id:
                continue
            owner = owners[document_id]
            if (
                (row["display_name"] or "").casefold()
                == approval["approved_display_name"].casefold()
                and all(row[column] == owner.get(column) for column in OWNER_COLUMNS)
            ):
                raise Refused(
                    f"document {document_id} name already used by document {existing_id}"
                )


def _write_rollback_snapshot(root, current, approvals, owners):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"naming-rollback-56-{stamp}.json"
    payload = {
        "schema": "client360.naming-rollback.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "approval_payload_sha256": EXPECTED_PINNED_PAYLOAD,
        "rows": [
            {
                "document_id": document_id,
                "before_display_name": current[document_id]["display_name"],
                "after_display_name": approvals[document_id]["approved_display_name"],
                **{
                    column: owners[document_id].get(column)
                    for column in OWNER_COLUMNS
                },
            }
            for document_id in sorted(approvals)
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path, _sha256(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the exact reviewed 56-row naming manifest atomically"
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize", default="")
    args = parser.parse_args(argv)

    if args.apply and args.authorize != AUTHORIZATION_PHRASE:
        raise Refused("apply requires the exact authorization phrase")
    if not args.apply and args.authorize:
        raise Refused("--authorize is invalid without --apply")
    if args.user_id <= 0:
        raise Refused("--user-id must be a positive staff user ID")

    approvals, owners = _load_authority(args.root)
    _check_live_preview(approvals)

    ids = sorted(approvals)
    names = sorted({row["approved_display_name"] for row in approvals.values()})
    select_ids = text(
        """
        SELECT id, display_name, person_id, household_id, organization_id
        FROM documents WHERE id IN :ids ORDER BY id FOR UPDATE
        """
    ).bindparams(bindparam("ids", expanding=True))
    select_names = text(
        """
        SELECT id, display_name, person_id, household_id, organization_id
        FROM documents WHERE display_name IN :names
        """
    ).bindparams(bindparam("names", expanding=True))
    update_name = (
        documents.update()
        .where(
            documents.c.id == bindparam("target_document_id"),
            documents.c.display_name.is_(None),
            documents.c.person_id.is_not_distinct_from(bindparam("expected_person_id")),
            documents.c.household_id.is_not_distinct_from(
                bindparam("expected_household_id")
            ),
            documents.c.organization_id.is_not_distinct_from(
                bindparam("expected_organization_id")
            ),
        )
        .values(display_name=bindparam("new_display_name"))
    )

    rollback_path = rollback_sha = None
    request_id = f"naming-manifest-{EXPECTED_PINNED_PAYLOAD[:12]}-{uuid.uuid4()}"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            actor_exists = connection.execute(
                select(users.c.id).where(users.c.id == args.user_id)
            ).scalar_one_or_none()
            if actor_exists is None:
                raise Refused(f"staff user {args.user_id} does not exist")

            rows = connection.execute(select_ids, {"ids": ids}).mappings().all()
            current = _check_locked_rows(rows, approvals, owners)
            collision_rows = connection.execute(
                select_names, {"names": names}
            ).mappings().all()
            _check_collisions(collision_rows, approvals, owners)

            if not args.apply:
                transaction.rollback()
                print("ALL-OR-NOTHING APPLY DRY RUN: PASSED")
                print("DOCUMENTS=56")
                print(f"OWNER_PINNED_PAYLOAD_SHA256={EXPECTED_PINNED_PAYLOAD}")
                print("DATABASE_WRITES=0")
                print("PHYSICAL_FILES_CHANGED=0")
                print(f"AUTHORIZATION_PHRASE={AUTHORIZATION_PHRASE}")
                return 0

            rollback_path, rollback_sha = _write_rollback_snapshot(
                args.root, current, approvals, owners
            )
            for document_id in ids:
                approval = approvals[document_id]
                owner = owners[document_id]
                result = connection.execute(
                    update_name,
                    {
                        "target_document_id": document_id,
                        "new_display_name": approval["approved_display_name"],
                        "expected_person_id": owner.get("person_id"),
                        "expected_household_id": owner.get("household_id"),
                        "expected_organization_id": owner.get("organization_id"),
                    },
                )
                if result.rowcount != 1:
                    raise Refused(
                        f"document {document_id} guarded update affected "
                        f"{result.rowcount} rows"
                    )
                write_audit_event(
                    action="document.display_name.set",
                    entity_type="document",
                    entity_id=document_id,
                    actor_user_id=args.user_id,
                    request_id=request_id,
                    metadata={
                        "display_name": approval["approved_display_name"],
                        "source": "hash_pinned_naming_manifest",
                        "approval_payload_sha256": EXPECTED_PINNED_PAYLOAD,
                    },
                    conn=connection,
                )

            verify = connection.execute(select_ids, {"ids": ids}).mappings().all()
            for row in verify:
                document_id = int(row["id"])
                if row["display_name"] != approvals[document_id]["approved_display_name"]:
                    raise Refused(
                        f"document {document_id} failed in-transaction verification"
                    )
            transaction.commit()
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise

    with engine.connect() as connection:
        post = connection.execute(
            select(documents.c.id, documents.c.display_name)
            .where(documents.c.id.in_(ids))
            .order_by(documents.c.id)
        ).mappings().all()
    if len(post) != 56:
        raise Refused("post-commit document count differs")
    for row in post:
        document_id = int(row["id"])
        if row["display_name"] != approvals[document_id]["approved_display_name"]:
            raise Refused(f"document {document_id} failed post-commit verification")

    print("ALL-OR-NOTHING APPLY: COMMITTED")
    print("ROWS_APPLIED=56")
    print(f"OWNER_PINNED_PAYLOAD_SHA256={EXPECTED_PINNED_PAYLOAD}")
    print(f"ROLLBACK_SNAPSHOT={rollback_path}")
    print(f"ROLLBACK_SHA256={rollback_sha}")
    print("AUDIT_EVENTS_WRITTEN=56")
    print("PHYSICAL_FILES_CHANGED=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(1)
