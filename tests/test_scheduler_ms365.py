"""Scheduler — Microsoft 365 sync jobs skip cleanly when no account is connected.

Without a connected M365 account the mail/calendar/document sync jobs have nothing to talk to and would
fail deep inside on token/account lookup, spamming a RuntimeError stack trace every interval. They should
instead skip and log the reason once at INFO. When an account IS connected, they run as before.
"""
import logging

from app.jobs import scheduler


def test_connected_check_returns_bool():
    assert isinstance(scheduler._microsoft365_connected(), bool)   # never raises


def test_mail_sync_skips_and_logs_once_when_not_connected(monkeypatch, caplog):
    scheduler._ms365_skip_logged.clear()
    monkeypatch.setattr(scheduler, "_microsoft365_connected", lambda: False)

    def _must_not_run(*a, **k):
        raise AssertionError("sync must not run without a Microsoft 365 account")
    monkeypatch.setattr(scheduler, "sync_recent_mail", _must_not_run)

    with caplog.at_level(logging.INFO):
        scheduler.run_microsoft_mail_sync()      # first call → skip + one INFO line
        scheduler.run_microsoft_mail_sync()      # second call → skip, no new log

    skip_logs = [r for r in caplog.records if "no Microsoft 365 account is connected" in r.getMessage()]
    assert len(skip_logs) == 1
    assert all(r.levelno == logging.INFO for r in skip_logs)       # INFO, not an exception/stack trace


def test_mail_sync_runs_when_connected(monkeypatch):
    scheduler._ms365_skip_logged.clear()
    monkeypatch.setattr(scheduler, "_microsoft365_connected", lambda: True)
    ran = {"n": 0}

    def _sync(top=50):
        ran["n"] += 1
        return {"ok": True}
    monkeypatch.setattr(scheduler, "sync_recent_mail", _sync)
    scheduler.run_microsoft_mail_sync()
    assert ran["n"] == 1


def test_calendar_and_document_sync_skip_without_account(monkeypatch):
    scheduler._ms365_skip_logged.clear()
    monkeypatch.setattr(scheduler, "_microsoft365_connected", lambda: False)

    def _must_not_run(*a, **k):
        raise AssertionError("sync must not run without a Microsoft 365 account")
    monkeypatch.setattr(scheduler, "sync_calendar_events", _must_not_run)
    monkeypatch.setattr(scheduler, "sync_microsoft_documents", _must_not_run)
    scheduler.run_microsoft_calendar_sync()      # must not raise
    scheduler.run_microsoft_document_sync()      # must not raise
