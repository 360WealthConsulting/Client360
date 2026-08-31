from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

logger = logging.getLogger("client360.sharepoint.webhook")

router = APIRouter()

_LOCK = threading.Lock()
_RUNNING = False
_PENDING = False
_LAST_TRIGGER_AT = 0.0
_DEBOUNCE_SECONDS = 2.0


def _expected_client_state() -> str:
    return str(os.getenv("MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE") or "").strip()


def _run_delta_once() -> None:
    global _RUNNING, _PENDING, _LAST_TRIGGER_AT
    while True:
        with _LOCK:
            if _RUNNING:
                _PENDING = True
                return
            _RUNNING = True
            _PENDING = False
        try:
            delay = _DEBOUNCE_SECONDS - (time.monotonic() - _LAST_TRIGGER_AT)
            if delay > 0:
                time.sleep(delay)

            from app.services.microsoft_ingestion import run_sharepoint_delta_sync
            result = run_sharepoint_delta_sync(ocr=False)

            logger.info(
                "SharePoint webhook delta sync completed: status=%s changed=%s "
                "deleted=%s imported=%s checkpoints_advanced=%s",
                result.get("status"),
                result.get("changed"),
                result.get("deleted"),
                result.get("imported"),
                result.get("checkpoints_advanced"),
            )
            _LAST_TRIGGER_AT = time.monotonic()
        except Exception:
            logger.exception("SharePoint webhook-triggered delta sync failed")
        finally:
            with _LOCK:
                rerun = _PENDING
                _PENDING = False
                _RUNNING = False
        if not rerun:
            return


def trigger_delta_background() -> None:
    global _PENDING

    with _LOCK:
        if _RUNNING:
            _PENDING = True
            return

    threading.Thread(
        target=_run_delta_once,
        name="sharepoint-webhook-delta",
        daemon=True,
    ).start()

@router.post("/api/microsoft/sharepoint/webhook")
async def sharepoint_webhook(request: Request):
    validation_token = request.query_params.get("validationToken")
    if validation_token is not None:
        return PlainTextResponse(validation_token, media_type="text/plain")

    expected_state = _expected_client_state()
    if not expected_state:
        logger.error(
            "SharePoint webhook received notification but "
            "MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE is not configured"
        )
        return Response(status_code=503)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    notifications = payload.get("value")
    if not isinstance(notifications, list):
        return Response(status_code=400)

    accepted = 0
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        if str(notification.get("clientState") or "") != expected_state:
            logger.warning(
                "Rejected SharePoint webhook notification with invalid clientState"
            )
            continue
        accepted += 1

    if accepted == 0:
        return Response(status_code=403)

    trigger_delta_background()
    return Response(status_code=202)
