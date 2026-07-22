"""Job dispatch registry (Phase D.22) — job_type → EXISTING-service handler.

This is the orchestration seam: every job dispatches to an existing service and returns a result
summary. Automation **duplicates no business logic** — it invokes reporting/workflow/analytics/
communications/Microsoft 365 exactly as those domains define them (mirroring the workflow
``actions.py`` registry precedent). Handlers are deterministic and lazy-import their target service.
"""
from __future__ import annotations

from sqlalchemy import and_, select


class DispatchError(Exception):
    """Unknown job type or missing required config."""


def _run_report_schedule(context):
    from app.services.reporting import schedules as reporting_schedules
    sid = (context["config"] or {}).get("schedule_id")
    if sid is None:
        raise DispatchError("run_report_schedule requires config.schedule_id")
    r = reporting_schedules.run_schedule(context["principal"], int(sid),
                                         actor_user_id=context["actor_user_id"])
    return {"report_id": r.get("id"), "status": r.get("status")}


def _report_schedules_sweep(context):
    """The 'due schedules' sweeper Reporting reserved for a future phase (this one)."""
    from datetime import UTC, datetime

    from app.db import engine, report_schedules
    from app.services.reporting import schedules as reporting_schedules
    with engine.connect() as c:
        due = list(c.scalars(select(report_schedules.c.id).where(and_(
            report_schedules.c.active.is_(True), report_schedules.c.next_run_at.is_not(None),
            report_schedules.c.next_run_at <= datetime.now(UTC),
            report_schedules.c.frequency != "manual"))))
    swept = 0
    for sid in due:
        try:
            reporting_schedules.run_schedule(context["principal"], sid,
                                             actor_user_id=context["actor_user_id"])
            swept += 1
        except Exception:
            continue
    return {"due": len(due), "swept": swept}


def _capture_analytics_snapshots(context):
    from app.services.analytics import service as analytics_service
    out = analytics_service.capture_all(context["principal"], actor_user_id=context["actor_user_id"],
                                        period_key=(context["config"] or {}).get("period_key"))
    return {"captured": len(out.get("captured", [])), "skipped": len(out.get("skipped", []))}


def _launch_workflow(context):
    from app.services import workflow_automation as wf
    cfg = context["config"] or {}
    code = cfg.get("template_code")
    if not code:
        raise DispatchError("launch_workflow requires config.template_code")
    instance_id = wf.launch_workflow(code, actor_user_id=context["actor_user_id"],
                                     person_id=cfg.get("person_id"), household_id=cfg.get("household_id"),
                                     priority=cfg.get("priority", "normal"), context=cfg.get("context"),
                                     idempotency_key=cfg.get("idempotency_key"))
    return {"workflow_instance_id": instance_id}


def _workflow_sla_sweep(context):
    from app.services import workflow_automation as wf
    result = wf.evaluate_sla()
    return {"sla": result if isinstance(result, dict) else {"ok": True}}


def _dispatch_notifications(context):
    from app.services.notification_worker import run_dispatch_cycle
    metrics = run_dispatch_cycle(cycle_limit=(context["config"] or {}).get("limit"))
    return {"dispatched": getattr(metrics, "dispatched", None),
            "delivered": getattr(metrics, "delivered", None),
            "failed": getattr(metrics, "failed", None)}


def _dispatch_outbox(context):
    from app.platform.outbox import dispatch_pending
    return dispatch_pending() or {"dispatched": 0}


def _send_communication(context):
    from app.services.communications import service as comms
    cfg = context["config"] or {}
    cid = cfg.get("conversation_id")
    if cid is None or not cfg.get("body"):
        raise DispatchError("send_communication requires config.conversation_id and config.body")
    msg = comms.send_message(context["principal"], int(cid), body=cfg["body"],
                             subject=cfg.get("subject"), actor_user_id=context["actor_user_id"])
    return {"message_id": msg.get("id"), "status": msg.get("status")}


def _m365(sync_name):
    def handler(context):
        import app.jobs.microsoft_calendar_sync as cal
        import app.jobs.microsoft_document_sync as doc
        import app.jobs.microsoft_mail_sync as mail
        fn = {"m365_mail_sync": mail.sync_recent_mail,
              "m365_calendar_sync": cal.sync_calendar_events,
              "m365_document_sync": doc.sync_microsoft_documents}[sync_name]
        return {"sync": sync_name, "result": fn()}
    return handler


def _governance_quality_scan(context):
    """Run governance quality checks (Phase D.23) via the Governance service — never reimplements
    quality logic here; Governance owns the findings/decisions."""
    from app.services.governance import quality
    return quality.run_all_active_checks(context["principal"], run_type="automation",
                                         actor_user_id=context["actor_user_id"])


def _governance_stale_scan(context):
    from app.services.governance import quality
    return quality.run_stale_scan(context["principal"], actor_user_id=context["actor_user_id"])


def _governance_retention_review(context):
    from app.services.governance import retention
    return retention.review_due_retention(context["principal"], actor_user_id=context["actor_user_id"])


def _integration_sync(context):
    """Record due synchronization runs via the Integration domain (Phase D.24). Integration owns the
    sync metadata; the actual data movement is the existing importers/M365 jobs — no provider logic
    is duplicated here."""
    from app.services.integration import sync as integration_sync
    return integration_sync.run_due_syncs(context["principal"], actor_user_id=context["actor_user_id"])


def _security_review(context):
    """Run due security reviews via the Security domain (Phase D.25): flag secrets past their
    rotation date, certificates near/after expiry, and policies due for review. Security owns the
    metadata; this records findings/events only and performs no cryptographic operation."""
    from app.services.security import scans as security_scans
    return security_scans.run_due_reviews(context["principal"], actor_user_id=context["actor_user_id"])


def _observability_scan(context):
    """Run due observability scans via the Observability domain (Phase D.26): run enabled health &
    diagnostic checks, collect telemetry, capture a runtime snapshot, and evaluate alert rules.
    Observability owns the metadata; this records health/diagnostic/telemetry/alert metadata only and
    reuses the existing readiness/scheduler surfaces — it performs no external monitoring."""
    from app.services.observability import scans as observability_scans
    return observability_scans.run_due_scans(context["principal"], actor_user_id=context["actor_user_id"])


def _configuration_review(context):
    """Run due configuration reviews via the Configuration domain (Phase D.27): validate active
    configuration items against their runtime-setting references, flag environment-override drift, and
    review in-flight feature rollouts. Configuration owns the metadata; this records review metadata
    only and reads the existing runtime config — it changes no runtime configuration."""
    from app.services.configuration import scans as configuration_scans
    return configuration_scans.run_due_reviews(context["principal"], actor_user_id=context["actor_user_id"])


def _runtime_refresh(context):
    """Safely refresh the Runtime Configuration Engine (Phase D.28): invalidate the cache, rebuild the
    effective-configuration snapshot from the current D.27 metadata, and record the lifecycle events.
    The runtime engine only evaluates — it never edits configuration metadata. Failures are isolated
    and fall back to the last-known snapshot (never crashes the job)."""
    from app.services.runtime import engine as runtime_engine
    return runtime_engine.refresh(context["principal"], actor_user_id=context["actor_user_id"])


def _maintenance(context):
    """A deterministic no-op maintenance job (no side effects) — a safe scheduled heartbeat."""
    return {"maintenance": "ok"}


def _custom(context):
    """User-defined custom job — echoes its config; the platform performs no business logic here."""
    return {"custom": True, "config_keys": sorted((context["config"] or {}).keys())}


DISPATCH_REGISTRY = {
    "run_report_schedule": _run_report_schedule,
    "report_schedules_sweep": _report_schedules_sweep,
    "capture_analytics_snapshots": _capture_analytics_snapshots,
    "launch_workflow": _launch_workflow,
    "workflow_sla_sweep": _workflow_sla_sweep,
    "dispatch_notifications": _dispatch_notifications,
    "dispatch_outbox": _dispatch_outbox,
    "send_communication": _send_communication,
    "m365_mail_sync": _m365("m365_mail_sync"),
    "m365_calendar_sync": _m365("m365_calendar_sync"),
    "m365_document_sync": _m365("m365_document_sync"),
    "governance_quality_scan": _governance_quality_scan,
    "governance_stale_scan": _governance_stale_scan,
    "governance_retention_review": _governance_retention_review,
    "integration_sync": _integration_sync,
    "security_review": _security_review,
    "observability_scan": _observability_scan,
    "configuration_review": _configuration_review,
    "runtime_refresh": _runtime_refresh,
    "maintenance": _maintenance,
    "custom": _custom,
}


def execute_dispatch(job_type: str, *, config: dict, principal, actor_user_id) -> dict:
    """Deterministically dispatch a job to its existing-service handler. Raises on unknown type;
    the handler's own exceptions propagate to the caller (run_job) for retry/failure handling."""
    fn = DISPATCH_REGISTRY.get(job_type)
    if fn is None:
        raise DispatchError(f"unknown job_type {job_type!r}")
    return fn({"config": config or {}, "principal": principal, "actor_user_id": actor_user_id})


def list_job_types() -> list[str]:
    return sorted(DISPATCH_REGISTRY)
