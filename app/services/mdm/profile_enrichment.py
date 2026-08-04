"""MDM-2 — canonical profile enrichment from linked source_contacts.

After consolidation a survivor may own the correct email/phone in its linked ``source_contacts`` while its
canonical ``people`` row is still sparse (e.g. Austin Weaver 5265: 27 links, but primary_email/phone
NULL). This step safely backfills canonical identity fields from the linked source contacts:

- fills ONLY null canonical fields (never overwrites a populated one);
- derives values from the person's linked source_contacts (columns + raw_data);
- normalizes email (lowercased) and phone (digits) — reusing the consolidator's normalizers;
- fills a field only when the linked sources agree on a SINGLE unambiguous value;
- refuses (skips) a field when the sources hold conflicting values;
- records which fields were filled and from which source_contact_id (audit event + return summary);
- is idempotent (a second run finds the fields already set and does nothing);
- runs independently, scoped by --person-id or --group.

It never touches document_sources / Drake data, and never merges people.
"""
from __future__ import annotations

import csv
import uuid
from pathlib import Path

from sqlalchemy import text

from app.db import engine
from app.services.mdm.consolidator import (
    _EMAIL_KEYS,
    _PHONE_KEYS,
    _norm_email,
    _norm_group_name,
    _norm_phone,
)

# Identity field groups: canonical primary column + its normalized twin, both filled from one source value.
_FIELD_GROUPS = {
    "email": ("primary_email", "normalized_email"),
    "phone": ("primary_phone", "normalized_phone"),
}
_REPORT_COLUMNS = ("person_id", "field", "proposed_value", "source_contact_id", "status",
                   "conflicting_values", "reason")


def _source_candidates(conn, pid) -> dict:
    """{'email': {norm_value: {source_contact_id,...}}, 'phone': {...}} across the person's links."""
    cand = {"email": {}, "phone": {}}
    for sc in conn.execute(text(
            "SELECT sc.id, sc.email, sc.normalized_email, sc.phone, sc.normalized_phone, sc.raw_data "
            "FROM source_contacts sc JOIN person_source_links l ON l.source_contact_id = sc.id "
            "WHERE l.person_id = :p"), {"p": pid}).mappings():
        scid = sc["id"]
        for e in (sc["email"], sc["normalized_email"]):
            v = _norm_email(e)
            if v:
                cand["email"].setdefault(v, set()).add(scid)
        for ph in (sc["phone"], sc["normalized_phone"]):
            v = _norm_phone(ph)
            if v:
                cand["phone"].setdefault(v, set()).add(scid)
        raw = sc["raw_data"] if isinstance(sc["raw_data"], dict) else {}
        for k, val in raw.items():
            kl = str(k).strip().lower()
            if val in (None, ""):
                continue
            if kl in _EMAIL_KEYS and _norm_email(val):
                cand["email"].setdefault(_norm_email(val), set()).add(scid)
            elif kl in _PHONE_KEYS and _norm_phone(val):
                cand["phone"].setdefault(_norm_phone(val), set()).add(scid)
    return cand


def preview_person(conn, pid) -> dict:
    """Compute proposed canonical fills for one person. No changes. Returns rows describing each field:
    would_fill / already_set / conflict / no_source, plus the winning value + source_contact_id."""
    person = conn.execute(text(
        "SELECT primary_email, normalized_email, primary_phone, normalized_phone "
        "FROM people WHERE id = :p"), {"p": pid}).mappings().first()
    result = {"person_id": pid, "fills": {}, "rows": []}
    if person is None:
        result["rows"].append({"person_id": pid, "field": "-", "proposed_value": "",
                               "source_contact_id": "", "status": "not_found",
                               "conflicting_values": "", "reason": "person does not exist"})
        return result
    cand = _source_candidates(conn, pid)
    for kind, (primary_col, norm_col) in _FIELD_GROUPS.items():
        row = {"person_id": pid, "field": primary_col, "proposed_value": "", "source_contact_id": "",
               "status": "", "conflicting_values": "", "reason": ""}
        if person[primary_col] not in (None, ""):
            row.update(status="already_set", reason="canonical field already populated")
            result["rows"].append(row)
            continue
        values = cand[kind]
        if not values:
            row.update(status="no_source", reason="no usable value in linked source contacts")
        elif len(values) > 1:
            row.update(status="conflict", conflicting_values=" | ".join(sorted(values)),
                       reason="linked source contacts disagree — not populated")
        else:
            value = next(iter(values))
            scid = min(values[value])                     # deterministic provenance
            row.update(proposed_value=value, source_contact_id=scid, status="would_fill",
                       reason="single unambiguous value from linked source contacts")
            result["fills"][kind] = {"primary_col": primary_col, "norm_col": norm_col,
                                     "value": value, "source_contact_id": scid}
        result["rows"].append(row)
    return result


def _targets(conn, *, person_id, group_name, restrict_ids):
    if person_id is not None:
        return [int(person_id)]
    if group_name is not None:
        key = _norm_group_name(group_name)
        rows = conn.execute(text(
            "SELECT id FROM people WHERE active IS TRUE AND "
            "lower(btrim(coalesce(nullif(full_name,''), concat_ws(' ', first_name, last_name)))) = :k"),
            {"k": key}).scalars().all()
        return list(rows)
    if restrict_ids is not None:
        return list(restrict_ids)
    # Whole corpus: active people that have a linked source contact and a null email or phone.
    return list(conn.execute(text(
        "SELECT DISTINCT p.id FROM people p JOIN person_source_links l ON l.person_id = p.id "
        "WHERE p.active IS TRUE AND (p.primary_email IS NULL OR p.primary_phone IS NULL) "
        "ORDER BY p.id")).scalars().all())


def enrich_people(*, apply: bool = False, person_id: int | None = None, group_name: str | None = None,
                  restrict_ids=None, actor_user_id: int | None = None, report_path: str | None = None,
                  progress=None) -> dict:
    """Preview (default) or apply canonical profile enrichment. Fills only null fields, only on a single
    unambiguous source value, never overwriting populated fields. Idempotent. Returns a summary + rows."""
    summary = {"apply": apply, "people": 0, "fields_filled": 0, "people_enriched": 0,
               "conflicts": 0, "already_set": 0, "no_source": 0, "rows": []}
    with engine.connect() as conn:
        targets = _targets(conn, person_id=person_id, group_name=group_name, restrict_ids=restrict_ids)
    summary["people"] = len(targets)

    for pid in targets:
        with engine.connect() as conn:
            prev = preview_person(conn, pid)
        summary["rows"].extend(prev["rows"])
        summary["conflicts"] += sum(1 for r in prev["rows"] if r["status"] == "conflict")
        summary["already_set"] += sum(1 for r in prev["rows"] if r["status"] == "already_set")
        summary["no_source"] += sum(1 for r in prev["rows"] if r["status"] == "no_source")

        if not prev["fills"]:
            continue
        if not apply:
            for r in prev["rows"]:
                if r["status"] == "would_fill":
                    summary["fields_filled"] += 0      # preview: counted as would-fill only
            continue

        filled = _apply_fills(pid, prev["fills"])
        if filled:
            summary["fields_filled"] += len(filled)
            summary["people_enriched"] += 1
            for r in summary["rows"]:
                if r["person_id"] == pid and r["field"] in filled:
                    r["status"] = "filled"
            _audit(pid, filled, actor_user_id)
            if progress:
                progress(f"ENRICHED person {pid}: {', '.join(sorted(filled))}")

    if report_path:
        _write_csv(report_path, summary["rows"])
        summary["report_path"] = report_path
    return summary


def _apply_fills(pid, fills) -> dict:
    """Fill null canonical fields in one transaction. Re-checks NULL under FOR UPDATE (idempotent, never
    overwrites). Returns {primary_col: {'value','source_contact_id'}} actually written."""
    written = {}
    with engine.begin() as conn:
        person = conn.execute(text(
            "SELECT primary_email, normalized_email, primary_phone, normalized_phone "
            "FROM people WHERE id = :p FOR UPDATE"), {"p": pid}).mappings().first()
        if person is None:
            return written
        sets, params = [], {"p": pid}
        for f in fills.values():
            if person[f["primary_col"]] in (None, ""):        # still null → safe to fill
                sets.append(f"{f['primary_col']} = :{f['primary_col']}")
                params[f["primary_col"]] = f["value"]
                if person[f["norm_col"]] in (None, ""):
                    sets.append(f"{f['norm_col']} = :{f['norm_col']}")
                    params[f["norm_col"]] = f["value"]
                written[f["primary_col"]] = {"value": f["value"],
                                             "source_contact_id": f["source_contact_id"]}
        if sets:
            conn.execute(text(f"UPDATE people SET {', '.join(sets)} WHERE id = :p"), params)
    return written


def _audit(pid, filled, actor_user_id):
    from app.security.audit import write_audit_event
    write_audit_event(
        action="person.profile_enriched", entity_type="person", entity_id=pid,
        actor_user_id=actor_user_id, request_id=f"profile-enrich-{uuid.uuid4()}",
        metadata={"filled": {col: {"source_contact_id": v["source_contact_id"]}
                             for col, v in filled.items()}})


def _write_csv(path: str, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_REPORT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _REPORT_COLUMNS})
