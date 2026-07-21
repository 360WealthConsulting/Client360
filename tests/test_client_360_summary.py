"""Client 360 Summary tests (Phase D.2).

The summary is a factual, read-only per-domain snapshot composed from existing
person-keyed services. These tests verify composition, that values are never
summed into a composite figure, that the reads are strictly person-keyed (no
cross-client leak), and that the person Overview renders the section.
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, insert, select
from starlette.requests import Request

from app.db import (
    engine,
    exception_types,
    exceptions,
    insurance_policies,
    people,
    tax_engagements,
    tax_firms,
    tax_offices,
    tax_years,
)
from app.security.models import Principal
from app.services.advisor_workspace import get_client_snapshot


def _tax_type_id(conn):
    return conn.execute(exception_types.select().where(exception_types.c.domain == "tax")).mappings().first()["id"]


def _make_person(conn, tag, *, policies=0, face=Decimal("0"), open_exceptions=0, tax_active=False):
    pid = conn.execute(people.insert().values(
        full_name=f"C {tag}", primary_email=f"{tag}@e.test",
        normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
    for _ in range(policies):
        conn.execute(insert(insurance_policies).values(person_id=pid, status="in_force", face_amount=face))
    for i in range(open_exceptions):
        conn.execute(insert(exceptions).values(
            exception_type_id=_tax_type_id(conn), domain="tax", category="client",
            severity="high", status="open", title=f"E {tag}{i}", source="system",
            opened_at=datetime.now(UTC), escalation_level=0, notification_count=0, person_id=pid))
    if tax_active:
        firm = conn.scalar(select(tax_firms.c.id).limit(1))
        office = conn.scalar(select(tax_offices.c.id).limit(1))
        year = conn.scalar(select(tax_years.c.id).limit(1))
        if firm and office and year:
            conn.execute(insert(tax_engagements).values(
                tax_firm_id=firm, tax_office_id=office, tax_year_id=year,
                engagement_type="1040", status="active", opened_on=date.today(),
                metadata={}, person_id=pid))
            return pid, True
    return pid, False


def _cleanup(pid):
    with engine.begin() as conn:
        conn.execute(delete(exceptions).where(exceptions.c.person_id == pid))
        conn.execute(delete(insurance_policies).where(insurance_policies.c.person_id == pid))
        conn.execute(delete(tax_engagements).where(tax_engagements.c.person_id == pid))
        conn.execute(delete(people).where(people.c.id == pid))


def test_snapshot_composes_per_domain_and_never_sums():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        pid, tax_seeded = _make_person(conn, f"A{tag}", policies=2, face=Decimal("250000"),
                                       open_exceptions=1, tax_active=True)
    try:
        snap = get_client_snapshot(pid, None, portfolio={
            "aum": Decimal("600000"), "cash": Decimal("30000"), "cash_percent": Decimal("5"),
            "household": {"aum": Decimal("800000")},
        }, open_task_count=3)
        # Wealth reused from the passed portfolio.
        assert snap["aum"] == Decimal("600000")
        assert snap["household_aum"] == Decimal("800000")
        assert snap["cash"] == Decimal("30000")
        # Insurance / tax / attention / agenda composed.
        assert snap["insurance"]["policy_count"] == 2
        assert snap["insurance"]["total_face"] == Decimal("500000")
        assert snap["tax"]["active"] == (1 if tax_seeded else 0)
        assert snap["open_exceptions"] == 1
        assert snap["open_tasks"] == 3
        # NEVER summed into a composite figure — per-domain values stay distinct.
        for banned in ("relationship_value", "total_value", "combined", "composite", "net_worth"):
            assert banned not in snap
        assert isinstance(snap["insurance"], dict) and isinstance(snap["tax"], dict)
    finally:
        _cleanup(pid)


def test_snapshot_is_person_keyed_no_cross_client_leak():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        a, _ = _make_person(conn, f"A{tag}", policies=1, face=Decimal("100000"), open_exceptions=2)
        other, _ = _make_person(conn, f"C{tag}", policies=3, face=Decimal("999999"), open_exceptions=5)
    try:
        snap = get_client_snapshot(a, None, portfolio={}, open_task_count=0)
        # Only client A's data — never the other client's.
        assert snap["insurance"]["policy_count"] == 1
        assert snap["insurance"]["total_face"] == Decimal("100000")
        assert snap["open_exceptions"] == 2
    finally:
        _cleanup(a)
        _cleanup(other)


def test_empty_client_snapshot_is_zeroed():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        pid, _ = _make_person(conn, f"Z{tag}")
    try:
        snap = get_client_snapshot(pid, None, portfolio={}, open_task_count=0)
        assert snap["insurance"] == {"policy_count": 0, "total_face": 0}
        assert snap["tax"]["active"] == 0
        assert snap["open_exceptions"] == 0
        assert snap["open_tasks"] == 0
    finally:
        _cleanup(pid)


def test_person_overview_renders_client_360_section():
    from app.routes.people import person_profile
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        pid, _ = _make_person(conn, f"A{tag}", policies=1, face=Decimal("50000"))
    try:
        scope = {"type": "http", "method": "GET", "path": f"/people/{pid}",
                 "headers": [], "query_string": b""}
        req = Request(scope)
        req.state.principal = Principal(1, "a@e.com", "A",
                                        frozenset({"client.read", "record.read_all", "work.read", "exception.read"}))
        body = person_profile(req, pid, tab="overview").body.decode()
        assert "Client 360" in body
        for label in ("Client AUM", "Insurance", "Tax engagements", "Open exceptions", "Open tasks"):
            assert label in body
        # No advisor-intelligence recommendation strings in the snapshot itself.
        assert "Roth" not in body and "cross-sell" not in body.lower()
    finally:
        _cleanup(pid)
