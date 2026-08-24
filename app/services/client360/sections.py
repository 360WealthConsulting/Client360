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


def _dash_tasks(ctx, *, limit=8):
    from sqlalchemy import select

    from app.db import engine, tasks
    ids = [i for i in (ctx.get("scope_ids") or ([_pid(ctx)] if _pid(ctx) else [])) if i]
    if not ids:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(tasks.c.id, tasks.c.title, tasks.c.status, tasks.c.due_date, tasks.c.priority)
            .where(tasks.c.person_id.in_(ids), tasks.c.status != "complete")
            .order_by(tasks.c.due_date.asc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def dashboard(principal, ctx):
    """The Client Workspace landing tab (the "Dashboard"). A compact cross-domain snapshot composed from
    the SAME authoritative section builders — never a second data source, never new ownership/domain
    logic. Each card is gated by its capability so a user sees only what they may open, and any single
    card failing degrades to empty rather than breaking the landing tab."""
    def _safe(fn, default):
        try:
            return fn()
        except Exception:      # noqa: BLE001 — one card must never break the whole Dashboard
            return default

    pid0, hid0 = _pid(ctx), _hid(ctx)
    card = {
        "open_tasks": _safe(lambda: _dash_tasks(ctx), []),
        "recent_activity": [], "recent_documents": [], "documents_needing_review": [],
        "missing_tax_items": [], "tax_engagements": None, "upcoming_meetings": [],
        "planning_opportunities": [], "alerts": [],
        # Knowledge layer (Phase 6A) — surfaced through the existing Dashboard, no new screen.
        "newly_classified": [], "missing_document_alerts": [], "compliance_issues": [],
    }
    if principal.can("timeline.read"):
        card["recent_activity"] = _safe(lambda: timeline(principal, ctx).get("rows", [])[:8], [])
    if principal.can("documents.view"):
        docs = _safe(lambda: documents(principal, ctx).get("documents", []), [])
        card["recent_documents"] = docs[:8]
        card["documents_needing_review"] = [
            d for d in docs if str(d.get("review_status") or "").lower()
            in ("pending", "in_review", "needs_review", "review")][:8]
        from app.services import knowledge_pipeline as _kp
        _scope = [pid0] if pid0 else []
        _hids = [hid0] if hid0 else []
        card["newly_classified"] = _safe(
            lambda: _kp.recently_classified(_scope, household_ids=_hids), [])
        card["compliance_issues"] = _safe(
            lambda: _kp.compliance_documents(_scope, household_ids=_hids), [])
        card["missing_document_alerts"] = _safe(
            lambda: _kp.unidentified_documents(_scope, household_ids=_hids), [])
    if principal.can("tax.read"):
        t = _safe(lambda: tax(principal, ctx), {})
        exc = t.get("open_exceptions", []) or []
        missing = [e for e in exc if "missing" in str(e.get("category", "")).lower()
                   or "document" in str(e.get("category", "")).lower()]
        card["missing_tax_items"] = (missing or exc)[:8]
        card["tax_engagements"] = t.get("engagements")
    card["upcoming_meetings"] = _safe(lambda: meetings(principal, ctx).get("upcoming", [])[:5], [])
    card["planning_opportunities"] = _safe(
        lambda: recommendations(principal, ctx).get("recommendations", [])[:8], [])

    pid, hid = _pid(ctx), _hid(ctx)
    if pid:
        allow = {"insurance": "insurance.read", "benefits": "benefits.read",
                 "compliance": "compliance.review.read"}

        def _alerts():
            from app.services.exception_engine import open_exceptions_for_client
            out = []
            for e in open_exceptions_for_client(pid, hid):
                dom = e.get("domain")
                cap = allow.get(dom)
                if dom in allow and principal.can(cap):
                    out.append({"domain": dom, "title": e.get("title") or e.get("message"),
                                "severity": e.get("severity")})
            return out[:12]
        card["alerts"] = _safe(_alerts, [])
    return card


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
    """The Tax tab — the client's tax operating center, composed from the authoritative tax domain
    (engagements/returns/missing items/filing events/lifecycle/deadlines) plus exceptions/tasks/timeline.
    See app.services.client360.tax_workspace. Keeps ``open_exceptions``/``engagements`` keys so the
    Dashboard's missing-tax-items card keeps working."""
    from app.services.client360.tax_workspace import build_tax_workspace
    pid, hid = _pid(ctx), _hid(ctx)
    scope = list(ctx.get("scope_ids") or ([pid] if pid else []))
    tws = build_tax_workspace(principal, person_id=pid, household_id=hid, scope_ids=scope) if pid else {}
    if tws:
        tws["open_exceptions"] = tws.get("missing_and_exceptions", {}).get("exceptions", [])
        tws["engagements"] = {"active": tws.get("status_summary", {}).get("return_count", 0)}
    return tws or {"open_exceptions": [], "engagements": {"active": 0}}


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


_SUPPORTED_SOURCES = ("TaxDome", "Drake", "SharePoint", "Schwab", "AssetMark", "Upload",
                      "Scanner", "Email")


def _source_badge(row):
    """One canonical document, many sources (ADR-072). Today a row carries a single source in tags;
    derive the badge honestly (multi-source references arrive with the document_sources table)."""
    tags = row.get("tags") or {}
    ss = str(tags.get("source_system") or "").lower()
    prov = str(row.get("storage_provider") or "").lower()
    for key, badge in (("taxdome", "TaxDome"), ("drake", "Drake"), ("sharepoint", "SharePoint"),
                       ("microsoft", "SharePoint"), ("schwab", "Schwab"), ("assetmark", "AssetMark"),
                       ("scan", "Scanner"), ("email", "Email")):
        if key in ss:
            return badge
    if prov in ("client360 local", "local", ""):
        return "Upload"
    return row.get("storage_provider") or "Upload"


def _owner_label(row):
    if row.get("household_id"):
        return "Household"
    if row.get("organization_id"):
        return "Business/Trust/Estate"
    if row.get("person_id"):
        return "Client"
    return "Unassigned"


def enrich_documents(rows):
    """Map canonical document rows into the Documents-tab view model (shared by the person and
    household compositions). Duplicate detection is by SHA-256 over the given set. OCR/AI and true
    multi-source references are surfaced honestly as pending — never fabricated."""
    from collections import Counter

    from app.services.document_naming import document_display_name
    sha_counts = Counter(r.get("sha256") for r in rows if r.get("sha256"))
    docs = []
    for r in rows:
        tags = r.get("tags") or {}
        sha = r.get("sha256")
        docs.append({
            # Canonical display_name when set, else the original filename (document_naming).
            "id": r["id"], "name": document_display_name(r) or f"Document {r['id']}",
            "original_name": r.get("original_name"),
            "document_type": tags.get("document_type") or r.get("subcategory"),
            "category": r.get("category") or tags.get("category"),
            "tax_year": tags.get("tax_year") or tags.get("year"),
            "owner_label": _owner_label(r), "provenance": r.get("provenance"),
            "person_id": r.get("person_id"), "household_id": r.get("household_id"),
            "organization_id": r.get("organization_id"),
            "source": _source_badge(r),
            "review_status": r.get("review_status"),
            "ocr_status": r.get("ocr_status"),          # real column; pending until the OCR phase
            "ai_status": None,                          # not implemented — shown as pending, never faked
            "is_duplicate": bool(sha and sha_counts.get(sha, 0) > 1),
            "duplicate_count": sha_counts.get(sha, 0) if sha else 0,
            "version_count": r.get("current_version") or 1,
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
            "taxdome_folder": tags.get("taxdome_folder"),
            "source_path": tags.get("source_path"),
            "sha256": sha,
            "source_kind": "canonical",
            "download_url": f"/documents/{r['id']}/download",
        })
    return docs


# --- Vault documents merged into the unified Documents tab (ADR-072 consolidation) --------------------

def _vault_row(d):
    """Map one vault_documents row (from the Vault service) into the unified Documents-tab shape, with a
    Vault source badge and the Vault download route. Vault permissions/audit stay with the Vault service."""
    created = d.get("created_at")
    return {
        "id": d["id"], "name": d.get("display_name") or f"Vault document {d['id']}",
        "document_type": d.get("document_type"), "category": d.get("category"),
        "tax_year": created.year if created else None,
        "owner_label": "Client Vault", "provenance": "vault",
        "person_id": None, "household_id": None, "organization_id": None,
        "source": "Vault", "source_kind": "vault",
        "review_status": d.get("status"),
        "ocr_status": None, "ocr_label": None, "ocr_engine": None, "ocr_completed_at": None,
        "searchable_text": None, "ai_status": None,
        "classification_confidence": None, "extraction_label": None,
        "is_duplicate": False, "duplicate_count": 0,
        "version_count": d.get("current_version") or 1,
        "created_at": created, "updated_at": d.get("updated_at") or created,
        "taxdome_folder": None, "source_path": None, "sources": [], "source_systems": [],
        "sha256": d.get("checksum_sha256"),
        "download_url": f"/api/vault/documents/{d['id']}/download",
        "vault_document_id": d["id"],
    }


def _vault_rows(principal, *, person_ids=(), household_id=None):
    """Vault documents linked to the given person(s) and/or household, authorized + record-scoped by the
    existing Vault service. Deduped by vault document id across the anchors."""
    from app.services.vault import service as vault_service
    seen, rows = set(), []
    targets = ([{"household_id": household_id}] if household_id else []) + \
              [{"person_id": pid} for pid in person_ids if pid]
    for kw in targets:
        try:
            listed = vault_service.list_documents(principal, limit=200, **kw)
        except Exception:      # noqa: BLE001 — Vault must never break the Documents tab
            listed = []
        for d in listed:
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            rows.append(_vault_row(d))
    return rows


def _merge_documents(canonical, vault):
    """Canonical documents + Vault documents in one list. Deduped where deterministically possible: a
    vault document whose checksum matches a canonical document's SHA-256 is the same underlying file and
    is dropped in favor of the canonical record (its richer pipeline)."""
    canon_sha = {r.get("sha256") for r in canonical if r.get("sha256")}
    merged = list(canonical)
    for v in vault:
        if v.get("sha256") and v["sha256"] in canon_sha:
            continue
        merged.append(v)
    return merged


def documents_view_model(docs):
    """The full Documents-tab section payload (list + supported sources + unassigned worklist + honest
    capability flags), shared by both compositions."""
    # The client Documents tab shows ONLY documents owned by this client (person/household/organization).
    # The unassigned-folder migration/cleanup queue lives in Admin -> Document Management, never here.
    return {
        "documents": docs,
        "supported_sources": list(_SUPPORTED_SOURCES),
        "ocr_enabled": True, "ai_extraction_enabled": False, "multi_source_enabled": False,
    }


# Plain-language OCR states for staff (no technical jargon) — see _attach_ocr.
_OCR_LABELS = {
    "completed": "Text captured",
    "failed": "Text not captured",
    "unsupported": "No text layer",
    "processing": "Working…",
    "pending": "Not processed",
}


def _attach_ocr(docs):
    """Attach each canonical document's OCR record (status, completed date, searchable-text flag) for
    the Documents tab, plus a plain-language ``ocr_label`` for staff. Read-only (ADR-072)."""
    try:
        from app.services.document_ocr import ocr_for_documents
        recs = ocr_for_documents([d["id"] for d in docs])
    except Exception:      # noqa: BLE001 — OCR state must never break the Documents tab
        recs = {}
    for d in docs:
        rec = recs.get(d["id"])
        if rec:
            d["ocr_status"] = rec["status"]
            d["ocr_completed_at"] = rec.get("ocr_completed_at")
            d["ocr_page_count"] = rec.get("page_count")
            d["ocr_engine"] = rec.get("engine")
            d["searchable_text"] = rec["status"] == "completed" and (rec.get("char_count") or 0) > 0
        else:
            d.setdefault("ocr_completed_at", None)
            d["ocr_engine"] = None
            d["searchable_text"] = False
        d["ocr_label"] = _OCR_LABELS.get(d.get("ocr_status"), "Not processed")
    return docs


def _attach_classification(docs):
    """Attach each canonical document's Knowledge-layer classification + extraction status (Phase 6A):
    classified doc type, confidence, and how many structured facts were extracted. Read-only."""
    try:
        from app.services.knowledge_pipeline import (
            classification_for_documents,
            facts_for_documents,
        )
        ids = [d["id"] for d in docs]
        cls = classification_for_documents(ids)
        facts = facts_for_documents(ids)
    except Exception:      # noqa: BLE001 — the Knowledge layer must never break the Documents tab
        cls, facts = {}, {}
    for d in docs:
        c = cls.get(d["id"])
        n_facts = len(facts.get(d["id"], []))
        if c:
            d["classified_type"] = c["doc_type"]
            d["classification_confidence"] = float(c["confidence"]) if c["confidence"] is not None else None
            # A classified type is the authoritative label to show; fall back to the tag-derived type.
            d["document_type"] = d.get("document_type") or (
                c["doc_type"] if c["doc_type"] != "unknown" else None)
        else:
            d["classified_type"] = None
            d["classification_confidence"] = None
        d["fact_count"] = n_facts
        d["extraction_status"] = ("extracted" if n_facts else ("classified" if c else "pending"))
        # Plain-language extraction summary for staff.
        d["extraction_label"] = (f"{n_facts} detail{'s' if n_facts != 1 else ''} found" if n_facts
                                 else ("Classified" if c else None))
    return docs


def _attach_source_refs(docs):
    """Attach each canonical document's source references (ADR-072 multi-source) for the Documents tab."""
    try:
        from app.services.document_sources import sources_for_documents
        refs = sources_for_documents([d["id"] for d in docs])
    except Exception:      # noqa: BLE001 — source refs must never break the Documents tab
        refs = {}
    for d in docs:
        d["sources"] = refs.get(d["id"], [])
        if d["sources"]:
            d["source_systems"] = sorted({s["source_system"] for s in d["sources"]})
        else:
            d["source_systems"] = [d["source"]] if d.get("source") else []
    return docs


def documents(principal, ctx):
    """The client's unified canonical document list — the Documents tab's operating center. One row per
    canonical document (ADR-072), scoped to this client's ownership (ADR-073). Each row carries its
    source references (TaxDome / Drake / …) — one canonical document, many sources."""
    from app.services.document_platform.relationships import documents_for_entity
    et, eid = ctx["entity_type"], ctx["entity_id"]
    rows = documents_for_entity(principal, et, eid, limit=200)
    canonical = _attach_classification(_attach_ocr(_attach_source_refs(enrich_documents(rows))))
    # Merge the client's linked Vault documents into the ONE Documents tab (Vault permissions/audit stay
    # in the Vault service; canonical rows keep their pipeline). Person tab = vault links to this person.
    vault = _vault_rows(principal, person_ids=[eid] if et == "person" else (),
                        household_id=eid if et == "household" else None)
    return documents_view_model(_merge_documents(canonical, vault))


def _scope(ctx):
    return [i for i in (ctx.get("scope_ids") or ([_pid(ctx)] if _pid(ctx) else [])) if i]


def tasks(principal, ctx):
    """Client-scoped tasks (the authoritative ``tasks`` service — never a second task store). Create /
    complete happen in-workspace via /client/{id}/tasks*; detailed edits deep-link to the task surface."""
    from app.services.tasks import tasks_with_assignee
    rows = []
    for p in _scope(ctx):
        rows.extend(dict(r) for r in tasks_with_assignee(p))
    closed = {"complete", "completed", "closed", "cancelled", "resolved"}
    rows.sort(key=lambda t: (t.get("status") in closed,
                             t.get("due_date") is None, str(t.get("due_date") or "")))
    return {"tasks": rows, "can_write": principal.can("client.write"),
            "primary_person_id": _pid(ctx)}


def notes(principal, ctx):
    """Client notes (the authoritative append-only ``person_notes`` service). Internal-only — these are
    staff notes and are never exposed to the client portal here."""
    from app.services.notes import ACTIVITY_NOTE_TYPES, list_person_notes
    rows = []
    for p in _scope(ctx):
        rows.extend(list_person_notes(p))
    rows.sort(key=lambda n: (n.get("created_at") is not None, n.get("created_at")), reverse=True)
    return {"notes": rows, "note_types": sorted(ACTIVITY_NOTE_TYPES),
            "can_write": principal.can("client.write"), "primary_person_id": _pid(ctx),
            "internal_only": True}


def audit(principal, ctx):
    """Read-only audit history scoped to this client's ownership (person(s) + household + their tasks).
    Reads the authoritative append-only ``audit_events`` — never a second audit store. Registry-gated by
    ``audit.read``; scope already verified at the workspace boundary. Never surfaces secrets."""
    from sqlalchemy import and_, or_, select

    from app.db import audit_events, engine, households, people, tasks, users
    scope = [str(i) for i in _scope(ctx)]
    hid = _hid(ctx)
    conds = []
    if scope:
        conds.append(and_(audit_events.c.entity_type == "person", audit_events.c.entity_id.in_(scope)))
        conds.append(and_(audit_events.c.entity_type == "task",
                          audit_events.c.metadata["person_id"].astext.in_(scope)))
    if hid:
        conds.append(and_(audit_events.c.entity_type == "household",
                          audit_events.c.entity_id == str(hid)))
    if not conds:
        return {"events": []}
    with engine.connect() as conn:
        rows = conn.execute(
            select(audit_events.c.action, audit_events.c.entity_type, audit_events.c.entity_id,
                   audit_events.c.actor_user_id, audit_events.c.occurred_at, audit_events.c.outcome)
            .where(or_(*conds)).order_by(audit_events.c.occurred_at.desc()).limit(100)).mappings().all()
        events = [dict(r) for r in rows]
        # Resolve human labels so the tab never shows raw internal ids ("#1" / "person #1"). Bulk-load
        # the actor display names and the person/household/task names referenced by these events.
        actor_ids = {e["actor_user_id"] for e in events if e["actor_user_id"]}
        by_type: dict[str, set] = {}
        for e in events:
            if str(e["entity_id"]).isdigit():
                by_type.setdefault(e["entity_type"], set()).add(int(e["entity_id"]))

        def _names(table, name_col, ids):
            if not ids:
                return {}
            return {str(i): n for i, n in conn.execute(
                select(table.c.id, name_col).where(table.c.id.in_(ids))).all()}

        actor_names = ({str(i): n for i, n in conn.execute(
            select(users.c.id, users.c.display_name).where(users.c.id.in_(actor_ids))).all()}
            if actor_ids else {})
        labels = {
            "person": _names(people, people.c.full_name, by_type.get("person")),
            "household": _names(households, households.c.name, by_type.get("household")),
            "task": _names(tasks, tasks.c.title, by_type.get("task")),
        }
    for e in events:
        actor = e["actor_user_id"]
        e["actor_name"] = (actor_names.get(str(actor)) or "Unknown user") if actor else "System"
        e["entity_label"] = (labels.get(e["entity_type"], {}).get(str(e["entity_id"]))
                             or e["entity_type"].replace("_", " ").title())
    return {"events": events}


def vault(principal, ctx):
    """Client Vault tab — the client's linked, authorized vault documents (list + filters), plus the
    selected document's detail panel (versions / links / audit) when one is requested. Reuses the
    vault service for authorization + audit; upload/download/version/archive act via /api/vault."""
    from app.services.vault import service as vault_service
    pid, hid = _pid(ctx), _hid(ctx)
    view = ctx.get("vault_view") or {}
    # Scope to the entity being viewed: a person view filters by person only (person-linked docs have
    # a NULL household on the link, so passing household too would AND them away); a household view
    # filters by household.
    documents_list = vault_service.list_documents(
        principal, person_id=pid, household_id=(None if pid else hid),
        category=view.get("category") or None, document_type=view.get("document_type") or None,
        status=view.get("status") or None, year=view.get("year") or None, query=view.get("q") or None)
    detail = None
    selected = view.get("doc")
    if selected:
        try:
            # Detail render is a 'view' of the document → audits the access.
            detail = vault_service.get_document(
                principal, int(selected), actor_user_id=principal.user_id, audit_action="view")
        except (vault_service.VaultNotFound, vault_service.VaultPermissionError, ValueError):
            detail = None
    return {
        "documents": documents_list,
        "detail": detail,
        "filters": {"q": view.get("q") or "", "category": view.get("category") or "",
                    "document_type": view.get("document_type") or "", "status": view.get("status") or "",
                    "year": view.get("year") or ""},
        "categories": list(vault_service.CATEGORIES),
        "statuses": list(vault_service.STATUSES),
        "person_id": pid, "household_id": hid,
        "can_upload": principal.can("vault.upload"),
        "can_manage": principal.can("vault.manage"),
    }


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
    from app.services.organization_service import (
        list_household_business_ownership,
        list_person_business_ownership,
    )
    from app.services.relationships import build_relationship_graph, get_person_households
    pid = _pid(ctx)
    hid = _hid(ctx)
    graph = build_relationship_graph(pid) if pid else {"categories": {}, "relationships": []}
    households = get_person_households(pid) if pid else ctx.get("member_households") or []
    # Associated businesses via the ownership graph — a person shows businesses it owns; a household
    # shows businesses owned by the household entity or by any of its members (pure reads).
    businesses, seen = [], set()

    def _add(rows):
        for b in rows:
            if b["business_id"] not in seen:
                seen.add(b["business_id"])
                businesses.append(b)
    if pid:
        _add(list_person_business_ownership(pid))
    if hid:
        _add(list_household_business_ownership(hid))
        for mid in (ctx.get("member_ids") or []):
            _add(list_person_business_ownership(mid))
    return {"graph": graph, "households": households, "businesses": businesses,
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


def document_intelligence(principal, ctx):
    """A compact document-intelligence summary for this client (D.50) — composed read-only from the Document
    Platform entity read (documents_for_entity) + Compliance Intelligence documentation gaps. Counts +
    status only, never document content; deep-links to the authoritative document surface. Never a second
    DMS/OCR/index/archive."""
    from app.services.document_intelligence import client_documents
    pid = _pid(ctx)
    return {**(client_documents(principal, pid) if pid else {"enabled": False, "document_count": 0}),
            "source": "document_intelligence.client_documents", "not_a_second_engine": True}


def automation_history(principal, ctx):
    """A compact automation-history summary for this client (D.51) — composed read-only from the Workflow
    Orchestration facade (record-scoped workflow instances rolled up to counts + status). Never executes or
    launches anything; deep-links to the authoritative workflow surface. Never a second workflow engine."""
    from app.services.automation_orchestration import client_automation
    pid = _pid(ctx)
    return {**(client_automation(principal, pid) if pid else {"enabled": False, "workflow_count": 0}),
            "source": "automation_orchestration.client_automation", "not_a_second_engine": True}


def data_governance(principal, ctx):
    """A compact data-governance summary for this client (D.52) — composed read-only from the authoritative
    person lineage (governance.mdm.person_lineage, which reads person_source_links — never duplicated).
    Counts + source systems only, never a payload; deep-links to the authoritative governance surface. Never
    merges/alters an identity; never a second master-data/identity store."""
    from app.services.data_governance import client_governance
    pid = _pid(ctx)
    return {**(client_governance(principal, pid) if pid else {"enabled": False, "lineage_records": 0}),
            "source": "data_governance.client_governance", "not_a_second_engine": True}


def external_integrations(principal, ctx):
    """A compact external-integrations summary for this client (D.53) — the external systems the client's
    data connected from, composed read-only from the authoritative person lineage. Counts + source-system
    names only, never a payload; deep-links to the authoritative integration surface. Never connects/syncs/
    invokes anything; never a second integration platform."""
    from app.services.integration_hub import client_integrations
    pid = _pid(ctx)
    return {**(client_integrations(principal, pid) if pid else {"enabled": False, "source_systems": []}),
            "source": "integration_hub.client_integrations", "not_a_second_engine": True}


def security_access(principal, ctx):
    """A compact security & access summary for this client (D.54) — who can access this client's record,
    composed read-only from the authoritative authorization owner (record assignments). Counts only, never a
    payload; deep-links to the authoritative admin surface. Never authenticates/authorizes/alters anything;
    never a second IAM/RBAC engine."""
    from app.services.security_operations import client_security
    pid = _pid(ctx)
    return {**(client_security(principal, pid) if pid else {"enabled": False, "assigned_users": 0}),
            "source": "security_operations.client_security", "not_a_second_engine": True}


def business_continuity(principal, ctx):
    """A compact business-continuity summary in the context of this client (D.55) — the firm-level operational
    resilience posture (resilience score + infrastructure availability + backup coverage) protecting the
    client's data, composed read-only from the authoritative Observability + Runtime owners. Counts + status
    only, never a payload; deep-links to the authoritative continuity surface. Never backs up/restores/alters
    anything; never a second backup/monitoring/DR engine."""
    from app.services.business_continuity import client_continuity
    pid = _pid(ctx)
    return {**client_continuity(principal, pid),
            "source": "business_continuity.client_continuity", "not_a_second_engine": True}


def technology_dependencies(principal, ctx):
    """A compact technology-dependencies summary for this client (D.56) — the external vendors / systems the
    client's data depends on, composed read-only from the authoritative Integration Hub per-entity read
    (source systems from person lineage). Counts + vendor names only, never a payload; deep-links to the
    authoritative vendor surface. Never modifies a vendor/integration; never a second vendor platform."""
    from app.services.vendor_management import client_technology
    pid = _pid(ctx)
    return {**(client_technology(principal, pid) if pid else {"enabled": False, "vendor_dependencies": 0}),
            "source": "vendor_management.client_technology", "not_a_second_engine": True}


def financial_relationship(principal, ctx):
    """A compact financial-relationship summary for this client (D.57) — the advisory revenue basis (the
    client's AUM) the firm's relationship rests on, composed read-only from the authoritative portfolio owner.
    Aggregate total only, never a payload; per-client fee / commission billing has no authoritative owner
    (`not_configured`) and is never fabricated. Never bills/invoices/posts anything; never a second accounting
    or billing engine; deep-links to the authoritative financial surface."""
    from app.services.financial_operations import client_financial
    pid = _pid(ctx)
    return {**(client_financial(principal, pid) if pid else {"enabled": False, "advisory_revenue_basis": None}),
            "source": "financial_operations.client_financial", "not_a_second_engine": True}


def risk_controls(principal, ctx):
    """A compact risk-&-controls summary for this client (D.58) — client-relevant, authorized signals (open
    compliance exceptions, documentation gaps, data-quality issues, integration dependencies), composed
    read-only from ONLY the authoritative owners that support per-client record scope. Firm-wide incidents /
    findings are never exposed here. Counts + status only, never a payload; deep-links to the authoritative
    surface. Never a second GRC/risk engine; an absent signal never certifies compliance."""
    from app.services.enterprise_risk import client_risk_controls
    pid = _pid(ctx)
    return {**(client_risk_controls(principal, pid) if pid else {"enabled": False, "signals": {}}),
            "source": "enterprise_risk.client_risk_controls", "not_a_second_engine": True}


def evidence_readiness(principal, ctx):
    """A compact evidence-&-supervisory-readiness summary for this client (D.59) — client-relevant, authorized
    evidence signals (documentation completeness, open client-specific compliance exceptions, suitability /
    replacement / workflow-approval evidence via open reviews) composed read-only from ONLY the authoritative
    owners that support per-client record scope. Firm-wide examination posture, firm-wide incidents, unrelated
    supervisory findings, other clients' evidence, and confidential regulator information are never exposed.
    Counts + status only, never a payload; deep-links to the authoritative surface. Never a second compliance/
    evidence engine; operational readiness is not regulatory certification."""
    from app.services.regulatory_readiness import client_evidence_readiness
    pid = _pid(ctx)
    return {**(client_evidence_readiness(principal, pid) if pid else {"enabled": False, "signals": {}}),
            "source": "regulatory_readiness.client_evidence_readiness", "not_a_second_engine": True}


def operational_impact(principal, ctx):
    """A compact operational-impact summary for this client (D.60) — the external services / vendors the
    client's data depends on, composed read-only from ONLY the genuinely record-scoped owner (the Integration
    Hub per-entity read). Firm-wide operational information (incidents, alerts, service health) is never
    exposed at client scope; per-client incident impact has no authoritative owner (not_configured). Counts
    only, never a payload; deep-links to the authoritative surface. Never a second incident/monitoring engine;
    operational posture is not a certification that production is healthy."""
    from app.services.operational_resilience import client_operational_impact
    pid = _pid(ctx)
    return {**(client_operational_impact(principal, pid) if pid else {"enabled": False, "signals": {}}),
            "source": "operational_resilience.client_operational_impact", "not_a_second_engine": True}


def servicing_team(principal, ctx):
    """A compact servicing-team summary for this client (D.61) — ONLY the record-scoped staffing directly
    related to servicing this client (who is assigned to the record), composed read-only from the authoritative
    authorization owner. **Employee workload, firm utilization, and unrelated staffing data are never exposed
    at client scope.** Counts only, never an employee detail; deep-links to the authoritative surface. Never a
    second HR/scheduling engine; an operational summary, never an HR record."""
    from app.services.capacity_planning import client_staffing
    pid = _pid(ctx)
    return {**(client_staffing(principal, pid) if pid else {"enabled": False, "signals": {}}),
            "source": "capacity_planning.client_staffing", "not_a_second_engine": True}


def knowledge_documentation(principal, ctx):
    """A compact documentation summary for this client (D.62) — ONLY the record-scoped documentation relevant
    to servicing this client (this client's document count + documentation gaps), composed read-only from the
    authoritative Document Intelligence per-entity read over the Document Platform's scoped reads. **Internal
    SOPs, unrelated documentation, confidential operational procedures, and firm-wide documentation metrics are
    never exposed at client scope.** Counts + status only, never document contents; deep-links to the
    authoritative surface. Never a second wiki/DMS; a documentation-coverage summary, never fabricated
    knowledge."""
    from app.services.knowledge_management import client_documentation
    pid = _pid(ctx)
    return {**(client_documentation(principal, pid) if pid else {"enabled": False, "signals": {}}),
            "source": "knowledge_management.client_documentation", "not_a_second_engine": True}


def change_impact(principal, ctx):
    """A record-scoped change-impact summary for this client (D.63) — ONLY the external systems / integrations
    whose configuration changes could touch THIS client's data, composed read-only from the authoritative
    person lineage (via the change layer's `client_change_impact`, over Integration Hub / MDM lineage).
    **Firm-wide change / release / deployment / CI status is NOT record-scoped and is never exposed here** —
    there is no authoritative record-scoped change-management owner, so beyond the affected-integration surface
    this section reports not_configured honestly. Counts + source-system names only, never a deployment
    payload / configuration value; deep-links to the authoritative surface. Never a second change engine; never
    creates / merges / deploys / changes / approves anything. Merged is not deployed."""
    from app.services.change_management import client_change_impact
    pid = _pid(ctx)
    return {**(client_change_impact(principal, pid) if pid else {"enabled": False, "signals": {}}),
            "source": "change_management.client_change_impact", "not_a_second_engine": True}


def platform_dependencies(principal, ctx):
    """A record-scoped platform-dependency summary for this client (D.64). There is NO authoritative owner that
    maps a client RECORD to a platform / environment / infrastructure dependency, so this is reported
    `not_configured` honestly — **internal infrastructure, deployment topology, and environment metadata
    unrelated to the record are never exposed, and platform impact is never inferred at record scope.** Never a
    second CMDB / infrastructure platform; never creates / deploys / provisions / modifies anything."""
    from app.services.environment_management import client_platform_dependencies
    pid = _pid(ctx)
    return {**(client_platform_dependencies(principal, pid) if pid else {"enabled": False, "available": False,
               "signals": {}}),
            "source": "environment_management.client_platform_dependencies", "not_a_second_engine": True}


def authorization_context(principal, ctx):
    """A record-scoped authorization-context summary for this client (D.65) — ONLY the current principal's OWN
    authorization decision for this record, composed read-only from the authoritative Security Authorization
    owner (`record_in_scope`). **No internal identities, privileged roles, permission maps, authentication
    metadata, or security configuration are ever exposed, and authorization is never inferred** — this is the
    platform's actual, already-made decision for THIS principal on THIS record. Never a second authorization
    engine; never authenticates / authorizes / assigns / grants anything."""
    from app.services.identity_governance import client_authorization_context
    pid = _pid(ctx)
    return {**(client_authorization_context(principal, pid) if pid else {"enabled": False, "available": False,
               "signals": {}}),
            "source": "identity_governance.client_authorization_context", "not_a_second_engine": True}


def data_governance_metadata(principal, ctx):
    """A record-scoped data-governance-metadata summary for this client (D.66) — ONLY the source-system lineage
    / provenance for this client's record, composed read-only from the authoritative Governance MDM owner
    (`person_lineage`). **No internal governance notes, confidential metadata, quality-rule internals, system
    architecture, or platform configuration are ever exposed, and governance state is never inferred.** Never a
    second catalog / lineage engine; never mutates metadata / creates lineage / assigns a steward / repairs
    data."""
    from app.services.data_governance_intelligence import client_data_governance
    pid = _pid(ctx)
    return {**(client_data_governance(principal, pid) if pid else {"enabled": False, "available": False,
               "signals": {}}),
            "source": "data_governance_intelligence.client_data_governance", "not_a_second_engine": True}


def _matches(event, ctx):
    pid, hid = _pid(ctx), _hid(ctx)
    if pid and event.get("person_id") == pid:
        return True
    if hid and event.get("household_id") == hid:
        return True
    return not (pid or hid)
