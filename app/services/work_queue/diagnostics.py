"""Unified Work Queue diagnostics (Phase D.39) — read-only operational telemetry.

Reports queue composition, per-adapter latency/errors, capability suppression, projection-fallback
usage (from the D.37 adoption layer), saved-view usage, action success/failure counts, and page query
latency. Never mutates.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import dispatch
from .adapters import DOMAIN_CAPABILITY, SOURCE_DOMAINS
from .service import compose_queue


def work_queue_diagnostics(principal, *, now=None) -> dict:
    now = now or datetime.now(UTC)
    t0 = time.perf_counter()
    q = compose_queue(principal, page=1, page_size=25, now=now)
    page_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Projection fallback usage from the D.37 adoption layer (queue counts reuse those sources).
    try:
        from app.services.projections.adoption import usage_stats
        projection_usage = usage_stats()
    except Exception:
        projection_usage = {}

    try:
        from sqlalchemy import func, select

        from app.db import engine, work_queue_saved_views
        with engine.connect() as c:
            saved_view_count = int(c.scalar(select(func.count()).select_from(work_queue_saved_views)) or 0)
    except Exception:
        saved_view_count = None

    return {
        "generated_at": now.isoformat(),
        "total_visible": q["total"],
        "candidate_total": q["candidate_total"],
        "by_domain": q["counts"]["by_domain"],
        "by_status": q["counts"]["by_status"],
        "by_sla": q["counts"]["by_sla"],
        "overdue": q["counts"]["overdue"],
        "sla_breaches": q["counts"]["breached"],
        "unassigned": q["counts"]["unassigned"],
        "suppressed_by_capability": q["suppressed_capability"],
        "adapters": q["adapter_stats"],
        "adapter_errors": {d: s for d, s in q["adapter_stats"].items() if s.get("error")},
        "source_domains": list(SOURCE_DOMAINS),
        "domain_capability": DOMAIN_CAPABILITY,
        "projection_usage": projection_usage,
        "saved_view_count": saved_view_count,
        "action_stats": dispatch.action_stats(),
        "page_query_ms": page_ms,
    }
