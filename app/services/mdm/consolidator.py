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
              "conflicts": [], "category": "other", "clear_only_eligible": False,
              "materially_stronger": False}

    conflicts = _conflicts(evs)
    if conflicts:
        result.update(conflicts=conflicts, category="conflicting_identity",
                      reason="conflicting identifiers: " + "; ".join(conflicts))
        return result

    if not any(_has_evidence(evs[pid]) for pid in person_ids):
        result.update(category="all_empty_shells",
                      reason="no usable identity evidence (all empty shells)")
        return result

    # Consistent evidence + at least one signal → safe. Deterministic survivor: highest score, then most
    # source links, then lowest id. (Handles the "same email across shells" case with an explicit rule.)
    ranked = sorted(person_ids,
                    key=lambda p: (scores[p], evs[p]["counts"].get("source_links", 0), -p), reverse=True)
    survivor = ranked[0]
    top, second = scores[ranked[0]], (scores[ranked[1]] if len(ranked) > 1 else -1)
    sev = evs[survivor]
    # "materially stronger" = a clear score lead AND the survivor holds strong identity (email/phone/DOB).
    materially_stronger = top > second and bool(sev["emails"] or sev["phones"] or sev["dob"])
    dups_all_empty = all(not _has_evidence(evs[d]) for d in person_ids if d != survivor)
    if materially_stronger:
        category = "clear_survivor"
        reason = f"clear survivor: materially stronger evidence (score {top} vs {second}); no conflicts"
    elif top == second:
        category = "tied_consistent_evidence"
        reason = f"consistent shared evidence (score {top}); deterministic survivor by links then id"
    else:
        category = "other"
        reason = f"survivor by non-identity evidence (score {top} vs {second})"
    result.update(survivor_id=survivor, ambiguous=False, reason=reason, category=category,
                  materially_stronger=materially_stronger,
                  clear_only_eligible=materially_stronger and dups_all_empty)
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


def _norm_group_name(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _scope_groups(groups, *, group_name=None, person_id=None):
    """Restrict discovered groups to an exact normalized group name and/or the group containing a person
    (production-safe allowlist for a first pass)."""
    if group_name is not None:
        target = _norm_group_name(group_name)
        groups = [g for g in groups if _norm_group_name(g["group_key"]) == target]
    if person_id is not None:
        groups = [g for g in groups if int(person_id) in g["person_ids"]]
    return groups


_GROUP_COLUMNS = ("group_name", "member_count", "proposed_survivor_person_id", "survivor_score",
                  "survivor_evidence", "proposed_merge_count", "ambiguity_category",
                  "conflicting_identifiers", "selection_reason", "group_status")


def consolidate(*, apply: bool = False, apply_clear_only: bool = False, actor_user_id: int | None = None,
                restrict_ids=None, group_name: str | None = None, person_id: int | None = None,
                report_path: str | None = None, group_summary_path: str | None = None,
                progress=None) -> dict:
    """Consolidate duplicate people via the MDM-1 engine.

    Modes: ``apply=False`` (default) previews only — NO changes. ``apply=True`` merges every safe,
    unambiguous pair. ``apply_clear_only=True`` (implies apply) merges ONLY groups that qualify as a
    clear first-production pass: a materially-stronger unique survivor, every duplicate an empty shell
    (no email/phone/DOB/household/accounts/engagements/documents/opportunities/address), every pair
    ``safe_to_merge`` with no warnings/blockers. Scope with ``group_name`` (exact) and/or ``person_id``.
    Resumable. Writes a pair-level CSV (``report_path``) and a group-level CSV (``group_summary_path``)."""
    apply = apply or apply_clear_only
    summary = {"apply": apply, "apply_clear_only": apply_clear_only, "groups": 0, "merged": 0,
               "skipped": 0, "ambiguous": 0, "blocked": 0, "failed": 0,
               "clear_only_qualified": 0, "rows": [], "group_rows": []}
    if apply:
        with engine.connect() as conn:
            if not _history_table_exists(conn):
                raise MergeBlocked(
                    "person_merge_history is missing — apply migration pmh01 before running --apply. "
                    "(preview mode works without it.)")
    with engine.connect() as conn:
        groups = _scope_groups(find_duplicate_groups(conn, restrict_ids=restrict_ids),
                               group_name=group_name, person_id=person_id)
    summary["groups"] = len(groups)

    for group in groups:
        gkey, ids = group["group_key"], group["person_ids"]
        with engine.connect() as conn:
            sel = select_survivor(conn, ids)
        grow = {"group_name": gkey, "member_count": len(ids), "proposed_survivor_person_id": "",
                "survivor_score": "", "survivor_evidence": "", "proposed_merge_count": 0,
                "ambiguity_category": sel["category"],
                "conflicting_identifiers": "; ".join(sel.get("conflicts", []) or []),
                "selection_reason": sel["reason"], "group_status": ""}

        if sel["ambiguous"]:
            summary["ambiguous"] += 1
            grow["group_status"] = "ambiguous"
            for dup in ids:
                summary["rows"].append({
                    "survivor_person_id": "", "merged_person_id": dup, "group_name": gkey,
                    "reason": sel["reason"], "safe_to_merge": "", "status": "ambiguous",
                    "blocker": "", "warning": "", "survivor_score": "", "survivor_evidence": "",
                    "duplicate_evidence": sel["evidence"].get(dup, ""),
                    "conflicting_identifiers": grow["conflicting_identifiers"],
                    "selection_reason": sel["reason"]})
            summary["group_rows"].append(grow)
            if progress:
                progress(f"AMBIGUOUS [{sel['category']}] {gkey}")
            continue

        survivor = sel["survivor_id"]
        grow.update(proposed_survivor_person_id=survivor,
                    survivor_score=sel["scores"].get(survivor, ""),
                    survivor_evidence=sel["evidence"].get(survivor, ""))
        dups = [i for i in ids if i != survivor]

        # Preview every remaining pair up front (resume: skip already-merged / vanished duplicates).
        previews, pending = {}, []
        for dup in dups:
            with engine.connect() as conn:
                if _already_merged(conn, dup):
                    summary["skipped"] += 1
                    continue
            previews[dup] = preview_person_merge(survivor, dup)
            pending.append(dup)

        any_unsafe = any(not p["safe_to_merge"] for p in previews.values())
        any_warn = any(p.get("warnings") for p in previews.values())
        if any_unsafe:
            grow["ambiguity_category"] = "engine_blocked"
        clear_ok = sel["clear_only_eligible"] and not any_unsafe and not any_warn
        if clear_ok:
            summary["clear_only_qualified"] += 1

        # Decide whether this group merges in the current mode.
        if apply_clear_only:
            do_merge = clear_ok
        elif apply:
            do_merge = True
        else:
            do_merge = False

        safe_count = sum(1 for p in previews.values() if p["safe_to_merge"])
        grow["proposed_merge_count"] = safe_count
        merged_here = blocked_here = 0
        for dup in pending:
            report = previews[dup]
            row = _pair_rows(gkey, survivor, dup, report, sel)
            if not report["safe_to_merge"]:
                row["status"] = "blocked"
                summary["blocked"] += 1
                blocked_here += 1
                summary["rows"].append(row)
                continue
            if not do_merge:
                row["status"] = "would_merge" if (not apply_clear_only or clear_ok) else "skipped_not_clear"
                summary["skipped"] += 1
                summary["rows"].append(row)
                continue
            try:
                merge_people(survivor, dup, reason=f"MDM-2 consolidation ({gkey})",
                             actor_user_id=actor_user_id)
                row["status"] = "merged"
                summary["merged"] += 1
                merged_here += 1
            except MergeBlocked as exc:
                row["status"], row["blocker"] = "blocked", str(exc)
                summary["blocked"] += 1
                blocked_here += 1
            except Exception as exc:      # noqa: BLE001 — record & continue; the engine rolled back
                row["status"], row["blocker"] = "failed", str(exc)
                summary["failed"] += 1
            summary["rows"].append(row)

        grow["group_status"] = (
            "merged" if merged_here else
            "blocked" if blocked_here and not safe_count else
            "skipped_not_clear" if (apply_clear_only and not clear_ok) else
            "would_merge" if safe_count else "no_action")
        summary["group_rows"].append(grow)
        if progress:
            progress(f"{grow['group_status'].upper()} [{grow['ambiguity_category']}] {gkey} "
                     f"survivor={survivor} merges={safe_count}")

    if report_path:
        _write_csv(report_path, _REPORT_COLUMNS, summary["rows"])
        summary["report_path"] = report_path
    if group_summary_path:
        _write_csv(group_summary_path, _GROUP_COLUMNS, summary["group_rows"])
        summary["group_summary_path"] = group_summary_path
    return summary


def _write_csv(path: str, columns, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})
