"""Read-only operations surface for the continuous document pipeline.

Three GET endpoints under ``/api/document-pipeline``, each gated on the EXISTING ``documents.view``
capability — no new capability is seeded, so the RBAC model is unchanged:

* ``/metrics``  — backlog, running, completed, linked, review, blocked, failed, throughput,
                  last heartbeat, and the supporting breakdowns.
* ``/health``   — healthy / idle / stopped / stalled, with the reason. Returns 503 when unhealthy so
                  an external monitor can watch it the same way it watches ``/readiness``.
* ``/blockers`` — the visible blocker queue, and (``?queue=review``) the ownership review queue.

Nothing here starts, stops or changes the pipeline. Control is deliberately not an HTTP surface: the
service is started and stopped by the machine's service manager, and re-queueing a document is an
operator action through the CLI, where it is attributable to a person on that host.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.security.dependencies import require_capability
from app.security.models import Principal

router = APIRouter(prefix="/api/document-pipeline", tags=["document-pipeline"])


@router.get("/metrics")
def pipeline_metrics(_principal: Principal = Depends(require_capability("documents.view"))):
    """The operational snapshot. Safe to poll — it is a handful of grouped counts over one table."""
    from app.services.document_pipeline_continuous import service as pipeline
    return JSONResponse(pipeline.status())


@router.get("/health")
def pipeline_health(_principal: Principal = Depends(require_capability("documents.view"))):
    """503 when the pipeline is not progressing, 200 when it is healthy or correctly idle.

    An idle pipeline with an empty queue is HEALTHY, not degraded — reporting a finished backlog as a
    fault is how monitoring gets muted."""
    from app.services.document_pipeline_continuous import service as pipeline
    status = pipeline.health()
    return JSONResponse(status, status_code=200 if status.get("healthy") else 503)


@router.get("/blockers")
def pipeline_blockers(
    queue: str = Query("blocked", pattern="^(blocked|review)$"),
    lane: str | None = Query(None, pattern="^(drake|taxdome|sharepoint)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _principal: Principal = Depends(require_capability("documents.view")),
):
    """Permanent failures (``queue=blocked``) or ambiguous ownership (``queue=review``)."""
    from app.services.document_pipeline_continuous import service as pipeline
    if queue == "review":
        return JSONResponse({"queue": "review",
                             "rows": pipeline.reviews(lane=lane, limit=limit, offset=offset)})
    return JSONResponse({"queue": "blocked",
                         "rows": pipeline.blockers(limit=limit, offset=offset)})
