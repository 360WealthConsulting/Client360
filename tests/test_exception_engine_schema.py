"""Release 0.9.10 / Sprint 5.5 — Exception Engine schema (Phase 1) regression tests.

Covers the platform-wide tables, the `domain` CHECK, the append-only event ledger,
the partial-unique dedupe index, and the seeded tax types / capabilities / queues.
No service layer exists yet (later phases); these assert the schema foundation.
"""
import pytest
from sqlalchemy import select, text

from app.db import engine, exceptions, exception_events, exception_types


def _filing_type_id():
    with engine.connect() as c:
        return c.scalar(select(exception_types.c.id).where(exception_types.c.code == "FILING_REJECTED"))


# --- seed ---------------------------------------------------------------------

def test_tax_exception_types_seeded():
    # Sprint 5.5 seeds exactly 24 tax types (other domains are not seeded by the
    # migration; a shared test DB may contain test-inserted rows, so assert on tax).
    with engine.connect() as c:
        tax = c.scalar(select(text("count(*)")).select_from(exception_types).where(exception_types.c.domain == "tax"))
    assert tax == 24


def test_capability_family_seeded_and_granted():
    with engine.connect() as c:
        caps = set(c.execute(text("SELECT code FROM capabilities WHERE code LIKE 'exception.%'")).scalars())
        assert caps == {"exception.read", "exception.write", "exception.resolve", "exception.compliance"}
        # least-privilege grants
        read_roles = set(c.execute(text(
            "SELECT r.code FROM roles r JOIN role_capabilities rc ON rc.role_id=r.id "
            "JOIN capabilities cp ON cp.id=rc.capability_id WHERE cp.code='exception.read'")).scalars())
        assert {"administrator", "advisor", "operations", "compliance"} <= read_roles
        resolve_roles = set(c.execute(text(
            "SELECT r.code FROM roles r JOIN role_capabilities rc ON rc.role_id=r.id "
            "JOIN capabilities cp ON cp.id=rc.capability_id WHERE cp.code='exception.resolve'")).scalars())
        assert resolve_roles == {"administrator"}  # sensitive: admin only at baseline


def test_exception_work_queues_seeded():
    with engine.connect() as c:
        codes = set(c.execute(text("SELECT code FROM work_queues WHERE code LIKE '%exception%'")).scalars())
    assert {"exceptions", "exceptions_critical", "compliance_exceptions"} <= codes


# --- constraints --------------------------------------------------------------

def test_domain_check_rejects_unknown_domain():
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO exception_types(domain,code,category,name,default_severity,sla_minutes) "
                "VALUES ('bogus','ZZZ','client','Z','low',10)"))


def test_status_check_rejects_unknown_status():
    tid = _filing_type_id()
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(exceptions.insert().values(
                exception_type_id=tid, domain="tax", category="filing", severity="blocker",
                status="not_a_status", title="x"))


# --- append-only ledger -------------------------------------------------------

def test_exception_events_are_append_only():
    tid = _filing_type_id()
    with engine.begin() as c:
        eid = c.execute(exceptions.insert().values(
            exception_type_id=tid, domain="tax", category="filing", severity="blocker",
            title="append-only test").returning(exceptions.c.id)).scalar_one()
        ev = c.execute(exception_events.insert().values(
            exception_id=eid, event_type="opened").returning(exception_events.c.id)).scalar_one()
    with pytest.raises(Exception) as update_exc:
        with engine.begin() as c:
            c.execute(exception_events.update().where(exception_events.c.id == ev).values(event_type="x"))
    assert "append-only" in str(update_exc.value)
    with pytest.raises(Exception) as delete_exc:
        with engine.begin() as c:
            c.execute(exception_events.delete().where(exception_events.c.id == ev))
    assert "append-only" in str(delete_exc.value)


# --- dedupe partial-unique index ---------------------------------------------

def test_dedupe_key_prevents_duplicate_open_but_allows_after_resolution():
    import uuid
    key = f"dedupe-{uuid.uuid4().hex[:12]}"
    tid = _filing_type_id()
    with engine.begin() as c:
        first = c.execute(exceptions.insert().values(
            exception_type_id=tid, domain="tax", category="filing", severity="blocker",
            title="dedupe #1", dedupe_key=key).returning(exceptions.c.id)).scalar_one()
    # second OPEN with same dedupe_key → rejected by the partial-unique index
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(exceptions.insert().values(
                exception_type_id=tid, domain="tax", category="filing", severity="blocker",
                title="dedupe #2", dedupe_key=key))
    # resolve the first, then the same key may open again (condition recurred)
    with engine.begin() as c:
        c.execute(exceptions.update().where(exceptions.c.id == first).values(status="resolved"))
    with engine.begin() as c:
        c.execute(exceptions.insert().values(
            exception_type_id=tid, domain="tax", category="filing", severity="blocker",
            title="dedupe #3", dedupe_key=key))
