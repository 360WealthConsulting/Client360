"""Archetype 4 — the BUSINESS / ENTITY client's Documents experience.

A business is a client in its own right, and its paperwork is anchored on the ENTITY
(``documents.organization_id`` → ``relationship_entities``) rather than on a person or a household.
The acceptance risk here is one-directional and specific:

    the S-corp return, the payroll file and the operating agreement must file under the BUSINESS —
    never under the human being who happens to own it.

That failure is easy to produce and hard to see. The owner is a real client with a real household,
their surname is usually inside the business name, and a great deal of the firm's filing evidence is
folder text. So this module asserts the separation from every direction the union could cross:

  * a business document reaches the business workspace and the organization-scoped read;
  * it reaches NEITHER the owner's client page NOR the owner's household page;
  * the owner's own documents do not become the business's;
  * a second, unrelated business's file never mixes in;
  * and the row's own cells — Related To, filing location, year, type, name, download path — say
    "business" rather than implying a person.

``client_documents`` deliberately has no person+household union for an organization: it falls
through to the single-entity read. That is asserted here rather than assumed, because widening it
later would silently reattribute entity documents to people.

Every assertion is a read. Nothing is linked, re-anchored or written.
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
    relationship_entities,
    relationship_ownership,
    relationship_types,
    relationships,
)
from app.security.models import Principal
from app.services.business_workspace import get_business_workspace
from app.services.client360 import get_workspace
from app.services.client360.documents_screen import build as build_screen
from app.services.client360.household import get_household_workspace
from app.services.client360.sections import enrich_documents
from app.services.document_platform.relationships import client_documents, documents_for_entity
from app.services.documents import get_person_documents
from app.services.relationships import ensure_person_entity

_TAG = "BIZDOC"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})
_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_BIZ_ROOT = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Business"
_IND_ROOT = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual"
_BIZ_FOLDER = f"{_BIZ_ROOT}/SANDOVAL%20MASONRY%20LLC"
_OTHER_BIZ_FOLDER = f"{_BIZ_ROOT}/TREMBLAY%20LOGISTICS%20INC"
_IND_FOLDER = f"{_IND_ROOT}/SANDOVAL,%20ELENA%20AND%20RAUL"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            docs = list(c.scalars(select(documents.c.id)
                                  .where(documents.c.stored_name.like(f"%{_TAG}%"))))
            if docs:
                ds = metadata.tables["document_sources"]
                c.execute(ds.delete().where(ds.c.document_id.in_(docs)))
                c.execute(documents.delete().where(documents.c.id.in_(docs)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(f"%{_TAG}%"))))
            if pids:
                ents += list(c.scalars(select(relationship_entities.c.id)
                                       .where(relationship_entities.c.person_id.in_(pids))))
            ents = list(set(ents))
            if ents:
                rels = list(c.scalars(select(relationships.c.id).where(
                    relationships.c.from_entity_id.in_(ents)
                    | relationships.c.to_entity_id.in_(ents))))
                if rels:
                    c.execute(delete(relationship_ownership)
                              .where(relationship_ownership.c.relationship_id.in_(rels)))
                    c.execute(delete(relationships).where(relationships.c.id.in_(rels)))
                c.execute(delete(relationship_entities)
                          .where(relationship_entities.c.id.in_(ents)))
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


def _person(first, last, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}", active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member",
                is_primary=True, is_primary_household=True))
    return pid


def _business(name):
    with engine.begin() as c:
        return c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"{name} {_TAG}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()


def _owns(person_id, business_id):
    """An active ownership edge person -> business, shaped the way ``record_ownership`` shapes one."""
    with engine.begin() as c:
        rt = c.execute(select(relationship_types.c.id).where(
            relationship_types.c.category.in_(("ownership", "org_structure")),
            relationship_types.c.code == "owns").limit(1)).scalar_one()
        owner_entity = ensure_person_entity(c, person_id)
        rel = c.execute(insert(relationships).values(
            from_entity_id=owner_entity, to_entity_id=business_id, relationship_type_id=rt,
            active=True, source="test").returning(relationships.c.id)).scalar_one()
        c.execute(insert(relationship_ownership).values(
            relationship_id=rel, is_direct=True, evidence_source="acceptance-fixture"))


def _doc(name, *, organization_id=None, person_id=None, household_id=None, folder=_BIZ_FOLDER,
         folder_year=None, sub_folder=None, category=None, review_status="none",
         source_modified=None, status="active", deleted_at=None, archived=False):
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
            size_bytes=8192, sha256=uuid.uuid4().hex * 2,
            organization_id=organization_id, person_id=person_id, household_id=household_id,
            status=status, archived=archived, deleted_at=deleted_at, category=category,
            review_status=review_status, current_version=1, tags=tags,
        ).returning(documents.c.id)).scalar_one()
        ds = metadata.tables["document_sources"]
        c.execute(ds.insert().values(
            document_id=did, source_system="SharePoint", source_uri=web_url,
            source_path="/".join(parts), source_external_id=uuid.uuid4().hex, available=True))
    return did


def _principal():
    return Principal(0, "staff@e.test", "Staff", _CAPS)


def _fixture():
    """Sandoval Masonry LLC, owned by Elena Sandoval, who is also an individual client in a
    household — plus a second, unrelated business."""
    hid = _household("Sandoval Household")
    elena = _person("Elena", "Sandoval", hid)
    raul = _person("Raul", "Sandoval", hid)
    biz = _business("Sandoval Masonry LLC")
    _owns(elena, biz)
    biz_docs = {
        "return_2023": _doc("2023 Form 1120S.pdf", organization_id=biz, folder_year=2023,
                            category="tax_document", source_modified="2024-03-11T10:00:00Z"),
        "payroll": _doc("Q4 Payroll Summary.pdf", organization_id=biz, folder_year=2023,
                        sub_folder="Payroll", category="payroll",
                        source_modified="2024-01-15T10:00:00Z"),
        "operating": _doc("Operating Agreement.pdf", organization_id=biz,
                          category="legal_document", source_modified="2019-08-01T10:00:00Z"),
    }
    personal_docs = {
        "joint_1040": _doc("2023 Joint Form 1040.pdf", household_id=hid, folder=_IND_FOLDER,
                           folder_year=2023, category="tax_document"),
        "elena_k1": _doc("Elena K-1.pdf", person_id=elena, folder=_IND_FOLDER, folder_year=2023,
                         category="tax_document"),
    }
    other_biz = _business("Tremblay Logistics Inc")
    other_doc = _doc("Tremblay 1120.pdf", organization_id=other_biz, folder=_OTHER_BIZ_FOLDER,
                     folder_year=2023, category="tax_document")
    return hid, elena, raul, biz, biz_docs, personal_docs, other_biz, other_doc


def _entity_rows(biz):
    """The business's documents through the ownership-scoped read the workspace uses."""
    return client_documents(_principal(), "organization", biz, limit=500)


def _enriched(rows):
    """The SAME enrichment chain the Documents tab builds (client360.sections.documents), so a row a
    test inspects is the row staff actually see. A shorter chain here would test a shape no screen
    ever renders."""
    from app.services.client360.sections import (
        _attach_classification,
        _attach_ocr,
        _attach_source_refs,
        _attach_version_family,
    )
    return _attach_version_family(
        _attach_classification(_attach_ocr(_attach_source_refs(enrich_documents(rows)))))


def _entity_screen(biz):
    """The staff Documents screen over the business's own documents."""
    return build_screen(_enriched(_entity_rows(biz)), member_names={}, household_name=None)


def _person_rows(pid):
    return get_workspace(_principal(), person_id=pid)["sections"]["documents"]["documents"]


def _household_rows(hid):
    return get_household_workspace(_principal(), hid)["sections"]["documents"]["documents"]


def _by_name(rows):
    return {r.get("original_name"): r for r in rows}


# --- A. the business owns its documents ----------------------------------------------------------

def test_the_business_workspace_lists_the_entitys_documents():
    _hid, _e, _r, biz, biz_docs, _p, _ob, _od = _fixture()
    ws = get_business_workspace(biz)
    assert ws is not None
    assert {d["id"] for d in ws["documents"]} == set(biz_docs.values())
    assert ws["document_count"] == len(biz_docs)


def test_the_organization_scoped_read_returns_exactly_the_entitys_documents():
    _hid, _e, _r, biz, biz_docs, _p, _ob, _od = _fixture()
    assert {r["id"] for r in _entity_rows(biz)} == set(biz_docs.values())
    assert {r["id"] for r in documents_for_entity(_principal(), "organization", biz, limit=500)} \
        == set(biz_docs.values())


def test_an_organization_read_is_the_single_entity_read_and_never_a_person_union():
    """``client_documents`` unions person+household ONLY for those entity types. An organization
    falls through to the single-entity read, and must not acquire its owner's household file."""
    hid, _e, _r, biz, biz_docs, personal, _ob, _od = _fixture()
    seen = {r["id"] for r in _entity_rows(biz)}
    assert seen == set(biz_docs.values())
    assert not (set(personal.values()) & seen)
    assert hid not in {r.get("household_id") for r in _entity_rows(biz)}


def test_every_business_document_carries_the_entity_anchor_and_no_human_anchor():
    """The anchor columns are the whole safety story: a business document with a person or household
    id set would render on that client's page no matter what the business workspace shows."""
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for row in _entity_rows(biz):
        assert row["organization_id"] == biz
        assert row["person_id"] is None
        assert row["household_id"] is None


# --- B. the business's documents never attach to a person or household ---------------------------

def test_the_owners_client_page_does_not_show_the_business_file():
    """The named failure mode: the S-corp return filed onto the human being who owns the S-corp."""
    _hid, elena, _r, _biz, biz_docs, _p, _ob, _od = _fixture()
    seen = {r["id"] for r in _person_rows(elena)}
    assert not (set(biz_docs.values()) & seen)


def test_the_owners_household_page_does_not_show_the_business_file():
    hid, _e, _r, _biz, biz_docs, _p, _ob, _od = _fixture()
    assert not (set(biz_docs.values()) & {r["id"] for r in _household_rows(hid)})


def test_a_non_owner_household_member_does_not_acquire_the_business_file():
    """Raul owns nothing. If the business file reached him, filing would be following the surname."""
    _hid, _e, raul, _biz, biz_docs, _p, _ob, _od = _fixture()
    assert not (set(biz_docs.values()) & {r["id"] for r in _person_rows(raul)})


def test_the_person_document_service_also_excludes_the_business_file():
    """Asserted through the second, independent read as well — the count surface and the list
    surface must not disagree about whether a client owns a business's documents."""
    _hid, elena, _r, _biz, biz_docs, _p, _ob, _od = _fixture()
    assert not (set(biz_docs.values()) & {d["id"] for d in get_person_documents(elena)})


def test_the_owners_personal_documents_stay_with_the_owner():
    """Separation runs both ways: the K-1 and the joint 1040 are the family's, not the entity's."""
    hid, elena, _r, biz, _bd, personal, _ob, _od = _fixture()
    assert personal["elena_k1"] in {r["id"] for r in _person_rows(elena)}
    assert personal["joint_1040"] in {r["id"] for r in _household_rows(hid)}
    assert not (set(personal.values()) & {r["id"] for r in _entity_rows(biz)})


def test_one_business_never_sees_another_businesss_documents():
    _hid, _e, _r, biz, biz_docs, _p, other_biz, other_doc = _fixture()
    assert other_doc not in {r["id"] for r in _entity_rows(biz)}
    assert not (set(biz_docs.values()) & {r["id"] for r in _entity_rows(other_biz)})


def test_ownership_context_is_reported_without_moving_any_document():
    """The workspace names the owner and their household as CONTEXT. That is navigation, not
    filing — the document set is unchanged by it."""
    hid, elena, _r, biz, biz_docs, _p, _ob, _od = _fixture()
    ws = get_business_workspace(biz)
    assert [o["person_id"] for o in ws["owners"]] == [elena]
    assert hid in [h["household_id"] for h in ws["related_households"]]
    assert {d["id"] for d in ws["documents"]} == set(biz_docs.values())


# --- C. the row says "business" -------------------------------------------------------------------

def test_every_business_row_is_labelled_as_filed_at_the_business():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for row in _entity_screen(biz)["rows"]:
        assert row["related_to"]["kind"] == "organization"
        assert row["related_to"]["label"] == "Business"
        assert row["related_to"]["id"] == biz
        # And never the unfiled label — an entity document has an owner.
        assert row["related_to"]["kind"] != "none"


def test_no_business_row_is_flagged_as_having_no_owner():
    """``owner_missing`` is the Needs Review reason for an unanchored document. An entity-anchored
    document is anchored, so it must not appear on that queue."""
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for row in _entity_screen(biz)["rows"]:
        assert "owner_missing" not in {r["key"] for r in row["actionable"]}


# --- D. filing tree, year, type, name, source -----------------------------------------------------

def test_business_documents_are_filed_under_the_business_folder():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for row in enrich_documents(_entity_rows(biz)):
        folder = row["source_folder"] or ""
        assert "SANDOVAL MASONRY LLC" in folder
        assert "/Business/" in folder
        assert "/Individual/" not in folder, "an entity document filed in the individual tree"


def test_the_year_folder_and_subfolder_are_part_of_the_business_filing_location():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    rows = _by_name(enrich_documents(_entity_rows(biz)))
    assert rows["2023 Form 1120S.pdf"]["source_folder"].endswith("/2023")
    assert rows["Q4 Payroll Summary.pdf"]["source_folder"].endswith("/2023/Payroll")


def test_the_entitys_tax_year_is_derived_from_its_own_evidence():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    rows = _by_name(enrich_documents(_entity_rows(biz)))
    assert rows["2023 Form 1120S.pdf"]["tax_year"] == 2023
    assert rows["Q4 Payroll Summary.pdf"]["tax_year"] == 2023      # folder-only evidence
    assert rows["Q4 Payroll Summary.pdf"]["tax_year_inferred"] is True
    assert rows["Operating Agreement.pdf"]["tax_year"] is None     # no year, and none invented


def test_business_categories_and_types_reach_the_screen():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    rows = _by_name(enrich_documents(_entity_rows(biz)))
    assert rows["2023 Form 1120S.pdf"]["category"] == "tax_document"
    assert rows["Q4 Payroll Summary.pdf"]["category"] == "payroll"
    assert all(r["type_text"] for r in _entity_screen(biz)["rows"])


def test_every_business_row_shows_a_readable_name():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for row in enrich_documents(_entity_rows(biz)):
        assert row["name"] and not row["name"].startswith("http")
        assert _TAG not in row["name"]
        assert row["name"] != f"Document {row['id']}"


def test_the_business_workspace_names_documents_through_the_canonical_naming_layer():
    """The workspace builds its own document rows; they must use the same display-name resolution as
    every other surface rather than showing a raw stored name."""
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for d in get_business_workspace(biz)["documents"]:
        assert d["name"] and _TAG not in d["name"]
        assert not d["name"].startswith("/x/")


def test_every_business_row_offers_a_working_download_and_source_path():
    from app.services.document_sources import sources_for_document
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    for row in enrich_documents(_entity_rows(biz)):
        assert row["download_url"] == f"/documents/{row['id']}/download"
        sources = sources_for_document(row["id"])
        assert sources, "a synced entity document keeps its source reference"
        assert sources[0]["source_uri"].startswith("https://")
        assert sources[0]["available"] is True


# --- E. lifecycle ---------------------------------------------------------------------------------

def test_deleted_and_archived_business_documents_are_suppressed():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    _doc("Superseded Operating Agreement.pdf", organization_id=biz, status="deleted",
         deleted_at=datetime.now(UTC))
    _doc("Half Deleted Payroll.pdf", organization_id=biz, status="active",
         deleted_at=datetime.now(UTC))
    _doc("Archived Minutes.pdf", organization_id=biz, archived=True)
    names = {r["original_name"] for r in _entity_rows(biz)}
    assert not ({"Superseded Operating Agreement.pdf", "Half Deleted Payroll.pdf",
                 "Archived Minutes.pdf"} & names)
    assert get_business_workspace(biz)["document_count"] == 3


def test_a_business_document_marked_for_review_is_reported_on_the_entity_screen():
    _hid, _e, _r, biz, _bd, _p, _ob, _od = _fixture()
    did = _doc("Unsigned 1120S.pdf", organization_id=biz, folder_year=2023,
               review_status="pending")
    screen = _entity_screen(biz)
    assert screen["needs_review_count"] == 1
    shaped = next(r for r in screen["rows"] if r["id"] == did)
    assert shaped["needs_review"] is True
    assert "review_requested" in {r["key"] for r in shaped["actionable"]}
