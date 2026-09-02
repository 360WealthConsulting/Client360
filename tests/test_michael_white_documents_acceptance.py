"""Michael White — the staff-usability acceptance test for the client Documents experience.

Modelled on the real production shape found by the filing-readiness audit (person 3824 in household 1,
"White Household", spouse Debra, 175 active SharePoint-backed documents ALL linked at the household
rather than the person, and 8 soft-deleted documents that were still rendering on his page).

The fixture reproduces that shape on the disposable test database — never production:

* a household with two members, documents anchored at the HOUSEHOLD (not the person);
* a soft-deleted document (``status='deleted'`` + ``deleted_at``) that must never render;
* an inconsistently-marked document (``deleted_at`` set but status left ``active``) — production has
  49 of these, and a ``status``-only filter leaks every one;
* an archived document, which is a SEPARATE lifecycle state and must stay suppressed on its own terms;
* documents filed the way SharePoint actually files them, so tax-year and category evidence is real;
* two canonical rows carrying the SAME SharePoint item id — successive syncs of one file.

Everything asserted here is a read path. No test writes to production, and none of these tests writes
a tax year or an owner anywhere.
"""
import uuid

import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, people
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.documents import get_document, get_person_documents

_TAG = "MWACC"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})
_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_CLIENT_FOLDER = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual/WHITE,%20MICHAEL%20AND%20DEBRA"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            c.execute(documents.delete().where(documents.c.stored_name.like(f"%{_TAG}%")))
            if pids:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _household(name=f"{_TAG} White Household"):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=name)
                         .returning(households.c.id)).scalar_one()


def _person(first, last, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}", active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _doc(name, *, person_id=None, household_id=None, status="active", deleted_at=None,
         archived=False, category=None, folder_year=None, sha=None, item_id=None,
         source_modified=None, sub_folder=None):
    """One canonical document filed the way SharePoint files it (year folder, optional subfolder)."""
    parts = [_CLIENT_FOLDER]
    if folder_year:
        parts.append(str(folder_year))
    if sub_folder:
        parts.append(sub_folder.replace(" ", "%20"))
    web_url = f"{_SITE}/{'/'.join(parts)}/{name.replace(' ', '%20')}"
    tags = {"source_system": "SharePoint", "web_url": web_url,
            "sharepoint_client_folder": "WHITE, MICHAEL AND DEBRA"}
    if source_modified:
        tags["source_modified"] = source_modified
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{name}", storage_provider="Client360 Local", size_bytes=100,
            sha256=sha or uuid.uuid4().hex * 2,
            person_id=person_id, household_id=household_id, status=status, archived=archived,
            deleted_at=deleted_at, category=category, review_status="none", current_version=1,
            tags=tags).returning(documents.c.id)).scalar_one()
        if item_id:
            from app.db import metadata
            ds = metadata.tables["document_sources"]
            c.execute(ds.insert().values(
                document_id=did, source_system="SharePoint", source_uri=web_url,
                source_path="/".join(parts), source_external_id=item_id, available=True))
    return did


def _principal(caps=_CAPS):
    return Principal(0, "staff@e.test", "Staff", caps)


def _white_household():
    """The production shape: two members, every document anchored at the HOUSEHOLD."""
    from datetime import UTC, datetime
    hid = _household()
    michael = _person("Michael", "White", household_id=hid)
    debra = _person("Debra", "White", household_id=hid)
    ids = {
        "return_2021": _doc("2021 Tax Return Documents.pdf", household_id=hid,
                            category="tax_document", folder_year=2021,
                            source_modified="2022-03-14T10:00:00Z"),
        "w2_2022": _doc("Debs 2022 W2.pdf", household_id=hid, category="tax_document",
                        folder_year=2022, source_modified="2023-02-01T10:00:00Z"),
        "statement": _doc("Schwab Transfer Statement.pdf", household_id=hid, category="statement",
                          sub_folder="401 Statements", source_modified="2024-01-09T10:00:00Z"),
        # Filed in a 2020 folder with no year in the filename — folder-only year evidence.
        "supporting": _doc("Supporting Docs.pdf", household_id=hid, folder_year=2020,
                           sub_folder="Supporting Documents"),
        # Soft-deleted exactly as document_platform.service.soft_delete writes it.
        "deleted": _doc("Deleted Return.pdf", household_id=hid, category="tax_document",
                        status="deleted", deleted_at=datetime.now(UTC), folder_year=2021),
        # deleted_at set, status left 'active' — the 49-row production inconsistency.
        "half_deleted": _doc("Half Deleted.pdf", household_id=hid, status="active",
                             deleted_at=datetime.now(UTC), folder_year=2021),
        "archived": _doc("Archived Note.pdf", household_id=hid, archived=True),
    }
    return hid, michael, debra, ids


# --- A. deleted-document suppression -----------------------------------------------------------

def test_person_view_excludes_soft_deleted_documents():
    """Criterion 1 — the leak the audit found on Michael White's page."""
    _hid, michael, _debra, ids = _white_household()
    names = {d["original_name"] for d in get_person_documents(michael)}
    assert "Deleted Return.pdf" not in names
    assert "Half Deleted.pdf" not in names, "deleted_at alone must also suppress a row"
    assert "Archived Note.pdf" not in names, "archived stays suppressed on its own terms"
    assert "2021 Tax Return Documents.pdf" in names


def test_workspace_documents_tab_excludes_soft_deleted_documents():
    _hid, michael, _debra, _ids = _white_household()
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    originals = {d.get("original_name") for d in docs}
    assert "Deleted Return.pdf" not in originals
    assert "Half Deleted.pdf" not in originals


def test_household_workspace_excludes_soft_deleted_documents():
    from app.services.client360.household import get_household_workspace
    hid, _michael, _debra, _ids = _white_household()
    docs = get_household_workspace(_principal(), hid)["sections"]["documents"]["documents"]
    originals = {d.get("original_name") for d in docs}
    assert "Deleted Return.pdf" not in originals
    assert "Half Deleted.pdf" not in originals


def test_deleted_document_is_not_deliverable_but_is_recoverable_by_admin():
    """A deleted document must not be downloadable, yet must stay reachable for a recovery view."""
    _hid, _michael, _debra, ids = _white_household()
    assert get_document(ids["deleted"]) is None
    assert get_document(ids["deleted"], include_deleted=True)["id"] == ids["deleted"]
    assert get_document(ids["return_2021"])["id"] == ids["return_2021"]


# --- B/C. household visibility, row quality, findability ---------------------------------------

def test_household_documents_appear_under_the_person_automatically():
    """Criterion 2 — every document is anchored at the household; none at Michael directly."""
    _hid, michael, _debra, _ids = _white_household()
    rows = get_person_documents(michael)
    assert rows, "household documents must render on the member's page"
    assert all(r["person_id"] is None for r in rows)
    assert all(r["household_id"] is not None for r in rows)


def test_both_spouses_see_the_same_household_documents():
    _hid, michael, debra, _ids = _white_household()
    assert ({d["id"] for d in get_person_documents(michael)}
            == {d["id"] for d in get_person_documents(debra)})


def test_every_visible_row_carries_the_staff_facing_fields():
    """Criterion 6/7 — display name from the naming layer, provenance secondary."""
    _hid, michael, _debra, _ids = _white_household()
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    row = next(d for d in docs if d.get("original_name") == "2021 Tax Return Documents.pdf")
    assert row["name"] and not row["name"].startswith("http")     # a name, never a URL or an id
    assert row["document_type"]                                   # type/category label present
    assert row["tax_year"] == 2021
    assert row["source_systems"] == ["SharePoint"] or row["source"]
    assert "ocr_label" in row                                     # OCR state, plain language
    assert row["source_modified_at"] == "2022-03-14T10:00:00Z"    # the document's own date
    # Raw source internals stay available but are not the row's identity.
    assert row["name"] != row.get("source_path")


def test_text_search_fields_locate_a_document():
    """Criterion 3 — the fields the on-page filter searches actually carry the terms."""
    _hid, michael, _debra, _ids = _white_household()
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]

    def haystack(d):
        return " ".join(str(x) for x in (d.get("name"), d.get("original_name"),
                                         d.get("document_type"), d.get("category"),
                                         d.get("tax_year"))).lower()

    assert [d for d in docs if "schwab" in haystack(d)], "searching 'schwab' must find the statement"
    assert [d for d in docs if "w2" in haystack(d)]


def test_category_filter_partitions_the_documents():
    """Criterion 4."""
    _hid, michael, _debra, _ids = _white_household()
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    tax = [d for d in docs if d.get("category") == "tax_document"]
    statements = [d for d in docs if d.get("category") == "statement"]
    assert len(tax) >= 2 and len(statements) == 1
    assert {d["document_type"] for d in docs} != {None}, "the type facet must have real values"


def test_tax_year_filter_partitions_the_documents():
    """Criterion 5 — a year is derived where evidence is strong, and it filters."""
    _hid, michael, _debra, _ids = _white_household()
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    years = {d["tax_year"] for d in docs if d["tax_year"]}
    assert {2020, 2021, 2022} <= years
    assert len([d for d in docs if d["tax_year"] == 2021]) >= 1
    assert len([d for d in docs if d["tax_year"] == 2020]) == 1     # folder-only evidence


def test_derived_tax_years_are_labelled_as_derived_and_not_written():
    """A derived year must be visibly provisional, and must not be persisted."""
    _hid, michael, _debra, ids = _white_household()
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    row = next(d for d in docs if d.get("original_name") == "Supporting Docs.pdf")
    assert row["tax_year"] == 2020
    assert row["tax_year_inferred"] is True
    assert row["tax_year_evidence"], "staff must be able to see why"
    with engine.connect() as c:
        tags = c.execute(select(documents.c.tags)
                         .where(documents.c.id == ids["supporting"])).scalar_one()
    assert "tax_year" not in (tags or {}), "inference must never write a tax year"


# --- D. version presentation -------------------------------------------------------------------

def test_repeated_syncs_of_one_source_file_collapse_to_the_latest():
    """Criterion 8 — the SAME SharePoint item re-ingested is one document with history, not N rows."""
    hid = _household()
    michael = _person("Michael", "White", household_id=hid)
    item = "01ATBOW3NVULPKQSTT4JE3TLRZBAOV25UY"
    older = _doc("2021 Taxes In-Take.xlsx", household_id=hid, item_id=item, sha="a" * 64,
                 source_modified="2026-08-17T13:55:52Z")
    newer = _doc("2021 Taxes In-Take.xlsx", household_id=hid, item_id=item, sha="b" * 64,
                 source_modified="2026-08-31T12:26:32Z")
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    by_id = {d["id"]: d for d in docs}
    assert by_id[newer]["is_current_version"] is True
    assert by_id[older]["is_current_version"] is False
    assert by_id[older]["superseded_by"] == newer
    assert by_id[newer]["version_family_size"] == 2
    # The family stays together so expanding it reveals history in place.
    order = [d["id"] for d in docs if d["id"] in (older, newer)]
    assert order == [newer, older]


def test_same_filename_from_different_source_items_never_collapses():
    """The Michael White case: 'Signed 8879s.pdf' exists once per tax year, as separate SharePoint
    items. Collapsing by name would hide a real document, so only source identity may group."""
    hid = _household()
    michael = _person("Michael", "White", household_id=hid)
    a = _doc("Signed 8879s.pdf", household_id=hid, item_id="01ATBOW3LKEEZDTGGYYVFJ5OQXFDHTKDWK",
             sha="c" * 64, folder_year=2021)
    b = _doc("Signed 8879s.pdf", household_id=hid, item_id="01ATBOW3NFJXBRQA4TXRGKD3PJELAHFMGM",
             sha="d" * 64, folder_year=2022)
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    by_id = {d["id"]: d for d in docs}
    assert by_id[a]["is_current_version"] is True and by_id[b]["is_current_version"] is True
    assert by_id[a]["version_family_size"] == 1 and by_id[b]["version_family_size"] == 1


def test_documents_without_a_source_item_id_stand_alone():
    """No source identity means no grouping — never guess that two rows are the same file."""
    hid = _household()
    michael = _person("Michael", "White", household_id=hid)
    x = _doc("Cash Flow.pdf", household_id=hid, sha="e" * 64)
    y = _doc("Cash Flow.pdf", household_id=hid, sha="f" * 64)
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    by_id = {d["id"]: d for d in docs}
    assert by_id[x]["version_family_size"] == 1 and by_id[y]["version_family_size"] == 1
    assert by_id[x]["is_current_version"] and by_id[y]["is_current_version"]


def test_unresolvable_lookalikes_are_flagged_not_merged():
    """PART 4 — same filename, different content, no shared source item: retain BOTH and flag the
    presentation issue. Michael White has nine such pairs, every one a different size."""
    hid = _household()
    michael = _person("Michael", "White", household_id=hid)
    a = _doc("UC Death Certificate.pdf", household_id=hid, sha="1" * 64)
    b = _doc("UC Death Certificate.pdf", household_id=hid, sha="2" * 64)
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    by_id = {d["id"]: d for d in docs}
    assert a in by_id and b in by_id, "both documents are retained"
    assert by_id[a]["needs_version_review"] and by_id[b]["needs_version_review"]
    assert by_id[a]["version_family_size"] == 1, "they are NOT merged into a version family"
    assert by_id[a]["is_current_version"] and by_id[b]["is_current_version"]
    assert "different content" in by_id[a]["version_review_reason"]


def test_a_resolved_version_family_is_not_also_flagged_for_review():
    """When source identity DOES resolve them, no review flag is raised."""
    hid = _household()
    michael = _person("Michael", "White", household_id=hid)
    item = "01ATBOW3NVULPKQSTT4JE3TLRZBAOV25UY"
    older = _doc("In-Take.xlsx", household_id=hid, item_id=item, sha="3" * 64,
                 source_modified="2026-08-17T13:55:52Z")
    newer = _doc("In-Take.xlsx", household_id=hid, item_id=item, sha="4" * 64,
                 source_modified="2026-08-31T12:26:32Z")
    docs = get_workspace(_principal(), person_id=michael)["sections"]["documents"]["documents"]
    by_id = {d["id"]: d for d in docs}
    assert by_id[newer]["needs_version_review"] is False
    assert by_id[older]["is_current_version"] is False
