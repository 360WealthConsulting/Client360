"""The organization profile reaches every document it owns, not the first 200.

WHAT THIS FIXES, AND HOW IT WAS FOUND

A read-only production trace showed 16 TaxDome documents on two organizations that appeared on no
staff surface at all. They were owned correctly, active, unarchived, and past the ``limit(200)`` in
``business_workspace``. Organization-owned documents have no second route: ``client_documents``
unions person and household anchors only, so a document carrying ``organization_id`` and nothing
else is reachable from the organization profile or nowhere. The heading meanwhile rendered the true
total, so the screen said "Documents (514)" above 200 rows and offered no way to see the rest.

WHAT THESE TESTS PIN

Reachability      every document is on exactly one page, and paging visits all of them.
Stability         the sequence does not shuffle between reads, so nothing is duplicated or skipped.
                  Fixtures deliberately share one ``created_at`` — that tie is what made the old
                  single-column ordering non-deterministic under LIMIT/OFFSET.
Honest total      ``document_count`` counts what paging can actually reach.
Unchanged rules   archived and deleted rows stay out; one organization never sees another's
                  documents; and the source system a document arrived from is not an input.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.db import documents, engine, metadata, relationship_entities
from app.services.business_workspace import DOCUMENTS_PER_PAGE, get_business_workspace

_TAG = "ORGPAGE"
_document_sources = metadata.tables["document_sources"]

#: Enough to need six pages at the default size, and comfortably past both the old 200 cap and the
#: 500 that a "just raise the limit" fix would have introduced.
_LARGE = 5 * DOCUMENTS_PER_PAGE + 14


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            ids = list(c.scalars(select(documents.c.id)
                                 .where(documents.c.stored_name.like(f"%{_TAG}%"))))
            if ids:
                c.execute(_document_sources.delete()
                          .where(_document_sources.c.document_id.in_(ids)))
                c.execute(documents.delete().where(documents.c.id.in_(ids)))
            c.execute(delete(relationship_entities)
                      .where(relationship_entities.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _business(name="Org"):
    with engine.begin() as c:
        return c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"{name} {_TAG} {uuid.uuid4().hex[:6]}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()


def _doc(organization_id, *, name=None, source_system="TaxDome Drive", created_at=None,
         status="active", archived=False, deleted_at=None, household_id=None):
    name = name or f"doc-{uuid.uuid4().hex[:10]}.pdf"
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{uuid.uuid4().hex}", storage_provider="local",
            size_bytes=1024, sha256=uuid.uuid4().hex * 2,
            organization_id=organization_id, household_id=household_id,
            status=status, archived=archived, deleted_at=deleted_at,
            created_at=created_at or datetime.now(UTC), current_version=1,
        ).returning(documents.c.id)).scalar_one()
        c.execute(_document_sources.insert().values(
            document_id=did, source_system=source_system,
            source_uri=f"{source_system}://{did}", source_external_id=uuid.uuid4().hex,
            available=True))
    return did


def _walk_all_pages(business_id):
    """Every document id the profile can reach, in the order a reader would meet them."""
    seen, page = [], 1
    while True:
        ws = get_business_workspace(business_id, page=page)
        seen.extend(d["id"] for d in ws["documents"])
        if not ws["has_next"]:
            return seen, ws
        page += 1


# --- reachability past the old cap ---------------------------------------------------------------

def test_every_document_is_reachable_when_there_are_far_more_than_five_hundred():
    """The defect, at scale. 514 was the largest affected organization in production."""
    biz = _business()
    created = {_doc(biz) for _ in range(_LARGE)}

    reached, last = _walk_all_pages(biz)

    assert len(created) == _LARGE
    assert set(reached) == created, "paging did not reach every document the organization owns"
    assert last["document_count"] == _LARGE
    assert last["page_count"] == -(-_LARGE // DOCUMENTS_PER_PAGE)


def test_a_document_past_the_old_two_hundred_cap_is_reachable():
    """The exact production shape: a row the capped read could never return."""
    biz = _business()
    base = datetime.now(UTC)
    # Oldest document, so it sorts last and lands well past position 200.
    ids = [_doc(biz, created_at=base - timedelta(minutes=i)) for i in range(250)]
    stranded = ids[-1]

    reached, _ = _walk_all_pages(biz)

    assert stranded in reached
    assert reached.index(stranded) == 249, "ordering should place the oldest document last"
    assert stranded not in {d["id"] for d in get_business_workspace(biz, page=1)["documents"]}


# --- navigation ----------------------------------------------------------------------------------

def test_pages_are_full_contiguous_and_disjoint():
    biz = _business()
    for _ in range(_LARGE):
        _doc(biz)

    pages = []
    for page in range(1, -(-_LARGE // DOCUMENTS_PER_PAGE) + 1):
        ws = get_business_workspace(biz, page=page)
        pages.append([d["id"] for d in ws["documents"]])
        assert ws["page"] == page
        assert ws["first_index"] == (page - 1) * DOCUMENTS_PER_PAGE + 1
        assert ws["last_index"] == ws["first_index"] + len(ws["documents"]) - 1

    for page in pages[:-1]:
        assert len(page) == DOCUMENTS_PER_PAGE
    assert len(pages[-1]) == _LARGE % DOCUMENTS_PER_PAGE

    flat = [i for page in pages for i in page]
    assert len(flat) == len(set(flat)) == _LARGE, "a document was duplicated or skipped"


def test_navigation_flags_bound_the_ends():
    biz = _business()
    for _ in range(_LARGE):
        _doc(biz)
    last_page = -(-_LARGE // DOCUMENTS_PER_PAGE)

    first = get_business_workspace(biz, page=1)
    assert first["has_prev"] is False and first["has_next"] is True

    last = get_business_workspace(biz, page=last_page)
    assert last["has_prev"] is True and last["has_next"] is False


@pytest.mark.parametrize("requested", [0, -5, 9999])
def test_an_out_of_range_page_clamps_instead_of_failing(requested):
    """A stale bookmark should land on a real page, not an empty screen that reads as data loss."""
    biz = _business()
    for _ in range(_LARGE):
        _doc(biz)
    last_page = -(-_LARGE // DOCUMENTS_PER_PAGE)

    ws = get_business_workspace(biz, page=requested)
    assert ws["page"] == (1 if requested < 1 else last_page)
    assert ws["documents"], "a clamped page must still render rows"


def test_a_single_page_organization_is_unchanged():
    """Small organizations must look exactly as they did before, pager included."""
    biz = _business()
    for _ in range(3):
        _doc(biz)

    ws = get_business_workspace(biz)
    assert ws["document_count"] == len(ws["documents"]) == 3
    assert ws["page"] == 1 and ws["page_count"] == 1
    assert ws["has_prev"] is False and ws["has_next"] is False


# --- the total tells the truth --------------------------------------------------------------------

def test_the_total_counts_what_paging_can_reach():
    biz = _business()
    for _ in range(_LARGE):
        _doc(biz)
    _doc(biz, archived=True)
    _doc(biz, status="deleted", deleted_at=datetime.now(UTC))

    reached, last = _walk_all_pages(biz)
    assert last["document_count"] == _LARGE == len(reached)


def test_an_empty_organization_reports_zero_and_one_page():
    ws = get_business_workspace(_business())
    assert ws["document_count"] == 0
    assert ws["documents"] == []
    assert ws["page"] == 1 and ws["page_count"] == 1
    assert ws["first_index"] == 0 and ws["last_index"] == 0


# --- stable ordering -------------------------------------------------------------------------------

def test_ordering_is_stable_when_every_document_shares_a_timestamp():
    """``created_at DESC`` alone is not a total order, and bulk imports stamp rows in the same
    second. Under LIMIT/OFFSET an unstable sort can show one row twice and another never."""
    biz = _business()
    same = datetime.now(UTC)
    created = {_doc(biz, created_at=same) for _ in range(_LARGE)}

    first_walk, _ = _walk_all_pages(biz)
    second_walk, _ = _walk_all_pages(biz)

    assert first_walk == second_walk, "the sequence shuffled between reads"
    assert set(first_walk) == created
    assert len(first_walk) == len(set(first_walk)) == _LARGE


def test_newest_first_survives_paging():
    biz = _business()
    base = datetime.now(UTC)
    newest_to_oldest = [_doc(biz, created_at=base - timedelta(seconds=i))
                        for i in range(DOCUMENTS_PER_PAGE * 2 + 5)]

    reached, _ = _walk_all_pages(biz)
    assert reached == newest_to_oldest


# --- lifecycle exclusions are unchanged ------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"archived": True},
    {"status": "archived"},
    {"status": "deleted", "deleted_at": datetime.now(UTC)},
    {"deleted_at": datetime.now(UTC)},
])
def test_archived_and_deleted_documents_are_excluded_from_every_page(kwargs):
    biz = _business()
    live = [_doc(biz) for _ in range(DOCUMENTS_PER_PAGE + 5)]
    hidden = _doc(biz, **kwargs)

    reached, last = _walk_all_pages(biz)
    assert hidden not in reached
    assert set(reached) == set(live)
    assert last["document_count"] == len(live)


# --- organization isolation --------------------------------------------------------------------------

def test_one_organization_never_sees_another_organizations_documents():
    mine, theirs = _business("Mine"), _business("Theirs")
    my_docs = {_doc(mine) for _ in range(DOCUMENTS_PER_PAGE + 7)}
    their_docs = {_doc(theirs) for _ in range(DOCUMENTS_PER_PAGE + 3)}

    reached, last = _walk_all_pages(mine)
    assert set(reached) == my_docs
    assert not (set(reached) & their_docs)
    assert last["document_count"] == len(my_docs)


def test_a_deep_page_number_cannot_reach_another_organization():
    """Paging selects rows within one organization; it is not a way to widen the read."""
    mine, theirs = _business("Mine"), _business("Theirs")
    _doc(mine)
    their_docs = {_doc(theirs) for _ in range(DOCUMENTS_PER_PAGE * 2)}

    for page in (1, 2, 3, 500):
        ws = get_business_workspace(mine, page=page)
        assert not ({d["id"] for d in ws["documents"]} & their_docs)
        assert ws["document_count"] == 1


def test_an_unowned_document_belongs_to_no_organization_page():
    biz = _business()
    owned = _doc(biz)
    unowned = _doc(None)

    ws = get_business_workspace(biz)
    ids = {d["id"] for d in ws["documents"]}
    assert owned in ids and unowned not in ids
    assert ws["document_count"] == 1


# --- source system is not an input ---------------------------------------------------------------------

def test_drake_and_taxdome_documents_are_treated_identically():
    """Source system records where a file came from. It has never gated staff visibility, and
    paging must not become the place where it starts to."""
    biz = _business()
    drake = {_doc(biz, source_system="Drake") for _ in range(DOCUMENTS_PER_PAGE + 11)}
    taxdome = {_doc(biz, source_system="TaxDome Drive") for _ in range(DOCUMENTS_PER_PAGE + 9)}

    reached, last = _walk_all_pages(biz)

    assert set(reached) == drake | taxdome
    assert last["document_count"] == len(drake) + len(taxdome)
    assert len(drake & set(reached)) == len(drake)
    assert len(taxdome & set(reached)) == len(taxdome)


def test_a_document_with_no_source_reference_is_still_reachable():
    """Ingestion provenance is optional metadata; a document without it is still the client's."""
    biz = _business()
    with engine.begin() as c:
        bare = c.execute(documents.insert().values(
            original_name="bare.pdf", stored_name=f"{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{uuid.uuid4().hex}", storage_provider="local",
            size_bytes=1, sha256=uuid.uuid4().hex * 2, organization_id=biz,
            status="active", archived=False,
        ).returning(documents.c.id)).scalar_one()

    assert bare in {d["id"] for d in get_business_workspace(biz)["documents"]}


# --- related households do not drift between pages -------------------------------------------------------

def test_related_households_are_the_organizations_and_do_not_change_with_the_page():
    """They used to be derived from whichever rows the capped read happened to return."""
    from app.db import households

    biz = _business()
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"{_TAG} Household")
                       .returning(households.c.id)).scalar_one()
    try:
        base = datetime.now(UTC)
        # The only household-bearing document is the OLDEST, so it lands on the last page.
        for i in range(DOCUMENTS_PER_PAGE + 5):
            _doc(biz, created_at=base - timedelta(seconds=i))
        _doc(biz, created_at=base - timedelta(days=1), household_id=hh)

        first = get_business_workspace(biz, page=1)
        last = get_business_workspace(biz, page=first["page_count"])
        ids = {h["household_id"] for h in first["related_households"]}

        assert hh in ids, "a household on a later page must still be listed"
        assert ids == {h["household_id"] for h in last["related_households"]}
    finally:
        with engine.begin() as c:
            c.execute(delete(households).where(households.c.id == hh))
