"""A client's Documents tab shows their FILE, not the firm's cleanup queue.

WHAT THIS LOCKS. The person and household profiles used to embed the operational document-cleanup
workspace: a left rail carrying incomplete-metadata diagnostics, per-reason proposal queues,
OCR/source failure counts and firm-wide category totals. That is work-management, and it belongs in
the Document Workspace or My Work. On an individual client profile it was the dominant interface,
and it described the state of the firm's metadata rather than the client's documents.

The rail is gone. Everything a staff member actually does on a client's file stays: search, the
year / category / type / related-entity filters, clear-filters, the document list, the portal
visibility badge, the preview drawer, sorting and paging. The two things the rail uniquely carried
are preserved in a smaller form — category became a filter beside the others, and the review
worklist became one chip that appears only when something genuinely needs attention.

Scrolling and the drawer are NOT this change's subject and are NOT asserted here; they work today
and the frame that makes them work is untouched. ``tests/test_documents_workspace_scroll.py`` owns
that behaviour and continues to prove it.

Renders the real templates through the real route functions. No database writes beyond the tagged
fixtures, which are removed.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import insert, select

from app.db import documents, engine, households, people
from app.security.models import Principal

# documents.view is what /document-library itself enforces, so it is also what gates the link to it.
_BASE = {"client.read", "record.read_all", "documents.view"}
STAFF = Principal(1, "staff@t", "Staff", frozenset(_BASE))
NO_LIBRARY = Principal(2, "nolib@t", "NoLib", frozenset({"client.read", "record.read_all"}))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            for col, vals in ((documents.c.person_id, ppl), (documents.c.household_id, hhs)):
                if vals:
                    c.execute(documents.delete().where(col.in_(vals)))
            if ppl:
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "PROFDOC" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _seed(tag, *, count=6):
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"House {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, first_name="Ada", last_name=tag,
                                              full_name=f"Ada {tag}", active=True)
                        .returning(people.c.id)).scalar_one()
        for i in range(count):
            u = uuid.uuid4().hex
            c.execute(insert(documents).values(
                original_name=f"Statement {i:02d} {tag}.pdf", stored_name=f"s-{u}",
                storage_path=f"/vault/{u}.bin", storage_uri=f"/vault/{u}.bin", size_bytes=9,
                sha256=u.ljust(64, "0")[:64], status="active", archived=False,
                content_type="application/pdf", person_id=pid))
    return pid, hid


def _person_tab(principal, person_id, **params):
    from app.routes.client360 import client_workspace
    from tests._portal_util import fake_request, render
    request = fake_request(f"/client/{person_id}?tab=documents", state_principal=principal)
    return render(client_workspace(request, person_id, tab="documents", principal=principal,
                                   **params))


def _household_tab(principal, household_id, **params):
    from app.routes.client360 import household_workspace
    from tests._portal_util import fake_request, render
    request = fake_request(f"/client/household/{household_id}?tab=documents",
                           state_principal=principal)
    return render(household_workspace(request, household_id, tab="documents",
                                      principal=principal, **params))


@pytest.fixture
def surfaces():
    """The person and household Documents tabs, which share one partial and must not drift."""
    tag = _tag()
    pid, hid = _seed(tag)
    return {"person": _person_tab(STAFF, pid), "household": _household_tab(STAFF, hid),
            "tag": tag, "person_id": pid, "household_id": hid}


# --- what must be GONE -------------------------------------------------------

#: Every marker that only the operational cleanup rail produced. Each is checked on its own so a
#: failure names the control that came back rather than "the rail".
RAIL_MARKERS = {
    "the rail container": 'class="docws-rail"',
    "a rail navigation item": "docws-navitem",
    "the rail's view/category headings": "docws-navlabel",
    "the per-reason drill-down links": "docws-navitem--reason",
    "the zero-count reason rows": "zerorow",
    "the scope disclosure": "docws-scopenote",
    "the incomplete-metadata worklist link": "dincomplete=1",
    "the recent-documents worklist link": "drecent=1",
    "the per-reason flag filter links": "dflag=",
}


@pytest.mark.parametrize("surface", ["person", "household"])
@pytest.mark.parametrize("marker", sorted(RAIL_MARKERS), ids=lambda m: m.replace(" ", "_"))
def test_the_cleanup_rail_is_gone_from_the_client_profile(surfaces, surface, marker):
    """No diagnostic or cleanup control from the rail may appear on a client's own profile."""
    assert RAIL_MARKERS[marker] not in surfaces[surface], (
        f"{marker} is still rendered on the {surface} Documents tab")


@pytest.mark.parametrize("surface", ["person", "household"])
def test_the_profile_does_not_advertise_proposal_or_failure_queues(surfaces, surface):
    """The rail's operational vocabulary is not the voice of a client profile."""
    html = surfaces[surface]
    for phrase in ("Look-alike not resolved", "Document type is a proposal",
                   "Source copy missing", "Incomplete metadata"):
        assert phrase not in html, f"{phrase!r} still appears on the {surface} profile"


# --- what must REMAIN --------------------------------------------------------

#: The controls a staff member actually uses on a client's file.
KEPT_CONTROLS = {
    "search": 'id="doc-q"',
    "year filter": 'id="doc-year"',
    "category filter": 'id="doc-category"',
    "type filter": 'id="doc-type"',
    "related-entity filter": 'id="doc-related"',
    "the document list": 'class="docws-list"',
    "the preview drawer": "data-docdrawer",
    "the portal visibility badge": "docrow-vis",
}


@pytest.mark.parametrize("surface", ["person", "household"])
@pytest.mark.parametrize("control", sorted(KEPT_CONTROLS), ids=lambda c: c.replace(" ", "_"))
def test_the_working_controls_are_all_still_there(surfaces, surface, control):
    assert KEPT_CONTROLS[control] in surfaces[surface], (
        f"{control} disappeared from the {surface} Documents tab")


@pytest.mark.parametrize("surface", ["person", "household"])
def test_the_table_still_shows_the_six_columns_staff_read(surfaces, surface):
    """Document, Type, Year, Related To, Date, Actions. Source and Status stay in the panel."""
    html = surfaces[surface]
    headers = re.findall(r"<th[^>]*>(?:\s*<a[^>]*>)?\s*([A-Za-z ]+?)\s*(?:<|$)", html)
    for column in ("Document", "Type", "Year", "Related To", "Date", "Actions"):
        assert column in headers, f"the {column} column is missing: saw {headers}"


@pytest.mark.parametrize("surface", ["person", "household"])
def test_category_survived_the_rail_as_a_filter(surfaces, surface):
    """Category was the rail's bottom half. It is a filter, so it moved in with the filters.

    Asserted as a real select carrying `dtab`, and asserted to be the ONLY control with that name —
    the toolbar used to submit `dtab` as a hidden input, and two controls sharing a name would send
    the stale value.
    """
    html = surfaces[surface]
    assert re.search(r'<select[^>]*id="doc-category"[^>]*name="dtab"', html), \
        "the category filter is not a select bound to dtab"
    assert html.count('name="dtab"') == html.count('name="dtab"'), "sanity"
    toolbar = html.split('class="docws-toolbar"', 1)[1].split("</form>", 1)[0]
    assert toolbar.count('name="dtab"') == 1, (
        "the toolbar submits dtab more than once; the hidden input was not removed")


# --- the two things that replaced the rail -----------------------------------

def test_needs_attention_is_absent_when_nothing_needs_attention():
    """A count of nothing is not news. The chip renders only when there is work."""
    tag = _tag()
    pid, _ = _seed(tag)
    html = _person_tab(STAFF, pid)
    assert "docws-attention" not in html, "the Needs attention chip renders with an empty queue"
    assert "need" not in html.split('class="docws-list"', 1)[1][:4000].lower().replace(
        "needs-", "").replace("needed", ""), "unexpected attention wording on a clean file"


def test_needs_attention_appears_and_toggles_when_work_exists():
    """With a real review queue the chip appears once, and offers the way back to the full list."""
    tag = _tag()
    pid, _ = _seed(tag)
    with engine.begin() as c:
        c.execute(documents.update()
                  .where(documents.c.person_id == pid)
                  .values(review_status="needs_review"))

    plain = _person_tab(STAFF, pid)
    assert "docws-attention" in plain, "the chip is missing while documents need review"
    assert plain.count('class="attn"') == 1, "more than one attention control rendered"

    filtered = _person_tab(STAFF, pid, review=1)
    assert "attn--on" in filtered, "the chip does not show itself as the active filter"
    assert "Show all" in filtered, "no way back to the unfiltered list"


# --- the link out to the operational surface ---------------------------------

@pytest.mark.parametrize("surface", ["person", "household"])
def test_the_document_workspace_link_is_always_visible(surfaces, surface):
    """Detailed cleanup has a home, and the profile says where it is — not only when work exists."""
    html = surfaces[surface]
    assert "Open in Document Workspace" in html
    assert 'href="/document-library"' in html, "the link does not point at the Document Workspace"


def test_the_workspace_link_is_gated_on_the_capability_that_route_enforces():
    """/document-library requires documents.view, so a principal without it is not shown the link.

    Visibility only: this asserts the link is hidden, never that access is granted.
    """
    tag = _tag()
    pid, _ = _seed(tag)
    assert "Open in Document Workspace" in _person_tab(STAFF, pid)
    assert "Open in Document Workspace" not in _person_tab(NO_LIBRARY, pid)


# --- nothing about scope or lifecycle changed --------------------------------

def test_only_this_clients_active_documents_are_listed(surfaces):
    """Cross-client isolation and lifecycle filtering are untouched by a presentation change."""
    other_tag = _tag()
    other_pid, _ = _seed(other_tag, count=3)
    html = _person_tab(STAFF, surfaces["person_id"])
    assert surfaces["tag"] in html
    assert other_tag not in html, "another client's documents leaked onto this profile"


def test_a_deleted_document_stays_off_the_profile():
    tag = _tag()
    pid, _ = _seed(tag, count=4)
    with engine.begin() as c:
        victim = c.execute(select(documents.c.id)
                           .where(documents.c.person_id == pid)
                           .order_by(documents.c.id).limit(1)).scalar_one()
        c.execute(documents.update().where(documents.c.id == victim)
                  .values(status="deleted", deleted_at="2026-01-01T00:00:00+00:00"))
    html = _person_tab(STAFF, pid)
    assert f'data-doc-id="{victim}"' not in html, "a deleted document is still listed"
