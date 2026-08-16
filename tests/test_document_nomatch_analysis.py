"""Phase 4 — READ-ONLY NO_MATCH context analysis: bucket classification + zero mutation."""
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, people
from app.services import document_nomatch_analysis as na
from app.services import document_owner_proposal as dop

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_DOCS: list = []
_PEOPLE: list = []
_HH: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    for lst in (_DOCS, _PEOPLE, _HH):
        lst.clear()


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _person(full_name, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True, household_id=household_id)
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(tmp_path, body, *, folder, name="d.txt", person_id=None, household_id=None):
    f = tmp_path / f"{uuid.uuid4().hex}.txt"
    f.write_text(body)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=None, original_name=name,
            stored_name=f"na-{_TAG}-{uuid.uuid4().hex}", storage_path=str(f), storage_uri=str(f),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive", "taxdome_folder": folder}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _bucket(did):
    res = na.analyze_nomatch()
    row = next((r for r in res["rows"] if r["document_id"] == did), None)
    return row


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id).where(documents.c.id == did)).first())


# --- classification --------------------------------------------------------------------------------

def test_uniquely_mapped_folder_is_context_high(tmp_path):
    folder = f"Folder-{_A}-UNIQ"
    owner = _person(f"Owner {_A}")
    _doc(tmp_path, "resolved statement\n", folder=folder, person_id=owner)     # resolved neighbour
    did = _doc(tmp_path, "expenses total 42, no identity\n", folder=folder)    # NO_MATCH sibling
    row = _bucket(did)
    assert row and row["bucket"] == "CONTEXT_HIGH"
    assert (row["proposed_owner_type"], row["proposed_owner_id"]) == ("person", owner)
    assert row["folder_mapping"] == "unique"
    assert _owner(did) == (None, None, None)                                   # READ-ONLY


def test_mixed_household_folder_maps_to_household_not_wrong_person(tmp_path):
    folder = f"Folder-{_A}-HH"
    hid = _household(f"{_A} Household")
    a = _person(f"Aspouse {_A}", household_id=hid)
    b = _person(f"Bspouse {_A}", household_id=hid)
    _doc(tmp_path, "a\n", folder=folder, person_id=a)          # two resolved members, same household
    _doc(tmp_path, "b\n", folder=folder, person_id=b)
    did = _doc(tmp_path, "joint expenses, no identity\n", folder=folder)
    row = _bucket(did)
    assert row and row["bucket"] == "CONTEXT_LIKELY"
    assert (row["proposed_owner_type"], row["proposed_owner_id"]) == ("household", hid)   # NOT a person


def test_conflicting_resolved_neighbors_is_conflict(tmp_path):
    folder = f"Folder-{_A}-CONF"
    p1 = _person(f"Unrelated1 {_A}")                          # different, no shared household
    p2 = _person(f"Unrelated2 {_A}")
    _doc(tmp_path, "one\n", folder=folder, person_id=p1)
    _doc(tmp_path, "two\n", folder=folder, person_id=p2)
    did = _doc(tmp_path, "ambiguous, no identity\n", folder=folder)
    row = _bucket(did)
    assert row and row["bucket"] == "CONFLICT" and row["proposed_owner_id"] is None


def test_generic_firm_folder_is_general_unresolved(tmp_path):
    did = _doc(tmp_path, "office supplies invoice total 19.99\n", folder=f"Admin-General-{_A}")
    row = _bucket(did)
    assert row and row["bucket"] == "GENERAL_OR_UNRESOLVED" and row["proposed_owner_id"] is None


def test_unknown_strong_identity_is_possible_new_entity(tmp_path):
    did = _doc(tmp_path, f"Dear Brandnewperson {_A}, contact newp-{_TAG}@x.com\n",
               folder=f"Intake-{_A}")
    row = _bucket(did)
    assert row and row["bucket"] == "POSSIBLE_NEW_ENTITY"


# --- safety ----------------------------------------------------------------------------------------

def test_read_only_no_ownership_mutation(tmp_path):
    folder = f"Folder-{_A}-RO"
    owner = _person(f"RO {_A}")
    _doc(tmp_path, "resolved\n", folder=folder, person_id=owner)
    did = _doc(tmp_path, "sibling no identity\n", folder=folder)
    na.analyze_nomatch(); na.analyze_nomatch()
    assert _owner(did) == (None, None, None)


def test_permanent_reject_excluded(tmp_path, monkeypatch):
    did = _doc(tmp_path, "no identity\n", folder=f"F-{_A}")
    monkeypatch.setattr(dop, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({did}))
    res = na.analyze_nomatch()
    assert did not in {r["document_id"] for r in res["rows"]}


def test_already_owned_documents_ignored(tmp_path):
    owner = _person(f"Owned {_A}")
    did = _doc(tmp_path, "owned doc\n", folder=f"F-{_A}", person_id=owner)     # already owned
    res = na.analyze_nomatch()
    assert did not in {r["document_id"] for r in res["rows"]}


def test_counts_and_folder_stats_present(tmp_path):
    _doc(tmp_path, "office supplies\n", folder=f"General-{_A}")
    res = na.analyze_nomatch()
    assert set(res["counts"]) == set(na.BUCKETS)
    assert res["total"] == sum(res["counts"].values())
    assert set(res["folder_stats"]) == {"unique_folders", "unique_mapped", "mixed_or_ambiguous"}
    assert isinstance(res["top_folders"], list) and isinstance(res["reasons"], list)


def test_counts_never_write(tmp_path):
    before = None
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents)).scalar()
    _doc(tmp_path, "x\n", folder=f"Z-{_A}")
    na.analyze_nomatch()
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert after == before + 1                                # only our fixture insert; analysis wrote nothing
