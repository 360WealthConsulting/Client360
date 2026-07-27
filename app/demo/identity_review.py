"""Identity Review — demo dashboard exposing the EXISTING identity-matching intelligence.

DEMO-ONLY surface (mounted solely by app.demo.demo_app). It adds NO new matching logic: it
composes read-only counts from people / source_contacts / person_source_links and reuses the
existing services —

  * app.matching.promote.promote_unlinked      (the "Run Identity Matching" button)
  * app.matching.promote.list_ambiguous_unlinked (the unresolved-review queue + source filter)
  * app.routes.identity_review /matches/unresolved/{id}/resolve  (Link / Create actions)
  * app.routes.matches /matches                (the duplicate-candidate merge review)

so Michael can see, run, and resolve identity matching without hand-searching 15,000 people.
"""
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, select

from app.db import engine, people, person_source_links, source_contacts
from app.matching.promote import list_ambiguous_unlinked, promote_unlinked
from app.routes.matches import count_pending_match_groups
from app.security.audit import write_audit_event
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.templating import install_filters

router = APIRouter(prefix="/demo", tags=["demo"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)

# The source systems the office imports (matches the importers under app/importers/).
SOURCES = ["Wealthbox", "Dave Ramsey", "AssetMark", "Schwab"]
# Cross-source pairs surfaced on the dashboard.
CROSS_PAIRS = [("Wealthbox", "Dave Ramsey"), ("Wealthbox", "AssetMark"), ("Wealthbox", "Schwab")]


def _pair_count(conn, a, b):
    """People linked to source contacts from BOTH systems a and b."""
    sub = (
        select(person_source_links.c.person_id)
        .join(source_contacts, source_contacts.c.id == person_source_links.c.source_contact_id)
        .where(source_contacts.c.source_system.in_([a, b]))
        .group_by(person_source_links.c.person_id)
        .having(func.count(distinct(source_contacts.c.source_system)) == 2)
        .subquery()
    )
    return conn.scalar(select(func.count()).select_from(sub)) or 0


def _counts(conn):
    """Live, database-backed identity counts — read-only aggregation, no matching logic."""
    people_total = conn.scalar(select(func.count()).select_from(people)) or 0
    sc_total = conn.scalar(select(func.count()).select_from(source_contacts)) or 0
    linked = conn.scalar(
        select(func.count(distinct(person_source_links.c.source_contact_id)))
    ) or 0
    ambiguous = len(list_ambiguous_unlinked(conn=conn))

    by_system = dict(
        conn.execute(
            select(source_contacts.c.source_system, func.count())
            .group_by(source_contacts.c.source_system)
            .order_by(source_contacts.c.source_system)
        ).all()
    )

    multi_source_people = conn.scalar(
        select(func.count()).select_from(
            select(person_source_links.c.person_id)
            .join(source_contacts, source_contacts.c.id == person_source_links.c.source_contact_id)
            .group_by(person_source_links.c.person_id)
            .having(func.count(distinct(source_contacts.c.source_system)) >= 2)
            .subquery()
        )
    ) or 0

    cross = {f"{a} + {b}": _pair_count(conn, a, b) for a, b in CROSS_PAIRS}

    return {
        "canonical_people": people_total,
        "total_source_contacts": sc_total,
        "linked_source_contacts": linked,
        "unlinked_source_contacts": sc_total - linked,
        "ambiguous_unresolved": ambiguous,
        "multi_source_people": multi_source_people,
        "by_system": by_system,
        "cross_source": cross,
        "duplicate_candidate_groups": count_pending_match_groups(),
    }


def _last_run(request):
    """Read the just-completed promotion summary from the redirect query params, if present."""
    qp = request.query_params
    if qp.get("ran") != "1":
        return None
    return {
        "inspected": int(qp.get("inspected", 0)),
        "created": int(qp.get("created", 0)),
        "linked_existing": int(qp.get("linked_existing", 0)),
        "ambiguous": int(qp.get("ambiguous", 0)),
        "source": qp.get("source") or "all sources",
    }


@router.get("/identity", response_class=HTMLResponse)
def identity_dashboard(request: Request, source: str = "",
                       principal: Principal = Depends(current_principal)):
    """The Identity Review dashboard: live counts, the Run Identity Matching control, the
    unresolved-review queue (optionally filtered by source), and a link into the existing
    duplicate-candidate merge review."""
    active_source = source if source in SOURCES else ""
    with engine.connect() as conn:
        counts = _counts(conn)
        unresolved = list_ambiguous_unlinked(source_system=active_source or None, conn=conn)
    return templates.TemplateResponse(
        request=request, name="demo/identity.html",
        context={
            "counts": counts,
            "sources": SOURCES,
            "active_source": active_source,
            "unresolved": unresolved,
            "last_run": _last_run(request),
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/identity/run-matching")
def run_matching(request: Request, source: str = "",
                 principal: Principal = Depends(current_principal)):
    """Run the EXISTING promotion/matching intelligence (app.matching.promote.promote_unlinked)
    over unlinked source contacts — for the selected source, or all sources. Never duplicates the
    logic; just invokes it and shows the report."""
    active_source = source if source in SOURCES else ""
    report = promote_unlinked(source_system=active_source or None)
    write_audit_event(
        action="identity.demo_run_matching", entity_type="source_contact",
        entity_id=active_source or "all",
        actor_user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None) or f"demo-identity-{uuid.uuid4()}",
        metadata=report.to_dict(),
    )
    params = (
        f"ran=1&inspected={report.inspected}&created={report.created}"
        f"&linked_existing={report.linked_existing}&ambiguous={report.ambiguous}"
    )
    if active_source:
        params += f"&source={active_source}"
    return RedirectResponse(f"/demo/identity?{params}", status_code=303)
