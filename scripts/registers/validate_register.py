#!/usr/bin/env python3
"""Validator for the canonical Publication Register (docs/registers/pages.yml).

Release 0.11.0 · P3. Enforces schema, controlled vocabularies, unique identity, the AD-5 invariant,
D10 taxonomy completeness, framework/area coverage, known-Confluence-ID fidelity, governance-row
presence, legacy non-canonicity, and crosswalk currency. Non-zero exit on any failure.
"""
from __future__ import annotations

import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "docs", "registers", "pages.yml")

STATUS = {"planned", "draft", "published", "needs_review"}
CANON = {"git", "confluence", "generated", "legacy_unresolved"}
PROFILES = {"hybrid", "infrastructure", "operations", "library"}
NODES = {"00", "01", "10", "20", "30", "40", "80", "90"}
FRAMEWORK_AREAS = {
    "CLM360", "TAXOPS", "WLTH", "INS", "BEN", "RET", "CRM", "WORK", "DOC", "RPT", "AIA",
    "M365", "AD", "NET", "SRV", "SEC", "DR", "CMP", "VEND", "OFFICE", "HR", "ACCT", "MKT",
    "SOPLIB", "TRAIN", "RELMGMT",
}
PSEUDO_AREAS = {"SHARED", "GOV"}  # only the 26 framework codes + SHARED + GOV are valid (no MANUAL)
VALID_AREAS = FRAMEWORK_AREAS | PSEUDO_AREAS
DOCTYPES = {
    "EXEC", "PURPOSE", "ARCH", "DATA", "USERGUIDE", "ADMINGUIDE", "SOP", "RULES", "SEC", "WF",
    "EXC", "INTEG", "REPORT", "TROUBLE", "FAQ", "TRAIN", "RELNOTES", "CHANGELOG", "RELATED",
    "POLICY", "RACI", "CHECKLIST", "PROCESS", "RUNBOOK", "BCDR", "ASSET", "VENDOR", "INCIDENT",
    "CONTROLS", "CALENDAR", "GLOSSARY", "KPI", "NODE", "TEMPLATE", "README", "META", "REGISTER",
    "LEGACY",
}
REQUIRED_FIELDS = ["page_id", "title", "area", "node", "profile", "doc_type", "canonical_source",
                   "status"]
ALL_FIELDS = REQUIRED_FIELDS + ["repository_path", "confluence_page_id", "confluence_parent_id",
                                "owner", "reviewer", "last_reviewed", "review_cycle", "next_review",
                                "compliance_gate", "legacy_identifier", "legacy_source",
                                "reconciliation_status", "notes"]
LETTERS = set("ABCDEFGHIJKLMN")

# known verified Confluence IDs -> expected status (P1 skeleton, 0.10.0 pages, benefits, legacy)
KNOWN_IDS = {
    "28966913": "published", "28835861": "published", "28999681": "published",
    "29032449": "published", "29032469": "published", "28868631": "published",
    "28835881": "published", "28868651": "published",  # 8 nodes
    "28966933": "published", "28999701": "published", "28835901": "published",  # 3 templates
    "28770305": "published", "28803073": "published", "28835841": "published",
    "28868609": "published", "28901377": "published", "28901397": "published",  # 6 INS published
    "27951106": "draft", "27983873": "draft", "27918338": "draft",  # 3 benefits drafts
}
GOVERNANCE_PATHS = {
    "governance/README.md", "governance/CONTRIBUTING.md", "governance/policies/README.md",
    "governance/runbooks/README.md", "governance/dr/README.md", "governance/controls/README.md",
    "governance/inventory/README.md", "governance/calendar/README.md",
}


def main():
    errs = []
    with open(SRC, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    pages = doc["pages"]

    seen_pid, seen_cid, seen_repo = {}, {}, {}
    coverage_types = {}  # area -> list of doc_types for generic coverage rows
    for r in pages:
        pid = r.get("page_id")
        # schema: every field key present
        for f in ALL_FIELDS:
            if f not in r:
                errs.append(f"{pid}: missing schema field '{f}'")
        # required non-null
        for f in REQUIRED_FIELDS:
            if r.get(f) in (None, ""):
                errs.append(f"{pid}: required field '{f}' is empty")
        # enums
        if r.get("status") not in STATUS:
            errs.append(f"{pid}: invalid status '{r.get('status')}'")
        if r.get("canonical_source") not in CANON:
            errs.append(f"{pid}: invalid canonical_source '{r.get('canonical_source')}'")
        if r.get("profile") not in PROFILES:
            errs.append(f"{pid}: invalid profile '{r.get('profile')}'")
        if r.get("node") not in NODES:
            errs.append(f"{pid}: invalid node '{r.get('node')}'")
        if r.get("area") not in VALID_AREAS:
            errs.append(f"{pid}: invalid area '{r.get('area')}'")
        if r.get("doc_type") not in DOCTYPES:
            errs.append(f"{pid}: invalid doc_type '{r.get('doc_type')}'")
        # unique page_id
        if pid in seen_pid:
            errs.append(f"duplicate page_id '{pid}'")
        seen_pid[pid] = True
        # unique canonical identity: non-null real confluence_page_id
        cid = r.get("confluence_page_id")
        if cid and cid != "TBD":
            if cid in seen_cid:
                errs.append(f"duplicate confluence_page_id '{cid}' ({pid} & {seen_cid[cid]})")
            seen_cid[cid] = pid
        # unique canonical identity: real repository_path (ignore TBD/planned placeholders)
        rp = r.get("repository_path")
        if rp and rp != "TBD" and "(planned)" not in rp and r["canonical_source"] in ("git", "generated"):
            if rp in seen_repo:
                errs.append(f"duplicate repository_path '{rp}' ({pid} & {seen_repo[rp]})")
            seen_repo[rp] = pid
        # AD-5 invariant
        if r.get("compliance_gate") not in ("none", None):
            if r.get("status") == "published":
                errs.append(f"{pid}: AD-5 invariant violated (compliance_gate set but status=published)")
        # legacy rows never canonical, never published-by-inference
        if r.get("legacy_source") == "360os_atlas":
            if r.get("canonical_source") != "legacy_unresolved":
                errs.append(f"{pid}: legacy page not marked legacy_unresolved")
            if r.get("reconciliation_status") != "manual_review":
                errs.append(f"{pid}: legacy page reconciliation_status must be manual_review")
            if r.get("status") == "published":
                errs.append(f"{pid}: legacy page must not be status=published")
        # collect generic coverage doc_types (page_id == AREA-TYPE exactly)
        if pid == f"{r['area']}-{r['doc_type']}":
            coverage_types.setdefault(r["area"], []).append(r["doc_type"])

    # no duplicate profile-union coverage rows
    for area, types in coverage_types.items():
        if len(types) != len(set(types)):
            dup = [t for t in set(types) if types.count(t) > 1]
            errs.append(f"area {area}: duplicate profile-union coverage rows for {dup}")

    # COMPLETE required profile/document-type coverage (full 01 §2 sets)
    _CORE = {"EXEC", "PURPOSE", "RELATED", "CHANGELOG"}
    _SW = _CORE | {"ARCH", "DATA", "USERGUIDE", "ADMINGUIDE", "SOP", "RULES", "SEC", "WF", "EXC",
                   "INTEG", "REPORT", "TROUBLE", "FAQ", "TRAIN", "RELNOTES"}
    _INFRA = _CORE | {"ARCH", "ASSET", "RUNBOOK", "BCDR", "INCIDENT", "ADMINGUIDE", "SEC", "INTEG",
                      "VENDOR", "KPI"}
    _BIZ = _CORE | {"POLICY", "RACI", "SOP", "CHECKLIST", "PROCESS", "CONTROLS", "CALENDAR",
                    "VENDOR", "TRAIN", "KPI"}
    _HYBRID = _SW | _BIZ
    _LIB = {"EXEC", "PURPOSE", "PROCESS", "RELATED"}
    PROFILE_FULL = {"hybrid": _HYBRID, "infrastructure": _INFRA, "operations": _BIZ, "library": _LIB}
    AREA_PROFILE = {}
    for a in ["CLM360", "TAXOPS", "WLTH", "INS", "BEN", "RET", "CRM", "WORK", "DOC", "RPT", "AIA"]:
        AREA_PROFILE[a] = "hybrid"
    for a in ["M365", "AD", "NET", "SRV", "SEC", "DR"]:
        AREA_PROFILE[a] = "infrastructure"
    for a in ["CMP", "VEND", "OFFICE", "HR", "ACCT", "MKT"]:
        AREA_PROFILE[a] = "operations"
    for a in ["SOPLIB", "TRAIN", "RELMGMT"]:
        AREA_PROFILE[a] = "library"
    for area, prof in AREA_PROFILE.items():
        have = set(coverage_types.get(area, []))
        want = PROFILE_FULL[prof]
        missing = want - have
        if missing:
            errs.append(f"area {area} ({prof}): missing required doc types {sorted(missing)}")

    # every legacy letter A-N mapped in taxonomy_migration
    mapped = {m["letter"] for m in doc.get("taxonomy_migration_d10", [])}
    for lt in sorted(LETTERS - mapped):
        errs.append(f"D10: crosswalk letter '{lt}' not mapped")
    # and preserved as legacy_identifier on some framework row
    preserved = {r.get("legacy_identifier") for r in pages
                 if r.get("legacy_source") == "crosswalk_section_letter"}
    for lt in sorted(LETTERS - preserved):
        errs.append(f"D10: letter '{lt}' not preserved as legacy_identifier on any area row")

    # every framework area represented + SHARED + GOV
    present_areas = {r["area"] for r in pages}
    for a in sorted((FRAMEWORK_AREAS | {"SHARED", "GOV"}) - present_areas):
        errs.append(f"framework area '{a}' not represented")

    # known Confluence IDs represented exactly as intended
    for cid, want in KNOWN_IDS.items():
        got = [r for r in pages if r.get("confluence_page_id") == cid]
        if not got:
            errs.append(f"known Confluence id {cid} missing from register")
        elif len(got) > 1:
            errs.append(f"known Confluence id {cid} appears {len(got)} times")
        elif got[0]["status"] != want:
            errs.append(f"known Confluence id {cid}: status '{got[0]['status']}' != expected '{want}'")

    # every governance artifact represented
    reg_paths = {r.get("repository_path") for r in pages}
    for g in sorted(GOVERNANCE_PATHS - reg_paths):
        errs.append(f"governance artifact '{g}' not represented in register")

    # 23 legacy pages present and non-canonical
    legacy_rows = [r for r in pages if r.get("legacy_source") == "360os_atlas"]
    if len(legacy_rows) != 23:
        errs.append(f"expected 23 legacy Atlas rows, found {len(legacy_rows)}")

    # crosswalk currency
    gen = os.path.join(ROOT, "scripts", "registers", "gen_crosswalk.py")
    rc = subprocess.run([sys.executable, gen, "--check"], capture_output=True, text=True)
    if rc.returncode != 0:
        errs.append("crosswalk not current: " + (rc.stderr.strip() or rc.stdout.strip()))

    if errs:
        print(f"REGISTER VALIDATION FAILED — {len(errs)} error(s):", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
    print(f"register OK: {len(pages)} rows, {len(legacy_rows)} legacy (manual_review), "
          f"AD-5 invariant holds, crosswalk current")


if __name__ == "__main__":
    main()
