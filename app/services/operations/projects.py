"""Projects, phases & milestones (Phase D.20) — authoritative firm project metadata.

A project is firm work (client anchors are optional). Instantiating from a template scaffolds
default phases and tasks deterministically. Lifecycle is a deterministic state machine
(planned → active → completed, plus blocked/on_hold/cancelled/archived). Approved lifecycle events
(project created/completed, milestone reached) publish to the shared timeline only when the item
carries a client anchor. Record scope is always enforced.
"""
from __future__ import annotations

from sqlalchemy import and_, func, select

from app.database.operations_tables import (
    OPERATIONAL_STATUSES,
    PRIORITIES,
    PROJECT_CATEGORIES,
)
from app.db import engine
from app.db import operational_tasks as tasks_t
from app.db import project_milestones as milestones_t
from app.db import project_phases as phases_t
from app.db import projects as projects_t

from . import templates as tmpl
from .common import (
    OperationsError,
    OperationsNotFound,
    can_write,
    now,
    publish_timeline,
    record_event,
    require_anchor_write,
    scope_clause,
    visible,
)

_TRANSITIONS = {
    "planned": {"active", "on_hold", "cancelled", "archived"},
    "active": {"blocked", "on_hold", "completed", "cancelled"},
    "blocked": {"active", "on_hold", "cancelled"},
    "on_hold": {"active", "cancelled", "archived"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "archived": set(),
}


def _load_scoped(c, principal, project_id: int, *, write=False) -> dict:
    p = c.execute(select(projects_t).where(projects_t.c.id == project_id)).mappings().first()
    if p is None or not visible(principal, dict(p), c):
        raise OperationsNotFound(str(project_id))
    p = dict(p)
    if write and not can_write(principal, p, c):
        raise OperationsError("write not permitted in record scope")
    return p


# --- reads -------------------------------------------------------------------

def list_projects(principal, *, status=None, category=None, search=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = scope_clause(projects_t, principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(projects_t.c.status == status)
        if category:
            conds.append(projects_t.c.category == category)
        if search:
            conds.append(projects_t.c.name.ilike(f"%{search.strip()}%"))
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(projects_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(projects_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(projects_t.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_project(principal, project_id: int) -> dict | None:
    with engine.connect() as c:
        try:
            p = _load_scoped(c, principal, project_id)
        except (OperationsNotFound, OperationsError):
            return None
        p["phases"] = [dict(r) for r in c.execute(
            select(phases_t).where(phases_t.c.project_id == project_id)
            .order_by(phases_t.c.sequence, phases_t.c.id)).mappings()]
        p["milestones"] = [dict(r) for r in c.execute(
            select(milestones_t).where(milestones_t.c.project_id == project_id)
            .order_by(milestones_t.c.id)).mappings()]
        p["tasks"] = [dict(r) for r in c.execute(
            select(tasks_t).where(tasks_t.c.project_id == project_id)
            .order_by(tasks_t.c.id)).mappings()]
    return p


# --- projects ----------------------------------------------------------------

def create_project(principal, *, name, category="general", priority="normal", status="planned",
                   template_code=None, department=None, description=None, start_date=None,
                   target_end_date=None, person_id=None, household_id=None, organization_id=None,
                   opportunity_id=None, compliance_review_id=None, conversation_id=None,
                   workflow_instance_id=None, actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise OperationsError("name is required")
    if category not in PROJECT_CATEGORIES:
        raise OperationsError(f"invalid category {category!r}")
    if priority not in PRIORITIES:
        raise OperationsError(f"invalid priority {priority!r}")
    if status not in ("planned", "active"):
        raise OperationsError("new projects start as 'planned' or 'active'")
    require_anchor_write(principal, person_id=person_id, household_id=household_id,
                         organization_id=organization_id)
    template = tmpl.get_template(code=template_code) if template_code else None
    if template_code and (template is None or not template.get("active")):
        raise OperationsError(f"unknown or inactive template {template_code!r}")
    ts = now()
    with engine.begin() as c:
        p = c.execute(projects_t.insert().values(
            name=name, category=(category if not template else (template["category"] or category)),
            priority=priority, status=status, template_id=(template["id"] if template else None),
            owner_user_id=actor_user_id, department=department, description=description,
            start_date=start_date, target_end_date=target_end_date, person_id=person_id,
            household_id=household_id, organization_id=organization_id, opportunity_id=opportunity_id,
            compliance_review_id=compliance_review_id, conversation_id=conversation_id,
            workflow_instance_id=workflow_instance_id, last_status_at=ts,
            created_by_user_id=actor_user_id, created_at=ts, updated_at=ts)
            .returning(*projects_t.c)).mappings().one()
        p = dict(p)
        record_event(c, entity_type="project", entity_id=p["id"], project_id=p["id"],
                     event_type="project_created", to_status=status, actor_user_id=actor_user_id,
                     payload={"category": p["category"]})
        # Scaffold default phases + tasks from the template (deterministic).
        if template:
            for i, ph in enumerate(template.get("default_phases") or []):
                pname = ph.get("name") if isinstance(ph, dict) else str(ph)
                seq = ph.get("sequence", i) if isinstance(ph, dict) else i
                c.execute(phases_t.insert().values(project_id=p["id"], name=pname, sequence=seq))
            for tk in (template.get("default_tasks") or []):
                title = tk.get("title") if isinstance(tk, dict) else str(tk)
                c.execute(tasks_t.insert().values(project_id=p["id"], title=title,
                                                  created_by_user_id=actor_user_id))
    publish_timeline(p, "project_created")
    return p


def update_project(principal, project_id: int, *, actor_user_id=None, **fields) -> dict:
    allowed = {"name", "priority", "category", "health", "department", "description", "start_date",
               "target_end_date", "estimated_minutes", "actual_minutes", "tags", "project_metadata",
               "opportunity_id", "compliance_review_id", "conversation_id", "workflow_instance_id"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not values:
        raise OperationsError("no updatable fields provided")
    with engine.begin() as c:
        _load_scoped(c, principal, project_id, write=True)
        values["updated_at"] = now()
        p = c.execute(projects_t.update().where(projects_t.c.id == project_id)
                      .values(**values).returning(*projects_t.c)).mappings().one()
        record_event(c, entity_type="project", entity_id=project_id, project_id=project_id,
                     event_type="project_updated", actor_user_id=actor_user_id,
                     payload={"fields": sorted(values.keys())})
        return dict(p)


def transition_project(principal, project_id: int, status: str, *, actor_user_id=None, reason=None) -> dict:
    if status not in OPERATIONAL_STATUSES:
        raise OperationsError(f"invalid status {status!r}")
    with engine.begin() as c:
        p = _load_scoped(c, principal, project_id, write=True)
        current = p["status"]
        if status != current and status not in _TRANSITIONS.get(current, set()):
            raise OperationsError(f"cannot transition {current!r} -> {status!r}")
        ts = now()
        values = {"status": status, "last_status_at": ts, "updated_at": ts}
        if status == "completed":
            values["actual_end_date"] = ts.date()
        updated = c.execute(projects_t.update().where(projects_t.c.id == project_id)
                            .values(**values).returning(*projects_t.c)).mappings().one()
        record_event(c, entity_type="project", entity_id=project_id, project_id=project_id,
                     event_type=f"project_{status}", from_status=current, to_status=status,
                     actor_user_id=actor_user_id, payload={"reason": reason})
        updated = dict(updated)
    if status == "completed":
        publish_timeline(updated, "project_completed")
    return updated


# --- phases ------------------------------------------------------------------

def add_phase(principal, project_id: int, *, name, sequence=0, actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise OperationsError("phase name is required")
    with engine.begin() as c:
        _load_scoped(c, principal, project_id, write=True)
        row = c.execute(phases_t.insert().values(
            project_id=project_id, name=name, sequence=int(sequence)).returning(*phases_t.c)).mappings().one()
        record_event(c, entity_type="phase", entity_id=dict(row)["id"], project_id=project_id,
                     event_type="phase_added", actor_user_id=actor_user_id, payload={"name": name})
        return dict(row)


# --- milestones --------------------------------------------------------------

def add_milestone(principal, project_id: int, *, name, due_date=None, phase_id=None,
                  actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise OperationsError("milestone name is required")
    with engine.begin() as c:
        _load_scoped(c, principal, project_id, write=True)
        row = c.execute(milestones_t.insert().values(
            project_id=project_id, name=name, due_date=due_date, phase_id=phase_id,
            status="pending").returning(*milestones_t.c)).mappings().one()
        record_event(c, entity_type="milestone", entity_id=dict(row)["id"], project_id=project_id,
                     event_type="milestone_added", actor_user_id=actor_user_id, payload={"name": name})
        return dict(row)


def reach_milestone(principal, milestone_id: int, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        ms = c.execute(select(milestones_t).where(milestones_t.c.id == milestone_id)).mappings().first()
        if ms is None:
            raise OperationsNotFound(f"milestone {milestone_id}")
        project = _load_scoped(c, principal, ms["project_id"], write=True)
        ts = now()
        row = c.execute(milestones_t.update().where(milestones_t.c.id == milestone_id)
                        .values(status="reached", reached_at=ts, updated_at=ts)
                        .returning(*milestones_t.c)).mappings().one()
        record_event(c, entity_type="milestone", entity_id=milestone_id, project_id=ms["project_id"],
                     event_type="milestone_reached", to_status="reached", actor_user_id=actor_user_id,
                     payload={"name": ms["name"]})
        row = dict(row)
    # Approved lifecycle event, published with the PROJECT's client anchor (if any).
    publish_timeline({"id": milestone_id, "name": ms["name"], "person_id": project.get("person_id"),
                      "household_id": project.get("household_id")}, "milestone_reached")
    return row


# --- metrics -----------------------------------------------------------------

def project_metrics(principal) -> dict:
    with engine.connect() as c:
        scope = scope_clause(projects_t, principal, c)
        def _count(*extra):
            stmt = select(func.count()).select_from(projects_t)
            conds = [] if scope is None else [scope]
            conds.extend(extra)
            return c.scalar(stmt.where(and_(*conds)) if conds else stmt) or 0
        return {"total": _count(), "active": _count(projects_t.c.status == "active"),
                "completed": _count(projects_t.c.status == "completed"),
                "at_risk": _count(projects_t.c.health == "red")}
