"""Unified Work Queue composition (Phase D.39).

``compose_queue`` gathers bounded candidate sets from every capability-enabled adapter, suppresses items
whose per-item capability the principal lacks, applies the requested filters, sorts deterministically,
paginates, and resolves display names for the visible page only (no N+1 over the whole set). It is
read-only and record-scope-preserving (each adapter enforces scope; adapters fail closed). It never
reads an ``rm_*`` table directly. Returns the page plus counts + adapter timings for diagnostics.
"""
from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

from .adapters import ADAPTERS
from .contract import PRIORITY_RANK

_DT_MAX = datetime.max.replace(tzinfo=UTC)


def collect(principal, *, now=None):
    """Run every enabled adapter (fail-closed, timed) and return (items, stats). ``stats`` records
    per-adapter counts / latency / errors for diagnostics."""
    now = now or datetime.now(UTC)
    items, stats = [], {}
    for adapter in ADAPTERS:
        if not adapter.enabled_for(principal):
            stats[adapter.domain] = {"enabled": False, "count": 0, "ms": 0, "error": False}
            continue
        t0 = time.perf_counter()
        error = False
        try:
            produced = adapter.list_items(principal, now=now)
        except Exception:
            produced, error = [], True
        items.extend(produced)
        stats[adapter.domain] = {"enabled": True, "count": len(produced),
                                 "ms": round((time.perf_counter() - t0) * 1000, 1), "error": error}
    return items, stats


def _suppress(items, principal):
    """Drop items whose per-item capability the principal lacks (never shown-then-403)."""
    kept, suppressed = [], 0
    for it in items:
        if it.capability and not principal.can(it.capability):
            suppressed += 1
        else:
            kept.append(it)
    return kept, suppressed


def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _match(it, f, principal, now):
    dom = f.get("domain")
    if dom is not None:
        doms = dom if isinstance(dom, (list, tuple)) else [dom]
        if it.source_domain not in doms:
            return False
    if f.get("status") and it.status_group != f["status"]:
        return False
    pr = f.get("priority")
    if pr is not None:
        prs = pr if isinstance(pr, (list, tuple)) else [pr]
        if it.priority not in prs:
            return False
    if f.get("sla") and it.sla_state != f["sla"]:
        return False
    if f.get("overdue") and not it.overdue:
        return False
    if f.get("unassigned") and it.assignee_user_id is not None:
        return False
    asg = f.get("assignee")
    if asg == "me":
        if it.assignee_user_id != principal.user_id:
            return False
    elif asg not in (None, "", "team"):
        if str(it.assignee_user_id) != str(asg):
            return False
    if f.get("team") and str(it.team) != str(f["team"]):
        return False
    if f.get("person_id") and str(it.person_id) != str(f["person_id"]):
        return False
    if f.get("household_id") and str(it.household_id) != str(f["household_id"]):
        return False
    search = f.get("search")
    if search:
        s = str(search).lower()
        if s not in (it.title or "").lower() and s not in (it.summary or "").lower():
            return False
    due = f.get("due")
    if due == "today":
        if not (it.due_at and it.due_at.date() == now.date()):
            return False
    elif due == "week":
        if not (it.due_at and now.date() <= it.due_at.date() <= now.date() + timedelta(days=7)):
            return False
    elif due == "overdue":
        if not it.overdue:
            return False
    df = _as_date(f.get("due_from"))
    if df and (not it.due_at or it.due_at.date() < df):
        return False
    dt = _as_date(f.get("due_to"))
    if dt and (not it.due_at or it.due_at.date() > dt):
        return False
    return True


def _sort_key(it):
    # 1 overdue → 2 SLA-breached → 3 priority → 4 earliest due → 5 oldest created → 6 stable key.
    return (0 if it.overdue else 1,
            0 if it.sla_state == "breached" else 1,
            PRIORITY_RANK.get(it.priority, 2),
            it.due_at or _DT_MAX,
            it.created_at or _DT_MAX,
            it.work_item_key)


def _resolve_names(rows):
    if not rows:
        return
    from sqlalchemy import select

    from app.db import engine, households, people, users
    uids = {r.assignee_user_id for r in rows if r.assignee_user_id}
    pids = {r.person_id for r in rows if r.person_id}
    hids = {r.household_id for r in rows if r.household_id}
    unames, pnames, hnames = {}, {}, {}
    with engine.connect() as c:
        if uids:
            unames = {u.id: u.display_name for u in
                      c.execute(select(users.c.id, users.c.display_name)
                                .where(users.c.id.in_(uids))).mappings()}
        if pids:
            pnames = {p.id: p.full_name for p in
                      c.execute(select(people.c.id, people.c.full_name)
                                .where(people.c.id.in_(pids))).mappings()}
        if hids:
            name_col = households.c.name if "name" in households.c else households.c.id
            hnames = {h.id: getattr(h, "name", None) for h in
                      c.execute(select(households.c.id, name_col).where(households.c.id.in_(hids))).mappings()}
    for r in rows:
        r.assignee_name = unames.get(r.assignee_user_id)
        r.person_name = pnames.get(r.person_id)
        r.household_name = hnames.get(r.household_id)


def compose_queue(principal, *, filters=None, page=1, page_size=25, now=None):
    """Compose the paginated Unified Work Queue for ``principal``. Read-only; never mutates."""
    now = now or datetime.now(UTC)
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 25)))
    filters = filters or {}

    items, stats = collect(principal, now=now)
    items, suppressed = _suppress(items, principal)
    filtered = [it for it in items if _match(it, filters, principal, now)]
    filtered.sort(key=_sort_key)

    total = len(filtered)
    pages = (total + page_size - 1) // page_size if total else 0
    page_rows = filtered[(page - 1) * page_size: (page - 1) * page_size + page_size]
    _resolve_names(page_rows)

    by_domain, by_status, by_sla = {}, {}, {}
    overdue = breached = unassigned = 0
    for it in filtered:
        by_domain[it.source_domain] = by_domain.get(it.source_domain, 0) + 1
        by_status[it.status_group] = by_status.get(it.status_group, 0) + 1
        by_sla[it.sla_state] = by_sla.get(it.sla_state, 0) + 1
        overdue += 1 if it.overdue else 0
        breached += 1 if it.sla_state == "breached" else 0
        unassigned += 1 if it.assignee_user_id is None else 0

    return {
        "rows": [it.to_dict() for it in page_rows],
        "items": page_rows,
        "total": total, "page": page, "page_size": page_size, "pages": pages,
        "candidate_total": len(items),
        "suppressed_capability": suppressed,
        "counts": {"by_domain": by_domain, "by_status": by_status, "by_sla": by_sla,
                   "overdue": overdue, "breached": breached, "unassigned": unassigned},
        "adapter_stats": stats,
    }
