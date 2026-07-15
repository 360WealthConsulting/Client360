"""Tax exception detectors (Release 0.9.10 / Sprint 5.5, Phase 3).

Detectors reuse existing tax source-of-truth records (missing items, organizers,
questionnaires, engagement letters, reviews, workflow escalations, assignments,
filing events, documents) and translate their conditions into platform Exception
Engine records via the canonical service. They never duplicate source data.

Each detector uses a **stable dedupe key**, so repeated scans are idempotent (the
engine returns the existing open exception). When a source condition **clears**, the
detector auto-closes the matching open exception (resolve if it was being worked,
otherwise cancel); if the condition later recurs, a resolved exception is reopened.

Tax domain only. No scheduler job here (Phase 4), no routes/UI/portal (later).
"""
from datetime import date, datetime, timezone

from sqlalchemy import or_, select

from app.db import (documents, engine, exceptions,
    record_assignments, tax_engagement_letters, tax_engagement_returns, tax_engagements,
    tax_filing_events, tax_missing_items, tax_organizers, tax_questionnaires,
    tax_return_reviews, tax_workflow_links, workflow_escalations)
from app.services import exception_engine as ee

# Return lifecycle states used by several detectors.
_WORKING_STATES = ("ready_to_prepare", "in_preparation", "manager_review", "partner_review",
                   "client_review", "awaiting_efile_authorization", "ready_to_file")
_POST_REVIEW_STATES = ("awaiting_efile_authorization", "ready_to_file", "filed", "accepted",
                       "delivered", "completed")
_CLOSED = ("resolved", "cancelled")


def _now():
    return datetime.now(timezone.utc)


def _age_days(ts, now):
    return (now - ts).days if ts is not None else 0


def _return_scopes(connection):
    """Map return_id -> {person_id, household_id, engagement_id, status, status_entered_at}."""
    rows = connection.execute(
        select(tax_engagement_returns.c.id, tax_engagement_returns.c.status,
               tax_engagement_returns.c.status_entered_at, tax_engagements.c.id.label("engagement_id"),
               tax_engagements.c.person_id, tax_engagements.c.household_id)
        .select_from(tax_engagement_returns.join(tax_engagements))
    ).mappings().all()
    return {r["id"]: dict(r) for r in rows}


def _reconcile(prefix, code, conditions, *, actor_user_id):
    """Raise an exception for each current condition (idempotent) and auto-close any
    open exception in this detector's dedupe family whose condition has cleared.

    ``conditions`` maps ``dedupe_key`` -> scope dict passed to ``raise_exception``.
    """
    raised = 0
    for key, scope in conditions.items():
        ee.raise_exception(code=code, actor_user_id=actor_user_id, source="system",
                           dedupe_key=key, **scope)
        raised += 1
    closed = 0
    with engine.connect() as c:
        stale = c.execute(
            select(exceptions.c.id, exceptions.c.status, exceptions.c.dedupe_key)
            .where(exceptions.c.dedupe_key.like(f"{prefix}%"),
                   exceptions.c.status.notin_(_CLOSED))
        ).mappings().all()
    for row in stale:
        if row["dedupe_key"] in conditions:
            continue
        _auto_resolve(row["id"], row["status"], actor_user_id)
        closed += 1
    return {"raised": raised, "closed": closed}


def _auto_resolve(exception_id, status, actor_user_id, resolution="auto_source_cleared"):
    """System-resolve an exception whose source cleared (so a recurrence reopens it).
    Un-started exceptions are advanced open->in_progress first (both legal transitions)."""
    if status in ("open", "acknowledged", "reopened"):
        ee.begin_work(exception_id, principal=None, actor_user_id=actor_user_id)
    ee.resolve(exception_id, resolution, principal=None, actor_user_id=actor_user_id)


# --- individual detectors -----------------------------------------------------

def detect_missing_documents(*, actor_user_id=None, today=None):
    today = today or date.today()
    with engine.connect() as c:
        scopes = _return_scopes(c)
        rows = c.execute(select(tax_missing_items.c.id, tax_missing_items.c.tax_engagement_return_id,
                                tax_missing_items.c.title)
                         .where(tax_missing_items.c.status == "open",
                                tax_missing_items.c.due_date < today)).mappings().all()
    conditions = {}
    for r in rows:
        s = scopes.get(r["tax_engagement_return_id"])
        if not s:
            continue
        conditions[f"tax:doc_missing:{r['id']}"] = {
            "tax_engagement_return_id": r["tax_engagement_return_id"], "person_id": s["person_id"],
            "household_id": s["household_id"], "related_entity_type": "tax_missing_item",
            "related_entity_id": r["id"], "title": f"Overdue document: {r['title']}"}
    return _reconcile("tax:doc_missing:", "DOC_MISSING_OVERDUE", conditions, actor_user_id=actor_user_id)


def _intake_incomplete(prefix, table, label, code="CLIENT_UNRESPONSIVE", *, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        rows = c.execute(select(table.c.tax_engagement_return_id)
                         .where(table.c.status != "completed")).mappings().all()
    conditions = {}
    for r in rows:
        s = scopes.get(r["tax_engagement_return_id"])
        if not s:
            continue
        rid = r["tax_engagement_return_id"]
        conditions[f"{prefix}{rid}"] = {
            "tax_engagement_return_id": rid, "person_id": s["person_id"],
            "household_id": s["household_id"], "title": f"{label} not completed"}
    return _reconcile(prefix, code, conditions, actor_user_id=actor_user_id)


def detect_missing_organizer(*, actor_user_id=None):
    return _intake_incomplete("tax:organizer:", tax_organizers, "Organizer", actor_user_id=actor_user_id)


def detect_missing_questionnaire(*, actor_user_id=None):
    return _intake_incomplete("tax:questionnaire:", tax_questionnaires, "Questionnaire", actor_user_id=actor_user_id)


def detect_missing_signatures(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        # Engagement letters are engagement-scoped; map to each of the engagement's returns.
        unsigned = set(c.execute(select(tax_engagement_letters.c.tax_engagement_id)
                                 .where(tax_engagement_letters.c.status != "accepted")).scalars())
    conditions = {}
    for rid, s in scopes.items():
        if s["engagement_id"] in unsigned:
            conditions[f"tax:signatures:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "Engagement letter unsigned"}
    return _reconcile("tax:signatures:", "CLIENT_ENGAGEMENT_UNSIGNED", conditions, actor_user_id=actor_user_id)


def detect_client_non_response(*, actor_user_id=None, threshold_days=5, now=None):
    now = now or _now()
    with engine.connect() as c:
        scopes = _return_scopes(c)
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] == "awaiting_information" and _age_days(s["status_entered_at"], now) >= threshold_days:
            conditions[f"tax:nonresponse:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "Client non-response"}
    return _reconcile("tax:nonresponse:", "CLIENT_UNRESPONSIVE", conditions, actor_user_id=actor_user_id)


def detect_overdue_work(*, actor_user_id=None, threshold_days=7, now=None):
    now = now or _now()
    with engine.connect() as c:
        scopes = _return_scopes(c)
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] in _WORKING_STATES and _age_days(s["status_entered_at"], now) > threshold_days:
            conditions[f"tax:overdue:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": f"Tax work overdue in {s['status'].replace('_', ' ')}"}
    return _reconcile("tax:overdue:", "FILING_DEADLINE_AT_RISK", conditions, actor_user_id=actor_user_id)


def detect_blocked_workflow_step(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        links = {r["workflow_instance_id"]: r["tax_engagement_return_id"]
                 for r in c.execute(select(tax_workflow_links.c.workflow_instance_id,
                                           tax_workflow_links.c.tax_engagement_return_id)).mappings()}
        rows = c.execute(select(workflow_escalations.c.id, workflow_escalations.c.workflow_instance_id)
                         .where(workflow_escalations.c.status == "open")).mappings().all()
    conditions = {}
    for r in rows:
        rid = links.get(r["workflow_instance_id"])
        s = scopes.get(rid)
        if not s:
            continue
        conditions[f"tax:wf_blocked:{r['id']}"] = {
            "tax_engagement_return_id": rid, "person_id": s["person_id"], "household_id": s["household_id"],
            "workflow_instance_id": r["workflow_instance_id"], "related_entity_type": "workflow_escalation",
            "related_entity_id": r["id"], "title": "Blocked workflow step"}
    return _reconcile("tax:wf_blocked:", "WORKFLOW_STUCK", conditions, actor_user_id=actor_user_id)


def detect_missing_preparer(*, actor_user_id=None, today=None):
    today = today or date.today()
    with engine.connect() as c:
        scopes = _return_scopes(c)
        assigned = set(c.execute(
            select(record_assignments.c.entity_id).where(
                record_assignments.c.entity_type == "tax_return",
                record_assignments.c.effective_date <= today,
                or_(record_assignments.c.inactive_date.is_(None),
                    record_assignments.c.inactive_date >= today))).scalars())
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] in ("ready_to_prepare", "in_preparation") and rid not in assigned:
            conditions[f"tax:no_preparer:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "No preparer assigned"}
    return _reconcile("tax:no_preparer:", "WORKFLOW_STUCK", conditions, actor_user_id=actor_user_id)


def detect_missing_reviewer(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        rows = c.execute(select(tax_return_reviews.c.id, tax_return_reviews.c.tax_engagement_return_id,
                                tax_return_reviews.c.review_type)
                         .where(tax_return_reviews.c.status == "pending",
                                tax_return_reviews.c.reviewer_user_id.is_(None),
                                tax_return_reviews.c.reviewer_team_id.is_(None))).mappings().all()
    conditions = {}
    for r in rows:
        s = scopes.get(r["tax_engagement_return_id"])
        if not s:
            continue
        conditions[f"tax:no_reviewer:{r['id']}"] = {
            "tax_engagement_return_id": r["tax_engagement_return_id"], "person_id": s["person_id"],
            "household_id": s["household_id"], "related_entity_type": "tax_return_review",
            "related_entity_id": r["id"], "title": f"No {r['review_type']} reviewer assigned"}
    return _reconcile("tax:no_reviewer:", "WORKFLOW_STUCK", conditions, actor_user_id=actor_user_id)


def detect_filing_rejection(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] == "rejected":
            conditions[f"tax:filing_rejected:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "Filing rejected"}
    return _reconcile("tax:filing_rejected:", "FILING_REJECTED", conditions, actor_user_id=actor_user_id)


def detect_filing_transmission_failure(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        rows = c.execute(select(tax_filing_events.c.id, tax_filing_events.c.tax_engagement_return_id)
                         .where(tax_filing_events.c.reason_code.isnot(None))).mappings().all()
    conditions = {}
    for r in rows:
        s = scopes.get(r["tax_engagement_return_id"])
        # Only while the return has not yet been accepted (the failure is unresolved).
        if not s or s["status"] == "accepted":
            continue
        conditions[f"tax:transmission:{r['id']}"] = {
            "tax_engagement_return_id": r["tax_engagement_return_id"], "person_id": s["person_id"],
            "household_id": s["household_id"], "related_entity_type": "tax_filing_event",
            "related_entity_id": r["id"], "title": "Filing transmission failure"}
    return _reconcile("tax:transmission:", "FILING_TRANSMISSION_ERROR", conditions, actor_user_id=actor_user_id)


def detect_acceptance_pending(*, actor_user_id=None, threshold_days=3, now=None):
    now = now or _now()
    with engine.connect() as c:
        scopes = _return_scopes(c)
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] == "filed" and _age_days(s["status_entered_at"], now) > threshold_days:
            conditions[f"tax:acceptance_pending:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "Acceptance pending too long"}
    return _reconcile("tax:acceptance_pending:", "FILING_DEADLINE_AT_RISK", conditions, actor_user_id=actor_user_id)


def detect_compliance_docs_missing(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        with_open_items = set(c.execute(select(tax_missing_items.c.tax_engagement_return_id)
                                        .where(tax_missing_items.c.status == "open")).scalars())
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] in ("delivered", "completed") and rid in with_open_items:
            conditions[f"tax:retention:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "Required documentation missing at completion"}
    return _reconcile("tax:retention:", "COMPLIANCE_RETENTION_RISK", conditions, actor_user_id=actor_user_id)


def detect_required_review_skipped(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        approved = {(r["tax_engagement_return_id"], r["review_type"])
                    for r in c.execute(select(tax_return_reviews.c.tax_engagement_return_id,
                                              tax_return_reviews.c.review_type)
                                       .where(tax_return_reviews.c.status == "approved")).mappings()}
    conditions = {}
    for rid, s in scopes.items():
        if s["status"] in _POST_REVIEW_STATES and (rid, "manager") not in approved:
            conditions[f"tax:signoff:{rid}"] = {
                "tax_engagement_return_id": rid, "person_id": s["person_id"],
                "household_id": s["household_id"], "title": "Required review sign-off missing"}
    return _reconcile("tax:signoff:", "COMPLIANCE_SIGNOFF_MISSING", conditions, actor_user_id=actor_user_id)


def detect_document_ambiguity(*, actor_user_id=None):
    with engine.connect() as c:
        scopes = _return_scopes(c)
        person_to_return = {}
        for rid, s in scopes.items():
            person_to_return.setdefault(s["person_id"], (rid, s["household_id"]))
        rows = c.execute(select(documents.c.id, documents.c.person_id)
                         .where(documents.c.review_status.in_(("pending", "ready_for_review")),
                                documents.c.archived.is_(False))).mappings().all()
    conditions = {}
    for r in rows:
        scope = person_to_return.get(r["person_id"])
        if not scope:
            continue
        rid, household_id = scope
        conditions[f"tax:docreview:{r['id']}"] = {
            "tax_engagement_return_id": rid, "person_id": r["person_id"], "household_id": household_id,
            "document_id": r["id"], "related_entity_type": "document", "related_entity_id": r["id"],
            "title": "Document awaiting review / ambiguous match"}
    return _reconcile("tax:docreview:", "DOC_AMBIGUOUS_MATCH", conditions, actor_user_id=actor_user_id)


# --- orchestrator -------------------------------------------------------------

DETECTORS = (
    detect_missing_documents, detect_missing_organizer, detect_missing_questionnaire,
    detect_missing_signatures, detect_client_non_response, detect_overdue_work,
    detect_blocked_workflow_step, detect_missing_preparer, detect_missing_reviewer,
    detect_filing_rejection, detect_filing_transmission_failure, detect_acceptance_pending,
    detect_compliance_docs_missing, detect_required_review_skipped, detect_document_ambiguity,
)


def scan_tax_exceptions(*, actor_user_id=None):
    """Run every tax detector once (idempotent). Returns per-detector raised/closed counts."""
    summary = {}
    for detector in DETECTORS:
        summary[detector.__name__] = detector(actor_user_id=actor_user_id)
    return summary
