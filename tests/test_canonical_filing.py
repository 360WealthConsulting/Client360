"""Canonical filing contract, TaxDome derivation, labels, manifests and the legacy retirement.

Pure tests — no database. Everything here is a function of preview rows, which is the property that
makes the plan layer hashable and reviewable as an artifact.
"""
from __future__ import annotations

import pytest

from app.services import document_filing_apply as legacy
from app.services.canonical_filing import (
    AUTO_FILE_SAFE,
    CANONICAL_DEPTH,
    EXCLUDED,
    REVIEW,
    UNRESOLVED,
    build_rows,
    client_code,
    destination_identity,
    folder_nodes,
    summarize,
    year_code,
)
from app.services.canonical_filing_phases import (
    build_phase_a_manifest,
    build_phase_b_manifest,
    folder_manifest_digest_of,
)
from app.services.filing_labels import (
    UnsafeLabelError,
    folder_safe_label,
    is_folder_safe,
    visible_label_collisions,
)
from app.services.filing_manifest import (
    PHASE_A,
    PHASE_B,
    ManifestError,
    confirm_phrase,
    digest_of,
    require_phase,
)
from app.services.filing_service_vocabulary import (
    BY_CODE,
    reconciliation_report,
    service_code_for_label,
)
from app.services.taxdome_service_derivation import (
    MIN_BACKING_DOCUMENTS,
    STATUS_DERIVED,
    STATUS_REVIEW,
    build_owner_profiles,
    derive_service,
)

# --- helpers -------------------------------------------------------------------------------------

_NEXT_ID = [1000]


def proposal(**overrides):
    """A preview proposal that is AUTO_FILE_SAFE unless an override breaks it."""
    _NEXT_ID[0] += 1
    base = {
        "document_id": _NEXT_ID[0],
        "original_name": "2023 Tax Return 8879s.pdf",
        "source": "SharePoint",
        "source_path": "Clients/Tax Preparation/Individual/Doe, Jane/2023 Tax Return 8879s.pdf",
        "proposed_scope_type": "person", "proposed_scope_id": 4242,
        "proposed_scope_name": "Jane Doe",
        "filing_scope_state": "resolved",
        "proposed_top_level_category": "Tax Preparation",
        "proposed_tax_year": 2023, "tax_year_confidence": "strong",
        "tax_year_source": "filename+source_path",
        "proposed_display_name": "1040 - Jane Doe",
        "display_name_source": "document_naming.safe_document_label",
        "proposed_document_type": "8879",
        "filing_status": "AUTO_FILE_SAFE",
        "reasons": [],
    }
    base.update(overrides)
    return base


def taxdome(**overrides):
    base = proposal(
        source="TaxDome Drive",
        source_path=r"Jane Doe\Firm docs shared with client\2023\2023 Signature Documents.pdf",
        proposed_top_level_category="Firm Deliverables",
        original_name="2023 Signature Documents.pdf",
    )
    base.update(overrides)
    return base


def backing(count, *, scope_id=4242, service="Tax Preparation", source="SharePoint"):
    """``count`` non-TaxDome labelled documents establishing an owner's service profile."""
    return [proposal(source=source, proposed_scope_id=scope_id,
                     proposed_top_level_category=service) for _ in range(count)]


def classify_one(target, evidence=()):
    rows, _ = build_rows([*evidence, target])
    return rows[-1]


# --- vocabulary ----------------------------------------------------------------------------------

def test_the_filing_vocabulary_is_the_single_reconciliation_point():
    report = reconciliation_report()
    assert set(report["mapped_to_existing_service_line"]) == {
        "tax_preparation", "payroll", "bookkeeping", "wealth_accounts"}
    # These four have no service_lines row today; the migration adds them and nothing renames data.
    assert set(report["requires_new_service_line_row"]) == {
        "sales_litter_tax", "client_services", "form_1099_processing", "tax_resolution"}


@pytest.mark.parametrize("label,code", [
    ("Tax Preparation", "tax_preparation"),
    ("sales, litter & pp tax", "sales_litter_tax"),
    ("Sales & Litter Tax", "sales_litter_tax"),
    ("Accounts", "wealth_accounts"),
    ("bookkeeping(1)", "bookkeeping"),
])
def test_known_spellings_fold_to_one_code(label, code):
    assert service_code_for_label(label) == code


@pytest.mark.parametrize("label", ["Client Uploads", "Firm Deliverables"])
def test_provenance_is_never_a_service(label):
    assert service_code_for_label(label) is None


def test_an_unknown_label_is_never_guessed():
    assert service_code_for_label("Some New Folder Nobody Mapped") is None


# --- folder-safe labels --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,safe", [
    ("Shelbe LLC / Shear Maddness", "Shelbe LLC - Shear Maddness"),
    ('Franklin "Tripp" Brown', "Franklin 'Tripp' Brown"),
    (r"A\B", "A - B"),
    ("Smith: Family", "Smith - Family"),
    ("What?", "What"),
    ("A|B", "A - B"),
    ("<Trust>", "(Trust)"),
    ("Star*Corp", "Star+Corp"),
    ("  spaced   out  ", "spaced out"),
    ("Trailing dots...", "Trailing dots"),
])
def test_folder_safe_label_rules(raw, safe):
    assert folder_safe_label(raw) == safe


def test_a_clean_label_is_untouched():
    assert is_folder_safe("Jane Doe")
    assert folder_safe_label("Jane Doe") == "Jane Doe"


def test_control_and_zero_width_characters_are_stripped():
    assert folder_safe_label("Jane​Doe\x07") == "JaneDoe"


@pytest.mark.parametrize("raw", ["", "   ", "?", "...", None])
def test_a_label_that_sanitizes_to_nothing_is_refused(raw):
    with pytest.raises(UnsafeLabelError):
        folder_safe_label(raw)


def test_visible_label_collisions_are_reported_not_merged():
    owners = {("person", 7391): "Jessica Zielske", ("person", 7769): "JESSICA ZIELSKE",
              ("organization", 122): "JERAJH INC", ("person", 7450): "JERAJH INC",
              ("person", 1): "Unique Person"}
    collisions = visible_label_collisions(owners)
    assert set(collisions) == {"jessica zielske", "jerajh inc"}
    assert collisions["jerajh inc"] == [("organization", 122), ("person", 7450)]


def test_colliding_labels_still_produce_two_distinct_folder_identities():
    a = destination_identity("organization", 122, "tax_preparation", 2023)
    b = destination_identity("person", 7450, "tax_preparation", 2023)
    assert a["folder_code"] != b["folder_code"]


# --- the destination contract --------------------------------------------------------------------

def test_a_strong_year_document_is_auto_file_safe():
    row = classify_one(proposal())
    assert row["status"] == AUTO_FILE_SAFE
    assert row["reason"] is None
    assert row["folder_segments"] == ["Jane Doe", "Tax Preparation", "2023"]
    assert len(row["folder_segments"]) == CANONICAL_DEPTH


def test_a_moderate_year_is_review_and_never_filed():
    row = classify_one(proposal(tax_year_confidence="moderate"))
    assert row["status"] == REVIEW
    assert row["reason"] == "tax_year_not_strong"
    assert row["folder_segments"] == []


def test_a_conflicting_year_is_review():
    assert classify_one(proposal(tax_year_confidence="conflict"))["reason"] == "tax_year_conflict"


def test_a_missing_year_is_unresolved():
    row = classify_one(proposal(proposed_tax_year=None, tax_year_confidence=None))
    assert row["status"] == UNRESOLVED
    assert row["reason"] == "missing_tax_year"


def test_an_implausible_year_is_not_a_year():
    assert classify_one(proposal(proposed_tax_year=1899))["reason"] == "missing_tax_year"


def test_no_auto_file_safe_row_can_have_depth_two():
    rows, _ = build_rows([proposal(), proposal(tax_year_confidence="moderate"),
                          proposal(proposed_tax_year=None)])
    for row in rows:
        if row["status"] == AUTO_FILE_SAFE:
            assert len(row["folder_segments"]) == CANONICAL_DEPTH
    assert summarize(rows)["depth_census"] == {CANONICAL_DEPTH: 1}


def test_ownership_conflict_is_review_and_absence_is_unresolved():
    assert classify_one(proposal(filing_scope_state="conflict"))["reason"] == "ownership_conflict"
    assert classify_one(proposal(filing_scope_state="unresolved",
                                 proposed_scope_type=None))["reason"] == "missing_owner"


def test_excluded_nonclient_material_never_reaches_the_tree():
    row = classify_one(proposal(reasons=["excluded_nonclient"]))
    assert row["status"] == EXCLUDED


def test_provenance_never_becomes_a_folder_segment():
    row = classify_one(proposal(proposed_top_level_category="Client Uploads"))
    assert row["status"] == REVIEW
    assert row["reason"] == "service_line_is_provenance_only"
    assert row["folder_segments"] == []


def test_document_type_never_becomes_a_folder_segment():
    row = classify_one(proposal(proposed_document_type="W-2"))
    assert row["status"] == AUTO_FILE_SAFE
    assert "W-2" not in row["folder_segments"]
    assert row["proposed_document_type"] == "W-2"


def test_an_unusable_owner_label_is_review_not_a_crash():
    row = classify_one(proposal(proposed_scope_name="   "))
    assert row["status"] == REVIEW
    assert row["reason"] == "owner_label_unusable"


def test_the_owner_label_is_sanitized_in_the_path_only():
    row = classify_one(proposal(proposed_scope_name="Shelbe LLC / Shear Maddness",
                                proposed_scope_type="organization", proposed_scope_id=93))
    assert row["status"] == AUTO_FILE_SAFE
    assert row["owner_source_label"] == "Shelbe LLC / Shear Maddness"
    assert row["owner_folder_label"] == "Shelbe LLC - Shear Maddness"
    assert row["owner_label_sanitized"] is True
    assert len(row["folder_segments"]) == CANONICAL_DEPTH
    assert row["folder_path"].count("/") == 2


def test_the_census_reconciles():
    rows, _ = build_rows([proposal(), proposal(tax_year_confidence="moderate"),
                          proposal(proposed_tax_year=None), proposal(reasons=["excluded_nonclient"]),
                          proposal(filing_scope_state="conflict")])
    census = summarize(rows)
    assert sum(census["buckets"].values()) == census["total_rows"] == 5
    assert sum(census["first_fail_reasons"].values()) == 5 - census["auto_file_safe"]


# --- naming --------------------------------------------------------------------------------------

def test_the_existing_naming_engine_is_reported_never_reimplemented():
    row = classify_one(proposal())
    assert row["display_name_source"] == "document_naming.safe_document_label"
    assert row["display_name_quality"] == "engine_named"
    assert row["raw_filename_fallback"] is False


def test_a_useful_raw_filename_is_measurable_but_does_not_block_placement():
    row = classify_one(proposal(proposed_display_name="2023 Tax Return 8879s.pdf"))
    assert row["status"] == AUTO_FILE_SAFE
    assert row["raw_filename_fallback"] is True
    assert row["display_name_quality"] == "raw_filename_already_useful"
    assert row["phase_b_naming_ok"] is True


def test_a_document_with_no_name_at_all_is_review():
    assert classify_one(proposal(proposed_display_name=""))["reason"] == "missing_display_name"


# --- TaxDome derivation --------------------------------------------------------------------------

def test_taxdome_single_service_owner_with_enough_backing_is_derived():
    row = classify_one(taxdome(), backing(MIN_BACKING_DOCUMENTS))
    assert row["status"] == AUTO_FILE_SAFE
    assert row["service_code"] == "tax_preparation"
    assert row["service_source"] == "taxdome_owner_profile_derivation"
    assert row["derivation"]["backing_document_count"] == MIN_BACKING_DOCUMENTS
    assert row["derivation"]["derivation_rule"] == "owner_profile_single_service_v1"
    assert row["derivation"]["backing_digest"]


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_taxdome_owner_below_the_minimum_backing_is_rejected(count):
    row = classify_one(taxdome(), backing(count))
    assert row["status"] == REVIEW
    assert row["reason"] == "taxdome_owner_profile_below_minimum"


def test_taxdome_multi_service_owner_is_never_collapsed_to_a_majority():
    evidence = backing(20) + backing(2, service="Payroll")
    row = classify_one(taxdome(), evidence)
    assert row["status"] == REVIEW
    assert row["reason"] == "taxdome_owner_multiple_services"
    assert row["service_code"] is None


def test_taxdome_owner_with_no_service_evidence_is_unresolved():
    row = classify_one(taxdome())
    assert row["status"] == UNRESOLVED
    assert row["reason"] == "taxdome_owner_no_established_service"


def test_taxdome_unsorted_is_review_even_with_a_perfect_owner_profile():
    row = classify_one(
        taxdome(source_path=r"Jane Doe\Client uploaded documents\Unsorted\2023 W2.pdf"),
        backing(20))
    assert row["status"] == REVIEW
    assert row["reason"] == "taxdome_unsorted"


def test_the_content_veto_removes_a_derivation_and_never_creates_one():
    row = classify_one(taxdome(original_name="Riverlight Profit and Loss 2023.pdf"), backing(20))
    assert row["status"] == REVIEW
    assert row["reason"] == "taxdome_content_contradiction_veto"
    assert row["derivation"]["contradiction_veto"] is True
    assert row["service_code"] is None
    # The cue named bookkeeping; the veto must not have FILED it as bookkeeping.
    assert "bookkeeping" in row["derivation"]["veto_cues"]


def test_agreeing_content_does_not_veto():
    row = classify_one(taxdome(original_name="2023 Tax Return 8879.pdf"), backing(20))
    assert row["status"] == AUTO_FILE_SAFE


def test_silent_content_does_not_veto():
    row = classify_one(taxdome(original_name="scan_240223-154351.pdf"), backing(20))
    assert row["status"] == AUTO_FILE_SAFE


def test_taxdome_documents_never_back_a_profile():
    """Otherwise a derived service would become evidence for the next derivation."""
    evidence = [taxdome(proposed_top_level_category="Tax Preparation") for _ in range(20)]
    profiles = build_owner_profiles(evidence)
    assert profiles == {}


def test_provenance_labels_never_back_a_profile():
    evidence = [proposal(proposed_top_level_category="Client Uploads") for _ in range(20)]
    assert build_owner_profiles(evidence) == {}


def test_derivation_provenance_is_complete():
    profiles = build_owner_profiles(backing(7))
    record = derive_service(taxdome(), profiles)
    assert record["status"] == STATUS_DERIVED
    for field in ("derivation_rule", "owner_scope_type", "owner_scope_id", "derived_service_line",
                  "backing_document_count", "backing_digest", "status", "contradiction_veto",
                  "min_backing_documents"):
        assert field in record
    assert record["derived_service_line"] in BY_CODE


def test_a_derived_service_is_distinguishable_from_an_asserted_one():
    derived = classify_one(taxdome(), backing(20))
    asserted = classify_one(proposal())
    assert derived["service_source"] == "taxdome_owner_profile_derivation"
    assert asserted["service_source"] == "source_taxonomy"
    assert derived["derivation"] is not None
    assert asserted["derivation"] is None


def test_ownership_conflict_short_circuits_the_derivation():
    record = derive_service(taxdome(filing_scope_state="conflict"), {})
    assert record["status"] == STATUS_REVIEW
    assert record["reason"] == "ownership_conflict"


# --- folder identity -----------------------------------------------------------------------------

def test_folder_identity_keys_on_scope_id_not_on_the_label():
    a = classify_one(proposal(proposed_scope_name="Jane Doe"))
    b = classify_one(proposal(proposed_scope_name="Jane Q. Doe-Smith"))
    assert a["folder_code"] == b["folder_code"]


def test_folder_identity_keys_on_the_service_code_not_the_label():
    identity = destination_identity("person", 4242, "sales_litter_tax", 2023)
    assert "sales_litter_tax" in identity["folder_code"]
    assert "&" not in identity["folder_code"]


def test_folder_codes_are_deterministic_and_hierarchical():
    assert client_code("person", 42) == "cf-client-person-42"
    assert year_code("person", 42, "payroll", 2023).startswith(client_code("person", 42))
    assert year_code("person", 42, "payroll", 2023).endswith("--yr-2023")


def test_canonical_codes_cannot_collide_with_the_retired_batch_codes():
    assert client_code("person", 42).startswith("cf-")
    assert not client_code("person", 42).startswith("client-")


def test_folder_nodes_are_parents_before_children_and_deduplicated():
    rows, _ = build_rows([proposal(), proposal(proposed_tax_year=2022),
                          proposal(proposed_top_level_category="Payroll")])
    nodes = folder_nodes(rows)
    kinds = [n["kind"] for n in nodes]
    assert kinds == sorted(kinds, key=("client", "service", "year").index)
    assert len([n for n in nodes if n["kind"] == "client"]) == 1
    assert len([n for n in nodes if n["kind"] == "service"]) == 2
    assert len([n for n in nodes if n["kind"] == "year"]) == 3
    positions = {n["code"]: i for i, n in enumerate(nodes)}
    for node in nodes:
        if node["parent_code"]:
            assert positions[node["parent_code"]] < positions[node["code"]]


def test_one_code_claimed_by_two_names_is_a_collision():
    rows, _ = build_rows([proposal(proposed_scope_name="Jane Doe"),
                          proposal(proposed_scope_name="Janet Doe")])
    with pytest.raises(ManifestError, match="claimed twice"):
        folder_nodes(rows)


# --- manifests -----------------------------------------------------------------------------------

def test_phase_a_manifest_carries_folders_and_no_documents():
    rows, _ = build_rows([proposal(), proposal(proposed_tax_year=2022)])
    manifest = build_phase_a_manifest(rows)
    assert manifest["phase"] == PHASE_A
    assert "documents" not in manifest and "assignments" not in manifest
    assert manifest["folder_count"] == len(manifest["folders"]) == 4
    assert manifest["census"] == {"client": 1, "service": 1, "year": 2}
    assert manifest["confirm_phrase"] == "APPLY-CANONICAL-FILING-PHASE_A_FOLDERS-4"


def test_phase_b_manifest_binds_to_the_phase_a_it_was_planned_against():
    rows, _ = build_rows([proposal()])
    phase_a = build_phase_a_manifest(rows)
    phase_b = build_phase_b_manifest(rows, phase_a)
    assert phase_b["phase"] == PHASE_B
    assert phase_b["folder_manifest_digest"] == phase_a["folder_manifest_digest"]
    assert phase_b["document_count"] == 1
    assert phase_b["confirm_phrase"] == "APPLY-CANONICAL-FILING-PHASE_B_DOCUMENTS-1"


def test_phase_b_refuses_to_plan_against_a_phase_b_manifest():
    rows, _ = build_rows([proposal()])
    phase_a = build_phase_a_manifest(rows)
    phase_b = build_phase_b_manifest(rows, phase_a)
    with pytest.raises(ManifestError, match="expected a PHASE_A_FOLDERS manifest"):
        build_phase_b_manifest(rows, phase_b)


def test_phase_b_cannot_name_a_folder_phase_a_does_not_create():
    rows, _ = build_rows([proposal(), proposal(proposed_tax_year=2019)])
    phase_a = build_phase_a_manifest(rows[:1])
    with pytest.raises(ManifestError, match="phase B may never create a folder"):
        build_phase_b_manifest(rows, phase_a)


def test_a_phase_a_manifest_is_refused_by_a_phase_b_gate_and_the_reverse():
    rows, _ = build_rows([proposal()])
    phase_a = build_phase_a_manifest(rows)
    phase_b = build_phase_b_manifest(rows, phase_a)
    require_phase(phase_a, PHASE_A)
    require_phase(phase_b, PHASE_B)
    with pytest.raises(ManifestError, match="one authorization may never execute both phases"):
        require_phase(phase_a, PHASE_B)
    with pytest.raises(ManifestError, match="one authorization may never execute both phases"):
        require_phase(phase_b, PHASE_A)


def test_an_untagged_manifest_is_refused():
    with pytest.raises(ManifestError, match="no valid phase tag"):
        require_phase({"folders": []}, PHASE_A)


def test_confirmation_phrases_encode_phase_and_size():
    assert confirm_phrase(PHASE_A, 1621) == "APPLY-CANONICAL-FILING-PHASE_A_FOLDERS-1621"
    assert confirm_phrase(PHASE_B, 1621) == "APPLY-CANONICAL-FILING-PHASE_B_DOCUMENTS-1621"
    assert confirm_phrase(PHASE_A, 1621) != confirm_phrase(PHASE_B, 1621)


def test_manifest_tampering_changes_the_digest():
    rows, _ = build_rows([proposal()])
    manifest = build_phase_a_manifest(rows)
    tampered = [dict(node) for node in manifest["folders"]]
    tampered[0]["name"] = "Somebody Else"
    assert folder_manifest_digest_of(tampered) != manifest["folder_manifest_digest"]


def test_scope_drift_changes_the_assignment_digest():
    rows, _ = build_rows([proposal()])
    phase_a = build_phase_a_manifest(rows)
    phase_b = build_phase_b_manifest(rows, phase_a)
    widened = [*phase_b["assignments"],
               {**phase_b["assignments"][0], "document_id": 999999}]
    from app.services.canonical_filing_phases import ASSIGNMENT_FIELDS
    assert digest_of([{k: a[k] for k in ASSIGNMENT_FIELDS} for a in widened]) \
        != phase_b["assignment_digest"]


def test_digests_are_content_addressed_not_order_dependent():
    rows, _ = build_rows([proposal(), proposal(proposed_tax_year=2021)])
    first = build_phase_a_manifest(rows)
    second = build_phase_a_manifest(list(reversed(rows)))
    assert first["folder_manifest_digest"] == second["folder_manifest_digest"]


def test_a_duplicate_document_id_is_refused():
    rows, _ = build_rows([proposal()])
    with pytest.raises(ManifestError, match="duplicate document_id"):
        build_phase_a_manifest([rows[0], dict(rows[0])])


# --- the retired batch-1 path --------------------------------------------------------------------

def test_the_legacy_batch_is_marked_retired():
    assert legacy.RETIRED is True


@pytest.mark.parametrize("entry_point", legacy.RETIRED_ENTRY_POINTS)
def test_every_legacy_apply_entry_point_refuses_to_run(entry_point):
    with pytest.raises(legacy.LegacyBatchRetired):
        getattr(legacy, entry_point)("anything")


def test_the_retired_set_is_exactly_the_apply_path():
    assert set(legacy.RETIRED_ENTRY_POINTS) == {
        "build_plan", "read_frozen_rows", "confirm_phrase", "rollback_phrase"}


@pytest.mark.parametrize("symbol", [
    "FOLDER_FIELDS", "FOLDER_KINDS", "PLAN_FIELDS", "PlanError", "category_code", "client_code",
    "folder_manifest_digest", "plan_digest", "sha256_of", "slugify",
])
def test_batch2s_borrowed_primitives_still_work(symbol):
    """Batch 2 is merged and applied in production; retiring a POLICY must not break its arithmetic."""
    assert hasattr(legacy, symbol)


def test_the_borrowed_primitives_are_the_frozen_ones():
    # 16,854 production documents sit in folders these two generated. They are frozen, not
    # maintained: a changed slug rule would orphan live folder references.
    assert legacy.slugify("Sales & Litter Tax") == "sales-litter-tax"
    assert legacy.client_code("person", 5830) == "client-person-5830"
    assert legacy.category_code("person", 5830, "Tax Preparation") == \
        "client-person-5830--category-tax-preparation"
    assert legacy.year_code("person", 5830, "Tax Preparation", 2023) == \
        "client-person-5830--category-tax-preparation--year-2023"


def test_the_merged_batch2_module_still_imports():
    import importlib

    module = importlib.import_module("app.services.document_filing_batch2")
    assert module.DESTINATION_DEPTH == 2  # legacy policy, preserved but not adopted
    assert module.EXPECTED_DOCUMENTS == 550


def test_the_old_confirmation_phrase_can_no_longer_be_produced():
    with pytest.raises(legacy.LegacyBatchRetired):
        legacy.confirm_phrase(16304)
    # It survives only as a recorded string, and it is not a phrase the new gates accept.
    assert legacy.LEGACY_CONFIRM_PHRASE == "APPLY-DOCUMENT-FILING-BATCH1-16304"
    assert legacy.LEGACY_CONFIRM_PHRASE != confirm_phrase(PHASE_A, 16304)
    assert legacy.LEGACY_CONFIRM_PHRASE != confirm_phrase(PHASE_B, 16304)


def test_the_old_frozen_manifest_cannot_be_turned_into_a_plan(tmp_path):
    frozen = tmp_path / "old_preview.csv"
    frozen.write_text("document_id,filing_status\n1,AUTO_FILE_SAFE\n", encoding="utf-8")
    with pytest.raises(legacy.LegacyBatchRetired):
        legacy.build_plan(frozen)


def test_the_legacy_apply_scripts_are_gone():
    import importlib

    for name in ("scripts.apply_document_filing", "scripts.rollback_document_filing"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_the_retired_depth_census_is_recorded_as_the_reason_not_as_a_target():
    # 9,361 depth-2 documents is why the batch is retired. It must never be reachable again.
    assert legacy.LEGACY_EXPECTED_DEPTH_CENSUS == {2: 9361, 3: 6943}
    rows, _ = build_rows([proposal(tax_year_confidence="moderate")])
    assert rows[0]["status"] == REVIEW


# --- the legacy aggregate status must not veto canonical derivation ------------------------------

@pytest.mark.parametrize("legacy_reason", [
    "no_trustworthy_category", "no_client_taxonomy_source", "operational_paths_only"])
def test_a_legacy_path_taxonomy_failure_no_longer_vetoes_a_derived_taxdome_document(legacy_reason):
    """The exact condition owner_profile_single_service_v1 exists to solve must not block it."""
    row = classify_one(
        taxdome(filing_status="UNRESOLVED", reasons=[legacy_reason]), backing(MIN_BACKING_DOCUMENTS))
    assert row["status"] == AUTO_FILE_SAFE
    assert row["service_source"] == "taxdome_owner_profile_derivation"
    assert row["folder_segments"] == ["Jane Doe", "Tax Preparation", "2023"]


def test_the_aggregate_legacy_status_is_not_consulted_at_all():
    a = classify_one(taxdome(filing_status="UNRESOLVED", reasons=["no_trustworthy_category"]),
                     backing(20))
    b = classify_one(taxdome(filing_status="AUTO_FILE_SAFE", reasons=[]), backing(20))
    assert a["status"] == b["status"] == AUTO_FILE_SAFE


def test_a_non_taxdome_document_with_no_service_is_still_not_filed():
    row = classify_one(proposal(proposed_top_level_category=None,
                                filing_status="UNRESOLVED", reasons=["no_trustworthy_category"]))
    assert row["status"] == UNRESOLVED
    assert row["reason"] == "missing_service_line"


def test_a_non_taxdome_provenance_only_category_is_still_review():
    row = classify_one(proposal(proposed_top_level_category="Client Uploads",
                                filing_status="UNRESOLVED"))
    assert row["reason"] == "service_line_is_provenance_only"


# --- conflicting client context is now an explicit gate ------------------------------------------

def test_the_conflicting_client_reason_always_blocks():
    row = classify_one(taxdome(reasons=["conflicting_client_context"]), backing(20))
    assert row["status"] == REVIEW
    assert row["reason"] == "conflicting_client_context"


def test_a_path_naming_a_different_known_client_always_blocks():
    """The legacy engine returns before its own check when the taxonomy is unreadable, but it has
    already appended the conflict — so the conflict string is the signal, not the reason code."""
    row = classify_one(
        taxdome(filing_status="UNRESOLVED", reasons=["no_trustworthy_category"],
                conflicts=["a source path names a different known client"]),
        backing(20))
    assert row["status"] == REVIEW
    assert row["reason"] == "conflicting_client_context"


def test_dropping_the_aggregate_status_cannot_admit_a_different_client_conflict():
    # Same document, once with the legacy status and once without: blocked either way.
    for status in ("UNRESOLVED", "AUTO_FILE_SAFE"):
        row = classify_one(
            taxdome(filing_status=status,
                    conflicts=["a source path names a different known client"]), backing(20))
        assert row["reason"] == "conflicting_client_context"


def test_the_different_client_gate_outranks_service_and_year():
    row = classify_one(
        taxdome(proposed_tax_year=None, tax_year_confidence=None,
                conflicts=["a source path names a different known client"]), backing(20))
    assert row["reason"] == "conflicting_client_context"


def test_an_unrelated_conflict_string_does_not_block():
    row = classify_one(taxdome(conflicts=["tax-year signals disagree: filename=2022"]), backing(20))
    assert row["status"] == AUTO_FILE_SAFE


# --- naming quality: a Phase B gate, never a Phase A one -----------------------------------------

@pytest.mark.parametrize("name,useful", [
    ("MCC 1099 2023.pdf", True),
    ("2023 Signature Documents (CASPER AARON).pdf", True),
    ("2021 8879 S.pdf", True),          # no three-letter word, still a real name
    ("M w2 2023.pdf", True),
    ("Chapel 2023.pdf", True),
    ("Sch C Income Expense Worksheet.pdf", True),
    ("IMG_4695.jpg", False),
    ("DSC00123.JPG", False),
    ("20220325_155854.jpg", False),
    ("d1888c09-a1d1-49c2-881b-29c348f3a265.pdf", False),
    ("COMMPREF 0001-STATEMENT-01-13-2023-2eff75b5-af22-4c1a.pdf", False),
    ("document.pdf", False),
    ("Untitled.pdf", False),
    ("scan_240223-154351.pdf", False),
])
def test_the_naming_quality_rule_is_deterministic(name, useful):
    from app.services.filing_name_quality import is_useful_filename

    assert is_useful_filename(name) is useful


def test_naming_quality_depends_only_on_the_strings():
    from app.services.filing_name_quality import classify as name_classify

    assert name_classify("1040 - Jane Doe", "IMG_1.jpg") == "engine_named"
    assert name_classify("MCC 1099 2023.pdf", "MCC 1099 2023.pdf") == "raw_filename_already_useful"
    assert name_classify("IMG_4695.jpg", "IMG_4695.jpg") == "raw_filename_low_quality"
    assert name_classify("", "x.pdf") == "missing"


def test_an_opaque_filename_still_gets_a_canonical_destination():
    """Placement and naming are independent — Phase A must see this document."""
    row = classify_one(proposal(original_name="IMG_4695.jpg", proposed_display_name="IMG_4695.jpg"))
    assert row["status"] == AUTO_FILE_SAFE
    assert row["folder_segments"] == ["Jane Doe", "Tax Preparation", "2023"]
    assert row["display_name_quality"] == "raw_filename_low_quality"
    assert row["phase_b_naming_ok"] is False


def test_phase_a_ignores_naming_quality_and_phase_b_does_not():
    good = proposal()
    opaque = proposal(original_name="IMG_4695.jpg", proposed_display_name="IMG_4695.jpg",
                      proposed_tax_year=2022)
    rows, _ = build_rows([good, opaque])
    phase_a = build_phase_a_manifest(rows)
    phase_b = build_phase_b_manifest(rows, phase_a)
    # Phase A builds the tree for BOTH documents — two years under one client/service.
    assert phase_a["census"] == {"client": 1, "service": 1, "year": 2}
    # Phase B admits only the well-named one, and says so.
    assert phase_b["document_count"] == 1
    assert phase_b["naming_hold_count"] == 1
    assert phase_b["naming_hold_document_ids"] == [opaque["document_id"]]


def test_phase_b_accepts_engine_named_and_deterministically_useful_names():
    engine = proposal(proposed_display_name="1040 - Jane Doe")
    useful = proposal(proposed_display_name="MCC 1099 2023.pdf", original_name="MCC 1099 2023.pdf")
    rows, _ = build_rows([engine, useful])
    phase_b = build_phase_b_manifest(rows, build_phase_a_manifest(rows))
    assert phase_b["document_count"] == 2
    assert phase_b["naming_hold_count"] == 0
    assert phase_b["by_display_name_quality"] == {
        "engine_named": 1, "raw_filename_already_useful": 1}
