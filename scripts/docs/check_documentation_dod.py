#!/usr/bin/env python3
"""Documentation Definition-of-Done checker — ADVISORY (Release 0.11.0 · P4, decision D6).

Validates documentation quality and Publication Register integrity. **Advisory by default**: it
reports findings and exits 0. `--strict` returns non-zero on ERRORS (for local testing / a future
authorized enforcement phase) — it is NOT wired as blocking CI in Release 0.11.0. `--changed`
focuses document-level checks on files changed vs a Git base while still running repository-wide
register/generated-file integrity.

Register validation is NOT reimplemented here — it reuses the P3 tooling by subprocess:
  scripts/registers/validate_register.py   (schema, areas, profiles, doctypes, statuses,
    canonical-source, unique page_id, unique canonical identity, AD-5 invariant, complete coverage,
    no duplicate Hybrid rows, all areas + SHARED/GOV, legacy non-canonical, governance rows, known
    Confluence IDs, crosswalk currency)
  scripts/registers/gen_crosswalk.py --check  (generated crosswalk is current)

Findings never expose a discovered secret value: only the filename, rule name, and a masked/
generalized note are reported.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

try:
    import yaml
    HAVE_YAML = True
except Exception:  # pragma: no cover - yaml is installed in CI and locally
    HAVE_YAML = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REG_VALIDATOR = os.path.join(ROOT, "scripts", "registers", "validate_register.py")
GEN = os.path.join(ROOT, "scripts", "registers", "gen_crosswalk.py")
PAGES = os.path.join(ROOT, "docs", "registers", "pages.yml")
CROSSWALK = os.path.join(ROOT, "docs", "DOCUMENTATION_CROSSWALK.md")

# Documentation surface scanned for file-level checks (NOT scripts/ — avoids self-matching).
SCAN_DIRS = [os.path.join(ROOT, "docs"), os.path.join(ROOT, "governance")]
EXTRA_FILES = [os.path.join(ROOT, ".github", "pull_request_template.md")]
# generated files must retain their warning + be current
GENERATED = {CROSSWALK: ("GENERATED FILE", "DO NOT EDIT")}
VALID_STATUS = {"planned", "draft", "published", "needs_review"}

ERROR, WARNING = "ERROR", "WARNING"

# --- conservative secret / client-data patterns (value never printed) --------
SECRET_RULES = [
    (ERROR, "private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (ERROR, "aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (ERROR, "assigned_secret",
     re.compile(r"(?i)\b(password|passwd|api[_-]?key|secret|client_secret|access_token|token)\b"
                r"\s*[:=]\s*['\"][^'\"\s]{6,}['\"]")),
    (ERROR, "bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    (ERROR, "ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (WARNING, "account_number_like", re.compile(r"\b\d{10,17}\b")),
]
CONFLICT = re.compile(r"^(<{7}|>{7})(\s|$)")
CONFLICT_MID = re.compile(r"^={7}$")
LINK = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)]+)\)")


class Report:
    def __init__(self):
        self.findings = []  # (severity, rule, relpath, message)

    def add(self, severity, rule, path, message):
        rel = os.path.relpath(path, ROOT) if os.path.isabs(str(path)) else path
        self.findings.append((severity, rule, rel, message))

    @property
    def errors(self):
        return [f for f in self.findings if f[0] == ERROR]

    @property
    def warnings(self):
        return [f for f in self.findings if f[0] == WARNING]


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


def check_register(rep):
    """Reuse the P3 register validator (all register invariants) by subprocess."""
    r = _run([sys.executable, REG_VALIDATOR])
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()
        rep.add(ERROR, "register_integrity", "docs/registers/pages.yml",
                "P3 register validation failed: " + (" | ".join(detail[:6]) or "unknown"))
    return r.returncode == 0


def check_crosswalk_current(rep):
    r = _run([sys.executable, GEN, "--check"])
    if r.returncode != 0:
        rep.add(ERROR, "generated_stale", "docs/DOCUMENTATION_CROSSWALK.md",
                "crosswalk not current with pages.yml — regenerate with gen_crosswalk.py")


def check_generated_warnings(rep):
    for path, markers in GENERATED.items():
        if not os.path.exists(path):
            rep.add(ERROR, "generated_missing", path, "generated file missing")
            continue
        text = _read(path)
        for m in markers:
            if m not in text:
                rep.add(ERROR, "generated_warning_removed", path,
                        f"generated-file warning missing marker '{m}'")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except Exception:  # pragma: no cover
        return ""


def scan_file(rep, path):
    text = _read(path)
    if not text.strip():
        rep.add(WARNING, "empty_file", path, "file is empty")
        return
    # merge markers
    has_side = any(CONFLICT.match(ln) for ln in text.splitlines())
    for i, ln in enumerate(text.splitlines(), 1):
        if CONFLICT.match(ln) or (has_side and CONFLICT_MID.match(ln)):
            rep.add(ERROR, "merge_conflict_marker", path, f"conflict marker at line {i}")
            break
    # secrets / client data (never print the value)
    for severity, rule, rx in SECRET_RULES:
        if rx.search(text):
            rep.add(severity, rule, path, "pattern matched (value masked)")
    # relative markdown links
    base = os.path.dirname(path)
    for m in LINK.finditer(text):
        target = m.group(1).split()[0].strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        resolved = os.path.normpath(os.path.join(base, target))
        if not os.path.exists(resolved):
            rep.add(WARNING, "broken_relative_link", path, f"link target not found: {target}")
    # YAML front matter (where present)
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        block = text[4:end] if end != -1 else ""
        if HAVE_YAML:
            try:
                fm = yaml.safe_load(block) or {}
            except Exception as e:
                rep.add(ERROR, "frontmatter_unparseable", path, f"front matter YAML error: {e}")
                fm = None
            if isinstance(fm, dict):
                for field in ("owner", "reviewer", "status", "review_cycle"):
                    if field not in fm or fm.get(field) in (None, ""):
                        rep.add(WARNING, "frontmatter_missing_field", path,
                                f"front matter missing '{field}'")
                if fm.get("status") and fm["status"] not in VALID_STATUS:
                    rep.add(ERROR, "frontmatter_bad_status", path,
                            f"invalid status '{fm['status']}'")
                if fm.get("compliance_gate") not in (None, "none") and fm.get("status") == "published":
                    rep.add(ERROR, "frontmatter_ad5_published", path,
                            "compliance_gate set but status=published")


def check_register_advisory(rep):
    """Advisory ownership / canonical-source checks read directly from pages.yml.
    Hard invariants are already enforced (as ERRORS) by the P3 validator; these add WARNINGS."""
    if not HAVE_YAML or not os.path.exists(PAGES):
        return
    try:
        doc = yaml.safe_load(_read(PAGES))
    except Exception as e:
        rep.add(ERROR, "pages_yml_unparseable", PAGES, f"pages.yml YAML error: {e}")
        return
    for r in doc.get("pages", []):
        pid = r.get("page_id")
        for field in ("owner", "reviewer", "status", "review_cycle", "next_review"):
            if r.get(field) in (None, ""):
                rep.add(WARNING, "register_missing_ownership", "docs/registers/pages.yml",
                        f"{pid}: missing '{field}'")
        # AD-5 rows must keep reviewer UNFILLED (business owner is not a compliance certifier)
        if r.get("compliance_gate") not in (None, "none"):
            rev = str(r.get("reviewer") or "")
            if "UNFILLED" not in rev.upper():
                rep.add(ERROR, "ad5_reviewer_not_unfilled", "docs/registers/pages.yml",
                        f"{pid}: AD-5 row reviewer must remain UNFILLED, not a named certifier")
        # git-canonical rows should name a repository_path (TBD allowed while planned)
        if r.get("canonical_source") in ("git", "generated") and not r.get("repository_path"):
            rep.add(WARNING, "git_missing_repo_path", "docs/registers/pages.yml",
                    f"{pid}: git/generated row missing repository_path")
        # confluence-canonical rows should name a page id unless explicitly TBD
        if r.get("canonical_source") == "confluence" and not r.get("confluence_page_id"):
            rep.add(WARNING, "confluence_missing_id", "docs/registers/pages.yml",
                    f"{pid}: confluence row missing confluence_page_id (use TBD)")


def collect_files(changed_only):
    files = []
    for d in SCAN_DIRS:
        for base, _dirs, names in os.walk(d):
            for n in names:
                if n.endswith((".md", ".yml", ".yaml")):
                    files.append(os.path.join(base, n))
    for f in EXTRA_FILES:
        if os.path.exists(f):
            files.append(f)
    files = sorted(set(files))
    if not changed_only:
        return files, None
    base_ref = _resolve_base()
    if base_ref is None:
        return files, "base ref unavailable — scanning all documentation files"
    r = _run(["git", "diff", "--name-only", f"{base_ref}...HEAD"])
    r2 = _run(["git", "diff", "--name-only"])  # unstaged working-tree changes too
    changed = set((r.stdout + "\n" + r2.stdout).split())
    changed_abs = {os.path.join(ROOT, c) for c in changed}
    focus = [f for f in files if f in changed_abs]
    return focus, f"changed vs {base_ref} ({len(focus)} documentation file(s))"


def _resolve_base():
    for cand in (os.environ.get("GITHUB_BASE_REF"), "origin/main", "main"):
        if not cand:
            continue
        ref = f"origin/{cand}" if cand == os.environ.get("GITHUB_BASE_REF") and "/" not in cand else cand
        mb = _run(["git", "merge-base", ref, "HEAD"])
        if mb.returncode == 0 and mb.stdout.strip():
            return mb.stdout.strip()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Advisory documentation Definition-of-Done checker.")
    ap.add_argument("--changed", action="store_true",
                    help="focus file-level checks on files changed vs a Git base")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on ERRORS (local/testing; NOT blocking CI in 0.11.0)")
    args = ap.parse_args(argv)

    rep = Report()
    # repository-wide integrity (always, regardless of --changed)
    check_register(rep)
    check_crosswalk_current(rep)
    check_generated_warnings(rep)
    check_register_advisory(rep)
    # file-level checks
    files, scope_note = collect_files(args.changed)
    for f in files:
        scan_file(rep, f)

    print("=" * 72)
    print("Documentation Definition-of-Done — ADVISORY report (Release 0.11.0 · P4)")
    if scope_note:
        print(f"scope: {scope_note}")
    print("=" * 72)
    if not rep.findings:
        print("No findings. Documentation state is clean.")
    for sev in (ERROR, WARNING):
        group = [f for f in rep.findings if f[0] == sev]
        if group:
            print(f"\n{sev}S ({len(group)}):")
            for _s, rule, path, msg in group:
                print(f"  [{rule}] {path}: {msg}")
    print("\n" + "-" * 72)
    print(f"Summary: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s).")

    if args.strict:
        if rep.errors:
            print("STRICT mode: exiting non-zero due to ERRORS (local/testing only; not blocking CI).")
            return 1
        print("STRICT mode: no errors (warnings do not fail strict).")
        return 0
    print("ADVISORY mode: findings are advisory only — exit 0 (documentation does not block the PR).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
