"""Stage A remediation CLI — Drake joint-return people + households (guarded).

PREVIEW is read-only. APPLY is guarded: it requires ``--confirm``, a verified non-empty ``--backup``
file, and the approved ``--expect`` bucket count, and it fails closed on count drift before any write.
Stage A never touches documents, storage_uri, document_sources, or files.

Usage::
    python -m scripts.migration.remediate_joint_households --stage a1 --preview
    python -m scripts.migration.remediate_joint_households --stage a1 --apply --confirm \
        --backup D:\\Client360\\SQLBackups\\pre-stageA1.dump --expect 53
    python -m scripts.migration.remediate_joint_households --stage a2 --apply --confirm \
        --backup D:\\Client360\\SQLBackups\\pre-stageA2.dump --expect 127
    python -m scripts.migration.remediate_joint_households --verify
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.joint_household_remediation import (
    RemediationGuardError,
    apply_stage_a,
    preview_stage_a,
    verify_stage_a,
)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.remediate_joint_households",
                                description="Stage A guarded people+households remediation (no documents).")
    p.add_argument("--stage", choices=["a1", "a2"], help="A1 (household-only) or A2 (promote + household).")
    p.add_argument("--preview", action="store_true", help="Read-only preview.")
    p.add_argument("--apply", action="store_true", help="Guarded APPLY (needs --confirm/--backup/--expect).")
    p.add_argument("--verify", action="store_true", help="Post-apply verification (read-only).")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--backup", default=None)
    p.add_argument("--expect", type=int, default=None)
    args = p.parse_args(argv)

    if args.verify:
        v = verify_stage_a()
        print("=== Stage A verification (read-only) ===")
        for k in ("A1_couples_left_in_bucket", "A1_remaining_actionable", "A1_conflicts",
                  "A2_couples_left_in_bucket"):
            print(f"  {k}: {v[k]}")
        return 0

    if args.preview or not args.apply:
        prev = preview_stage_a()
        for stage in ("A1", "A2"):
            s = prev[stage]
            print(f"=== {stage} PREVIEW (read-only) ===")
            print(f"  couples: {s['couples']}   actions: {s.get('actions')}")
            if stage == "A1":
                print(f"  actionable: {s['actionable']}  already_done: {s['already_done']}  "
                      f"conflicts: {s['conflicts']}")
        return 0

    stage = (args.stage or "").upper()
    if stage not in ("A1", "A2"):
        print("ERROR: --apply requires --stage a1|a2")
        return 2
    try:
        res = apply_stage_a(stage, confirm=args.confirm, backup=args.backup, expect=args.expect)
    except RemediationGuardError as exc:
        print(f"GUARD ABORT: {exc}")
        return 3
    print(f"=== {stage} APPLY complete ===")
    for k in ("couples", "created_households", "assigned_households", "promoted_people", "skipped"):
        print(f"  {k}: {res[k]}")
    if res["conflicts"]:
        print(f"  conflicts (held, no write): {len(res['conflicts'])}")
        for c in res["conflicts"][:20]:
            print(f"    - {c}")
    print("\nStage A APPLY touched people + households only. No documents/storage/files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
