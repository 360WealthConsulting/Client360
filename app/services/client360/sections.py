"""Client 360 Workspace section builders (Phase D.40).

Each builder composes ONE section from the authoritative domain reads — it never mutates, never
recomputes a domain calculation, and never reads an ``rm_*`` projection table directly. Record scope is
already verified once at the workspace boundary (``service.get_workspace``) before any builder runs, so
person-keyed factual reads (which do not self-check scope) are safe here. Builders take a shared ``ctx``
(``entity_type``, ``person_id``, ``household_id``, ``portfolio``, ``subject``) and return a plain dict.
Unmodeled financial concepts (banking, retirement accounts, outside assets, liabilities, net worth) are
reported as ``not tracked`` — the platform has no such domain and the workspace does not invent one.
"""
from __future__ import annotations

from datetime import UTC

# Financial concepts the platform does not model — surfaced honestly, never fabricated.
_UNMODELLED_FINANCIAL = ("banking", "retirement_accounts", "outside_assets", "liabilities", "net_worth")


def _pid(ctx):
    return ctx.get("person_id")


def _hid(ctx):
    return ctx.get("household_id")


def summary(principal, ctx):
    """Household overview + client health + assigned advisor/team + last contact / next activity.
    Wealth/insurance/tax figures are presented side-by-side (never summed — units differ)."""
    from app.security.object_security import resolve_assignments
    snap = ctx.get("snapshot") or {}
    et, eid = ctx["entity_type"], ctx["entity_id"]
    advisors = resolve_assignments(et, eid)
    return {
        "snapshot": snap,
        "assigned": advisors,
        "household_id": _hid(ctx),
        "household_name": ctx.get("household_name"),
        "members": ctx.get("members"),
        "last_contact": ctx.get("last_contact"),
        "next_activity": ctx.get("next_activity"),
        # Client health / status / tier / risk are not modelled as structured fields on the person.
        "client_status": None, "service_tier": None, "risk_profile": None,
        "unavailable": ["client_status", "service_tier", "risk_profile"],
    }


def financial(principal, ctx):
    """Investment accounts / AUM / cash / allocation (authoritative portfolio math, reused) +
    insurance face + benefit relationships — side by side. No net-worth roll-up (not modelled)."""
    portfolio = ctx.get("portfolio") or {}
    pid, hid = _pid(ctx), _hid(ctx)
    section = {
        "aum": portfolio.get("aum", portfolio.get("total_aum")) or 0,
        "cash": portfolio.get("cash") or 0,
        "cash_percent": portfolio.get("cash_percent") or 0,
        "allocation": portfolio.get("allocation") or portfolio.get("asset_allocation") or {},
        "accounts": portfolio.get("accounts") or [],
        "household_aum": (portfolio.get("household") or {}).get("aum",
                          (portfolio.get("household") or {}).get("total_aum")) or 0,
        # not summed — the units are not comparable.
        "not_summed": True,
        "not_tracked": list(_UNMODELLED_FINANCIAL),
    }
    if pid:
        from app.services.benefits_domain import client_benefits_summary
        from app.services.insurance import client_policy_summary
        section["insurance"] = client_policy_summary(pid, hid)
        section["benefits"] = client_benefits_summary(pid, hid)
    return section


def tax(principal, ctx):
    """Tax engagement summary + open tax exceptions (returns/filing/extensions/missing-docs/estimated
    payments are engagement-keyed, not person-keyed — surfaced via exceptions + the deep link)."""
    from app.services.exception_engine import open_exceptions_for_client
    from app.services.tax_domain import client_engagement_summary
    pid, hid = _pid(ctx), _hid(ctx)
    engagements = client_engagement_summary(pid, hid) if pid else {"active": 0}
    tax_exc = [e for e in open_exceptions_for_client(pid, hid) if e.get("domain") == "tax"] if pid else []
    return {"engagements": engagements, "open_exceptions": tax_exc,
            "note": "Return/filing detail opens on the Tax surface."}


def insurance(principal, ctx):
    """Coverage summary + renewal/review items due (policy/case detail opens on the Insurance surface)."""
    from app.services.insurance import client_policy_summary, reviews_due_for_people
    pid, hid = _pid(ctx), _hid(ctx)
    coverage = client_policy_summary(pid, hid) if pid else {"policy_count": 0, "total_face": 0}
    renewals = reviews_due_for_people({pid}) if pid else []
    return {"coverage": coverage, "renewals": renewals}


def benefits(principal, ctx):
    """Employer/benefit relationships (employer plans / 401k / HSA / FSA detail is org-keyed and opens
    on the Benefits/Organizations surface)."""
    from app.services.benefits_domain import client_benefits_summary
    pid = _pid(ctx)
    return {"summary": client_benefits_summary(pid) if pid else {"employments": 0}}


def opportunities(principal, ctx):
    """Pipeline for this client + recommendations (reused Advisor Intelligence signals, not regenerated)."""
    from app.services.advisor_intelligence import get_client_signals
    from app.services.opportunity.service import opportunities_for_person
    pid = _pid(ctx)
    if not pid:
        return {"pipeline": [], "recommendations": []}
    pipeline = opportunities_for_person(principal, pid, open_only=False, limit=50)
    recs = [s.to_dict() if hasattr(s, "to_dict") else {"title": getattr(s, "title", None)}
            for s in get_client_signals(principal, pid) if getattr(s, "category", None) == "recommendation"]
    return {"pipeline": pipeline, "recommendations": recs}


def documents(principal, ctx):
    """Client documents (uploads / classification / review status) via the document platform."""
    from app.services.document_platform.relationships import documents_for_entity
    et, eid = ctx["entity_type"], ctx["entity_id"]
    return {"documents": documents_for_entity(principal, et, eid, limit=25)}


def meetings(principal, ctx):
    """Upcoming + previous meetings from the client's calendar-event timeline (authoritative)."""
    from datetime import datetime

    from app.services.timeline import recent_events
    scope = ctx.get("scope_ids")
    now = datetime.now(UTC)
    events = recent_events(scope, event_types=("calendar_event",), limit=50)
    events = [e for e in events if _matches(e, ctx)]
    upcoming = [e for e in events if (e.get("event_time") and e["event_time"] >= now)]
    previous = [e for e in events if (e.get("event_time") and e["event_time"] < now)]
    return {"upcoming": upcoming[:10], "previous": previous[:10]}


def compliance(principal, ctx):
    """Outstanding reviews, annual-review status, open exceptions, and review history."""
    from app.services.annual_review import list_completed_sessions, open_session_for
    from app.services.compliance.reviews import person_reviews
    from app.services.exception_engine import open_exceptions_for_client
    pid, hid = _pid(ctx), _hid(ctx)
    reviews = person_reviews(principal, pid) if pid else []
    open_states = {"pending_submission", "pending_assignment", "pending_review",
                   "blocked_pending_authorized_reviewer"}
    return {
        "reviews": reviews,
        "outstanding": [r for r in reviews if r.get("status") in open_states],
        "annual_review_open": open_session_for(principal, pid) if pid else None,
        "annual_review_history": list_completed_sessions(principal, pid, limit=5) if pid else [],
        "exceptions": open_exceptions_for_client(pid, hid) if pid else [],
    }


def timeline(principal, ctx):
    """The unified cross-domain activity timeline (references only — never duplicates event storage)."""
    from app.services.activity_timeline.service import client_timeline, household_timeline
    pid, hid = _pid(ctx), _hid(ctx)
    if pid:
        result = client_timeline(principal, pid, page=ctx.get("page", 1), page_size=25)
    elif hid:
        result = household_timeline(principal, hid, page=ctx.get("page", 1), page_size=25)
    else:
        result = None
    if result is None:
        return {"rows": [], "total": 0, "page": 1, "page_size": 25, "pages": 0}
    return {**result, "rows": [r.to_dict() if hasattr(r, "to_dict") else r for r in result["rows"]]}


def communications(principal, ctx):
    """Unified engagement summary for the client — recent interactions across every channel, composed by
    the D.44 engagement layer over the authoritative subsystems (never a second store)."""
    from app.services.communications.engagement import engagement_summary, engagement_timeline
    pid, hid = _pid(ctx), _hid(ctx)
    summary = engagement_summary(principal, person_id=pid, household_id=hid)
    recent = engagement_timeline(principal, person_id=pid, household_id=hid, page=1, page_size=8)
    rows = recent.get("rows", []) if recent else []
    return {"summary": summary, "recent": rows, "source": "communications.engagement",
            "not_a_second_store": True}


def knowledge(principal, ctx):
    """Connected entities + relationship explanations, composed by the D.45 knowledge layer over the
    authoritative relationship engine + scoped reads (never a graph database, never a second store)."""
    from app.services.knowledge import knowledge_graph, knowledge_summary
    pid, hid = _pid(ctx), _hid(ctx)
    summary = knowledge_summary(principal, person_id=pid, household_id=hid)
    graph = knowledge_graph(principal, person_id=pid, household_id=hid)
    if graph is None or not graph.get("enabled"):
        return {"summary": summary, "nodes": [], "edges": [], "explanations": [],
                "source": "knowledge.graph", "not_a_graph_db": True}
    return {"summary": summary, "nodes": graph["nodes"], "edges": graph["edges"],
            "explanations": graph.get("explanations", []), "suppressed_nodes": graph["suppressed_nodes"],
            "source": "knowledge.graph", "not_a_graph_db": True}


def recommendations(principal, ctx):
    """Client-specific explainable recommendations (missing reviews, outstanding requests, planning
    opportunities, communication follow-up, compliance tasks), composed by the D.46 operational-intelligence
    layer over the authoritative recommendation sources (never a second recommendation engine)."""
    from app.services.recommendations import client_recommendations, recommendation_summary
    pid, hid = _pid(ctx), _hid(ctx)
    summary = recommendation_summary(principal, person_id=pid, household_id=hid)
    result = client_recommendations(principal, pid) if pid else None
    rows = result.get("recommendations", []) if result else []
    return {"summary": summary, "recommendations": rows, "source": "recommendations.engine",
            "not_a_second_engine": True}


def compliance_summary(principal, ctx):
    """Supervisory compliance oversight for the client (open reviews + supervisory status + outstanding
    exceptions), composed by the D.47 compliance-intelligence layer. Supervisor-only (the section is gated
    by compliance.supervise); never a second compliance engine, never mutates."""
    from app.services.compliance_intelligence import client_compliance
    from app.services.compliance_intelligence import compliance_summary as _summary
    pid, hid = _pid(ctx), _hid(ctx)
    summary = _summary(principal, person_id=pid, household_id=hid)
    result = client_compliance(principal, pid) if pid else None
    return {"summary": summary,
            "reviews": result.get("reviews", []) if result else [],
            "exceptions": result.get("exceptions", []) if result else [],
            "source": "compliance_intelligence", "not_a_second_engine": True}


def executive(principal, ctx):
    """Firm executive context (KPIs + firm-intelligence observations) for an executive viewing this client,
    composed by the D.48 executive-intelligence layer over the SINGLE Analytics Registry (never a second
    analytics engine). Gated by analytics.executive; never mutates."""
    from app.services.executive_intelligence import executive_summary
    summary = executive_summary(principal)
    return {"kpis": summary.get("kpis", {}), "observations": summary.get("observations", []),
            "governing_services": summary.get("governing_services", []),
            "source": "executive_intelligence", "not_a_second_analytics_engine": True}


def relationships(principal, ctx):
    """Household members + the read-only relationship graph (beneficiaries/trustees/businesses/
    employers/dependents/advisors) + assigned advisors."""
    from app.security.object_security import resolve_assignments
    from app.services.relationships import build_relationship_graph, get_person_households
    pid = _pid(ctx)
    graph = build_relationship_graph(pid) if pid else {"categories": {}, "relationships": []}
    households = get_person_households(pid) if pid else ctx.get("member_households") or []
    return {"graph": graph, "households": households,
            "assigned": resolve_assignments(ctx["entity_type"], ctx["entity_id"])}


def work(principal, ctx):
    """Open advisor work for this client (deep-links into the authoritative advisor-work surface)."""
    from app.services.advisor_work import person_work
    pid = _pid(ctx)
    # Key is "work_items" (not "items") so Jinja attribute lookup does not collide with dict.items.
    return {"work_items": person_work(principal, pid, open_only=True) if pid else []}


def operational_workload(principal, ctx):
    """A compact operational-workload summary for this client (D.49) — composed read-only from the Practice
    Management layer over the Unified Work Queue (book-scoped counts + aging). Never a second work engine;
    deep-links to the authoritative work surface."""
    from app.services.practice_management import client_workload
    pid = _pid(ctx)
    return {**(client_workload(principal, pid) if pid else {"enabled": False, "open": 0}),
            "source": "practice_management.client_workload", "not_a_second_engine": True}


def _matches(event, ctx):
    pid, hid = _pid(ctx), _hid(ctx)
    if pid and event.get("person_id") == pid:
        return True
    if hid and event.get("household_id") == hid:
        return True
    return not (pid or hid)
