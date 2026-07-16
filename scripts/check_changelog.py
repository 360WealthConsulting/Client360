#!/usr/bin/env python3
"""CHANGELOG.md structural lint (Release 0.9.13, Phase 4).

The changelog had drifted in ways nothing caught: 0.9.11 was headed "release
candidate; not yet tagged" after it had been tagged, 0.9.12 had no entry at all
despite being tagged and RC-validated, and a stray `# Unreleased` H1 at the
bottom contradicted the real one at the top. This lint makes those states fail.

    python scripts/check_changelog.py

Checks:
  1. exactly one top-level `# Changelog` H1 and no other bare H1
     (a stray `# Unreleased` is the exact bug this catches)
  2. exactly one `## [Unreleased]`
  3. every other `## [X.Y.Z]` heading carries an ISO date `— YYYY-MM-DD`
  4. every release git tag `vX.Y.Z` has a matching `[X.Y.Z]` entry
     (known stray tags are ignored; see KNOWN_STRAY_TAGS)
  5. entries are in descending version order
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# v1.0.0 predates the whole 0.9.x line (it tags "Add Alembic migration
# framework"); it is a known stray mis-tag, tracked separately, not a release.
KNOWN_STRAY_TAGS = {"v1.0.0"}

H1 = re.compile(r"^# (?!Changelog$)(.+)$")
VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\](.*)$")
DATED = re.compile(r"—\s*\d{4}-\d{2}-\d{2}")


def release_tags() -> set[str]:
    out = subprocess.run(
        ["git", "tag", "-l", "v*"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout
    return {t for t in out.split() if re.fullmatch(r"v\d+\.\d+\.\d+", t)} - KNOWN_STRAY_TAGS


def main() -> int:
    lines = CHANGELOG.read_text().splitlines()
    errors: list[str] = []

    # 1. no stray bare H1 (other than the leading `# Changelog`)
    for i, line in enumerate(lines, 1):
        if H1.match(line):
            errors.append(f"line {i}: stray top-level heading {line!r} (only '# Changelog' is allowed)")

    # 2 + 3 + 5. version headings
    unreleased = sum(1 for line in lines if line.strip() == "## [Unreleased]")
    if unreleased != 1:
        errors.append(f"expected exactly one '## [Unreleased]', found {unreleased}")

    versions: list[str] = []
    for i, line in enumerate(lines, 1):
        m = VERSION_HEADING.match(line)
        if not m:
            continue
        version, rest = m.group(1), m.group(2)
        versions.append(version)
        if not DATED.search(rest):
            errors.append(f"line {i}: [{version}] heading has no '— YYYY-MM-DD' date")

    def as_tuple(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.split("."))

    for a, b in zip(versions, versions[1:], strict=False):
        if as_tuple(a) <= as_tuple(b):
            errors.append(f"entries out of order: [{a}] appears before [{b}] (want descending)")

    # 4. every release tag has an entry
    documented = set(versions)
    for tag in sorted(release_tags()):
        if tag[1:] not in documented:
            errors.append(f"git tag {tag} has no CHANGELOG entry [{tag[1:]}]")

    if errors:
        print("CHANGELOG.md has problems:\n")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"CHANGELOG.md OK: {len(versions)} released versions, all dated, all tags documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
