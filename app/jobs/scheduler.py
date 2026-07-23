import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.microsoft_calendar_sync import sync_calendar_events
from app.jobs.microsoft_document_sync import sync_microsoft_documents
from app.jobs.microsoft_mail_sync import sync_recent_mail
from app.services.tax_intake import process_reminders
from app.services.workflow_automation import evaluate_sla

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="America/New_York")


def run_microsoft_mail_sync() -> None:
    try:
        result = sync_recent_mail(top=50)
        logger.info("Microsoft mail sync result: %s", result)
    except Exception:
        logger.exception("Microsoft mail sync failed.")


def run_microsoft_calendar_sync() -> None:
    try:
        result = sync_calendar_events()
        logger.info("Microsoft calendar sync result: %s", result)
    except Exception:
        logger.exception("Microsoft calendar sync failed.")


def run_microsoft_document_sync() -> None:
    try:
        result = sync_microsoft_documents()
        logger.info("Microsoft document sync result: %s", result)
    except Exception:
        logger.exception("Microsoft document sync failed.")

def run_workflow_sla_automation() -> None:
    try:
        logger.info("Workflow SLA escalation result: %s", evaluate_sla())
    except Exception:
        logger.exception("Workflow SLA automation failed.")

def run_tax_intake_reminders() -> None:
    try:
        logger.info("Tax intake reminder result: %s", process_reminders())
    except Exception:
        logger.exception("Tax intake reminders failed.")

def run_exception_sla_sweep() -> None:
    try:
        from app.services.exception_sla import sweep_exception_slas
        logger.info("Exception SLA sweep result: %s", sweep_exception_slas())
    except Exception:
        logger.exception("Exception SLA sweep failed.")


def run_benefits_detector_scan() -> None:
    """Idempotent benefits detector scan so Work Management reflects current conditions.
    Honest result (scanned orgs, opened/resolved/reopened/skipped, failures); no Phase-5
    escalation notifications. Overlap is prevented by APScheduler (max_instances=1, coalesce)."""
    try:
        from app.services.benefits_detectors import run_benefits_scan
        from app.services.benefits_notifications import record_scan_health
        from app.services.benefits_work import auto_assign_unassigned
        result = run_benefits_scan()
        result["auto_assignment"] = auto_assign_unassigned()
        result["scan_health"] = record_scan_health(result)
        logger.info("Benefits detector scan result: %s", result)
    except Exception:
        logger.exception("Benefits detector scan failed.")


def run_insurance_detector_scan() -> None:
    """Idempotent insurance scan (reviews + licensing/CE + commissions) through the shared
    Exception Engine, then auto-assign new exceptions via the existing assignment rules so the
    insurance work queues reflect current conditions. Honest result (orgs scanned, opened/
    resolved/reopened/skipped, failures). Overlap is prevented by APScheduler
    (max_instances=1, coalesce). No insurance-specific engine, queue, or scheduler."""
    try:
        from app.services.insurance_detectors import run_insurance_scan
        from app.services.insurance_work import auto_assign_unassigned
        result = run_insurance_scan()
        result["auto_assignment"] = auto_assign_unassigned()
        logger.info("Insurance detector scan result: %s", result)
    except Exception:
        logger.exception("Insurance detector scan failed.")


def run_outbox_dispatch() -> None:
    """Deliver due transactional-outbox events (E1.6 / F1.3). No-op when empty."""
    try:
        from app.platform.outbox import dispatch_pending
        result = dispatch_pending()
        if any(result.values()):
            logger.info("Outbox dispatch result: %s", result)
    except Exception:
        logger.exception("Outbox dispatch failed.")


def run_automation_tick() -> None:
    """Drive one Automation runner tick (D.22): sweep due schedules, drain runnable runs. No-op
    when idle. Failure-isolated — a job crash never propagates."""
    try:
        from app.services.automation.runner import run_worker_cycle
        result = run_worker_cycle()
        if result.get("enqueued") or result.get("executed"):
            logger.info("Automation tick result: %s", result)
    except Exception:
        logger.exception("Automation tick failed.")


def run_orchestration_tick() -> None:
    """Drive one Workflow Orchestration housekeeping tick (D.33): scan non-terminal orchestration
    instances. No-op when idle. Failure-isolated — a job crash never propagates."""
    try:
        from app.services.orchestration import execution as orchestration
        result = orchestration.tick()
        if result.get("advanced"):
            logger.info("Orchestration tick result: %s", result)
    except Exception:
        logger.exception("Orchestration tick failed.")


def run_projection_tick() -> None:
    """Drive one projection incremental tick (D.36): apply new outbox events to the read models. No-op
    when idle. Failure-isolated — projection failures never propagate to business transactions."""
    try:
        from app.services.projections import engine as projections
        result = projections.tick()
        if result.get("processed"):
            logger.info("Projection tick result: %s", result)
    except Exception:
        logger.exception("Projection tick failed.")


def run_runtime_refresh() -> None:
    """Safely refresh the Runtime Configuration Engine (D.28): invalidate the cache and rebuild the
    effective-configuration snapshot from the current D.27 metadata. Failure-isolated — a refresh
    crash never propagates and the engine keeps serving the last-known snapshot."""
    try:
        from app.services.runtime import engine as runtime_engine
        result = runtime_engine.refresh(trigger="scheduled")
        if result.get("refreshed"):
            logger.info("Runtime refresh: snapshot v%s", result.get("snapshot_version"))
    except Exception:
        logger.exception("Runtime refresh failed.")


def run_runtime_heartbeat() -> None:
    """Emit this worker's runtime coordination heartbeat and converge onto the current runtime
    generation if behind (D.29). Failure-isolated — a coordination crash never propagates."""
    try:
        from app.services.runtime import coordination
        coordination.heartbeat()
    except Exception:
        logger.exception("Runtime heartbeat failed.")


def run_runtime_stale_cleanup() -> None:
    """Expire stale runtime workers and recompute cluster convergence (D.29). Failure-isolated."""
    try:
        from app.services.runtime import cluster
        result = cluster.coordination_sweep()
        if result.get("expired"):
            logger.info("Runtime stale-worker cleanup expired %s workers", result.get("expired"))
    except Exception:
        logger.exception("Runtime stale-worker cleanup failed.")


def start_scheduler() -> None:
    if _scheduler.running:
        return

    _scheduler.add_job(
        run_microsoft_mail_sync,
        trigger="interval",
        minutes=15,
        id="microsoft-mail-sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        run_microsoft_calendar_sync,
        trigger="interval",
        minutes=15,
        id="microsoft-calendar-sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        run_microsoft_document_sync,
        trigger="interval",
        minutes=30,
        id="microsoft-document-sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        run_workflow_sla_automation, trigger="interval", minutes=5,
        id="workflow-sla-automation", replace_existing=True, max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        run_tax_intake_reminders, trigger="cron", hour=9, minute=0,
        id="tax-intake-reminders", replace_existing=True, max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        run_exception_sla_sweep, trigger="interval", minutes=5,
        id="exception-sla-sweep", replace_existing=True, max_instances=1, coalesce=True,
    )
    from app.config import benefits_scan_interval_minutes
    _scheduler.add_job(
        run_benefits_detector_scan, trigger="interval", minutes=benefits_scan_interval_minutes(),
        id="benefits-detector-scan", replace_existing=True, max_instances=1, coalesce=True,
    )
    from app.config import insurance_scan_interval_minutes
    _scheduler.add_job(
        run_insurance_detector_scan, trigger="interval", minutes=insurance_scan_interval_minutes(),
        id="insurance-detector-scan", replace_existing=True, max_instances=1, coalesce=True,
    )

    # Transactional-outbox dispatcher (E1.6 / F1.3): scheduled only when explicitly
    # enabled, so default runtime behavior is unchanged. Workflow automation consumers
    # (F4.4) are registered in the same gated block (dark launch), so no subscribers
    # exist until the dispatcher is enabled.
    from app.config import outbox_dispatch_interval_seconds, outbox_dispatcher_enabled
    if outbox_dispatcher_enabled():
        from app.services.workflow_automation_consumers import register_workflow_consumers
        register_workflow_consumers()
        from app.services.notification_intents import register_notification_consumers
        register_notification_consumers()
        # (D.29) Distributed runtime coordination consumers — dark-launched here so cross-process
        # cache invalidation flows through the transactional outbox only when the dispatcher is on.
        from app.services.runtime.events import register_runtime_consumers
        register_runtime_consumers()
        # (D.34) Domain-event model consumers — dark-launched here so the orchestration.lifecycle sink
        # subscribes only when the dispatcher is on (behavior unchanged by default).
        from app.services.events.subscriptions import register_event_consumers
        register_event_consumers()
        _scheduler.add_job(
            run_outbox_dispatch, trigger="interval", seconds=outbox_dispatch_interval_seconds(),
            id="outbox-dispatch", replace_existing=True, max_instances=1, coalesce=True,
        )

    # (D.22) Automation runner tick — gated OFF by default. When enabled, it sweeps due automation
    # schedules and drains runnable runs (single-instance, no distributed lock, no new threads).
    from app.config import automation_enabled, automation_tick_interval_seconds
    if automation_enabled():
        _scheduler.add_job(
            run_automation_tick, trigger="interval", seconds=automation_tick_interval_seconds(),
            id="automation-tick", replace_existing=True, max_instances=1, coalesce=True,
        )

    # (D.33) Workflow Orchestration housekeeping tick — gated OFF by default. When enabled, it scans
    # non-terminal orchestration instances. The scheduler infrastructure is unchanged; this only
    # launches orchestration (never new threads, single-instance).
    from app.config import orchestration_enabled, orchestration_tick_interval_seconds
    if orchestration_enabled():
        _scheduler.add_job(
            run_orchestration_tick, trigger="interval", seconds=orchestration_tick_interval_seconds(),
            id="orchestration-tick", replace_existing=True, max_instances=1, coalesce=True,
        )

    # (D.36) Projection incremental tick — gated OFF by default. When enabled, it applies new outbox
    # events to the disposable read models. Read models are rebuildable from events; nothing depends on
    # them until a read surface adopts one, so runtime behavior is unchanged by default.
    from app.config import projections_enabled, projections_tick_interval_seconds
    if projections_enabled():
        _scheduler.add_job(
            run_projection_tick, trigger="interval", seconds=projections_tick_interval_seconds(),
            id="projection-tick", replace_existing=True, max_instances=1, coalesce=True,
        )

    # (D.28) Runtime Configuration Engine periodic safe-refresh — gated OFF by default. When enabled,
    # it rebuilds the effective-config snapshot on a cadence; a manual refresh is always available.
    from app.config import runtime_refresh_enabled, runtime_refresh_interval_seconds
    if runtime_refresh_enabled():
        _scheduler.add_job(
            run_runtime_refresh, trigger="interval", seconds=runtime_refresh_interval_seconds(),
            id="runtime-refresh", replace_existing=True, max_instances=1, coalesce=True,
        )

    # (D.29) Distributed runtime coordination — gated OFF by default. When enabled, each worker
    # heartbeats + converges on a cadence and a stale-worker sweep expires inactive workers.
    from app.config import (
        runtime_coordination_enabled,
        runtime_heartbeat_interval_seconds,
        runtime_worker_ttl_seconds,
    )
    if runtime_coordination_enabled():
        _scheduler.add_job(
            run_runtime_heartbeat, trigger="interval", seconds=runtime_heartbeat_interval_seconds(),
            id="runtime-heartbeat", replace_existing=True, max_instances=1, coalesce=True,
        )
        _scheduler.add_job(
            run_runtime_stale_cleanup, trigger="interval", seconds=max(30, runtime_worker_ttl_seconds()),
            id="runtime-stale-cleanup", replace_existing=True, max_instances=1, coalesce=True,
        )

    _scheduler.start()
    logger.info("Client360 background scheduler started.")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Client360 background scheduler stopped.")


def scheduler_status() -> dict:
    """Readiness/observability snapshot of the in-process background scheduler."""
    try:
        jobs = [{"id": job.id, "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None}
                for job in _scheduler.get_jobs()]
    except Exception:
        jobs = []
    return {"running": _scheduler.running, "job_count": len(jobs), "jobs": jobs}
