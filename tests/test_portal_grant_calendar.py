"""Portal access grants are measured in ONE calendar: UTC.

Grants are WRITTEN from ``datetime.now(timezone.utc).date()`` but were READ with the server's local
``date.today()``. In the evening window where UTC has already rolled over and local time has not, a
grant created moments earlier carried an ``effective_date`` one day in the future, so the account was
denied its own access until local midnight — a client invited at 8pm Eastern could not sign in.

Every test here pins the calendar explicitly. None of them depends on the machine's current clock.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import insert, select

from app.db import engine, households, people, portal_access_grants, portal_accounts
from app.portal import service as portal_service

# The rollover instant: 00:30 UTC on Aug 29 is still Aug 28 in America/New_York.
ROLLOVER_UTC = datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
UTC_DAY = date(2026, 8, 29)
LOCAL_DAY = date(2026, 8, 28)          # what date.today() would have returned on the server
MIDDAY_UTC = datetime(2026, 8, 29, 15, 0, tzinfo=UTC)   # ordinary daytime: both agree


@pytest.fixture
def at_instant(monkeypatch):
    """Pin BOTH calendars: module ``datetime.now(utc)`` and module ``date.today()`` (local)."""
    def _apply(utc_instant, local_day):
        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return utc_instant if tz else utc_instant.replace(tzinfo=None)

        class _FrozenDate(date):
            @classmethod
            def today(cls):
                return local_day

        monkeypatch.setattr(portal_service, "datetime", _FrozenDatetime)
        monkeypatch.setattr(portal_service, "date", _FrozenDate)
        return utc_instant
    return _apply


def _account_with_grant(effective_date, inactive_date=None):
    """A portal account plus one grant with EXPLICIT dates — no reliance on any default."""
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"GC {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Grant Cal {tag}",
                                              active=True).returning(people.c.id)).scalar_one()
        aid = c.execute(insert(portal_accounts).values(
            person_id=pid, email=f"gc-{tag}@e.test", normalized_email=f"gc-{tag}@e.test",
            display_name="Grant Cal", status="active",
        ).returning(portal_accounts.c.id)).scalar_one()
        c.execute(insert(portal_access_grants).values(
            portal_account_id=aid, household_id=hid, person_id=pid, access_type="self",
            permissions={"documents": True, "messages": True},
            effective_date=effective_date, inactive_date=inactive_date))
    return aid, pid


def _active_grant_count(account_id):
    with engine.connect() as c:
        return len(c.execute(select(portal_access_grants).where(
            portal_access_grants.c.portal_account_id == account_id,
            portal_service._active_grant())).mappings().all())


# --- the calendar basis itself -------------------------------------------------------------------

def test_grant_today_is_the_utc_date_not_the_local_date(at_instant):
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    assert portal_service.grant_today() == UTC_DAY
    assert portal_service.grant_today() != LOCAL_DAY, \
        "reading grants in local time is what made evening invitations fail"


def test_write_and_read_bases_agree_at_the_rollover(at_instant):
    """The write path and the read path must derive the same calendar day."""
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    written = portal_service.datetime.now(UTC).date()   # what invite_portal_account uses
    assert written == portal_service.grant_today() == UTC_DAY


# --- behaviour across the boundary -----------------------------------------------------------------

def test_a_grant_created_during_the_rollover_is_active_immediately(at_instant):
    """local Aug 28 / UTC Aug 29: the grant is stamped Aug 29 and must be live at once."""
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY)
    assert _active_grant_count(account_id) == 1, \
        "a grant created in the evening rollover window must not be inactive until local midnight"


def test_an_existing_grant_stays_active_across_the_boundary(at_instant):
    """Granted the previous day; crossing the UTC/local boundary must not deactivate it."""
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=30))
    assert _active_grant_count(account_id) == 1


def test_a_future_effective_grant_is_still_inactive(at_instant):
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY + timedelta(days=1))
    assert _active_grant_count(account_id) == 0, "the fix must not make future grants live"


def test_an_expired_grant_remains_expired(at_instant):
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=30),
                                        inactive_date=UTC_DAY - timedelta(days=1))
    assert _active_grant_count(account_id) == 0


def test_a_grant_inactive_from_today_is_still_active_today(at_instant):
    """``inactive_date >= today`` is inclusive — the boundary day itself still counts."""
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=5),
                                        inactive_date=UTC_DAY)
    assert _active_grant_count(account_id) == 1


# --- ordinary daytime, where the two calendars agree ------------------------------------------------

def test_normal_daytime_behaviour_is_unchanged(at_instant):
    at_instant(MIDDAY_UTC, UTC_DAY)                     # both calendars agree
    active, _ = _account_with_grant(effective_date=UTC_DAY)
    past, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=10))
    future, _ = _account_with_grant(effective_date=UTC_DAY + timedelta(days=3))
    expired, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=10),
                                     inactive_date=UTC_DAY - timedelta(days=2))
    assert _active_grant_count(active) == 1
    assert _active_grant_count(past) == 1
    assert _active_grant_count(future) == 0
    assert _active_grant_count(expired) == 0


# --- revocation is unaffected ------------------------------------------------------------------------

def test_revocation_stamps_the_same_utc_day_and_is_otherwise_unchanged(at_instant):
    """Revocation writes ``inactive_date`` from the SAME UTC calendar the filter reads.

    Note the pre-existing (unchanged) semantics: the filter is ``inactive_date >= today``, so a grant
    revoked today stays nominally active for the remainder of that day. That has no access
    consequence — revocation also kills the sessions, invitations and one-time codes in the same
    transaction — and this fix deliberately does not alter it.
    """
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=5))
    assert _active_grant_count(account_id) == 1
    with engine.begin() as c:
        portal_service.revoke_account_access(c, account_id)
    with engine.connect() as c:
        stamped = c.execute(select(portal_access_grants.c.inactive_date).where(
            portal_access_grants.c.portal_account_id == account_id)).scalar_one()
    assert stamped == UTC_DAY, "revocation must stamp the UTC day, not the local one"
    assert _active_grant_count(account_id) == 1        # unchanged inclusive-boundary behaviour


def test_a_grant_revoked_yesterday_is_inactive_today(at_instant):
    at_instant(ROLLOVER_UTC, LOCAL_DAY)
    account_id, _ = _account_with_grant(effective_date=UTC_DAY - timedelta(days=5),
                                        inactive_date=UTC_DAY - timedelta(days=1))
    assert _active_grant_count(account_id) == 0
