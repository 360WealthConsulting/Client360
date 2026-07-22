"""Data quality checks & findings (Phase D.23) — deterministic rules only.

Runs deterministic quality rules (required fields, orphan records, stale data, unresolved matching,
…) over canonical records — **reading** them, never mutating them — and records governance-owned
findings. No AI, no probabilistic logic. Reuses the existing normalizers and ambiguity detection
(``promote.list_ambiguous_unlinked``); it recomputes no matching math. Unimplemented rule types
complete as a deterministic no-op (zero findings) so the framework is complete and extensible.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.database.governance_tables import FINDING_STATUSES
from app.db import accounts, engine, people
from app.db import governance_quality_checks as checks_t
from app.db import governance_quality_findings as findings_t
from app.db import governance_quality_rules as rules_t

from .common import (
    GovernanceError,
    GovernanceNotFound,
    now,
    publish_timeline,
    record_event,
    scope_clause,
    visible,
)

_OPEN = ("open", "acknowledged")
_SCAN_LIMIT = 5000


# --- findings ----------------------------------------------------------------

def _open_finding(c, rule, *, entity_type, entity_id, finding_type, detail=None, person_id=None,
                  household_id=None, check_id=None, actor_user_id=None) -> int | None:
    """Insert an open finding only if none already exists for (rule, entity) — idempotent."""
    existing = c.scalar(select(findings_t.c.id).where(
        findings_t.c.rule_id == rule["id"], findings_t.c.entity_type == entity_type,
        findings_t.c.entity_id == entity_id, findings_t.c.status.in_(_OPEN)))
    if existing is not None:
        return None
    fid = c.execute(findings_t.insert().values(
        rule_id=rule["id"], check_id=check_id, data_element_id=rule.get("data_element_id"),
        entity_type=entity_type, entity_id=entity_id, finding_type=finding_type,
        severity=rule["severity"], status="open", detail=detail, person_id=person_id,
        household_id=household_id, created_by_user_id=actor_user_id).returning(findings_t.c.id)).scalar()
    record_event(c, entity_type="finding", entity_id=fid, event_type="finding_opened",
                 to_status="open", actor_user_id=actor_user_id, payload={"rule": rule["code"]})
    return fid


def list_findings(principal, *, status=None, severity=None, rule_id=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = scope_clause(findings_t, principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(findings_t.c.status == status)
        if severity:
            conds.append(findings_t.c.severity == severity)
        if rule_id is not None:
            conds.append(findings_t.c.rule_id == rule_id)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(findings_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(findings_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(findings_t.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_finding(principal, finding_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(findings_t).where(findings_t.c.id == finding_id)).mappings().first()
    if row is None or not visible(principal, dict(row)):
        return None
    return dict(row)


def create_finding(principal, *, rule_id=None, entity_type, entity_id, finding_type, severity="medium",
                   detail=None, person_id=None, household_id=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        row = c.execute(findings_t.insert().values(
            rule_id=rule_id, entity_type=entity_type, entity_id=entity_id, finding_type=finding_type,
            severity=severity, status="open", detail=detail, person_id=person_id,
            household_id=household_id, created_by_user_id=actor_user_id).returning(*findings_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="finding", entity_id=row["id"], event_type="finding_opened",
                     to_status="open", actor_user_id=actor_user_id)
    publish_timeline(row, "finding_opened", title=finding_type)
    return row


def set_finding_status(principal, finding_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in FINDING_STATUSES:
        raise GovernanceError(f"invalid status {status!r}")
    with engine.begin() as c:
        f = c.execute(select(findings_t).where(findings_t.c.id == finding_id)).mappings().first()
        if f is None or not visible(principal, dict(f)):
            raise GovernanceNotFound(str(finding_id))
        values = {"status": status, "updated_at": now()}
        if status in ("resolved", "false_positive", "waived"):
            values["resolved_at"] = now()
            values["resolved_by_user_id"] = actor_user_id
        row = c.execute(findings_t.update().where(findings_t.c.id == finding_id)
                        .values(**values).returning(*findings_t.c)).mappings().one()
        record_event(c, entity_type="finding", entity_id=finding_id, event_type=f"finding_{status}",
                     from_status=f["status"], to_status=status, actor_user_id=actor_user_id)
        return dict(row)


# --- deterministic check runners ---------------------------------------------

def _run_required_field(c, rule, check_id, actor_user_id) -> tuple[int, int]:
    field = (rule.get("config") or {}).get("field", "primary_email")
    col = people.c.get(field)
    if col is None:
        return 0, 0
    rows = c.execute(select(people.c.id).where(or_(col.is_(None), col == ""),
                                               people.c.active.is_(True)).limit(_SCAN_LIMIT)).scalars()
    scanned = f = 0
    for pid in rows:
        scanned += 1
        if _open_finding(c, rule, entity_type="person", entity_id=pid, person_id=pid,
                         finding_type=f"required_field:{field}", detail=f"missing {field}",
                         check_id=check_id, actor_user_id=actor_user_id) is not None:
            f += 1
    return scanned, f


def _run_orphan(c, rule, check_id, actor_user_id) -> tuple[int, int]:
    rows = c.execute(select(accounts.c.id).where(
        accounts.c.person_id.is_(None), accounts.c.household_id.is_(None)).limit(_SCAN_LIMIT)).scalars()
    scanned = f = 0
    for aid in rows:
        scanned += 1
        if _open_finding(c, rule, entity_type="account", entity_id=aid,
                         finding_type="orphan:account", detail="account has no person or household",
                         check_id=check_id, actor_user_id=actor_user_id) is not None:
            f += 1
    return scanned, f


def _run_stale(c, rule, check_id, actor_user_id) -> tuple[int, int]:
    from datetime import timedelta
    days = int((rule.get("config") or {}).get("days", 730))
    cutoff = now() - timedelta(days=days)
    rows = c.execute(select(people.c.id).where(
        people.c.active.is_(True), people.c.updated_at < cutoff).limit(_SCAN_LIMIT)).scalars()
    scanned = f = 0
    for pid in rows:
        scanned += 1
        if _open_finding(c, rule, entity_type="person", entity_id=pid, person_id=pid,
                         finding_type=f"stale:{days}d", detail=f"not updated in {days} days",
                         check_id=check_id, actor_user_id=actor_user_id) is not None:
            f += 1
    return scanned, f


def _run_unresolved_matching(c, rule, check_id, actor_user_id) -> tuple[int, int]:
    from app.matching.promote import list_ambiguous_unlinked
    ambiguous = list_ambiguous_unlinked(conn=c)
    f = 0
    for item in ambiguous[:_SCAN_LIMIT]:
        sc_id = item.get("source_contact_id") or item.get("id")
        if sc_id is None:
            continue
        if _open_finding(c, rule, entity_type="source_contact", entity_id=sc_id,
                         finding_type="unresolved_matching",
                         detail="source contact has ambiguous person candidates",
                         check_id=check_id, actor_user_id=actor_user_id) is not None:
            f += 1
    return len(ambiguous), f


_RUNNERS = {"required_field": _run_required_field, "orphan": _run_orphan, "stale": _run_stale,
            "unresolved_matching": _run_unresolved_matching}


def run_check(principal, rule_id: int, *, run_type="manual", actor_user_id=None) -> dict:
    """Run one quality rule deterministically, recording a check + any findings."""
    with engine.begin() as c:
        rule = c.execute(select(rules_t).where(rules_t.c.id == rule_id)).mappings().first()
        if rule is None:
            raise GovernanceNotFound(str(rule_id))
        rule = dict(rule)
        started = now()
        check_id = c.execute(checks_t.insert().values(
            rule_id=rule_id, run_type=run_type, status="running", started_at=started,
            triggered_by_user_id=actor_user_id).returning(checks_t.c.id)).scalar()
        runner = _RUNNERS.get(rule["rule_type"])
        try:
            scanned, found = runner(c, rule, check_id, actor_user_id) if runner else (0, 0)
            status = "completed"
        except Exception as exc:            # noqa: BLE001 — deterministic completion, isolated
            scanned, found, status = 0, 0, "failed"
            c.execute(checks_t.update().where(checks_t.c.id == check_id)
                      .values(check_metadata={"error": str(exc)[:500]}))
        c.execute(checks_t.update().where(checks_t.c.id == check_id).values(
            status=status, records_scanned=scanned, findings_count=found, finished_at=now()))
    return {"check_id": check_id, "rule": rule["code"], "records_scanned": scanned,
            "findings": found, "status": status}


def run_all_active_checks(principal, *, run_type="automation", actor_user_id=None) -> dict:
    with engine.connect() as c:
        rule_ids = list(c.scalars(select(rules_t.c.id).where(rules_t.c.active.is_(True))))
    total_findings = total_checks = 0
    for rid in rule_ids:
        try:
            res = run_check(principal, rid, run_type=run_type, actor_user_id=actor_user_id)
            total_checks += 1
            total_findings += res["findings"]
        except Exception:
            continue
    return {"checks_run": total_checks, "findings_opened": total_findings}


def run_stale_scan(principal, *, days=730, actor_user_id=None) -> dict:
    with engine.connect() as c:
        rule_ids = list(c.scalars(select(rules_t.c.id).where(
            rules_t.c.rule_type == "stale", rules_t.c.active.is_(True))))
    opened = 0
    for rid in rule_ids:
        opened += run_check(principal, rid, run_type="automation", actor_user_id=actor_user_id)["findings"]
    return {"stale_rules_run": len(rule_ids), "findings_opened": opened}


def metrics(principal) -> dict:
    with engine.connect() as c:
        scope = scope_clause(findings_t, principal, c)
        def _count(*extra):
            stmt = select(func.count()).select_from(findings_t)
            conds = [] if scope is None else [scope]
            conds.extend(extra)
            return c.scalar(stmt.where(and_(*conds)) if conds else stmt) or 0
        return {"open": _count(findings_t.c.status.in_(_OPEN)),
                "critical_open": _count(findings_t.c.status.in_(_OPEN),
                                        findings_t.c.severity == "critical"),
                "total": _count()}
