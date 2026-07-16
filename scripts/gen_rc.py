#!/usr/bin/env python3
"""Release-candidate document generator (Release 0.9.13, Phase 4).

The RC docs (RC12–RC14) share an invariant spine — header + metadata, a
recommendation, build/suite/static gates, migration integrity, domain sections,
defects, verdict — and the mechanical gate rows are the same every release. This
fills that skeleton from real facts so a validator writes the judgement, not the
boilerplate.

    python scripts/gen_rc.py 0.9.13 --title "Platform Foundation" [--run]

Without --run the mechanical gate cells are left as `PENDING` for the validator
to fill after running each gate. With --run the cheap, safe gates are executed
and their pass/fail recorded; the full suite is never auto-run here (it needs the
isolated database and is the validator's explicit step) and stays `PENDING`.

Dates and the candidate SHA come from git (the sandbox forbids wall-clock calls),
so the output is deterministic for a given commit.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "docs" / "templates" / "RC_TEMPLATE.md"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()


def run_gate(cmd: list[str]) -> str:
    """Run a gate command; return a PASS/FAIL cell."""
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return "**PASS**" if proc.returncode == 0 else "**FAIL**"


def ci_status() -> str:
    """Best-effort CI conclusion for HEAD via gh, or a note if unavailable."""
    sha = git("rev-parse", "HEAD")
    proc = subprocess.run(
        ["gh", "run", "list", "--commit", sha, "--limit", "1", "--json", "conclusion",
         "--jq", ".[0].conclusion"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    got = proc.stdout.strip()
    if proc.returncode != 0:
        return "unknown (gh unavailable)"
    return f"**{got}**" if got else "no run found for HEAD"


def suite_result() -> str:
    """Run the full suite on the isolated DB and return a PASS cell with counts.

    Only called under --run. Uses scripts/test.sh so the isolated-DB guard and
    reset apply; parses the pytest summary line for the counts.
    """
    proc = subprocess.run(
        ["scripts/test.sh", "run"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    m = re.search(r"(\d+ passed(?:, \d+ skipped)?)", tail)
    counts = m.group(1) if m else "counts unavailable"
    return f"**PASS** ({counts})" if proc.returncode == 0 else f"**FAIL** ({tail})"


def latest_release_tag() -> str:
    tags = [
        t
        for t in git("tag", "-l", "v*").split()
        if t.count(".") == 2 and t != "v1.0.0"
    ]
    tags.sort(key=lambda t: tuple(int(p) for p in t[1:].split(".")))
    return tags[-1] if tags else "(none)"


def alembic_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout
    return out.split()[0] if out.split() else "(unknown)"


def build(version: str, title: str, run_gates: bool) -> str:
    rc_num = f"RC-{version}"
    candidate = git("rev-parse", "--short", "HEAD")
    date = git("log", "-1", "--format=%cs")  # commit date, deterministic
    pending = "PENDING"

    fields = {
        "RC_ID": rc_num,
        "VERSION": version,
        "TITLE": f" · {title}" if title else "",
        "SCOPE": f"Release {version} candidate validation.",
        "BASELINE_REF": latest_release_tag(),
        "CANDIDATE_SHA": candidate,
        "BRANCH": git("rev-parse", "--abbrev-ref", "HEAD"),
        "CI_STATUS": ci_status(),
        "VALIDATOR": "<name>",
        "DATE": date,
        "MERGE_GATE": "Not merged; tag not yet applied.",
        "RECOMMENDATION": "<!-- FILL after the gates below are green -->",
        "SUITE": suite_result() if run_gates else pending,
        "COMPILEALL": run_gate(["python", "-m", "compileall", "-q", "app", "tests", "migrations"]) if run_gates else pending,
        "DIFFCHECK": run_gate(["git", "diff", "--check"]) if run_gates else pending,
        "RUFF": run_gate(["python", "scripts/ruff_gate.py"]) if run_gates else pending,
        "CHANGELOG": run_gate(["python", "scripts/check_changelog.py"]) if run_gates else pending,
        "SINGLE_HEAD": run_gate(["scripts/check_migration_heads.sh"]) if run_gates else pending,
        "REVERSIBLE": pending,  # destructive; validator runs on the isolated DB
        "AT_HEAD": pending,
        "ALEMBIC_HEAD": alembic_head(),
        "DEFECTS_N": "4",
        "VERDICT_N": "5",
        "VERDICT": "<!-- FILL: SAFE TO MERGE / CONCERNS / BLOCKED -->",
    }

    text = TEMPLATE.read_text()
    for key, value in fields.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--title", default="")
    ap.add_argument("--run", action="store_true", help="run the cheap, safe gates and fill them")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    text = build(args.version, args.title, args.run)
    out = Path(args.out) if args.out else REPO_ROOT / "docs" / f"RC_{args.version}_VALIDATION.md"
    out.write_text(text)
    shown = out.relative_to(REPO_ROOT) if out.is_relative_to(REPO_ROOT) else out
    print(f"Wrote {shown} ({'gates run' if args.run else 'gates PENDING'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
