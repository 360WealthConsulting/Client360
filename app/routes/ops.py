"""Operational readiness endpoint (Release 0.9.9 Phase 7).

`/health` (in dashboard.py) is the DB-independent *liveness* probe. `/readiness`
is the *readiness* probe: it verifies database connectivity, reports the current
vs. expected Alembic head (migration-drift detection), the background scheduler
state, and the Microsoft 365 sync-health summary. It returns HTTP 503 when the
service is not ready so orchestrators can gate traffic. It adds no business
behavior and requires no authentication (listed in the middleware public paths).
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.db import engine, microsoft_accounts

logger = logging.getLogger("client360.ops")
router = APIRouter()

_expected_head_cache = None


def _expected_head():
    """The single Alembic head the code expects (cached)."""
    global _expected_head_cache
    if _expected_head_cache is None:
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            cfg = Config()
            cfg.set_main_option("script_location", "migrations")
            heads = ScriptDirectory.from_config(cfg).get_heads()
            _expected_head_cache = heads[0] if len(heads) == 1 else "|".join(sorted(heads))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not resolve expected Alembic head: %s", exc)
            _expected_head_cache = ""
    return _expected_head_cache or None


def _sync_health(connection):
    row = connection.execute(
        select(
            microsoft_accounts.c.email,
            microsoft_accounts.c.last_sync_at,
            microsoft_accounts.c.last_sync_status,
            microsoft_accounts.c.token_cache_encrypted,
        ).order_by(microsoft_accounts.c.updated_at.desc()).limit(1)
    ).mappings().one_or_none()
    if row is None:
        return {"connected": False, "status": "no_account"}
    return {
        "connected": bool(row["token_cache_encrypted"]),
        "last_sync_status": row["last_sync_status"] or "unknown",
        "last_sync_at": row["last_sync_at"].isoformat() if row["last_sync_at"] else None,
    }


@router.get("/readiness")
def readiness():
    db_ok = False
    current_head = None
    sync = {"connected": False, "status": "unknown"}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            db_ok = True
            current_head = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            sync = _sync_health(connection)
    except Exception as exc:
        logger.warning("readiness database check failed: %s", exc)

    expected_head = _expected_head()
    migrations_in_sync = bool(current_head) and (expected_head is None or current_head == expected_head)

    try:
        from app.jobs.scheduler import scheduler_status
        scheduler = scheduler_status()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("scheduler status unavailable: %s", exc)
        scheduler = {"running": False, "job_count": 0, "jobs": []}

    ready = db_ok and migrations_in_sync
    body = {
        "status": "ready" if ready else "not_ready",
        "application": "Client360",
        "checks": {
            "database": "ok" if db_ok else "error",
            "migrations": {
                "current_head": current_head,
                "expected_head": expected_head,
                "in_sync": migrations_in_sync,
            },
            "scheduler": scheduler,
            "microsoft_sync": sync,
        },
    }
    return JSONResponse(body, status_code=200 if ready else 503)
