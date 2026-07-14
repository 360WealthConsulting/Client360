import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.microsoft_calendar_sync import sync_calendar_events
from app.jobs.microsoft_mail_sync import sync_recent_mail


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

    _scheduler.start()
    logger.info("Client360 background scheduler started.")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Client360 background scheduler stopped.")
