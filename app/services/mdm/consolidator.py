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

# Distinct identity-evidence categories scored to pick a survivor. Canonical people rows are sparse, so
# evidence is gathered from each person's linked source_contacts (columns + raw_data) as well — we score
# DISTINCT usable identity evidence (unique emails/phones/etc.), never the raw count of populated fields.
SCORE_SIGNALS = (
    "emails", "phones", "addresses", "dob", "household",
    "accounts", "engagements", "documents", "opportunities", "timeline",
)
AUTOMATIC_SURVIVOR_RULE_COUNT = len(SCORE_SIGNALS)

_COUNT_TABLES = {
    "source_links": ("person_source_links", "person_id"),
    "accounts": ("accounts", "person_id"),
    "engagements": ("engagements", "person_id"),
    "documents": ("documents", "person_id"),
    "opportunities": ("opportunities", "person_id"),
    "timeline": ("timeline_events", "person_id"),
}

_REPORT_COLUMNS = ("survivor_person_id", "merged_person_id", "group_name", "reason",
                   "safe_to_merge", "status", "blocker", "warning",
                   "survivor_score", "survivor_evidence", "duplicate_evidence",
                   "conflicting_identifiers", "selection_reason")

# raw_data keys (case-insensitive) that carry identity evidence in Wealthbox/other exports.
_EMAIL_KEYS = {"primary_email", "personal_email", "home_email", "work_email", "email", "email_address"}
_PHONE_KEYS = {"primary_phone", "mobile_phone", "home_phone", "work_phone", "phone", "cell_phone",
               "mobile", "telephone"}
_ADDR_KEYS = {"mailing_address", "home_address", "work_address", "address", "street", "street_address",
              "address_line_1", "postal_code", "zip", "city"}
_DOB_KEYS = {"birth_date", "dob", "date_of_birth", "birthdate"}
_META_KEYS = {"contact_type", "contact_source", "type", "source", "status"}


def _count(conn, table, col, pid):
    return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {col} = :p"), {"p": pid}).scalar_one()


def _norm_email(v):
    s = str(v or "").strip().lower()
    return s if "@" in s and "." in s.split("@")[-1] else None


def _norm_phone(v):
    import re
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) >= 11 and d.startswith("1"):
        d = d[1:]
    return d[-10:] if len(d) >= 10 else (d if len(d) >= 7 else None)


def _norm_addr(parts):
    joined = " ".join(str(p).strip() for p in parts if p not in (None, ""))
    joined = " ".join(joined.lower().split())
    return joined if len(joined) >= 6 else None      # ignore trivial fragments


def gather_identity(conn, pid) -> dict:
    """Collect DISTINCT identity evidence for a person from the canonical row AND every linked
    source_contact (columns + raw_data). Returns sets of normalized emails/phones/addresses/dobs +
    contact metadata + linked-record counts + household. Empty-shell people yield empty sets."""
    ev = {"emails": set(), "phones": set(), "addresses": set(), "dob": set(), "meta": set(),
          "household": None, "counts": {}}
    p = conn.execute(text(
        "SELECT primary_email, normalized_email, primary_phone, normalized_phone, address_line_1, "
        "address_line_2, city, state, postal_code, birth_date, contact_type, household_id "
        "FROM people WHERE id = :p"), {"p": pid}).mappings().first()
    if p is None:
        return ev
    ev["household"] = p["household_id"]
    for e in (p["primary_email"], p["normalized_email"]):
        if _norm_email(e):
            ev["emails"].add(_norm_email(e))
    for ph in (p["primary_phone"], p["normalized_phone"]):
        if _norm_phone(ph):
            ev["phones"].add(_norm_phone(ph))
    a = _norm_addr([p["address_line_1"], p["address_line_2"], p["city"], p["state"], p["postal_code"]])
    if a:
        ev["addresses"].add(a)
    if p["birth_date"]:
        ev["dob"].add(str(p["birth_date"]))
    if p["contact_type"]:
        ev["meta"].add(f"contact_type={str(p['contact_type']).strip().lower()}")

    # Linked source contacts — the real identity signal for sparse canonical rows.
    for sc in conn.execute(text(
        "SELECT sc.email, sc.normalized_email, sc.phone, sc.normalized_phone, sc.address_line_1, "
        "sc.address_line_2, sc.city, sc.state, sc.postal_code, sc.raw_data "
        "FROM source_contacts sc JOIN person_source_links l ON l.source_contact_id = sc.id "
        "WHERE l.person_id = :p"), {"p": pid}).mappings():
        for e in (sc["email"], sc["normalized_email"]):
            if _norm_email(e):
                ev["emails"].add(_norm_email(e))
        for ph in (sc["phone"], sc["normalized_phone"]):
            if _norm_phone(ph):
                ev["phones"].add(_norm_phone(ph))
        a = _norm_addr([sc["address_line_1"], sc["address_line_2"], sc["city"], sc["state"],
                        sc["postal_code"]])
        if a:
            ev["addresses"].add(a)
        raw = sc["raw_data"] if isinstance(sc["raw_data"], dict) else {}
        raw_addr = []
        for k, v in raw.items():
            kl = str(k).strip().lower()
            if v in (None, ""):
                continue
            if kl in _EMAIL_KEYS and _norm_email(v):
                ev["emails"].add(_norm_email(v))
            elif kl in _PHONE_KEYS and _norm_phone(v):
                ev["phones"].add(_norm_phone(v))
            elif kl in _DOB_KEYS:
                ev["dob"].add(str(v).strip())
            elif kl in _META_KEYS:
                ev["meta"].add(f"{kl}={str(v).strip().lower()}")
            elif kl in _ADDR_KEYS:
                raw_addr.append(v)
        ra = _norm_addr(raw_addr)
        if ra:
            ev["addresses"].add(ra)

    for key, (table, col) in _COUNT_TABLES.items():
        ev["counts"][key] = _count(conn, table, col, pid)
    return ev


def _score(ev: dict) -> int:
    """Weighted DISTINCT-evidence score. Emails/phones/DOB dominate (strong identity); linked records
    and metadata add smaller amounts."""
    c = ev["counts"]
    return (5 * len(ev["emails"]) + 5 * len(ev["phones"]) + 3 * len(ev["dob"])
            + 2 * len(ev["addresses"]) + (2 if ev["household"] else 0)
            + 3 * c.get("accounts", 0) + 3 * c.get("engagements", 0)
            + c.get("documents", 0) + c.get("opportunities", 0) + c.get("timeline", 0)
            + (1 if ev["meta"] else 0))


def _evidence_summary(ev: dict) -> str:
    c = ev["counts"]
    return (f"emails={len(ev['emails'])} phones={len(ev['phones'])} addr={len(ev['addresses'])} "
            f"dob={len(ev['dob'])} household={'y' if ev['household'] else 'n'} "
            f"accts={c.get('accounts', 0)} engs={c.get('engagements', 0)} "
            f"docs={c.get('documents', 0)} opps={c.get('opportunities', 0)} "
            f"tl={c.get('timeline', 0)} links={c.get('source_links', 0)}")


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


def _conflicts(evs: dict) -> list[str]:
    """Group-level conflicting identifiers across members. Two members with DIFFERENT emails/phones/
    DOB/households — or two members that each independently own accounts/engagements — are (likely)
    distinct real people, so the group is not auto-mergeable."""
    out = []
    all_emails = set().union(*(e["emails"] for e in evs.values())) if evs else set()
    all_phones = set().union(*(e["phones"] for e in evs.values())) if evs else set()
    all_dob = set().union(*(e["dob"] for e in evs.values())) if evs else set()
    all_addr = set().union(*(e["addresses"] for e in evs.values())) if evs else set()
    households = {e["household"] for e in evs.values() if e["household"]}
    if len(all_emails) > 1:
        out.append("email: " + " | ".join(sorted(all_emails)))
    if len(all_phones) > 1:
        out.append("phone: " + " | ".join(sorted(all_phones)))
    if len(all_dob) > 1:
        out.append("dob: " + " | ".join(sorted(all_dob)))
    if len(all_addr) > 1:
        out.append("address: multiple distinct")
    if len(households) > 1:
        out.append("household: " + " | ".join(str(h) for h in sorted(households)))
    if sum(1 for e in evs.values() if e["counts"].get("accounts", 0) > 0) > 1:
        out.append("accounts: owned by multiple members")
    if sum(1 for e in evs.values() if e["counts"].get("engagements", 0) > 0) > 1:
        out.append("engagements: owned by multiple members")
    return out


def _has_evidence(ev: dict) -> bool:
    c = ev["counts"]
    return bool(ev["emails"] or ev["phones"] or ev["dob"] or ev["addresses"] or ev["household"]
                or c.get("accounts", 0) or c.get("engagements", 0))


def select_survivor(conn, person_ids) -> dict:
    """Choose a survivor from a duplicate group using DISTINCT identity evidence gathered from linked
    source_contacts. A unique survivor is chosen ONLY when the group has usable evidence, no conflicting
    identifiers exist, and one member is materially strongest (ties broken deterministically). Otherwise
    the group is AMBIGUOUS. Returns survivor_id, ambiguous, reason, scores, evidence, conflicts."""
    evs = {pid: gather_identity(conn, pid) for pid in person_ids}
    scores = {pid: _score(evs[pid]) for pid in person_ids}
    result = {"survivor_id": None, "ambiguous": True, "reason": "", "scores": scores,
              "evidence": {pid: _evidence_summary(evs[pid]) for pid in person_ids},
              "conflicts": []}

    conflicts = _conflicts(evs)
    if conflicts:
        result["conflicts"] = conflicts
        result["reason"] = "conflicting identifiers: " + "; ".join(conflicts)
        return result

    if not any(_has_evidence(evs[pid]) for pid in person_ids):
        result["reason"] = "no usable identity evidence (all empty shells)"
        return result

    # Consistent evidence + at least one signal → safe. Deterministic survivor: highest score, then most
    # source links, then lowest id. (Handles the "same email across shells" case with an explicit rule.)
    ranked = sorted(person_ids,
                    key=lambda p: (scores[p], evs[p]["counts"].get("source_links", 0), -p), reverse=True)
    survivor = ranked[0]
    top, second = scores[ranked[0]], (scores[ranked[1]] if len(ranked) > 1 else -1)
    reason = (f"clear survivor: materially stronger evidence (score {top} vs {second}); no conflicts"
              if top > second else
              f"consistent shared evidence (score {top}); deterministic survivor by links then id")
    result.update(survivor_id=survivor, ambiguous=False, reason=reason)
    return result


def _pair_rows(group_key, survivor_id, duplicate_id, report, sel):
    """Flatten one preview report + survivor-selection evidence into a CSV row dict."""
    return {
        "survivor_person_id": survivor_id, "merged_person_id": duplicate_id, "group_name": group_key,
        "reason": report.get("reason", ""), "safe_to_merge": report.get("safe_to_merge"),
        "status": report.get("status", ""),
        "blocker": "; ".join(report.get("blockers", []) or []),
        "warning": "; ".join(report.get("warnings", []) or []),
        "survivor_score": sel["scores"].get(survivor_id, ""),
        "survivor_evidence": sel["evidence"].get(survivor_id, ""),
        "duplicate_evidence": sel["evidence"].get(duplicate_id, ""),
        "conflicting_identifiers": "; ".join(sel.get("conflicts", []) or []),
        "selection_reason": sel.get("reason", ""),
    }


def _history_table_exists(conn) -> bool:
    """Whether pmh01's person_merge_history exists. Preview must stay usable before the migration."""
    return conn.execute(text("SELECT to_regclass('person_merge_history')")).scalar() is not None


def _already_merged(conn, duplicate_id) -> bool:
    # Before pmh01 the history table does not exist → nothing has been merged yet, so preview's resume
    # check treats it as "not previously merged" instead of raising UndefinedTable.
    if not _history_table_exists(conn):
        return False
    return conn.execute(text("SELECT 1 FROM person_merge_history WHERE merged_person_id = :d"),
                        {"d": duplicate_id}).first() is not None


def consolidate(*, apply: bool = False, actor_user_id: int | None = None, restrict_ids=None,
                report_path: str | None = None, progress=None) -> dict:
    """Consolidate duplicate people. ``apply=False`` (default) previews only — NO database changes.
    ``apply=True`` merges each safe, unambiguous group via ``merge_people()`` (resumable: already-merged
    or vanished duplicates are skipped). Returns a summary and, if ``report_path`` given, writes a CSV."""
    summary = {"apply": apply, "groups": 0, "merged": 0, "skipped": 0, "ambiguous": 0,
               "blocked": 0, "failed": 0, "rows": []}
    # Apply requires the merge-history ledger; refuse clearly BEFORE any work if pmh01 is not applied.
    # (Preview never needs it — see _already_merged.)
    if apply:
        with engine.connect() as conn:
            if not _history_table_exists(conn):
                raise MergeBlocked(
                    "person_merge_history is missing — apply migration pmh01 before running --apply. "
                    "(preview mode works without it.)")
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
                    "blocker": "", "warning": "",
                    "survivor_score": "", "survivor_evidence": "",
                    "duplicate_evidence": sel["evidence"].get(dup, ""),
                    "conflicting_identifiers": "; ".join(sel.get("conflicts", []) or []),
                    "selection_reason": sel["reason"]})
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
            row = _pair_rows(gkey, survivor, dup, report, sel)
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
