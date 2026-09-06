"""PHASE R — reconciling the legacy filing tree into canonical identity. Pure tests, no database."""
from __future__ import annotations

import pytest

from app.services.canonical_filing import build_rows
from app.services.canonical_filing_reconcile import (
    ADOPT_FOLDER_IN_PLACE,
    ATTACH_CANONICAL_IDENTITY,
    CANONICAL_CONFLICT,
    CANONICAL_EQUIVALENT,
    CANONICAL_NEEDS_YEAR,
    CANONICAL_REVIEW,
    CANONICAL_SERVICE_MAPPING_REQUIRED,
    CANONICAL_UNRESOLVED,
    LEAVE_REVIEW,
    MOVE_DOCUMENT,
    NOOP_ALREADY_CANONICAL,
    classify_document,
    folder_identity_from_code,
    legacy_service_code,
    plan_folders,
    summarize,
)
from app.services.legacy_filing_codes import (
    category_code,
    client_code,
    parse_legacy_code,
    year_code,
)
from tests.test_canonical_filing import backing, classify_one, proposal, taxdome

CLIENT = client_code("person", 4242)
SERVICE = category_code("person", 4242, "Tax Preparation")
YEAR = year_code("person", 4242, "Tax Preparation", 2023)


# --- reading a legacy code back ------------------------------------------------------------------

def test_the_three_legacy_code_shapes_parse():
    assert parse_legacy_code(CLIENT) == {
        "kind": "client", "scope_type": "person", "scope_id": 4242,
        "category_slug": None, "tax_year": None}
    assert parse_legacy_code(SERVICE)["category_slug"] == "tax-preparation"
    assert parse_legacy_code(YEAR)["tax_year"] == 2023


def test_a_canonical_code_is_not_mistaken_for_a_legacy_one():
    assert parse_legacy_code("cf-client-person-4242--svc-tax_preparation--yr-2023") is None
    assert parse_legacy_code("something-else") is None
    assert parse_legacy_code(None) is None


def test_the_legacy_category_level_is_the_canonical_service_level():
    identity = folder_identity_from_code(SERVICE)
    assert identity["folder_kind"] == "service"
    assert identity["service_code"] == "tax_preparation"
    assert identity["tax_year"] is None


def test_every_vocabulary_label_round_trips_through_its_legacy_slug():
    from app.services.filing_service_vocabulary import FILING_SERVICES

    for service in FILING_SERVICES:
        code = category_code("person", 1, service.label)
        assert folder_identity_from_code(code)["service_code"] == service.code


def test_an_unmapped_service_slug_is_reported_not_guessed():
    identity = folder_identity_from_code("client-person-1--category-some-old-thing")
    assert identity["service_code"] is None
    assert identity["unmapped_service_slug"] == "some-old-thing"
    assert legacy_service_code("some-old-thing") is None


# --- folder adoption -----------------------------------------------------------------------------

def _folder(folder_id, code, adopted=False):
    return {"id": folder_id, "code": code,
            "owner_scope_type": "person" if adopted else None}


def test_a_legacy_chain_is_adoptable_in_place():
    plan = plan_folders([_folder(1, CLIENT), _folder(2, SERVICE), _folder(3, YEAR)])
    assert plan["action_counts"] == {ATTACH_CANONICAL_IDENTITY: 3}
    assert [f["folder_id"] for f in plan["folders"]] == [1, 2, 3]
    # Adoption never proposes a new id or a new code — that is the whole point.
    assert all(f["canonical_identity"] is not None for f in plan["folders"])
    assert plan["collisions"] == {}


def test_an_already_adopted_folder_is_a_noop():
    plan = plan_folders([_folder(1, CLIENT, adopted=True)])
    assert plan["action_counts"] == {NOOP_ALREADY_CANONICAL: 1}


def test_an_unrecognised_code_is_left_for_review():
    plan = plan_folders([_folder(1, "handmade-folder")])
    assert plan["action_counts"] == {LEAVE_REVIEW: 1}
    assert "not a recognised legacy pattern" in plan["folders"][0]["reason"]


def test_an_unmapped_service_folder_is_left_for_review():
    plan = plan_folders([_folder(1, "client-person-1--category-mystery-service")])
    assert plan["folders"][0]["proposed_action"] == LEAVE_REVIEW
    assert "not in the canonical vocabulary" in plan["folders"][0]["reason"]


def test_two_folders_claiming_one_canonical_identity_collide():
    """The partial unique index would refuse the second; the planner must say so first."""
    same = category_code("person", 4242, "Tax Preparation")
    plan = plan_folders([_folder(1, same), _folder(2, same)])
    assert plan["action_counts"] == {ATTACH_CANONICAL_IDENTITY: 1, LEAVE_REVIEW: 1}
    assert len(plan["collisions"]) == 1


def test_adoption_proposes_no_document_change_at_all():
    plan = plan_folders([_folder(1, CLIENT), _folder(2, SERVICE), _folder(3, YEAR)])
    for entry in plan["folders"]:
        assert entry["proposed_action"] in (ATTACH_CANONICAL_IDENTITY, NOOP_ALREADY_CANONICAL)
        assert "document" not in entry


# --- document classification ---------------------------------------------------------------------

def _assignment(document_id, code, folder_id=10):
    return {"document_id": document_id, "folder_id": folder_id, "folder_code": code}


def test_a_depth3_legacy_placement_that_matches_is_already_canonical():
    row = classify_one(proposal())
    record = classify_document(_assignment(row["document_id"], YEAR), row)
    assert record["classification"] == CANONICAL_EQUIVALENT
    assert record["proposed_action"] == NOOP_ALREADY_CANONICAL


def test_a_depth2_placement_with_a_strong_year_moves_down_one_level():
    row = classify_one(proposal())
    record = classify_document(_assignment(row["document_id"], SERVICE), row)
    assert record["classification"] == CANONICAL_NEEDS_YEAR
    assert record["proposed_action"] == MOVE_DOCUMENT
    assert record["canonical_identity"]["tax_year"] == 2023


def test_a_depth2_placement_without_a_strong_year_is_never_grandfathered():
    row = classify_one(proposal(tax_year_confidence="moderate"))
    record = classify_document(_assignment(row["document_id"], SERVICE), row)
    assert record["classification"] == CANONICAL_NEEDS_YEAR
    assert record["proposed_action"] == LEAVE_REVIEW
    assert "not grandfathered" in record["reason"]


def test_a_wrong_year_placement_is_a_conflict():
    row = classify_one(proposal(proposed_tax_year=2022,
                                source_path="Clients/Tax Preparation/X/2022 Return.pdf",
                                original_name="2022 Return.pdf"))
    record = classify_document(_assignment(row["document_id"], YEAR), row)
    assert record["classification"] == CANONICAL_CONFLICT
    assert record["proposed_action"] == LEAVE_REVIEW


def test_a_wrong_owner_placement_is_a_conflict():
    row = classify_one(proposal())
    other = year_code("person", 9999, "Tax Preparation", 2023)
    record = classify_document(_assignment(row["document_id"], other), row)
    assert record["classification"] == CANONICAL_CONFLICT


def test_a_wrong_service_placement_is_a_conflict():
    row = classify_one(proposal())
    other = year_code("person", 4242, "Payroll", 2023)
    record = classify_document(_assignment(row["document_id"], other), row)
    assert record["classification"] == CANONICAL_CONFLICT


def test_a_document_in_an_unparseable_folder_is_unresolved():
    row = classify_one(proposal())
    record = classify_document(_assignment(row["document_id"], "handmade"), row)
    assert record["classification"] == CANONICAL_UNRESOLVED
    assert record["proposed_action"] == LEAVE_REVIEW


def test_an_unmapped_service_needs_mapping_before_anything_else():
    row = classify_one(proposal())
    record = classify_document(
        _assignment(row["document_id"], "client-person-4242--category-mystery"), row)
    assert record["classification"] == CANONICAL_SERVICE_MAPPING_REQUIRED


def test_a_document_with_no_canonical_row_at_depth3_is_review():
    record = classify_document(_assignment(1, YEAR), None)
    assert record["classification"] == CANONICAL_REVIEW
    assert record["proposed_action"] == LEAVE_REVIEW


def test_a_taxdome_document_rescued_by_derivation_reconciles_normally():
    row = classify_one(taxdome(filing_status="UNRESOLVED",
                               reasons=["no_trustworthy_category"]), backing(20))
    record = classify_document(_assignment(row["document_id"], SERVICE), row)
    assert record["classification"] == CANONICAL_NEEDS_YEAR
    assert record["proposed_action"] == MOVE_DOCUMENT


def test_no_classification_ever_proposes_deleting_a_folder():
    rows, _ = build_rows([proposal(), proposal(tax_year_confidence="moderate")])
    records = [classify_document(_assignment(r["document_id"], SERVICE), r) for r in rows]
    assert all(r["proposed_action"] in (MOVE_DOCUMENT, LEAVE_REVIEW, NOOP_ALREADY_CANONICAL)
               for r in records)


def test_the_summary_reconciles():
    rows, _ = build_rows([proposal(), proposal(tax_year_confidence="moderate"), proposal()])
    records = [classify_document(_assignment(r["document_id"], YEAR), r) for r in rows]
    folder_plan = plan_folders([_folder(1, CLIENT), _folder(2, SERVICE), _folder(3, YEAR)])
    summary = summarize(folder_plan, records)
    assert summary["documents"]["total"] == 3
    assert sum(summary["documents"]["classifications"].values()) == 3
    assert sum(summary["documents"]["actions"].values()) == 3
    assert sum(summary["folders"]["actions"].values()) == 3


@pytest.mark.parametrize("action", [
    ADOPT_FOLDER_IN_PLACE, ATTACH_CANONICAL_IDENTITY, MOVE_DOCUMENT, LEAVE_REVIEW,
    NOOP_ALREADY_CANONICAL])
def test_every_action_is_an_explicit_named_constant(action):
    assert isinstance(action, str) and action.isupper()
