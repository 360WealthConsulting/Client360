"""TaxDome Drive — demo dashboard over the EXISTING document + identity model.

DEMO-ONLY surface (mounted only by app.demo.demo_app). It composes the read-only indexer
(app.importers.taxdome_drive) and the existing `documents` / `people` tables — no parallel
document platform. It shows the scan status/counts, runs a scan, resolves unresolved
folder→person mappings (Link to an existing person / Keep Separate as a new client / Defer),
and searches indexed TaxDome document metadata. It NEVER copies drive contents.
"""
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select

from app.importers import taxdome_drive as td
from app.security.audit import write_audit_event
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.templating import install_filters

router = APIRouter(prefix="/demo", tags=["demo"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)

PROVIDER = td.SOURCE_SYSTEM


def _folder_col():
    return td._database().documents.c.tags["taxdome_folder"].astext


def _folder_states(conn):
    """Per-folder rollup: (folder, linked?, deferred?, file_count) for the TaxDome docs."""
    d = td._database().documents
    folder = _folder_col()
    deferred = d.c.tags["review_deferred"].astext
    rows = conn.execute(
        select(folder.label("folder"),
               func.bool_or(d.c.person_id.isnot(None)).label("linked"),
               func.bool_or(deferred == "true").label("deferred"),
               func.count().label("files"))
        .where(td.taxdome_filter(d))
        .group_by(folder)
        .order_by(folder)
    ).mappings().all()
    return rows


def _counts(conn):
    d = td._database().documents
    states = _folder_states(conn)
    linked = sum(1 for s in states if s["linked"])
    deferred = sum(1 for s in states if s["deferred"] and not s["linked"])
    total_folders = len(states)
    total_files = conn.scalar(
        select(func.count()).select_from(d)
        .where(and_(td.taxdome_filter(d), d.c.status == "active"))) or 0
    # A "missing" document is one whose source file has disappeared; the local copy is retained
    # (status stays active), so this is tracked via the availability tag, not the status.
    missing_total = conn.scalar(
        select(func.count()).select_from(d)
        .where(and_(td.taxdome_filter(d),
                    d.c.tags["available_from_source"].astext == "false"))) or 0
    last = td.latest_scan() or {}
    return {
        "total_folders": total_folders,
        "folders_linked": linked,
        "folders_unresolved": max(total_folders - linked - deferred, 0),
        "folders_deferred": deferred,
        "total_files": total_files,
        "missing_total": missing_total,
        "new_last_scan": last.get("copied", last.get("new", 0)),
        "changed_last_scan": last.get("updated", last.get("changed", 0)),
        "missing_last_scan": last.get("missing", 0),
        "last_scan_time": last.get("completed_at"),
        "scan_status": last.get("status", "never run"),
        "scan_errors": last.get("errors", []) or [],
    }


def _unresolved_folders(conn, *, limit=200):
    """Unresolved folders (no canonical link, not deferred) with candidate people."""
    out = []
    for state in _folder_states(conn):
        if state["linked"] or state["deferred"]:
            continue
        out.append({
            "folder": state["folder"], "files": state["files"],
            "suggestions": td.suggest_people(conn, state["folder"]),
        })
        if len(out) >= limit:
            break
    return out


@router.get("/taxdome-drive", response_class=HTMLResponse)
def taxdome_dashboard(request: Request, q: str = "",
                      principal: Principal = Depends(current_principal)):
    with td._database().engine.connect() as conn:
        counts = _counts(conn)
        unresolved = _unresolved_folders(conn)
        results = td._search_documents(conn, q) if q.strip() else []
    return templates.TemplateResponse(
        request=request, name="demo/taxdome_drive.html",
        context={
            "counts": counts, "unresolved": unresolved, "q": q, "results": results,
            "scanned": request.query_params.get("scanned"),
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/taxdome-drive/scan")
def taxdome_scan(request: Request, principal: Principal = Depends(current_principal)):
    """Run the read-only TaxDome Drive indexer and record the run."""
    summary = td.scan(actor_user_id=principal.user_id)
    write_audit_event(
        action="taxdome.drive_scanned", entity_type="import_job", entity_id=summary.get("scan_id"),
        actor_user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None) or f"taxdome-{uuid.uuid4()}",
        metadata={k: summary.get(k) for k in
                  ("folders_examined", "copied", "updated", "skipped", "missing")},
    )
    return RedirectResponse(
        f"/demo/taxdome-drive?scanned={summary['copied']}-{summary['updated']}-{summary['missing']}",
        status_code=303)


@router.post("/taxdome-drive/folders/resolve")
def taxdome_resolve(request: Request, folder: str = Form(...), action: str = Form(...),
                    person_id: str = Form(""), principal: Principal = Depends(current_principal)):
    """Resolve one unresolved folder mapping using the existing canonical-person model:
    Link (to an existing person), Keep Separate (create a new canonical person for the folder),
    or Defer (leave for later). Never an automatic weak-name merge — this is a human decision."""
    db = td._database()
    d = db.documents
    folder_docs = and_(td.taxdome_filter(d), _folder_col() == folder)
    if action not in {"link", "keep_separate", "defer"}:
        return RedirectResponse("/demo/taxdome-drive", status_code=303)
    if action == "link" and not person_id.strip():
        return RedirectResponse("/demo/taxdome-drive", status_code=303)

    with db.engine.begin() as conn:
        if action == "link":
            resolution = {"action": "link", "person_id": int(person_id)}
            conn.execute(d.update().where(folder_docs).values(person_id=int(person_id)))
        elif action == "keep_separate":
            pid = conn.execute(
                db.people.insert().values(full_name=folder).returning(db.people.c.id)
            ).scalar_one()
            conn.execute(d.update().where(folder_docs).values(person_id=pid))
            resolution = {"action": "keep_separate", "person_id": pid}
        else:  # defer — flag the folder (read-modify-write on tags; no schema change)
            for row in conn.execute(select(d.c.id, d.c.tags).where(folder_docs)).mappings().all():
                tags = dict(row["tags"] or {})
                tags["review_deferred"] = True
                conn.execute(d.update().where(d.c.id == row["id"]).values(tags=tags))
            resolution = {"action": "defer"}

    write_audit_event(
        action="taxdome.folder_resolved", entity_type="taxdome_folder", entity_id=folder,
        actor_user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None) or f"taxdome-{uuid.uuid4()}",
        metadata=resolution)
    return RedirectResponse("/demo/taxdome-drive?saved=1", status_code=303)


# exposed for search reuse / tests
search_documents = td._search_documents
