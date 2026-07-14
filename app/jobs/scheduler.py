import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.microsoft_calendar_sync import sync_calendar_events
from app.jobs.microsoft_mail_sync import sync_recent_mail
from app.jobs.microsoft_document_sync import sync_microsoft_documents
from app.services.workflow_automation import evaluate_sla
from app.services.tax_intake import process_reminders


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
