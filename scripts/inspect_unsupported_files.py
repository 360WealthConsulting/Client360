"""Phase 5 — READ-ONLY inspection of specific unsupported files (before deciding on extractors).

Reports, for each given document id, its filename, source path, size, content_type/MIME, whether the file
exists, the first-16-byte magic identification, and (for SQLite) the table names. Reads only 16 bytes
(plus SQLite schema, read-only). Parses no proprietary contents; writes nothing.

Usage:
    python scripts/inspect_unsupported_files.py --doc 22290 22291 5038 5328 9836 22401
"""
from __future__ import annotations

import argparse

from app.services.document_unsupported import inspect_files


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY inspection of unsupported files.")
    ap.add_argument("--doc", type=int, nargs="+", required=True)
    args = ap.parse_args(argv)
    print("=" * 74)
    print("UNSUPPORTED FILE INSPECTION (READ-ONLY)")
    print("=" * 74)
    for r in inspect_files(args.doc):
        if r.get("error"):
            print(f"  #{r['document_id']}: {r['error']}")
            continue
        print(f"  #{r['document_id']}  {r['filename']}  [.{r['extension']}]")
        print(f"      source_path : {r['source_path']!r}")
        print(f"      size_bytes  : {r['size_bytes']}   content_type: {r['content_type']}")
        print(f"      exists      : {r['file_exists']}   magic: {r['magic_hex']}")
        print(f"      identified  : {r['identified']}")
        if r.get("sqlite_tables") is not None:
            print(f"      sqlite tables: {r['sqlite_tables']}")
    print("=" * 74)
    print("READ-ONLY: nothing written; no proprietary contents parsed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
