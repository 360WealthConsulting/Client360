"""Deferred-ownership lane — park a document whose owner is not currently provable.

WHY THIS EXISTS
---------------
34,445 of the 73,207 active documents carry no owner. That number is the master review backlog, it
has never gone down, and while it is the completion metric the document filing project can never
finish. Most of it is not work anyone can do today: 15,237 documents produced a ``NO_MATCH`` owner
proposal (their content names nobody in the canonical directory) and 492 have no proposal at all.
No amount of reviewer time resolves those — the evidence to resolve them does not exist yet.

So they are DEFERRED rather than left to block: still real, still searchable, still carrying every
byte of their provenance, but out of the actionable queue and out of the completion denominator.
Deferral is a filing decision about the QUEUE, never a statement about the document, and it is
reversible in one call.

WHAT IT IS NOT
--------------
Not a delete, not an archive, not an owner. ``person_id`` / ``household_id`` / ``organization_id``
stay NULL — deferral never guesses. ``document_sources``, ``source_external_id``, ``storage_uri``,
``sha256``, ``document_ocr`` and every ``document_facts`` owner proposal are untouched: this writes
exactly one column plus one ``tags`` key, and nothing else in the platform.

WHY ``review_status`` AND NOT ``status`` OR ``archived``
--------------------------------------------------------
``documents.status`` is CHECK-constrained to a lifecycle enum whose ``'review'`` value is ALREADY
counted as ``pending_review`` by ``document_intelligence.panels``; reusing it would corrupt that
panel and collide with the platform's transition map. ``documents.archived`` is worse: archived rows
are already excluded from the backlog metric, so deferring through it would HIDE these documents
rather than classify them, and would represent live client paperwork as filing the firm has put
away. ``review_status`` is a free-text column with no CHECK constraint, carrying two values across
121,806 rows (``not_required`` ×121,805, ``pending`` ×1) and written by nothing in production code.
Extending it needs no migration and displaces nothing — and it displaces nothing precisely because
:data:`DEFERRABLE_REVIEW_STATUSES` refuses any row already carrying review state, including that one
``pending`` row. Deferral only ever overwrites the column's own default.

WHY ``UNSUPPORTED`` IS NOT DEFERRABLE HERE
------------------------------------------
11,001 unowned documents route ``UNSUPPORTED`` — no text could be extracted. That is not evidence
that their owner is unprovable; the production OCR backend is not wired in this repo
(``document_ocr.default_extractor`` raises), so a large share of them have simply never been read.
Deferring them would record "we could not resolve this" when the truth is "we have not looked yet".
They stay in the actionable queue until OCR lands, and get their own reason code when they are
revisited. :data:`ROUTE_REASON` is the single place that decision is expressed.

EVERY GUARD IS RE-CHECKED IN THE WRITE
--------------------------------------
The eligibility checks below are read first for a clear error message, then restated inside the
UPDATE's WHERE clause, so a concurrent assignment between the read and the write loses rather than
being overwritten. A document that gained an owner in that window is simply not updated, and the
caller is told ``already_owned``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import text

from app.db import engine
from app.services.document_eligibility import is_intelligence_eligible
from app.services.document_owner_proposal import PERMANENT_REJECT_DOCUMENT_IDS

#: The ``documents.review_status`` sentinel. One value, one meaning.
DEFERRED_REVIEW_STATUS = "deferred_ownership"

#: What ``review_status`` returns to when a deferral is cleared — the column's own default.
SETTLED_REVIEW_STATUS = "not_required"

#: The ONLY prior ``review_status`` values a document may carry and still enter the lane.
#:
#: Deferral writes ``review_status``, and promotion writes ``not_required`` back — it cannot restore
#: a value it never recorded. So a row carrying REAL review state (``pending``, ``in_review``,
#: ``flagged``, ``hold``, ``rejected``, or anything a future workflow introduces) must never be
#: deferred: parking it would destroy that state, and promoting it would silently settle a review
#: that nobody performed. Production carries one such row (``pending`` ×1 against
#: ``not_required`` ×121,805), and one is enough — the backlog is not worth a lost review.
#:
#: This is an ALLOW-list precisely so it fails closed: an unrecognised value is refused rather than
#: assumed harmless, which means a new review state added elsewhere in the platform cannot quietly
#: become deferrable without someone editing this line. The members are the states that carry no
#: information to lose — the column default, plus the empty/NULL forms of "nothing recorded".
#: Promotion normalising those to ``not_required`` is the column's own default, not a lost verdict.
DEFERRABLE_REVIEW_STATUSES: frozenset[str] = frozenset({"", "not_required"})


def _normalized_review_status(value) -> str:
    """``review_status`` reduced to the form the allow-list is written in (NULL -> ``""``)."""
    return str(value or "").strip().lower()

#: The ``documents.tags`` key carrying the deferral's evidence.
TAGS_KEY = "deferred_ownership"

REASON_NO_MATCH = "no_match"
REASON_NO_PROPOSAL = "no_proposal"

#: The ONLY reasons this implementation will write. A reason outside this set is refused rather
#: than recorded, so the lane cannot quietly grow a meaning nobody reviewed.
DEFERRABLE_REASONS: frozenset[str] = frozenset({REASON_NO_MATCH, REASON_NO_PROPOSAL})

#: Owner-proposal route -> deferral reason. The absence of ``UNSUPPORTED`` is the scope decision
#: described in the module docstring, and it is expressed HERE so that widening the lane is a
#: one-line, reviewable change rather than an edit spread across the service and its tooling.
#: ``None`` is the route of a document that has no current ``owner_proposal`` fact at all.
ROUTE_REASON: dict[str | None, str] = {
    "NO_MATCH": REASON_NO_MATCH,
    None: REASON_NO_PROPOSAL,
}

#: Routes that explicitly may NOT be deferred, kept as a named set so the refusal message can say
#: which one blocked and a reader can see that HIGH/MEDIUM/AMBIGUOUS are deliberately excluded —
#: those carry a proposed owner and are exactly the work a reviewer can finish today.
NON_DEFERRABLE_ROUTES: frozenset[str] = frozenset(
    {"UNSUPPORTED", "HIGH", "MEDIUM", "AMBIGUOUS", "NEW_CLIENT_CANDIDATE", "ERROR", "SKIPPED"})


class DeferralError(ValueError):
    """The requested deferral is not permitted (bad reason, or an ineligible document)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _rejects() -> list[int]:
    return sorted(PERMANENT_REJECT_DOCUMENT_IDS)


# --- read helpers -------------------------------------------------------------------------------

_DOC_SQL = text("""
    SELECT d.id, d.original_name, d.person_id, d.household_id, d.organization_id,
           d.status, d.deleted_at, d.archived, d.review_status,
           d.content_type,
           d.tags -> :tags_key AS deferral,
           f.fact_value::jsonb ->> 'route' AS route
      FROM documents d
      LEFT JOIN document_facts f
             ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
     WHERE d.id = :document_id
""")


def _load(conn, document_id: int):
    return conn.execute(_DOC_SQL, {"document_id": int(document_id), "tags_key": TAGS_KEY}).mappings().first()


def reason_for_route(route: str | None) -> str | None:
    """The deferral reason a proposal route earns, or None when the route may not be deferred.

    A route this implementation has never heard of returns None — unknown is refused, not guessed.
    """
    if route in NON_DEFERRABLE_ROUTES:
        return None
    return ROUTE_REASON.get(route)


def eligibility(conn, document_id: int) -> dict:
    """Why this document may or may not be deferred. Read-only, and the same rules the write uses."""
    row = _load(conn, document_id)
    if row is None:
        return {"eligible": False, "reason_code": "not_found"}
    if int(row["id"]) in PERMANENT_REJECT_DOCUMENT_IDS:
        return {"eligible": False, "reason_code": "permanent_reject", "row": row}
    if row["status"] == "deleted" or row["deleted_at"] is not None:
        return {"eligible": False, "reason_code": "deleted", "row": row}
    if row["archived"]:
        return {"eligible": False, "reason_code": "archived", "row": row}
    if row["person_id"] is not None or row["household_id"] is not None \
            or row["organization_id"] is not None:
        return {"eligible": False, "reason_code": "already_owned", "row": row}
    if row["review_status"] == DEFERRED_REVIEW_STATUS:
        return {"eligible": False, "reason_code": "already_deferred", "row": row}
    if _normalized_review_status(row["review_status"]) not in DEFERRABLE_REVIEW_STATUSES:
        # Fails closed: this document is already carrying review state, and deferral has nowhere to
        # put it. See :data:`DEFERRABLE_REVIEW_STATUSES` for why an allow-list rather than a list of
        # blocked values. Checked after ``already_deferred`` so a re-run still reports the lane it
        # is in rather than this refusal.
        return {"eligible": False, "reason_code": "review_status_not_deferrable",
                "review_status": row["review_status"], "row": row}
    if not is_intelligence_eligible(row["original_name"], row["content_type"]):
        # A Thumbs.db or an Outlook temp file is not a client document whose owner is unprovable —
        # it is not a client document at all. Deferral means "valid paperwork, owner not currently
        # provable", so a program/OS artifact belongs in EXCLUDED and is refused here rather than
        # quietly diluting the lane. Production carries three such rows in the candidate set.
        return {"eligible": False, "reason_code": "not_a_client_document", "row": row}
    reason = reason_for_route(row["route"])
    if reason is None:
        return {"eligible": False, "reason_code": "route_not_deferrable",
                "route": row["route"], "row": row}
    return {"eligible": True, "reason_code": None, "reason": reason, "route": row["route"], "row": row}


# --- the two writes -----------------------------------------------------------------------------

_DEFER_SQL = text("""
    UPDATE documents
       SET review_status = :deferred,
           tags = CASE WHEN jsonb_typeof(tags) = 'object' THEN tags || CAST(:payload AS jsonb)
                       ELSE CAST(:payload AS jsonb) END
     WHERE id = :document_id
       AND person_id IS NULL AND household_id IS NULL AND organization_id IS NULL
       AND status <> 'deleted' AND deleted_at IS NULL AND archived = false
       AND review_status IS DISTINCT FROM :deferred
       AND coalesce(lower(btrim(review_status)), '') = ANY(:safe_review_statuses)
       AND NOT (id = ANY(:rejects))
    RETURNING id
""")

_PROMOTE_SQL = text("""
    UPDATE documents
       SET review_status = :settled,
           tags = CASE WHEN jsonb_typeof(tags) = 'object' THEN tags - :tags_key ELSE tags END
     WHERE id = :document_id
       AND review_status = :deferred
    RETURNING id
""")


def defer_document(document_id: int, *, reason: str | None = None, actor_user_id=None,
                   request_id: str | None = None, conn=None, dry_run: bool = False) -> dict:
    """Move ONE unowned document into the deferred-ownership lane.

    ``reason`` is optional: omitted, it is derived from the document's current owner-proposal route.
    Supplied, it must both be in :data:`DEFERRABLE_REASONS` and agree with the derived reason — a
    caller cannot label a ``HIGH`` proposal ``no_match`` to get it out of the queue.

    Idempotent: a document already in the lane returns ``{deferred: False, outcome:
    'already_deferred'}`` and writes nothing, so a re-run of the backfill is a no-op.

    Returns ``{document_id, deferred, outcome, reason, route, dry_run}``. Raises
    :class:`DeferralError` only for a reason that is not permitted; an ineligible DOCUMENT is
    reported in the result rather than raised, because a batch must be able to skip and continue.
    """
    if reason is not None and reason not in DEFERRABLE_REASONS:
        raise DeferralError(
            f"reason {reason!r} is not deferrable; expected one of {sorted(DEFERRABLE_REASONS)}")

    own = engine.begin() if conn is None else None
    close = conn is None
    connection = own.__enter__() if close else conn
    try:
        check = eligibility(connection, document_id)
        base = {"document_id": int(document_id), "deferred": False, "dry_run": dry_run,
                "route": check.get("route")}
        if not check["eligible"]:
            return {**base, "outcome": check["reason_code"], "reason": None}

        derived = check["reason"]
        if reason is not None and reason != derived:
            raise DeferralError(
                f"document {document_id} routes {check.get('route')!r}, which is reason "
                f"{derived!r}, not {reason!r}")
        effective = derived

        if dry_run:
            return {**base, "outcome": "would_defer", "reason": effective}

        payload = {TAGS_KEY: {
            "reason": effective,
            "route": check.get("route"),
            "deferred_at": _now().isoformat(),
            "deferred_by_user_id": actor_user_id,
        }}
        updated = connection.execute(_DEFER_SQL, {
            "document_id": int(document_id), "deferred": DEFERRED_REVIEW_STATUS,
            "payload": json.dumps(payload), "rejects": _rejects(),
            "safe_review_statuses": sorted(DEFERRABLE_REVIEW_STATUSES)}).first()
        if updated is None:
            # Lost a race with an assignment (or another deferral) between the read and the write.
            return {**base, "outcome": "no_longer_eligible", "reason": None}

        _audit(connection, "document.ownership_deferred", document_id, actor_user_id, request_id,
               {"reason": effective, "route": check.get("route")})
        return {**base, "deferred": True, "outcome": "deferred", "reason": effective}
    finally:
        if close:
            own.__exit__(None, None, None)


def promote_document(document_id: int, *, actor_user_id=None, request_id: str | None = None,
                     conn=None, dry_run: bool = False) -> dict:
    """Clear a deferral, returning the document to the actionable review queue.

    This does NOT assign an owner — promotion to FILED is the existing atomic ownership write
    (``households.resolve_document_ownership``), which clears the deferral in the same guarded
    UPDATE. This function is the other direction: "this is workable again, put it back in the
    queue", used by the backfill's rollback and by a reviewer who disagrees with a deferral.

    Idempotent: a document that is not deferred returns ``{promoted: False, outcome:
    'not_deferred'}`` and writes nothing.
    """
    own = engine.begin() if conn is None else None
    close = conn is None
    connection = own.__enter__() if close else conn
    try:
        row = _load(connection, document_id)
        base = {"document_id": int(document_id), "promoted": False, "dry_run": dry_run}
        if row is None:
            return {**base, "outcome": "not_found"}
        if row["review_status"] != DEFERRED_REVIEW_STATUS:
            return {**base, "outcome": "not_deferred"}
        if dry_run:
            return {**base, "outcome": "would_promote"}
        updated = connection.execute(_PROMOTE_SQL, {
            "document_id": int(document_id), "settled": SETTLED_REVIEW_STATUS,
            "deferred": DEFERRED_REVIEW_STATUS, "tags_key": TAGS_KEY}).first()
        if updated is None:
            return {**base, "outcome": "no_longer_deferred"}
        _audit(connection, "document.ownership_deferral_cleared", document_id, actor_user_id,
               request_id, {"previous_reason": (row["deferral"] or {}).get("reason")
                            if isinstance(row["deferral"], dict) else None})
        return {**base, "promoted": True, "outcome": "promoted"}
    finally:
        if close:
            own.__exit__(None, None, None)


def _audit(connection, action, document_id, actor_user_id, request_id, metadata):
    """Audit in the CALLER's transaction, so the ledger entry and the change commit together."""
    from app.security.audit import write_audit_event
    write_audit_event(
        action=action, entity_type="document", entity_id=document_id,
        actor_user_id=actor_user_id, request_id=request_id or f"deferral-{document_id}",
        metadata={"document_id": int(document_id), **(metadata or {})}, conn=connection)


# --- read side ----------------------------------------------------------------------------------

_SUMMARY_SQL = text(f"""
    SELECT coalesce(tags -> '{TAGS_KEY}' ->> 'reason', '(unrecorded)') AS reason, count(*) AS n
      FROM documents
     WHERE review_status = :deferred
       AND status <> 'deleted' AND deleted_at IS NULL AND archived = false
     GROUP BY 1 ORDER BY 2 DESC
""")


def deferred_counts(conn=None) -> dict[str, int]:
    """``{reason: count}`` for the live deferred lane, plus ``total``."""
    def _run(c):
        rows = c.execute(_SUMMARY_SQL, {"deferred": DEFERRED_REVIEW_STATUS}).mappings().all()
        out = {r["reason"]: int(r["n"]) for r in rows}
        out["total"] = sum(out.values())
        return out
    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        return _run(c)


_LIST_SQL = text(f"""
    SELECT d.id, d.original_name, d.display_name, d.created_at, d.ocr_status,
           d.tags -> '{TAGS_KEY}' ->> 'reason' AS deferral_reason,
           d.tags -> '{TAGS_KEY}' ->> 'route'  AS deferral_route,
           d.tags -> '{TAGS_KEY}' ->> 'deferred_at' AS deferred_at,
           d.tags ->> 'source_system' AS source_system
      FROM documents d
     WHERE d.review_status = :deferred
       AND d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
       AND (:reason IS NULL OR d.tags -> '{TAGS_KEY}' ->> 'reason' = :reason)
     ORDER BY d.id
     LIMIT :limit
""")


def list_deferred(*, reason: str | None = None, limit: int = 500, conn=None) -> list[dict]:
    """The staff deferred lane, optionally filtered to one reason. Read-only."""
    def _run(c):
        return [dict(r) for r in c.execute(
            _LIST_SQL, {"deferred": DEFERRED_REVIEW_STATUS, "reason": reason,
                        "limit": int(limit)}).mappings()]
    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        return _run(c)
