"""Document filing persistence BATCH 3 — the plan, the guarded apply, and the scoped rollback.

Batch 3 files 49 documents from the tax-year-conflict lane at their depth-2 category destinations,
creating 26 folder rows and reusing 5 that earlier batches created.

Two things make this batch worth testing carefully. The first is the same as Batch 2: it must never
touch a folder it did not create, because deleting or renaming one would disturb another batch's
documents through ``ON DELETE SET NULL``. The second is specific to this population — **none of the
49 has an available source path that confirms the client**. They were excluded from Batch 2 for that
reason and carry only ``conflicting_filing_evidence`` because the preview returns that verdict before
the confirmation check runs. So the tests pin what is actually true of the lane (no different-client
context, no category conflict, tax-year conflicts only) rather than a confirmation the data lacks.

Synthetic fixtures use the preview-shaped candidate schema this batch actually reads. One test
validates the real frozen artifact.

Temp rows only, all tagged, all cleaned up. Nothing here writes to a production database.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services import document_filing_batch3 as plan_mod
from scripts import apply_document_filing_batch3 as ap
from scripts import rollback_document_filing_batch3 as rb

_TAG = f"FB3{uuid.uuid4().hex[:6]}"

FROZEN_DIR = (Path(__file__).resolve().parents[1] / "reports"
              / "document-filing-batch3-candidate-corroborated")
FROZEN_CSV = FROZEN_DIR / "document_filing_batch3_candidate.csv"
FROZEN_JSON = FROZEN_DIR / "document_filing_batch3_candidate.json"

CANDIDATE_COLUMNS = list(plan_mod.CANDIDATE_COLUMNS)


# --- synthetic candidates -------------------------------------------------------

def candidate_row(document_id, *, scope_type="person", scope_id=1, scope_name="Ada Lovelace",
                  category="Tax Preparation", segments=None, path=None, tax_year="",
                  tax_year_confidence="conflict", reasons=None, conflicts=None, contexts=None,
                  filing_status="REVIEW_REQUIRED", scope_state="resolved", client_segment=None):
    segs = segments if segments is not None else [scope_name, category]
    # By default the source path names the owner exactly, so a fixture is corroborated unless a
    # test deliberately makes it otherwise.
    segment = client_segment if client_segment is not None else scope_name
    return {
        "document_id": str(document_id), "proposed_scope_type": scope_type,
        "proposed_scope_id": str(scope_id), "proposed_scope_name": scope_name,
        "filing_scope_state": scope_state,
        "proposed_folder_segments": json.dumps(segs),
        "proposed_folder_path": path if path is not None else "/".join(segs),
        "proposed_top_level_category": category, "proposed_tax_year": tax_year,
        "tax_year_confidence": tax_year_confidence, "filing_status": filing_status,
        "reasons": json.dumps(reasons if reasons is not None else ["conflicting_filing_evidence"]),
        "conflicts": json.dumps(conflicts if conflicts is not None else
                                ["tax-year signals disagree: filename=2021, source_path=2022"]),
        "evidence": json.dumps({
            "client_contexts": contexts if contexts is not None else [["unknown", 1]],
            "sources": [{"available": True, "taxonomy": "sharepoint:tax",
                         "client_segment": segment, "system": "SharePoint", "source_id": 1}]}),
    }


def write_candidate(tmp_path, rows, *, name="candidate.csv") -> Path:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CANDIDATE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path = tmp_path / name
    path.write_bytes(buffer.getvalue().encode("utf-8"))
    return path


def build(path, **kw):
    kw.setdefault("expect_sha", plan_mod.sha256_of(Path(path)))
    kw.setdefault("expect_documents", None)
    kw.setdefault("enforce_census", False)
    return plan_mod.build_plan(path, **kw)


# --- the frozen artifact --------------------------------------------------------

def test_the_real_frozen_candidate_reproduces_the_reviewed_facts():
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 3 candidate is not present in this worktree")
    assert plan_mod.sha256_of(FROZEN_CSV) == plan_mod.CANDIDATE_CSV_SHA256
    assert plan_mod.verify_json_candidate(FROZEN_JSON) == plan_mod.CANDIDATE_JSON_SHA256
    plan = plan_mod.build_plan(FROZEN_CSV)
    assert plan["census"] == {
        "documents": 12, "client_nodes": 6, "category_nodes": 6, "year_nodes": 0,
        "folder_nodes": 12,
        "by_category": {"Sales & Litter Tax": 3, "Tax Preparation": 9},
        "by_scope_type": {"organization": 5, "person": 7}}
    assert plan["plan_digest"] == plan_mod.EXPECTED_PLAN_DIGEST
    assert plan["folder_manifest_digest"] == plan_mod.EXPECTED_FOLDER_MANIFEST_DIGEST
    assert plan["expect_new_folders"] == 12 and plan["expect_reused_folders"] == 0
    assert plan_mod.confirm_phrase(12) == "APPLY-DOCUMENT-FILING-BATCH3-12"
    assert plan_mod.rollback_phrase(12) == "ROLLBACK-DOCUMENT-FILING-BATCH3-12"
    assert all(d["tax_year"] is None and d["depth"] == 2 for d in plan["documents"])
    assert not any("--year-" in f["code"] for f in plan["folders"])


def test_the_frozen_plan_contains_only_corroborated_clients():
    """Every retained client is one whose path folder mechanically names them."""
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 3 candidate is not present in this worktree")
    plan = plan_mod.build_plan(FROZEN_CSV)
    folders = {f["code"]: f for f in plan["folders"]}
    assert folders["client-organization-144"]["name"] == "Harmony Day Support Inc"
    assert folders["client-person-7646"]["name"] == "LANDON FOSTER"
    assert set(plan["corroborations"]) == {d["document_id"] for d in plan["documents"]}
    assert {rule for rule, _segment in plan["corroborations"].values()} <= \
        set(plan_mod.CORROBORATION_RULES)
    # the ownership-only clients from the first cut are gone
    for gone in ("client-person-5765", "client-person-2003", "client-person-3021",
                 "client-person-2401", "client-person-5055"):
        assert gone not in folders


@pytest.mark.parametrize("mutation", ["dropped_row", "edited_field", "crlf", "appended_row"])
def test_a_stale_or_edited_copy_of_the_approved_artifact_is_refused(tmp_path, mutation):
    """What the guard actually pins is the BYTES, so any other population is refused.

    A superseded cut of this batch, a hand-edited row, an extra row smuggled in, a file that
    picked up CRLF in transit — none of them can be applied under the reviewed constants, and none
    of them has to be committed to the repository for this to be provable.
    """
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 3 candidate is not present in this worktree")
    approved = FROZEN_CSV.read_bytes()
    assert plan_mod.sha256_of(FROZEN_CSV) == plan_mod.CANDIDATE_CSV_SHA256
    lines = approved.split(b"\n")

    if mutation == "dropped_row":
        payload = b"\n".join(lines[:-2] + lines[-1:])
    elif mutation == "appended_row":
        payload = b"\n".join(lines[:-1] + [lines[-2], lines[-1]])
    elif mutation == "edited_field":
        payload = approved.replace(b"Tax Preparation", b"Bookkeeping", 1)
    else:
        payload = approved.replace(b"\n", b"\r\n")

    stale = tmp_path / "document_filing_batch3_candidate.csv"
    stale.write_bytes(payload)
    assert payload != approved
    assert plan_mod.sha256_of(stale) != plan_mod.CANDIDATE_CSV_SHA256
    with pytest.raises(plan_mod.PlanError, match="SHA256"):
        plan_mod.build_plan(stale)


def test_an_edited_copy_of_the_approved_json_is_refused(tmp_path):
    if not FROZEN_JSON.is_file():
        pytest.skip("the frozen batch 3 candidate is not present in this worktree")
    stale = tmp_path / "document_filing_batch3_candidate.json"
    stale.write_bytes(FROZEN_JSON.read_bytes().replace(b'"documents": 12', b'"documents": 13', 1))
    with pytest.raises(plan_mod.PlanError, match="json candidate SHA256"):
        plan_mod.verify_json_candidate(stale)


def test_the_approved_artifact_is_accepted_from_any_path(tmp_path):
    """The other half of the same property: location is not what makes an artifact approved."""
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 3 candidate is not present in this worktree")
    elsewhere = tmp_path / "somewhere-else.csv"
    elsewhere.write_bytes(FROZEN_CSV.read_bytes())
    plan = plan_mod.build_plan(elsewhere)
    assert plan["census"]["documents"] == plan_mod.EXPECTED_DOCUMENTS
    assert plan["plan_digest"] == plan_mod.EXPECTED_PLAN_DIGEST


@pytest.mark.parametrize("sha_kind", ["csv", "json"])
def test_a_tampered_artifact_is_rejected(tmp_path, sha_kind):
    if sha_kind == "csv":
        path = write_candidate(tmp_path, [candidate_row(1)])
        with pytest.raises(plan_mod.PlanError, match="SHA256"):
            plan_mod.build_plan(path, expect_sha="0" * 64, expect_documents=None,
                                enforce_census=False)
    else:
        path = tmp_path / "c.json"
        path.write_bytes(b"{}")
        with pytest.raises(plan_mod.PlanError, match="json candidate SHA256"):
            plan_mod.verify_json_candidate(path)


def test_census_drift_is_rejected(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1)])
    with pytest.raises(plan_mod.PlanError, match="rows, approved"):
        build(path, expect_documents=49)


def test_digest_drift_is_rejected(tmp_path, monkeypatch):
    path = write_candidate(tmp_path, [candidate_row(1)])
    monkeypatch.setattr(plan_mod, "EXPECTED_CLIENT_NODES", 1)
    monkeypatch.setattr(plan_mod, "EXPECTED_CATEGORY_NODES", 1)
    monkeypatch.setattr(plan_mod, "EXPECTED_FOLDER_NODES", 2)
    monkeypatch.setattr(plan_mod, "EXPECTED_CATEGORY_CENSUS", {"Tax Preparation": 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_SCOPE_CENSUS", {"person": 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_PLAN_DIGEST", "0" * 64)
    with pytest.raises(plan_mod.PlanError, match="plan digest"):
        plan_mod.build_plan(path, expect_sha=plan_mod.sha256_of(path), expect_documents=1)


# --- structural refusals --------------------------------------------------------

def test_duplicate_document_ids_are_rejected(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1), candidate_row(1)])
    with pytest.raises(plan_mod.PlanError, match="duplicate document_id"):
        build(path)


def test_a_year_destination_can_never_be_produced(tmp_path):
    """A third segment is refused, so no year node can enter the manifest."""
    path = write_candidate(tmp_path, [candidate_row(
        1, segments=["Ada Lovelace", "Tax Preparation", "2023"])])
    with pytest.raises(plan_mod.PlanError, match="folder depth"):
        build(path)


def test_a_proposed_tax_year_is_refused(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1, tax_year="2023")])
    with pytest.raises(plan_mod.PlanError, match="never files a year"):
        build(path)


def test_a_non_conflict_tax_year_confidence_is_refused(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1, tax_year_confidence="strong")])
    with pytest.raises(plan_mod.PlanError, match="tax_year_confidence"):
        build(path)


@pytest.mark.parametrize("reasons,match", [
    (["provenance_category_only"], "reasons are"),
    (["no_client_confirmation_in_path"], "reasons are"),
    (["conflicting_filing_evidence", "provenance_category_only"], "reasons are"),
], ids=["provenance-only", "no-client-confirmation", "extra-reason"])
def test_only_the_reviewed_reason_lane_is_accepted(tmp_path, reasons, match):
    path = write_candidate(tmp_path, [candidate_row(1, reasons=reasons)])
    with pytest.raises(plan_mod.PlanError, match=match):
        build(path)


@pytest.mark.parametrize("conflicts,match", [
    (["available sources disagree on category: Payroll, Tax Preparation"], "non-tax-year conflict"),
    ([], "carries no conflicts"),
], ids=["category-conflict", "no-conflict"])
def test_non_tax_year_conflicts_are_refused(tmp_path, conflicts, match):
    path = write_candidate(tmp_path, [candidate_row(1, conflicts=conflicts)])
    with pytest.raises(plan_mod.PlanError, match=match):
        build(path)


def test_a_different_client_context_is_refused(tmp_path):
    """The residual safety property of this lane: the path may not name a DIFFERENT known client."""
    path = write_candidate(tmp_path, [candidate_row(1, contexts=[["different_client", 1]])])
    with pytest.raises(plan_mod.PlanError, match="different-client context"):
        build(path)


def test_an_unconfirmed_client_is_accepted_because_that_is_this_lane(tmp_path):
    """None of the 49 has path confirmation. Requiring it would reject the reviewed population."""
    plan = build(write_candidate(tmp_path, [candidate_row(1, contexts=[["unknown", 1]])]))
    assert plan["census"]["documents"] == 1


@pytest.mark.parametrize("kwargs,match", [
    ({"filing_status": "AUTO_FILE_SAFE"}, "filing_status"),
    ({"scope_state": "conflict"}, "filing_scope_state"),
    ({"scope_type": "vendor"}, "scope type"),
    ({"segments": ["Somebody Else", "Tax Preparation"]}, "!= scope name"),
    ({"segments": ["Ada Lovelace", "Payroll"]}, "!= category"),
    ({"path": "Ada Lovelace/Wrong"}, "does not match its segments"),
], ids=["status", "scope-state", "scope-type", "client", "category", "path"])
def test_field_mismatches_are_refused(tmp_path, kwargs, match):
    path = write_candidate(tmp_path, [candidate_row(1, **kwargs)])
    with pytest.raises(plan_mod.PlanError, match=match):
        build(path)


def test_a_code_claimed_by_two_names_is_a_collision(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1, scope_id=7, scope_name="Ada Lovelace"),
                                      candidate_row(2, scope_id=7, scope_name="Someone Else")])
    with pytest.raises(plan_mod.PlanError, match="two different names"):
        build(path)


def test_plan_digest_is_content_addressed(tmp_path):
    rows = [candidate_row(2), candidate_row(1)]
    a = build(write_candidate(tmp_path, rows, name="a.csv"))
    b = build(write_candidate(tmp_path, list(reversed(rows)), name="b.csv"))
    assert a["plan_digest"] == b["plan_digest"]
    assert a["folder_manifest_digest"] == b["folder_manifest_digest"]


# --- the client-corroboration gate ---------------------------------------------

@pytest.mark.parametrize("scope_type,owner,segment,rule", [
    ("person", "LANDON FOSTER", "FOSTER, LANDON", "exact_or_reordered"),
    ("person", "Noah Harding", "HARDING,NOAH", "exact_or_reordered"),
    ("person", "Casey Fury", "  fury ,   casey  ", "exact_or_reordered"),
    ("person", "George Stevens", "STEVENS, GEORGE & MARY", "exact_or_reordered"),
    ("organization", "Harmony Day Support Inc", "Harmony Day Support", "organization_core"),
    ("organization", "SOUTH EAST VAL6 INC", "SOUTH EAST VAL6 IINC", "organization_core"),
], ids=["last-first", "punctuation", "spacing-and-case", "joint-folder", "legal-suffix",
        "suffix-typo"])
def test_deterministic_variants_are_accepted(scope_type, owner, segment, rule):
    assert plan_mod.client_corroboration(scope_type, owner, [segment]) == (rule, segment)


@pytest.mark.parametrize("scope_type,owner,segment", [
    ("person", "Benjamin Reynolds", "Reynolds, Ben"),
    ("person", "Matt Lesiv", "LESIV,MATTHEW"),
    ("person", "Juan Lacayo", "Lacayo, JP"),
    ("person", "Brandy Mc Croskey", "McCroskey, Brandy"),
    ("person", 'Franklin "Tripp" Brown', "BROWN, FRANKLIN &"),
    ("organization", "SANTRAM CORPORATION", "Santram Inc"),
    ("organization", "Murray & Sons Electrical", "Murray & Sons"),
    ("organization", "SOUTH EAST VAL6 INC", "VAL6, INC"),
], ids=["nickname-ben", "nickname-matthew", "initials", "surname-spacing", "quoted-nickname",
        "different-legal-form", "dropped-word", "partial-org"])
def test_unsupported_variants_are_rejected(scope_type, owner, segment):
    """No nickname table, no initials, no edit distance, no invented spacing rule."""
    assert plan_mod.client_corroboration(scope_type, owner, [segment]) is None


def test_a_path_naming_a_different_person_is_rejected():
    """Sharing a surname is not corroboration — this is the confusion the gate exists to catch."""
    assert plan_mod.client_corroboration(
        "person", "Malik Shareef", ["SHAREEF, REGINALD A & FAYE S"]) is None


@pytest.mark.parametrize("segment", ["Federal", "Unemployment", "", "   ", "2023"])
def test_a_path_with_no_client_name_is_rejected(segment):
    assert plan_mod.client_corroboration(
        "organization", "MORGAN & MORGAN CONSTRUCTION INC", [segment]) is None


def test_the_organization_rule_never_applies_to_a_person():
    """core_name_tokens is a BUSINESS primitive; a person must match on their whole name."""
    assert plan_mod.client_corroboration("person", "Harmony Day Support Inc",
                                         ["Harmony Day Support"]) is None


def test_an_uncorroborated_row_cannot_enter_the_plan(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(
        1, scope_name="Benjamin Reynolds", segments=["Benjamin Reynolds", "Tax Preparation"],
        client_segment="Reynolds, Ben")])
    with pytest.raises(plan_mod.PlanError, match="mechanically names its client"):
        build(path)


def test_the_four_morgan_and_morgan_documents_cannot_enter_the_plan(tmp_path):
    """The ownership-only rows that prompted this repair, by document id."""
    rows = [candidate_row(did, scope_type="organization", scope_id=155,
                          scope_name="MORGAN & MORGAN CONSTRUCTION INC", category="Payroll",
                          client_segment=segment)
            for did, segment in ((32320, "Federal"), (32322, "Federal"),
                                 (32323, "Unemployment"), (32326, "Unemployment"))]
    for row in rows:
        path = write_candidate(tmp_path, [row], name=f"m{row['document_id']}.csv")
        with pytest.raises(plan_mod.PlanError, match="mechanically names its client"):
            build(path)


def test_reasons_omitting_no_client_confirmation_cannot_bypass_the_gate(tmp_path):
    """The absence of that reason is an early-return artefact and must not be usable as a gate."""
    row = candidate_row(1, scope_name="Benjamin Reynolds",
                        segments=["Benjamin Reynolds", "Tax Preparation"],
                        client_segment="Reynolds, Ben")
    assert json.loads(row["reasons"]) == ["conflicting_filing_evidence"]
    assert "no_client_confirmation_in_path" not in row["reasons"]
    with pytest.raises(plan_mod.PlanError, match="mechanically names its client"):
        build(write_candidate(tmp_path, [row]))


# --- database-backed ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean():
    yield
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"fb3:{_TAG}%")))]
        if ids:
            c.execute(documents.update().where(documents.c.id.in_(ids)).values(folder_id=None))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))
        c.execute(delete(folders).where(folders.c.code.like("client-person-%")))


def _person(first) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=_TAG, full_name=f"{first} {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()


def _document(person_id, *, archived=False, status="active", folder_id=None) -> int:
    filename = f"{_TAG}-{uuid.uuid4().hex[:6]}.pdf"
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=filename, stored_name=f"fb3:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status="not_required", current_version=1, person_id=person_id,
            folder_id=folder_id, tags={}).returning(documents.c.id)).scalar_one()


def _make_folder(code, name, parent_id=None, *, classification=None, created_by=1) -> int:
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        return c.execute(folders.insert().values(
            code=code, name=name, parent_folder_id=parent_id, classification=classification,
            created_by=created_by).returning(folders.c.id)).scalar_one()


def _folder_row(code):
    with engine.connect() as c:
        return c.execute(text("select id, code, name, parent_folder_id, classification, created_by "
                              "from document_folders where code = :c"),
                         {"c": code}).mappings().first()


def _folder_of(document_id):
    with engine.connect() as c:
        return c.execute(text(
            "select f.code from documents d left join document_folders f on f.id = d.folder_id "
            "where d.id = :i"), {"i": document_id}).scalar()


def _row(document_id):
    with engine.connect() as c:
        return c.execute(select(documents).where(documents.c.id == document_id)).mappings().one()


@pytest.fixture
def batch(tmp_path):
    """One client whose folders already exist (an earlier batch's), one whose do not."""
    existing_person, new_person = _person("Ada"), _person("Grace")
    existing_name, new_name = f"Ada {_TAG}", f"Grace {_TAG}"
    client_code = plan_mod.client_code("person", existing_person)
    cat_code = plan_mod.category_code("person", existing_person, "Tax Preparation")
    client_id = _make_folder(client_code, existing_name)
    cat_id = _make_folder(cat_code, "Tax Preparation", client_id)
    bystander = _document(existing_person, folder_id=cat_id)

    d1 = _document(existing_person)
    d2 = _document(new_person)
    rows = [candidate_row(d1, scope_id=existing_person, scope_name=existing_name),
            candidate_row(d2, scope_id=new_person, scope_name=new_name)]
    path = write_candidate(tmp_path, rows)
    return {"preexisting_client": client_code, "preexisting_category": cat_code,
            "preexisting_ids": (client_id, cat_id), "bystander": bystander,
            "existing_person": existing_person, "new_person": new_person,
            "ids": sorted([d1, d2]), "candidate": path, "plan": build(path),
            "out": tmp_path / "out"}


def _overrides(batch):
    return {"expect_sha": plan_mod.sha256_of(Path(batch["candidate"])),
            "expect_documents": None, "enforce_census": False}


def _run(batch, **kw):
    kw.setdefault("output_root", batch["out"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["candidate"], _plan_overrides=_overrides(batch), **kw)


def _apply(batch, **kw):
    return _run(batch, apply_changes=True,
                confirm=plan_mod.confirm_phrase(len(batch["ids"])), actor_user_id=1, **kw)


def test_dry_run_writes_nothing(batch):
    report = _run(batch)
    assert report["state"] == "PRISTINE"
    assert report["committed"] is False
    assert report["folders_created"] == 0 and report["assigned"] == 0
    assert report["folders_reused"] == 2
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_apply_refuses_without_flag_wrong_phrase_or_missing_actor(batch):
    assert _run(batch, confirm=plan_mod.confirm_phrase(2), actor_user_id=1)["assigned"] == 0
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, confirm="APPLY-WRONG", actor_user_id=1)
    with pytest.raises(SystemExit, match="actor-user-id"):
        _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(2), actor_user_id=None)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_the_actor_is_taken_from_the_cli_and_recorded(batch):
    report = _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(2), actor_user_id=1)
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        actors = {r[0] for r in c.execute(select(audit.c.actor_user_id)
                                          .where(audit.c.request_id == report["request_id"]))}
    assert actors == {1}
    created = plan_mod.client_code("person", batch["new_person"])
    assert _folder_row(created)["created_by"] == 1


def test_apply_creates_only_missing_folders_and_reuses_the_rest(batch):
    before_existing = dict(_folder_row(batch["preexisting_category"]))
    report = _apply(batch)
    assert report["committed"] is True
    assert report["folders_created"] == 2 and report["folders_reused"] == 2
    assert report["assigned"] == 2 and report["audit_rows"] == 2
    destinations = {d["document_id"]: d["folder_code"] for d in batch["plan"]["documents"]}
    for document_id in batch["ids"]:
        assert _folder_of(document_id) == destinations[document_id]
    assert dict(_folder_row(batch["preexisting_category"])) == before_existing
    assert _folder_of(batch["bystander"]) == batch["preexisting_category"]
    with engine.connect() as c:
        assert c.execute(text("select count(*) from document_folders where code like '%--year-%'")
                         ).scalar() == 0


def test_protected_fields_and_non_targets_are_untouched(batch):
    before = {i: dict(_row(i)) for i in batch["ids"]}
    bystander_before = dict(_row(batch["bystander"]))
    _apply(batch)
    for document_id in batch["ids"]:
        after, prior = dict(_row(document_id)), before[document_id]
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"folder_id", "updated_at", "updated_by_user_id"}, changed
    assert dict(_row(batch["bystander"])) == bystander_before


def test_audits_match_assignments_and_carry_the_batch3_request_id(batch):
    report = _apply(batch)
    assert report["request_id"].startswith("document-filing:DOCUMENT-FILING-BATCH3:")
    assert report["request_id"].endswith(batch["plan"]["plan_digest"][:12])
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        events = c.execute(select(audit.c.entity_id, audit.c.action, audit.c.metadata)
                           .where(audit.c.request_id == report["request_id"])).mappings().all()
    assert len(events) == 2
    assert {e["action"] for e in events} == {"document.filing_folder_assigned"}
    assert {int(e["entity_id"]) for e in events} == set(batch["ids"])
    for event in events:
        assert event["metadata"]["batch"] == "DOCUMENT-FILING-BATCH3"
        assert "tax_year" not in event["metadata"]
        assert event["metadata"]["destination_depth"] == 2


@pytest.mark.parametrize("mutation,match", [
    ({"name": "Renamed"}, "is named"),
    ({"classification": "tax"}, "non-NULL classification"),
], ids=["renamed", "reclassified"])
def test_an_incompatible_existing_folder_aborts(batch, mutation, match):
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        c.execute(folders.update().where(folders.c.code == batch["preexisting_category"])
                  .values(**mutation))
    with pytest.raises(SystemExit, match="partial or incompatible"):
        _apply(batch)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_a_colliding_pre_existing_new_node_aborts(batch):
    """A node the plan expects to be ABSENT must not already exist with foreign content."""
    code = plan_mod.client_code("person", batch["new_person"])
    _make_folder(code, "somebody else's folder")
    with pytest.raises(SystemExit, match="partial or incompatible"):
        _apply(batch)


@pytest.mark.parametrize("mutation", [{"archived": True}, {"status": "deleted"}],
                         ids=["archived", "deleted"])
def test_an_inactive_target_aborts(batch, mutation):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0]).values(**mutation))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)


def test_ownership_drift_aborts(batch):
    other = _person("Interloper")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)


def test_an_already_filed_target_aborts_as_partial(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=batch["preexisting_ids"][1]))
    with pytest.raises(SystemExit, match="partial or incompatible"):
        _apply(batch)


def test_an_exact_rerun_is_a_no_op(batch):
    assert _apply(batch)["committed"] is True
    second = _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(len(batch["ids"])),
                  actor_user_id=1)
    assert second["state"] == "ALREADY_APPLIED"
    assert second["committed"] is False
    assert second["folders_created"] == 0 and second["assigned"] == 0


def test_a_partial_state_aborts_rather_than_repairing(batch):
    _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=None))
    with pytest.raises(SystemExit, match="partial or incompatible"):
        _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(len(batch["ids"])),
             actor_user_id=1)


def test_a_late_invariant_failure_rolls_everything_back(batch, monkeypatch):
    snapshots = {"n": 0}
    original = ap.write_snapshot

    def counting(plan, locked, created, out_dir):
        snapshots["n"] += 1
        return original(plan, locked, created, out_dir)

    monkeypatch.setattr(ap, "write_snapshot", counting)
    monkeypatch.setattr("app.security.audit.write_audit_event", lambda **kwargs: None)
    with pytest.raises(RuntimeError, match="audit rows"):
        _apply(batch)
    assert snapshots["n"] == 1
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    assert _folder_row(plan_mod.client_code("person", batch["new_person"])) is None
    assert _folder_row(batch["preexisting_category"]) is not None


# --- rollback -------------------------------------------------------------------

def _receipt(report):
    return Path(report["snapshot"]).parent / "apply_receipt.json"


def _rb(batch, report, **kw):
    kw.setdefault("out", lambda *_a, **_k: None)
    kw.setdefault("expect_candidate_sha", plan_mod.sha256_of(Path(batch["candidate"])))
    return rb.run(_receipt(report), **kw)


def test_rollback_dry_run_changes_nothing(batch):
    report = _apply(batch)
    result = _rb(batch, report)
    assert result["committed"] is False and result["restored"] == 0
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is not None


def test_rollback_restores_and_deletes_only_batch3_folders(batch):
    report = _apply(batch)
    result = _rb(batch, report, apply_changes=True,
                 confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1)
    assert result["committed"] is True
    assert result["restored"] == 2 and result["folders_deleted"] == 2
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    assert _folder_row(batch["preexisting_category"]) is not None
    assert _folder_row(batch["preexisting_client"]) is not None
    assert _folder_of(batch["bystander"]) == batch["preexisting_category"]
    assert _folder_row(plan_mod.client_code("person", batch["new_person"])) is None


def test_rollback_verifies_the_snapshot_sha(batch):
    report = _apply(batch)
    snapshot = Path(report["snapshot"])
    snapshot.write_bytes(snapshot.read_bytes() + b"\n")
    with pytest.raises(SystemExit, match="snapshot has been modified"):
        _rb(batch, report)


def test_rollback_refuses_an_outside_document_in_a_created_folder(batch):
    report = _apply(batch)
    created = plan_mod.category_code("person", batch["new_person"], "Tax Preparation")
    outsider = _document(batch["new_person"])
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == outsider)
                  .values(folder_id=_folder_row(created)["id"]))
    with pytest.raises(SystemExit, match="outside this batch"):
        _rb(batch, report, apply_changes=True,
            confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1)


def test_rollback_refuses_an_unexpected_child_of_a_created_folder(batch):
    report = _apply(batch)
    created = plan_mod.client_code("person", batch["new_person"])
    _make_folder(f"client-person-{_TAG}-intruder", "intruder", _folder_row(created)["id"])
    with pytest.raises(SystemExit, match="beneath the folders it created"):
        _rb(batch, report, apply_changes=True,
            confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1)


def test_rollback_refuses_document_drift(batch):
    report = _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=batch["preexisting_ids"][0]))
    with pytest.raises(SystemExit, match="have moved since the apply"):
        _rb(batch, report, apply_changes=True,
            confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1)


def test_rollback_changes_no_protected_metadata(batch):
    before = {i: dict(_row(i)) for i in batch["ids"]}
    report = _apply(batch)
    _rb(batch, report, apply_changes=True,
        confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1)
    for document_id in batch["ids"]:
        after, prior = dict(_row(document_id)), before[document_id]
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"updated_at", "updated_by_user_id"}, changed
