"""MDM-2 — duplicate canonical-person consolidation ORCHESTRATION.

This is the orchestration layer over the MDM-1 merge engine. It NEVER reimplements or bypasses that
engine: it discovers duplicate groups, scores profiles to pick a clear survivor (or marks the group
ambiguous), and for safe, unambiguous groups it calls ``merge_people()`` exactly as built in PR #185.
Preview uses ``preview_person_merge()``. All safety (transaction, locks, conflict/legal blockers,
dedup, fill-null survivorship, history, event, audit) lives in the engine and is untouched here.

Group discovery is candidate-only: active people are grouped by a normalized name key. Nothing is merged
on the strength of a name — every proposed pair is gated by ``preview_person_merge`` (``safe_to_merge``)
and then by ``merge_people`` (which re-locks and re-checks blockers atomically).
"""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import bindparam, text

from app.db import engine
from app.services.person_merge import MergeBlocked, merge_people, preview_person_merge

# Automatic-survivor scoring signals — the richness rules. A survivor is auto-selected ONLY when exactly
# one member scores strictly highest; otherwise the group is AMBIGUOUS (never guess).
SCORE_SIGNALS = (
    "has_email", "has_phone", "has_address", "has_household",
    "source_links", "accounts", "engagements", "documents", "opportunities", "timeline_activity",
)
AUTOMATIC_SURVIVOR_RULE_COUNT = len(SCORE_SIGNALS)

_COUNT_TABLES = {
    "source_links": ("person_source_links", "person_id"),
    "accounts": ("accounts", "person_id"),
    "engagements": ("engagements", "person_id"),
    "documents": ("documents", "person_id"),
    "opportunities": ("opportunities", "person_id"),
    "timeline_activity": ("timeline_events", "person_id"),
}

_REPORT_COLUMNS = ("survivor_person_id", "merged_person_id", "group_name", "reason",
                   "safe_to_merge", "status", "blocker", "warning")


def _count(conn, table, col, pid):
    return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {col} = :p"), {"p": pid}).scalar_one()


def score_person(conn, pid) -> tuple[int, dict]:
    """Richness score for a person. Higher = keep. Profile presence is weighted; linked records add
    their counts. Returns (total, breakdown)."""
    row = conn.execute(text(
        "SELECT primary_email, primary_phone, address_line_1, household_id FROM people WHERE id = :p"),
        {"p": pid}).mappings().first()
    if row is None:
        return -1, {}
    b = {
        "has_email": 2 if row["primary_email"] else 0,
        "has_phone": 2 if row["primary_phone"] else 0,
        "has_address": 1 if row["address_line_1"] else 0,
        "has_household": 1 if row["household_id"] else 0,
    }
    for key, (table, col) in _COUNT_TABLES.items():
        b[key] = _count(conn, table, col, pid)
    return sum(b.values()), b


def find_duplicate_groups(conn, *, restrict_ids=None) -> list[dict]:
    """Active people grouped by a normalized name key; groups with ≥2 members are duplicate candidates.
    ``restrict_ids`` scopes discovery (used by tests so unrelated people are never touched)."""
    name_key = ("lower(btrim(coalesce(nullif(full_name, ''), "
                "concat_ws(' ', first_name, last_name))))")
    base = (f"SELECT {name_key} AS gkey, array_agg(id ORDER BY id) AS ids "
            f"FROM people WHERE active IS TRUE AND {name_key} <> ''")
    tail = " GROUP BY gkey HAVING count(*) > 1 ORDER BY gkey"
    if restrict_ids is not None:
        stmt = text(base + " AND id IN :ids" + tail).bindparams(bindparam("ids", expanding=True))
        rows = conn.execute(stmt, {"ids": list(restrict_ids) or [-1]}).mappings().all()
    else:
        rows = conn.execute(text(base + tail)).mappings().all()
    return [{"group_key": r["gkey"], "person_ids": list(r["ids"])} for r in rows]


def select_survivor(conn, person_ids) -> dict:
    """Pick the clearly-richest member. Returns {survivor_id, ambiguous, reason, scores}. Ambiguous when
    no member has a positive score or two or more members tie for the top — never guess."""
    scores = {pid: score_person(conn, pid)[0] for pid in person_ids}
    top = max(scores.values())
    winners = [pid for pid, s in scores.items() if s == top]
    if top <= 0:
        return {"survivor_id": None, "ambiguous": True,
                "reason": "no member has a distinguishing profile (all empty/equal)", "scores": scores}
    if len(winners) != 1:
        return {"survivor_id": None, "ambiguous": True,
                "reason": f"{len(winners)} members tie for richest (score {top})", "scores": scores}
    return {"survivor_id": winners[0], "ambiguous": False,
            "reason": f"clear survivor (score {top})", "scores": scores}


def _pair_rows(group_key, survivor_id, duplicate_id, report):
    """Flatten one preview report into a CSV row dict."""
    return {
        "survivor_person_id": survivor_id, "merged_person_id": duplicate_id, "group_name": group_key,
        "reason": report.get("reason", ""), "safe_to_merge": report.get("safe_to_merge"),
        "status": report.get("status", ""),
        "blocker": "; ".join(report.get("blockers", []) or []),
        "warning": "; ".join(report.get("warnings", []) or []),
    }


def _already_merged(conn, duplicate_id) -> bool:
    return conn.execute(text("SELECT 1 FROM person_merge_history WHERE merged_person_id = :d"),
                        {"d": duplicate_id}).first() is not None


def consolidate(*, apply: bool = False, actor_user_id: int | None = None, restrict_ids=None,
                report_path: str | None = None, progress=None) -> dict:
    """Consolidate duplicate people. ``apply=False`` (default) previews only — NO database changes.
    ``apply=True`` merges each safe, unambiguous group via ``merge_people()`` (resumable: already-merged
    or vanished duplicates are skipped). Returns a summary and, if ``report_path`` given, writes a CSV."""
    summary = {"apply": apply, "groups": 0, "merged": 0, "skipped": 0, "ambiguous": 0,
               "blocked": 0, "failed": 0, "rows": []}
    with engine.connect() as conn:
        groups = find_duplicate_groups(conn, restrict_ids=restrict_ids)
    summary["groups"] = len(groups)

    for group in groups:
        gkey, ids = group["group_key"], group["person_ids"]
        with engine.connect() as conn:
            sel = select_survivor(conn, ids)
        if sel["ambiguous"]:
            summary["ambiguous"] += 1
            for dup in ids:
                summary["rows"].append({
                    "survivor_person_id": "", "merged_person_id": dup, "group_name": gkey,
                    "reason": sel["reason"], "safe_to_merge": "", "status": "ambiguous",
                    "blocker": "", "warning": ""})
            if progress:
                progress(f"AMBIGUOUS {gkey}: {sel['reason']}")
            continue

        survivor = sel["survivor_id"]
        for dup in [i for i in ids if i != survivor]:
            with engine.connect() as conn:
                if _already_merged(conn, dup):          # resume: never repeat a completed merge
                    summary["skipped"] += 1
                    continue
            report = preview_person_merge(survivor, dup)
            row = _pair_rows(gkey, survivor, dup, report)
            if not report["safe_to_merge"]:
                row["status"] = "blocked"
                summary["blocked"] += 1
                summary["rows"].append(row)
                if progress:
                    progress(f"BLOCKED {survivor}<-{dup}: {row['blocker']}")
                continue
            if not apply:
                row["status"] = "would_merge"
                summary["skipped"] += 1
                summary["rows"].append(row)
                continue
            try:
                merge_people(survivor, dup, reason=f"MDM-2 consolidation ({gkey})",
                             actor_user_id=actor_user_id)
                row["status"] = "merged"
                summary["merged"] += 1
            except MergeBlocked as exc:
                row["status"], row["blocker"] = "blocked", str(exc)
                summary["blocked"] += 1
            except Exception as exc:      # noqa: BLE001 — record & continue; the engine rolled back
                row["status"], row["blocker"] = "failed", str(exc)
                summary["failed"] += 1
            summary["rows"].append(row)
            if progress:
                progress(f"{row['status'].upper()} {survivor}<-{dup}")

    if report_path:
        _write_report(report_path, summary["rows"])
        summary["report_path"] = report_path
    return summary


def _write_report(path: str, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_REPORT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _REPORT_COLUMNS})
