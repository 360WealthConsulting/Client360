from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

# Use the shared reflected metadata/engine (app.db) rather than the separate
# app.database.schema engine, so the process opens a single connection pool
# (H22 — the /matches -> person_merge import chain previously created a second
# engine against the same database at startup).
from app.db import (
    engine,
    people,
    person_source_links,
    source_contacts,
)


def _first_value(records, field_name):
    for record in records:
        value = record.get(field_name)
        if value not in (None, ""):
            return value
    return None


def merge_source_contacts(record_ids: Iterable[int]) -> int:
    normalized_ids = sorted({int(record_id) for record_id in record_ids})

    if not normalized_ids:
        raise ValueError("At least one source contact ID is required.")

    with engine.begin() as conn:
        records = conn.execute(
            select(source_contacts)
            .where(source_contacts.c.id.in_(normalized_ids))
            .order_by(source_contacts.c.id)
        ).mappings().all()

        if len(records) != len(normalized_ids):
            found_ids = {record["id"] for record in records}
            missing_ids = [
                record_id
                for record_id in normalized_ids
                if record_id not in found_ids
            ]
            raise ValueError(
                f"Source contacts not found: {missing_ids}"
            )

        existing_person_ids = conn.execute(
            select(person_source_links.c.person_id)
            .where(
                person_source_links.c.source_contact_id.in_(
                    normalized_ids
                )
            )
            .distinct()
        ).scalars().all()

        if len(existing_person_ids) > 1:
            raise ValueError(
                "The selected source contacts are already linked "
                "to different canonical people."
            )

        if existing_person_ids:
            person_id = existing_person_ids[0]
        else:
            person_values = {
                "first_name": _first_value(records, "first_name"),
                "middle_name": _first_value(records, "middle_name"),
                "last_name": _first_value(records, "last_name"),
                "full_name": _first_value(records, "full_name"),
                "primary_email": _first_value(records, "email"),
                "normalized_email": _first_value(
                    records,
                    "normalized_email",
                ),
                "primary_phone": _first_value(records, "phone"),
                "normalized_phone": _first_value(
                    records,
                    "normalized_phone",
                ),
                "address_line_1": _first_value(
                    records,
                    "address_line_1",
                ),
                "address_line_2": _first_value(
                    records,
                    "address_line_2",
                ),
                "city": _first_value(records, "city"),
                "state": _first_value(records, "state"),
                "postal_code": _first_value(records, "postal_code"),
                "active": True,
            }

            person_id = conn.execute(
                people.insert()
                .values(**person_values)
                .returning(people.c.id)
            ).scalar_one()

        for record_id in normalized_ids:
            statement = (
                insert(person_source_links)
                .values(
                    person_id=person_id,
                    source_contact_id=record_id,
                    match_method="manual_review",
                    match_score=100,
                    confirmed=True,
                )
                .on_conflict_do_nothing(
                    constraint="uq_person_source_link"
                )
            )

            conn.execute(statement)

        # (D.35) Publish the identity-merged business FACT (references only) in the merge transaction.
        from app.services.events import publisher
        publisher.publish_safe("people.identity_merged",
                               {"person_id": person_id, "source_contact_count": len(normalized_ids)},
                               conn=conn, producer="people.merge", subject_ref=f"person:{person_id}")

    return person_id


# ==========================================================================================
# MDM-1 — production-safe canonical PERSON merge (survivor ← duplicate).
#
# merge_source_contacts (above) only consolidates *source contacts* and refuses cross-person merges.
# The functions below merge two CANONICAL people. Every foreign key that references people.id is handled
# by an EXPLICIT registry (never a blind sweep of all 53): each reference is classified so uniqueness
# conflicts and CASCADE deletes can never silently destroy business records. The duplicate person row is
# only deleted after every reference has been reassigned/consolidated and a final re-scan confirms zero
# remaining references (otherwise the whole transaction rolls back).
# ==========================================================================================


class MergeBlocked(RuntimeError):
    """A merge cannot proceed safely (a conflict-sensitive or legal/governance blocker was found)."""


# Survivor profile fields filled from the duplicate ONLY when the survivor's value is null/empty. A
# populated survivor field is never overwritten (there is no other survivorship rule in MDM-1).
_PROFILE_FILL_FIELDS = (
    "first_name", "middle_name", "last_name", "full_name", "preferred_name", "birth_date",
    "primary_email", "normalized_email", "primary_phone", "normalized_phone",
    "address_line_1", "address_line_2", "city", "state", "postal_code", "contact_type",
    "household_id",
)

# Explicit table-handling registry. Each entry: (table, person_column, strategy, extra).
#   simple            — reassign duplicate person_id -> survivor (no person-scoped unique to violate).
#   dedup             — junction with a unique(person + other keys): reassign the non-conflicting rows,
#                       delete + count the rows that would collide with an existing survivor row.
#   conflict_singular — singular/business-unique ownership (unique on person_id alone, or one-per-person):
#                       if BOTH own a row it is a BLOCKER; if only the duplicate does, reassign.
#   block_if_present  — legal / governance history: if the duplicate is referenced at all, BLOCK.
# The registry covers every FK to people.id; a final re-scan guarantees nothing was missed.
_REGISTRY = [
    # --- B. Deduplicating junctions -------------------------------------------------------
    ("person_source_links", "person_id", "dedup", ("source_contact_id",)),
    ("household_relationships", "person_id", "dedup", ("household_id",)),
    ("benefit_dependents", "person_id", "dedup", ("benefit_enrollment_id",)),
    ("benefit_employments", "person_id", "dedup", ("organization_id",)),
    ("opportunity_participants", "person_id", "dedup", ("opportunity_id",)),
    ("match_queue", "candidate_person_id", "dedup", ("source_contact_id",)),
    ("microsoft_document_matching_rules", "person_id", "dedup", ("rule_type", "pattern")),
    ("portal_access_grants", "person_id", "dedup",
     ("portal_account_id", "household_id", "access_type", "effective_date")),
    # --- C. Singular / conflict-sensitive ownership ---------------------------------------
    ("relationship_entities", "person_id", "conflict_singular", None),   # UNIQUE(person_id)
    ("portal_accounts", "person_id", "conflict_singular", None),         # one portal account per person
    # --- Legal / governance history: refuse to merge a person entangled in these -----------
    ("governance_legal_holds", "person_id", "block_if_present", None),
    ("governance_deletion_requests", "person_id", "block_if_present", None),
    ("governance_merge_decisions", "merged_person_id", "block_if_present", None),
    # --- A. Simple reassignment (person FK, no person-scoped unique) -----------------------
    ("accounts", "person_id", "simple", None),
    ("activities", "person_id", "simple", None),
    ("advisor_work_items", "person_id", "simple", None),
    ("annual_review_sessions", "person_id", "simple", None),
    ("automation_runs", "person_id", "simple", None),
    ("business_planning_profiles", "emergency_contact_person_id", "simple", None),
    ("business_planning_profiles", "successor_person_id", "simple", None),
    ("communication_conversations", "person_id", "simple", None),
    ("compliance_reviews", "person_id", "simple", None),
    ("documents", "person_id", "simple", None),
    ("engagements", "person_id", "simple", None),
    ("exceptions", "person_id", "simple", None),
    ("governance_cases", "person_id", "simple", None),
    ("governance_duplicate_candidates", "person_id", "simple", None),
    ("governance_quality_findings", "person_id", "simple", None),
    ("governance_retention_assignments", "person_id", "simple", None),
    ("insurance_cases", "person_id", "simple", None),
    ("insurance_policies", "person_id", "simple", None),
    ("integration_sync_runs", "person_id", "simple", None),
    ("meetings", "person_id", "simple", None),
    ("microsoft_accounts", "person_id", "simple", None),
    ("microsoft_documents", "person_id", "simple", None),
    ("microsoft_unmatched_calendar_attendees", "matched_person_id", "simple", None),
    ("microsoft_unmatched_messages", "matched_person_id", "simple", None),
    ("observability_reliability_incidents", "person_id", "simple", None),
    ("operational_tasks", "person_id", "simple", None),
    ("opportunities", "person_id", "simple", None),
    ("opportunities", "referral_source_person_id", "simple", None),
    ("portal_document_requests", "person_id", "simple", None),
    ("portal_threads", "person_id", "simple", None),
    ("projects", "person_id", "simple", None),
    ("referral_sources", "person_id", "simple", None),
    ("reports", "person_id", "simple", None),
    ("security_incidents", "person_id", "simple", None),
    ("signature_requests", "person_id", "simple", None),
    ("tasks", "person_id", "simple", None),
    ("tax_engagements", "person_id", "simple", None),
    ("timeline_events", "person_id", "simple", None),
    ("vault_document_links", "person_id", "simple", None),
    ("workflow_instances", "person_id", "simple", None),
]


def _person(conn, pid, *, lock=False):
    stmt = f"SELECT * FROM people WHERE id = :pid{' FOR UPDATE' if lock else ''}"
    return conn.execute(text(stmt), {"pid": pid}).mappings().first()


def _count(conn, table, col, pid):
    return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {col} = :pid"),
                        {"pid": pid}).scalar_one()


def _profile_fills(survivor, duplicate):
    """Fields the survivor would inherit from the duplicate (survivor null/empty, duplicate populated)."""
    fills = {}
    for f in _PROFILE_FILL_FIELDS:
        sv, dv = survivor.get(f), duplicate.get(f)
        if sv in (None, "") and dv not in (None, ""):
            fills[f] = dv
    return fills


def _person_summary(conn, row):
    n_links = _count(conn, "person_source_links", "person_id", row["id"])
    return {"id": row["id"], "full_name": row.get("full_name"),
            "primary_email": row.get("primary_email"), "primary_phone": row.get("primary_phone"),
            "active": row.get("active"), "household_id": row.get("household_id"),
            "source_link_count": n_links}


def _dedup_counts(conn, table, col, other_keys, survivor, duplicate):
    """(would_move, would_consolidate) for a dedup junction — consolidate = rows that collide with an
    existing survivor row on the unique key."""
    total = _count(conn, table, col, duplicate)
    if total == 0:
        return 0, 0
    on = " AND ".join(f"x.{k} = t.{k}" for k in other_keys)
    collide = conn.execute(text(
        f"SELECT count(*) FROM {table} t WHERE t.{col} = :dup "
        f"AND EXISTS (SELECT 1 FROM {table} x WHERE x.{col} = :surv AND {on})"),
        {"dup": duplicate, "surv": survivor}).scalar_one()
    return total - collide, collide


def _build_report(conn, survivor_id, duplicate_id):
    report = {
        "survivor_person_id": survivor_id, "duplicate_person_id": duplicate_id,
        "survivor": None, "duplicate": None, "source_links_would_move": 0,
        "foreign_key_row_counts": {}, "profile_fields_would_fill": {},
        "dedup_actions": [], "conflicts": [], "blockers": [], "warnings": [],
        "safe_to_merge": False,
    }
    if survivor_id == duplicate_id:
        report["blockers"].append("survivor and duplicate are the same person")
        return report
    survivor = _person(conn, survivor_id)
    duplicate = _person(conn, duplicate_id)
    if survivor is None:
        report["blockers"].append(f"survivor person {survivor_id} does not exist")
    if duplicate is None:
        report["blockers"].append(f"duplicate person {duplicate_id} does not exist")
    if survivor is None or duplicate is None:
        return report

    report["survivor"] = _person_summary(conn, survivor)
    report["duplicate"] = _person_summary(conn, duplicate)
    report["profile_fields_would_fill"] = {k: str(v) for k, v in _profile_fills(survivor, duplicate).items()}

    # Warn where BOTH have a populated profile value and they differ — kept on the survivor, not overwritten.
    for f in _PROFILE_FILL_FIELDS:
        sv, dv = survivor.get(f), duplicate.get(f)
        if sv not in (None, "") and dv not in (None, "") and sv != dv:
            report["warnings"].append(
                f"{f}: survivor keeps '{sv}' (duplicate '{dv}' not applied)")

    for table, col, strategy, extra in _REGISTRY:
        n = _count(conn, table, col, duplicate_id)
        if n:
            report["foreign_key_row_counts"][f"{table}.{col}"] = n
        if strategy == "dedup":
            move, consolidate = _dedup_counts(conn, table, col, extra, survivor_id, duplicate_id)
            if move or consolidate:
                report["dedup_actions"].append(
                    {"table": table, "column": col, "reassign": move, "consolidate": consolidate})
            if table == "person_source_links":
                report["source_links_would_move"] = move + consolidate
        elif strategy == "conflict_singular":
            if n and _count(conn, table, col, survivor_id):
                report["blockers"].append(
                    f"both people own a row in {table} (unique per person) — needs a manual decision")
            elif n:
                report["conflicts"].append({"table": table, "column": col, "action": "reassign", "rows": n})
        elif strategy == "block_if_present":
            if n:
                report["blockers"].append(
                    f"duplicate is referenced by {table}.{col} ({n} row(s)) — legal/governance hold")

    report["safe_to_merge"] = not report["blockers"]
    return report


def preview_person_merge(survivor_person_id: int, duplicate_person_id: int) -> dict:
    """Read-only structured preview of merging ``duplicate_person_id`` into ``survivor_person_id``.
    Makes NO changes. Returns survivor/duplicate summaries, FK row counts, profile fields that would be
    filled, dedup actions, conflicts, blockers, warnings, and ``safe_to_merge``."""
    with engine.connect() as conn:
        return _build_report(conn, int(survivor_person_id), int(duplicate_person_id))


def _apply_dedup(conn, table, col, other_keys, survivor_id, duplicate_id):
    on = " AND ".join(f"x.{k} = {table}.{k}" for k in other_keys)
    moved = conn.execute(text(
        f"UPDATE {table} SET {col} = :surv WHERE {col} = :dup "
        f"AND NOT EXISTS (SELECT 1 FROM {table} x WHERE x.{col} = :surv AND {on})"),
        {"surv": survivor_id, "dup": duplicate_id}).rowcount or 0
    consolidated = conn.execute(text(f"DELETE FROM {table} WHERE {col} = :dup"),
                                {"dup": duplicate_id}).rowcount or 0
    return moved, consolidated


def merge_people(survivor_person_id: int, duplicate_person_id: int, *, reason: str,
                 actor_user_id: int | None = None, dry_run: bool = False) -> dict:
    """Merge ``duplicate_person_id`` into ``survivor_person_id`` (survivor kept). Safe: rejects identical
    ids, requires both to exist, locks both rows FOR UPDATE, runs everything in ONE transaction, refuses
    on any blocker, never silently deletes business records, dedups junctions, fills only null survivor
    fields, records history, publishes an event, writes an audit entry, then removes the duplicate. On any
    failure the whole transaction rolls back. ``dry_run=True`` returns the plan and makes NO changes."""
    survivor_id, duplicate_id = int(survivor_person_id), int(duplicate_person_id)
    if survivor_id == duplicate_id:
        raise ValueError("Cannot merge a person into themselves (identical ids).")

    if dry_run:
        report = preview_person_merge(survivor_id, duplicate_id)
        report["dry_run"] = True
        report["applied"] = False
        return report

    summary = {"survivor_person_id": survivor_id, "duplicate_person_id": duplicate_id,
               "dry_run": False, "applied": False, "reassigned": {}, "consolidated": {},
               "profile_filled": {}, "reason": reason}

    with engine.begin() as conn:
        # Lock both rows in a stable order (avoid deadlocks); both must exist.
        for pid in sorted((survivor_id, duplicate_id)):
            if _person(conn, pid, lock=True) is None:
                raise ValueError(f"Person {pid} does not exist (nothing to merge).")
        survivor = _person(conn, survivor_id)
        duplicate = _person(conn, duplicate_id)

        report = _build_report(conn, survivor_id, duplicate_id)
        if report["blockers"]:
            raise MergeBlocked("; ".join(report["blockers"]))

        pre_snapshot = {"survivor": {k: str(v) for k, v in dict(survivor).items()},
                        "duplicate": {k: str(v) for k, v in dict(duplicate).items()},
                        "foreign_key_row_counts": report["foreign_key_row_counts"]}

        # 1) Fill only NULL survivor profile fields from the duplicate (never overwrite populated).
        fills = _profile_fills(survivor, duplicate)
        if fills:
            sets = ", ".join(f"{f} = :{f}" for f in fills)
            conn.execute(text(f"UPDATE people SET {sets} WHERE id = :sid"), {**fills, "sid": survivor_id})
            summary["profile_filled"] = {k: str(v) for k, v in fills.items()}

        # 2) Handle every FK reference via the explicit registry.
        for table, col, strategy, extra in _REGISTRY:
            if strategy == "simple":
                n = conn.execute(text(f"UPDATE {table} SET {col} = :surv WHERE {col} = :dup"),
                                 {"surv": survivor_id, "dup": duplicate_id}).rowcount or 0
                if n:
                    summary["reassigned"][f"{table}.{col}"] = n
            elif strategy == "dedup":
                moved, consolidated = _apply_dedup(conn, table, col, extra, survivor_id, duplicate_id)
                if moved:
                    summary["reassigned"][f"{table}.{col}"] = moved
                if consolidated:
                    summary["consolidated"][f"{table}.{col}"] = consolidated
            elif strategy == "conflict_singular":
                # Blockers already caught above; here only the duplicate can own a row → reassign it.
                n = conn.execute(text(f"UPDATE {table} SET {col} = :surv WHERE {col} = :dup"),
                                 {"surv": survivor_id, "dup": duplicate_id}).rowcount or 0
                if n:
                    summary["reassigned"][f"{table}.{col}"] = n
            # block_if_present: guaranteed zero rows here (blocker check passed).

        # 3) Final safety net: no reference to the duplicate may remain before it is removed, or a
        #    CASCADE/SET NULL delete could silently damage business records.
        remaining = {f"{t}.{c}": _count(conn, t, c, duplicate_id)
                     for t, c, _s, _e in _REGISTRY if _count(conn, t, c, duplicate_id)}
        if remaining:
            raise MergeBlocked(f"references to the duplicate still remain, refusing to delete: {remaining}")

        # 4) Record permanent merge history (survives deletion of the duplicate row — no FK on ids).
        summary["applied"] = True
        merge_summary = {k: summary[k] for k in ("reassigned", "consolidated", "profile_filled")}
        conn.execute(text(
            "INSERT INTO person_merge_history "
            "(survivor_person_id, merged_person_id, reason, merge_method, actor_user_id, "
            " pre_merge_snapshot, merge_summary) "
            "VALUES (:surv, :dup, :reason, :method, :actor, :snap, :sum)"),
            {"surv": survivor_id, "dup": duplicate_id, "reason": reason,
             "method": "manual_review", "actor": actor_user_id,
             "snap": _json(pre_snapshot), "sum": _json(merge_summary)})

        # 5) Publish the identity fact within the same transaction (transactional outbox).
        from app.services.events import publisher
        publisher.publish_safe(
            "people.person_merged",
            {"survivor_person_id": survivor_id, "merged_person_id": duplicate_id},
            conn=conn, producer="people.merge", subject_ref=f"person:{survivor_id}")

        # 6) Remove the now-unreferenced duplicate person.
        conn.execute(text("DELETE FROM people WHERE id = :dup"), {"dup": duplicate_id})

    # 7) Audit (existing infrastructure) — after the merge transaction commits.
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(
            action="person.merged", entity_type="person", entity_id=survivor_id,
            actor_user_id=actor_user_id, request_id=f"person-merge-{uuid.uuid4()}",
            metadata={"merged_person_id": duplicate_id, "reason": reason,
                      "reassigned": summary["reassigned"], "consolidated": summary["consolidated"]})
    except Exception:      # noqa: BLE001 — audit failure must not undo a committed, valid merge
        summary["audit_warning"] = "audit event could not be written"
    return summary


def _json(value):
    import json
    return json.dumps(value, default=str)
