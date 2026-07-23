"""Client/household activity timeline service (Phase D.10).

A read-only cross-domain **projection** — never a system of record. It reads existing
authoritative records through explicit per-domain adapters, merges them, orders
deterministically ``(occurred_at desc, sort_key desc)``, resolves actor display names in a
single batched query (no N+1), applies filters/search over presentation fields only, and
paginates with bounded page sizes. It scope-first (person/household record scope) and
enforces per-source redaction server-side. It never mutates a source record, never
recomputes Advisor Intelligence, and never fabricates timestamps.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.db import engine, people, users
from app.security.authorization import record_in_scope
from app.services.activity_timeline.adapters import advisor_work, compliance, domain_events

# Explicit adapter list (no reflection, no giant SQL union in the route).
_ADAPTERS = (domain_events, advisor_work, compliance)

#: Most-recent events fetched per source before merge — bounded so a single client's
#: timeline is never an unbounded load. A client's cross-domain event count is small.
PER_SOURCE_CAP = 500
MAX_PAGE_SIZE = 100

#: Source-domain labels shown/filterable in the UI.
SOURCE_DOMAINS = ("activity", "advisor_work", "compliance")

_MIN = datetime.min


def _redact_flags(principal) -> dict:
    return {
        "advisor_work": principal.can("advisor_work.read"),
        "compliance": principal.can("compliance.review.read"),
    }


def _household_member_ids(conn, household_id: int) -> tuple[int, ...]:
    return tuple(conn.scalars(select(people.c.id).where(people.c.household_id == household_id)))


def _resolve_actors(conn, events):
    ids = {e.actor_principal_id for e in events if e.actor_principal_id}
    if not ids:
        return events
    names = {r["id"]: r["display_name"] for r in conn.execute(
        select(users.c.id, users.c.display_name).where(users.c.id.in_(tuple(ids)))).mappings()}
    return [e.with_actor(names.get(e.actor_principal_id)) for e in events]


def _match(event, *, event_type, source_domain, date_from, date_to, search):
    if source_domain and event.source_domain != source_domain:
        return False
    if event_type and event.event_type != event_type:
        return False
    occ = event.occurred_at.date() if event.occurred_at else None
    if date_from and (occ is None or occ < date_from):
        return False
    if date_to and (occ is None or occ > date_to):
        return False
    if search:
        needle = search.strip().lower()
        blob = " ".join(filter(None, (event.title, event.summary, event.actor_display_name,
                                      event.source_domain))).lower()
        if needle not in blob:
            return False
    return True


def _project(principal, *, person_ids, household_id, event_type, source_domain,
             date_from, date_to, search, page, page_size):
    redact = _redact_flags(principal)
    with engine.connect() as conn:
        events: list = []
        for adapter in _ADAPTERS:
            events.extend(adapter.events(
                conn, person_ids=tuple(person_ids), household_id=household_id,
                limit=PER_SOURCE_CAP, redact=redact))
        events = _resolve_actors(conn, events)
    # De-duplicate by stable event id (defensive; adapters are already disjoint).
    events = list({e.event_id: e for e in events}.values())
    # Filters/search over presentation fields (actors already resolved).
    events = [e for e in events if _match(
        e, event_type=event_type, source_domain=source_domain,
        date_from=date_from, date_to=date_to, search=search)]
    # Deterministic ordering: occurred_at desc, then stable sort_key desc.
    events.sort(key=lambda e: (e.occurred_at or _MIN, e.sort_key), reverse=True)
    total = len(events)
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    rows = events[(page - 1) * page_size: page * page_size]
    pages = (total + page_size - 1) // page_size if total else 0
    return {"rows": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def client_timeline(principal, person_id: int, *, event_type=None, source_domain=None,
                    date_from=None, date_to=None, search=None, page=1, page_size=25):
    """Reverse-chronological timeline for one client. Scope-first: an inaccessible person
    yields ``None`` (the caller returns 404)."""
    if not record_in_scope(principal, "person", person_id):
        return None
    return _project(principal, person_ids={person_id}, household_id=None,
                    event_type=event_type, source_domain=source_domain,
                    date_from=_parse_date(date_from), date_to=_parse_date(date_to),
                    search=search, page=page, page_size=page_size)


def household_timeline(principal, household_id: int, *, event_type=None, source_domain=None,
                       date_from=None, date_to=None, search=None, page=1, page_size=25):
    """Reverse-chronological timeline for a household: household-level events plus events
    for its current members. Scope-first on the household. Membership is taken from the
    stored person/household links only — no historical membership windows are invented."""
    if not record_in_scope(principal, "household", household_id):
        return None
    with engine.connect() as conn:
        member_ids = set(_household_member_ids(conn, household_id))
    return _project(principal, person_ids=member_ids, household_id=household_id,
                    event_type=event_type, source_domain=source_domain,
                    date_from=_parse_date(date_from), date_to=_parse_date(date_to),
                    search=search, page=page, page_size=page_size)


def recent_activity_feed(principal, *, limit=50):
    """(D.37) A FIRM-WIDE recent domain-event activity feed — an additive read surface served from the
    ``activity.feed`` projection when it is healthy + fresh, else falling back to a bounded authoritative
    read of ``timeline_events``. Firm-wide only (references-only rows carry no record-scope anchor): a
    record-scoped principal gets the authoritative firm feed only via ``record.read_all`` — otherwise an
    empty list (scoped users use the per-person/household timelines above, whose behavior is unchanged).
    This never mutates a projection, never reconstructs business logic, and never bypasses RBAC."""
    from app.services.projections import adoption
    rows = adoption.recent_feed(principal, limit=limit)          # projection when usable, else None
    if rows is not None:
        return rows
    if principal is not None and not principal.can("record.read_all"):
        return []                                                # scoped users: no firm-wide fallback
    from sqlalchemy import select

    from app.db import timeline_events
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(timeline_events).order_by(timeline_events.c.event_time.desc())
            .limit(min(500, max(1, limit)))).mappings()]
