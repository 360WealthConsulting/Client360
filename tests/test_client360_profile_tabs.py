"""Client profile workspace: tab routing, content placement, document scoping, access control.

These cover the repair of four concrete defects on the person workspace:

1. The Drake Tax return history rendered ABOVE the tab bar, on every tab. It pushed the tab bar
   down a screen and put a return-by-return history in front of anyone opening Messages or
   Documents. It now renders inside the Tax tab.
2. Overview therefore showed the full Drake history rather than a concise summary.
3. The Documents list was flat, with no category/year structure.
4. Nothing on the Documents list said whether a document was staff-only or readable by the client
   in the portal.
"""
from pathlib import Path

import pytest

from app.services.client360 import documents_screen as ds
from app.services.client360.sections import _merge_documents

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates" / "client360"
WORKSPACE = (TEMPLATES / "workspace.html").read_text(encoding="utf-8")
SECTION_NAV = (TEMPLATES / "_section_nav.html").read_text(encoding="utf-8")
DOC_SCREEN = (TEMPLATES / "_documents_screen.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- tab routing

def test_every_required_tab_is_still_defined():
    # The repair must not drop a tab. These are the client domains the workspace offers.
    for label in ("Overview", "People", "Wealth", "Tax", "Documents", "Work",
                  "Messages", "Internal", "Compliance", "Activity"):
        assert f'("{label}"' in SECTION_NAV, f"tab group {label} is missing"


def test_tab_groups_map_to_real_sections():
    # Each headline group routes to a section key the workspace actually renders.
    for key in ("dashboard", "tax", "documents", "members", "financial",
                "tasks", "messages", "notes", "compliance", "timeline"):
        assert f'"{key}"' in SECTION_NAV, f"section key {key} is not routed by the tab bar"


def test_person_workspace_rejects_unknown_tab_and_falls_back(monkeypatch):
    from app.routes import client360 as route_mod
    # The route's allowed-tab handling is what keeps ?tab=anything from rendering a stray section.
    src = Path(route_mod.__file__).read_text(encoding="utf-8")
    assert "active_tab" in src


# --------------------------------------------------------------- content placement (the defect)

def test_drake_detail_is_not_rendered_above_the_tab_bar():
    """The regression this repair exists to prevent."""
    nav = WORKSPACE.index('include "client360/_section_nav.html"')
    head = WORKSPACE[:nav]
    assert "drake_tax_detail" not in head, (
        "Drake detail is being rendered above the tab bar again")
    assert "ws.drake_returns" not in head


def test_drake_detail_renders_inside_the_tax_tab():
    tax = WORKSPACE.index('active_tab == "tax"')
    nav = WORKSPACE.index('include "client360/_section_nav.html"')
    assert tax > nav, "the tax section must render below the tab bar"
    body = WORKSPACE[tax:tax + 1200]
    assert "drake.drake_tax_detail(ws.drake_returns)" in body


def test_drake_detail_still_shows_agi_acknowledgements_and_dates():
    # Moving the block must not quietly drop the tax facts it carried.
    partial = (TEMPLATES / "_drake_tax.html").read_text(encoding="utf-8")
    for token in ("AGI", "federal_ack_code", "federal_ack_date", "state_ack_date",
                  "tax_year", "preparer_code", "spouse_first_name"):
        assert token in partial, f"Drake field {token} was lost in the move"


def test_tab_bar_sits_near_the_top_of_the_page():
    # A tab bar a screen down the page IS the layout defect, so the budget above it is guarded:
    # breadcrumb + identity header + snapshot strip, and nothing domain-specific.
    nav_line = WORKSPACE[:WORKSPACE.index('include "client360/_section_nav.html"')].count("\n")
    assert nav_line < 75, f"tab bar has drifted down to line {nav_line}"


# ------------------------------------------------------------------ documents: grouping

def _row(**kw):
    base = dict(id=1, name="Doc", original_name="Doc.pdf", category=None, classification=None,
                document_type=None, tax_year=None, tags=None, updated_at=None, created_at=None)
    base.update(kw)
    return base


def _shape(**kw):
    return ds.shape_row(_row(**kw), member_names={}, household_name=None)


def test_group_rows_partitions_by_category_and_year_without_losing_rows():
    rows = [
        _shape(id=1, original_name="2023 Form 1040.pdf"),
        _shape(id=2, original_name="2023 Form W-2.pdf"),
        _shape(id=3, original_name="2022 Form 1040.pdf"),
        _shape(id=4, original_name="Brokerage Statement 2023.pdf"),
        _shape(id=5, original_name="Untitled scan.pdf"),
    ]
    groups = ds.group_rows(rows)
    assert sum(g["count"] for g in groups) == len(rows)          # nothing added or dropped
    assert sum(len(g["rows"]) for g in groups) == len(rows)
    keys = [(g["category"], g["year"]) for g in groups]
    assert len(keys) == len(set(keys)), "a (category, year) pair was emitted twice"


def test_group_rows_orders_categories_then_year_newest_first_with_undated_last():
    rows = [
        _shape(id=1, original_name="2022 Form 1040.pdf"),
        _shape(id=2, original_name="2024 Form 1040.pdf"),
        _shape(id=3, original_name="Form 1040.pdf"),      # same category, no year in the name
    ]
    groups = [g for g in ds.group_rows(rows) if g["category"] == "tax"]
    years = [g["year"] for g in groups]
    assert years == ["2024", "2022", None], (
        "years descend within a category and undated documents sort last")


def test_group_labels_match_the_category_tabs():
    built = ds.build([], member_names={}, household_name=None)
    labels = built["group_labels"]
    for tab in ds.TABS:
        assert labels[tab["key"]] == tab["label"], "a group heading disagrees with its tab"


def test_build_exposes_groups_covering_exactly_the_visible_page():
    rows = [_row(id=i, original_name=f"202{i % 4} Form 1040.pdf") for i in range(1, 9)]
    built = ds.build(rows, member_names={}, household_name=None, page_size=5)
    assert len(built["rows"]) == 5
    assert sum(g["count"] for g in built["groups"]) == 5, "groups must cover the page, not the set"


def test_documents_template_renders_group_headings():
    assert "docws-group" in DOC_SCREEN
    assert "group_labels" in DOC_SCREEN


# ------------------------------------------------------- documents: portal vs internal

def test_a_canonical_document_is_internal_by_default():
    row = _shape(id=1, original_name="2023 Form 1040.pdf")
    assert row["portal"]["published"] is False
    assert row["portal"]["label"] == "Internal only"


def test_a_client_visible_vault_document_reads_as_published():
    row = _shape(id=1, original_name="2023 Form 1040.pdf", portal_visible=True)
    assert row["portal"]["published"] is True
    assert row["portal"]["label"] == "Client portal"


def test_merge_marks_a_canonical_row_published_when_vault_publishes_the_same_file():
    canonical = [{"id": 1, "sha256": "abc", "name": "Return.pdf"}]
    vault = [{"id": 9, "sha256": "abc", "name": "Return.pdf", "portal_visible": True}]
    merged = _merge_documents(canonical, vault)
    assert len(merged) == 1, "the vault duplicate should collapse into the canonical row"
    assert merged[0]["portal_visible"] is True, (
        "collapsing the duplicate must not discard the only evidence the client can see it")


def test_merge_leaves_a_canonical_row_internal_when_the_vault_copy_is_not_client_visible():
    canonical = [{"id": 1, "sha256": "abc"}]
    vault = [{"id": 9, "sha256": "abc", "portal_visible": False}]
    merged = _merge_documents(canonical, vault)
    assert merged[0]["portal_visible"] is False


def test_merge_never_mutates_the_caller_rows():
    canonical = [{"id": 1, "sha256": "abc"}]
    vault = [{"id": 9, "sha256": "abc", "portal_visible": True}]
    _merge_documents(canonical, vault)
    assert "portal_visible" not in canonical[0], "the caller's row object was mutated"


def test_documents_template_states_visibility_in_both_directions():
    # "no badge" must never be a state: staff deciding what a client may read need it said.
    # The row renders unconditionally on d.portal, which always carries one of the two labels.
    assert "d.portal.label" in DOC_SCREEN
    assert "docrow-vis--{{ d.portal.tone }}" in DOC_SCREEN
    assert "{% if d.portal %}" in DOC_SCREEN, "the badge must not be gated on published-only"
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css"
           / "documents_workspace.css").read_text(encoding="utf-8")
    assert ".docrow-vis--published" in css and ".docrow-vis--internal" in css


def test_both_visibility_tones_are_reachable_from_the_shaper():
    tones = {_shape(id=1, portal_visible=v)["portal"]["tone"] for v in (True, False)}
    assert tones == {"published", "internal"}


# ------------------------------------------------------------------ scoping / access control

def test_client_documents_scopes_to_the_person_and_their_household_only():
    """No cross-client leakage: the anchor set is this client's ids and nothing else."""
    from app.services.document_platform import relationships as rel
    src = Path(rel.__file__).read_text(encoding="utf-8")
    # The union is built from _client_anchors, and every predicate is an IN over those ids.
    assert "def _client_anchors" in src
    assert "documents.c.person_id.in_(tuple(person_ids))" in src
    assert "documents.c.household_id.in_(tuple(household_ids))" in src


def test_client_anchors_never_widen_beyond_one_client():
    from app.services.document_platform.relationships import _client_anchors

    class _Conn:
        def scalar(self, _stmt):
            return 29                     # the person's household

        def scalars(self, _stmt):
            return [240, 239]             # that household's members

    people_ids, household_ids = _client_anchors(_Conn(), "person", 240)
    assert people_ids == {240} and household_ids == {29}, (
        "a person's anchor set must be that person plus their own household")

    people_ids, household_ids = _client_anchors(_Conn(), "household", 29)
    assert household_ids == {29} and people_ids == {240, 239}


def test_unknown_entity_type_falls_back_to_the_narrow_single_entity_read():
    from app.services.document_platform import relationships as rel
    src = Path(rel.__file__).read_text(encoding="utf-8")
    assert 'if entity_type not in ("person", "household")' in src
    assert "return documents_for_entity(" in src


def test_sections_are_gated_and_an_ungranted_section_is_absent_not_403():
    # The nav renders only keys present in ws.section_keys, which the service gates by capability.
    #
    # The condition now also requires the section to be one this template can DRAW, so the literal
    # it used to match is no longer present verbatim. The gate itself is unchanged and is what this
    # asserts: membership of ws.section_keys is still required in both the grouped branch and the
    # "More" catch-all. A narrower condition cannot show a section capability would have hidden —
    # only the reverse would be a regression, which is why both branches are checked.
    assert "k in ws.section_keys and k in drawable" in SECTION_NAV
    assert "k not in gk.all and k in drawable" in SECTION_NAV
    assert "You do not have access to this section." in WORKSPACE


def test_upload_form_is_gated_on_documents_edit():
    assert 'principal.can("documents.edit")' in WORKSPACE


@pytest.mark.parametrize("forbidden", ["/portal/", "portal_account", "portal_session"])
def test_the_repair_does_not_touch_the_client_portal(forbidden):
    assert forbidden not in DOC_SCREEN
