"""Declarative projection definitions (Phase D.36) — the read-model catalog + apply handlers.

Builds the executable ``ProjectionDefinition`` objects from the shared pure-data seed
(``app/database/projection_seed.py``) — the same data the migration seeds — so the registry rows and
the executable definitions cannot drift. Each definition binds a projection to its read-model table and
an ``apply(conn, event)`` handler that projects a domain event into the read model. Handlers contain NO
authoritative business logic: they only copy references/statuses/timestamps from the (references-only)
event payload into a query-optimized row. They read/write ONLY the read-model table + the outbox — never
an authoritative table. Applying the same events always yields the same read model (deterministic).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.database.projection_seed import PROJECTION_CATEGORIES, PROJECTION_DEFINITIONS_SEED

from .common import increment, upsert


def _tbl(name):
    from app.db import metadata
    return metadata.tables[name]


# --- apply handlers (event → read-model row); references only, no business logic -----------------

def _people(conn, event):
    p = event["payload"]
    pid = p.get("person_id")
    if pid is None:
        return
    t = _tbl("rm_people_summary")
    et = event["event_type"]
    if et == "people.person_created":
        upsert(conn, t, "person_id", pid, {}, event, insert_extra={"created_at": event.get("occurred_at")})
    elif et == "people.person_updated":
        increment(conn, t, "person_id", pid, "update_count", event)
    elif et == "people.identity_merged":
        increment(conn, t, "person_id", pid, "merge_count", event)


def _household(conn, event):
    p = event["payload"]
    hid = p.get("household_id")
    if hid is None:
        return
    t = _tbl("rm_household_summary")
    if event["event_type"] == "households.household_created":
        upsert(conn, t, "household_id", hid, {}, event, insert_extra={"created_at": event.get("occurred_at")})
    else:
        increment(conn, t, "household_id", hid, "membership_change_count", event)


def _opportunity(conn, event):
    p = event["payload"]
    oid = p.get("opportunity_id")
    if oid is None:
        return
    t = _tbl("rm_opportunity_pipeline")
    et = event["event_type"]
    if et == "opportunity.created":
        upsert(conn, t, "opportunity_id", oid,
               {"pipeline_id": p.get("pipeline_id"), "stage_id": p.get("stage_id"), "status": p.get("status")},
               event, insert_extra={"created_at": event.get("occurred_at")})
    elif et == "opportunity.stage_changed":
        upsert(conn, t, "opportunity_id", oid,
               {"stage_id": p.get("to_stage_id"), "status": p.get("to_status")}, event)
    else:  # won / lost
        upsert(conn, t, "opportunity_id", oid,
               {"status": p.get("status"), "closed_at": event.get("occurred_at")}, event)


def _op_tasks(conn, event):
    p = event["payload"]
    tid = p.get("task_id")
    if tid is None:
        return
    t = _tbl("rm_operational_tasks")
    if event["event_type"] == "operations.task_created":
        upsert(conn, t, "task_id", tid,
               {"project_id": p.get("project_id"), "status": p.get("status"), "priority": p.get("priority")},
               event, insert_extra={"created_at": event.get("occurred_at")})
    else:
        upsert(conn, t, "task_id", tid,
               {"status": p.get("to_status"), "completed_at": event.get("occurred_at")}, event)


def _projects(conn, event):
    p = event["payload"]
    pid = p.get("project_id")
    if pid is None:
        return
    t = _tbl("rm_projects")
    if event["event_type"] == "operations.project_created":
        upsert(conn, t, "project_id", pid, {"category": p.get("category"), "status": p.get("status")},
               event, insert_extra={"created_at": event.get("occurred_at")})
    else:
        upsert(conn, t, "project_id", pid, {"status": p.get("to_status")}, event)


def _compliance(conn, event):
    p = event["payload"]
    rid = p.get("review_id")
    if rid is None:
        return
    t = _tbl("rm_compliance_queue")
    if event["event_type"] == "compliance.review_opened":
        upsert(conn, t, "review_id", rid,
               {"status": p.get("status"), "governing_rule": p.get("governing_rule")},
               event, insert_extra={"opened_at": event.get("occurred_at")})
    else:  # approval granted / denied
        upsert(conn, t, "review_id", rid,
               {"status": p.get("decision"), "decision": p.get("decision"),
                "decided_at": event.get("occurred_at")}, event)


def _tax(conn, event):
    p = event["payload"]
    rid = p.get("return_id")
    if rid is None:
        return
    t = _tbl("rm_tax_pipeline")
    et = event["event_type"]
    if et == "tax.engagement_created":
        upsert(conn, t, "return_id", rid,
               {"engagement_id": p.get("engagement_id"), "tax_year": p.get("tax_year")},
               event, insert_extra={"created_at": event.get("occurred_at")})
    elif et == "tax.return_status_changed":
        upsert(conn, t, "return_id", rid, {"status": p.get("to_status")}, event)
    else:  # filing submitted / acknowledged
        upsert(conn, t, "return_id", rid, {"filing_status": p.get("filing_status")}, event)


def _insurance(conn, event):
    p = event["payload"]
    cid = p.get("case_id")
    if cid is None:
        return
    t = _tbl("rm_insurance_pipeline")
    if event["event_type"] == "insurance.case_created":
        upsert(conn, t, "case_id", cid, {"case_type": p.get("case_type"), "status": p.get("status")},
               event, insert_extra={"created_at": event.get("occurred_at")})
    else:
        upsert(conn, t, "case_id", cid, {"status": p.get("to_status")}, event)


def _benefits(conn, event):
    p = event["payload"]
    eid = p.get("enrollment_id")
    if eid is None:
        return
    t = _tbl("rm_benefits_enrollment")
    if event["event_type"] == "benefits.enrollment_created":
        upsert(conn, t, "enrollment_id", eid,
               {"plan_year_id": p.get("plan_year_id"), "coverage_tier": p.get("coverage_tier"),
                "status": p.get("status")}, event, insert_extra={"created_at": event.get("occurred_at")})
    else:
        upsert(conn, t, "enrollment_id", eid, {"status": p.get("to_status")}, event)


def _document(conn, event):
    p = event["payload"]
    did = p.get("document_id")
    if did is None:
        return
    t = _tbl("rm_document_status")
    et = event["event_type"]
    if et == "document.registered":
        upsert(conn, t, "document_id", did,
               {"classification": p.get("classification"), "status": p.get("status")},
               event, insert_extra={"created_at": event.get("occurred_at")})
    elif et == "document.archived":
        upsert(conn, t, "document_id", did,
               {"status": p.get("to_status"), "archived_at": event.get("occurred_at")}, event)
    else:
        upsert(conn, t, "document_id", did, {"status": p.get("to_status")}, event)


def _exception(conn, event):
    p = event["payload"]
    xid = p.get("exception_id")
    if xid is None:
        return
    t = _tbl("rm_exception_dashboard")
    if event["event_type"] == "exception.opened":
        upsert(conn, t, "exception_id", xid,
               {"code": p.get("code"), "domain": p.get("domain"), "category": p.get("category"),
                "severity": p.get("severity"), "status": p.get("status")},
               event, insert_extra={"opened_at": event.get("occurred_at")})
    else:
        upsert(conn, t, "exception_id", xid,
               {"status": p.get("to_status"), "resolved_at": event.get("occurred_at")}, event)


def _activity(conn, event):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    t = _tbl("rm_activity_feed")
    stmt = pg_insert(t).values(
        event_id=event.get("event_id"), outbox_event_id=event.get("outbox_id"),
        event_type=event["event_type"], category=str(event["event_type"]).split(".")[0],
        subject_ref=event.get("subject_ref"), occurred_at=event.get("occurred_at"))
    conn.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))


_HANDLERS = {
    "people.summary": _people, "household.summary": _household,
    "opportunity.pipeline": _opportunity, "operations.tasks": _op_tasks, "operations.projects": _projects,
    "compliance.queue": _compliance, "tax.pipeline": _tax, "insurance.pipeline": _insurance,
    "benefits.enrollment": _benefits, "document.status": _document,
    "exception.dashboard": _exception, "activity.feed": _activity,
}


@dataclass(frozen=True)
class ProjectionDefinition:
    projection_id: str
    name: str
    category: str
    owner: str
    read_table: str
    subscribed_events: tuple
    schema_version: int
    depends_on: tuple
    apply: Callable
    rebuild_strategy: str = "full"
    description: str = ""

    @property
    def all_events(self) -> bool:
        return "*" in self.subscribed_events

    def matches(self, event_type: str) -> bool:
        return self.all_events or event_type in self.subscribed_events


def _build(row) -> ProjectionDefinition:
    (pid, name, category, owner, read_table, events, version, deps, desc) = row
    return ProjectionDefinition(
        projection_id=pid, name=name, category=category, owner=owner, read_table=read_table,
        subscribed_events=tuple(events), schema_version=version, depends_on=tuple(deps),
        apply=_HANDLERS[pid], description=desc)


PROJECTION_DEFINITIONS: dict[str, ProjectionDefinition] = {
    r[0]: _build(r) for r in PROJECTION_DEFINITIONS_SEED}

CATEGORIES = tuple(PROJECTION_CATEGORIES)


def get_definition(projection_id: str) -> ProjectionDefinition | None:
    return PROJECTION_DEFINITIONS.get(projection_id)


def definitions_for_event(event_type: str) -> list[ProjectionDefinition]:
    return [d for d in PROJECTION_DEFINITIONS.values() if d.matches(event_type)]
