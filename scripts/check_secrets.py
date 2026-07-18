#!/usr/bin/env python3
"""Lightweight secret-hygiene check (E1.4).

Dependency-free and conservative — high-signal patterns only, so it can gate CI
without false positives. It is a baseline, not a replacement for a full secret
scanner (documented as a future improvement in docs/CI.md).

Checks tracked files (git ls-files — never touches gitignored real env files):
  1. No real environment file is committed (`.env`, `*.env`), only `*.env.example`.
  2. No committed file contains an unambiguous credential:
       * a PEM private key block
       * an AWS access key id (AKIA + 16 uppercase alphanumerics)

Usage:  python scripts/check_secrets.py
Exit 0 when clean, 1 on any finding.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Files that legitimately contain the patterns as documentation/regex.
SKIP = {
    "scripts/check_secrets.py",
    "tests/test_e1_4_ci_gates.py",
}
SKIP_PREFIXES = ("docs/",)

PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
AWS = re.compile(r"AKIA[0-9A-Z]{16}")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def main() -> int:
    findings: list[str] = []

    for path in tracked_files():
        name = Path(path).name
        # 1. Real env files must never be tracked (example templates are fine).
        if (name == ".env" or name.endswith(".env")) and not name.endswith(".env.example"):
            findings.append(f"tracked environment file (should be gitignored): {path}")

        if path in SKIP or path.startswith(SKIP_PREFIXES):
            continue

        p = Path(path)
        try:
            if p.stat().st_size > 2_000_000:  # skip large/binary blobs
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError):
            continue

        if PEM.search(text):
            findings.append(f"PEM private key material in: {path}")
        if AWS.search(text):
            findings.append(f"AWS access key id in: {path}")

    if findings:
        print("SECRET HYGIENE CHECK FAILED:", file=sys.stderr)
        for f in findings:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: no committed secrets or real env files detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
