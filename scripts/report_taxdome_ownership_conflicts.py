"""READ-ONLY report: where the TaxDome folder mapping and the stored owner disagree.

WHY THIS EXISTS
---------------
The TaxDome account/folder mapping is AUTHORITATIVE in the continuous pipeline
(:mod:`app.services.document_pipeline_continuous.ownership`): a folder that resolves to exactly one
client links the document, and content evidence never overrides it. The one thing that mapping may
never do is overwrite a DIFFERENT owner that is already recorded — that disagreement is queued as an
``ownership_conflict`` review instead.

Before the pipeline is ever switched on, somebody has to know how many of those there are and why.
This produces that list, grouped by cause, without writing anything.

READ-ONLY BY CONSTRUCTION
-------------------------
The connection is opened through :func:`scripts.plan_document_ownership.read_only_engine` and
asserted read-only before a single row is read, the same guard the ownership planner uses. No
statement here is a mutation; ``tests/test_taxdome_conflict_report.py`` greps this file for write
verbs so that stays true.

THE NAME RULE IS IMPORTED, NOT REIMPLEMENTED
--------------------------------------------
``taxdome_drive.resolve_folder`` re-reads every ``people`` row on each call, which is fine for one
document and hopeless for twenty-two thousand. This builds the same index once and applies the same
rules — ``_folder_person_keys`` and ``_name_key`` are imported from that module rather than copied,
and :func:`_resolve_with_index` is checked against ``resolve_folder`` itself on a sample at the end
of every run, so the fast path cannot drift from the authoritative one.

CAUSES
------
``conflict_person`` / ``conflict_household``
    The folder resolves to one client and the document already names a DIFFERENT one. The pipeline
    would queue these for review; none would be overwritten.
``folder_unresolved``
    The folder matched no canonical person, matched several, or matched people who share no single
    household. Nothing to conflict with yet — the folder itself needs a human.
``missing_folder_tag``
    TaxDome-sourced but carrying no ``taxdome_folder`` tag, so the lane has lost the thing that makes
    it authoritative and falls back to evidence.
``agrees``
    Folder and stored owner name the same client. Reported so the totals reconcile.
``would_link``
    Unowned, folder resolves. The pipeline would link it. Not a conflict.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402

from scripts.plan_document_ownership import (  # noqa: E402
    _database_url,
    assert_read_only,
    read_only_engine,
)

#: Causes, in the order a reader wants them: real conflicts first.
CAUSE_ORDER = ("conflict_person", "conflict_household", "folder_unresolved",
               "missing_folder_tag", "agrees", "would_link")


def _live_clause(documents):
    """The shared four-condition predicate, imported so this report counts what discovery counts."""
    from app.services.document_platform.lifecycle import live_document_clause

    assert documents is not None
    return live_document_clause()


def _people_index(conn):
    """``name key -> [(person_id, household_id)]`` built once, from the same key function."""
    from app.db import people
    from app.importers.taxdome_drive import _name_key

    index: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
    for row in conn.execute(sa.select(people.c.id, people.c.full_name,
                                      people.c.household_id)).mappings():
        index[_name_key(row["full_name"])].append((row["id"], row["household_id"]))
    return index


def _resolve_with_index(folder_name: str, index) -> tuple[int | None, int | None]:
    """``taxdome_drive.resolve_folder``'s rules, against a prebuilt index. Same answers, one scan."""
    from app.importers.taxdome_drive import _folder_person_keys

    keys = _folder_person_keys(folder_name)
    if not keys:
        return (None, None)
    matched: list[int] = []
    households: set[int] = set()
    for key in keys:
        candidates = index.get(key) or []
        if len(candidates) == 1:                       # unique match for this name only
            pid, hh = candidates[0]
            matched.append(pid)
            if hh is not None:
                households.add(hh)
    unique_people = set(matched)
    if not unique_people:
        return (None, None)
    if len(keys) == 1 and len(unique_people) == 1:
        return (None, matched[0])
    if len(households) == 1:
        return (households.pop(), None)
    if len(unique_people) == 1:
        return (None, matched[0])
    return (None, None)


def _classify(row, index) -> tuple[str, dict]:
    """One document's cause, plus the detail a reviewer needs to act on it."""
    tags = row["tags"] or {}
    folder = tags.get("taxdome_folder")
    stored = {"person_id": row["person_id"], "household_id": row["household_id"],
              "organization_id": row["organization_id"]}
    owned = any(v is not None for v in stored.values())

    if not folder:
        return "missing_folder_tag", {"folder": None, "proposed": None, "stored": stored}

    household_id, person_id = _resolve_with_index(folder, index)
    if household_id is None and person_id is None:
        return "folder_unresolved", {"folder": folder, "proposed": None, "stored": stored}

    proposed = ({"entity_type": "household", "entity_id": household_id} if household_id is not None
                else {"entity_type": "person", "entity_id": person_id})
    if not owned:
        return "would_link", {"folder": folder, "proposed": proposed, "stored": stored}

    # Owned already. Agreement is the common case; disagreement is the report's subject.
    if proposed["entity_type"] == "household":
        if row["household_id"] == proposed["entity_id"]:
            return "agrees", {"folder": folder, "proposed": proposed, "stored": stored}
        return "conflict_household", {"folder": folder, "proposed": proposed, "stored": stored}
    if row["person_id"] == proposed["entity_id"]:
        return "agrees", {"folder": folder, "proposed": proposed, "stored": stored}
    return "conflict_person", {"folder": folder, "proposed": proposed, "stored": stored}


def _verify_fast_path(conn, sampled, index) -> int:
    """Prove the index agrees with ``resolve_folder`` itself, so the shortcut cannot drift."""
    from app.importers.taxdome_drive import resolve_folder

    for folder in sampled:
        if _resolve_with_index(folder, index) != resolve_folder(conn, folder):
            raise SystemExit(f"fast path disagrees with resolve_folder for folder {folder!r}")
    return len(sampled)


def run(*, out_dir: Path, limit: int | None = None, database_url: str | None = None,
        sample: int = 25) -> dict:
    from app.db import documents

    engine = read_only_engine(_database_url(database_url))
    started = datetime.now(UTC)
    counts: Counter = Counter()
    rows_out: list[dict] = []
    folders_seen: list[str] = []

    with engine.connect() as conn:
        assert_read_only(conn)
        index = _people_index(conn)
        query = (sa.select(documents.c.id, documents.c.tags, documents.c.person_id,
                           documents.c.household_id, documents.c.organization_id,
                           documents.c.original_name)
                 # Three ways a document can be TaxDome-sourced, and the storage path is the one
                 # that still holds when the tags are the thing that went missing. Without it the
                 # report's own ``missing_folder_tag`` cause could not see the single production
                 # document that has neither tag — the exact row it exists to surface.
                 .where(_live_clause(documents),
                        sa.or_(documents.c.tags.has_key("taxdome_folder"),
                               documents.c.tags["source_system"].astext == "TaxDome",
                               documents.c.storage_uri.ilike("%taxdome%")))
                 .order_by(documents.c.id))
        for row in conn.execute(query).mappings():
            cause, detail = _classify(row, index)
            counts[cause] += 1
            if detail["folder"] and len(folders_seen) < sample:
                folders_seen.append(detail["folder"])
            if cause.startswith("conflict") or cause in ("folder_unresolved", "missing_folder_tag"):
                    rows_out.append({"document_id": row["id"], "cause": cause,
                                     "taxdome_folder": detail["folder"],
                                     "proposed_entity_type": (detail["proposed"] or {}).get("entity_type"),
                                     "proposed_entity_id": (detail["proposed"] or {}).get("entity_id"),
                                     "stored_person_id": detail["stored"]["person_id"],
                                     "stored_household_id": detail["stored"]["household_id"],
                                     "stored_organization_id": detail["stored"]["organization_id"]})
        verified = _verify_fast_path(conn, folders_seen, index)

    # Order by CAUSE, then id, BEFORE truncating. Taken in id order the first 800 rows were 747
    # unresolved folders and not one of the 340 household conflicts — the report would have hidden
    # exactly the rows it exists to surface. Conflicts lead; ``reportable_total`` says what was cut.
    reportable_total = len(rows_out)
    rows_out.sort(key=lambda r: (CAUSE_ORDER.index(r["cause"]), r["document_id"]))
    if limit is not None:
        rows_out = rows_out[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "taxdome_ownership_conflicts.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows_out[0].keys()) if rows_out else
                                ["document_id", "cause", "taxdome_folder",
                                 "proposed_entity_type", "proposed_entity_id",
                                 "stored_person_id", "stored_household_id",
                                 "stored_organization_id"])
        writer.writeheader()
        writer.writerows(rows_out)

    summary = {
        "generated_at": started.isoformat(),
        "read_only": True,
        "taxdome_documents_examined": sum(counts.values()),
        "by_cause": {cause: counts.get(cause, 0) for cause in CAUSE_ORDER},
        "items_written": len(rows_out),
        "items_reportable": reportable_total,
        "items_truncated": max(0, reportable_total - len(rows_out)),
        "item_limit": limit,
        "item_order": "cause (conflicts first), then document id",
        "resolve_folder_agreement_sample": verified,
        "csv": str(csv_path),
    }
    (out_dir / "taxdome_ownership_conflicts.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print("=" * 78)
    print(f"TAXDOME OWNERSHIP CONFLICTS — {summary['taxdome_documents_examined']:,} live "
          "TaxDome documents, read-only")
    print("=" * 78)
    for cause in CAUSE_ORDER:
        print(f"  {cause:<22}{counts.get(cause, 0):>10,}")
    print(f"  {'items written':<22}{len(rows_out):>10,}  of {reportable_total:,} reportable  -> {csv_path}")
    print(f"  fast-path agreement verified on {verified} folders against resolve_folder")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/taxdome_conflicts"))
    # No default cap. A report that silently stops at 800 rows looks complete and is not, and the
    # number 800 was itself a miscount — see conflicts_with_stored_owner. The flag remains for a
    # deliberate spot-check; omitting it writes every row.
    parser.add_argument("--limit", type=int, default=None,
                        help="optional cap on item rows written (default: no cap — write them all)")
    parser.add_argument("--sample", type=int, default=25,
                        help="folders re-checked against resolve_folder to prove the fast path")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    run(out_dir=args.out_dir, limit=args.limit, database_url=args.database_url, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
