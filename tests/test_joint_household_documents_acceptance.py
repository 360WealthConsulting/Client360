"""Archetype 3 — the JOINT / HOUSEHOLD client's Documents experience.

A married couple filing jointly is not "two individuals who share a folder". Their paperwork splits
three ways and each part has a different correct answer:

  * the JOINT documents — the 1040, the organizer, the engagement letter — are anchored on the
    HOUSEHOLD and belong to both spouses;
  * a SPOUSE-SPECIFIC document — one partner's W-2, their own IRA statement — is anchored on that
    person and belongs to them;
  * nothing at all belongs to the household next door.

The acceptance question is therefore not "do documents appear" but "does each document appear in
exactly the places it should, labelled with where it is actually filed". Two properties carry that:

  1. **A household document reaches both spouses.** ``client_documents`` unions the person with
     their household, which is what stops the joint return from being visible to neither spouse
     (it is anchored on the household, so a person-anchored read alone returns nothing).
  2. **A spouse-specific document is NOT re-attributed to the other spouse.** The union adds the
     household, not the household's other members — so Dana's own W-2 shows on Dana's page and on
     the household page, and never reads as Marcus's document. Widening that arm would be the
     "attached to the wrong individual" failure this acceptance set exists to catch.

Everything asserted here is a read. No test writes an owner, a year, a category or a name.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    metadata,
    people,
)
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.household import get_household_workspace
from app.services.documents import get_person_documents

_TAG = "JOINTHH"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})
_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_ROOT = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual"
_FOLDER = f"{_ROOT}/REYES,%20MARCUS%20AND%20DANA"
_OTHER_FOLDER = f"{_ROOT}/HALVORSEN,%20INGRID%20AND%20LARS"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            docs = list(c.scalars(select(documents.c.id)
                                  .where(documents.c.stored_name.like(f"%{_TAG}%"))))
            if docs:
                ds = metadata.tables["document_sources"]
                c.execute(ds.delete().where(ds.c.document_id.in_(docs)))
                c.execute(documents.delete().where(documents.c.id.in_(docs)))
            if pids:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


# --- fixture builders ---------------------------------------------------------------------------

def _household(name):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{_TAG} {name}")
                         .returning(households.c.id)).scalar_one()


def _person(first, last, household_id, *, primary=False):
    """A household member written the way the canonical services write one: BOTH the
    ``people.household_id`` column and the ``household_relationships`` member row."""
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}", active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(
            household_id=household_id, person_id=pid, relationship_type="spouse",
            is_primary=primary, is_primary_household=True))
    return pid


def _doc(name, *, person_id=None, household_id=None, folder=_FOLDER, folder_year=None,
         sub_folder=None, category=None, review_status="none", source_modified=None,
         status="active", deleted_at=None, archived=False):
    parts = [folder]
    if folder_year:
        parts.append(str(folder_year))
    if sub_folder:
        parts.append(sub_folder.replace(" ", "%20"))
    web_url = f"{_SITE}/{'/'.join(parts)}/{name.replace(' ', '%20')}"
    tags = {"source_system": "SharePoint", "web_url": web_url}
    if source_modified:
        tags["source_modified"] = source_modified
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{uuid.uuid4().hex}", storage_provider="Client360 Local",
            size_bytes=4096, sha256=uuid.uuid4().hex * 2,
            person_id=person_id, household_id=household_id, status=status, archived=archived,
            deleted_at=deleted_at, category=category, review_status=review_status,
            current_version=1, tags=tags).returning(documents.c.id)).scalar_one()
        ds = metadata.tables["document_sources"]
        c.execute(ds.insert().values(
            document_id=did, source_system="SharePoint", source_uri=web_url,
            source_path="/".join(parts), source_external_id=uuid.uuid4().hex, available=True))
    return did


def _principal():
    return Principal(0, "staff@e.test", "Staff", _CAPS)


def _fixture():
    """The Reyes household: two spouses, joint documents on the household, one document each on the
    individual spouses, and an entirely separate household filed next door."""
    hid = _household("Reyes Household")
    marcus = _person("Marcus", "Reyes", hid, primary=True)
    dana = _person("Dana", "Reyes", hid)
    ids = {
        "joint_1040": _doc("2023 Joint Form 1040.pdf", household_id=hid, folder_year=2023,
                           category="tax_document", source_modified="2024-04-10T12:00:00Z"),
        "organizer": _doc("2023 Tax Organizer.pdf", household_id=hid, folder_year=2023,
                          category="tax_document", source_modified="2024-01-20T12:00:00Z"),
        "joint_statement": _doc("Joint Brokerage Statement.pdf", household_id=hid,
                                sub_folder="Statements", category="statement",
                                source_modified="2024-06-30T12:00:00Z"),
        # Spouse-specific: anchored on the person, inside the same household folder.
        "dana_w2": _doc("Dana W2.pdf", person_id=dana, folder_year=2023, category="tax_document",
                        source_modified="2024-02-02T12:00:00Z"),
        "marcus_w2": _doc("Marcus W2.pdf", person_id=marcus, folder_year=2023,
                          category="tax_document", source_modified="2024-02-03T12:00:00Z"),
    }
    other_hid = _household("Halvorsen Household")
    other_person = _person("Ingrid", "Halvorsen", other_hid, primary=True)
    foreign = {
        "household": _doc("Halvorsen Joint 1040.pdf", household_id=other_hid,
                          folder=_OTHER_FOLDER, folder_year=2023, category="tax_document"),
        "person": _doc("Ingrid W2.pdf", person_id=other_person, folder=_OTHER_FOLDER,
                       folder_year=2023, category="tax_document"),
    }
    return hid, marcus, dana, ids, other_hid, foreign


def _person_rows(pid):
    return get_workspace(_principal(), person_id=pid)["sections"]["documents"]["documents"]


def _person_screen(pid):
    return get_workspace(_principal(), person_id=pid)["sections"]["documents"]["screen"]


def _household_rows(hid):
    return get_household_workspace(_principal(), hid)["sections"]["documents"]["documents"]


def _household_screen(hid):
    return get_household_workspace(_principal(), hid)["sections"]["documents"]["screen"]


def _by_name(rows):
    return {r.get("original_name"): r for r in rows}


# --- A. shared ownership: a household document belongs to both spouses ---------------------------

def test_both_spouses_see_every_joint_document():
    """The joint return is anchored on the household. A person-anchored read alone would show it to
    neither spouse, which is exactly why the client read is a person+household union."""
    _hid, marcus, dana, ids, _o, _f = _fixture()
    joint = {ids["joint_1040"], ids["organizer"], ids["joint_statement"]}
    assert joint <= {r["id"] for r in _person_rows(marcus)}
    assert joint <= {r["id"] for r in _person_rows(dana)}


def test_a_joint_document_says_it_is_filed_at_the_household():
    """Correct owner, stated honestly: the row reports WHERE it is filed rather than implying that
    the spouse whose page you happen to be on owns it."""
    _hid, marcus, _dana, _ids, _o, _f = _fixture()
    row = _by_name(_person_screen(marcus)["rows"])["2023 Joint Form 1040.pdf"]
    assert row["related_to"]["kind"] == "household"
    assert "Reyes" in row["related_to"]["label"]
    assert row["person_id"] is None and row["household_id"] is not None


def test_the_household_workspace_shows_the_whole_family_file():
    """The household view is the union of the household's own documents and every member's."""
    hid, _m, _d, ids, _o, _f = _fixture()
    assert set(ids.values()) <= {r["id"] for r in _household_rows(hid)}


def test_the_person_document_service_agrees_with_the_workspace():
    _hid, marcus, _dana, ids, _o, _f = _fixture()
    expected = {ids["joint_1040"], ids["organizer"], ids["joint_statement"], ids["marcus_w2"]}
    assert {d["id"] for d in get_person_documents(marcus)} == expected


# --- B. a spouse-specific document is not re-attributed ------------------------------------------

def test_a_spouses_own_document_stays_on_that_spouse():
    """Dana's W-2 is Dana's. Marcus's client page must not claim it, and vice versa — the union adds
    the HOUSEHOLD, never the household's other members."""
    _hid, marcus, dana, ids, _o, _f = _fixture()
    assert ids["dana_w2"] in {r["id"] for r in _person_rows(dana)}
    assert ids["dana_w2"] not in {r["id"] for r in _person_rows(marcus)}
    assert ids["marcus_w2"] in {r["id"] for r in _person_rows(marcus)}
    assert ids["marcus_w2"] not in {r["id"] for r in _person_rows(dana)}


def test_a_spouse_document_is_labelled_with_that_spouses_name_on_the_household_screen():
    """On the household screen both kinds of row are present, and Related To distinguishes them —
    otherwise a reviewer cannot tell a joint document from one member's."""
    hid, _marcus, _dana, _ids, _o, _f = _fixture()
    rows = _by_name(_household_screen(hid)["rows"])
    assert rows["Dana W2.pdf"]["related_to"]["kind"] == "person"
    assert "Dana" in rows["Dana W2.pdf"]["related_to"]["label"]
    assert rows["2023 Joint Form 1040.pdf"]["related_to"]["kind"] == "household"


def test_the_related_to_facet_separates_the_household_from_its_members():
    """The screen's Related To filter is how staff act on that distinction, so the options must
    actually contain both kinds."""
    hid, _m, _d, _ids, _o, _f = _fixture()
    kinds = {o["kind"] for o in _household_screen(hid)["related_options"]}
    assert {"household", "person"} <= kinds


def test_every_household_row_keeps_its_real_anchor():
    """The household read tags each row with where it is anchored and must not rewrite any of them."""
    hid, _m, _d, ids, _o, _f = _fixture()
    rows = {r["id"]: r for r in _household_rows(hid)}
    assert rows[ids["joint_1040"]]["household_id"] == hid
    assert rows[ids["joint_1040"]]["person_id"] is None
    assert rows[ids["dana_w2"]]["household_id"] is None
    assert rows[ids["dana_w2"]]["person_id"] is not None


# --- C. no foreign-household leakage -------------------------------------------------------------

def test_the_household_next_door_never_appears():
    _hid, marcus, dana, _ids, _o, foreign = _fixture()
    for pid in (marcus, dana):
        seen = {r["id"] for r in _person_rows(pid)}
        assert not (set(foreign.values()) & seen)


def test_this_households_documents_do_not_reach_the_other_household():
    hid, _m, _d, ids, other_hid, _f = _fixture()
    assert not (set(ids.values()) & {r["id"] for r in _household_rows(other_hid)})


def test_the_household_screen_names_only_this_households_members():
    """A member label leaking from another roster would mean the union crossed households."""
    hid, _m, _d, _ids, _o, _f = _fixture()
    labels = " ".join(r["related_to"]["label"] for r in _household_screen(hid)["rows"])
    assert "Halvorsen" not in labels


# --- D. filing tree, tax year, type and name -----------------------------------------------------

def test_every_document_is_filed_under_the_joint_client_folder():
    hid, _m, _d, _ids, _o, _f = _fixture()
    for row in _household_rows(hid):
        assert "REYES, MARCUS AND DANA" in (row["source_folder"] or "")
        assert "HALVORSEN" not in (row["source_folder"] or "")


def test_the_tax_year_is_derived_from_the_filing_tree_where_the_filename_is_silent():
    hid, _m, _d, _ids, _o, _f = _fixture()
    rows = _by_name(_household_rows(hid))
    assert rows["2023 Joint Form 1040.pdf"]["tax_year"] == 2023
    assert rows["Dana W2.pdf"]["tax_year"] == 2023               # folder-only evidence
    assert rows["Dana W2.pdf"]["tax_year_inferred"] is True
    assert rows["Joint Brokerage Statement.pdf"]["tax_year"] is None


def test_the_year_facet_partitions_the_household_file():
    hid, _m, _d, _ids, _o, _f = _fixture()
    assert "2023" in _household_screen(hid)["years"]


def test_categories_and_types_survive_to_the_household_screen():
    hid, _m, _d, _ids, _o, _f = _fixture()
    rows = _by_name(_household_rows(hid))
    assert rows["2023 Joint Form 1040.pdf"]["category"] == "tax_document"
    assert rows["Joint Brokerage Statement.pdf"]["category"] == "statement"
    assert all(r["type_text"] for r in _household_screen(hid)["rows"])


def test_every_household_row_shows_a_readable_name_and_a_working_download_path():
    hid, _m, _d, _ids, _o, _f = _fixture()
    for row in _household_rows(hid):
        assert row["name"] and not row["name"].startswith("http")
        assert _TAG not in row["name"]
        assert row["download_url"] == f"/documents/{row['id']}/download"
        assert row["sources"] and row["sources"][0]["source_uri"].startswith("https://")
        assert row["sources"][0]["available"] is True


# --- E. lifecycle and review state ---------------------------------------------------------------

def test_deleted_documents_are_suppressed_on_both_spouse_and_household_views():
    hid, marcus, _dana, _ids, _o, _f = _fixture()
    _doc("Retired Joint Return.pdf", household_id=hid, status="deleted",
         deleted_at=datetime.now(UTC), folder_year=2022)
    _doc("Half Retired.pdf", household_id=hid, status="active",
         deleted_at=datetime.now(UTC), folder_year=2022)
    for names in ({r["original_name"] for r in _person_rows(marcus)},
                  {r["original_name"] for r in _household_rows(hid)}):
        assert "Retired Joint Return.pdf" not in names
        assert "Half Retired.pdf" not in names


def test_a_household_document_awaiting_review_is_counted_at_both_levels():
    """A joint document someone has flagged must not be visible as settled on either spouse's page."""
    hid, marcus, _dana, _ids, _o, _f = _fixture()
    did = _doc("Missing Signature Page.pdf", household_id=hid, folder_year=2023,
               review_status="pending")
    for screen in (_household_screen(hid), _person_screen(marcus)):
        assert screen["needs_review_count"] == 1
        shaped = next(r for r in screen["rows"] if r["id"] == did)
        assert shaped["needs_review"] is True
        assert "review_requested" in {r["key"] for r in shaped["actionable"]}


def test_a_settled_household_file_reports_no_review_work():
    hid, _m, _d, _ids, _o, _f = _fixture()
    assert _household_screen(hid)["needs_review_count"] == 0
