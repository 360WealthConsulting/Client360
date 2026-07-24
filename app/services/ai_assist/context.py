"""Read-only AI context assembly for Advisor AI Assist (Phase D.42).

Assembles ONLY authorized, deterministic, minimized inputs by reusing the scope-guarded D.38–D.41
summaries (never re-querying domains, never reading rm_* tables, never calling the un-scoped raw
``get_meeting_brief``/``get_client_snapshot``). Unauthorized/suppressed sources are omitted before
assembly; sensitive fields (note bodies, contact PII, account numbers) are never included. Each fact is
grounded with its source label + deep link.
"""
from __future__ import annotations

import json

from .contracts import CONFIRMED, DERIVED, ContextBundle, GroundedFact, unavailable

# Source labels + their deep-link builders (never expose raw ids in user-facing text).
_LINKS = {
    "daily_brief": lambda **k: "/workspace",
    "work_queue": lambda **k: "/work",
    "client360": lambda person_id=None, **k: f"/client/{person_id}" if person_id else "/client",
    "household360": lambda household_id=None, **k: f"/client/household/{household_id}" if household_id else "/client",
    "meeting_brief": lambda person_id=None, **k: f"/workspace/meetings/{person_id}" if person_id else "/workspace",
    "communications": lambda person_id=None, household_id=None, **k: (
        f"/engagement?person_id={person_id}" if person_id
        else (f"/engagement?household_id={household_id}" if household_id else "/engagement")),
    "knowledge": lambda person_id=None, household_id=None, **k: (
        f"/knowledge?person_id={person_id}" if person_id
        else (f"/knowledge?household_id={household_id}" if household_id else "/knowledge")),
    "recommendations": lambda person_id=None, household_id=None, **k: (
        f"/recommendations?person_id={person_id}" if person_id
        else (f"/recommendations?household_id={household_id}" if household_id else "/recommendations")),
}
_LABEL = {"daily_brief": "Advisor Workspace", "work_queue": "Unified Work Queue",
          "client360": "Client 360", "household360": "Household 360", "meeting_brief": "Meeting Brief",
          "communications": "Unified Engagement", "knowledge": "Knowledge Graph",
          "recommendations": "Operational Intelligence"}


def _fact(source, key, value, *, fact_class=CONFIRMED, deep_link=None, available=True):
    return GroundedFact(source_type=source, source_label=_LABEL.get(source, source), fact_key=key,
                        fact_value=value, fact_class=fact_class, deep_link=deep_link, available=available)


def _bundle(capability, facts, sources, *, suppressed=None, unavail=None, navigation=None):
    b = ContextBundle(capability=capability, facts=facts, sources_used=sorted(set(sources)),
                      suppressed_sources=suppressed or [], unavailable=unavail or [],
                      navigation=navigation or [])
    b.context_size = len(json.dumps([f.to_dict() for f in facts], default=str))
    return b


def assemble(principal, capability, *, person_id=None, household_id=None, event_id=None,
             item_type=None, item_id=None, question=None) -> ContextBundle:
    if capability == "daily_brief":
        return _daily(principal)
    if capability == "client_brief":
        return _client(principal, person_id)
    if capability == "household_brief":
        return _household(principal, household_id)
    if capability == "meeting_prep":
        return _meeting(principal, person_id, event_id)
    if capability == "work_explanation":
        return _work(principal, item_type, item_id)
    if capability == "factual_question_answering":
        return _factual(principal, question, person_id=person_id, household_id=household_id)
    return _bundle(capability, [], [])


def _daily(principal):
    from app.services.work_queue.summary import work_queue_summary
    from app.services.workspace.summaries import daily_brief
    facts, links = [], "/workspace"
    d = daily_brief(principal)
    today = d.get("today") or {}
    for k, v in today.items():
        facts.append(_fact("daily_brief", f"today.{k}", v, deep_link=links))
    pr = d.get("priorities") or {}
    for k in ("high", "medium", "low", "total"):
        facts.append(_fact("daily_brief", f"priorities.{k}", pr.get(k, 0), deep_link=links))
    facts.append(_fact("daily_brief", "meetings.today", d.get("meetings_today", 0), deep_link=links))
    wq = work_queue_summary(principal)
    for k in ("my_overdue", "due_today", "high_priority", "sla_breaches", "unassigned_team"):
        facts.append(_fact("work_queue", f"work.{k}", wq.get(k, 0), deep_link="/work"))
    for it in (wq.get("top_urgent") or [])[:5]:
        facts.append(_fact("work_queue", "work.urgent_item",
                           _safe_title(it.get("title")), deep_link=it.get("deep_link")))
    return _bundle("daily_brief", facts, ["Advisor Workspace", "Unified Work Queue"],
                   navigation=[{"label": "Advisor Workspace", "href": "/workspace"},
                               {"label": "Unified Work Queue", "href": "/work"}])


def _client(principal, person_id):
    from app.services.client360 import get_workspace
    ws = get_workspace(principal, person_id=person_id, section_timings=False)
    if ws is None:
        return _bundle("client_brief", [], [], unavail=["client (out of scope or not found)"])
    link = f"/client/{person_id}"
    s = ws.get("snapshot") or {}
    facts = [_fact("client360", "identity.name", ws.get("display_name"), deep_link=link)]
    a = s.get("assets") or {}
    for k in ("aum", "cash", "household_aum"):
        facts.append(_fact("client360", f"financial.{k}", a.get(k, 0), deep_link=link))
    for key, val in (("work.open_work", s.get("open_work")), ("work.open_exceptions", s.get("open_exceptions")),
                     ("tax.active", (s.get("tax") or {}).get("active")),
                     ("insurance.policy_count", (s.get("insurance") or {}).get("policy_count"))):
        facts.append(_fact("client360", key, val if val is not None else "Unavailable",
                           available=val is not None, deep_link=link))
    if s.get("revenue"):
        facts.append(_fact("client360", "opportunities.expected_revenue",
                           s["revenue"].get("expected_revenue"), fact_class=DERIVED, deep_link=link))
    else:
        facts.append(unavailable("client360", "Client 360", "opportunities.revenue", reason="Unavailable",
                                 deep_link=link))
    if s.get("compliance"):
        facts.append(_fact("client360", "compliance.open_reviews",
                           s["compliance"].get("open_reviews"), deep_link=link))
    facts.append(_fact("client360", "meetings.next_activity",
                       (s.get("next_activity") or {}).get("title") if s.get("next_activity") else "None",
                       deep_link=link))
    # Unified engagement summary — sourced from the composed Client 360 section (no raw domain fan-out);
    # counts only, never message bodies/subjects.
    comms = (ws.get("sections") or {}).get("communications") or {}
    csum = comms.get("summary") or {}
    if csum.get("enabled"):
        clink = f"/engagement?person_id={person_id}"
        facts.append(_fact("communications", "communications.recent_interactions", csum.get("total", 0),
                           deep_link=clink))
        facts.append(_fact("communications", "communications.unread", csum.get("unread", 0), deep_link=clink))
        facts.append(_fact("communications", "communications.action_required",
                           csum.get("action_required", 0), deep_link=clink))
    # Knowledge graph — connected-entity counts from the composed Client 360 section (no raw graph query);
    # every explanation cites its authoritative service, so AI never explores the graph unrestricted.
    know = (ws.get("sections") or {}).get("knowledge") or {}
    ksum = know.get("summary") or {}
    if ksum.get("enabled"):
        klink = f"/knowledge?person_id={person_id}"
        facts.append(_fact("knowledge", "knowledge.connected_entities", ksum.get("connected", 0),
                           fact_class=DERIVED, deep_link=klink))
    # Operational Intelligence — recommendation counts from the composed Client 360 section (AI SUMMARIZES
    # existing recommendation contracts; it never invents recommendations).
    reco = (ws.get("sections") or {}).get("recommendations") or {}
    rsum = reco.get("summary") or {}
    if rsum.get("enabled"):
        rlink = f"/recommendations?person_id={person_id}"
        facts.append(_fact("recommendations", "recommendations.count", rsum.get("total", 0),
                           fact_class=DERIVED, deep_link=rlink))
        if rsum.get("top"):
            facts.append(_fact("recommendations", "recommendations.top",
                               _safe_title(rsum["top"].get("title")), deep_link=rlink))
    return _bundle("client_brief", facts, ["Client 360"],
                   navigation=[{"label": "Open Client 360", "href": link},
                               {"label": "Open Engagement", "href": f"/engagement?person_id={person_id}"},
                               {"label": "Open Knowledge", "href": f"/knowledge?person_id={person_id}"},
                               {"label": "Open Recommendations", "href": f"/recommendations?person_id={person_id}"}])


def _household(principal, household_id):
    from app.services.client360.household import get_household_workspace
    ws = get_household_workspace(principal, household_id)
    if ws is None:
        return _bundle("household_brief", [], [], unavail=["household (out of scope or not found)"])
    link = f"/client/household/{household_id}"
    s = ws.get("snapshot") or {}
    pm = s.get("primary_member") or {}
    facts = [
        _fact("household360", "identity.household", s.get("household_name"), deep_link=link),
        _fact("household360", "members.count", s.get("member_count"), deep_link=link),
        _fact("household360", "members.active", s.get("active_members"), deep_link=link),
        _fact("household360", "members.primary", pm.get("name"), deep_link=link),
        _fact("household360", "financial.portfolio_assets", s.get("portfolio_assets"), deep_link=link),
        _fact("household360", "work.open_work", s.get("open_work"), deep_link=link),
        _fact("household360", "opportunities.open", s.get("open_opportunities"), deep_link=link),
        _fact("household360", "meetings.upcoming", s.get("upcoming_meetings"), deep_link=link),
        _fact("household360", "compliance.items", s.get("compliance_items"), deep_link=link),
        _fact("household360", "relationships.businesses", s.get("connected_businesses"), deep_link=link),
    ]
    comms = (ws.get("sections") or {}).get("communications") or {}
    csum = comms.get("summary") or {}
    if csum.get("enabled"):
        clink = f"/engagement?household_id={household_id}"
        facts.append(_fact("communications", "communications.recent_interactions", csum.get("total", 0),
                           deep_link=clink))
        facts.append(_fact("communications", "communications.unread", csum.get("unread", 0), deep_link=clink))
    know = (ws.get("sections") or {}).get("knowledge") or {}
    ksum = know.get("summary") or {}
    if ksum.get("enabled"):
        facts.append(_fact("knowledge", "knowledge.connected_entities", ksum.get("connected", 0),
                           fact_class=DERIVED, deep_link=f"/knowledge?household_id={household_id}"))
    reco = (ws.get("sections") or {}).get("recommendations") or {}
    rsum = reco.get("summary") or {}
    if rsum.get("enabled"):
        facts.append(_fact("recommendations", "recommendations.count", rsum.get("total", 0),
                           fact_class=DERIVED, deep_link=f"/recommendations?household_id={household_id}"))
    return _bundle("household_brief", facts, ["Household 360"],
                   navigation=[{"label": "Open Household 360", "href": link}])


def _meeting(principal, person_id, event_id):
    """Meeting prep — MINIMIZED: note bodies, contact email/phone, and account numbers are excluded."""
    from app.services.workspace.summaries import meeting_prep
    brief = meeting_prep(principal, person_id, event_id=event_id)
    if brief is None:
        return _bundle("meeting_prep", [], [], unavail=["meeting (out of scope or not found)"])
    link = f"/workspace/meetings/{person_id}"
    person = brief.get("person") or {}
    snap = brief.get("snapshot") or {}
    ev = brief.get("meeting_event") or {}
    facts = [
        _fact("meeting_brief", "meeting.client", person.get("full_name"), deep_link=link),   # name only
        _fact("meeting_brief", "meeting.event",
              _safe_title(ev.get("title")) if ev else "No linked meeting event", deep_link=link),
        _fact("client360", "context.aum", snap.get("aum", 0), deep_link=f"/client/{person_id}"),
        _fact("client360", "context.open_tasks", len(brief.get("open_tasks") or []), fact_class=DERIVED,
              deep_link=f"/client/{person_id}"),
        _fact("client360", "open_items.open_exceptions", len(brief.get("open_exceptions") or []),
              fact_class=DERIVED, deep_link=f"/client/{person_id}"),
        _fact("client360", "deadlines.reviews_due", len(brief.get("reviews") or []), fact_class=DERIVED,
              deep_link=f"/client/{person_id}"),
        # note COUNT only — never the note bodies (protected content).
        _fact("meeting_brief", "context.note_count", len(brief.get("notes") or []), fact_class=DERIVED,
              deep_link=link),
    ]
    facts.append(GroundedFact("meeting_brief", "Meeting Brief", "questions.prompt",
                              "Consider open items, deadlines, and any missing information before the "
                              "meeting.", fact_class="model_generated_summary", deep_link=link))
    return _bundle("meeting_prep", facts, ["Meeting Brief", "Client 360"],
                   navigation=[{"label": "Open Meeting Workspace", "href": link}])


def _work(principal, item_type, item_id):
    from app.services.work_queue import compose_queue
    q = compose_queue(principal, page=1, page_size=200)
    match = next((r for r in q.get("rows", [])
                  if str(r.get("source_domain")) == str(item_type) and str(r.get("source_id")) == str(item_id)),
                 None)
    if match is None:
        return _bundle("work_explanation", [], [], unavail=["work item (out of scope or not visible)"])
    link = match.get("deep_link")
    facts = [
        _fact("work_queue", "item.title", _safe_title(match.get("title")), deep_link=link),
        _fact("work_queue", "item.domain", match.get("source_domain"), deep_link=link),
        _fact("work_queue", "item.status", match.get("status"), deep_link=link),
        _fact("work_queue", "item.sla_state", match.get("sla_state"), deep_link=link),
        _fact("work_queue", "item.due_at", match.get("due_at") or "Unavailable",
              available=bool(match.get("due_at")), deep_link=link),
        _fact("work_queue", "why.surfaced",
              _why(match), fact_class=DERIVED, deep_link=link),
        _fact("work_queue", "next_step.deep_link", link, deep_link=link),
    ]
    return _bundle("work_explanation", facts, ["Unified Work Queue"],
                   navigation=[{"label": "Open source record", "href": link},
                               {"label": "Unified Work Queue", "href": "/work"}])


def _factual(principal, question, *, person_id=None, household_id=None):
    base = _daily(principal)
    facts = list(base.facts)
    sources = list(base.sources_used)
    if person_id:
        c = _client(principal, person_id)
        facts += c.facts
        sources += c.sources_used
    if household_id:
        h = _household(principal, household_id)
        facts += h.facts
        sources += h.sources_used
    b = _bundle("factual_question_answering", facts, sources, navigation=base.navigation)
    b.unavailable = [] if facts else ["no authorized context available"]
    return b


def _why(item):
    if item.get("overdue"):
        return "Overdue — past its due date."
    if item.get("sla_state") == "breached":
        return "SLA breached."
    if item.get("priority") in ("urgent", "high"):
        return f"{item.get('priority')} priority."
    return f"Open {item.get('source_domain')} item requiring action."


def _safe_title(title):
    """Titles may embed light identifiers; keep short and never expose long numeric strings verbatim."""
    if not title:
        return None
    t = str(title)
    return (t[:80] + "…") if len(t) > 80 else t
