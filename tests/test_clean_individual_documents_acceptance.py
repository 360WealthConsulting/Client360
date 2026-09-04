"""Archetype 2 — the CLEAN INDIVIDUAL client's Documents experience.

The Michael White module (``test_michael_white_documents_acceptance``) covers the hard case: a
household whose entire file is anchored above the person, with soft-delete inconsistencies and
re-synced look-alikes. This module covers the case that must be boring, and stays boring:

    one person, no household, every document anchored directly on them.

That shape is worth its own acceptance module for two reasons. First, the person+household union in
``document_platform.relationships.client_documents`` has a branch for a person with NO household —
``_client_anchors`` contributes no household id, so the household half of the OR disappears — and a
union that silently widened there would attach other clients' paperwork to a single filer. Second,
"correct" for this archetype is not "some documents render": it is that every visible cell (owner,
filing location, tax year, type, display name, source) says something the row can actually support.

Every assertion is a READ. Nothing here writes an owner, a tax year, a category or a display name,
and the fixture lives entirely on the disposable test database.

Foreign-client leakage is asserted in BOTH directions against three neighbours the union could
plausibly reach — an unrelated individual, an unrelated household, and a business — because a
containment bug is only visible from the side that gains the document.
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
)
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.documents import get_person_documents

_TAG = "CLEANIND"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})
_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_ROOT = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual"
#: This client's own SharePoint folder, and a NEIGHBOURING client's. Both are real filing-tree
#: shapes; the second exists so "filed under the right client" is a comparison, not an assertion
#: about a string nobody else could have produced.
_FOLDER = f"{_ROOT}/NAKAMURA,%20PRIYA"
_OTHER_FOLDER = f"{_ROOT}/OKONKWO,%20DANIEL"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(f"%{_TAG}%"))))
            docs = list(c.scalars(select(documents.c.id)
                                  .where(documents.c.stored_name.like(f"%{_TAG}%"))))
            if docs:
                ds = metadata.tables["document_sources"]
                c.execute(ds.delete().where(ds.c.document_id.in_(docs)))
                c.execute(documents.delete().where(documents.c.id.in_(docs)))
            if ents:
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

def _person(first, last, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}", active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _household(name):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{_TAG} {name}")
                         .returning(households.c.id)).scalar_one()


def _business(name):
    with engine.begin() as c:
        return c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"{name} {_TAG}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()


def _doc(name, *, person_id=None, household_id=None, organization_id=None, folder=_FOLDER,
         folder_year=None, sub_folder=None, category=None, review_status="none",
         source_modified=None, item_id=None, available=True, with_source=True, status="active",
         deleted_at=None, archived=False, display_name=None):
    """One canonical document, filed the way the SharePoint connector files it.

    ``with_source`` distinguishes a synced document from a direct upload: a document with NO source
    references is not a document whose source is missing, and the two must not be conflated (see
    ``documents_screen._no_available_source``).
    """
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
            size_bytes=2048, sha256=uuid.uuid4().hex * 2, display_name=display_name,
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            status=status, archived=archived, deleted_at=deleted_at, category=category,
            review_status=review_status, current_version=1, tags=tags,
        ).returning(documents.c.id)).scalar_one()
        if with_source:
            ds = metadata.tables["document_sources"]
            c.execute(ds.insert().values(
                document_id=did, source_system="SharePoint", source_uri=web_url,
                source_path="/".join(parts), source_external_id=item_id or uuid.uuid4().hex,
                available=available))
    return did


def _principal():
    return Principal(0, "staff@e.test", "Staff", _CAPS)


def _fixture():
    """One clean individual, plus the three neighbours a scope bug could reach.

    The neighbours are deliberately of all three anchor kinds — person, household, organization —
    because ``client_documents`` builds a different OR arm for each.
    """
    priya = _person("Priya", "Nakamura")
    ids = {
        # Filed in a year folder AND named with the same year: the unambiguous case.
        "return_2023": _doc("2023 Form 1040.pdf", person_id=priya, folder_year=2023,
                            category="tax_document", source_modified="2024-04-02T09:00:00Z"),
        # Year evidence from the FOLDER only — the filename carries none.
        "w2_2022": _doc("W2 Employer.pdf", person_id=priya, folder_year=2022,
                        category="tax_document", source_modified="2023-02-14T09:00:00Z"),
        # No year anywhere, and a non-tax category: a perfectly ordinary document.
        "statement": _doc("Brokerage Statement.pdf", person_id=priya, sub_folder="Statements",
                          category="statement", source_modified="2024-06-01T09:00:00Z"),
        # Filename and folder name DIFFERENT years — the evidence disagrees, so no year is filed
        # and none is proposed.
        "conflict": _doc("2021 Amended Return.pdf", person_id=priya, folder_year=2023,
                         category="tax_document", source_modified="2024-05-01T09:00:00Z"),
    }
    neighbours = {
        "person": _doc("Daniel Okonkwo 1040.pdf", person_id=_person("Daniel", "Okonkwo"),
                       folder=_OTHER_FOLDER, folder_year=2023, category="tax_document"),
    }
    other_hh = _household("Okonkwo Household")
    _person("Grace", "Okonkwo", household_id=other_hh)
    neighbours["household"] = _doc("Okonkwo Joint Organizer.pdf", household_id=other_hh,
                                   folder=_OTHER_FOLDER, folder_year=2023)
    neighbours["organization"] = _doc("Okonkwo Holdings 1120S.pdf",
                                      organization_id=_business("Okonkwo Holdings LLC"),
                                      folder=_OTHER_FOLDER, folder_year=2023,
                                      category="tax_document")
    return priya, ids, neighbours


def _rows(person_id):
    """The Documents-tab rows for this client (the enriched view model, pre-screen)."""
    return get_workspace(_principal(), person_id=person_id)["sections"]["documents"]["documents"]


def _screen(person_id):
    """The staff Documents SCREEN — shaped rows, with related_to / review reasons resolved."""
    return get_workspace(_principal(), person_id=person_id)["sections"]["documents"]["screen"]


def _by_name(rows):
    return {r.get("original_name"): r for r in rows}


# --- A. correct owner and correct client scope ---------------------------------------------------

def test_every_document_is_anchored_on_the_person_and_nowhere_else():
    """Correct owner: a single filer's documents hang off the person, with no household anchor.

    ``anchor`` is the read-side tag ``client_documents`` writes onto the row so the UI can say WHERE
    a document is filed. For this archetype it must be "person" on every row — a household or
    "related" anchor here would mean the union reached something this client does not own.
    """
    priya, ids, _n = _fixture()
    rows = _rows(priya)
    assert {r["id"] for r in rows} == set(ids.values())
    assert all(r["person_id"] == priya for r in rows)
    assert all(r["household_id"] is None for r in rows)
    assert all(r["organization_id"] is None for r in rows)


def test_the_person_view_and_the_workspace_agree_on_the_document_set():
    """``get_person_documents`` and the Documents tab must not disagree about what this client has.

    They are different reads — the first is the person+household clause in ``services.documents``,
    the second the union in ``document_platform.relationships`` — and a client whose count and list
    disagree is the exact defect ``person_documents_clause`` was written to end.
    """
    priya, ids, _n = _fixture()
    assert {d["id"] for d in get_person_documents(priya)} == set(ids.values())


def test_the_row_says_the_document_is_filed_on_this_person_by_name():
    """Correct scope, stated in staff language: Related To names the client, not "Household"."""
    priya, _ids, _n = _fixture()
    screen = _screen(priya)
    labels = {r["related_to"]["kind"] for r in screen["rows"]}
    assert labels == {"person"}
    assert all(r["related_to"]["id"] == priya for r in screen["rows"])
    assert all("Nakamura" in r["related_to"]["label"] for r in screen["rows"])
    # And never the placeholder the label falls back to when a name cannot be resolved.
    assert all(r["related_to"]["label"] != f"Person {priya}" for r in screen["rows"])


# --- B. no foreign-client leakage ----------------------------------------------------------------

def test_no_other_clients_documents_reach_this_client():
    """A person-anchored read must not pick up a neighbour's person, household or business file."""
    priya, _ids, neighbours = _fixture()
    seen = {r["id"] for r in _rows(priya)}
    for kind, did in neighbours.items():
        assert did not in seen, f"{kind}-anchored document of another client leaked in"


def test_this_clients_documents_do_not_reach_anyone_else():
    """Leakage is only visible from the side that GAINS the document, so both sides are checked."""
    priya, ids, _n = _fixture()
    with engine.connect() as c:
        other = c.execute(select(people.c.id).where(
            people.c.full_name.like(f"%Okonkwo%{_TAG}%"),
            people.c.id != priya)).scalars().first()
    assert other is not None
    assert not (set(ids.values()) & {r["id"] for r in _rows(other)})


def test_a_business_document_never_attaches_to_the_individual():
    """Archetype 4's failure mode, asserted from archetype 2's side: an organization-anchored
    document has no person or household anchor and must never surface on an individual's page."""
    priya, _ids, neighbours = _fixture()
    rows = _rows(priya)
    assert neighbours["organization"] not in {r["id"] for r in rows}
    assert all(not r.get("organization_id") for r in rows)


# --- C. correct filing-tree placement ------------------------------------------------------------

def test_every_row_carries_the_filing_location_it_was_synced_from():
    """Filing tree: the row reports the SharePoint folder the document actually lives in.

    ``source_folder`` is derived read-only from the captured ``web_url`` (document_tax_year
    .source_path_for), so it is the source's own hierarchy rather than a path this app invented.
    """
    priya, _ids, _n = _fixture()
    for row in _rows(priya):
        assert row["source_folder"], f"{row['original_name']} has no filing location"
        assert "NAKAMURA, PRIYA" in row["source_folder"]
        assert "OKONKWO" not in row["source_folder"]


def test_the_year_folder_is_part_of_the_filing_location():
    priya, _ids, _n = _fixture()
    rows = _by_name(_rows(priya))
    assert rows["2023 Form 1040.pdf"]["source_folder"].endswith("/2023")
    assert rows["Brokerage Statement.pdf"]["source_folder"].endswith("/Statements")


# --- D. correct tax year -------------------------------------------------------------------------

def test_a_year_is_derived_only_where_the_evidence_supports_one():
    priya, _ids, _n = _fixture()
    rows = _by_name(_rows(priya))
    assert rows["2023 Form 1040.pdf"]["tax_year"] == 2023
    assert rows["W2 Employer.pdf"]["tax_year"] == 2022          # folder-only evidence
    assert rows["Brokerage Statement.pdf"]["tax_year"] is None  # no evidence, no guess


def test_a_derived_year_is_labelled_as_derived_and_carries_its_evidence():
    priya, _ids, _n = _fixture()
    row = _by_name(_rows(priya))["W2 Employer.pdf"]
    assert row["tax_year_inferred"] is True
    assert row["tax_year_evidence"], "staff must be able to see why the year was proposed"


def test_conflicting_year_evidence_produces_no_year_and_reaches_review():
    """When the filename and the folder name different years, the platform files neither.

    This is the acceptance criterion behind "unresolved items stay unresolved": a disagreement is
    surfaced as an ACTIONABLE review reason, never averaged into a plausible-looking year.
    """
    priya, _ids, _n = _fixture()
    row = _by_name(_rows(priya))["2021 Amended Return.pdf"]
    assert row["tax_year"] is None
    assert row["tax_year_confidence"] == "conflict"
    shaped = _by_name(_screen(priya)["rows"])["2021 Amended Return.pdf"]
    assert "tax_year_conflict" in {r["key"] for r in shaped["actionable"]}


def test_no_derived_year_is_ever_written_back_to_the_document():
    priya, ids, _n = _fixture()
    _rows(priya)                                    # the read that does the inferring
    with engine.connect() as c:
        tags = c.execute(select(documents.c.tags)
                         .where(documents.c.id == ids["w2_2022"])).scalar_one()
    assert "tax_year" not in (tags or {}) and "year" not in (tags or {})


# --- E. correct document type / category ---------------------------------------------------------

def test_the_filed_category_survives_to_the_row():
    priya, _ids, _n = _fixture()
    rows = _by_name(_rows(priya))
    assert rows["2023 Form 1040.pdf"]["category"] == "tax_document"
    assert rows["Brokerage Statement.pdf"]["category"] == "statement"


def test_every_row_has_a_type_the_type_facet_can_reach():
    """A row whose Type cell renders "—" is unreachable by the type filter, which is what put the
    stored category in as the last-resort type in ``_attach_classification``."""
    priya, _ids, _n = _fixture()
    screen = _screen(priya)
    assert all(r["type_text"] for r in screen["rows"])
    assert screen["types"], "the type facet must offer real values"


def test_a_derived_type_is_declared_as_a_proposal():
    """A type read off the filename is INCOMPLETE metadata, not a filed fact — and says so."""
    priya, _ids, _n = _fixture()
    for row in _screen(priya)["rows"]:
        if row["type_derived"]:
            assert "type_missing" in {r["key"] for r in row["incomplete"]}


# --- F. usable display name ----------------------------------------------------------------------

def test_every_row_shows_a_name_a_person_can_read():
    """Not a URL, not a storage path, not the internal stored name, and never "Document <id>"."""
    priya, _ids, _n = _fixture()
    for row in _rows(priya):
        name = row["name"]
        assert name and not name.startswith("http")
        assert name != row.get("source_path")
        assert not name.startswith("/x/")
        assert name != f"Document {row['id']}"
        assert _TAG not in name, "the internal stored name must never be the display name"


def test_a_canonical_display_name_wins_over_the_original_filename():
    """When the naming layer HAS set a display name, that is what staff see — and the original
    filename stays on the row so provenance is never hidden."""
    priya, _ids, _n = _fixture()
    did = _doc("scan0007.pdf", person_id=priya, folder_year=2023, category="tax_document",
               display_name="2023 Schedule C")
    row = next(r for r in _rows(priya) if r["id"] == did)
    assert row["name"] == "2023 Schedule C"
    assert row["original_name"] == "scan0007.pdf"


# --- G. source / open / download path ------------------------------------------------------------

def test_every_row_offers_a_download_path_that_points_at_itself():
    priya, _ids, _n = _fixture()
    for row in _rows(priya):
        assert row["download_url"] == f"/documents/{row['id']}/download"


def test_every_synced_row_keeps_a_resolvable_link_back_to_its_source():
    priya, _ids, _n = _fixture()
    for row in _rows(priya):
        assert row["source_systems"] == ["SharePoint"]
        assert row["sources"], "a synced document must keep its source reference"
        for src in row["sources"]:
            assert src["source_uri"].startswith("https://")
            assert src["source_external_id"]
            assert src["available"] is True


def test_a_source_the_system_says_is_gone_is_reported_not_hidden():
    """``available`` is a RECORDED state (``document_sources.mark_source_unavailable``), so a missing
    copy becomes an actionable review reason while the canonical record is kept."""
    priya, _ids, _n = _fixture()
    did = _doc("Lost Original.pdf", person_id=priya, folder_year=2023, available=False)
    shaped = next(r for r in _screen(priya)["rows"] if r["id"] == did)
    assert "source_missing" in {r["key"] for r in shaped["actionable"]}
    assert did in {r["id"] for r in _rows(priya)}, "the canonical record is retained"


def test_a_direct_upload_with_no_source_is_not_reported_as_a_missing_source():
    """Absence of a source is not a missing source — flagging every upload would make the reason
    meaningless."""
    priya, _ids, _n = _fixture()
    did = _doc("Uploaded By Staff.pdf", person_id=priya, with_source=False)
    shaped = next(r for r in _screen(priya)["rows"] if r["id"] == did)
    assert "source_missing" not in {r["key"] for r in shaped["actionable"]}


# --- H. lifecycle suppression and review state ---------------------------------------------------

def test_deleted_and_archived_documents_never_render_for_a_single_filer():
    """The same safety pair the household archetype asserts, on the person-anchored path."""
    priya, _ids, _n = _fixture()
    _doc("Deleted.pdf", person_id=priya, status="deleted", deleted_at=datetime.now(UTC))
    _doc("Half Deleted.pdf", person_id=priya, status="active", deleted_at=datetime.now(UTC))
    _doc("Archived.pdf", person_id=priya, archived=True)
    names = {r["original_name"] for r in _rows(priya)}
    assert not ({"Deleted.pdf", "Half Deleted.pdf", "Archived.pdf"} & names)


def test_an_ordinary_document_is_not_dragged_onto_the_review_worklist():
    """A clean individual's file must read as clean: nothing here carries an unsettled review
    status, so the Needs Review count is zero and the number keeps its meaning."""
    priya, _ids, _n = _fixture()
    assert _screen(priya)["needs_review_count"] == 0


def test_a_document_marked_for_review_is_counted_and_filterable():
    priya, _ids, _n = _fixture()
    did = _doc("Question On This.pdf", person_id=priya, folder_year=2023, review_status="pending")
    screen = _screen(priya)
    assert screen["needs_review_count"] == 1
    shaped = next(r for r in screen["rows"] if r["id"] == did)
    assert shaped["needs_review"] is True
    assert shaped["status"]["label"] == "Pending review"
    assert "review_requested" in {r["key"] for r in shaped["actionable"]}
