"""Document filing preview — the rules, the conflicts, and the guarantee that nothing is written.

The preview proposes a destination for every active document. A wrong destination is worse than no
destination, so most of what follows pins a REFUSAL: an operational backup path that must not become
client evidence, a provenance bucket that must not become a filing category, a four-digit number that
must not become a tax year, a classifier guess that must not create a destination on its own.

Almost every test here is PURE — the service's core takes a document row and its source rows and
returns a proposal with no database access at all — which is what makes the filing rules testable one
clause at a time. The handful of database-backed tests cover the corpus query, the read-only
transaction, and the fact that a filtered run agrees with a full one.
"""
from __future__ import annotations

import csv
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db import documents, engine, metadata, people
from app.services import document_filing_preview as fp
from scripts import preview_document_filing as cli

_TAG = f"FPV{uuid.uuid4().hex[:6]}"

SP_TAX = "https://example.sharepoint.com/sites/360Data/Shared%20Documents"
TD_ROOT = "Z:"


# --- builders -----------------------------------------------------------------

def sp_uri(*segments: str) -> str:
    from urllib.parse import quote
    return SP_TAX + "/" + "/".join(quote(s) for s in segments)


def sp_source(*segments, source_id=1, available=True, filename="doc.pdf"):
    """A SharePoint source row. ``segments`` are the folders under /Shared Documents/."""
    return {"id": source_id, "source_system": "SharePoint", "available": available,
            "source_uri": sp_uri(*segments, filename),
            # Deliberately a drive-id path, exactly as production stores it: the reader must ignore
            # this field for SharePoint and read the hierarchy from the URL.
            "source_path": f"1d5e7f6a-guid/b!drive/General/{'/'.join(segments)}/{filename}",
            "source_external_id": f"EXT{source_id}"}


def td_source(*segments, source_id=1, available=True, filename="doc.pdf"):
    """A TaxDome source row: the hierarchy lives in source_path (``Z:\\Client\\...``)."""
    bs = chr(92)
    return {"id": source_id, "source_system": "TaxDome Drive", "available": available,
            "source_uri": f"C:{bs}Client360{bs}data{bs}Documents{bs}TaxDome{bs}"
                          + bs.join([*segments, filename]),
            "source_path": TD_ROOT + bs + bs.join([*segments, filename]),
            "source_external_id": f"TD{source_id}"}


def doc_row(**kw):
    row = {"id": 1, "original_name": "doc.pdf", "display_name": None, "storage_path": "",
           "tags": {}, "category": None, "review_status": "not_required",
           "person_id": None, "household_id": None, "organization_id": None,
           "effective_date": None}
    row.update(kw)
    return row


PEOPLE = {
    1: {"id": 1, "first_name": "Terry", "last_name": "Brown", "full_name": "Terry Brown",
        "household_id": 10},
    2: {"id": 2, "first_name": "Carla", "last_name": "Brown", "full_name": "Carla Brown",
        "household_id": 10},
    3: {"id": 3, "first_name": "Ada", "last_name": "Lovelace", "full_name": "Ada Lovelace",
        "household_id": None},
}
HOUSEHOLDS = {10: {"id": 10, "name": "Brown Household"}}
ORGANIZATIONS = {20: {"id": 20, "name": "Sunderam Inc"}}
MEMBERS = {10: [PEOPLE[1], PEOPLE[2]]}
KNOWN = fp.build_known_client_index(list(PEOPLE.values()), list(ORGANIZATIONS.values()))

REFERENCE = {"people_by_id": PEOPLE, "households_by_id": HOUSEHOLDS,
             "organizations_by_id": ORGANIZATIONS, "household_members": MEMBERS,
             "known_clients_by_token": KNOWN}


def evaluate(row, sources, **kw):
    return fp.evaluate_document(row, sources, **{**REFERENCE, **kw})


def tax_prep(*, client="Brown, Terry and Carla", year="2022", source_id=1, available=True,
             filename="doc.pdf", category="Tax Preparation"):
    segments = ["360 Tax Solutions, LLC", "Clients", category, "Individual", client]
    if year:
        segments.append(year)
    return sp_source(*segments, source_id=source_id, available=available, filename=filename)


# --- path reading -------------------------------------------------------------

def test_path_segments_prefers_the_url_for_sharepoint_and_the_drive_path_for_taxdome():
    """The hierarchy lives in a different column per system; reading the wrong one erases it."""
    sp = sp_source("360 Tax Solutions, LLC", "Clients", "Tax Preparation", "Individual", "X")
    assert fp.path_segments(sp["source_uri"], sp["source_path"]) == [
        "360 Tax Solutions, LLC", "Clients", "Tax Preparation", "Individual", "X"]
    td = td_source("Christian Green", "Client uploaded documents", "2023")
    assert fp.path_segments(td["source_uri"], td["source_path"]) == [
        "Christian Green", "Client uploaded documents", "2023"]


def test_path_segments_decodes_and_drops_the_filename():
    segments = fp.path_segments(sp_uri("360 Wealth Consulting, LLC", "Accounts",
                                       "O'Gorman, Amedee", "statement.pdf"))
    assert segments == ["360 Wealth Consulting, LLC", "Accounts", "O'Gorman, Amedee"]
    assert fp.path_segments("") == []
    assert fp.path_segments(None, None) == []


@pytest.mark.parametrize("segment,year", [
    ("2022", 2022), ("2023 Receipts", 2023), ("2020-Tax", 2020),
    ("BROWN, TERRY 2020", None), ("Tax Preparation", None), ("1099", None), ("", None),
])
def test_segment_year_only_reads_a_segment_that_leads_with_a_year(segment, year):
    assert fp.segment_year(segment) == year


def test_year_segment_rule_agrees_with_the_canonical_engine():
    """This module's year-segment rule must not drift from ``document_tax_year``'s."""
    from app.services.document_tax_year import _folder_years
    paths = ["A/2022/f.pdf", "A/2023 Receipts/f.pdf", "A/BROWN 2020/f.pdf", "A/B/f.pdf",
             "A/1999/f.pdf", "A/2020-Tax/f.pdf"]
    for path in paths:
        segments = path.split("/")[:-1]
        mine = [y for y in (fp.segment_year(s) for s in segments) if y is not None]
        assert mine == _folder_years(path), path


# --- SharePoint reading -------------------------------------------------------

def test_operational_roots_contribute_no_client_evidence():
    """AWS Migration Backup is 64,890 references of backup tree. It must never file anything."""
    for root in ("AWS Migration Backup", "Documents 1", "sites", "Pictures", "personal"):
        reading = fp.read_source(sp_source(root, "Clients", "Tax Preparation", "Individual", "X"))
        assert reading["operational"] is True
        assert reading["taxonomy"] is None
        assert reading["category"] is None
        assert reading["client_segment"] is None


def test_unknown_sharepoint_root_is_silence_not_evidence():
    reading = fp.read_source(sp_source("Some New Root", "Clients", "Tax Preparation", "X"))
    assert reading["taxonomy"] is None and reading["category"] is None
    assert reading["operational"] is False


def test_sharepoint_tax_root_reads_service_line_client_and_year():
    reading = fp.read_source(tax_prep(client="SHAREEF, REGINALD A", year="2021"))
    assert reading["taxonomy"] == "sharepoint:tax"
    assert reading["raw_category"] == "Tax Preparation"
    assert reading["category"] == "Tax Preparation"
    assert reading["client_segment"] == "SHAREEF, REGINALD A"
    assert reading["year"] == 2021


def test_sharepoint_wealth_root_reads_the_client_directly_under_accounts():
    reading = fp.read_source(sp_source("360 Wealth Consulting, LLC", "Accounts", "Lewis, Ava"))
    assert reading["taxonomy"] == "sharepoint:wealth"
    assert reading["category"] == "Wealth Accounts"
    assert reading["client_segment"] == "Lewis, Ava"


def test_generic_folder_never_becomes_a_client_or_a_category():
    reading = fp.read_source(sp_source("360 Tax Solutions, LLC", "Clients", "Bookkeeping(1)",
                                       "Inactive", "Scans"))
    assert reading["category"] == "Bookkeeping"          # the service line is still real
    assert reading["client_segment"] is None             # "Scans" is not a client
    assert fp.is_generic_segment("Unsorted") and fp.is_generic_segment("NEEDS TO BE DONE")
    assert not fp.is_generic_segment("Brown, Terry")


def test_category_labels_that_name_the_same_service_normalize_together():
    assert fp.SHAREPOINT_CATEGORY_MAP["bookkeeping(1)"] == fp.SHAREPOINT_CATEGORY_MAP["bookkeeping"]
    assert (fp.SHAREPOINT_CATEGORY_MAP["sales, litter & pp tax"]
            == fp.SHAREPOINT_CATEGORY_MAP["sales & litter tax"] == "Sales & Litter Tax")
    # and the raw label is preserved, never silently replaced
    reading = fp.read_source(tax_prep(category="Bookkeeping(1)"))
    assert reading["raw_category"] == "Bookkeeping(1)" and reading["category"] == "Bookkeeping"


def test_taxdome_reads_client_and_provenance_bucket():
    reading = fp.read_source(td_source("Christian Green", "Client uploaded documents", "2023"))
    assert reading["taxonomy"] == "taxdome"
    assert reading["client_segment"] == "Christian Green"
    assert reading["category"] == "Client Uploads"
    assert reading["year"] == 2023


# --- client scope -------------------------------------------------------------

def test_person_household_and_organization_scopes():
    person = fp.client_scope(doc_row(person_id=1), **{k: REFERENCE[k] for k in (
        "people_by_id", "households_by_id", "organizations_by_id")})
    assert person["proposed_scope_type"] == "person"
    assert person["proposed_client_scope"] == "person:1"
    assert person["proposed_scope_name"] == "Terry Brown"

    household = fp.client_scope(doc_row(household_id=10), **{k: REFERENCE[k] for k in (
        "people_by_id", "households_by_id", "organizations_by_id")})
    assert household["proposed_scope_type"] == "household"
    assert household["proposed_scope_name"] == "Brown Household"

    org = fp.client_scope(doc_row(organization_id=20), **{k: REFERENCE[k] for k in (
        "people_by_id", "households_by_id", "organizations_by_id")})
    assert org["proposed_scope_type"] == "organization"
    assert org["proposed_scope_name"] == "Sunderam Inc"


def test_multiple_ownership_scopes_are_a_conflict_not_a_preference():
    scope = fp.client_scope(doc_row(person_id=1, household_id=10), **{k: REFERENCE[k] for k in (
        "people_by_id", "households_by_id", "organizations_by_id")})
    assert scope["proposed_scope_type"] is None
    assert any("multiple ownership scopes" in c for c in scope["conflicts"])


def test_a_dangling_owner_is_not_a_filing_destination():
    scope = fp.client_scope(doc_row(person_id=999), **{k: REFERENCE[k] for k in (
        "people_by_id", "households_by_id", "organizations_by_id")})
    assert scope["proposed_client_scope"] is None
    assert any("does not resolve" in c for c in scope["conflicts"])


# --- client context -----------------------------------------------------------

def test_household_member_folder_is_consistent_context_not_a_conflict():
    """A joint folder is how the firm files a household. It must not read as a conflict."""
    owner = fp.person_name_tokens("Carla", "Brown")
    members = [fp.person_name_tokens("Terry", "Brown")]
    assert fp.client_context("Brown, Carla", owner_tokens=owner,
                             member_token_sets=members) == "owner"
    assert fp.client_context("Brown, Terry and Carla", owner_tokens=owner,
                             member_token_sets=members) == "owner"
    assert fp.client_context("Brown, Terry", owner_tokens=owner,
                             member_token_sets=members) == "household_member"


def test_a_different_known_client_is_a_true_conflict():
    owner = fp.person_name_tokens("Carla", "Brown")
    assert fp.client_context("Lovelace, Ada", owner_tokens=owner, member_token_sets=[],
                             known_clients_by_token=KNOWN) == "different_client"


def test_an_unrecognised_folder_name_is_silence_not_disagreement():
    owner = fp.person_name_tokens("Carla", "Brown")
    assert fp.client_context("Misc Scans 2021", owner_tokens=owner, member_token_sets=[],
                             known_clients_by_token=KNOWN) == "unknown"


# --- tax year -----------------------------------------------------------------

def test_independent_year_signals_that_agree_are_strong():
    row = doc_row(person_id=1, original_name="Brown 2022 return.pdf")
    year = fp._year_evidence(row, [fp.read_source(tax_prep(year="2022"))])
    assert year == {"year": 2022, "confidence": "strong", "source": "filename+source_path",
                    "evidence": {"filename": 2022, "source_path": 2022}}


def test_a_single_year_signal_is_only_moderate():
    row = doc_row(person_id=1, original_name="statement.pdf")
    year = fp._year_evidence(row, [fp.read_source(tax_prep(year="2022"))])
    assert year["year"] == 2022 and year["confidence"] == "moderate"
    assert year["evidence"] == {"source_path": 2022}


def test_a_recorded_tag_year_is_strong_on_its_own():
    row = doc_row(person_id=1, original_name="statement.pdf", tags={"tax_year": "2019"})
    year = fp._year_evidence(row, [])
    assert year["year"] == 2019 and year["confidence"] == "strong" and year["source"] == "tag"


def test_disagreeing_year_signals_are_a_conflict_and_pick_no_winner():
    row = doc_row(person_id=1, original_name="Brown 2021 return.pdf")
    year = fp._year_evidence(row, [fp.read_source(tax_prep(year="2022"))])
    assert year["year"] is None and year["confidence"] == "conflict"
    assert year["evidence"] == {"filename": 2021, "source_path": 2022}


def test_a_random_four_digit_number_is_not_a_year():
    """Account and hash fragments must not file a document into a year folder."""
    row = doc_row(person_id=1, original_name="invoice c4aa9e2000 acct 1099.pdf")
    year = fp._year_evidence(row, [])
    assert year["year"] is None and year["confidence"] == "none"
    row2 = doc_row(person_id=1, original_name="Scan_20220217 (9).png")
    assert fp._year_evidence(row2, [])["year"] is None


# --- the filing decision ------------------------------------------------------

def test_known_owner_plus_strong_sharepoint_path_is_auto_file_safe():
    row = doc_row(person_id=2, original_name="Brown 2022 return.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"
    assert proposal["proposed_folder_path"] == "Carla Brown/Tax Preparation/2022"
    assert proposal["proposed_tax_year"] == 2022
    assert proposal["category_source"] == "sharepoint:tax"


def test_organization_and_household_scopes_reach_auto_file_safe():
    org = evaluate(doc_row(organization_id=20, original_name="Sunderam 2016 sales tax.pdf"),
                   [tax_prep(client="Sunderam Inc", year="2016", category="Payroll")])
    assert org["filing_status"] == "AUTO_FILE_SAFE"
    assert org["proposed_folder_path"] == "Sunderam Inc/Payroll/2016"

    household = evaluate(doc_row(household_id=10, original_name="2022 Tax Docs.pdf"),
                         [tax_prep(client="Brown, Terry and Carla", year="2022")])
    assert household["filing_status"] == "AUTO_FILE_SAFE"
    assert household["proposed_folder_path"] == "Brown Household/Tax Preparation/2022"


def test_a_moderate_year_is_reported_but_kept_out_of_the_auto_filed_path():
    row = doc_row(person_id=2, original_name="statement.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"
    assert proposal["proposed_tax_year"] == 2022
    assert proposal["tax_year_confidence"] == "moderate"
    assert proposal["proposed_folder_path"] == "Carla Brown/Tax Preparation"


def test_a_taxdome_provenance_bucket_alone_is_never_auto_file_safe():
    """"Client uploaded documents" says who uploaded it, not what it is."""
    row = doc_row(person_id=3, original_name="W-2 2022.pdf")
    proposal = evaluate(row, [td_source("Ada Lovelace", "Client uploaded documents", "2022")])
    assert proposal["filing_status"] == "REVIEW_REQUIRED"
    assert "provenance_category_only" in proposal["reasons"]
    assert proposal["proposed_top_level_category"] == "Client Uploads"


def test_conflicting_categories_between_available_sources_require_review():
    row = doc_row(person_id=2, original_name="Brown 2022.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022", source_id=1),
                              tax_prep(client="Brown, Carla", year="2022", source_id=2,
                                       category="Payroll")])
    assert proposal["filing_status"] == "REVIEW_REQUIRED"
    assert any("disagree on category" in c for c in proposal["conflicts"])
    assert proposal["proposed_top_level_category"] is None


def test_a_provenance_bucket_does_not_conflict_with_a_real_service_line():
    """TaxDome says who uploaded it, SharePoint says what service it belongs to — different axes.

    Reading these as competing categories suppressed the real filing category on every document held
    in both systems, which is most of the tax corpus.
    """
    row = doc_row(person_id=2, original_name="Brown 2022 return.pdf")
    proposal = evaluate(row, [td_source("Brown, Carla", "Client uploaded documents", "2022",
                                        source_id=1),
                              tax_prep(client="Brown, Carla", year="2022", source_id=2)])
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"
    assert proposal["proposed_top_level_category"] == "Tax Preparation"
    assert proposal["category_source"] == "sharepoint:tax"
    assert proposal["conflicts"] == []
    # the provenance reading is kept as evidence rather than discarded
    assert proposal["evidence"]["sources"][0]["category"] in ("Client Uploads", "Tax Preparation")


def test_two_provenance_buckets_that_disagree_are_still_a_conflict():
    row = doc_row(person_id=3, original_name="doc.pdf")
    proposal = evaluate(row, [td_source("Ada Lovelace", "Client uploaded documents", source_id=1),
                              td_source("Ada Lovelace", "Firm docs shared with client",
                                        source_id=2)])
    assert proposal["filing_status"] == "REVIEW_REQUIRED"
    assert "multiple_plausible_categories" in proposal["reasons"]


def test_a_stale_source_never_overrides_a_current_one():
    row = doc_row(person_id=2, original_name="Brown 2022.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022", source_id=1),
                              tax_prep(client="Brown, Carla", year="2022", source_id=2,
                                       category="Payroll", available=False)])
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"
    assert proposal["proposed_top_level_category"] == "Tax Preparation"
    # the stale disagreement stays visible in diagnostics rather than being dropped
    assert proposal["evidence"]["stale_categories"] == ["Payroll"]


def test_conflicting_tax_years_require_review():
    row = doc_row(person_id=2, original_name="Brown 2021 return.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["filing_status"] == "REVIEW_REQUIRED"
    assert any("tax-year signals disagree" in c for c in proposal["conflicts"])


def test_a_path_naming_a_different_client_is_unresolved_not_filed():
    row = doc_row(person_id=2, original_name="doc.pdf")
    proposal = evaluate(row, [tax_prep(client="Lovelace, Ada", year="2022")])
    assert proposal["filing_status"] == "UNRESOLVED"
    assert "conflicting_client_context" in proposal["reasons"]


def test_no_owner_is_unresolved_and_never_guessed():
    proposal = evaluate(doc_row(original_name="Brown 2022.pdf"),
                        [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["filing_status"] == "UNRESOLVED"
    assert proposal["reasons"] == ["no_current_owner"]
    assert proposal["proposed_folder_path"] is None
    assert proposal["proposed_scope_id"] is None


def test_excluded_nonclient_is_never_a_client_filing_candidate():
    row = doc_row(person_id=2, original_name="Brown 2022.pdf",
                  review_status=fp.EXCLUDED_NONCLIENT_REVIEW_STATUS)
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["filing_status"] == "UNRESOLVED"
    assert proposal["reasons"] == ["excluded_nonclient"]
    assert proposal["proposed_folder_path"] is None


def test_an_operational_path_alone_is_unresolved():
    row = doc_row(person_id=2, original_name="Brown 2022.pdf")
    proposal = evaluate(row, [sp_source("AWS Migration Backup", "Users", "mike", "Brown 2022")])
    assert proposal["filing_status"] == "UNRESOLVED"
    assert "operational_paths_only" in proposal["reasons"]


def test_multiple_ownership_scopes_are_unresolved():
    proposal = evaluate(doc_row(person_id=1, household_id=10),
                        [tax_prep(client="Brown, Terry", year="2022")])
    assert proposal["filing_status"] == "UNRESOLVED"
    assert "conflicting_ownership_context" in proposal["reasons"]


def test_a_weak_classifier_cannot_create_a_destination_on_its_own():
    """No client-taxonomy path: a confident classifier still files nothing."""
    row = doc_row(person_id=2, original_name="Form W-2 wage and tax statement 2022.pdf")
    proposal = evaluate(row, [sp_source("Some New Root", "whatever")])
    assert proposal["proposed_document_type"] == "W-2"
    assert proposal["document_type_confidence"] > 0.8
    assert proposal["filing_status"] == "UNRESOLVED"
    assert proposal["proposed_folder_path"] is None


def test_classifier_disagreement_is_surfaced_not_hidden():
    row = doc_row(person_id=3, original_name="Form W-2 wage and tax statement.pdf")
    proposal = evaluate(row, [td_source("Ada Lovelace", "Client uploaded documents")])
    assert proposal["proposed_document_type"] == "W-2"
    assert "classifier_disagrees_with_provenance_category" in proposal["reasons"]
    assert proposal["evidence"]["classifier"]["type"] == "W-2"


def test_household_member_context_is_reported_on_an_auto_filed_row():
    row = doc_row(person_id=2, original_name="2022 Tax Docs.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Terry", year="2022")])
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"
    assert "household_member_context" in proposal["reasons"]
    assert proposal["filing_confidence"] < 0.9


def test_a_category_with_no_client_confirmation_requires_review():
    row = doc_row(person_id=2, original_name="doc.pdf")
    proposal = evaluate(row, [tax_prep(client="Needs To Be Done", year="2022")])
    assert proposal["filing_status"] == "REVIEW_REQUIRED"
    assert "no_client_confirmation_in_path" in proposal["reasons"]


# --- multiple sources and determinism -----------------------------------------

def test_every_source_is_considered_not_just_the_first():
    """A disagreement carried by the LAST source must still be found."""
    row = doc_row(person_id=2, original_name="Brown 2022.pdf")
    sources = [td_source("Brown, Carla", "Client uploaded documents", source_id=9),
               tax_prep(client="Brown, Carla", year="2022", source_id=3),
               tax_prep(client="Brown, Carla", year="2022", source_id=12, category="Payroll")]
    proposal = evaluate(row, sources)
    assert {s["source_id"] for s in proposal["evidence"]["sources"]} == {3, 9, 12}
    assert any("disagree on category" in c for c in proposal["conflicts"])
    assert proposal["filing_status"] == "REVIEW_REQUIRED"


def test_source_ordering_does_not_change_the_output():
    row = doc_row(person_id=2, original_name="Brown 2022.pdf")
    sources = [tax_prep(client="Brown, Carla", year="2022", source_id=3),
               tax_prep(client="Brown, Carla", year="2022", source_id=7),
               td_source("Brown, Carla", "Client uploaded documents", source_id=1,
                         available=False)]
    first = evaluate(row, sources)
    second = evaluate(row, list(reversed(sources)))
    assert json.dumps(first, sort_keys=True, default=str) == \
        json.dumps(second, sort_keys=True, default=str)


def test_a_proposal_is_json_serializable_and_carries_every_field():
    proposal = evaluate(doc_row(person_id=2), [tax_prep(client="Brown, Carla", year="2022")])
    assert set(proposal) == set(fp.PROPOSAL_FIELDS)
    json.dumps(proposal)          # must not raise


def test_evaluation_is_deterministic_across_repeated_calls():
    row = doc_row(person_id=2, original_name="Brown 2022.pdf")
    sources = [tax_prep(client="Brown, Carla", year="2022")]
    runs = [json.dumps(evaluate(row, sources), sort_keys=True) for _ in range(3)]
    assert len(set(runs)) == 1


# --- display name -------------------------------------------------------------

def test_display_name_preview_uses_the_canonical_naming_service():
    from app.services.document_naming import document_display_name, safe_document_label
    row = doc_row(person_id=2, original_name="Brown 2022 return.pdf")
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["current_display_name"] == document_display_name(row)
    assert proposal["proposed_display_name"] == safe_document_label(row, owner="Carla Brown")
    assert proposal["display_name_source"] == "document_naming.safe_document_label"
    assert proposal["display_name_changes"] is False


def test_display_name_change_is_reported_when_the_canonical_label_differs():
    row = doc_row(person_id=2, original_name="123-45-6789.pdf", tags={"tax_year": "2022"})
    proposal = evaluate(row, [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["proposed_display_name"] != row["original_name"]
    assert proposal["display_name_changes"] is True


# --- census -------------------------------------------------------------------

def test_summary_counts_and_breakdowns_are_deterministic_and_sorted():
    proposals = [
        evaluate(doc_row(id=1, person_id=2, original_name="Brown 2022.pdf"),
                 [tax_prep(client="Brown, Carla", year="2022")]),
        evaluate(doc_row(id=2, person_id=3, original_name="w2.pdf"),
                 [td_source("Ada Lovelace", "Client uploaded documents")]),
        evaluate(doc_row(id=3, original_name="orphan.pdf"), []),
    ]
    summary = fp.summarize(proposals, totals={"total_documents": 10,
                                              "total_active_documents": 5})
    census = summary["census"]
    assert census["EVALUATED"] == 3 and census["TOTAL_DOCUMENTS"] == 10
    assert census["AUTO_FILE_SAFE"] == 1 and census["REVIEW_REQUIRED"] == 1
    assert census["UNRESOLVED"] == 1
    assert census["FILING_SCOPE_RESOLVED"] == 2 and census["FILING_SCOPE_UNRESOLVED"] == 1
    assert census["FILING_SCOPE_CONFLICT"] == 0
    assert json.dumps(summary, sort_keys=True)      # serializable, no unsortable None keys
    assert list(summary["by_filing_status"]) == sorted(
        summary["by_filing_status"], key=lambda k: (-summary["by_filing_status"][k], k))


# --- accounting invariants ----------------------------------------------------
#
# Two censuses answer two different questions and DO NOT have the same totals:
#
#   RAW DATABASE OWNERSHIP   what the ownership batches move. A document owned by both a person and
#                            a household is OWNED.
#   FILING SCOPE RESOLUTION  whether this preview can name one client to file under. That same
#                            document is a CONFLICT.
#
# Reporting one under the other's name is exactly the drift these tests exist to prevent.

def test_filing_scope_states_partition_the_evaluated_set_exactly():
    proposals = [
        evaluate(doc_row(id=1, person_id=2), [tax_prep(client="Brown, Carla", year="2022")]),
        evaluate(doc_row(id=2, person_id=1, household_id=10), []),      # conflict
        evaluate(doc_row(id=3), []),                                    # unresolved
        evaluate(doc_row(id=4, organization_id=20), []),                # resolved
    ]
    summary = fp.summarize(proposals)
    census = summary["census"]
    assert census["FILING_SCOPE_RESOLVED"] == 2
    assert census["FILING_SCOPE_CONFLICT"] == 1
    assert census["FILING_SCOPE_UNRESOLVED"] == 1
    assert (census["FILING_SCOPE_RESOLVED"] + census["FILING_SCOPE_CONFLICT"]
            + census["FILING_SCOPE_UNRESOLVED"]) == census["EVALUATED"] == len(proposals)


def test_filing_status_counts_also_partition_the_evaluated_set():
    proposals = [
        evaluate(doc_row(id=1, person_id=2), [tax_prep(client="Brown, Carla", year="2022")]),
        evaluate(doc_row(id=2, person_id=3), [td_source("Ada Lovelace",
                                                        "Client uploaded documents")]),
        evaluate(doc_row(id=3), []),
    ]
    census = fp.summarize(proposals)["census"]
    assert (census["AUTO_FILE_SAFE"] + census["REVIEW_REQUIRED"] + census["UNRESOLVED"]
            == census["EVALUATED"])


def test_a_multi_scope_document_is_database_owned_but_a_filing_conflict():
    """The one case where the two censuses must disagree — and must both be reported."""
    proposal = evaluate(doc_row(person_id=1, household_id=10), [])
    assert proposal["current_person_id"] == 1 and proposal["current_household_id"] == 10
    assert proposal["filing_scope_state"] == "conflict"
    assert proposal["proposed_client_scope"] is None
    assert proposal["filing_status"] == "UNRESOLVED"
    assert "conflicting_ownership_context" in proposal["reasons"]


def test_a_person_with_no_full_name_is_still_a_resolvable_client_scope():
    """254 active documents belong to people with first+last but a NULL full_name.

    They are ordinary client documents with one column missing, not ownership conflicts, and
    reporting them as conflicts overstated FILING_SCOPE_CONFLICT by 254.
    """
    people = {**PEOPLE, 4: {"id": 4, "first_name": "LeAn", "last_name": "Hatch",
                            "full_name": None, "household_id": None}}
    proposal = fp.evaluate_document(
        doc_row(person_id=4, original_name="LeAn Hatch 2022 return.pdf"),
        [tax_prep(client="Hatch, LeAn", year="2022")],
        people_by_id=people, households_by_id=HOUSEHOLDS, organizations_by_id=ORGANIZATIONS,
        household_members=MEMBERS, known_clients_by_token=KNOWN)
    assert proposal["filing_scope_state"] == "resolved"
    assert proposal["proposed_scope_name"] == "LeAn Hatch"
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"


def test_a_scope_pointing_at_a_missing_entity_is_a_conflict():
    proposal = evaluate(doc_row(person_id=999), [tax_prep(client="Brown, Carla", year="2022")])
    assert proposal["filing_scope_state"] == "conflict"
    assert proposal["filing_status"] == "UNRESOLVED"
    assert any("does not resolve" in c for c in proposal["conflicts"])


def test_conflicting_ownership_context_is_never_auto_file_safe():
    """The hard requirement. No combination of strong filing evidence can lift a scope conflict."""
    perfect_evidence = [tax_prep(client="Brown, Carla", year="2022")]
    for row in (doc_row(person_id=1, household_id=10, original_name="Brown 2022 return.pdf"),
                doc_row(person_id=1, organization_id=20, original_name="Brown 2022 return.pdf"),
                doc_row(person_id=999, original_name="Brown 2022 return.pdf")):
        proposal = evaluate(row, perfect_evidence)
        assert proposal["filing_scope_state"] == "conflict"
        assert proposal["filing_status"] != "AUTO_FILE_SAFE"
        assert proposal["proposed_folder_path"] is None


def test_summary_exposes_raw_ownership_and_filing_scope_as_separate_sections():
    proposals = [evaluate(doc_row(id=1, person_id=2),
                          [tax_prep(client="Brown, Carla", year="2022")])]
    totals = {"total_documents": 10, "total_active_documents": 5,
              "database_ownership": {"TOTAL_ACTIVE": 5, "DATABASE_OWNED_ACTIVE": 4,
                                     "DATABASE_UNOWNED_ACTIVE": 1, "PERSON_ONLY": 3,
                                     "HOUSEHOLD_ONLY": 0, "ORGANIZATION_ONLY": 0,
                                     "MULTI_SCOPE": 1, "NO_SCOPE": 1}}
    summary = fp.summarize(proposals, totals=totals)
    assert summary["database_ownership"]["DATABASE_OWNED_ACTIVE"] == 4
    # the filing census must NOT carry ownership-shaped names that could be mistaken for the above
    for legacy in ("OWNED_ACTIVE", "UNOWNED_ACTIVE", "OWNED_AUTO_FILE_SAFE",
                   "UNOWNED_SKIPPED_OR_UNRESOLVED"):
        assert legacy not in summary["census"], f"{legacy} is ambiguous and must not return"
    assert "FILING_SCOPE_RESOLVED" in summary["census"]


def test_database_ownership_census_reconciles_exactly():
    """owned+unowned == active, and the five shapes sum to active. Read-only, against the corpus."""
    with engine.connect() as c:
        census = fp.database_ownership_census(c)
    assert census["DATABASE_OWNED_ACTIVE"] + census["DATABASE_UNOWNED_ACTIVE"] \
        == census["TOTAL_ACTIVE"]
    assert (census["PERSON_ONLY"] + census["HOUSEHOLD_ONLY"] + census["ORGANIZATION_ONLY"]
            + census["MULTI_SCOPE"] + census["NO_SCOPE"]) == census["TOTAL_ACTIVE"]
    assert census["NO_SCOPE"] == census["DATABASE_UNOWNED_ACTIVE"]


def test_filing_context_resolution_never_changes_the_raw_ownership_counts():
    """A filing conflict must not remove a document from the database-owned population."""
    with engine.connect() as c:
        before = fp.database_ownership_census(c)
        fp.build_preview(c, limit=200)
        after = fp.database_ownership_census(c)
    assert before == after


# --- no write path ------------------------------------------------------------

def test_neither_module_contains_a_write_statement():
    """The guarantee is structural: there is no INSERT/UPDATE/DELETE to reach."""
    for path in (Path(fp.__file__), Path(cli.__file__)):
        body = path.read_text(encoding="utf-8")
        code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
        for statement in (r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b"):
            assert not re.search(statement, code, re.I), f"{path.name} contains {statement}"


def test_the_cli_exposes_no_apply_or_mutation_flag():
    """No apply flag is DEFINED — checked against the parser, not against prose about it."""
    body = Path(cli.__file__).read_text(encoding="utf-8")
    assert 'add_argument("--apply' not in body
    assert not hasattr(cli, "apply")
    for name in ("apply", "apply_changes", "commit", "write"):
        assert f'add_argument("--{name.replace("_", "-")}"' not in body
    with pytest.raises(SystemExit):
        cli.main(["--apply"])       # argparse refuses: the flag does not exist


# --- database-backed ----------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean():
    yield
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"fpv:{_TAG}%")))]
        if ids:
            c.execute(delete(sources).where(sources.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))


def _live_doc(person_id, *, filename, folder):
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=filename, stored_name=f"fpv:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status="active", archived=False,
            review_status="not_required", current_version=1, person_id=person_id, tags={},
        ).returning(documents.c.id)).scalar_one()
        c.execute(sources.insert().values(
            document_id=did, source_system="SharePoint",
            source_uri=sp_uri("360 Tax Solutions, LLC", "Clients", "Tax Preparation", "Individual",
                              folder, "2022", filename),
            source_external_id=f"E{did}", available=True, metadata={}))
    return did


def test_build_preview_reads_the_corpus_and_filters_without_changing_evaluation():
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Grace", last_name=_TAG, full_name=f"Grace {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _live_doc(pid, filename=f"Grace {_TAG} 2022 return.pdf", folder=f"{_TAG}, Grace")

    scoped = fp.build_preview(person_id=pid)
    assert [p["document_id"] for p in scoped] == [did]
    proposal = scoped[0]
    assert proposal["filing_status"] == "AUTO_FILE_SAFE"
    assert proposal["proposed_folder_path"] == f"Grace {_TAG}/Tax Preparation/2022"

    by_id = fp.build_preview(document_ids=[did])
    assert by_id == scoped, "a filter selects rows; it must not change how one is evaluated"


def test_build_preview_is_ordered_by_document_id():
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Ada", last_name=_TAG, full_name=f"Ada {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    ids = sorted(_live_doc(pid, filename=f"Ada {_TAG} 2022 f{n}.pdf", folder=f"{_TAG}, Ada")
                 for n in range(3))
    assert [p["document_id"] for p in fp.build_preview(person_id=pid)] == ids


def test_the_cli_runs_inside_an_explicitly_read_only_transaction(tmp_path):
    """The read-only guarantee is enforced by the server, not by review."""
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Read", last_name=_TAG, full_name=f"Read {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    _live_doc(pid, filename=f"Read {_TAG} 2022.pdf", folder=f"{_TAG}, Read")

    result = cli.run(person_id=pid, output_dir=tmp_path / "report", skip_taxonomy=True,
                     out=lambda *_a, **_k: None)
    assert result["summary"]["census"]["EVALUATED"] == 1

    rows = list(csv.DictReader((tmp_path / "report" / "document_filing_preview.csv")
                               .open(encoding="utf-8")))
    assert [r["document_id"] for r in rows] == [str(result["proposals"][0]["document_id"])]
    written = json.loads((tmp_path / "report" / "document_filing_preview.json")
                         .read_text(encoding="utf-8"))
    assert [list(r) for r in written] == [list(fp.PROPOSAL_FIELDS)] * len(written)


def test_csv_and_json_reports_are_byte_identical_across_runs(tmp_path):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Stable", last_name=_TAG, full_name=f"Stable {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    for n in range(3):
        _live_doc(pid, filename=f"Stable {_TAG} 2022 f{n}.pdf", folder=f"{_TAG}, Stable")

    first = cli.run(person_id=pid, output_dir=tmp_path / "a", skip_taxonomy=True,
                    out=lambda *_a, **_k: None)
    second = cli.run(person_id=pid, output_dir=tmp_path / "b", skip_taxonomy=True,
                     out=lambda *_a, **_k: None)
    for name in ("document_filing_preview.csv", "document_filing_preview.json",
                 "document_filing_summary.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert first["summary"] == second["summary"]


def test_taxonomy_census_reads_both_hierarchies_read_only():
    with engine.connect() as c:
        taxdome = fp.taxdome_taxonomy(c)
        sharepoint = fp.sharepoint_taxonomy(c)
    for census in (taxdome, sharepoint):
        assert census["references"] >= 0 and census["parsed"] <= census["references"]
        assert isinstance(census["top_shapes"], dict)
        json.dumps(census)
    assert set(sharepoint["client_roots"]) == {"360 Tax Solutions, LLC",
                                               "360 Wealth Consulting, LLC"}
    assert "aws migration backup" in sharepoint["operational_roots"]
