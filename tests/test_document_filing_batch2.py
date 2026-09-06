"""Document filing persistence BATCH 2 — the plan, the guarded apply, and the scoped rollback.

Batch 2 files 550 documents whose only remaining uncertainty is contradictory tax-year evidence. It
writes ``documents.folder_id`` and creates FOUR folder rows; the other 377 nodes it files into are
Batch 1's, already in production and holding thousands of other documents.

That reuse is what the tests are mostly about. The failure that would matter here is not a wrong
destination — it is touching a folder this batch did not create. A renamed Batch 1 folder, a
reparented one, or a rollback that deletes one would unfile Batch 1's documents through
``ON DELETE SET NULL``, quietly and with no record of where they had been.

Synthetic fixtures build a miniature of the real shape: some destination folders pre-created to stand
in for Batch 1's, some genuinely missing. One test validates the real frozen candidate artifact.

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
from app.services import document_filing_batch2 as plan_mod
from scripts import apply_document_filing_batch2 as ap
from scripts import rollback_document_filing_batch2 as rb

_TAG = f"FB2{uuid.uuid4().hex[:6]}"

FROZEN = (Path(__file__).resolve().parents[1] / "reports"
          / "document-filing-batch2-candidate-b2a5636ad2d4"
          / "document_filing_batch2_candidate.csv")

CANDIDATE_COLUMNS = list(plan_mod.CANDIDATE_COLUMNS)


# --- synthetic candidates -------------------------------------------------------

def candidate_row(document_id, *, scope_type="person", scope_id=1, scope_name="Ada Lovelace",
                  category="Tax Preparation", folder_code=None, folder_path=None,
                  tax_year_confidence="conflict"):
    code = folder_code if folder_code is not None else plan_mod.category_code(
        scope_type, scope_id, category)
    return {
        "document_id": str(document_id), "scope_type": scope_type, "scope_id": str(scope_id),
        "scope_name": scope_name, "category": category, "folder_code": code,
        "folder_path": folder_path if folder_path is not None else f"{scope_name}/{category}",
        "source": "SharePoint", "category_source": "sharepoint:tax",
        "tax_year_confidence": tax_year_confidence,
        "tax_year_evidence": '{"filename": 2021, "source_path": 2022}',
        "conflicts": '["tax-year signals disagree: filename=2021, source_path=2022"]',
        "inclusion_reason": "only conflict is the tax year",
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

def test_the_real_frozen_candidate_reproduces_the_reviewed_digests():
    if not FROZEN.is_file():
        pytest.skip("the frozen batch 2 candidate is not present in this worktree")
    assert plan_mod.sha256_of(FROZEN) == plan_mod.CANDIDATE_CSV_SHA256
    plan = plan_mod.build_plan(FROZEN)
    assert plan["census"]["documents"] == plan_mod.EXPECTED_DOCUMENTS == 550
    assert plan["census"]["client_nodes"] == 190
    assert plan["census"]["category_nodes"] == 191
    assert plan["census"]["year_nodes"] == 0
    assert plan["census"]["folder_nodes"] == 381
    assert plan["plan_digest"] == plan_mod.EXPECTED_PLAN_DIGEST
    assert plan["folder_manifest_digest"] == plan_mod.EXPECTED_FOLDER_MANIFEST_DIGEST
    assert plan_mod.confirm_phrase(550) == "APPLY-DOCUMENT-FILING-BATCH2-550"
    assert plan_mod.rollback_phrase(550) == "ROLLBACK-DOCUMENT-FILING-BATCH2-550"
    # the defining property of this batch
    assert all(d["tax_year"] is None and d["depth"] == 2 for d in plan["documents"])
    assert not any("--year-" in f["code"] for f in plan["folders"])


def test_wrong_candidate_sha_is_rejected(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1)])
    with pytest.raises(plan_mod.PlanError, match="SHA256"):
        plan_mod.build_plan(path, expect_sha="0" * 64, expect_documents=None,
                            enforce_census=False)


def test_wrong_document_count_is_rejected(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1)])
    with pytest.raises(plan_mod.PlanError, match="rows, approved"):
        build(path, expect_documents=550)


def test_wrong_plan_or_folder_digest_is_rejected(tmp_path, monkeypatch):
    path = write_candidate(tmp_path, [candidate_row(1)])
    monkeypatch.setattr(plan_mod, "EXPECTED_CLIENT_NODES", 1)
    monkeypatch.setattr(plan_mod, "EXPECTED_CATEGORY_NODES", 1)
    monkeypatch.setattr(plan_mod, "EXPECTED_FOLDER_NODES", 2)
    monkeypatch.setattr(plan_mod, "EXPECTED_CATEGORY_CENSUS", {"Tax Preparation": 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_SCOPE_CENSUS", {"person": 1})
    monkeypatch.setattr(plan_mod, "EXPECTED_PLAN_DIGEST", "0" * 64)
    with pytest.raises(plan_mod.PlanError, match="plan digest"):
        plan_mod.build_plan(path, expect_sha=plan_mod.sha256_of(path), expect_documents=1)
    monkeypatch.setattr(plan_mod, "EXPECTED_PLAN_DIGEST",
                        plan_mod.plan_digest([{k: d[k] for k in plan_mod.PLAN_FIELDS}
                                              for d in build(path)["documents"]]))
    monkeypatch.setattr(plan_mod, "EXPECTED_FOLDER_MANIFEST_DIGEST", "0" * 64)
    with pytest.raises(plan_mod.PlanError, match="folder manifest digest"):
        plan_mod.build_plan(path, expect_sha=plan_mod.sha256_of(path), expect_documents=1)


# --- structural refusals --------------------------------------------------------

def test_duplicate_document_ids_are_rejected(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(1), candidate_row(1)])
    with pytest.raises(plan_mod.PlanError, match="duplicate document_id"):
        build(path)


def test_a_year_destination_is_refused(tmp_path):
    """The batch declines to choose a year. A year folder in the artifact is a hard stop."""
    code = plan_mod.category_code("person", 1, "Tax Preparation") + "--year-2023"
    path = write_candidate(tmp_path, [candidate_row(1, folder_code=code)])
    with pytest.raises(plan_mod.PlanError, match="deterministic|YEAR destination"):
        build(path)


def test_a_hand_edited_destination_is_refused(tmp_path):
    """The destination is recomputed, never trusted — another client's folder cannot be smuggled in."""
    path = write_candidate(tmp_path, [candidate_row(
        1, folder_code=plan_mod.category_code("person", 999, "Tax Preparation"))])
    with pytest.raises(plan_mod.PlanError, match="!= the deterministic"):
        build(path)


def test_a_three_segment_path_is_refused(tmp_path):
    path = write_candidate(tmp_path, [candidate_row(
        1, folder_path="Ada Lovelace/Tax Preparation/2023")])
    with pytest.raises(plan_mod.PlanError, match="segments, must have 2"):
        build(path)


@pytest.mark.parametrize("field,value,match", [
    ("scope_name", "Somebody Else", "path client"),
    ("category", "Payroll", "!= the deterministic"),
    ("scope_type", "vendor", "scope type"),
], ids=["client-mismatch", "category-mismatch", "bad-scope-type"])
def test_field_mismatches_are_refused(tmp_path, field, value, match):
    row = candidate_row(1)
    row[field] = value
    path = write_candidate(tmp_path, [row])
    with pytest.raises(plan_mod.PlanError, match=match):
        build(path)


def test_a_row_without_a_year_conflict_is_refused(tmp_path):
    """This batch is defined by the year conflict. A 'strong' year row belongs elsewhere."""
    path = write_candidate(tmp_path, [candidate_row(1, tax_year_confidence="strong")])
    with pytest.raises(plan_mod.PlanError, match="tax_year_confidence"):
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


# --- database-backed ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean():
    yield
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"fb2:{_TAG}%")))]
        if ids:
            c.execute(documents.update().where(documents.c.id.in_(ids)).values(folder_id=None))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))
        # only folders this test module could have created
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
            original_name=filename, stored_name=f"fb2:{_TAG}{uuid.uuid4().hex}",
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
                              "from document_folders where code = :c"), {"c": code}).mappings().first()


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
    """Two clients: one whose folders ALREADY exist (Batch-1-like), one whose do not.

    Mirrors the real shape — most destinations are reused, a few must be created.
    """
    existing_person = _person("Ada")
    new_person = _person("Grace")
    existing_name, new_name = f"Ada {_TAG}", f"Grace {_TAG}"

    # stand-in for Batch 1's production tree
    client_code = plan_mod.client_code("person", existing_person)
    cat_code = plan_mod.category_code("person", existing_person, "Tax Preparation")
    client_id = _make_folder(client_code, existing_name)
    cat_id = _make_folder(cat_code, "Tax Preparation", client_id)
    # a Batch-1 document already living in that folder — it must survive everything
    bystander = _document(existing_person, folder_id=cat_id)

    d1 = _document(existing_person)
    d2 = _document(existing_person)
    d3 = _document(new_person)
    rows = [candidate_row(d1, scope_id=existing_person, scope_name=existing_name),
            candidate_row(d2, scope_id=existing_person, scope_name=existing_name),
            candidate_row(d3, scope_id=new_person, scope_name=new_name)]
    path = write_candidate(tmp_path, rows)
    plan = build(path)
    return {"preexisting_client": client_code, "preexisting_category": cat_code,
            "preexisting_ids": (client_id, cat_id), "bystander": bystander,
            "existing_person": existing_person, "new_person": new_person,
            "ids": sorted([d1, d2, d3]), "candidate": path, "plan": plan,
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
    assert report["folders_reused"] == 2, "the pre-existing client+category must be recognised"
    assert sorted(report["created_codes"]) == sorted([
        plan_mod.client_code("person", batch["new_person"]),
        plan_mod.category_code("person", batch["new_person"], "Tax Preparation")])
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_apply_refuses_without_flag_wrong_phrase_or_wrong_actor(batch):
    assert _run(batch, confirm=plan_mod.confirm_phrase(3), actor_user_id=1)["assigned"] == 0
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, confirm="APPLY-WRONG", actor_user_id=1)
    with pytest.raises(SystemExit, match="--actor-user-id 1"):
        _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(3), actor_user_id=2)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_apply_creates_only_missing_folders_and_reuses_the_rest(batch):
    before_existing = dict(_folder_row(batch["preexisting_category"]))
    report = _apply(batch)
    assert report["committed"] is True
    assert report["folders_created"] == 2, "only the genuinely missing nodes"
    assert report["folders_reused"] == 2
    assert report["assigned"] == 3 and report["audit_rows"] == 3

    destinations = {d["document_id"]: d["folder_code"] for d in batch["plan"]["documents"]}
    for document_id in batch["ids"]:
        assert _folder_of(document_id) == destinations[document_id]
    # the reused folder was NOT recreated, renamed, reparented or reclassified
    assert dict(_folder_row(batch["preexisting_category"])) == before_existing
    # and the Batch-1 document living in it never moved
    assert _folder_of(batch["bystander"]) == batch["preexisting_category"]
    # no year folder anywhere
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


def test_audits_match_assignments_exactly(batch):
    report = _apply(batch)
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        events = c.execute(select(audit.c.entity_id, audit.c.action, audit.c.metadata)
                           .where(audit.c.request_id == report["request_id"])).mappings().all()
    assert len(events) == 3
    assert {e["action"] for e in events} == {ap.AUDIT_ACTION}
    assert {int(e["entity_id"]) for e in events} == set(batch["ids"])
    for event in events:
        assert event["metadata"]["batch"] == "DOCUMENT-FILING-BATCH2"
        # No ``tax_year`` key: the audit layer redacts that name, so a NULL year would be stored
        # as "[REDACTED]" and read as a year that was recorded and withheld.
        assert "tax_year" not in event["metadata"]
        assert event["metadata"]["destination_depth"] == 2
        assert "--year-" not in event["metadata"]["folder_code"]


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


def test_a_reparented_existing_folder_aborts(batch):
    other = _make_folder(f"client-person-{_TAG}-other", "other")
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        c.execute(folders.update().where(folders.c.code == batch["preexisting_category"])
                  .values(parent_folder_id=other))
    with pytest.raises(SystemExit, match="partial or incompatible"):
        _apply(batch)


def test_a_missing_expected_parent_is_created_not_assumed(batch):
    """Deleting the pre-existing client makes it a node to CREATE, which changes the count."""
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["bystander"])
                  .values(folder_id=None))
        c.execute(delete(folders).where(folders.c.code == batch["preexisting_category"]))
        c.execute(delete(folders).where(folders.c.code == batch["preexisting_client"]))
    report = _apply(batch)
    assert report["folders_created"] == 4 and report["folders_reused"] == 0
    assert _folder_row(batch["preexisting_client"])["parent_folder_id"] is None


@pytest.mark.parametrize("mutation", [{"archived": True}, {"status": "deleted"}],
                         ids=["archived", "deleted"])
def test_an_inactive_target_aborts(batch, mutation):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0]).values(**mutation))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_ownership_drift_aborts(batch):
    other = _person("Interloper")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)


def test_a_target_already_filed_aborts_as_partial(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=batch["preexisting_ids"][1]))
    with pytest.raises(SystemExit, match="partial or incompatible"):
        _apply(batch)


def test_an_exact_rerun_is_a_no_op(batch):
    first = _apply(batch)
    assert first["committed"] is True
    second = _run(batch, apply_changes=True,
                  confirm=plan_mod.confirm_phrase(len(batch["ids"])), actor_user_id=1)
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
    assert snapshots["n"] == 1, "the snapshot is taken before any write"
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    assert _folder_row(plan_mod.client_code("person", batch["new_person"])) is None, \
        "no folder created by the failed batch may survive"
    # and the pre-existing tree is still there
    assert _folder_row(batch["preexisting_category"]) is not None


def test_snapshot_records_previous_folder_and_which_nodes_were_created(batch):
    report = _apply(batch)
    snapshot = Path(report["snapshot"])
    rows = list(csv.DictReader(snapshot.open(encoding="utf-8")))
    assert len(rows) == 3
    assert all(r["previous_folder_id"] == "" for r in rows)
    created = {r["target_folder_code"] for r in rows if r["created_folder"] == "1"}
    reused = {r["target_folder_code"] for r in rows if r["created_folder"] == "0"}
    assert reused == {batch["preexisting_category"]}
    assert created == {plan_mod.category_code("person", batch["new_person"], "Tax Preparation")}
    receipt = json.loads((snapshot.parent / "apply_receipt.json").read_text(encoding="utf-8"))
    assert receipt["committed"] is True and receipt["folders_created"] == 2


# --- rollback -------------------------------------------------------------------

def _receipt(report):
    return Path(report["snapshot"]).parent / "apply_receipt.json"


def _rb(batch, report, **kw):
    """Rollback against the fixture's own candidate rather than the production pin."""
    kw.setdefault("out", lambda *_a, **_k: None)
    kw.setdefault("expect_candidate_sha", plan_mod.sha256_of(Path(batch["candidate"])))
    return rb.run(_receipt(report), **kw)


def test_rollback_dry_run_changes_nothing(batch):
    report = _apply(batch)
    result = _rb(batch, report)
    assert result["committed"] is False and result["restored"] == 0
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is not None


def test_rollback_restores_and_deletes_only_batch2_folders(batch):
    report = _apply(batch)
    result = _rb(batch, report, apply_changes=True,
                     confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1)
    assert result["committed"] is True
    assert result["restored"] == 3
    assert result["folders_deleted"] == 2, "only the two this batch created"
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    # Batch-1's folders and its document are untouched
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
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is not None


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
