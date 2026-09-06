"""Non-client exclusion lane — take a file that is not client paperwork out of the review queue.

WHY THIS EXISTS
---------------
After the deferred-ownership lane parks the 15,729 documents whose owner is not currently provable,
18,713 remain in the actionable backlog. A read-only production audit showed that 3,251 of those are
not client documents at all: web pages saved from a browser, Windows help files, fonts, stylesheets,
shortcuts, and `.js.download` assets. They can never be owned by a client because they are not
client paperwork. Counting them as outstanding review work makes the backlog permanently
unfinishable for reasons that have nothing to do with the firm's filing.

WHAT IT IS NOT
--------------
Not a delete, not an archive, not an owner, and NOT a statement that a document is unreadable. This
writes exactly one column plus one ``tags`` key. ``documents.status``, ``archived``, ``deleted_at``,
the ownership columns, ``document_sources``, ``document_ocr`` and every ``document_facts`` row are
untouched, and the file itself is never moved. The row stays visible to staff search, and one call
puts it back.

WHY ``review_status`` AND ITS OWN SENTINEL
------------------------------------------
Same reasoning as ``app.services.document_deferral``: ``documents.status`` is CHECK-constrained and
``archived`` would HIDE the row rather than classify it. ``review_status`` is the queue-state
column, and this lane gets its OWN sentinel rather than reusing the deferral one — "we cannot prove
the owner yet" and "this is not a client document" are different facts, and a backlog that cannot
tell them apart cannot tell recoverable work from work that never existed.

WHY AN ALLOW-LIST, AND WHY IT IS NARROW
---------------------------------------
The audit found that the obvious rule — "exclude everything ``is_intelligence_eligible`` rejects" —
would have swept in 252 REAL client documents: tax forms saved with no extension at all
(``USA_941_2020_2``, ``2021 1099-G``, ``Michelle's 1099-C``), ``.zip`` statement archives, Apple
``.numbers`` tax workbooks, and QuickBooks/Access company files. Those are client data in formats
this platform cannot currently read, which is a DIFFERENT problem with a different remedy.

So eligibility here is a closed allow-list of file families that are provably software or web
artifacts, plus three individually named rows. Anything else — an unknown extension, a bare name, a
container, a format nobody has classified yet — FAILS CLOSED and stays in the actionable queue.
Widening the lane must be an edit to :data:`APPROVED_EXTENSIONS`, :data:`WEB_ASSET_DOWNLOAD_RE` or
:data:`APPROVED_ARTIFACT_DOCUMENTS`, which is exactly the reviewable one-line change it should be.

The 269 documents whose extraction failed as "unsupported" are deliberately NOT in scope: they are
``.pdf``, ``.doc``, ``.csv``, ``.docx``, ``.xlsb`` client paperwork the current engine cannot read.

EVERY GUARD IS RE-CHECKED IN THE WRITE
--------------------------------------
The eligibility checks are read first for a clear message, then restated inside the UPDATE's WHERE
clause — including the filename the rule was derived from and the owner-proposal route. A document
that gained an owner, was renamed, or was re-proposed between the read and the write is simply not
updated, and the caller is told so.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from sqlalchemy import text

from app.db import engine
from app.services.document_owner_proposal import PERMANENT_REJECT_DOCUMENT_IDS

#: The ``documents.review_status`` sentinel. One value, one meaning.
EXCLUDED_REVIEW_STATUS = "excluded_nonclient"

#: What ``review_status`` returns to when an exclusion is reversed — the column's own default.
RESTORED_REVIEW_STATUS = "not_required"

#: The ``documents.tags`` key carrying the exclusion's evidence.
TAGS_KEY = "nonclient_exclusion"

#: The ONLY prior ``review_status`` values a document may carry and still be excluded. An allow-list
#: for the same reason the deferral lane uses one: a row already carrying real review state
#: (``pending``, ``in_review``, ``flagged``) has information that this write would destroy and that
#: restoration could not recover, so it is refused rather than assumed safe.
EXCLUDABLE_REVIEW_STATUSES: frozenset[str] = frozenset({"", "not_required"})

#: The owner-proposal route a rule-A candidate must currently carry. A ``.html`` file that somehow
#: routed HIGH is a proposal worth reading, not an artifact to sweep away.
REQUIRED_ROUTE = "UNSUPPORTED"

REASON_TECHNICAL_EXTENSION = "technical_extension"
REASON_WEB_ASSET_DOWNLOAD = "web_asset_download"
REASON_NAMED_ARTIFACT = "named_artifact"

#: The ONLY reasons this implementation will write.
EXCLUDABLE_REASONS: frozenset[str] = frozenset(
    {REASON_TECHNICAL_EXTENSION, REASON_WEB_ASSET_DOWNLOAD, REASON_NAMED_ARTIFACT})

#: Rule A — file extensions that are provably software, web or OS artifacts rather than paperwork.
#: Every member was verified against the production census. Formats that merely CANNOT BE READ
#: (pdf, doc, docx, csv, xlsb, msg, pptx, zip, numbers, qbw, qbb, mdb, oxps, rpt) are deliberately
#: absent: those are client documents awaiting a better extractor, not artifacts.
APPROVED_EXTENSIONS: frozenset[str] = frozenset({
    "html", "htm", "hlp", "ttf", "otf", "tex", "lnk", "config",
    "css", "aspx", "map", "xsl", "json", "prf", "url", "nd",
})

#: Rule A (second form) — a browser-saved web asset. The extension is ``.download``, so the family is
#: carried by the SECOND-level suffix. A bare ``.download`` (or ``report.pdf.download``) does NOT
#: match: 75 of those exist in production and their content is unknown, so they stay actionable.
WEB_ASSET_DOWNLOAD_RE = re.compile(
    r"\.(js|css|html?|png|gif|jpg|svg|woff2?|ttf|json|map)\.download$", re.IGNORECASE)

#: Rule B — three individually audited rows that carry no owner proposal at all, pinned by BOTH id
#: and expected filename so neither alone can classify the wrong row.
APPROVED_ARTIFACT_DOCUMENTS: dict[int, str] = {
    17155: "thumbs.db",
    17139: "thumbs.db",
    119997: "outlook-5rp5rfei",
}


class ExclusionError(ValueError):
    """The requested exclusion is not permitted (bad reason, or an ineligible document)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _rejects() -> list[int]:
    return sorted(PERMANENT_REJECT_DOCUMENT_IDS)


def _normalized_review_status(value) -> str:
    """``review_status`` reduced to the form the allow-list is written in (NULL -> ``""``)."""
    return str(value or "").strip().lower()


def extension_of(name: str | None) -> str:
    """The lowercase final extension, or ``""`` when the name carries none."""
    name = (name or "").strip()
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def exclusion_rule(document_id: int, name: str | None) -> str | None:
    """The reason this file is a non-client artifact, or ``None`` — the whole allow-list, in order.

    ``None`` is the fail-closed answer and the common one: an extension nobody has classified, a
    container, or a bare filename is NOT excludable here.
    """
    clean = (name or "").strip()
    expected = APPROVED_ARTIFACT_DOCUMENTS.get(int(document_id))
    if expected is not None and clean.lower() == expected:
        return REASON_NAMED_ARTIFACT
    # Checked before the extension test: a web asset's own extension is ``download``, which is
    # deliberately NOT in APPROVED_EXTENSIONS because a bare .download could be anything.
    if WEB_ASSET_DOWNLOAD_RE.search(clean):
        return REASON_WEB_ASSET_DOWNLOAD
    if extension_of(clean) in APPROVED_EXTENSIONS:
        return REASON_TECHNICAL_EXTENSION
    return None


# --- read helpers -------------------------------------------------------------------------------

_DOC_SQL = text("""
    SELECT d.id, d.original_name, d.content_type,
           d.person_id, d.household_id, d.organization_id,
           d.status, d.deleted_at, d.archived, d.review_status,
           d.tags -> :tags_key AS exclusion,
           f.fact_value::jsonb ->> 'route' AS route
      FROM documents d
      LEFT JOIN document_facts f
             ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
     WHERE d.id = :document_id
""")


def _load(conn, document_id: int):
    return conn.execute(_DOC_SQL, {"document_id": int(document_id),
                                   "tags_key": TAGS_KEY}).mappings().first()


def eligibility(conn, document_id: int) -> dict:
    """Why this document may or may not be excluded. Read-only, and the rules the write restates."""
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
    if row["review_status"] == EXCLUDED_REVIEW_STATUS:
        return {"eligible": False, "reason_code": "already_excluded", "row": row}
    if _normalized_review_status(row["review_status"]) not in EXCLUDABLE_REVIEW_STATUSES:
        return {"eligible": False, "reason_code": "review_status_not_excludable",
                "review_status": row["review_status"], "row": row}
    reason = exclusion_rule(row["id"], row["original_name"])
    if reason is None:
        # The fail-closed exit, and the one most rows take. An unrecognised extension is NOT
        # evidence that a file is an artifact — 252 production rows proved the opposite.
        return {"eligible": False, "reason_code": "not_an_approved_artifact",
                "extension": extension_of(row["original_name"]), "row": row}
    if reason != REASON_NAMED_ARTIFACT and row["route"] != REQUIRED_ROUTE:
        # A rule-A file must still be the unreadable thing the audit measured. If it has since
        # produced a real proposal, that proposal is worth a human's time.
        return {"eligible": False, "reason_code": "route_not_excludable",
                "route": row["route"], "row": row}
    return {"eligible": True, "reason_code": None, "reason": reason,
            "route": row["route"], "row": row}


# --- the two writes -----------------------------------------------------------------------------

_EXCLUDE_SQL = text("""
    UPDATE documents
       SET review_status = :excluded,
           tags = CASE WHEN jsonb_typeof(tags) = 'object' THEN tags || CAST(:payload AS jsonb)
                       ELSE CAST(:payload AS jsonb) END
     WHERE id = :document_id
       AND person_id IS NULL AND household_id IS NULL AND organization_id IS NULL
       AND status <> 'deleted' AND deleted_at IS NULL AND archived = false
       AND review_status IS DISTINCT FROM :excluded
       AND coalesce(lower(btrim(review_status)), '') = ANY(:safe_review_statuses)
       AND NOT (id = ANY(:rejects))
       AND lower(btrim(coalesce(original_name, ''))) = :expected_name
       AND (:reason = :named_artifact
            OR EXISTS (SELECT 1 FROM document_facts f
                        WHERE f.document_id = documents.id
                          AND f.fact_type = 'owner_proposal' AND f.is_current
                          AND f.fact_value::jsonb ->> 'route' = :required_route))
    RETURNING id
""")

_RESTORE_SQL = text("""
    UPDATE documents
       SET review_status = :restored,
           tags = CASE WHEN jsonb_typeof(tags) = 'object' THEN tags - :tags_key ELSE tags END
     WHERE id = :document_id
       AND review_status = :excluded
    RETURNING id
""")


def exclude_document(document_id: int, *, reason: str | None = None, actor_user_id=None,
                     request_id: str | None = None, conn=None, dry_run: bool = False) -> dict:
    """Classify ONE document as a non-client artifact.

    ``reason`` is optional: omitted, it is derived from the allow-list. Supplied, it must be in
    :data:`EXCLUDABLE_REASONS` AND agree with the derived reason — a caller cannot label a client
    document ``technical_extension`` to get it out of the queue.

    Idempotent: an already-excluded document returns ``{excluded: False, outcome:
    'already_excluded'}`` and writes nothing.
    """
    if reason is not None and reason not in EXCLUDABLE_REASONS:
        raise ExclusionError(
            f"reason {reason!r} is not excludable; expected one of {sorted(EXCLUDABLE_REASONS)}")

    own = engine.begin() if conn is None else None
    close = conn is None
    connection = own.__enter__() if close else conn
    try:
        check = eligibility(connection, document_id)
        base = {"document_id": int(document_id), "excluded": False, "dry_run": dry_run,
                "route": check.get("route")}
        if not check["eligible"]:
            return {**base, "outcome": check["reason_code"], "reason": None}

        derived = check["reason"]
        if reason is not None and reason != derived:
            raise ExclusionError(
                f"document {document_id} matches rule {derived!r}, not {reason!r}")
        effective = derived
        name = (check["row"]["original_name"] or "").strip()

        if dry_run:
            return {**base, "outcome": "would_exclude", "reason": effective}

        payload = {TAGS_KEY: {
            "reason": effective,
            "matched_name": name,
            "extension": extension_of(name),
            "route": check.get("route"),
            "excluded_at": _now().isoformat(),
            "excluded_by_user_id": actor_user_id,
        }}
        updated = connection.execute(_EXCLUDE_SQL, {
            "document_id": int(document_id), "excluded": EXCLUDED_REVIEW_STATUS,
            "payload": json.dumps(payload), "rejects": _rejects(),
            "safe_review_statuses": sorted(EXCLUDABLE_REVIEW_STATUSES),
            "expected_name": name.lower(), "reason": effective,
            "named_artifact": REASON_NAMED_ARTIFACT,
            "required_route": REQUIRED_ROUTE}).first()
        if updated is None:
            # Lost a race with an assignment, a rename, or a re-proposal.
            return {**base, "outcome": "no_longer_eligible", "reason": None}

        _audit(connection, "document.nonclient_excluded", document_id, actor_user_id, request_id,
               {"reason": effective, "route": check.get("route"), "name": name})
        return {**base, "excluded": True, "outcome": "excluded", "reason": effective}
    finally:
        if close:
            own.__exit__(None, None, None)


def restore_document(document_id: int, *, actor_user_id=None, request_id: str | None = None,
                     conn=None, dry_run: bool = False) -> dict:
    """Reverse an exclusion, returning the document to the actionable queue.

    The reversibility half of the lane: one call, no owner assigned, no provenance consulted.
    Idempotent — a document that is not excluded returns ``{restored: False, outcome:
    'not_excluded'}`` and writes nothing.
    """
    own = engine.begin() if conn is None else None
    close = conn is None
    connection = own.__enter__() if close else conn
    try:
        row = _load(connection, document_id)
        base = {"document_id": int(document_id), "restored": False, "dry_run": dry_run}
        if row is None:
            return {**base, "outcome": "not_found"}
        if row["review_status"] != EXCLUDED_REVIEW_STATUS:
            return {**base, "outcome": "not_excluded"}
        if dry_run:
            return {**base, "outcome": "would_restore"}
        updated = connection.execute(_RESTORE_SQL, {
            "document_id": int(document_id), "restored": RESTORED_REVIEW_STATUS,
            "excluded": EXCLUDED_REVIEW_STATUS, "tags_key": TAGS_KEY}).first()
        if updated is None:
            return {**base, "outcome": "no_longer_excluded"}
        _audit(connection, "document.nonclient_exclusion_reversed", document_id, actor_user_id,
               request_id, {})
        return {**base, "restored": True, "outcome": "restored"}
    finally:
        if close:
            own.__exit__(None, None, None)


def _audit(connection, action, document_id, actor_user_id, request_id, metadata):
    """Audit in the CALLER's transaction, so the ledger entry and the change commit together."""
    from app.security.audit import write_audit_event
    write_audit_event(
        action=action, entity_type="document", entity_id=document_id,
        actor_user_id=actor_user_id, request_id=request_id or f"nonclient-{document_id}",
        metadata={"document_id": int(document_id), **(metadata or {})}, conn=connection)


# --- read side ----------------------------------------------------------------------------------

_SUMMARY_SQL = text(f"""
    SELECT coalesce(tags -> '{TAGS_KEY}' ->> 'reason', '(unrecorded)') AS reason, count(*) AS n
      FROM documents
     WHERE review_status = :excluded
       AND status <> 'deleted' AND deleted_at IS NULL AND archived = false
     GROUP BY 1 ORDER BY 2 DESC
""")


def excluded_counts(conn=None) -> dict[str, int]:
    """``{reason: n, ..., total: n}`` over the live non-client exclusion lane."""
    def _run(c):
        rows = c.execute(_SUMMARY_SQL, {"excluded": EXCLUDED_REVIEW_STATUS}).mappings().all()
        out = {r["reason"]: int(r["n"]) for r in rows}
        out["total"] = sum(out.values())
        return out

    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        return _run(c)
