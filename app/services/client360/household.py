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
from app.services.person_names import person_row_display_name

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
    ("communications", "communications.view"),
    ("knowledge", None),
    ("recommendations", None),
    ("compliance_summary", "compliance.supervise"),
    ("executive", "analytics.executive"),
    ("work", "work.read"),
    ("operational_workload", "capacity.read"),
    ("document_intelligence", "documents.view"),
    ("automation_history", "automation.view"),
    ("data_governance", "governance.view"),
    ("external_integrations", "integration.view"),
    ("security_access", "security.view"),
    ("business_continuity", "observability.view"),
    ("technology_dependencies", "integration.view"),
    ("financial_relationship", "analytics.executive"),
    ("risk_controls", "compliance.supervise"),
    ("evidence_readiness", "compliance.supervise"),
    ("operational_impact", "observability.view"),
    ("servicing_team", "capacity.read"),
    ("knowledge_documentation", "documents.view"),
    ("change_impact", "observability.view"),
    ("platform_dependencies", "observability.view"),
    ("authorization_context", "observability.view"),
    ("data_governance_metadata", "governance.view"),
    ("timeline", "timeline.read"),
    ("tasks", None),
    ("notes", None),
    ("audit", "audit.read"),
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
        "suppressed_members": [{"id": m["id"], "name": person_row_display_name(m)} for m in suppressed],
        "primary": primary, "page": page,
        "public": {
            "household_id": household_id, "household_name": row.get("name"),
            "primary_member": ({"id": primary["id"], "name": person_row_display_name(primary)}
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
            "person_id": pid, "name": person_row_display_name(m),
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
        members.append({"person_id": m["id"], "name": person_row_display_name(m), "aum": aum,
                        "contribution_pct": round(aum / household_aum * 100, 1) if household_aum else None})
    return {
        "household_aum": household_aum, "household_cash": float(hp.get("cash") or 0),
        "allocation": hp.get("allocation") or {}, "accounts": hp.get("accounts") or [],
        "members": members,
        "not_summed": True,   # portfolio assets are never combined with insurance/opportunity/benefit/tax
        "not_tracked": ["banking", "retirement_accounts", "outside_assets", "liabilities", "net_worth"],
    }


def _tax(principal, ctx):
    """The household Tax tab — the same tax operating center as the person workspace, scoped to the
    household (ADR-073). Composed from the authoritative tax domain; no inferred filing relationships."""
    from app.services.client360.tax_workspace import build_tax_workspace
    hid = ctx["household_id"]
    members = ctx.get("member_ids") or []
    tws = build_tax_workspace(principal, person_id=(members[0] if members else None),
                              household_id=hid, scope_ids=members)
    tws["open_exceptions"] = tws.get("missing_and_exceptions", {}).get("exceptions", [])
    tws["inferred_relationships"] = False
    return tws


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
    """Household-anchored documents UNION per-member documents, deduped by document id — rendered as the
    same canonical Documents operating center as the person workspace (shared enrichment)."""
    from app.services.client360.sections import (
        _attach_classification,
        _attach_ocr,
        _attach_source_refs,
        _merge_documents,
        _vault_rows,
        documents_view_model,
        enrich_documents,
    )
    from app.services.document_platform.relationships import documents_for_entity
    seen, rows = set(), []
    for et, eid in [("household", ctx["household_id"])] + [("person", p) for p in ctx["member_ids"]]:
        for d in _safe(lambda e=et, i=eid: documents_for_entity(principal, e, i, limit=50), []):
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            d["provenance"] = et
            rows.append(d)
    canonical = _attach_classification(_attach_ocr(_attach_source_refs(enrich_documents(rows))))
    # Merge Vault documents linked to the household or any current member (Vault permissions/audit stay in
    # the Vault service). Deduped against canonical by checksum where deterministically possible.
    vault = _vault_rows(principal, person_ids=ctx["member_ids"], household_id=ctx["household_id"])
    merged = _merge_documents(canonical, vault)
    return {**documents_view_model(merged), "deduped_by": "document_id + checksum", "count": len(merged)}


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


def _communications(principal, ctx):
    """Household unified engagement summary — composed by the D.44 engagement layer over the household's
    authoritative activity timeline (member-merged, deduped). Never a second store."""
    from app.services.communications.engagement import engagement_summary, engagement_timeline
    hid = ctx["household_id"]
    summary = engagement_summary(principal, household_id=hid)
    recent = engagement_timeline(principal, household_id=hid, page=1, page_size=8)
    return {"summary": summary, "recent": recent.get("rows", []) if recent else [],
            "source": "communications.engagement", "not_a_second_store": True}


def _knowledge(principal, ctx):
    """Household knowledge graph — connected entities (businesses, trusts, shared advisors, professionals)
    + relationship explanations, composed by the D.45 knowledge layer over the household's members'
    authoritative relationship graphs. Never a graph database, never a second store."""
    from app.services.knowledge import knowledge_graph, knowledge_summary
    hid = ctx["household_id"]
    summary = knowledge_summary(principal, household_id=hid)
    graph = knowledge_graph(principal, household_id=hid)
    if graph is None or not graph.get("enabled"):
        return {"summary": summary, "nodes": [], "edges": [], "explanations": [],
                "source": "knowledge.graph", "not_a_graph_db": True}
    return {"summary": summary, "nodes": graph["nodes"], "edges": graph["edges"],
            "explanations": graph.get("explanations", []), "suppressed_nodes": graph["suppressed_nodes"],
            "source": "knowledge.graph", "not_a_graph_db": True}


def _recommendations(principal, ctx):
    """Household-aggregated explainable recommendations (deduplicated across members, household-prioritized),
    composed by the D.46 operational-intelligence layer over the authoritative recommendation sources
    (never a second recommendation engine)."""
    from app.services.recommendations import household_recommendations, recommendation_summary
    hid = ctx["household_id"]
    summary = recommendation_summary(principal, household_id=hid)
    result = household_recommendations(principal, hid)
    rows = result.get("recommendations", []) if result else []
    return {"summary": summary, "recommendations": rows, "source": "recommendations.engine",
            "not_a_second_engine": True}


def _compliance_summary(principal, ctx):
    """Household supervisory compliance oversight — aggregated across members (deduplicated), composed by the
    D.47 compliance-intelligence layer. Supervisor-only (gated by compliance.supervise); never a second
    compliance engine, never mutates."""
    from app.services.compliance_intelligence import compliance_summary as _summary
    from app.services.compliance_intelligence import household_compliance
    hid = ctx["household_id"]
    summary = _summary(principal, household_id=hid)
    result = household_compliance(principal, hid)
    return {"summary": summary,
            "reviews": result.get("reviews", []) if result else [],
            "exceptions": result.get("exceptions", []) if result else [],
            "source": "compliance_intelligence", "not_a_second_engine": True}


def _executive(principal, ctx):
    """Firm executive context for the household (KPIs + firm-intelligence observations), composed by the D.48
    executive-intelligence layer over the SINGLE Analytics Registry. Gated by analytics.executive; never a
    second analytics engine, never mutates."""
    from app.services.executive_intelligence import executive_summary
    summary = executive_summary(principal)
    return {"kpis": summary.get("kpis", {}), "observations": summary.get("observations", []),
            "governing_services": summary.get("governing_services", []),
            "source": "executive_intelligence", "not_a_second_analytics_engine": True}


def _operational_workload(principal, ctx):
    """Aggregated household operational workload (D.49) — composed read-only from the Practice Management
    layer over the Unified Work Queue (household-scoped counts + aging). Never re-sums incompatible member
    units; a count rollup only. Never a second work engine; deep-links to the authoritative work surface."""
    from app.services.practice_management import household_workload
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_workload(principal, ctx["household_id"], member_ids),
            "source": "practice_management.household_workload", "not_a_second_engine": True}


def _document_intelligence(principal, ctx):
    """Aggregated household document status (D.50) — composed read-only from the Document Platform entity
    read across the household + members (deduped by document id), rolled up to counts + status. Never
    re-stores or copies a document; counts + status only. Never a second DMS; deep-links to the
    authoritative document surface."""
    from app.services.document_intelligence import household_documents
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_documents(principal, ctx["household_id"], member_ids),
            "source": "document_intelligence.household_documents", "not_a_second_engine": True}


def _automation_history(principal, ctx):
    """Aggregated household automation activity (D.51) — composed read-only from the Workflow Orchestration
    facade across the household + members, rolled up to counts + status. Never re-executes or duplicates a
    workflow; a count rollup only. Never a second workflow engine; deep-links to the authoritative workflow
    surface."""
    from app.services.automation_orchestration import household_automation
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_automation(principal, ctx["household_id"], member_ids),
            "source": "automation_orchestration.household_automation", "not_a_second_engine": True}


def _data_governance(principal, ctx):
    """Aggregated household data-governance summary (D.52) — composed read-only from the authoritative person
    lineage (governance.mdm.person_lineage) across members, rolled up to counts. Never merges/duplicates an
    identity; a count rollup only. Never a second master-data store; deep-links to the authoritative
    governance surface."""
    from app.services.data_governance import household_governance
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_governance(principal, ctx["household_id"], member_ids),
            "source": "data_governance.household_governance", "not_a_second_engine": True}


def _external_integrations(principal, ctx):
    """Aggregated household external-integrations summary (D.53) — the external systems the household's
    members connected from, composed read-only from the authoritative person lineage across members. Counts
    + source-system names only; a rollup, never an external-system call. Never a second integration platform;
    deep-links to the authoritative integration surface."""
    from app.services.integration_hub import household_integrations
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_integrations(principal, ctx["household_id"], member_ids),
            "source": "integration_hub.household_integrations", "not_a_second_engine": True}


def _security_access(principal, ctx):
    """Aggregated household security & access summary (D.54) — who can access the household + its members'
    records, composed read-only from the authoritative authorization owner (record assignments). Counts
    only; a rollup, never a payload. Never authenticates/authorizes/alters anything; never a second
    IAM/RBAC engine; deep-links to the authoritative admin surface."""
    from app.services.security_operations import household_security
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_security(principal, ctx["household_id"], member_ids),
            "source": "security_operations.household_security", "not_a_second_engine": True}


def _business_continuity(principal, ctx):
    """Household business-continuity summary (D.55) — the firm-level operational resilience posture protecting
    the household's data, composed read-only from the authoritative Observability + Runtime owners. Counts +
    status only; never a payload. (Business continuity is firm-level; the same posture protects every
    household.) Never backs up/restores/alters anything; never a second backup/monitoring/DR engine."""
    from app.services.business_continuity import household_continuity
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_continuity(principal, ctx["household_id"], member_ids),
            "source": "business_continuity.household_continuity", "not_a_second_engine": True}


def _technology_dependencies(principal, ctx):
    """Household technology-dependencies summary (D.56) — the external vendors / systems the household's
    members depend on, composed read-only from the authoritative Integration Hub per-entity read across
    members. Counts + vendor names only; a rollup, never a payload. Never modifies a vendor/integration;
    never a second vendor platform; deep-links to the authoritative vendor surface."""
    from app.services.vendor_management import household_technology
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_technology(principal, ctx["household_id"], member_ids),
            "source": "vendor_management.household_technology", "not_a_second_engine": True}


def _financial_relationship(principal, ctx):
    """Household financial-relationship summary (D.57) — the advisory revenue basis (the household members'
    AUM) the firm's relationship rests on, composed read-only from the authoritative portfolio owner.
    Aggregate total only; a rollup, never a payload. Per-household fee / commission billing has no
    authoritative owner (`not_configured`). Never bills/invoices/posts anything; never a second accounting or
    billing engine; deep-links to the authoritative financial surface."""
    from app.services.financial_operations import household_financial
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_financial(principal, ctx["household_id"], member_ids),
            "source": "financial_operations.household_financial", "not_a_second_engine": True}


def _risk_controls(principal, ctx):
    """Household risk-&-controls summary (D.58) — authorized member- and household-level signals (compliance /
    documentation / data-quality / integration dependencies), aggregated read-only across members from ONLY
    the authoritative owners that support record scope. Deduplication of shared household findings is handled
    by composing the household-scoped owner reads. Counts + status only; never a payload; never a second
    GRC/risk engine; an absent signal never certifies compliance."""
    from app.services.enterprise_risk import household_risk_controls
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_risk_controls(principal, ctx["household_id"], member_ids),
            "source": "enterprise_risk.household_risk_controls", "not_a_second_engine": True}


def _evidence_readiness(principal, ctx):
    """Household evidence-&-supervisory-readiness summary (D.59) — authorized member- and household-level
    evidence signals (documentation completeness) aggregated read-only across members from ONLY the
    authoritative owners that support record scope; shared household documents are deduplicated by composing
    the household-scoped owner reads. Counts + status only; never a payload; never exposes firm-wide
    examination information; never a second compliance/evidence engine; operational readiness is not regulatory
    certification."""
    from app.services.regulatory_readiness import household_evidence_readiness
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_evidence_readiness(principal, ctx["household_id"], member_ids),
            "source": "regulatory_readiness.household_evidence_readiness", "not_a_second_engine": True}


def _operational_impact(principal, ctx):
    """Household operational-impact summary (D.60) — the external services / vendors the household's members
    depend on, aggregated read-only across members from ONLY the genuinely record-scoped owner (the Integration
    Hub per-entity read). Firm-wide operational information is never exposed at household scope; per-household
    incident impact has no authoritative owner (not_configured). Counts only; never a payload; never a second
    incident/monitoring engine."""
    from app.services.operational_resilience import household_operational_impact
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_operational_impact(principal, ctx["household_id"], member_ids),
            "source": "operational_resilience.household_operational_impact", "not_a_second_engine": True}


def _servicing_team(principal, ctx):
    """Household servicing-team summary (D.61) — ONLY the record-scoped staffing directly related to servicing
    this household (who is assigned across the household + members), composed read-only from the authoritative
    authorization owner. Employee workload, firm utilization, and unrelated staffing data are never exposed at
    household scope. Counts only; never an employee detail; never a second HR/scheduling engine."""
    from app.services.capacity_planning import household_staffing
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_staffing(principal, ctx["household_id"], member_ids),
            "source": "capacity_planning.household_staffing", "not_a_second_engine": True}


def _knowledge_documentation(principal, ctx):
    """Household documentation summary (D.62) — ONLY the record-scoped documentation relevant to servicing this
    household (document count + gaps across the household + members), composed read-only from the authoritative
    Document Intelligence per-entity read (deduplicated by document id). Internal SOPs, unrelated
    documentation, and firm-wide documentation metrics are never exposed at household scope. Counts + status
    only; never document contents; never a second wiki/DMS."""
    from app.services.knowledge_management import household_documentation
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_documentation(principal, ctx["household_id"], member_ids),
            "source": "knowledge_management.household_documentation", "not_a_second_engine": True}


def _change_impact(principal, ctx):
    """Household change-impact summary (D.63) — ONLY the external systems / integrations whose configuration
    changes could touch the household's members' data, composed read-only from the authoritative person
    lineage across members (via the change layer's `household_change_impact`). Firm-wide change / release /
    deployment / CI status is never exposed at household scope. Counts + source-system names only; a rollup,
    never a deployment payload; never a second change engine; never creates / merges / deploys / approves.
    Merged is not deployed."""
    from app.services.change_management import household_change_impact
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_change_impact(principal, ctx["household_id"], member_ids),
            "source": "change_management.household_change_impact", "not_a_second_engine": True}


def _platform_dependencies(principal, ctx):
    """Household platform-dependency summary (D.64). No authoritative record-scoped platform / environment /
    infrastructure owner exists, so this is reported `not_configured` honestly — internal infrastructure and
    environment metadata unrelated to the household are never exposed, and platform impact is never inferred.
    Never a second CMDB / infrastructure platform; never creates / deploys / provisions / modifies anything."""
    from app.services.environment_management import household_platform_dependencies
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_platform_dependencies(principal, ctx["household_id"], member_ids),
            "source": "environment_management.household_platform_dependencies", "not_a_second_engine": True}


def _authorization_context(principal, ctx):
    """Household record-scoped authorization-context summary (D.65) — ONLY the current principal's OWN
    authorization decision for this household record, composed read-only from the authoritative Security
    Authorization owner (`record_in_scope`). No internal identities, privileged roles, permission maps,
    authentication metadata, or security configuration are ever exposed, and authorization is never inferred.
    Never a second authorization engine; never authenticates / authorizes / assigns / grants anything."""
    from app.services.identity_governance import household_authorization_context
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_authorization_context(principal, ctx["household_id"], member_ids),
            "source": "identity_governance.household_authorization_context", "not_a_second_engine": True}


def _data_governance_metadata(principal, ctx):
    """Household record-scoped data-governance-metadata summary (D.66) — ONLY the source-system lineage /
    provenance across the household's members, composed read-only from the authoritative Governance MDM owner
    (`person_lineage`). No internal governance notes, confidential metadata, quality-rule internals, system
    architecture, or platform configuration are ever exposed, and governance state is never inferred. Never a
    second catalog / lineage engine; never mutates metadata / creates lineage / assigns a steward / repairs
    data."""
    from app.services.data_governance_intelligence import household_data_governance
    member_ids = [m["id"] for m in ctx.get("members", [])]
    return {**household_data_governance(principal, ctx["household_id"], member_ids),
            "source": "data_governance_intelligence.household_data_governance", "not_a_second_engine": True}


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
        _add_node(mkey, {"type": "person", "id": m["id"], "name": person_row_display_name(m),
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


def _hh_scope_ctx(ctx):
    return {"scope_ids": ctx.get("member_ids") or [], "person_id": None,
            "household_id": ctx["household_id"]}


def _tasks(principal, ctx):
    """Household tasks — the same authoritative client-scoped read as the person workspace, aggregated
    across members. Create happens on a member's workspace; complete works here (member-keyed)."""
    from app.services.client360.sections import tasks as _person_tasks
    return _person_tasks(principal, _hh_scope_ctx(ctx))


def _notes(principal, ctx):
    from app.services.client360.sections import notes as _person_notes
    return _person_notes(principal, _hh_scope_ctx(ctx))


def _audit(principal, ctx):
    from app.services.client360.sections import audit as _person_audit
    return _person_audit(principal, _hh_scope_ctx(ctx))


_SECTION_BUILDERS = {
    "summary": _summary, "members": _members, "financial": _financial, "tax": _tax,
    "tasks": _tasks, "notes": _notes, "audit": _audit,
    "insurance": _insurance, "benefits": _benefits, "opportunities": _opportunities,
    "documents": _documents, "meetings": _meetings, "compliance": _compliance,
    "communications": _communications, "knowledge": _knowledge, "recommendations": _recommendations,
    "compliance_summary": _compliance_summary, "executive": _executive, "work": _work,
    "operational_workload": _operational_workload, "document_intelligence": _document_intelligence,
    "automation_history": _automation_history, "data_governance": _data_governance,
    "external_integrations": _external_integrations, "security_access": _security_access,
    "business_continuity": _business_continuity, "technology_dependencies": _technology_dependencies,
    "financial_relationship": _financial_relationship, "risk_controls": _risk_controls,
    "evidence_readiness": _evidence_readiness, "operational_impact": _operational_impact,
    "servicing_team": _servicing_team, "knowledge_documentation": _knowledge_documentation,
    "change_impact": _change_impact, "platform_dependencies": _platform_dependencies,
    "authorization_context": _authorization_context,
    "data_governance_metadata": _data_governance_metadata,
    "timeline": _timeline, "relationships": _relationships,
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
        # The household's OWN Documents tab, which carries an owner-aware upload form. It used to
        # deep-link to /document-library, sending staff out of the household to re-establish the
        # owner the workspace already knew. Mirrors the person quick action in registry.py.
        ("upload_document", "Upload Household Document", "documents.view",
         f"/client/household/{hid}?tab=documents"),
        # the staff HTML task page, not the /operations/items JSON API
        ("create_task", "Create Task", "work.read", f"/operations/task-list?household_id={hid}"),
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
            return person_row_display_name(m)
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
