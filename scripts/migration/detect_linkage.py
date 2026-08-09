"""READ-ONLY preview of the unresolved-subject linkage detector.

Runs the PR-3 detector in PREVIEW mode: it reports how many unresolved subjects (TaxDome folders) exist,
how many exceptions would be created, how many are already open / would reopen, how many are skipped
because a reusable approved resolution already exists, and any assembly errors. It makes ZERO writes and
NEVER creates exceptions — production creation is performed by the review workflow with an authorized
principal, not by this preview CLI.

Usage::
    python -m scripts.migration.detect_linkage [--limit N]
"""
from __future__ import annotations

import argparse
import sys

from app.services.migration.linkage_detector import detect


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.detect_linkage",
                                description="Read-only preview of the linkage unresolved-subject detector.")
    p.add_argument("--limit", type=int, default=None, help="Only consider the first N subjects.")
    args = p.parse_args(argv)

    s = detect(preview=True, limit=args.limit)
    print("=== linkage detector — PREVIEW (zero writes) ===")
    print(f"  source_system      : {s['source_system']}")
    print(f"  total_subjects     : {s['total_subjects']}")
    print(f"  would_create       : {s['would_create']}")
    print(f"  already_open       : {s['already_open']}")
    print(f"  would_reopen       : {s['would_reopen']}")
    print(f"  skipped_reusable   : {s['skipped_reusable']}")
    print(f"  held_no_candidates : {s['held_no_candidates']}")
    print(f"  errors             : {s['errors']}")
    for e in s["error_details"][:20]:
        print(f"    - {e['subject_key']}: {e['error']}")
    print("\nPreview complete (read-only). No exceptions created, no document/file/canonical changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
