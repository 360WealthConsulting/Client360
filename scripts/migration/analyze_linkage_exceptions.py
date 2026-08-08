"""READ-ONLY analysis of a linkage-remediation preview's exceptions.csv.

Classifies the ambiguous/unmatched folders from a Linkage Remediation preview to explain WHY they did not
resolve and to project which resolver improvements would recover them — WITHOUT changing anything. It uses
the repository's real name-normalization (``_name_key`` / ``_folder_person_keys`` / ``_JOINT_SPLIT_RE``)
so its verdicts match the live resolver exactly.

READ-ONLY: it issues SELECTs only (and sets the session to read-only as defense in depth). It never
writes a row, never creates an entity, never touches documents / storage_uri / document_sources, and it
does NOT run remediation apply.

Usage::
    python -m scripts.migration.analyze_linkage_exceptions <preview-directory>

where <preview-directory> contains the preview's ``exceptions.csv`` (e.g.
``D:\\Client360\\Reports\\reports\\linkage_remediation\\preview\\<timestamp>``).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

from app.importers.taxdome_drive import _JOINT_SPLIT_RE, _folder_person_keys, _name_key

_DIGIT_RE = re.compile(r"\d")

#: The pattern buckets, in report order.
PATTERNS = (
    "would_match_household_name",
    "would_match_in_people",
    "would_match_org_registry",
    "joint_partial_member_match",
    "family_household_style_no_match",
    "joint_no_member_match",
    "has_digits_or_label",
    "genuinely_absent",
)


def build_indexes(people, household_names, org_names):
    """Build normalized-name indexes. ``people`` is an iterable of (full_name, contact_type);
    ``household_names`` / ``org_names`` are iterables of names. Pure — no I/O."""
    person_keys: dict[str, list[str]] = defaultdict(list)
    for full_name, contact_type in people:
        key = _name_key(full_name)
        if key:
            person_keys[key].append(contact_type or "(null)")
    hh_keys: dict[str, int] = defaultdict(int)
    for name in household_names:
        key = _name_key(name)
        if key:
            hh_keys[key] += 1
    org_keys: dict[str, int] = defaultdict(int)
    for name in org_names:
        key = _name_key(name)
        if key:
            org_keys[key] += 1
    return person_keys, hh_keys, org_keys


def classify_all(folders, person_keys, hh_keys, org_keys):
    """Classify each folder into pattern buckets. Returns (Counter, {tag: [examples]}). Pure — no I/O.

    A folder may match more than one directory (each ``would_match_*`` is counted); the fallback buckets
    apply only when no direct directory match was found."""
    pat: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    def add_ex(tag, text):
        if len(examples[tag]) < 10:
            examples[tag].append(text)

    for folder in folders:
        fk = _name_key(folder)
        member_keys = _folder_person_keys(folder)
        low = (folder or "").lower()
        joint = bool(_JOINT_SPLIT_RE.search(folder or ""))
        family = ("family" in low) or ("household" in low)
        has_digits = bool(_DIGIT_RE.search(folder or ""))
        hit = False
        if fk and fk in hh_keys:
            pat["would_match_household_name"] += 1
            add_ex("would_match_household_name", folder)
            hit = True
        if fk and fk in person_keys:
            pat["would_match_in_people"] += 1
            add_ex("would_match_in_people", f"{folder}  [contact_types={sorted(set(person_keys[fk]))}]")
            hit = True
        if fk and fk in org_keys:
            pat["would_match_org_registry"] += 1
            add_ex("would_match_org_registry", folder)
            hit = True
        if not hit and joint and any(k in person_keys for k in member_keys):
            pat["joint_partial_member_match"] += 1
            add_ex("joint_partial_member_match", folder)
            hit = True
        if not hit:
            if family:
                pat["family_household_style_no_match"] += 1
                add_ex("family_household_style_no_match", folder)
            elif joint:
                pat["joint_no_member_match"] += 1
                add_ex("joint_no_member_match", folder)
            elif has_digits:
                pat["has_digits_or_label"] += 1
                add_ex("has_digits_or_label", folder)
            else:
                pat["genuinely_absent"] += 1
                add_ex("genuinely_absent", folder)
    return pat, examples


def read_exception_folders(preview_dir):
    """Read the ``source_folder`` column from ``<preview_dir>/exceptions.csv``. Read-only file access."""
    path = os.path.join(preview_dir, "exceptions.csv")
    with open(path, encoding="utf-8", newline="") as f:
        return [(row.get("source_folder") or "") for row in csv.DictReader(f)]


def load_canonical(engine):
    """SELECT the canonical directories (read-only session). Returns (people, household_names, org_names)."""
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))     # defense in depth: block any write
        people = [(r[0], r[1]) for r in conn.execute(text(
            "SELECT full_name, contact_type FROM people")).all()]
        household_names = [r[0] for r in conn.execute(text("SELECT name FROM households")).all()]
        org_names = [r[0] for r in conn.execute(text(
            "SELECT name FROM relationship_entities "
            "WHERE person_id IS NULL AND household_id IS NULL AND active IS TRUE")).all()]
    return people, household_names, org_names


def analyze(preview_dir, engine=None):
    """Run the full read-only analysis and return a structured result (used by the CLI and tests)."""
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    people, household_names, org_names = load_canonical(engine)
    person_keys, hh_keys, org_keys = build_indexes(people, household_names, org_names)
    folders = read_exception_folders(preview_dir)
    pat, examples = classify_all(folders, person_keys, hh_keys, org_keys)
    return {
        "contact_type_distribution": Counter((ct or "(null)") for _, ct in people),
        "index_sizes": {"people": len(person_keys), "households": len(hh_keys),
                        "standalone_orgs": len(org_keys)},
        "folders_analyzed": len(folders),
        "patterns": pat,
        "examples": examples,
    }


def _print_report(result) -> None:
    print("=== people.contact_type distribution ===")
    for ct, n in result["contact_type_distribution"].most_common():
        print(f"  {ct}: {n}")
    s = result["index_sizes"]
    print(f"\n=== index sizes: people={s['people']} households={s['households']} "
          f"standalone_orgs={s['standalone_orgs']} ===")
    print(f"\n=== {result['folders_analyzed']} ambiguous/unmatched folders classified ===")
    for tag in PATTERNS:
        if result["patterns"].get(tag):
            print(f"  {tag}: {result['patterns'][tag]}")
    print("\n=== examples ===")
    for tag in PATTERNS:
        if result["examples"].get(tag):
            print(f"[{tag}]")
            for ex in result["examples"][tag]:
                print(f"   {ex}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.analyze_linkage_exceptions",
                                description="Read-only analysis of a linkage-remediation preview's exceptions.csv.")
    p.add_argument("preview_dir", help="Preview directory containing exceptions.csv")
    args = p.parse_args(argv)
    if not os.path.isfile(os.path.join(args.preview_dir, "exceptions.csv")):
        print(f"ERROR: no exceptions.csv in {args.preview_dir}")
        return 2
    _print_report(analyze(args.preview_dir))
    print("\nAnalysis complete (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
