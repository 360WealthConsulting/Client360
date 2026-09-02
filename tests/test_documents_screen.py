"""The staff Client/Household Documents screen, and the soft-delete boundary underneath it.

Two things are covered here, and the first is the reason the second is trustworthy.

**Deleted documents must not leak.** A soft delete stamps TWO columns — ``status='deleted'`` and
``deleted_at`` — and consumers used to filter on whichever one their author remembered. That is not
hypothetical: the production census found 49 documents carrying ``deleted_at`` with a ``status``
that was never moved, because the merge/recovery paths write the pair in separate statements. Every
read path a user can reach is asserted against BOTH half-deleted shapes, so a future consumer that
checks only one column fails a test rather than showing a client a document they deleted.

**The screen shows what it says it shows.** Search, the category tabs, the year filter, the
Related To filter and the Needs Review worklist are exercised against rows whose values are
DERIVED rather than filed — which is the real production shape (the White household has NULL
classification on all 291 documents) and therefore the case that has to work.

Temp rows only, tagged and removed by the fixture.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, people
from app.security.models import Principal
from app.services.client360 import documents_screen
from app.services.document_platform.lifecycle import active_documents_clause, is_active
from app.services.document_platform.relationships import (
    client_documents,
    documents_for_entity,
)

_TAG = f"DOCSCR{uuid.uuid4().hex[:6]}"
_CAPS = frozenset({"client.read", "documents.view", "documents.edit", "record.read_all",
                   "record.write_all", "timeline.read", "tax.read"})
STAFF = Principal(1, "staff@t", "Staff", _CAPS)


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            hids = list(c.scalars(select(households.c.id).where(households.c.name.like(f"%{_TAG}%"))))
            c.execute(documents.delete().where(documents.c.stored_name.like(f"%{_TAG}%")))
            if pids:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            if hids:
                c.execute(delete(households).where(households.c.id.in_(hids)))
    _wipe()
    yield
    _wipe()


# --- fixtures ----------------------------------------------------------------

def _household(name="White"):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{name} {_TAG}")
                         .returning(households.c.id)).scalar_one()


def _person(first, last, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}",
            active=True, household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _doc(name, *, person_id=None, household_id=None, status="active", deleted_at=None,
         category=None, review="not_required", display_name=None, tags=None):
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"{uuid.uuid4().hex}-{_TAG}-{name}",
            storage_path=f"/tmp/{_TAG}/{name}", storage_provider="Client360 Local",
            size_bytes=1234, sha256=uuid.uuid4().hex * 2, person_id=person_id,
            household_id=household_id, status=status, deleted_at=deleted_at, archived=False,
            category=category, review_status=review, display_name=display_name,
            current_version=1, tags=tags or {},
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ).returning(documents.c.id)).scalar_one()


# --- 1. the canonical active/deleted boundary --------------------------------

@pytest.mark.parametrize("status,deleted_at,expected_active", [
    ("active", None, True),
    # A normal, complete soft delete.
    ("deleted", datetime(2026, 1, 1, tzinfo=UTC), False),
    # The two HALF-deleted shapes. Both are real: an interrupted merge retire leaves the first,
    # and a status-only writer leaves the second. Filtering on one column misses one of them.
    ("active", datetime(2026, 1, 1, tzinfo=UTC), False),
    ("deleted", None, False),
])
def test_is_active_and_sql_clause_agree(status, deleted_at, expected_active):
    """The row-level and SQL forms of "active" must never disagree — a consumer that loads a row
    and one that filters in SQL have to reach the same verdict."""
    hh = _household()
    doc_id = _doc("agree.pdf", household_id=hh, status=status, deleted_at=deleted_at)

    assert is_active({"status": status, "deleted_at": deleted_at}) is expected_active

    with engine.connect() as c:
        found = c.scalar(select(documents.c.id).where(
            documents.c.id == doc_id, active_documents_clause())) is not None
    assert found is expected_active


def test_is_active_treats_a_row_missing_the_markers_as_active():
    """Absent is not deleted. Rows reach ``is_active`` from enrichment layers and view models that
    do not always carry every column, and a missing key must not read as a deletion."""
    assert is_active({}) is True
    assert is_active({"status": "active"}) is True
    assert is_active(None) is False


def test_half_deleted_document_is_absent_from_the_client_views():
    """The regression. `deleted_at` stamped but `status` untouched — the exact production shape
    that leaked — must not reach the person view, the household view, or the screen."""
    hh = _household()
    pid = _person("Michael", "White", hh)
    live = _doc("2024 W-2.pdf", household_id=hh)
    half = _doc("2023 1099-R.pdf", household_id=hh, status="active",
                deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    fully = _doc("2022 1040.pdf", household_id=hh, status="deleted",
                 deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    for entity_type, entity_id in (("person", pid), ("household", hh)):
        ids = {d["id"] for d in client_documents(STAFF, entity_type, entity_id)}
        assert live in ids
        assert half not in ids, "a document with deleted_at set leaked into the client view"
        assert fully not in ids

    ids = {d["id"] for d in documents_for_entity(STAFF, "household", hh)}
    assert half not in ids and fully not in ids


def test_deleted_documents_are_excluded_from_the_library_listing():
    from app.services.document_platform import service as doc_service
    hh = _household()
    live = _doc("live.pdf", household_id=hh)
    half = _doc("half.pdf", household_id=hh, deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    ids = {r["id"] for r in doc_service.list_documents(STAFF, page_size=200)["rows"]}
    assert live in ids and half not in ids

    # And it cannot be argued back in: asking for status="deleted" returns nothing rather than
    # the deleted rows.
    asked = doc_service.list_documents(STAFF, status="deleted", page_size=200)
    assert asked["total"] == 0


def test_soft_deleted_document_is_not_downloadable_by_direct_url():
    """Disappearing from the listing is not enough — the file itself must stop being served."""
    from app.routes.documents import _unavailable
    from app.services.documents import get_document

    hh = _household()
    live = _doc("live.pdf", household_id=hh)
    half = _doc("half.pdf", household_id=hh, deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    gone = _doc("gone.pdf", household_id=hh, status="deleted",
                deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    assert _unavailable(get_document(live)) is False
    assert _unavailable(get_document(half)) is True
    assert _unavailable(get_document(gone)) is True


def test_person_document_list_excludes_soft_deleted():
    """`get_person_documents` filtered on `archived` alone and returned every deleted document."""
    from app.services.documents import get_person_documents
    hh = _household()
    pid = _person("Debra", "White", hh)
    live = _doc("live.pdf", person_id=pid)
    half = _doc("half.pdf", person_id=pid, deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    ids = {d["id"] for d in get_person_documents(pid)}
    assert live in ids and half not in ids


# --- 2. the person+household union -------------------------------------------

def test_person_view_includes_household_documents():
    """A person's screen must show the household's paperwork. On the real White household only 4
    of 291 documents are person-anchored; the rest are the household's, and a person-only read
    hides the joint return from both spouses."""
    hh = _household()
    michael = _person("Michael", "White", hh)
    debra = _person("Debra", "White", hh)
    joint = _doc("2024 joint return.pdf", household_id=hh)
    his = _doc("2024 W-2 Michael.pdf", person_id=michael)
    hers = _doc("2024 W-2 Debra.pdf", person_id=debra)

    seen = {d["id"] for d in client_documents(STAFF, "person", michael)}
    assert joint in seen, "the household union must reach the member screens"
    assert his in seen
    # A sibling member's PRIVATELY anchored document is deliberately not pulled onto this screen.
    # The union is person + household, not person + everyone who shares the household: widening it
    # would quietly publish one spouse's own file on the other's screen. The household screen is
    # where the whole family's documents are seen together.
    assert hers not in seen

    # Both spouses do see the household's own documents, which is the acceptance requirement.
    assert joint in {d["id"] for d in client_documents(STAFF, "person", debra)}
    # And the household screen sees everything, including both members' own documents.
    assert {joint, his, hers} <= {d["id"] for d in client_documents(STAFF, "household", hh)}

    # The union is a READ. Nothing is re-anchored: each row still reports where it is filed.
    rows = {d["id"]: d for d in client_documents(STAFF, "person", michael)}
    assert rows[joint]["household_id"] == hh and rows[joint]["person_id"] is None
    assert rows[his]["person_id"] == michael and rows[his]["household_id"] is None


def test_household_document_reads_as_household_on_a_person_screen():
    """It must not be silently attributed to whoever is being viewed — that would be exactly the
    unsafe owner inference the union is designed to avoid."""
    hh = _household()
    michael = _person("Michael", "White", hh)
    _doc("2024 joint return.pdf", household_id=hh)

    rows = client_documents(STAFF, "person", michael)
    screen = documents_screen.build(rows, member_names={michael: "Michael White"},
                                    household_name="White Household")
    joint = next(r for r in screen["rows"] if "joint" in r["original_name"])
    assert joint["related_to"]["kind"] == "household"
    assert joint["related_to"]["label"] == "White Household"


# --- 3. the screen: filters, tabs, display names ------------------------------

def _screen(rows, **kw):
    return documents_screen.build(rows, **kw)


@pytest.fixture()
def white_rows():
    """A household shaped like the real one: nothing classified, types and years only derivable
    from the filename."""
    hh = _household()
    michael = _person("Michael", "White", hh)
    _doc("Schwab_1099-R_2025.pdf", person_id=michael)
    _doc("2024 W-2 Home Trends.pdf", household_id=hh)
    _doc("Tax_Organizer_2024.pdf", household_id=hh)
    _doc("Brokerage Statement 2023.pdf", household_id=hh)
    _doc("Trust Document.pdf", household_id=hh, category="trust_document")
    _doc("IMG_2759.jpeg", household_id=hh, review="pending")
    return {"household_id": hh, "person_id": michael,
            "rows": client_documents(STAFF, "household", hh),
            "member_names": {michael: "Michael White"}}


def test_text_search_matches_the_visible_fields(white_rows):
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"], q="organizer")
    assert out["total"] == 1
    assert "Organizer" in out["rows"][0]["original_name"]

    # Case-insensitive, and it matches the derived type too, not just the filename.
    assert _screen(white_rows["rows"], member_names=white_rows["member_names"],
                   q="1099-r")["total"] == 1


def test_search_does_not_match_storage_internals(white_rows):
    """A hit the user cannot see the reason for is worse than no hit. Storage paths and hashes are
    provenance and are deliberately outside the haystack."""
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"], q=_TAG.lower())
    assert out["total"] == 0


def test_category_tabs_bucket_and_count(white_rows):
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"])
    counts = {t["key"]: t["count"] for t in out["tabs"]}
    assert counts["all"] == 6
    assert counts["tax"] == 3            # 1099-R, W-2, Organizer
    assert counts["investments"] == 1    # Brokerage Statement
    assert counts["estate"] == 1         # Trust Document
    assert counts["other"] == 1          # the untyped image
    # One bucket per document: the category tabs must sum to the total, never double-count.
    assert sum(v for k, v in counts.items() if k != "all") == counts["all"]


def test_category_filter_narrows_the_rows(white_rows):
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"], tab="investments")
    assert out["total"] == 1
    assert out["rows"][0]["type_code"] == "brokerage_statement"


def test_tax_year_filter(white_rows):
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"])
    assert out["years"] == ["2025", "2024", "2023"]

    narrowed = _screen(white_rows["rows"], member_names=white_rows["member_names"], year="2024")
    assert narrowed["total"] == 2
    assert {r["year"] for r in narrowed["rows"]} == {"2024"}


def test_type_filter(white_rows):
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"], type_code="W-2")
    assert out["total"] == 1
    assert out["rows"][0]["type_text"] == "W-2"


def test_related_to_filter(white_rows):
    michael = white_rows["person_id"]
    hh = white_rows["household_id"]
    kw = {"member_names": white_rows["member_names"], "household_name": "White Household"}

    mine = _screen(white_rows["rows"], related=f"person:{michael}", **kw)
    assert mine["total"] == 1
    assert mine["rows"][0]["related_to"]["label"] == "Michael White"

    theirs = _screen(white_rows["rows"], related=f"household:{hh}", **kw)
    assert theirs["total"] == 5

    options = {o["key"] for o in _screen(white_rows["rows"], **kw)["related_options"]}
    assert options == {f"person:{michael}", f"household:{hh}"}


def test_needs_review_filter(white_rows):
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"])
    assert out["needs_review_count"] == 1

    only = _screen(white_rows["rows"], member_names=white_rows["member_names"], needs_review=True)
    assert only["total"] == 1
    assert only["rows"][0]["original_name"] == "IMG_2759.jpeg"


def test_filters_compose(white_rows):
    """Filters narrow together rather than replacing one another, and the tab counts reflect the
    other active filters — so the number on a tab is what clicking it will show."""
    out = _screen(white_rows["rows"], member_names=white_rows["member_names"],
                  tab="tax", year="2024")
    assert out["total"] == 2
    counts = {t["key"]: t["count"] for t in out["tabs"]}
    assert counts["all"] == 2 and counts["investments"] == 0


def test_display_name_layer_is_used_and_original_is_kept():
    """The staff-facing label is the canonical display name; the original filename survives as the
    secondary line rather than being replaced."""
    hh = _household()
    _doc("MBW_scan_0001.pdf", household_id=hh, display_name="2024 Form 1040 - White Household")

    out = _screen(client_documents(STAFF, "household", hh))
    row = out["rows"][0]
    assert row["name"] == "2024 Form 1040 - White Household"
    assert row["original_name"] == "MBW_scan_0001.pdf"


def test_derived_values_are_flagged_as_derived():
    """An inferred type or year must be distinguishable from a filed one — that flag is what the
    UI renders as `inferred`, and it is the difference between a fact and a guess."""
    hh = _household()
    _doc("Schwab_1099-R_2025.pdf", household_id=hh)
    _doc("filed.pdf", household_id=hh, category="w2", tags={"tax_year": "2019"})

    rows = {r["original_name"]: r for r in _screen(client_documents(STAFF, "household", hh))["rows"]}

    derived = rows["Schwab_1099-R_2025.pdf"]
    assert derived["type_code"] == "1099-R" and derived["type_derived"] is True
    assert derived["year"] == "2025" and derived["year_derived"] is True
    assert 0 < derived["type_confidence"] <= 1

    filed = rows["filed.pdf"]
    assert filed["year"] == "2019" and filed["year_derived"] is False


def test_pagination_windows_the_rows():
    hh = _household()
    for i in range(30):
        _doc(f"doc-{i:02d}.pdf", household_id=hh)

    rows = client_documents(STAFF, "household", hh)
    first = _screen(rows, page=1, page_size=25)
    assert len(first["rows"]) == 25
    assert (first["total"], first["pages"], first["range_start"], first["range_end"]) == (30, 2, 1, 25)

    second = _screen(rows, page=2, page_size=25)
    assert len(second["rows"]) == 5 and second["range_start"] == 26

    # An out-of-range page clamps to the last one rather than rendering an empty table.
    assert _screen(rows, page=99, page_size=25)["page"] == 2


def test_deleted_documents_never_reach_the_screen():
    """The end-to-end assertion: a half-deleted document is absent from the rows, from every tab
    count, and from the facet lists — not merely filtered out of the current page."""
    hh = _household()
    _doc("live 2024 W-2.pdf", household_id=hh)
    _doc("deleted 2011 1099-R.pdf", household_id=hh,
         deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    out = _screen(client_documents(STAFF, "household", hh))
    assert out["total"] == 1 and out["total_all"] == 1
    assert "2011" not in out["years"]
    assert all(code != "1099-R" for code, _ in out["types"])


# --- 4. the preview drawer route ---------------------------------------------

def _panel_html(entity_type, entity_id, document_id, panel="preview", principal=STAFF):
    from app.routes.document_panel import _panel
    from tests._portal_util import fake_request, render
    url = (f"/client/household/{entity_id}/documents/{document_id}/panel"
           if entity_type == "household" else
           f"/client/{entity_id}/documents/{document_id}/panel")
    return render(_panel(fake_request(url, state_principal=principal), principal,
                         entity_type, entity_id, document_id, panel))


def test_panel_renders_filing_and_source_information():
    hh = _household()
    doc_id = _doc("Schwab_1099-R_2025.pdf", household_id=hh,
                  display_name="Schwab 1099-R")

    html = _panel_html("household", hh, doc_id)
    assert "Schwab 1099-R" in html
    assert "Filing Information" in html and "Source Information" in html
    assert "1099-R" in html and "2025" in html
    assert f"/documents/{doc_id}/download" in html          # Download
    assert "Open in New Tab" in html
    # A derived value is labelled as derived rather than presented as filed.
    assert "inferred" in html


def test_panel_history_states_the_gap_instead_of_inventing_one():
    """`document_events` is real and populated for documents registered through the platform. A
    bulk-synced document has none, and the panel must say so rather than manufacture a timeline."""
    hh = _household()
    doc_id = _doc("bulk_synced.pdf", household_id=hh)
    html = _panel_html("household", hh, doc_id, panel="history")
    assert "No recorded history" in html
    assert "synced in bulk" in html


def test_panel_404s_for_a_soft_deleted_document():
    """The drawer must agree with the table about what exists — including the half-deleted shape."""
    hh = _household()
    half = _doc("half.pdf", household_id=hh, deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    gone = _doc("gone.pdf", household_id=hh, status="deleted",
                deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    for doc_id in (half, gone):
        assert "not found" in _panel_html("household", hh, doc_id).lower()


def test_panel_404s_for_another_clients_document():
    """The membership test is the authorization: a document id alone must not open a panel."""
    mine = _household("Mine")
    theirs = _household("Theirs")
    other_doc = _doc("theirs.pdf", household_id=theirs)
    assert "not found" in _panel_html("household", mine, other_doc).lower()


def test_client_document_lookup_is_exact_not_a_capped_scan():
    """The drawer authorises on `client_document`, which must find a document regardless of how
    many the client has — a list read capped at N would 404 the (N+1)th document that the table
    itself can render."""
    from app.services.document_platform.relationships import client_document
    hh = _household()
    ids = [_doc(f"doc-{i:03d}.pdf", household_id=hh) for i in range(30)]
    for doc_id in (ids[0], ids[-1]):
        found = client_document(STAFF, "household", hh, doc_id)
        assert found is not None and found["id"] == doc_id
    assert client_document(STAFF, "household", hh, -1) is None


def test_panel_reachable_from_a_person_for_a_household_document():
    """The acceptance path: Michael's screen lists the household's joint return, so its drawer must
    open from Michael's route too."""
    hh = _household()
    michael = _person("Michael", "White", hh)
    joint = _doc("2024 joint return.pdf", household_id=hh)
    html = _panel_html("person", michael, joint)
    assert "joint return" in html
    assert "Filing Information" in html


# --- 5. the header shows only what exists ------------------------------------

def test_header_status_is_read_not_assumed():
    """A household has no status column, so "Active" means at least one active member — READ from
    the member rows. Defaulting it would have badged every household Active, closed ones included."""
    from app.services.client360.sections import _documents_header
    hh = _household()
    pid = _person("Michael", "White", hh)

    ctx = {"entity_type": "household", "entity_id": hh, "household_id": hh}
    assert _documents_header(ctx)["status"] == "Active"

    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == pid).values(active=False))
    assert _documents_header(ctx)["status"] == "Inactive"


def test_header_omits_an_advisor_that_does_not_exist():
    """Most clients carry no `record_assignments` row. The header must render nothing rather than a
    placeholder — an invented advisor on a client file is worse than a missing one."""
    from app.services.client360.sections import _documents_header
    hh = _household()
    header = _documents_header({"entity_type": "household", "entity_id": hh, "household_id": hh})
    assert header["primary_advisor"] is None
    assert header["household_id"] == hh
