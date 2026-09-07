"""Document filing persistence BATCH 4 — the plan, the guarded apply, and the scoped rollback.

Batch 4 files 1,285 documents from the ``no_client_confirmation_in_path`` lane. What makes this
population different from Batch 4's is that the preview recorded NO conflicts at all: the scope is
resolved, the category is a real service category, and the single thing that stopped it was that
``client_context`` could not confirm the owner from the path — because for a person it compares
against ``first_name``/``last_name`` (NULL for every person here) and for an organization against
the full name including the legal suffix (which the path omits).

So the tests pin two things above all: that the corroboration is genuinely re-derived and refuses
everything Batch 4 refuses, and that the extra refusals this larger population needs actually bite —
a single-token owner name, a substituted legal suffix, and source paths that disagree with each
other.

Synthetic fixtures use the preview-shaped candidate schema this batch reads. Several tests validate
the real frozen artifact.

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
from app.services import document_filing_batch4 as plan_mod
from app.services.document_strict_safe_ownership_batch2 import ascii_tokens
from scripts import apply_document_filing_batch4 as ap
from scripts import rollback_document_filing_batch4 as rb

_TAG = f"FB4{uuid.uuid4().hex[:6]}"

FROZEN_DIR = (Path(__file__).resolve().parents[1] / "reports"
              / "document-filing-batch4-candidate")
FROZEN_CSV = FROZEN_DIR / "document_filing_batch4_candidate.csv"
FROZEN_JSON = FROZEN_DIR / "document_filing_batch4_candidate.json"

CANDIDATE_COLUMNS = list(plan_mod.CANDIDATE_COLUMNS)


# --- synthetic candidates -------------------------------------------------------

def candidate_row(document_id, *, scope_type="person", scope_id=1, scope_name="Ada Lovelace",
                  category="Tax Preparation", segments=None, path=None, tax_year="",
                  tax_year_confidence="moderate", reasons=None, conflicts=None, contexts=None,
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
        "reasons": json.dumps(reasons if reasons is not None
                              else ["no_client_confirmation_in_path"]),
        "conflicts": json.dumps(conflicts if conflicts is not None else []),
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
        pytest.skip("the frozen batch 4 candidate is not present in this worktree")
    assert plan_mod.sha256_of(FROZEN_CSV) == plan_mod.CANDIDATE_CSV_SHA256
    assert plan_mod.verify_json_candidate(FROZEN_JSON) == plan_mod.CANDIDATE_JSON_SHA256
    plan = plan_mod.build_plan(FROZEN_CSV)
    assert plan["census"] == {
        "documents": 1285, "client_nodes": 111, "category_nodes": 112, "year_nodes": 149,
        "folder_nodes": 372,
        "by_category": {"Payroll": 45, "Sales & Litter Tax": 265, "Tax Preparation": 975},
        "by_scope_type": {"organization": 706, "person": 579},
        "by_depth": {2: 794, 3: 491}}
    assert plan["plan_digest"] == plan_mod.EXPECTED_PLAN_DIGEST
    assert plan["folder_manifest_digest"] == plan_mod.EXPECTED_FOLDER_MANIFEST_DIGEST
    assert plan["expect_new_folders"] == 357 and plan["expect_reused_folders"] == 15
    assert plan_mod.confirm_phrase(1285) == "APPLY-DOCUMENT-FILING-BATCH4-1285"
    assert plan_mod.rollback_phrase(1285) == "ROLLBACK-DOCUMENT-FILING-BATCH4-1285"
    for d in plan["documents"]:
        assert (d["depth"] == 3) == (d["tax_year"] is not None)
        assert ("--year-" in d["folder_code"]) == (d["depth"] == 3)
    assert sum(1 for f in plan["folders"] if "--year-" in f["code"]) == 149


def test_the_frozen_plan_is_corroborated_end_to_end():
    """Every retained client is one whose path folder mechanically names them."""
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 4 candidate is not present in this worktree")
    plan = plan_mod.build_plan(FROZEN_CSV)
    assert set(plan["corroborations"]) == {d["document_id"] for d in plan["documents"]}
    rules = {rule for rule, _segment in plan["corroborations"].values()}
    assert rules == {"exact_or_reordered", "organization_core"}
    # the organization rule is never used on a person
    for doc in plan["documents"]:
        rule, _segment = plan["corroborations"][doc["document_id"]]
        if rule == "organization_core":
            assert doc["scope_type"] == "organization"
    # the fifteen reused Batch 3 nodes are present and unrenamed
    codes = {f["code"]: f for f in plan["folders"]}
    assert codes["client-person-7640"]["name"] == "George Stevens"
    assert codes["client-organization-64"]["name"] == "SOUTH EAST VAL6 INC"
    # every folder's parent is also in the plan, and year nodes hang off categories
    for folder in plan["folders"]:
        if folder["parent_code"] is not None:
            assert folder["parent_code"] in codes
        if folder["kind"] == "year":
            assert codes[folder["parent_code"]]["kind"] == "category"


def test_no_owner_is_named_by_a_single_token_in_the_frozen_plan():
    """Surname-only matching would be the quiet way this batch went wrong."""
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 4 candidate is not present in this worktree")
    plan = plan_mod.build_plan(FROZEN_CSV)
    for doc in plan["documents"]:
        assert len(ascii_tokens(doc["scope_name"])) >= plan_mod.MIN_OWNER_TOKENS, doc


@pytest.mark.parametrize("mutation", ["dropped_row", "edited_field", "crlf", "appended_row"])
def test_a_stale_or_edited_copy_of_the_approved_artifact_is_refused(tmp_path, mutation):
    """What the guard actually pins is the BYTES, so any other population is refused.

    A superseded cut of this batch, a hand-edited row, an extra row smuggled in, a file that
    picked up CRLF in transit — none of them can be applied under the reviewed constants, and none
    of them has to be committed to the repository for this to be provable.
    """
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 4 candidate is not present in this worktree")
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

    stale = tmp_path / "document_filing_batch4_candidate.csv"
    stale.write_bytes(payload)
    assert payload != approved
    assert plan_mod.sha256_of(stale) != plan_mod.CANDIDATE_CSV_SHA256
    with pytest.raises(plan_mod.PlanError, match="SHA256"):
        plan_mod.build_plan(stale)


def test_an_edited_copy_of_the_approved_json_is_refused(tmp_path):
    if not FROZEN_JSON.is_file():
        pytest.skip("the frozen batch 4 candidate is not present in this worktree")
    stale = tmp_path / "document_filing_batch4_candidate.json"
    stale.write_bytes(FROZEN_JSON.read_bytes().replace(b'"documents": 12', b'"documents": 13', 1))
    with pytest.raises(plan_mod.PlanError, match="json candidate SHA256"):
        plan_mod.verify_json_candidate(stale)


def test_the_approved_artifact_is_accepted_from_any_path(tmp_path):
    """The other half of the same property: location is not what makes an artifact approved."""
    if not FROZEN_CSV.is_file():
        pytest.skip("the frozen batch 4 candidate is not present in this worktree")
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
        build(path, expect_documents=13)


def test_digest_drift_is_rejected(tmp_path, monkeypatch):
    path = write_candidate(tmp_path, [candidate_row(1)])
    monkeypatch.setattr(plan_mod, "EXPECTED_CLIENT_NODES", 1)
    monkeypatch.setattr(plan_mod, "EXPECTED_CATEGORY_NODES", 1)
    monkeypatch.setattr(plan_mod, "EXPECTED_YEAR_NODES", 0)
    monkeypatch.setattr(plan_mod, "EXPECTED_FOLDER_NODES", 2)
    monkeypatch.setattr(plan_mod, "EXPECTED_CATEGORY_CENSUS", {"Tax Preparation": 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_SCOPE_CENSUS", {"person": 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_DEPTH_CENSUS", {2: 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_PLAN_DIGEST", "0" * 64)
    with pytest.raises(plan_mod.PlanError, match="plan digest"):
        plan_mod.build_plan(path, expect_sha=plan_mod.sha256_of(path), expect_documents=1)


# --- structural refusals --------------------------------------------------------

def test_duplicate_document_ids_are_rejected(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1), candidate_row(1)])
    with pytest.raises(plan_mod.PlanError, match="duplicate document_id"):
        build(path)


def test_a_fourth_segment_is_refused(tmp_path):
    """Depth 2 and 3 are the whole vocabulary; anything deeper is not a destination this batch has."""
    path = write_candidate(tmp_path, [candidate_row(
        1, segments=["Ada Lovelace", "Tax Preparation", "2023", "Q4"],
        tax_year="2023", tax_year_confidence="strong")])
    with pytest.raises(plan_mod.PlanError, match="folder depth"):
        build(path)


def test_a_year_that_is_not_four_digits_is_refused(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(
        1, tax_year="23", tax_year_confidence="strong",
        segments=["Ada Lovelace", "Tax Preparation", "23"])])
    with pytest.raises(plan_mod.PlanError, match="not four digits"):
        build(path)


def test_a_year_segment_that_disagrees_with_the_year_is_refused(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(
        1, tax_year="2023", tax_year_confidence="strong",
        segments=["Ada Lovelace", "Tax Preparation", "2022"])])
    with pytest.raises(plan_mod.PlanError, match="third segment"):
        build(path)


def test_a_non_conflict_tax_year_confidence_is_refused(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1, tax_year_confidence="strong")])
    with pytest.raises(plan_mod.PlanError, match="tax_year_confidence"):
        build(path)


@pytest.mark.parametrize("reasons,match", [
    (["provenance_category_only"], "reasons are"),
    (["conflicting_filing_evidence"], "reasons are"),
    (["no_client_confirmation_in_path", "weak_document_type"], "reasons are"),
    ([], "reasons are"),
], ids=["provenance-only", "batch3-lane", "extra-reason", "no-reason"])
def test_only_the_reviewed_reason_lane_is_accepted(tmp_path, reasons, match):
    path = write_candidate(tmp_path, [candidate_row(1, reasons=reasons)])
    with pytest.raises(plan_mod.PlanError, match=match):
        build(path)


@pytest.mark.parametrize("conflicts", [
    ["available sources disagree on category: Payroll, Tax Preparation"],
    ["tax-year signals disagree: filename=2021, source_path=2022"],
    ["a source path names a different known client"],
], ids=["category", "tax-year", "different-client"])
def test_any_conflict_at_all_is_refused(tmp_path, conflicts):
    """This lane is defined by the preview having found NOTHING in conflict."""
    path = write_candidate(tmp_path, [candidate_row(1, conflicts=conflicts)])
    with pytest.raises(plan_mod.PlanError, match="carries conflicts"):
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
    with pytest.raises(plan_mod.PlanError, match="claimed twice"):
        build(path)


def test_plan_digest_is_content_addressed(tmp_path):
    rows = [candidate_row(2), candidate_row(1)]
    a = build(write_candidate(tmp_path, rows, name="a.csv"))
    b = build(write_candidate(tmp_path, list(reversed(rows)), name="b.csv"))
    assert a["plan_digest"] == b["plan_digest"]
    assert a["folder_manifest_digest"] == b["folder_manifest_digest"]


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
    assert report["request_id"].startswith("document-filing:DOCUMENT-FILING-BATCH4:")
    assert report["request_id"].endswith(batch["plan"]["plan_digest"][:12])
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        events = c.execute(select(audit.c.entity_id, audit.c.action, audit.c.metadata)
                           .where(audit.c.request_id == report["request_id"])).mappings().all()
    assert len(events) == 2
    assert {e["action"] for e in events} == {"document.filing_folder_assigned"}
    assert {int(e["entity_id"]) for e in events} == set(batch["ids"])
    for event in events:
        assert event["metadata"]["batch"] == "DOCUMENT-FILING-BATCH4"
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


# --- the corroboration gate ------------------------------------------------------------------

@pytest.mark.parametrize("scope_type,owner,segment,rule", [
    ("person", "GARY CUNDIFF", "CUNDIFF, GARY", "exact_or_reordered"),
    ("person", "Vivian Cundiff", "cundiff, Vivian", "exact_or_reordered"),
    ("person", "Amanda Crisfulla", "Crisfulla, Amanda", "exact_or_reordered"),
    ("person", "George Stevens", "STEVENS, GEORGE & MARY", "exact_or_reordered"),
    ("organization", "Calhoun Construction LLC", "Calhoun Construction", "organization_core"),
    ("organization", "Haul-Max Trucking LLC", "Haul Max Trucking", "organization_core"),
    ("organization", "Mignard Company LLC", "MIGNARD COMPANY", "organization_core"),
    ("organization", "Katy & Co LLC", "Katy & Co", "organization_core"),
], ids=["last-first", "case", "reordered", "joint-folder", "legal-suffix", "hyphen",
        "company-suffix-kept", "co-suffix-kept"])
def test_deterministic_variants_are_accepted(scope_type, owner, segment, rule):
    assert plan_mod.corroborate(scope_type, owner, [segment]) == (rule, segment)


@pytest.mark.parametrize("scope_type,owner,segment", [
    ("person", "Benjamin Reynolds", "Reynolds, Ben"),
    ("person", "Matt Lesiv", "LESIV,MATTHEW"),
    ("person", "Juan Lacayo", "Lacayo, JP"),
    ("person", "Brandy Mc Croskey", "McCroskey, Brandy"),
    ("organization", "SANTRAM CORPORATION", "Santram Inc"),
    ("organization", "Murray & Sons Electrical", "Murray & Sons"),
    ("organization", "SOUTH EAST VAL6 INC", "VAL6, INC"),
    ("person", "Malik Shareef", "SHAREEF, REGINALD A & FAYE S"),
], ids=["nickname-ben", "nickname-matthew", "initials", "surname-spacing", "different-legal-form",
        "dropped-word", "partial-org", "different-person-same-surname"])
def test_everything_batch3_refused_is_still_refused(scope_type, owner, segment):
    """This batch is larger, not looser. The Batch 3 rejects must not become safe here."""
    assert plan_mod.corroborate(scope_type, owner, [segment]) is None


@pytest.mark.parametrize("owner,segment", [
    ("LILOLU PROPERTIES LLC", "Lilolu Properties Inc"),
    ("Acme Holdings Inc", "Acme Holdings LLC"),
    ("Acme Holdings LLC", "Acme Holdings Corporation"),
], ids=["llc-vs-inc", "inc-vs-llc", "llc-vs-corporation"])
def test_a_substituted_legal_suffix_is_refused(owner, segment):
    """An LLC and an Inc are not the same registered entity; nothing in Client360 says they are."""
    assert plan_mod.substituted_legal_suffix(owner, segment) is True
    assert plan_mod.corroborate("organization", owner, [segment]) is None


@pytest.mark.parametrize("owner,segment", [
    ("Calhoun Construction LLC", "Calhoun Construction"),
    ("Mignard Company LLC", "MIGNARD COMPANY"),
    ("Katy & Co LLC", "Katy & Co"),
], ids=["dropped-llc", "kept-company", "kept-co"])
def test_an_omitted_legal_suffix_is_not_a_substitution(owner, segment):
    """The path saying LESS than the stored name is a formatting difference, not a claim."""
    assert plan_mod.substituted_legal_suffix(owner, segment) is False


@pytest.mark.parametrize("owner,segment", [
    ("Cundiff", "CUNDIFF, GARY"),
    ("Smith", "SMITH, JOHN & MARY"),
], ids=["surname-only-owner", "surname-only-joint"])
def test_a_single_token_owner_name_can_never_be_corroborated(owner, segment):
    """Three different Cundiffs are filed by this batch. One token would have merged them."""
    assert plan_mod.corroborate("person", owner, [segment]) is None


def test_source_paths_that_disagree_are_refused_even_when_one_matches_exactly():
    """Preferring the agreeable path would be choosing the evidence that gives the wanted answer."""
    assert plan_mod.corroborate(
        "person", "GARY CUNDIFF", ["CUNDIFF, GARY", "Friar, Edward J & Tina"]) is None
    # ... while two spellings of the SAME name agree and are fine
    assert plan_mod.corroborate(
        "person", "GARY CUNDIFF", ["CUNDIFF, GARY", "Gary Cundiff"])[0] == "exact_or_reordered"


def test_the_organization_rule_never_applies_to_a_person():
    assert plan_mod.corroborate("person", "Calhoun Construction LLC",
                                ["Calhoun Construction"]) is None


def test_an_uncorroborated_row_cannot_enter_the_plan(tmp_path):
    """Carrying this lane's reason is not itself evidence of anything, in either direction.

    Batch 3 was repaired because the ABSENCE of ``no_client_confirmation_in_path`` was read as
    confirmation. Its PRESENCE is equally uninformative: it means the preview could not confirm,
    not that the path fails to name the client. Corroboration is re-derived either way.
    """
    path = write_candidate(tmp_path, [candidate_row(
        1, scope_name="Benjamin Reynolds", segments=["Benjamin Reynolds", "Tax Preparation"],
        client_segment="Reynolds, Ben")])
    with pytest.raises(plan_mod.PlanError, match="mechanically names its client"):
        build(path)


def test_a_provenance_category_cannot_enter_the_plan(tmp_path):
    """Checked against the taxonomy, never inferred from the absence of a reason code."""
    for category in ("Client Uploads", "Firm Deliverables"):
        path = write_candidate(tmp_path, [candidate_row(1, category=category)],
                               name=f"{category}.csv")
        with pytest.raises(plan_mod.PlanError, match="provenance-only"):
            build(path)


def test_a_row_carrying_any_conflict_cannot_enter_the_plan(tmp_path):
    """This lane's defining property: the preview found nothing in conflict."""
    path = write_candidate(tmp_path, [candidate_row(
        1, conflicts=["tax-year signals disagree: filename=2021, source_path=2022"])])
    with pytest.raises(plan_mod.PlanError, match="carries conflicts"):
        build(path)


def test_a_row_from_another_lane_cannot_enter_the_plan(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1, reasons=["conflicting_filing_evidence"])])
    with pytest.raises(plan_mod.PlanError, match="expected"):
        build(path)


# --- the year, both ways round ---------------------------------------------------------------

def test_a_strong_year_files_into_a_year_folder(tmp_path):
    row = candidate_row(1, tax_year="2023", tax_year_confidence="strong",
                        segments=["Ada Lovelace", "Tax Preparation", "2023"])
    plan = build(write_candidate(tmp_path, [row]))
    doc = plan["documents"][0]
    assert doc["depth"] == 3 and doc["tax_year"] == 2023
    assert doc["folder_code"].endswith("--year-2023")
    assert [f["kind"] for f in plan["folders"]] == ["client", "category", "year"]


@pytest.mark.parametrize("confidence", ["moderate", "none", "conflict"])
def test_an_uncertain_year_is_never_manufactured(confidence):
    """A year folder demands 'strong'. Nothing else will do."""
    assert confidence != plan_mod.STRONG_YEAR


@pytest.mark.parametrize("confidence", ["moderate", "none"])
def test_an_uncertain_year_cannot_reach_a_year_folder(tmp_path, confidence):
    row = candidate_row(1, tax_year="2023", tax_year_confidence=confidence,
                        segments=["Ada Lovelace", "Tax Preparation", "2023"])
    with pytest.raises(plan_mod.PlanError, match="a year folder requires"):
        build(write_candidate(tmp_path, [row], name=f"{confidence}.csv"))


def test_a_strong_year_cannot_be_silently_discarded_into_the_category(tmp_path):
    """The biconditional's other half: a proven year must not quietly lose its folder."""
    row = candidate_row(1, tax_year="2023", tax_year_confidence="strong",
                        segments=["Ada Lovelace", "Tax Preparation"])
    with pytest.raises(plan_mod.PlanError, match="a year folder requires"):
        build(write_candidate(tmp_path, [row]))


def test_a_moderate_year_files_at_the_category_and_records_no_year(tmp_path):
    row = candidate_row(1, tax_year="2023", tax_year_confidence="moderate")
    plan = build(write_candidate(tmp_path, [row]))
    doc = plan["documents"][0]
    assert doc["depth"] == 2 and doc["tax_year"] is None
    assert "--year-" not in doc["folder_code"]


# --- the post-write year biconditional ---------------------------------------------------------
# Batch 3 forbade year destinations outright and this script inherited the check verbatim, so a
# production apply wrote all 1,285 rows and then aborted on its own success. These prove the
# replacement admits exactly what the reviewed plan intends and nothing else.

def _doc(document_id, depth, folder_code):
    return {"document_id": document_id, "depth": depth, "folder_code": folder_code}


CATEGORY = "client-person-7--category-tax-preparation"
YEAR_2023 = f"{CATEGORY}--year-2023"
YEAR_2022 = f"{CATEGORY}--year-2022"


def test_a_planned_year_destination_is_accepted():
    """The case the inherited invariant wrongly rejected: 491 of the batch look like this."""
    documents = [_doc(1, 3, YEAR_2023), _doc(2, 2, CATEGORY)]
    placed = {1: YEAR_2023, 2: CATEGORY}
    assert ap.year_biconditional_violations(documents, placed) == []


def test_a_depth_two_document_in_a_year_folder_is_rejected():
    """No strongly proven year, so no year folder — the batch must not choose one by accident."""
    violations = ap.year_biconditional_violations([_doc(1, 2, YEAR_2023)], {1: YEAR_2023})
    assert len(violations) == 1
    assert "no strongly proven year" in violations[0]


def test_a_depth_three_document_in_a_category_folder_is_rejected():
    """A proven year must not be silently flattened into the shared category folder."""
    violations = ap.year_biconditional_violations([_doc(1, 3, CATEGORY)], {1: CATEGORY})
    assert len(violations) == 1
    assert "expected its planned year folder" in violations[0]


def test_the_wrong_year_folder_is_rejected():
    """A year folder is not enough; it must be THE planned one."""
    violations = ap.year_biconditional_violations([_doc(1, 3, YEAR_2023)], {1: YEAR_2022})
    assert len(violations) == 1
    assert "2023" in violations[0] and "2022" in violations[0]


def test_an_unplaced_document_is_rejected():
    violations = ap.year_biconditional_violations([_doc(1, 3, YEAR_2023)], {})
    assert len(violations) == 1


def test_the_blanket_batch3_prohibition_is_gone():
    """The exact defect: no code path may refuse a destination merely for being a year folder."""
    source = Path(ap.__file__).read_text(encoding="utf-8")
    assert "a batch 4 document was filed into a YEAR folder" not in source
    assert 'any("--year-" in code for code in placed.values())' not in source
    assert ap.YEAR_DESTINATION_DEPTH == 3


# --- the same property, end to end against the database ----------------------------------------

@pytest.fixture
def year_batch(tmp_path):
    """Two documents with a STRONGLY proven year, so both file at depth 3."""
    person = _person("Ida")
    name = f"Ida {_TAG}"
    d1, d2 = _document(person), _document(person)
    rows = [candidate_row(d, scope_id=person, scope_name=name, tax_year="2023",
                          tax_year_confidence="strong",
                          segments=[name, "Tax Preparation", "2023"])
            for d in (d1, d2)]
    path = write_candidate(tmp_path, rows)
    return {"person": person, "ids": sorted([d1, d2]), "candidate": path, "plan": build(path),
            "out": tmp_path / "out",
            "year_code": plan_mod.year_code("person", person, "Tax Preparation", 2023),
            "category_code": plan_mod.category_code("person", person, "Tax Preparation")}


def test_a_year_destination_applies_and_commits(year_batch):
    """The production apply that failed would now succeed for its 491 depth-3 rows."""
    plan = year_batch["plan"]
    assert plan["census"]["year_nodes"] == 1
    assert [f["kind"] for f in plan["folders"]] == ["client", "category", "year"]

    report = _apply(year_batch)
    assert report["committed"] is True
    assert report["assigned"] == 2 and report["folders_created"] == 3
    for document_id in year_batch["ids"]:
        assert _folder_of(document_id) == year_batch["year_code"]
    # the category node exists as the year node's parent, and holds no documents itself
    assert _folder_row(year_batch["category_code"]) is not None
    assert plan_mod.sha256_of(Path(year_batch["candidate"]))  # artifact untouched by the apply


def test_a_self_contradictory_plan_aborts_and_rolls_everything_back(year_batch, monkeypatch):
    """Equality alone cannot catch this: the row matches a plan that disagrees with itself.

    Relabelling a depth-3 document as depth 2 leaves its planned folder_code a YEAR code, so the
    exact-destination check passes and only the biconditional can refuse it. Every write must go.
    """
    original = plan_mod.build_plan

    def contradictory(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan["documents"][0]["depth"] = 2
        return plan

    monkeypatch.setattr(plan_mod, "build_plan", contradictory)
    with pytest.raises(RuntimeError, match="year biconditional"):
        _apply(year_batch)

    for document_id in year_batch["ids"]:
        assert _folder_of(document_id) is None
    assert _folder_row(year_batch["year_code"]) is None
    assert _folder_row(year_batch["category_code"]) is None
    assert _folder_row(plan_mod.client_code("person", year_batch["person"])) is None


def test_a_flattened_year_document_aborts_and_rolls_everything_back(year_batch, monkeypatch):
    """The mirror case: depth 3 kept, but the destination downgraded to the category node."""
    original = plan_mod.build_plan

    def flattened(*args, **kwargs):
        plan = original(*args, **kwargs)
        for document in plan["documents"]:
            document["folder_code"] = year_batch["category_code"]
        plan["folders"] = [f for f in plan["folders"] if f["kind"] != "year"]
        return plan

    monkeypatch.setattr(plan_mod, "build_plan", flattened)
    with pytest.raises(RuntimeError, match="year biconditional"):
        _apply(year_batch)

    for document_id in year_batch["ids"]:
        assert _folder_of(document_id) is None
    assert _folder_row(year_batch["category_code"]) is None


def test_batch3_still_refuses_every_year_destination():
    """Batch 4's repair must not have loosened Batch 3, which genuinely files no years."""
    from app.services import document_filing_batch3 as b3

    assert b3.DESTINATION_DEPTH == 2
    b3_apply = (Path(ap.__file__).parent / "apply_document_filing_batch3.py").read_text(
        encoding="utf-8")
    assert "a batch 3 document was filed into a YEAR folder" in b3_apply
