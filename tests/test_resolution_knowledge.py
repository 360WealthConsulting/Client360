"""Coverage for the durable resolution / alias knowledge ledger + service (PR-1).

Proves: approved positive resolutions to person / household / relationship_entity and firm_material are
reusable; reject / defer / ambiguous are audited but never reusable; corrections SUPERSEDE with full
history retained; lookup is by NORMALIZED subject identity; entity validation and conflict handling fail
closed; and the migrated schema enforces one-active-per-subject and the decision/entity CHECK at the DB.
"""
import uuid

import pytest
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from app.db import engine, folder_resolution_decisions, households, people, relationship_entities
from app.services.resolution_knowledge import (
    ResolutionConflictError,
    ResolutionKnowledgeError,
    get_current_decision,
    get_decision_history,
    get_reusable_resolution,
    record_decision,
)

_SYS = f"testsys-{uuid.uuid4().hex[:8]}"       # already lowercase => equals its normalized form
_TYPE = "folder"
_C = {"folder_resolution_decisions": [], "people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        c.execute(folder_resolution_decisions.update()
                  .where(folder_resolution_decisions.c.subject_system == _SYS)
                  .values(superseded_by=None))
        c.execute(folder_resolution_decisions.delete()
                  .where(folder_resolution_decisions.c.subject_system == _SYS))
        for tbl, key in ((relationship_entities, "relationship_entities"),
                         (people, "people"), (households, "households")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person():
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"P {uuid.uuid4().hex[:6]}", active=True)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _household():
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {uuid.uuid4().hex[:6]}")
                        .returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _business():
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"Biz {uuid.uuid4().hex[:6]}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
    _C["relationship_entities"].append(eid)
    return eid


def _active_count(key):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(folder_resolution_decisions).where(and_(
            folder_resolution_decisions.c.subject_system == _SYS,
            folder_resolution_decisions.c.subject_type == _TYPE,
            folder_resolution_decisions.c.subject_key == key,
            folder_resolution_decisions.c.active.is_(True)))).scalar_one()


# --- positive approved resolutions: person / household / business / firm ------

def test_positive_person_resolution_is_reusable():
    pid = _person()
    rid = record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="abigail dargis",
                          display_name="Abigail Dargis", decision="link_person",
                          resulting_entity_type="person", resulting_entity_id=pid,
                          match_reason="stable email identity", confidence=100, reviewed_by="Tester")
    cur = get_current_decision(_SYS, _TYPE, "abigail dargis")
    assert cur["decision"] == "link_person" and cur["resulting_entity_id"] == pid
    reuse = get_reusable_resolution(_SYS, _TYPE, "abigail dargis")
    assert reuse is not None and reuse["id"] == rid


def test_positive_household_and_business_targets():
    hid = _household()
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="white household",
                    display_name="White Household", decision="link_household",
                    resulting_entity_type="household", resulting_entity_id=hid)
    assert get_reusable_resolution(_SYS, _TYPE, "white household")["resulting_entity_id"] == hid

    eid = _business()
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="star city heating",
                    display_name="Star City Heating", decision="create_business",
                    resulting_entity_type="relationship_entity", resulting_entity_id=eid)
    biz = get_reusable_resolution(_SYS, _TYPE, "star city heating")
    assert biz["resulting_entity_type"] == "relationship_entity" and biz["resulting_entity_id"] == eid


def test_firm_material_is_positive_reusable_without_entity():
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="acme engagement letters",
                    display_name="Acme Engagement Letters", decision="firm_material", reviewed_by="Tester")
    reuse = get_reusable_resolution(_SYS, _TYPE, "acme engagement letters")
    assert reuse is not None
    assert reuse["resulting_entity_type"] == "firm" and reuse["resulting_entity_id"] is None


# --- non-reusable dispositions ------------------------------------------------

@pytest.mark.parametrize("decision", ["reject", "defer", "ambiguous"])
def test_non_reusable_disposition_audited_but_not_reusable(decision):
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=f"nobody {decision}",
                    display_name=f"Nobody {decision}", decision=decision, reviewed_by="Tester")
    assert get_current_decision(_SYS, _TYPE, f"nobody {decision}")["decision"] == decision   # audited
    assert get_reusable_resolution(_SYS, _TYPE, f"nobody {decision}") is None                 # not reusable


# --- normalized subject-identity lookup ---------------------------------------

def test_lookup_by_normalized_subject_identity():
    pid = _person()
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="john smith",
                    display_name="John Smith", decision="create_person",
                    resulting_entity_type="person", resulting_entity_id=pid)
    # different casing + extra whitespace resolves to the same normalized subject.
    assert get_current_decision(_SYS.upper(), "  Folder ", "  John   SMITH ") is not None
    assert get_reusable_resolution(_SYS, _TYPE, "JOHN smith")["resulting_entity_id"] == pid


# --- supersession + history ---------------------------------------------------

def test_correction_supersedes_with_history_retained():
    key = "correctable"
    p1, p2 = _person(), _person()
    first = record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key,
                            display_name="Correctable", decision="create_person",
                            resulting_entity_type="person", resulting_entity_id=p1, reviewed_by="A")
    second = record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key,
                             display_name="Correctable", decision="link_person",
                             resulting_entity_type="person", resulting_entity_id=p2, reviewed_by="B",
                             supersede=True)
    assert _active_count(key) == 1
    cur = get_current_decision(_SYS, _TYPE, key)
    assert cur["id"] == second and cur["resulting_entity_id"] == p2
    hist = get_decision_history(_SYS, _TYPE, key)
    assert [r["id"] for r in hist] == [second, first]                # newest first, both retained
    prior = next(r for r in hist if r["id"] == first)
    assert prior["active"] is False and prior["superseded_by"] == second and prior["superseded_at"]


def test_positive_superseded_by_rejection_not_reusable():
    key = "later rejected"
    pid = _person()
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key, display_name="LR",
                    decision="link_person", resulting_entity_type="person", resulting_entity_id=pid)
    assert get_reusable_resolution(_SYS, _TYPE, key) is not None
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key, display_name="LR",
                    decision="reject", reviewed_by="B", supersede=True)
    assert get_reusable_resolution(_SYS, _TYPE, key) is None
    assert len(get_decision_history(_SYS, _TYPE, key)) == 2


# --- fail-closed: invalid / conflicting ---------------------------------------

def test_conflicting_current_resolution_rejected_without_supersede():
    key = "conflict"
    p1, p2 = _person(), _person()
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key, display_name="C",
                    decision="link_person", resulting_entity_type="person", resulting_entity_id=p1)
    with pytest.raises(ResolutionConflictError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key, display_name="C",
                        decision="link_person", resulting_entity_type="person", resulting_entity_id=p2)
    assert _active_count(key) == 1                                    # unchanged


def test_supersede_without_active_rejected():
    with pytest.raises(ResolutionKnowledgeError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="ghost", display_name="G",
                        decision="reject", supersede=True)


def test_invalid_entity_id_rejected():
    with pytest.raises(ResolutionKnowledgeError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="badid", display_name="B",
                        decision="link_person", resulting_entity_type="person",
                        resulting_entity_id=999_000_111)             # no such person


def test_entity_type_mismatch_rejected():
    hid = _household()
    with pytest.raises(ResolutionKnowledgeError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="mismatch", display_name="M",
                        decision="link_person", resulting_entity_type="household", resulting_entity_id=hid)


def test_entity_decision_requires_entity():
    with pytest.raises(ResolutionKnowledgeError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="noent", display_name="N",
                        decision="link_business")                     # no entity supplied


def test_non_reusable_rejects_entity():
    pid = _person()
    with pytest.raises(ResolutionKnowledgeError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="rejent", display_name="R",
                        decision="reject", resulting_entity_type="person", resulting_entity_id=pid)


def test_unknown_decision_rejected():
    with pytest.raises(ResolutionKnowledgeError):
        record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key="tp", display_name="T",
                        decision="teleport")


# --- schema / migration compatibility (DB-level guards) -----------------------

def test_schema_present_with_expected_columns():
    cols = set(folder_resolution_decisions.c.keys())
    assert {"subject_system", "subject_type", "subject_key", "display_name", "decision",
            "resulting_entity_type", "resulting_entity_id", "evidence_snapshot", "match_reason",
            "confidence", "reviewed_by", "reviewed_at", "exception_id", "active", "superseded_at",
            "superseded_by", "created_at", "updated_at"} <= cols


def test_partial_unique_index_enforced_at_db():
    key = "double active"
    pid = _person()
    record_decision(subject_system=_SYS, subject_type=_TYPE, subject_key=key, display_name="DA",
                    decision="link_person", resulting_entity_type="person", resulting_entity_id=pid)
    with pytest.raises(IntegrityError), engine.begin() as c:      # second ACTIVE row, bypassing service
        c.execute(folder_resolution_decisions.insert().values(
            subject_system=_SYS, subject_type=_TYPE, subject_key=key, display_name="DA",
            decision="firm_material", resulting_entity_type="firm", active=True))


def test_db_check_forbids_entity_on_non_reusable():
    with pytest.raises(IntegrityError), engine.begin() as c:      # reject carrying an entity, at the DB
        c.execute(folder_resolution_decisions.insert().values(
            subject_system=_SYS, subject_type=_TYPE, subject_key="sneaky", display_name="S",
            decision="reject", resulting_entity_type="person", resulting_entity_id=1, active=True))
