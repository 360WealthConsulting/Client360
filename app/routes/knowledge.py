"""Enterprise Knowledge Graph routes (Phase D.45).

A governed COMPOSITION surface over the authoritative entities + the existing relationship engine — no graph
database, no second relationship engine. Reads only. The person/household graph, traversal, explanation, and
search all enforce record scope via the composition (out-of-scope → the service returns None → 404); reads
are gated by ``client.read`` and diagnostics by ``observability.audit``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.knowledge import explain_relationship, knowledge_graph, search_entities, traverse
from app.services.knowledge.diagnostics import knowledge_diagnostics
from app.services.knowledge.metrics import knowledge_metrics

router = APIRouter(tags=["knowledge"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_home(request: Request, person_id: int | None = None, household_id: int | None = None,
                   principal: Principal = Depends(require_capability("client.read"))):
    """The explainable knowledge graph for a person or household (HTML)."""
    if person_id is None and household_id is None:
        return templates.TemplateResponse(request=request, name="knowledge/home.html",
                                          context={"graph": None, "anchor": None})
    graph = knowledge_graph(principal, person_id=person_id, household_id=household_id)
    if graph is None:
        raise HTTPException(404, "Not found")
    anchor = f"person:{person_id}" if person_id else f"household:{household_id}"
    return templates.TemplateResponse(request=request, name="knowledge/home.html",
                                      context={"graph": graph, "anchor": anchor})


@router.get("/api/v1/knowledge/graph")
def api_knowledge_graph(person_id: int | None = None, household_id: int | None = None,
                        principal: Principal = Depends(require_capability("client.read"))):
    """The bounded, explainable knowledge graph (JSON). 404 when out of scope."""
    graph = knowledge_graph(principal, person_id=person_id, household_id=household_id)
    if graph is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(graph)


@router.get("/api/v1/knowledge/traverse")
def api_knowledge_traverse(person_id: int | None = None, household_id: int | None = None,
                           target_type: str | None = None, depth: int = 1,
                           principal: Principal = Depends(require_capability("client.read"))):
    """Bounded, cycle-safe traversal producing explainable paths (JSON). 404 when out of scope."""
    result = traverse(principal, person_id=person_id, household_id=household_id,
                      target_type=target_type, depth=depth)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/knowledge/explain")
def api_knowledge_explain(person_id: int | None = None, household_id: int | None = None,
                          target_id: str | None = None, relationship: str | None = None,
                          principal: Principal = Depends(require_capability("client.read"))):
    """Explain one relationship edge (JSON) — why/owner/evidence/deep-link/updated/inferred. 404 out of scope."""
    result = explain_relationship(principal, person_id=person_id, household_id=household_id,
                                  target_id=target_id, relationship=relationship)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/knowledge/search")
def api_knowledge_search(person_id: int | None = None, household_id: int | None = None,
                         q: str | None = None, entity_type: str | None = None,
                         relationship: str | None = None, owner: str | None = None,
                         visibility: str | None = None,
                         principal: Principal = Depends(require_capability("client.read"))):
    """Semantic search over the scoped, registered entity nodes (JSON). Never searches hidden entities."""
    result = search_entities(principal, person_id=person_id, household_id=household_id, query=q,
                             entity_type=entity_type, relationship=relationship, owner=owner,
                             visibility=visibility)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/knowledge/metrics")
def api_knowledge_metrics(principal: Principal = Depends(require_capability("client.read"))):
    """Low-cardinality knowledge metrics (JSON)."""
    return JSONResponse(knowledge_metrics(principal))


@router.get("/knowledge/diagnostics")
def knowledge_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only knowledge diagnostics (registry coverage, adapter availability, governance)."""
    return JSONResponse(knowledge_diagnostics())
