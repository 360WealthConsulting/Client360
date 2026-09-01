from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from app.db import audit_events, engine
from app.security.audit import write_audit_event
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import employee_admin as ea
from app.services.compliance.rule_catalog import RuleCatalog
from app.services.document_naming import document_display_name as _doc_display_name
from app.services.identity import (
    add_team_membership,
    assign_record,
    assign_role,
    compose_role,
    invite_user,
    list_identity_data,
    set_user_status,
)
from app.templating import render_error

router = APIRouter(prefix="/admin", tags=["administration"])
templates = Jinja2Templates(directory="app/templates")

class UserInvite(BaseModel): email: str; display_name: str; auth_subject: str | None = None
class StatusChange(BaseModel): status: str
class RoleAssignment(BaseModel): user_id: int; role_id: int
class RoleComposition(BaseModel): capability_ids: list[int]
class TeamMembership(BaseModel): user_id: int; team_id: int; membership_role: str = "member"
class RecordAssignment(BaseModel): user_id: int; entity_type: str; entity_id: int; assignment_type: str; team_id: int | None = None

def audit(request, principal, action, entity_type, entity_id=None, metadata=None):
    write_audit_event(action=action, entity_type=entity_type, entity_id=entity_id, actor_user_id=principal.user_id, request_id=request.state.request_id, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"), metadata=metadata)

@router.get("")
def administration(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(request=request, name="admin/identity.html", context={"identity": list_identity_data(), "principal": principal})


_BUSINESS_CONTACT_TYPES = {"business", "organization", "company", "entity", "business_contact", "org"}
_BUSINESS_NAME_HINTS = (" llc", " inc", " corp", " co.", " company", " ltd", " pllc", " lp",
                        " associates", " enterprises", " services", " group", " holdings")


def _designation(contact_type, name):
    """Whether a people-table row is a business contact (label + flag), from the existing
    contact_type metadata, with a name-suffix fallback for display only."""
    ct = (contact_type or "").strip().lower()
    low = (name or "").lower()
    is_biz = ct in _BUSINESS_CONTACT_TYPES or any(h in (" " + low + " ") for h in _BUSINESS_NAME_HINTS)
    return ("Business Contact" if is_biz else "Person", is_biz)


def _person_display(conn, pid):
    """Full identifying view of a people-table record for the worklist + confirmation page."""
    from app.db import households, people
    r = conn.execute(select(people.c.id, people.c.full_name, people.c.contact_type,
                            people.c.primary_email, people.c.primary_phone, people.c.household_id)
                     .where(people.c.id == pid)).mappings().first()
    if not r:
        return None
    hh = conn.execute(select(households.c.name).where(households.c.id == r["household_id"])).scalar() \
        if r["household_id"] else None
    designation, is_biz = _designation(r["contact_type"], r["full_name"])
    return {"id": r["id"], "name": r["full_name"], "designation": designation, "is_business": is_biz,
            "contact_type": r["contact_type"], "email": r["primary_email"], "phone": r["primary_phone"],
            "household_id": r["household_id"], "household_name": hh, "link": f"/client/{r['id']}"}


def _destination_display(conn, entity_type, entity_id):
    """Complete destination identity for the confirmation page (person / household / organization)."""
    from app.db import households, people, relationship_entities
    if entity_type == "person":
        d = _person_display(conn, entity_id) or {"id": entity_id, "name": None}
        return {"kind": "person", **d}
    if entity_type == "household":
        r = conn.execute(select(households.c.id, households.c.name)
                         .where(households.c.id == entity_id)).mappings().first()
        members = list(conn.scalars(select(people.c.full_name)
                                    .where(people.c.household_id == entity_id).order_by(people.c.full_name)))
        return {"kind": "household", "id": entity_id, "name": (r["name"] if r else None),
                "members": members, "link": f"/client/household/{entity_id}"}
    if entity_type == "organization":
        r = conn.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                relationship_entities.c.entity_type)
                         .where(relationship_entities.c.id == entity_id)).mappings().first()
        return {"kind": "organization", "id": entity_id, "name": (r["name"] if r else None),
                "entity_type": (r["entity_type"] if r else None),
                "link": f"/relationship-entities/{entity_id}"}
    return {"kind": entity_type, "id": entity_id, "name": None}


def _owner_detail(conn, row):
    """Current-owner type/id/name for a document row (name resolved when available)."""
    from app.db import households, people, relationship_entities
    if row["person_id"] is not None:
        n = conn.execute(select(people.c.full_name).where(people.c.id == row["person_id"])).scalar()
        return {"type": "person", "id": row["person_id"], "name": n,
                "label": f"person #{row['person_id']}" + (f" — {n}" if n else "")}
    if row["household_id"] is not None:
        n = conn.execute(select(households.c.name).where(households.c.id == row["household_id"])).scalar()
        return {"type": "household", "id": row["household_id"], "name": n,
                "label": f"household #{row['household_id']}" + (f" — {n}" if n else "")}
    if row["organization_id"] is not None:
        n = conn.execute(select(relationship_entities.c.name)
                         .where(relationship_entities.c.id == row["organization_id"])).scalar()
        return {"type": "organization", "id": row["organization_id"], "name": n,
                "label": f"organization #{row['organization_id']}" + (f" — {n}" if n else "")}
    return {"type": None, "id": None, "name": None, "label": "Unassigned (NULL)"}


_EXCEL_EXTS = {"xlsx", "xlsm"}
_HEIF_EXTS = {"heic", "heif"}


def _view_url(document_id, name):
    """Route the View action by file type: Excel -> Client360 workbook preview; HEIC/HEIF -> Client360
    image preview (JPEG rendition); everything else -> the existing inline document route (PDF and
    browser-native images open inline; other types download)."""
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    if ext in _EXCEL_EXTS:
        return f"/documents/{document_id}/preview"
    if ext in _HEIF_EXTS:
        return f"/documents/{document_id}/image-preview"
    return f"/documents/{document_id}/download?inline=1"


def _documents_detail(conn, doc_ids):
    """Per-document detail for the confirmation page: id, filename, source folder/path, type/year,
    current owner (type/id/name), a type-aware View link and a Download link. Read-only."""
    from app.db import documents
    if not doc_ids:
        return []
    rows = conn.execute(
        select(documents.c.id, documents.c.original_name, documents.c.display_name,
               documents.c.category, documents.c.subcategory,
               documents.c.tags, documents.c.storage_path, documents.c.person_id,
               documents.c.household_id, documents.c.organization_id)
        .where(documents.c.id.in_(list(doc_ids))).order_by(documents.c.id)).mappings().all()
    out = []
    for r in rows:
        tags = r["tags"] or {}
        owner = _owner_detail(conn, r)
        out.append({
            "id": r["id"], "name": _doc_display_name(r), "original_name": r["original_name"],
            "source_folder": tags.get("taxdome_folder"), "source_path": r["storage_path"],
            "doc_type": r["category"], "doc_subtype": r["subcategory"],
            "year": tags.get("tax_year") or tags.get("year"),
            "current_owner": owner["label"], "current_owner_type": owner["type"],
            "current_owner_id": owner["id"], "current_owner_name": owner["name"],
            "view_url": _view_url(r["id"], r["original_name"]),
            "download_url": f"/documents/{r['id']}/download",
        })
    return out


def _folder_category_counts(conn, folder_names):
    """Per-folder document category counts using the SAME eligibility rule as the resolve service:
    ELIGIBLE = all three ownership fields NULL (and not a permanent reject); ALREADY OWNED = any
    ownership field set; PERMANENT REJECT = protected. Read-only; grouped queries."""
    from app.db import documents
    from app.importers.taxdome_drive import taxdome_filter
    from app.services.households import PERMANENT_REJECT_DOCUMENT_IDS
    fc = documents.c.tags["taxdome_folder"].astext
    rejects = tuple(sorted(PERMANENT_REJECT_DOCUMENT_IDS))
    names = list(folder_names)
    counts = {n: {"docs_in_folder": 0, "eligible": 0, "already_owned": 0, "reject": 0} for n in names}
    if not names:
        return counts
    base = and_(taxdome_filter(documents), fc.in_(names))
    not_reject = documents.c.id.notin_(rejects)

    def _tally(key, predicate):
        for name, n in conn.execute(select(fc, func.count()).where(predicate).group_by(fc)):
            if name in counts:
                counts[name][key] = n

    _tally("docs_in_folder", base)
    _tally("reject", and_(base, documents.c.id.in_(rejects)))
    _tally("eligible", and_(base, not_reject, documents.c.person_id.is_(None),
                            documents.c.household_id.is_(None), documents.c.organization_id.is_(None)))
    _tally("already_owned", and_(base, not_reject, or_(
        documents.c.person_id.isnot(None), documents.c.household_id.isnot(None),
        documents.c.organization_id.isnot(None))))
    return counts


def _folder_samples_and_candidates(folders):
    """Enrich each folder row for the resolution UI: precise category counts (eligible / already-owned /
    reject), sample ELIGIBLE document names, and candidate people with full distinguishing info
    (designation/email/phone/household + record link). Read-only — never mutates."""
    from app.db import documents
    from app.importers.taxdome_drive import taxdome_filter
    fc = documents.c.tags["taxdome_folder"].astext
    out = []
    with engine.connect() as conn:
        counts = _folder_category_counts(conn, [f["folder"] for f in folders])
        for f in folders:
            folder = f["folder"]
            samples = list(conn.scalars(
                select(documents.c.original_name).where(
                    taxdome_filter(documents), fc == folder,
                    documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                    documents.c.organization_id.is_(None)).limit(4)))
            cands = []
            for s in (f.get("suggestions") or [])[:6]:
                pid = s.get("id") if isinstance(s, dict) else None
                disp = _person_display(conn, pid) if pid else None
                if disp:
                    cands.append(disp)
            c = counts.get(folder, {"docs_in_folder": 0, "eligible": 0, "already_owned": 0, "reject": 0})
            out.append({**f, "sample_documents": samples, "candidates": cands,
                        "docs_in_folder": c["docs_in_folder"], "eligible": c["eligible"],
                        "already_owned": c["already_owned"], "reject": c["reject"]})
    return out


def _entity_search(q, limit=15):
    """Search existing people / households / organizations by name for manual folder resolution."""
    from app.db import households, people, relationship_entities
    like = f"%{q.strip()}%"
    res = {"people": [], "households": [], "organizations": []}
    if not q.strip():
        return res
    with engine.connect() as conn:
        res["people"] = [dict(r) for r in conn.execute(
            select(people.c.id, people.c.full_name, people.c.primary_email, people.c.household_id)
            .where(people.c.full_name.ilike(like)).order_by(people.c.full_name).limit(limit)).mappings()]
        res["households"] = [dict(r) for r in conn.execute(
            select(households.c.id, households.c.name)
            .where(households.c.name.ilike(like)).order_by(households.c.name).limit(limit)).mappings()]
        res["organizations"] = [dict(r) for r in conn.execute(
            select(relationship_entities.c.id, relationship_entities.c.name,
                   relationship_entities.c.entity_type)
            .where(relationship_entities.c.name.ilike(like))
            .order_by(relationship_entities.c.name).limit(limit)).mappings()]
    return res


@router.get("/documents/unassigned")
def unassigned_documents(request: Request, q: str = "",
                         principal: Principal = Depends(require_capability("client.write"))):
    """Unified human-resolution worklist for genuinely unassigned documents.

    Existing TaxDome folder-level resolution remains unchanged. Drake documents
    are surfaced individually because their canonical registration intentionally
    preserved them as unassigned rather than guessing an owner.

    This route is READ-ONLY. Ownership changes still flow through the existing
    preview -> confirm resolve-document endpoint.
    """
    from sqlalchemy import exists

    from app.db import documents, metadata
    from app.services.households import unresolved_taxdome_folders
    from app.services.document_owner_proposal import (
        PERMANENT_REJECT_DOCUMENT_IDS,
        build_match_indexes,
        propose_document_owner,
    )

    folders = _folder_samples_and_candidates(
        unresolved_taxdome_folders(limit=200)
    )

    drake_unassigned = []
    ds = metadata.tables.get("document_sources")

    if ds is not None:
        with engine.connect() as conn:
            stmt = (
                select(
                    documents.c.id,
                    documents.c.original_name,
                    documents.c.display_name,
                    documents.c.notes,
                    documents.c.ocr_status,
                    documents.c.created_at,
                )
                .where(
                    documents.c.person_id.is_(None),
                    documents.c.household_id.is_(None),
                    documents.c.organization_id.is_(None),
                    documents.c.status != "deleted",
                    documents.c.archived.is_(False),
                    exists(
                        select(1).where(
                            ds.c.document_id == documents.c.id,
                            ds.c.source_system == "Drake",
                        )
                    ),
                )
                .order_by(documents.c.id)
                .limit(500)
            )

            rows = conn.execute(stmt).mappings().all()

            ids = [
                int(row["id"])
                for row in rows
                if int(row["id"]) not in PERMANENT_REJECT_DOCUMENT_IDS
            ]

            proposal_idx = build_match_indexes(conn)
            proposal_map = {}

            for document_id in ids:
                try:
                    proposal_map[document_id] = propose_document_owner(
                        document_id,
                        conn=conn,
                        idx=proposal_idx,
                        with_text=False,
                        ocr=False,
                    )
                except Exception:
                    proposal_map[document_id] = {
                        "eligible": True,
                        "confidence": "ERROR",
                        "proposed_entity_type": None,
                        "proposed_entity_id": None,
                        "proposed_entity_name": None,
                        "evidence": [],
                        "best_candidates": [],
                        "analysis_unavailable": True,
                    }

            source_map = {}

            if ids:
                source_rows = conn.execute(
                    select(
                        ds.c.document_id,
                        ds.c.source_external_id,
                        ds.c.source_path,
                        ds.c.metadata,
                    )
                    .where(
                        ds.c.document_id.in_(ids),
                        ds.c.source_system == "Drake",
                    )
                    .order_by(ds.c.document_id, ds.c.id)
                ).mappings().all()

                for src in source_rows:
                    source_map.setdefault(
                        int(src["document_id"]),
                        dict(src),
                    )

            for row in rows:
                document_id = int(row["id"])

                if document_id in PERMANENT_REJECT_DOCUMENT_IDS:
                    continue

                src = source_map.get(document_id, {})
                meta = src.get("metadata") or {}

                drake_unassigned.append({
                    "id": document_id,
                    # Canonical helper -- one precedence rule, one safety gate. Inlining
                    # "display_name or original_name" here duplicated that rule and bypassed the
                    # sensitive-identifier check every other surface goes through.
                    "name": _doc_display_name(row) or f"Document {document_id}",
                    "original_name": row["original_name"],
                    "notes": row["notes"],
                    "ocr_status": row["ocr_status"],
                    "drake_client_id": src.get("source_external_id"),
                    "source_path": src.get("source_path"),
                    "source_metadata": meta,
                    "view_url": f"/documents/{document_id}/download?inline=1",
                    "download_url": f"/documents/{document_id}/download",
                    "proposal": proposal_map.get(document_id),
                })

    return templates.TemplateResponse(
        request=request,
        name="admin/unassigned_documents.html",
        context={
            "principal": principal,
            "unassigned": folders,
            "drake_unassigned": drake_unassigned,
            "q": q,
            "search": _entity_search(q),
            "ok": request.query_params.get("ok"),
            "err": request.query_params.get("err"),
        },
    )


def _folder_candidates(conn, folder):
    """Suggested owners for a folder, for per-document actions: canonical people (engine suggestions,
    enriched with distinguishing info) plus households and businesses/orgs whose name matches the
    top-level folder token. Suggestions only — never assign. Returns (people, households, orgs)."""
    import re as _re

    from app.db import households, relationship_entities
    from app.importers.taxdome_drive import suggest_people

    def _norm(s):
        return _re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    people_c = []
    try:
        for s in (suggest_people(conn, folder) or [])[:6]:
            disp = _person_display(conn, s.get("id")) if isinstance(s, dict) else None
            if disp:
                people_c.append(disp)
    except Exception:  # noqa: BLE001
        people_c = []

    # Top-level TaxDome client folder token (drop a leading TaxDome root; ignore child paths).
    segs = [s for s in _re.split(r"[/\\]", folder or "") if s.strip()]
    if segs and _re.sub(r"[^a-z0-9]+", "", segs[0].lower()) in ("taxdome", "taxdomedrive"):
        segs = segs[1:]
    top = segs[0].strip() if segs else ""
    ntop = _norm(top)
    hh_c, org_c = [], []
    if top:
        like = f"%{top.strip()}%"
        for r in conn.execute(select(households.c.id, households.c.name)
                              .where(households.c.name.ilike(like)).limit(25)).mappings():
            if _norm(r["name"]) == ntop:
                hh_c.append({"id": r["id"], "name": r["name"]})
        for e in conn.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                     relationship_entities.c.entity_type)
                              .where(relationship_entities.c.name.ilike(like)).limit(25)).mappings():
            if _norm(e["name"]) == ntop:
                org_c.append({"id": e["id"], "name": e["name"], "entity_type": e["entity_type"]})
    return people_c, hh_c[:5], org_c[:5]


def _folder_documents(conn, folder):
    """Document ids for a folder, split by the SAME rule the resolve service uses:
    eligible (all three ownership fields NULL, not reject) / already-owned / permanent-reject."""
    from app.db import documents
    from app.importers.taxdome_drive import taxdome_filter
    from app.services.households import PERMANENT_REJECT_DOCUMENT_IDS
    fc = documents.c.tags["taxdome_folder"].astext
    rejects = tuple(sorted(PERMANENT_REJECT_DOCUMENT_IDS))
    base = and_(taxdome_filter(documents), fc == folder)
    not_reject = documents.c.id.notin_(rejects)
    eligible = sorted(conn.scalars(select(documents.c.id).where(and_(
        base, not_reject, documents.c.person_id.is_(None), documents.c.household_id.is_(None),
        documents.c.organization_id.is_(None)))))
    owned = sorted(conn.scalars(select(documents.c.id).where(and_(
        base, not_reject, or_(documents.c.person_id.isnot(None), documents.c.household_id.isnot(None),
                              documents.c.organization_id.isnot(None))))))
    reject = sorted(conn.scalars(select(documents.c.id).where(and_(base, documents.c.id.in_(rejects)))))
    return eligible, owned, reject


@router.get("/documents/unassigned/review")
def review_unassigned_folder(request: Request, folder: str,
                             principal: Principal = Depends(require_capability("client.write"))):
    """Inspect every document in a source folder BEFORE choosing an owner. Read-only: lists eligible,
    already-owned, and permanent-reject documents (each with an authorized View/Open link) plus the
    candidate people, so an administrator can open the actual files and decide ownership. Opening/View
    never mutates; assignment happens only via preview -> explicit Confirm."""
    from app.services.document_owner_proposal import build_match_indexes, propose_document_owner
    with engine.connect() as conn:
        eligible_ids, owned_ids, reject_ids = _folder_documents(conn, folder)
        eligible_docs = _documents_detail(conn, eligible_ids)
        already_owned_docs = _documents_detail(conn, owned_ids)
        excluded_docs = _documents_detail(conn, reject_ids)
        candidates, household_candidates, org_candidates = _folder_candidates(conn, folder)
        # Read-only content-based owner proposals (bounded; shared canonical indexes; never assigns).
        idx = build_match_indexes(conn) if eligible_ids else None
        proposals = {}
        for did in eligible_ids[:30]:
            try:
                proposals[did] = propose_document_owner(did, conn=conn, idx=idx)
            except Exception:  # noqa: BLE001 — one document's analysis can't break the page
                proposals[did] = None
        for d in eligible_docs:
            d["proposal"] = proposals.get(d["id"])
    return templates.TemplateResponse(request=request, name="admin/unassigned_review.html",
        context={"principal": principal, "folder": folder, "eligible_docs": eligible_docs,
                 "already_owned_docs": already_owned_docs, "excluded_docs": excluded_docs,
                 "candidates": candidates, "household_candidates": household_candidates,
                 "org_candidates": org_candidates})


@router.post("/documents/unassigned/resolve")
def resolve_unassigned_folder(
        request: Request, folder: str = Form(...), entity_type: str = Form(...),
        entity_id: int = Form(...), confirm: str = Form(""),
        principal: Principal = Depends(require_capability("client.write"))):
    """Folder-level ownership resolution with a mandatory preview -> confirm step. Without confirm=yes it
    renders the exact destination + affected-document count/ids for review; with confirm=yes it applies
    the assignment to every eligible (non-reject, currently-NULL) document in the folder in one audited
    operation. Record-scope enforced; the six permanent V2 rejects are excluded by the service."""
    from app.security.authorization import record_in_scope
    from app.services.households import resolve_folder_ownership
    if entity_type not in {"person", "household", "organization"}:
        return render_error(request, 400, detail="Invalid entity type.")
    scope_type = {"person": "person", "household": "household", "organization": "organization"}[entity_type]
    # Organizations are firm-scoped here (no per-record org assignments table); require record.write_all.
    if entity_type == "organization":
        if not principal.can("record.write_all"):
            return render_error(request, 404, detail="Organization not found.")
    elif not record_in_scope(principal, scope_type, entity_id, write=True):
        return render_error(request, 404, detail="Destination not found.")
    kwargs = {"person": {"person_id": entity_id}, "household": {"household_id": entity_id},
              "organization": {"organization_id": entity_id}}[entity_type]
    rid = getattr(request.state, "request_id", None)
    try:
        if confirm.strip().lower() != "yes":
            preview = resolve_folder_ownership(folder, dry_run=True, **kwargs)
            with engine.connect() as conn:
                destination = _destination_display(conn, entity_type, entity_id)
                affected_docs = _documents_detail(conn, preview["affected_document_ids"])
                already_owned_docs = _documents_detail(conn, preview.get("already_owned_document_ids", []))
                excluded_docs = _documents_detail(conn, preview["excluded_permanent_rejects"])
            return templates.TemplateResponse(request=request, name="admin/unassigned_confirm.html",
                context={"principal": principal, "folder": folder, "entity_type": entity_type,
                         "entity_id": entity_id, "preview": preview, "destination": destination,
                         "affected_docs": affected_docs, "already_owned_docs": already_owned_docs,
                         "excluded_docs": excluded_docs})
        result = resolve_folder_ownership(folder, actor_user_id=principal.user_id, request_id=rid, **kwargs)
    except ValueError as exc:
        return render_error(request, 400, detail=str(exc))
    audit(request, principal, "document.folder_resolved", "taxdome_folder", None,
          {"folder": folder, "destination": result["destination"],
           "documents_updated": result["documents_affected"]})
    msg = f"Assigned {result['documents_affected']} document(s) in '{folder}' to " \
          f"{result['destination']['entity_type']} {result['destination']['entity_name']}."
    return _back("/admin/documents/unassigned", ok=msg)


@router.post("/documents/unassigned/resolve-document")
def resolve_unassigned_document(
        request: Request, document_id: int = Form(...), entity_type: str = Form(...),
        entity_id: int = Form(...), folder: str = Form(""), confirm: str = Form(""),
        principal: Principal = Depends(require_capability("client.write"))):
    """Per-document ownership resolution (preview -> confirm). Assigns ONLY the given document id to the
    selected owner, leaving sibling documents untouched. The service re-checks on write that the
    document is still genuinely unassigned and not a permanent reject (stale/double-confirm safe).
    Record-scope enforced; gated require_capability('client.write')."""
    from app.security.authorization import record_in_scope
    from app.services.households import resolve_document_ownership
    if entity_type not in {"person", "household", "organization"}:
        return render_error(request, 400, detail="Invalid entity type.")
    if entity_type == "organization":
        if not principal.can("record.write_all"):
            return render_error(request, 404, detail="Organization not found.")
    elif not record_in_scope(principal, entity_type, entity_id, write=True):
        return render_error(request, 404, detail="Destination not found.")
    kwargs = {"person": {"person_id": entity_id}, "household": {"household_id": entity_id},
              "organization": {"organization_id": entity_id}}[entity_type]
    rid = getattr(request.state, "request_id", None)
    from urllib.parse import quote
    back = ("/admin/documents/unassigned/review?folder=" + quote(folder, safe="")) if folder \
        else "/admin/documents/unassigned"
    try:
        if confirm.strip().lower() != "yes":
            preview = resolve_document_ownership(document_id, dry_run=True, **kwargs)
            with engine.connect() as conn:
                destination = _destination_display(conn, entity_type, entity_id)
                docs = _documents_detail(conn, [document_id])
            return templates.TemplateResponse(request=request, name="admin/unassigned_document_confirm.html",
                context={"principal": principal, "folder": folder, "document_id": document_id,
                         "entity_type": entity_type, "entity_id": entity_id, "destination": destination,
                         "doc": (docs[0] if docs else None), "preview": preview})
        result = resolve_document_ownership(document_id, actor_user_id=principal.user_id,
                                            request_id=rid, **kwargs)
    except ValueError as exc:
        return render_error(request, 400, detail=str(exc))
    if result.get("assigned"):
        audit(request, principal, "document.single_resolved", "document", document_id,
              {"destination": result["destination"], "folder": folder})
        d = result["destination"]
        return _back(back, ok=f"Document {document_id} assigned to {d['entity_type']} "
                             f"{d['entity_name']}. Remaining documents are unchanged.")
    return _back(back, err=f"Document {document_id} was not assigned ({result.get('reason', 'not eligible')}).")


@router.post("/users")
def create_user(payload: UserInvite, request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    user_id = invite_user(payload.email, payload.display_name, payload.auth_subject); audit(request, principal, "identity.user_invited", "user", user_id); return {"id": user_id}

@router.patch("/users/{user_id}/status")
def change_status(user_id: int, payload: StatusChange, request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    try: changed = set_user_status(user_id, payload.status)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    if not changed: raise HTTPException(404, "User not found")
    audit(request, principal, "identity.status_changed", "user", user_id, {"status": payload.status}); return {"status": payload.status}

@router.post("/user-roles")
def create_user_role(payload: RoleAssignment, request: Request, principal: Principal = Depends(require_capability("role.manage"))):
    try: item_id = assign_role(payload.user_id, payload.role_id, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_assign_denied", entity_type="user", entity_id=payload.user_id, actor_user_id=principal.user_id, outcome="denied", request_id=request.state.request_id, metadata={"role_id": payload.role_id, "detail": str(exc)}); raise HTTPException(403, str(exc)) from exc
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    audit(request, principal, "authorization.role_assigned", "user", payload.user_id, {"role_id": payload.role_id}); return {"id": item_id}

@router.put("/roles/{role_id}/capabilities")
def update_role(role_id: int, payload: RoleComposition, request: Request, principal: Principal = Depends(require_capability("role.manage"))):
    try: compose_role(role_id, payload.capability_ids, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_compose_denied", entity_type="role", entity_id=role_id, actor_user_id=principal.user_id, outcome="denied", request_id=request.state.request_id, metadata={"capability_ids": payload.capability_ids, "detail": str(exc)}); raise HTTPException(403, str(exc)) from exc
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    audit(request, principal, "authorization.role_composed", "role", role_id, {"capability_ids": payload.capability_ids}); return {"role_id": role_id}

@router.post("/team-memberships")
def create_membership(payload: TeamMembership, request: Request, principal: Principal = Depends(require_capability("team.manage"))):
    item_id = add_team_membership(payload.user_id, payload.team_id, payload.membership_role); audit(request, principal, "team.membership_added", "team", payload.team_id, {"user_id": payload.user_id}); return {"id": item_id}

@router.post("/assignments")
def create_assignment(payload: RecordAssignment, request: Request, principal: Principal = Depends(require_capability("assignment.manage"))):
    try: item_id = assign_record(**payload.dict())
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    audit(request, principal, "assignment.created", payload.entity_type, payload.entity_id, {"user_id": payload.user_id, "assignment_type": payload.assignment_type}); return {"id": item_id}

# --- Employee & Access Management UI (administrator-only) ---------------------
# All routes below fall under the middleware `^/admin` rule (identity.manage); role-changing routes
# additionally require role.manage at the handler. Every mutation is audited; the final active
# administrator can never be removed or deactivated. No route writes the DB outside the identity
# service layer, and no capability is created from the UI.

def _back(url: str, ok: str | None = None, err: str | None = None):
    from urllib.parse import quote
    sep = "&" if "?" in url else "?"
    if ok:
        return RedirectResponse(f"{url}{sep}ok={quote(ok)}", status_code=303)
    if err:
        return RedirectResponse(f"{url}{sep}err={quote(err)}", status_code=303)
    return RedirectResponse(url, status_code=303)


@router.get("/employees")
def employees_list(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(request=request, name="admin/employees.html", context={
        "principal": principal, "employees": ea.roster(),
        "ok": request.query_params.get("ok"), "err": request.query_params.get("err")})


@router.get("/employees/{user_id}")
def employee_detail(user_id: int, request: Request,
                    principal: Principal = Depends(require_capability("identity.manage"))):
    detail = ea.employee_detail(user_id)
    if detail is None:
        return render_error(request, 404, detail="Employee not found.")
    return templates.TemplateResponse(request=request, name="admin/employee_detail.html", context={
        "principal": principal, "d": detail, "is_last_admin": ea.is_last_active_administrator(user_id),
        "ok": request.query_params.get("ok"), "err": request.query_params.get("err")})


@router.post("/employees/invite")
def employee_invite(request: Request, email: str = Form(...), display_name: str = Form(...),
                    role_id: int | None = Form(None), role_ids: list[int] = Form(default=[]),
                    principal: Principal = Depends(require_capability("identity.manage"))):
    # Accepts one or many access profiles. `role_id` is kept for backward compatibility with the
    # legacy single-select form; `role_ids` carries the new multi-select. Effective access is the
    # union of every profile that assigns cleanly (each still ceiling-checked + audited).
    user_id = invite_user(email, display_name)
    audit(request, principal, "identity.user_invited", "user", user_id, {"email": email})
    # Tolerate either field being omitted (legacy single-select sends role_id; new multi-select
    # sends role_ids) so both forms — and direct callers — behave identically.
    wanted = set(role_ids if isinstance(role_ids, list) else [])
    if isinstance(role_id, int):
        wanted.add(role_id)
    failed = []
    for rid in sorted(wanted):
        try:
            assign_role(user_id, rid, actor_capabilities=principal.capabilities)
            audit(request, principal, "authorization.role_assigned", "user", user_id, {"role_id": rid})
        except (PermissionError, ValueError) as exc:
            failed.append(f"{ea.role_code(rid) or rid}: {exc}")
    if failed:
        return _back(f"/admin/employees/{user_id}",
                     err="Invited, but some profiles were not assigned — " + "; ".join(failed))
    return _back(f"/admin/employees/{user_id}", ok="Employee invited.")


@router.post("/employees/{user_id}/status")
def employee_status(user_id: int, request: Request, status: str = Form(...),
                    principal: Principal = Depends(require_capability("identity.manage"))):
    if status == "disabled" and ea.is_last_active_administrator(user_id):
        return _back(f"/admin/employees/{user_id}",
                     err="Cannot deactivate the final active administrator.")
    try:
        changed = set_user_status(user_id, status)
    except ValueError as exc:
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    if not changed:
        return render_error(request, 404, detail="Employee not found.")
    audit(request, principal, "identity.status_changed", "user", user_id, {"status": status})
    return _back(f"/admin/employees/{user_id}", ok=f"Account {status}.")


@router.post("/employees/{user_id}/identity")
def employee_identity(user_id: int, request: Request, email: str | None = Form(None),
                      auth_subject: str | None = Form(None),
                      principal: Principal = Depends(require_capability("identity.manage"))):
    try:
        if email:
            if ea.update_email(user_id, email):
                audit(request, principal, "identity.email_changed", "user", user_id, {"email": email})
        if auth_subject is not None:
            if ea.set_auth_subject(user_id, auth_subject):
                audit(request, principal, "identity.subject_mapped", "user", user_id,
                      {"entra_subject_set": bool(auth_subject.strip())})
    except ValueError as exc:
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    return _back(f"/admin/employees/{user_id}", ok="Identity updated.")


@router.post("/employees/{user_id}/roles")
def employee_assign_role(user_id: int, request: Request, role_id: int = Form(...),
                         principal: Principal = Depends(require_capability("role.manage"))):
    try:
        assign_role(user_id, role_id, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_assign_denied", entity_type="user",
                          entity_id=user_id, actor_user_id=principal.user_id, outcome="denied",
                          request_id=request.state.request_id, metadata={"role_id": role_id, "detail": str(exc)})
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    except ValueError as exc:
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    audit(request, principal, "authorization.role_assigned", "user", user_id, {"role_id": role_id})
    return _back(f"/admin/employees/{user_id}", ok="Role assigned.")


@router.post("/employees/{user_id}/roles/remove")
def employee_remove_role(user_id: int, request: Request, role_id: int = Form(...),
                         principal: Principal = Depends(require_capability("role.manage"))):
    # Never strip administrator from the last active admin.
    if ea.role_code(role_id) == ea.ADMIN_ROLE_CODE and ea.is_last_active_administrator(user_id):
        return _back(f"/admin/employees/{user_id}",
                     err="Cannot remove administrator from the final active administrator.")
    changed = ea.end_role(user_id, role_id)
    if changed:
        audit(request, principal, "authorization.role_removed", "user", user_id, {"role_id": role_id})
    return _back(f"/admin/employees/{user_id}", ok="Role removed." if changed else "No active role to remove.")


@router.post("/employees/{user_id}/roles/set")
def employee_set_roles(user_id: int, request: Request, role_ids: list[int] = Form(default=[]),
                       principal: Principal = Depends(require_capability("role.manage"))):
    """Multi-select access-profile editor: reconcile the employee's active roles to exactly the
    submitted set. Additions go through the same capability-ceiling check as single assignment;
    removals honour the final-administrator guard. Each change is audited individually, so history
    is identical to doing the assigns/removes one at a time. Effective permissions are the union of
    the resulting profiles (unchanged RBAC engine). Backward compatible: submitting one id behaves
    exactly like the legacy single-role assignment."""
    current = ea.active_role_ids(user_id)
    desired = set(role_ids)
    to_remove = current - desired
    to_add = desired - current
    errors = []
    for rid in sorted(to_remove):
        if ea.role_code(rid) == ea.ADMIN_ROLE_CODE and ea.is_last_active_administrator(user_id):
            errors.append("administrator cannot be removed from the final active administrator")
            continue
        if ea.end_role(user_id, rid):
            audit(request, principal, "authorization.role_removed", "user", user_id, {"role_id": rid})
    for rid in sorted(to_add):
        try:
            assign_role(user_id, rid, actor_capabilities=principal.capabilities)
            audit(request, principal, "authorization.role_assigned", "user", user_id, {"role_id": rid})
        except PermissionError as exc:
            write_audit_event(action="authorization.role_assign_denied", entity_type="user",
                              entity_id=user_id, actor_user_id=principal.user_id, outcome="denied",
                              request_id=request.state.request_id,
                              metadata={"role_id": rid, "detail": str(exc)})
            errors.append(f"{ea.role_code(rid) or rid}: {exc}")
        except ValueError as exc:
            errors.append(f"{ea.role_code(rid) or rid}: {exc}")
    if errors:
        return _back(f"/admin/employees/{user_id}", err="; ".join(errors))
    if to_add or to_remove:
        return _back(f"/admin/employees/{user_id}", ok="Access profiles updated.")
    return _back(f"/admin/employees/{user_id}", ok="No changes to access profiles.")


@router.get("/access-profiles")
def access_profiles(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(request=request, name="admin/access_profiles.html", context={
        "principal": principal, "profiles": ea.access_profiles()})


@router.get("/invitations")
def invitations(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    roster = ea.roster()
    pending = [e for e in roster if e["access_status"] != "active"]
    return templates.TemplateResponse(request=request, name="admin/invitations.html", context={
        "principal": principal, "pending": pending, "profiles": ea.access_profiles(),
        "ok": request.query_params.get("ok"), "err": request.query_params.get("err")})


@router.get("/audit")
def audit_log(request: Request, limit: int = 100, principal: Principal = Depends(require_capability("audit.read"))):
    with engine.connect() as connection: rows = connection.execute(select(audit_events).order_by(audit_events.c.occurred_at.desc()).limit(min(max(limit, 1), 500))).mappings().all()
    audit(request, principal, "audit.viewed", "audit_event", metadata={"limit": limit}); return templates.TemplateResponse(request=request, name="admin/audit.html", context={"events": rows})


@router.get("/rule-catalog")
def rule_catalog(request: Request, q: str | None = None, category: str | None = None,
                 gate: str | None = None, status: str | None = None, sort: str = "rule_id",
                 desc: bool = False,
                 principal: Principal = Depends(require_capability("audit.read"))):
    """Read-only Rule Catalog — the Phase D.6 governance view over the Advisor
    Intelligence registry. It only reads registry metadata (never executes rules,
    never modifies Advisor Intelligence). No editing/approval/workflow controls."""
    catalog = RuleCatalog.from_registry()
    rules = catalog.query(search=q, category=category, policy_gate=gate,
                          approval_status=status, sort=sort, descending=desc)
    return templates.TemplateResponse(request=request, name="admin/rule_catalog.html", context={
        "principal": principal,
        "rules": rules,
        "categories": catalog.categories(),
        "gates": catalog.policy_gates(),
        "statuses": catalog.approval_statuses(),
        "filters": {"q": q or "", "category": category or "", "gate": gate or "",
                    "status": status or "", "sort": sort, "desc": desc},
        "total": len(catalog.list_rules()),
    })
