"""The Documents screen's TIERED review model.

What actually needs guarding here is the MEANING of "Needs Review" on this screen, because that is
the thing a reviewer will trust without checking:

* **Every reason is a state the backend really records.** The tests below construct rows carrying
  each state and assert the reason fires — and, just as importantly, assert that the states this
  screen deliberately does NOT count stay uncounted. An unclassified document is not review work
  (the Knowledge classifier has only ever run over unassigned documents, so counting it would flag
  essentially every client document forever), and an ownership proposal is not this screen's
  business at all: HIGH/HOLD proposals are resolved in Admin → Document Management → Unassigned
  Documents, before a document reaches a client.
* **The two tiers stay separate.** Bulk metadata gaps outnumber actionable problems by an order of
  magnitude on a real client — the White household has ``classification`` NULL on all 291 documents
  — so a regression that merges the tiers would silently turn the headline count back into noise.
  ``test_tiers_do_not_bleed`` fails if they merge.
* **Nothing is invented.** ``owner_missing`` reports the ABSENCE of a stored anchor; no reason reads
  a name out of a filename, and no reason resolves anything.

These are plain unit tests over constructed rows: the reason table is pure, so it takes a row and
returns a list. The database-backed read underneath the screen is already covered by
tests/test_documents_screen.py.
"""
from __future__ import annotations

import pytest

from app.services.client360 import documents_screen as ds

#: A document with nothing outstanding and nothing missing. Every test below starts here and breaks
#: exactly one thing, so a reason that fires is provably caused by the state under test rather than
#: by an incidental gap in the fixture.
SETTLED = {
    "id": 1,
    "name": "2024 Form 1040.pdf",
    "original_name": "2024 Form 1040.pdf",
    "person_id": 7,
    "review_status": "approved",
    "document_type": "tax_return",
    "category": "tax",
    "classification": "tax",
    "tax_year": "2024",
    "tax_year_confidence": "recorded",
    "tax_year_evidence": [],
    "ocr_status": "completed",
    "sources": [{"source_system": "SharePoint", "available": True}],
    "needs_version_review": False,
    "is_duplicate": False,
}


def _shape(**overrides):
    return ds.shape_row({**SETTLED, **overrides}, member_names={7: "Michael White"},
                        household_name=None)


def _keys(**overrides) -> set:
    return {r["key"] for r in _shape(**overrides)["review_reasons"]}


def _screen(rows, **kw):
    return ds.build(rows, member_names={7: "Michael White"}, **kw)


# ---------------------------------------------------------------------------------------------
# Every reason is a state the backend records
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("overrides", "expected"), [
    ({"review_status": "pending"}, "review_requested"),
    ({"person_id": None}, "owner_missing"),
    ({"needs_version_review": True, "version_review_reason": "same filename, different content"},
     "version_ambiguous"),
    ({"tax_year_confidence": "conflict", "tax_year_evidence": ["filename names 2024, 2025"]},
     "tax_year_conflict"),
    ({"ocr_status": "failed"}, "ocr_failed"),
    ({"ocr_status": "timed_out"}, "ocr_failed"),
    ({"sources": [{"source_system": "TaxDome", "available": False}]}, "source_missing"),
    ({"is_duplicate": True, "duplicate_count": 3}, "duplicate"),
    ({"ocr_status": "unsupported"}, "ocr_unsupported"),
    ({"category": None, "classification": None}, "category_missing"),
    # No filed year AND no year to read out of the filename — the fixture's own name carries 2024,
    # so it has to be replaced or the deterministic fallback finds one.
    ({"tax_year": None, "tax_year_confidence": "none",
      "name": "Engagement Letter.pdf", "original_name": "Engagement Letter.pdf"}, "year_missing"),
    # A filed year is absent but the filename yields one: a PROPOSAL, not a gap.
    ({"tax_year": None, "tax_year_confidence": "moderate"}, "year_derived"),
])
def test_each_backed_state_produces_its_reason(overrides, expected):
    assert expected in _keys(**overrides)


def test_a_settled_document_carries_no_reason():
    """The fixture must itself be clean, or every test above proves nothing."""
    assert _keys() == set()


def test_every_reason_is_reachable():
    """No reason may be declared and then be impossible to produce — a dead entry would sit in the
    rail forever showing 0 and reading as a queue that simply never fills."""
    declared = {r.key for r in ds.REVIEW_REASONS}
    produced = set()
    for overrides in ({"review_status": "pending"}, {"person_id": None},
                      {"needs_version_review": True}, {"tax_year_confidence": "conflict"},
                      {"ocr_status": "failed"}, {"ocr_status": "unsupported"},
                      {"sources": [{"source_system": "X", "available": False}]},
                      {"is_duplicate": True}, {"category": None, "classification": None},
                      {"tax_year": None, "tax_year_confidence": "none",
                       "name": "Engagement Letter.pdf", "original_name": "Engagement Letter.pdf"},
                      {"document_type": None, "original_name": "W-2 2023.pdf",
                       "name": "W-2 2023.pdf"},
                      {"tax_year": None, "tax_year_confidence": "moderate"}):
        produced |= _keys(**overrides)
    assert declared == produced


# ---------------------------------------------------------------------------------------------
# The reasons say what the backend says, not a paraphrase of it
# ---------------------------------------------------------------------------------------------

def test_reason_detail_quotes_the_backend_not_a_paraphrase():
    reason = next(r for r in _shape(review_status="flagged")["review_reasons"]
                  if r["key"] == "review_requested")
    assert "flagged" in reason["detail"]
    assert reason["basis"] == "review_status is outside the settled set"


def test_the_version_reviewer_s_own_reason_is_shown_verbatim():
    reason = next(r for r in _shape(needs_version_review=True,
                                    version_review_reason="same filename, different content — both "
                                                          "retained")["review_reasons"]
                  if r["key"] == "version_ambiguous")
    assert reason["detail"] == "same filename, different content — both retained"


def test_tax_year_conflict_shows_the_evidence():
    reason = next(r for r in _shape(tax_year_confidence="conflict",
                                    tax_year_evidence=["filename names several years: 2024, 2025"]
                                    )["review_reasons"] if r["key"] == "tax_year_conflict")
    assert "2024, 2025" in reason["detail"]


def test_a_conflicting_year_is_not_also_reported_as_a_plain_proposal():
    """``shape_row``'s last-resort filename fallback still yields a year on a conflicting row. If
    that were reported as an ordinary proposal, the panel would say "read from the filename" two
    inches under "the filename and the folder disagree". Both cannot be true."""
    keys = _keys(tax_year=None, tax_year_confidence="conflict",
                 name="IRS CP49 Notice (2024-2025).pdf",
                 original_name="IRS CP49 Notice (2024-2025).pdf",
                 tax_year_evidence=["filename names several years: 2024, 2025"])
    assert "tax_year_conflict" in keys
    assert "year_derived" not in keys and "year_missing" not in keys


def test_owner_missing_reports_absence_without_naming_an_owner():
    """The fail-closed case. It states that there is no anchor; it never reads a name out of the
    filename and calls that identity."""
    reason = next(r for r in _shape(person_id=None, name="Michael White W-2.pdf",
                                    original_name="Michael White W-2.pdf")["review_reasons"]
                  if r["key"] == "owner_missing")
    assert "Michael White" not in reason["detail"]
    assert "never inferred" in reason["detail"]


# ---------------------------------------------------------------------------------------------
# What this screen deliberately does NOT count
# ---------------------------------------------------------------------------------------------

def test_direct_upload_is_not_a_missing_source():
    """No source references at all is a direct upload. Absence of a source is not a missing one."""
    assert "source_missing" not in _keys(sources=[])


def test_one_available_source_clears_the_flag():
    assert "source_missing" not in _keys(sources=[
        {"source_system": "TaxDome", "available": False},
        {"source_system": "SharePoint", "available": True}])


def test_unclassified_is_not_review_work():
    """The Knowledge classifier has only ever run over unassigned documents. Counting an absent
    classification would flag virtually every client document forever — reporting a pipeline
    coverage gap as though it were a decision someone owes."""
    assert _keys(classified_type=None, classification_confidence=None) == set()


def test_a_low_confidence_classification_is_not_review_work():
    assert _keys(classified_type="tax_return", classification_confidence=0.11) == set()


def test_no_reason_reads_an_ownership_proposal():
    """HIGH/HOLD ``document_facts`` owner proposals are resolved in Admin → Document Management →
    Unassigned Documents, before a document reaches a client. Nothing on this screen may read one,
    so a row carrying proposal fields must produce no additional reason."""
    assert _keys(owner_proposal="HIGH", owner_proposal_confidence=0.97,
                 fact_type="owner_proposal") == set()


def test_ocr_never_attempted_is_not_review_work():
    """'pending' and NULL mean the pipeline has not reached this document. That is not a failure and
    not a gap anyone can close by hand."""
    assert "ocr_failed" not in _keys(ocr_status="pending")
    assert "ocr_unsupported" not in _keys(ocr_status="pending")
    assert "ocr_failed" not in _keys(ocr_status=None)


# ---------------------------------------------------------------------------------------------
# The two tiers
# ---------------------------------------------------------------------------------------------

def test_tiers_do_not_bleed():
    """The whole point of the split. A regression that merges them turns the headline count back
    into noise, because the bulk gaps outnumber the actionable problems by an order of magnitude."""
    actionable = {r.key for r in ds.REVIEW_REASONS if r.tier == ds.ACTIONABLE}
    incomplete = {r.key for r in ds.REVIEW_REASONS if r.tier == ds.INCOMPLETE}
    assert actionable and incomplete
    assert not (actionable & incomplete)
    assert "review_requested" in actionable and "source_missing" in actionable
    assert "category_missing" in incomplete and "ocr_unsupported" in incomplete


def test_the_two_ocr_states_land_in_different_tiers():
    """A retryable failure is somebody's next action; 'unsupported' means no text will ever be
    extracted and there is nothing to retry. They cannot share a tier."""
    failed = _shape(ocr_status="failed")
    unsupported = _shape(ocr_status="unsupported")
    assert [r["key"] for r in failed["actionable"]] == ["ocr_failed"]
    assert failed["incomplete"] == []
    assert [r["key"] for r in unsupported["incomplete"]] == ["ocr_unsupported"]
    assert unsupported["actionable"] == []


def test_a_document_can_be_in_both_tiers_and_is_counted_in_both():
    row = _shape(review_status="pending", category=None, classification=None)
    assert [r["key"] for r in row["actionable"]] == ["review_requested"]
    assert [r["key"] for r in row["incomplete"]] == ["category_missing"]

    screen = _screen([{**SETTLED, "review_status": "pending",
                       "category": None, "classification": None}])
    assert screen["actionable_count"] == 1
    assert screen["incomplete_count"] == 1


def test_actionable_reasons_are_listed_first():
    """Declaration order is display order, and the tier a reader must act on comes first."""
    reasons = _shape(review_status="pending", category=None, classification=None)["review_reasons"]
    tiers = [r["tier"] for r in reasons]
    assert tiers == sorted(tiers, key=lambda t: t != ds.ACTIONABLE)


# ---------------------------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------------------------

def test_every_reason_lands_in_exactly_one_rail_section():
    screen = _screen([SETTLED])
    placed = [item["key"] for group in screen["reason_views"] for item in group["reasons"]]
    assert sorted(placed) == sorted(r.key for r in ds.REVIEW_REASONS)
    assert len(placed) == len(set(placed))


def test_the_rail_keeps_empty_queues_visible():
    """A reviewer needs to see that a queue is EMPTY, which is a different fact from the queue not
    existing. Zero-count entries are rendered, not dropped."""
    screen = _screen([SETTLED])
    counts = {item["key"]: item["count"]
              for group in screen["reason_views"] for item in group["reasons"]}
    assert counts["ocr_failed"] == 0
    assert set(counts) == {r.key for r in ds.REVIEW_REASONS}


def test_rail_counts_agree_with_the_rows_they_filter_to():
    rows = [SETTLED,
            {**SETTLED, "id": 2, "ocr_status": "failed"},
            {**SETTLED, "id": 3, "ocr_status": "failed"}]
    screen = _screen(rows)
    count = next(item["count"] for group in screen["reason_views"]
                 for item in group["reasons"] if item["key"] == "ocr_failed")
    assert count == 2
    assert [r["id"] for r in _screen(rows, flag="ocr_failed")["rows"]] == [2, 3]


def test_excluded_notes_are_shipped_to_the_ui():
    """The deliberate omissions are stated on the screen, not left implicit in a docstring."""
    notes = dict(_screen([SETTLED])["excluded_notes"])
    assert "Ownership proposals" in notes
    assert "Unassigned Documents" in notes["Ownership proposals"]
    assert "Knowledge classification" in notes


# ---------------------------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------------------------

def test_an_unknown_reason_key_falls_back_to_everything():
    """A renamed reason or a stale bookmark must not render a convincing "0 documents". An
    unrecognised key is a BROKEN LINK, not an empty queue."""
    rows = [SETTLED, {**SETTLED, "id": 2, "ocr_status": "failed"}]
    screen = _screen(rows, flag="no_such_reason")
    assert screen["total"] == 2
    assert screen["view"] == "all"
    # And the dead key is not then carried along in every link the screen builds.
    assert "no_such_reason" not in screen["query_keep"]


def test_a_reason_filter_narrows_the_list_not_only_the_counts():
    rows = [SETTLED, {**SETTLED, "id": 2, "category": None, "classification": None}]
    screen = _screen(rows, flag="category_missing")
    assert [r["id"] for r in screen["rows"]] == [2]
    assert screen["view"] == "flag:category_missing"
    assert "&dflag=category_missing" in screen["query_keep"]


def test_the_incomplete_view_excludes_a_document_whose_only_problem_is_actionable():
    rows = [{**SETTLED, "id": 1, "review_status": "pending"},
            {**SETTLED, "id": 2, "category": None, "classification": None}]
    assert [r["id"] for r in _screen(rows, incomplete=True)["rows"]] == [2]
    assert _screen(rows, incomplete=True)["view"] == "incomplete"


def test_needs_review_keeps_its_established_meaning():
    """The Needs Review worklist is documents carrying an unsettled ``review_status`` — what it has
    always been, and what every existing link into it already means. The tier counts sit BESIDE it
    rather than redefining it, so a document that is merely missing a category does not appear on
    the review worklist."""
    rows = [{**SETTLED, "id": 1, "review_status": "pending"},
            {**SETTLED, "id": 2, "category": None, "classification": None}]
    screen = _screen(rows)
    assert screen["needs_review_count"] == 1
    assert [r["id"] for r in _screen(rows, needs_review=True)["rows"]] == [1]


def test_the_view_flags_survive_a_search():
    screen = _screen([SETTLED], flag="ocr_failed", q="1040")
    assert "&dflag=ocr_failed" in screen["query_keep"]
    # ...and the rail's own links deliberately drop them, so switching view does not stack views.
    assert "dflag" not in screen["query_keep_no_view"]
    assert "dq=1040" in screen["query_keep_no_view"]


# ---------------------------------------------------------------------------------------------
# The rail actually renders
# ---------------------------------------------------------------------------------------------

def _render(screen) -> str:
    from app.templating import templates
    return templates.get_template("client360/_documents_screen.html").render(
        screen=screen, base="/client/7", panel_base="/client/7",
        is_canonical=True, principal=None, delete_url=None, asset_version="t")


def test_the_rail_renders_every_reason_group():
    """A render-level guard, not a duplicate of the view-model tests above.

    ``reason_views`` groups are keyed ``reasons`` rather than ``items`` because Jinja resolves
    ``group.items`` to the dict's own built-in method before it looks for a key of that name — the
    loop then dies with "object is not iterable" at request time while every view-model test still
    passes. This test is what catches that.
    """
    html = _render(_screen([SETTLED, {**SETTLED, "id": 2, "ocr_status": "failed"}]))
    for reason in ds.REVIEW_REASONS:
        assert f"dflag={reason.key}" in html, reason.key
    assert "Needs review" in html and "Incomplete metadata" in html
    assert "dincomplete=1" in html
    # The deliberate omissions are on the screen, not only in a docstring.
    assert "Unassigned Documents" in html


def test_a_reason_view_renders_its_own_banner():
    html = _render(_screen([{**SETTLED, "id": 2, "ocr_status": "failed"}], flag="ocr_failed"))
    assert "Text extraction failed" in html
    assert "retryable" in html


def test_one_malformed_row_does_not_blank_the_screen():
    """A reason whose test raises must cost that reason, never the page."""
    screen = _screen([SETTLED, {**SETTLED, "id": 2, "sources": "not-a-list"}])
    assert screen["total"] == 2
