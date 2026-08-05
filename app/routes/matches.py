import csv
import hashlib
from html import escape
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    match_review_decisions,
    source_contacts,
)
from app.services.person_merge import merge_source_contacts
from app.templating import render_error

MERGE_PLAN_PATH = Path("06 Reports/private/exact_match_merge_plan.csv")

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _group_key(record_ids):
    """Stable sha256 key over the sorted integer record ids of a match group."""
    ids = sorted(record_ids)
    return hashlib.sha256("|".join(str(i) for i in ids).encode("utf-8")).hexdigest()


def _record_ids(row):
    return [int(v.strip()) for v in row.get("record_ids", "").split("|") if v.strip().isdigit()]


def count_pending_match_groups():
    """Return the number of duplicate-match review groups still awaiting a
    decision, i.e. groups in the merge plan with no persisted decision.

    The dashboard previously counted ``match_review_decisions.decision ==
    'pending'``, but that value is never written (the only writer allows
    approved/rejected/skipped), so the metric was structurally always zero
    (H14). This mirrors the computation used by the /matches review page.
    Returns 0 if the merge plan has not been generated yet.
    """
    if not MERGE_PLAN_PATH.exists():
        return 0
    group_keys = []
    with MERGE_PLAN_PATH.open("r", encoding="utf-8-sig", newline="") as file_handle:
        for row in csv.DictReader(file_handle):
            if row.get("decision") != "REVIEW":
                continue
            group_keys.append(_group_key(_record_ids(row)))
    if not group_keys:
        return 0
    with engine.connect() as connection:
        decided = set(connection.scalars(
            select(match_review_decisions.c.group_key).where(
                match_review_decisions.c.group_key.in_(group_keys)
            )
        ))
    return max(len(group_keys) - len(decided), 0)


def _review_groups():
    with MERGE_PLAN_PATH.open("r", encoding="utf-8-sig", newline="") as file_handle:
        return [row for row in csv.DictReader(file_handle) if row.get("decision") == "REVIEW"]


@router.get("/matches/{group_number:int}")
def match_group_page(request: Request, group_number: int):
    if not MERGE_PLAN_PATH.exists():
        return render_error(request, 404, detail="Match report not found.")

    review_groups = _review_groups()
    if group_number < 1 or group_number > len(review_groups):
        return render_error(request, 404, detail="Review group not found.")

    group = review_groups[group_number - 1]
    record_ids = _record_ids(group)
    group_key = _group_key(record_ids)

    with engine.connect() as connection:
        saved_decision = connection.execute(
            select(match_review_decisions).where(
                match_review_decisions.c.group_key == group_key
            )
        ).mappings().first()

    decision_label = (
        saved_decision["decision"].replace("_", " ").title()
        if saved_decision
        else "Pending Review"
    )

    with engine.connect() as connection:
        records = connection.execute(
            select(source_contacts)
            .where(source_contacts.c.id.in_(record_ids))
            .order_by(source_contacts.c.source_system)
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="matches/group.html",
        context={
            "group_number": group_number,
            "decision_label": decision_label,
            "group": group,
            "records": [dict(record) for record in records],
        },
    )


@router.post("/matches/{group_number}/decision/{decision}")
def save_match_decision(group_number: int, decision: str):
    allowed_decisions = {
        "approved",
        "rejected",
        "skipped",
    }

    if decision not in allowed_decisions:
        return HTMLResponse(
            "<h1>Invalid match decision</h1>",
            status_code=400,
        )

    report_path = Path(
        "06 Reports/private/exact_match_merge_plan.csv"
    )

    if not report_path.exists():
        return HTMLResponse(
            "<h1>Match report not found</h1>",
            status_code=404,
        )

    with report_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file_handle:
        review_groups = [
            row
            for row in csv.DictReader(file_handle)
            if row.get("decision") == "REVIEW"
        ]

    if group_number < 1 or group_number > len(review_groups):
        return HTMLResponse(
            "<h1>Review group not found</h1>",
            status_code=404,
        )

    group = review_groups[group_number - 1]

    record_ids = sorted(
        int(value.strip())
        for value in group.get("record_ids", "").split("|")
        if value.strip().isdigit()
    )

    group_key_source = "|".join(
        str(record_id)
        for record_id in record_ids
    )

    group_key = hashlib.sha256(
        group_key_source.encode("utf-8")
    ).hexdigest()

    with engine.connect() as connection:
        existing_decision = connection.execute(
            select(match_review_decisions.c.decision).where(
                match_review_decisions.c.group_key == group_key
            )
        ).scalar_one_or_none()

    if existing_decision == "approved" and decision != "approved":
        return HTMLResponse(
            "<h1>Approved merge is locked</h1>"
            "<p>This group has already been merged into a canonical "
            "person. An unmerge workflow is required before changing "
            "the decision.</p>",
            status_code=409,
        )

    statement = (
        pg_insert(match_review_decisions)
        .values(
            group_key=group_key,
            record_ids=record_ids,
            decision=decision,
            reviewed_by="Michael Shelton",
        )
        .on_conflict_do_update(
            index_elements=["group_key"],
            set_={
                "record_ids": record_ids,
                "decision": decision,
                "reviewed_by": "Michael Shelton",
                "reviewed_at": func.now(),
                "updated_at": func.now(),
            },
        )
    )

    if decision == "approved":
        try:
            merge_source_contacts(record_ids)
        except ValueError as exc:
            return HTMLResponse(
                f"<h1>Merge failed</h1><p>{escape(str(exc))}</p>",
                status_code=400,
            )

    with engine.begin() as connection:
        connection.execute(statement)

    next_group = min(
        group_number + 1,
        len(review_groups),
    )

    return RedirectResponse(
        url=f"/matches/{next_group}",
        status_code=303,
    )



def _next_pending_drake_identity(connection):
    return connection.execute(text("""
        SELECT identifier_hash
        FROM drake_identity_match_candidates
        WHERE status = 'pending'
        ORDER BY score DESC, identifier_hash
        LIMIT 1
    """)).scalar_one_or_none()


@router.get("/matches/drake")
def drake_identity_review_page(
    request: Request,
    status: str = "pending",
    saved: int | None = None,
):
    allowed = {"pending", "approved", "rejected", "deferred", "all"}
    if status not in allowed:
        status = "pending"

    status_clause = ""
    parameters = {}

    if status != "all":
        status_clause = "WHERE c.status = :status"
        parameters["status"] = status

    with engine.connect() as connection:
        rows = connection.execute(text(f"""
            SELECT
                c.id AS candidate_id,
                c.identifier_hash,
                c.person_id,
                c.score,
                c.reasons,
                c.rank,
                c.status,
                di.first_year,
                di.last_year,
                di.return_count,
                di.taxpayer_name,
                di.spouse_name,
                di.primary_person_id,
                p.full_name AS candidate_name,
                p.primary_email,
                p.primary_phone,
                p.city,
                p.state
            FROM drake_identity_match_candidates c
            JOIN drake_identity di
              ON di.identifier_hash = c.identifier_hash
            JOIN people p
              ON p.id = c.person_id
            {status_clause}
            ORDER BY
                CASE c.status
                    WHEN 'pending' THEN 0
                    WHEN 'deferred' THEN 1
                    ELSE 2
                END,
                c.score DESC,
                c.identifier_hash,
                c.rank
        """), parameters).mappings().all()

        summary = connection.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM drake_identity) AS total_identities,
                (SELECT COUNT(*) FROM drake_identity
                  WHERE primary_person_id IS NOT NULL) AS linked_identities,
                (SELECT COUNT(*) FROM drake_identity
                  WHERE primary_person_id IS NULL) AS unresolved_identities,
                COUNT(DISTINCT identifier_hash)
                  FILTER (WHERE status = 'pending') AS pending,
                COUNT(DISTINCT identifier_hash)
                  FILTER (WHERE status = 'approved') AS approved,
                COUNT(DISTINCT identifier_hash)
                  FILTER (WHERE status = 'rejected') AS rejected,
                COUNT(DISTINCT identifier_hash)
                  FILTER (WHERE status = 'deferred') AS deferred
            FROM drake_identity_match_candidates
        """)).mappings().one()

    identities = []
    by_hash = {}

    for row in rows:
        identifier_hash = row["identifier_hash"]

        if identifier_hash not in by_hash:
            identity = {
                "identifier_hash": identifier_hash,
                "first_year": row["first_year"],
                "last_year": row["last_year"],
                "return_count": row["return_count"],
                "taxpayer_name": row["taxpayer_name"],
                "spouse_name": row["spouse_name"],
                "primary_person_id": row["primary_person_id"],
                "status": row["status"],
                "candidates": [],
            }
            identities.append(identity)
            by_hash[identifier_hash] = identity

        by_hash[identifier_hash]["candidates"].append(dict(row))

    return templates.TemplateResponse(
        request=request,
        name="matches/drake_identities.html",
        context={
            "status": status,
            "saved": saved,
            "identities": identities,
            "summary": dict(summary),
        },
    )


@router.post("/matches/drake/{identifier_hash}/{person_id}/approve")
def approve_drake_identity(identifier_hash: str, person_id: int):
    with engine.begin() as connection:
        candidate = connection.execute(text("""
            SELECT id, score
            FROM drake_identity_match_candidates
            WHERE identifier_hash = :identifier_hash
              AND person_id = :person_id
        """), {
            "identifier_hash": identifier_hash,
            "person_id": person_id,
        }).mappings().first()

        if not candidate:
            return HTMLResponse(
                "<h1>Drake match candidate not found</h1>",
                status_code=404,
            )

        identity_exists = connection.execute(text("""
            SELECT 1
            FROM drake_identity
            WHERE identifier_hash = :identifier_hash
        """), {
            "identifier_hash": identifier_hash,
        }).scalar_one_or_none()

        if not identity_exists:
            return HTMLResponse(
                "<h1>Drake identity not found</h1>",
                status_code=404,
            )

        connection.execute(text("""
            UPDATE drake_identity
            SET
                primary_person_id = :person_id,
                confidence = :score
            WHERE identifier_hash = :identifier_hash
        """), {
            "identifier_hash": identifier_hash,
            "person_id": person_id,
            "score": candidate["score"],
        })

        connection.execute(text("""
            INSERT INTO person_source_links (
                person_id,
                source_contact_id,
                match_method,
                match_score,
                confirmed
            )
            SELECT
                :person_id,
                sc.id,
                'drake_identity_review',
                :score,
                TRUE
            FROM source_contacts sc
            WHERE sc.source_system = 'Drake'
              AND sc.raw_data->>'identifier_hash' = :identifier_hash
            ON CONFLICT ON CONSTRAINT uq_person_source_link
            DO NOTHING
        """), {
            "identifier_hash": identifier_hash,
            "person_id": person_id,
            "score": candidate["score"],
        })

        connection.execute(text("""
            UPDATE drake_identity_match_candidates
            SET
                status = CASE
                    WHEN person_id = :person_id THEN 'approved'
                    ELSE 'rejected'
                END,
                reviewed_at = now(),
                updated_at = now()
            WHERE identifier_hash = :identifier_hash
        """), {
            "identifier_hash": identifier_hash,
            "person_id": person_id,
        })

    return RedirectResponse(
        "/matches/drake?status=pending&saved=1",
        status_code=303,
    )


@router.post("/matches/drake/{identifier_hash}/reject")
def reject_drake_identity(identifier_hash: str):
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE drake_identity_match_candidates
            SET
                status = 'rejected',
                reviewed_at = now(),
                updated_at = now()
            WHERE identifier_hash = :identifier_hash
              AND status IN ('pending', 'deferred')
        """), {
            "identifier_hash": identifier_hash,
        })

    return RedirectResponse(
        "/matches/drake?status=pending&saved=1",
        status_code=303,
    )


@router.post("/matches/drake/{identifier_hash}/defer")
def defer_drake_identity(identifier_hash: str):
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE drake_identity_match_candidates
            SET
                status = 'deferred',
                reviewed_at = now(),
                updated_at = now()
            WHERE identifier_hash = :identifier_hash
              AND status = 'pending'
        """), {
            "identifier_hash": identifier_hash,
        })

    return RedirectResponse(
        "/matches/drake?status=pending&saved=1",
        status_code=303,
    )


@router.get("/matches")
def match_review_page(request: Request, status: str = "all"):
    if not MERGE_PLAN_PATH.exists():
        return render_error(request, 404, detail="Match report not found. Run the matching plan first.")

    review_groups = _review_groups()
    group_keys = [_group_key(_record_ids(row)) for row in review_groups]

    if group_keys:
        with engine.connect() as connection:
            saved_decisions = connection.execute(
                select(
                    match_review_decisions.c.group_key,
                    match_review_decisions.c.decision,
                ).where(
                    match_review_decisions.c.group_key.in_(group_keys)
                )
            ).mappings().all()
    else:
        saved_decisions = []

    decision_by_key = {row["group_key"]: row["decision"] for row in saved_decisions}

    approved_count = sum(d == "approved" for d in decision_by_key.values())
    rejected_count = sum(d == "rejected" for d in decision_by_key.values())
    skipped_count = sum(d == "skipped" for d in decision_by_key.values())
    decided_count = approved_count + rejected_count + skipped_count
    remaining_count = max(len(review_groups) - decided_count, 0)
    completion_percent = (
        decided_count / len(review_groups) * 100 if review_groups else 0
    )

    allowed_statuses = {"all", "pending", "approved", "rejected", "skipped"}
    if status not in allowed_statuses:
        status = "all"

    status_labels = {
        "approved": "Approved",
        "rejected": "Not a Duplicate",
        "skipped": "Skipped",
        "pending": "Pending Review",
    }

    groups = []
    for group_number, row in enumerate(review_groups, start=1):
        row_decision = decision_by_key.get(_group_key(_record_ids(row)), "pending")
        if status != "all" and row_decision != status:
            continue
        record_ids = [value.strip() for value in row.get("record_ids", "").split("|") if value.strip()]
        groups.append({
            "group_number": group_number,
            "decision": row_decision,
            "status_label": status_labels.get(row_decision, "Pending Review"),
            "names": row.get("names", ""),
            "sources": row.get("source_systems", ""),
            "email": row.get("email", ""),
            "phone": row.get("phone", ""),
            "review_reason": row.get("review_reason", ""),
            "record_ids": record_ids,
        })

    return templates.TemplateResponse(
        request=request,
        name="matches/list.html",
        context={
            "status": status,
            "groups": groups,
            "summary": {
                "total": len(review_groups),
                "approved": approved_count,
                "rejected": rejected_count,
                "skipped": skipped_count,
                "remaining": remaining_count,
                "completion_percent": completion_percent,
            },
        },
    )
