"""Golden regression for the Phase D.5E architecture-hardening refactor.

Proves the refactor is behavior-preserving: the serialized signals, the rendered
Advisor Intelligence HTML, and the registry contents produced by the (refactored)
code must exactly match a golden snapshot captured from the pre-refactor code
(tests/fixtures/d5e_golden.json). Record-id-derived numbers are normalized (→"N")
so the golden is primary-key independent. Also re-checks scope-first authorization.
"""
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, insert

from app.db import (
    account_beneficiaries,
    accounts,
    engine,
    households,
    insurance_policies,
    insurance_policy_reviews,
    insurance_product_families,
    insurance_product_versions,
    people,
    record_assignments,
    relationship_entities,
    users,
)
from app.security.models import Principal
from app.services.advisor_intelligence import (
    get_client_signals,
    group_signals,
    list_registered_signals,
)
from app.services.advisor_workspace import FIRM_TZ

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=FIRM_TZ)
TODAY = NOW.date()
CAPS = frozenset({"client.read", "work.read", "task.read", "exception.read", "insurance.read"})
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "d5e_golden.json").read_text())


def _norm(s):
    return re.sub(r"\d+", "N", s)


def _nj(obj):
    """Normalized-JSON projection: every number (pk ids, versions, confidence) → "N"
    so the golden is primary-key / value independent, proving structural identity."""
    return _norm(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def _sfx():
    return uuid.uuid4().hex[:6]


def _setup():
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"g-{_sfx()}@e.test", normalized_email=f"g-{_sfx()}@e.test",
            display_name="G", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name="HH GOLD").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name="Client G", primary_email=f"{_sfx()}@e.test",
            normalized_email=f"{_sfx()}@e.test", household_id=hh, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"REV-{_sfx()}",
            account_name="Brokerage G", status="open", last_review_date=TODAY - timedelta(days=350)))
        c.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"OD-{_sfx()}",
            account_name="Overdue G", status="open", last_review_date=None))
        c.execute(insert(accounts).values(
            person_id=pid, custodian="Schwab", account_number=f"IRA-{_sfx()}",
            account_name="IRA G", status="open", registration_type="Traditional IRA",
            last_review_date=TODAY))
        carrier = c.execute(relationship_entities.insert().values(
            entity_type="insurance_carrier", name="C GOLD", details={}, active=True
        ).returning(relationship_entities.c.id)).scalar_one()
        fam = c.execute(insurance_product_families.insert().values(
            carrier_id=carrier, name="F GOLD", product_type="term_life", line="life"
        ).returning(insurance_product_families.c.id)).scalar_one()
        pv = c.execute(insurance_product_versions.insert().values(
            family_id=fam, version_label="1").returning(insurance_product_versions.c.id)).scalar_one()
        pol = c.execute(insurance_policies.insert().values(
            carrier_id=carrier, product_version_id=pv, person_id=pid, status="in_force"
        ).returning(insurance_policies.c.id)).scalar_one()
        c.execute(insert(insurance_policy_reviews).values(
            policy_id=pol, review_type="annual", status="due", due_date=TODAY))
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=TODAY))
    return {"uid": uid, "pid": pid, "hh": hh,
            "principal": Principal(uid, "a@e.com", "Adv", CAPS)}


def _teardown(ids):
    with engine.begin() as c:
        pol_ids = list(c.scalars(insurance_policies.select().with_only_columns(
            insurance_policies.c.id).where(insurance_policies.c.person_id == ids["pid"])))
        if pol_ids:
            c.execute(delete(insurance_policy_reviews).where(insurance_policy_reviews.c.policy_id.in_(pol_ids)))
            c.execute(delete(insurance_policies).where(insurance_policies.c.person_id == ids["pid"]))
        aids = list(c.scalars(accounts.select().with_only_columns(accounts.c.id).where(
            accounts.c.person_id == ids["pid"])))
        if aids:
            c.execute(delete(account_beneficiaries).where(account_beneficiaries.c.account_id.in_(aids)))
        c.execute(delete(accounts).where(accounts.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _render(signals):
    tpl = Jinja2Templates(directory="app/templates")
    tpl.env.globals["signal_groups"] = group_signals
    html = tpl.env.from_string(
        '{% import "components/intelligence.html" as intel %}{{ intel.signals_panel(signals) }}'
    ).render(signals=signals)
    return _norm(re.sub(r"\s+", " ", html).strip())


def test_serialized_signals_match_golden():
    ids = _setup()
    try:
        sigs = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        assert _nj([s.to_dict() for s in sigs]) == _nj(GOLDEN["signals"])
    finally:
        _teardown(ids)


def test_rendered_html_matches_golden():
    ids = _setup()
    try:
        sigs = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        assert _render(sigs) == GOLDEN["html"]
    finally:
        _teardown(ids)


def test_registry_matches_golden():
    assert _nj([r.to_dict() for r in list_registered_signals()]) == _nj(GOLDEN["registry"])


def test_ordering_and_ids_are_deterministic():
    ids = _setup()
    try:
        a = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        b = get_client_signals(ids["principal"], ids["pid"], now=NOW)
        assert [s.id for s in a] == [s.id for s in b]
        ranks = [s.priority.rank for s in a]
        assert ranks == sorted(ranks, reverse=True)
    finally:
        _teardown(ids)


def test_scope_first_authorization_preserved():
    ids = _setup()
    try:
        # A principal with no assignment to this person sees nothing.
        stranger = Principal(999999, "s@e.com", "S", CAPS)
        assert get_client_signals(stranger, ids["pid"], now=NOW) == ()
    finally:
        _teardown(ids)
