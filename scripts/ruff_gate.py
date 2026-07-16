#!/usr/bin/env python3
"""Ruff new-violation gate (Release 0.9.13, Phase 2).

The codebase carries a lint backlog (~560 findings) that this release does not
fix. Instead it is *baselined*: recorded once, and never allowed to grow. This
gate enforces the ratchet.

    python scripts/ruff_gate.py                 # fail if any NEW violation appeared
    python scripts/ruff_gate.py --update        # record the current state as baseline
    python scripts/ruff_gate.py --assert-not-grown <old-baseline.json>
                                                # fail if the committed baseline grew

The `--assert-not-grown` mode closes the bypass in the plain gate: someone could
add a violation AND run `--update`, so the baseline absorbs it and the gate
passes. New code must be clean, so the baseline should only ever shrink (burndown)
or hold. CI compares this PR's baseline against the base branch's and fails if any
(file, rule) count rose.

The baseline is a count of findings per (file, rule) — not per line — so it
survives edits that shift line numbers or move code around. A finding is "new"
only if a (file, rule) pair appears that was not in the baseline, or its count
rises above the baselined count. That is exactly "a new violation was
introduced": brand-new files with findings fail (no baseline entry), and no
existing file may add findings, while an unrelated edit to a file that still
carries baselined findings does not block.

Legacy files nobody touches are never implicated. Fixing findings only ever
lowers a count, which the gate rewards by telling you to refresh the baseline.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "docs" / "ruff-baseline.json"
# scripts/ is included so the tooling gates itself; ruff traversal skips non-.py.
TARGETS = ["app", "tests", "migrations", "scripts"]


def current_counts() -> Counter:
    """Return {"path::code": count} for the current tree, honouring pyproject."""
    proc = subprocess.run(
        ["ruff", "check", *TARGETS, "--output-format=json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # ruff exits non-zero when findings exist; that is expected, not an error.
    if proc.returncode not in (0, 1):
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"ruff failed to run (exit {proc.returncode})")

    counts: Counter = Counter()
    for finding in json.loads(proc.stdout or "[]"):
        rel = Path(finding["filename"]).resolve().relative_to(REPO_ROOT)
        counts[f"{rel.as_posix()}::{finding['code']}"] += 1
    return counts


def load_baseline() -> Counter:
    if not BASELINE.exists():
        raise SystemExit(f"No baseline at {BASELINE}. Run: python scripts/ruff_gate.py --update")
    return Counter(json.loads(BASELINE.read_text()))


def write_baseline(counts: Counter) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: counts[k] for k in sorted(counts)}
    BASELINE.write_text(json.dumps(ordered, indent=2) + "\n")
    total = sum(counts.values())
    print(f"Baseline updated: {total} findings across {len(counts)} (file, rule) pairs.")


def assert_not_grown(old_baseline_path: str) -> int:
    """Fail if the committed baseline grew versus a reference baseline.

    Guards against burying a new violation by regenerating the baseline. A brand
    new file with findings, or any file whose count rose, is a regression here
    too — new code is required to be clean, so the baseline never legitimately
    grows.
    """
    old = Counter(json.loads(Path(old_baseline_path).read_text()))
    new = load_baseline()
    grew = {k: (old.get(k, 0), new[k]) for k in new if new[k] > old.get(k, 0)}
    if grew:
        print("The committed Ruff baseline GREW — a violation was baselined away:\n")
        for key in sorted(grew):
            was, now = grew[key]
            path, code = key.rsplit("::", 1)
            print(f"  {path}  [{code}]  ({'added to baseline' if was == 0 else f'{was} -> {now}'})")
        print("\nThe baseline may only shrink. Fix the finding instead of recording it.")
        return 1
    print(f"Baseline did not grow ({sum(new.values())} findings, was {sum(old.values())}).")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--assert-not-grown":
        if len(argv) < 2:
            raise SystemExit("usage: ruff_gate.py --assert-not-grown <old-baseline.json>")
        return assert_not_grown(argv[1])

    counts = current_counts()

    if "--update" in argv:
        write_baseline(counts)
        return 0

    baseline = load_baseline()

    regressions = {k: (baseline.get(k, 0), counts[k]) for k in counts if counts[k] > baseline.get(k, 0)}
    improvements = sum(max(0, baseline[k] - counts.get(k, 0)) for k in baseline)

    if regressions:
        print("NEW Ruff violations introduced (the lint gate fails):\n")
        for key in sorted(regressions):
            was, now = regressions[key]
            path, code = key.rsplit("::", 1)
            label = "new file/rule" if was == 0 else f"{was} -> {now}"
            print(f"  {path}  [{code}]  ({label})")
        print(
            "\nFix these in the files you changed. Do NOT edit unrelated legacy files, and\n"
            "do NOT run --update to bury them — the baseline records only pre-existing debt."
        )
        return 1

    total = sum(counts.values())
    msg = f"No new Ruff violations. Baseline holds ({total} known findings)."
    if improvements:
        msg += f" {improvements} fewer than baseline — run `python scripts/ruff_gate.py --update` to lock in the win."
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
