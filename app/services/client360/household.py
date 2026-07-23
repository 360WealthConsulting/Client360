"""Household 360 Workspace composition (Phase D.41).

Upgrades the household path of the Client 360 surface (`/client/household/{id}`) into a full household
workspace: household context, a member directory, a member-by-member overview, member-level rollups
(financial / tax / insurance / benefits / opportunities / documents / meetings / compliance / work),
a household activity timeline, a household relationship graph, and a compact snapshot.

It is a read-only COMPOSITION over the authoritative domain services — NOT a second household database,
no shadow household/person record, no duplicate portfolio aggregation, no new event bus, and it never
mutates (every edit deep-links into the authoritative workflow). Record scope is verified ONCE at the
household boundary; member visibility is then gated by the existing `accessible_person_ids` rule (which
inherits household→member access) — members not in scope are suppressed (fail closed). The household
portfolio total reuses the single authoritative `get_household_portfolio` aggregation; incompatible
figures (insurance face, opportunity revenue, benefits, tax) are shown side by side and NEVER summed,
and no net-worth is fabricated (banking/retirement/outside-assets/liabilities are not modelled).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from app.security.authorization import accessible_person_ids, record_in_scope

# Household section capabilities (reuse the D.40 domain read capabilities; None → page-level client.read).
HOUSEHOLD_SECTIONS = (
    ("summary", None),
    ("members", None),
    ("financial", None),
    ("tax", "tax.read"),
    ("insurance", "insurance.read"),
    ("benefits", "benefits.read"),
    ("opportunities", "opportunity.view"),
    ("documents", "documents.view"),
    ("meetings", None),
    ("compliance", "compliance.review.read"),
    ("work", "work.read"),
    ("timeline", "timeline.read"),
    ("relationships", None),
)
GRAPH_DEPTH = 1   # each member's relationship graph is one-hop; the household adds a membership hop.


def get_household_workspace(principal, household_id, *, page=1):
    """Compose the Household 360 workspace. Returns None if the household is out of record scope."""
    household_id = int(household_id)
    if not record_in_scope(principal, "household", household_id):
        return None
    ctx = _context(principal, household_id, page)
    if ctx is None:
        return None

    built, timings, suppressed = {}, {}, []
    for key, cap in HOUSEHOLD_SECTIONS:
        if cap is not None and not principal.can(cap):
            suppressed.append(key)
            continue
        t0 = time.perf_counter()
        try:
            built[key] = _SECTION_BUILDERS[key](principal, ctx)
        except Exception as exc:   # per-section failure isolation (fail closed)
            built[key] = {"error": str(exc)}
        timings[key] = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "entity_type": "household", "entity_id": household_id, "household_id": household_id,
        "household_name": ctx["household_name"],
        "display_name": ctx["household_name"] or f"Household {household_id}",
        "context": ctx["public"],
        "member_directory": built.get("members", {}).get("directory", []),
        "snapshot": _snapshot(principal, ctx, built),
        "sections": built,
        "section_keys": [k for k, cap in HOUSEHOLD_SECTIONS if cap is None or principal.can(cap)],
        "suppressed_sections": suppressed,
        "suppressed_members": ctx["suppressed_members"],
        "quick_actions": _quick_actions(principal, ctx),
        "relationship_graph": built.get("relationships", {}).get("graph"),
        "timings": timings,
    }


# --- context -----------------------------------------------------------------

def _context(principal, household_id, page):
    from sqlalchemy import select

    from app.db import engine, households
    from app.services.portfolio import get_household_portfolio
    with engine.connect() as c:
        row = c.execute(select(households).where(households.c.id == household_id)).mappings().first()
        if row is None:
            return None
        accessible = accessible_person_ids(c, principal)   # None = unrestricted (record.read_all)

    portfolio = _safe(lambda: get_household_portfolio(household_id), {})
    roster = portfolio.get("members") or []
    primary = next((m for m in roster if m.get("is_primary")), roster[0] if roster else None)

    scoped, suppressed = [], []
    for m in roster:
        visible = accessible is None or m["id"] in accessible
        m = {**m, "in_scope": visible}
        (scoped if visible else suppressed).append(m)
    member_ids = [m["id"] for m in scoped]

    return {
        "household_id": household_id, "household_name": row.get("name"),
        "household_row": dict(row), "portfolio": portfolio,
        "roster": roster, "members": scoped, "member_ids": member_ids,
        "suppressed_members": [{"id": m["id"], "name": m.get("full_name")} for m in suppressed],
        "primary": primary, "page": page,
        "public": {
            "household_id": household_id, "household_name": row.get("name"),
            "primary_member": ({"id": primary["id"], "name": primary.get("full_name")}
                               if primary else None),
            "member_count": len(roster), "active_client_count": len(scoped),
            "member_ids": member_ids,
        },
    }


# --- sections ----------------------------------------------------------------

def _summary(principal, ctx):
    from app.security.object_security import resolve_assignments
    from app.services.timeline import recent_events
    now = datetime.now(UTC)
    events = _safe(lambda: recent_events(set(ctx["member_ids"]) or {-1}, limit=50), [])
    past = [e for e in events if e.get("event_time") and e["event_time"] <= now]
    future = [e for e in events
              if e.get("event_type") == "calendar_event" and e.get("event_time") and e["event_time"] > now]
    return {
        "household_name": ctx["household_name"],
        "primary_member": ctx["public"]["primary_member"],
        "member_count": ctx["public"]["member_count"],
        "active_client_count": ctx["public"]["active_client_count"],
        "assigned": resolve_assignments("household", ctx["household_id"]),
        "last_activity": _fmt_event(max(past, key=lambda e: e["event_time"]) if past else None),
        "next_activity": _fmt_event(min(future, key=lambda e: e["event_time"]) if future else None),
        # Household status / tier / risk are not modelled as structured fields.
        "unavailable": ["household_status", "service_tier", "risk_profile"],
    }


def _members(principal, ctx):
    """First-class member directory — summarize + navigate; the person workspace holds the detail."""
    from app.security.object_security import resolve_assignments
    directory = []
    for m in ctx["roster"]:
        pid = m["id"]
        in_scope = m.get("in_scope", False)
        entry = {
            "person_id": pid, "name": m.get("full_name"),
            "relationship": m.get("relationship_type"), "is_primary": bool(m.get("is_primary")),
            "in_scope": in_scope, "deep_link": f"/client/{pid}",
            "email": m.get("primary_email") if in_scope else None,
            "assigned": resolve_assignments("person", pid) if in_scope else [],
        }
        if in_scope:
            entry["indicators"] = _member_indicators(principal, pid)
        directory.append(entry)
    return {"directory": directory, "member_count": len(ctx["roster"]),
            "in_scope_count": len(ctx["member_ids"]), "suppressed": ctx["suppressed_members"]}


def _member_indicators(principal, pid):
    """Compact available-domain indicators for one member (navigation summary, not full detail)."""
    ind = {}
    if principal.can("tax.read"):
        from app.services.tax_domain import client_engagement_summary
        ind["tax"] = _safe(lambda: client_engagement_summary(pid).get("active", 0), 0)
    if principal.can("insurance.read"):
        from app.services.insurance import client_policy_summary
        ind["insurance"] = _safe(lambda: client_policy_summary(pid).get("policy_count", 0), 0)
    if principal.can("benefits.read"):
        from app.services.benefits_domain import client_benefits_summary
        ind["benefits"] = _safe(lambda: client_benefits_summary(pid).get("employments", 0), 0)
    if principal.can("advisor_work.read"):
        from app.services.advisor_work import person_work
        ind["work"] = _safe(lambda: len(person_work(principal, pid, open_only=True)), 0)
    ind["portfolio_aum"] = _safe(lambda: _person_aum(pid), 0)
    return ind


def _financial(principal, ctx):
    """Portfolio rollup: the authoritative household total (reused, never re-summed) + per-member AUM +
    each member's contribution. Insurance/benefits/opportunity/tax are NOT summed into assets."""
    hp = ctx["portfolio"]
    household_aum = float(hp.get("aum") or 0)
    members = []
    for m in ctx["members"]:
        aum = float(_safe(lambda pid=m["id"]: _person_aum(pid), 0) or 0)
        members.append({"person_id": m["id"], "name": m.get("full_name"), "aum": aum,
                        "contribution_pct": round(aum / household_aum * 100, 1) if household_aum else None})
    return {
        "household_aum": household_aum, "household_cash": float(hp.get("cash") or 0),
        "allocation": hp.get("allocation") or {}, "accounts": hp.get("accounts") or [],
        "members": members,
        "not_summed": True,   # portfolio assets are never combined with insurance/opportunity/benefit/tax
        "not_tracked": ["banking", "retirement_accounts", "outside_assets", "liabilities", "net_worth"],
    }


def _tax(principal, ctx):
    """Per-member tax engagement counts + open tax exceptions. Filing/dependency/joint relationships
    are NOT inferred from household membership."""
    from app.services.exception_engine import open_exceptions_for_people
    from app.services.tax_domain import client_engagement_summary
    members = [{"person_id": pid, "name": _name(ctx, pid),
                "engagements": _safe(lambda p=pid: client_engagement_summary(p).get("active", 0), 0)}
               for pid in ctx["member_ids"]]
    exc = [e for e in _safe(lambda: open_exceptions_for_people(set(ctx["member_ids"])), [])
           if e.get("domain") == "tax"]
    return {"members": members, "open_exceptions": exc, "inferred_relationships": False,
            "note": "Filing status / joint returns / dependency are not inferred from membership."}


def _insurance(principal, ctx):
    from app.services.insurance import client_policy_summary, reviews_due_for_people
    members = [{"person_id": pid, "name": _name(ctx, pid),
                "coverage": _safe(lambda p=pid: client_policy_summary(p), {"policy_count": 0, "total_face": 0})}
               for pid in ctx["member_ids"]]
    renewals = _safe(lambda: reviews_due_for_people(set(ctx["member_ids"])), [])
    return {"members": members, "renewals": renewals, "is_asset": False}


def _benefits(principal, ctx):
    from app.services.benefits_domain import client_benefits_summary
    members = [{"person_id": pid, "name": _name(ctx, pid),
                "summary": _safe(lambda p=pid: client_benefits_summary(p), {"employments": 0})}
               for pid in ctx["member_ids"]]
    return {"members": members}


def _opportunities(principal, ctx):
    from app.services.opportunity.service import opportunities_for_people
    rows = _safe(lambda: opportunities_for_people(set(ctx["member_ids"]), open_only=False, limit=200), [])
    for r in rows:
        r["member_name"] = _name(ctx, r.get("person_id"))
    return {"opportunities": rows, "member_attributed": True, "summed_into_assets": False}


def _documents(principal, ctx):
    """Household-anchored documents UNION per-member documents, deduped by document id."""
    from app.services.document_platform.relationships import documents_for_entity
    seen, docs = set(), []
    for et, eid in [("household", ctx["household_id"])] + [("person", p) for p in ctx["member_ids"]]:
        for d in _safe(lambda e=et, i=eid: documents_for_entity(principal, e, i, limit=50), []):
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            d["provenance"] = et
            docs.append(d)
    return {"documents": docs, "deduped_by": "document_id", "count": len(docs)}


def _meetings(principal, ctx):
    """Upcoming + previous meetings from the household + members' calendar-event timeline, deduped."""
    from app.services.timeline import recent_events
    now = datetime.now(UTC)
    raw = _safe(lambda: recent_events(set(ctx["member_ids"]) or {-1},
                                      event_types=("calendar_event",), limit=100), [])
    seen, events = set(), []
    for e in raw:
        key = e.get("id")
        if key in seen:
            continue
        seen.add(key)
        events.append(e)
    upcoming = [e for e in events if e.get("event_time") and e["event_time"] >= now]
    previous = [e for e in events if e.get("event_time") and e["event_time"] < now]
    return {"upcoming": [_fmt_event(e) for e in upcoming[:10]],
            "previous": [_fmt_event(e) for e in previous[:10]],
            "deduped": len(raw) - len(events)}


def _compliance(principal, ctx):
    """Member-level reviews + open exceptions, plus household-level exception count. Provenance labelled;
    compliance decision/approval logic is unchanged."""
    from app.services.compliance.reviews import person_reviews
    from app.services.exception_engine import open_count_for_client, open_exceptions_for_people
    open_states = {"pending_submission", "pending_assignment", "pending_review",
                   "blocked_pending_authorized_reviewer"}
    reviews = []
    for pid in ctx["member_ids"]:
        for r in _safe(lambda p=pid: person_reviews(principal, p), []):
            if r.get("status") in open_states:
                reviews.append({**r, "provenance": "member", "person_id": pid,
                                "member_name": _name(ctx, pid)})
    exceptions = _safe(lambda: open_exceptions_for_people(set(ctx["member_ids"])), [])
    return {"outstanding_reviews": reviews, "exceptions": exceptions,
            "household_open_exception_count": _safe(
                lambda: open_count_for_client(None, ctx["household_id"]), 0),
            "provenance_levels": ["household", "member"]}


def _work(principal, ctx):
    """Household work — REUSES the D.39 Unified Work Queue (no re-query of task/workflow/exception).
    Household-anchored items; attributed to members by person_id."""
    from app.services.work_queue import compose_queue
    result = _safe(lambda: compose_queue(principal, filters={"household_id": ctx["household_id"]},
                                         page=ctx.get("page", 1), page_size=50),
                   {"rows": [], "total": 0, "counts": {}})
    for r in result.get("rows", []):
        r["member_name"] = _name(ctx, r.get("person_id"))
    return {"rows": result.get("rows", []), "total": result.get("total", 0),
            "counts": result.get("counts", {}), "source": "work_queue.compose_queue",
            "note": "Member-only work (no household anchor) is shown on the member's own workspace."}


def _timeline(principal, ctx):
    """Household activity timeline — REUSES household_timeline (already merges members, dedups by
    event_id, orders deterministically). Never writes timeline rows."""
    from app.services.activity_timeline.service import household_timeline
    result = household_timeline(principal, ctx["household_id"], page=ctx.get("page", 1), page_size=25)
    if result is None:
        return {"rows": [], "total": 0, "page": 1, "page_size": 25, "pages": 0, "dedup_count": 0}
    rows = [r.to_dict() if hasattr(r, "to_dict") else r for r in result["rows"]]
    # a defensive second dedup pass at the composition layer (the service already deduped by event_id).
    seen, deduped = set(), []
    for r in rows:
        k = r.get("event_id")
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    return {**result, "rows": deduped, "dedup_count": len(rows) - len(deduped)}


def _relationships(principal, ctx):
    """Household relationship graph — composed from each member's one-hop graph + household memberships,
    with node/edge dedup, a depth cap, and cycle protection. Read-only; never creates/mutates a
    relationship (no new relationship engine)."""
    from app.services.relationships import build_relationship_graph
    nodes, edges, node_keys, edge_keys = {}, [], set(), set()

    def _add_node(key, data):
        if key not in node_keys:
            node_keys.add(key)
            nodes[key] = data

    def _add_edge(a, b, code, label):
        ek = (a, b, code)
        if a != b and ek not in edge_keys:   # cycle/self-loop protection via key dedup
            edge_keys.add(ek)
            edges.append({"from": a, "to": b, "code": code, "label": label})

    hkey = f"household:{ctx['household_id']}"
    _add_node(hkey, {"type": "household", "id": ctx["household_id"], "name": ctx["household_name"]})
    for m in ctx["members"]:
        mkey = f"person:{m['id']}"
        _add_node(mkey, {"type": "person", "id": m["id"], "name": m.get("full_name"),
                         "is_primary": bool(m.get("is_primary"))})
        _add_edge(hkey, mkey, "household_member", m.get("relationship_type") or "member")
        graph = _safe(lambda p=m["id"]: build_relationship_graph(p), {"relationships": []})
        for rel in graph.get("relationships", [])[:100]:   # depth cap: one hop per member
            if rel.get("code") == "household_member":
                continue
            tkey = (f"person:{rel['person_id']}" if rel.get("person_id")
                    else f"{rel.get('entity_type', 'entity')}:{rel.get('entity_id') or rel.get('household_id')}")
            _add_node(tkey, {"type": rel.get("entity_type") or "entity",
                             "id": rel.get("entity_id") or rel.get("person_id") or rel.get("household_id"),
                             "name": rel.get("name"), "category": rel.get("code")})
            _add_edge(mkey, tkey, rel.get("code"), rel.get("label"))
    return {"graph": {"nodes": list(nodes.values()), "edges": edges,
                      "node_count": len(nodes), "edge_count": len(edges),
                      "depth_limit": GRAPH_DEPTH, "cycle_protection": True}}


_SECTION_BUILDERS = {
    "summary": _summary, "members": _members, "financial": _financial, "tax": _tax,
    "insurance": _insurance, "benefits": _benefits, "opportunities": _opportunities,
    "documents": _documents, "meetings": _meetings, "compliance": _compliance,
    "work": _work, "timeline": _timeline, "relationships": _relationships,
}


# --- snapshot + quick actions ------------------------------------------------

def _snapshot(principal, ctx, built):
    fin = built.get("financial") or {}
    work = built.get("work") or {}
    opps = built.get("opportunities") or {}
    meet = built.get("meetings") or {}
    comp = built.get("compliance") or {}
    graph = (built.get("relationships") or {}).get("graph") or {}
    businesses = sum(1 for n in graph.get("nodes", []) if n.get("type") == "business")
    estate = sum(1 for n in graph.get("nodes", []) if n.get("type") in ("trust", "estate"))
    return {
        "kind": "household_snapshot",
        "household_id": ctx["household_id"], "household_name": ctx["household_name"],
        "primary_member": ctx["public"]["primary_member"],
        "member_count": ctx["public"]["member_count"],
        "active_members": ctx["public"]["active_client_count"],
        "portfolio_assets": fin.get("household_aum", 0),
        "open_work": work.get("total", 0),
        "open_opportunities": len(opps.get("opportunities", [])),
        "upcoming_meetings": len(meet.get("upcoming", [])),
        "compliance_items": len(comp.get("outstanding_reviews", [])),
        "connected_businesses": businesses, "connected_estate_entities": estate,
        # incompatible figures are presented side by side — never a composite household score.
        "not_summed": True,
    }


def _quick_actions(principal, ctx):
    hid = ctx["household_id"]
    prim = ctx["primary"]["id"] if ctx["primary"] else None
    actions = [
        ("schedule_meeting", "Schedule Household Meeting", "scheduling.view", f"/scheduling?household_id={hid}"),
        ("upload_document", "Upload Household Document", "documents.view", f"/document-library?household_id={hid}"),
        ("create_task", "Create Task", "work.read", f"/operations/items?household_id={hid}"),
        ("start_tax", "Start Tax Work", "tax.read", f"/tax/intake?household_id={hid}"),
        ("create_opportunity", "Create Opportunity", "opportunity.view", f"/opportunities?household_id={hid}"),
        ("start_insurance_case", "Start Insurance Case", "insurance.read", f"/insurance?household_id={hid}"),
        ("send_secure_message", "Send Secure Message", "communications.read", f"/communications?household_id={hid}"),
    ]
    # person-scoped surfaces are prefilled with the primary member.
    if prim:
        actions += [
            ("add_note", "Add Household Note", "client.read", f"/people/{prim}/notes"),
            ("meeting_prep", "Generate Household Meeting Prep", "client.read", f"/workspace/meetings/{prim}"),
        ]
    return [{"key": k, "label": lbl, "href": href} for k, lbl, cap, href in actions if principal.can(cap)]


# --- helpers -----------------------------------------------------------------

def _person_aum(pid):
    from app.services.portfolio import get_person_portfolio
    p = get_person_portfolio(pid)
    return float(p.get("aum", p.get("total_aum")) or 0)


def _name(ctx, pid):
    for m in ctx["roster"]:
        if m["id"] == pid:
            return m.get("full_name")
    return None


def _fmt_event(e):
    if not e:
        return None
    return {"title": e.get("title"), "event_time": str(e.get("event_time")),
            "event_type": e.get("event_type"),
            "link": (f"/client/{e['person_id']}" if e.get("person_id") else None)}


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default
