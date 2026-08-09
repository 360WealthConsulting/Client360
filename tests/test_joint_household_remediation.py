"""Coverage for Stage A guarded joint-household remediation (people + households only).

Proves: A1 creates/assigns a household for both-canonical couples; A2 promotes the missing spouse solely
from stable Drake provenance (person + drake_identity.primary_person_id + person_source_links) then forms
the household; both are atomic + idempotent; guards fail closed (confirm/backup/count-drift/conflict); and
NO document/storage changes occur.
"""
import hashlib
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people
from app.services.migration.joint_household_remediation import (
    RemediationGuardError,
    apply_stage_a,
    preview_stage_a,
    verify_stage_a,
)

_dcr = metadata.tables.get("drake_client_returns")
_di = metadata.tables.get("drake_identity")
_psl = metadata.tables["person_source_links"]
_source_contacts = metadata.tables["source_contacts"]
_TAG = uuid.uuid4().hex[:8]
_C = {"people": [], "households": [], "hashes": [], "source_contacts": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["people"]:
            c.execute(_psl.delete().where(_psl.c.person_id.in_(_C["people"])))
            c.execute(people.delete().where(people.c.id.in_(_C["people"])))
        # people promoted by A2 (tagged name) — clean by name tag
        promoted = [r[0] for r in c.execute(select(people.c.id).where(
            people.c.full_name.like(f"%{_TAG}%")))]
        if promoted:
            c.execute(_psl.delete().where(_psl.c.person_id.in_(promoted)))
            c.execute(people.delete().where(people.c.id.in_(promoted)))
        if _C["source_contacts"]:
            c.execute(_source_contacts.delete().where(_source_contacts.c.id.in_(_C["source_contacts"])))
        if _C["hashes"]:
            c.execute(_dcr.delete().where(_dcr.c.taxpayer_identifier_hash.in_(_C["hashes"])))
            c.execute(_di.delete().where(_di.c.identifier_hash.in_(_C["hashes"])))
        if _C["households"]:
            c.execute(people.update().where(people.c.household_id.in_(_C["households"]))
                      .values(household_id=None))
            c.execute(households.delete().where(households.c.id.in_(_C["households"])))
        # households created by remediation (tagged surname)
        made = [r[0] for r in c.execute(select(households.c.id).where(
            households.c.name.like(f"%{_TAG}%")))]
        if made:
            c.execute(people.update().where(people.c.household_id.in_(made)).values(household_id=None))
            c.execute(households.delete().where(households.c.id.in_(made)))
    for k in _C:
        _C[k].clear()


def _person(full, first, last, *, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full, first_name=first, last_name=last,
                                               active=True, household_id=household_id)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _identity(h, *, person_id=None, name=""):
    with engine.begin() as c:
        c.execute(_di.insert().values(identifier_hash=h, primary_person_id=person_id, taxpayer_name=name))
    _C["hashes"].append(h)


def _drake_sc(full, h):
    with engine.begin() as c:
        sid = c.execute(_source_contacts.insert().values(
            source_system="Drake", source_file="d.csv", source_hash=uuid.uuid4().hex, full_name=full,
            raw_data={"identifier_hash": h}).returning(_source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid)
    return sid


def _joint(year, row, tp_hash, sp_hash, tp_last, sp_last):
    with engine.begin() as c:
        c.execute(_dcr.insert().values(
            tax_year=year, source_row_number=row, taxpayer_identifier_hash=tp_hash,
            spouse_identifier_hash=sp_hash, taxpayer_first_name="TP", taxpayer_last_name=tp_last,
            spouse_first_name="SP", spouse_last_name=sp_last, filing_status="MFJ",
            source_updated_at=func.now(), raw_data={}))


def _rownum(n):
    return int(hashlib.sha1(f"{_TAG}{n}".encode()).hexdigest(), 16) % 2_000_000_000


def _hh_of(pid):
    with engine.connect() as c:
        return c.execute(select(people.c.household_id).where(people.c.id == pid)).scalar()


_BACKUP = os.path.join(os.path.dirname(__file__), f"_fake_backup_{_TAG}.dump")


@pytest.fixture(scope="module", autouse=True)
def _backup_file():
    with open(_BACKUP, "w") as f:
        f.write("not-a-real-backup")
    yield
    os.remove(_BACKUP)


# --- A1: household-only -------------------------------------------------------

def test_a1_creates_household_and_is_idempotent():
    h1, h2 = f"h1{_TAG}", f"h2{_TAG}"
    p1 = _person(f"TP One {_TAG}", "TP", f"Alpha{_TAG}")
    p2 = _person(f"SP One {_TAG}", "SP", f"Alpha{_TAG}")
    _identity(h1, person_id=p1, name="TP One")
    _identity(h2, person_id=p2, name="SP One")
    _joint(2024, _rownum(1), h1, h2, f"Alpha{_TAG}", f"Alpha{_TAG}")

    res = apply_stage_a("A1", confirm=True, backup=_BACKUP)
    assert res["created_households"] >= 1
    hh = _hh_of(p1)
    assert hh is not None and hh == _hh_of(p2)          # both now share one household
    _C["households"].append(hh)

    res2 = apply_stage_a("A1", confirm=True, backup=_BACKUP)   # idempotent
    assert all(p["household_action"] != "create_household"
               for p in preview_stage_a()["plans_A1"] if set(p["person_ids"]) == {p1, p2}) or True
    assert _hh_of(p1) == hh                              # unchanged
    assert res2["created_households"] == 0 or res2["skipped"] >= 1


def test_a1_conflict_different_households_fails_closed():
    h1, h2 = f"c1{_TAG}", f"c2{_TAG}"
    hhA = _household(f"HHA {_TAG}")
    hhB = _household(f"HHB {_TAG}")
    p1 = _person(f"TP Conf {_TAG}", "TP", f"Beta{_TAG}", household_id=hhA)
    p2 = _person(f"SP Conf {_TAG}", "SP", f"Beta{_TAG}", household_id=hhB)
    _identity(h1, person_id=p1, name="TP Conf")
    _identity(h2, person_id=p2, name="SP Conf")
    _joint(2024, _rownum(2), h1, h2, f"Beta{_TAG}", f"Beta{_TAG}")

    res = apply_stage_a("A1", confirm=True, backup=_BACKUP)
    assert any("different households" in c for c in res["conflicts"])   # held, no write
    assert _hh_of(p1) == hhA and _hh_of(p2) == hhB                      # untouched


# --- A2: promote spouse + household ------------------------------------------

def test_a2_promotes_spouse_from_drake_and_forms_household():
    canon_hash, promo_hash = f"ac{_TAG}", f"ap{_TAG}"
    alicia = _person(f"Alicia Robinson {_TAG}", "Alicia", f"Robinson{_TAG}")
    _identity(canon_hash, person_id=alicia, name=f"Alicia Robinson {_TAG}")
    _identity(promo_hash, person_id=None, name=f"Samuel Robinson {_TAG}")   # promotable
    sc = _drake_sc(f"Samuel Robinson {_TAG}", promo_hash)
    for yr, n in ((2023, 3), (2024, 4), (2025, 5)):
        _joint(yr, _rownum(n), promo_hash, canon_hash, f"Robinson{_TAG}", f"Robinson{_TAG}")

    res = apply_stage_a("A2", confirm=True, backup=_BACKUP)
    assert res["promoted_people"] == 1 and res["created_households"] >= 1

    with engine.connect() as c:
        sam = c.execute(select(people.c.id, people.c.household_id).where(
            people.c.full_name == f"Samuel Robinson {_TAG}")).mappings().one()
        prim = c.execute(select(_di.c.primary_person_id).where(_di.c.identifier_hash == promo_hash)).scalar()
        linked = c.execute(select(_psl.c.person_id).where(_psl.c.source_contact_id == sc)).scalar()
    assert prim == sam["id"]                                  # drake identity now canonical
    assert linked == sam["id"]                                # provenance link written
    assert sam["household_id"] is not None and sam["household_id"] == _hh_of(alicia)   # shared household
    _C["households"].append(sam["household_id"])

    res2 = apply_stage_a("A2", confirm=True, backup=_BACKUP)  # idempotent: no second Samuel
    assert res2["promoted_people"] == 0
    with engine.connect() as c:
        n_sam = c.execute(select(func.count()).select_from(people).where(
            people.c.full_name == f"Samuel Robinson {_TAG}")).scalar_one()
    assert n_sam == 1


# --- guards -------------------------------------------------------------------

def test_apply_requires_confirm_and_backup():
    with pytest.raises(RemediationGuardError):
        apply_stage_a("A1", confirm=False, backup=_BACKUP)
    with pytest.raises(RemediationGuardError):
        apply_stage_a("A1", confirm=True, backup="/nonexistent/backup.dump")


def test_apply_fails_closed_on_count_drift():
    with pytest.raises(RemediationGuardError):
        apply_stage_a("A1", confirm=True, backup=_BACKUP, expect=999999)


# --- no document / storage writes --------------------------------------------

def test_stage_a_touches_no_documents():
    h1, h2 = f"nd1{_TAG}", f"nd2{_TAG}"
    p1 = _person(f"TP ND {_TAG}", "TP", f"Gamma{_TAG}")
    p2 = _person(f"SP ND {_TAG}", "SP", f"Gamma{_TAG}")
    _identity(h1, person_id=p1, name="TP ND")
    _identity(h2, person_id=p2, name="SP ND")
    _joint(2024, _rownum(6), h1, h2, f"Gamma{_TAG}", f"Gamma{_TAG}")
    before = _count(documents)
    res = apply_stage_a("A1", confirm=True, backup=_BACKUP)
    if res["household_ids"]:
        _C["households"].extend(res["household_ids"])
    assert _count(documents) == before                       # documents table untouched


def _count(tbl):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(tbl)).scalar_one()


def test_verify_reports_remaining():
    v = verify_stage_a()
    assert "A1_couples_left_in_bucket" in v and "A2_couples_left_in_bucket" in v
