"""Document filing persistence BATCH 1 — the plan, the guarded apply, and the rollback.

This batch writes ONE column, ``documents.folder_id``, and creates the folder tree it points at.
Almost every test below pins a REFUSAL, because the ways this could go wrong are all quiet ones: a
preview edited after review, a document that gained an owner, a folder tree half-built, a rollback
that deletes a parent and silently orphans its children through ``ON DELETE SET NULL``.

The plan layer is pure, so most of it is tested against small synthetic previews built here rather
than the 73,240-row production artifact. One test validates the real frozen artifact when it is
present on the machine.

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
from app.services import document_filing_apply as plan_mod
from scripts import apply_document_filing as ap
from scripts import rollback_document_filing as rb

_TAG = f"FILE{uuid.uuid4().hex[:6]}"

FROZEN = Path(r"C:\Client360\reports\document-filing-preview-20260906-154138"
              r"\document_filing_preview.csv")

PREVIEW_COLUMNS = ["document_id", "proposed_scope_type", "proposed_scope_id", "proposed_scope_name",
                   "filing_scope_state", "proposed_folder_segments", "proposed_folder_path",
                   "proposed_top_level_category", "proposed_tax_year", "filing_status", "conflicts"]


# --- synthetic previews ---------------------------------------------------------

def preview_row(document_id, *, scope_type="person", scope_id=1, scope_name="Ada Lovelace",
                category="Tax Preparation", year=2023, status="AUTO_FILE_SAFE",
                scope_state="resolved", conflicts="[]", segments=None, path=None):
    segs = segments if segments is not None else (
        [scope_name, category] + ([str(year)] if year else []))
    return {
        "document_id": str(document_id), "proposed_scope_type": scope_type,
        "proposed_scope_id": str(scope_id), "proposed_scope_name": scope_name,
        "filing_scope_state": scope_state,
        "proposed_folder_segments": json.dumps(segs),
        "proposed_folder_path": path if path is not None else "/".join(segs),
        "proposed_top_level_category": category,
        "proposed_tax_year": str(year) if year else "", "filing_status": status,
        "conflicts": conflicts,
    }


def write_preview(tmp_path, rows, *, name="preview.csv", newline="\n") -> Path:
    """A synthetic preview file. ``newline`` lets a test produce the CRLF representation."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PREVIEW_COLUMNS, lineterminator=newline)
    writer.writeheader()
    writer.writerows(rows)
    path = tmp_path / name
    path.write_bytes(buffer.getvalue().encode("utf-8"))
    return path


def build(path, **kw):
    kw.setdefault("expect_sha", plan_mod.sha256_of(Path(path)))
    kw.setdefault("expect_rows", None)
    kw.setdefault("expect_auto_rows", None)
    kw.setdefault("enforce_census", False)
    return plan_mod.build_plan(path, **kw)


# --- slug and codes -------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("Tax Preparation", "tax-preparation"),
    ("Sales & Litter Tax", "sales-litter-tax"),
    ("  Wealth   Accounts  ", "wealth-accounts"),
    ("O'Gorman, Amedee", "o-gorman-amedee"),
    ("---", "unnamed"),
    ("", "unnamed"),
    (None, "unnamed"),
])
def test_slug_rule_is_the_audited_one(value, expected):
    assert plan_mod.slugify(value) == expected


def test_deterministic_codes_match_the_audited_examples():
    assert plan_mod.client_code("household", 1) == "client-household-1"
    assert plan_mod.category_code("household", 1, "Tax Preparation") == \
        "client-household-1--category-tax-preparation"
    assert plan_mod.year_code("household", 1, "Tax Preparation", 2023) == \
        "client-household-1--category-tax-preparation--year-2023"


# --- the frozen artifact --------------------------------------------------------

def test_wrong_frozen_sha_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(1)])
    with pytest.raises(plan_mod.PlanError, match="SHA256"):
        plan_mod.build_plan(path, expect_sha="0" * 64, expect_rows=None, expect_auto_rows=None,
                            enforce_census=False)


def test_correct_frozen_sha_is_accepted(tmp_path):
    path = write_preview(tmp_path, [preview_row(1)])
    assert build(path)["census"]["auto_file_safe"] == 1


def test_auto_row_count_mismatch_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(1), preview_row(2)])
    with pytest.raises(plan_mod.PlanError, match="AUTO_FILE_SAFE rows"):
        build(path, expect_auto_rows=99)


def test_preview_row_count_mismatch_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(1)])
    with pytest.raises(plan_mod.PlanError, match="rows, approved"):
        build(path, expect_rows=99)


def test_only_auto_file_safe_rows_are_planned(tmp_path):
    path = write_preview(tmp_path, [preview_row(1),
                                    preview_row(2, status="REVIEW_REQUIRED"),
                                    preview_row(3, status="UNRESOLVED")])
    plan = build(path)
    assert [d["document_id"] for d in plan["documents"]] == [1]


# --- structural refusals --------------------------------------------------------

def test_duplicate_document_ids_are_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(1), preview_row(1)])
    with pytest.raises(plan_mod.PlanError, match="duplicate document_id"):
        build(path)


def test_unresolved_scope_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(1, scope_state="conflict")])
    with pytest.raises(plan_mod.PlanError, match="filing_scope_state"):
        build(path)


def test_scope_state_match_is_case_insensitive(tmp_path):
    path = write_preview(tmp_path, [preview_row(1, scope_state="RESOLVED")])
    assert build(path)["census"]["auto_file_safe"] == 1


def test_conflicts_are_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(1, conflicts='["ownership drifted"]')])
    with pytest.raises(plan_mod.PlanError, match="carries conflicts"):
        build(path)


def test_malformed_segments_are_rejected(tmp_path):
    rows = [preview_row(1)]
    rows[0]["proposed_folder_segments"] = "not json"
    path = write_preview(tmp_path, rows)
    with pytest.raises(plan_mod.PlanError, match="unreadable proposed_folder_segments"):
        build(path)


@pytest.mark.parametrize("segments", [["Ada Lovelace"], ["a", "b", "2023", "extra"]],
                         ids=["depth-1", "depth-4"])
def test_bad_depth_is_rejected(tmp_path, segments):
    path = write_preview(tmp_path, [preview_row(1, segments=segments)])
    with pytest.raises(plan_mod.PlanError, match="folder depth"):
        build(path)


def test_client_segment_mismatch_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(
        1, segments=["Somebody Else", "Tax Preparation", "2023"])])
    with pytest.raises(plan_mod.PlanError, match="!= scope name"):
        build(path)


def test_category_segment_mismatch_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(
        1, segments=["Ada Lovelace", "Payroll", "2023"])])
    with pytest.raises(plan_mod.PlanError, match="!= category"):
        build(path)


def test_non_year_third_segment_is_rejected(tmp_path):
    path = write_preview(tmp_path, [preview_row(
        1, segments=["Ada Lovelace", "Tax Preparation", "Q1"])])
    with pytest.raises(plan_mod.PlanError, match="not a four-digit year"):
        build(path)


def test_year_segment_must_equal_proposed_tax_year(tmp_path):
    rows = [preview_row(1, year=2023)]
    rows[0]["proposed_tax_year"] = "2022"
    path = write_preview(tmp_path, rows)
    with pytest.raises(plan_mod.PlanError, match="!= proposed_tax_year"):
        build(path)


def test_path_must_match_segments(tmp_path):
    path = write_preview(tmp_path, [preview_row(1, path="Ada Lovelace/Wrong/2023")])
    with pytest.raises(plan_mod.PlanError, match="does not match its segments"):
        build(path)


def test_blank_scope_name_and_category_are_rejected(tmp_path):
    blank_name = write_preview(tmp_path, [preview_row(1, scope_name="")], name="a.csv")
    with pytest.raises(plan_mod.PlanError):
        build(blank_name)
    rows = [preview_row(2)]
    rows[0]["proposed_top_level_category"] = ""
    blank_category = write_preview(tmp_path, rows, name="b.csv")
    with pytest.raises(plan_mod.PlanError):
        build(blank_category)


# --- folder tree ----------------------------------------------------------------

def test_folder_tree_shape_and_destinations(tmp_path):
    path = write_preview(tmp_path, [
        preview_row(1, scope_id=1, scope_name="Ada Lovelace", year=2023),
        preview_row(2, scope_id=1, scope_name="Ada Lovelace", year=2024),
        preview_row(3, scope_id=1, scope_name="Ada Lovelace", year=None),
        preview_row(4, scope_id=2, scope_name="Grace Hopper", category="Payroll", year=None),
    ])
    plan = build(path)
    assert plan["census"] == {
        "preview_rows": 4, "auto_file_safe": 4, "client_nodes": 2, "category_nodes": 2,
        "year_nodes": 2, "folder_nodes": 6,
        "by_category": {"Payroll": 1, "Tax Preparation": 3}, "by_depth": {2: 2, 3: 2}}
    kinds = [f["kind"] for f in plan["folders"]]
    assert kinds == sorted(kinds, key=plan_mod.FOLDER_KINDS.index), "parents must precede children"
    destinations = {d["document_id"]: d["folder_code"] for d in plan["documents"]}
    assert destinations[1] == "client-person-1--category-tax-preparation--year-2023"
    assert destinations[3] == "client-person-1--category-tax-preparation"
    assert not any(f["kind"] == "client" for f in plan["folders"]
                   if f["code"] in destinations.values()), "never file at the client root"


def test_every_folder_code_is_unique(tmp_path):
    path = write_preview(tmp_path, [preview_row(i, scope_id=i % 3, scope_name=f"Client {i % 3}")
                                    for i in range(1, 20)])
    codes = [f["code"] for f in build(path)["folders"]]
    assert len(codes) == len(set(codes))


def test_a_code_claimed_by_two_names_is_a_collision(tmp_path):
    """Same scope id, different display names — the unique index would catch it mid-transaction."""
    path = write_preview(tmp_path, [preview_row(1, scope_id=7, scope_name="Ada Lovelace"),
                                    preview_row(2, scope_id=7, scope_name="Someone Else")])
    with pytest.raises(plan_mod.PlanError, match="claimed by two different names"):
        build(path)


# --- digests --------------------------------------------------------------------

def test_plan_digest_is_stable_and_content_addressed(tmp_path):
    rows = [preview_row(2), preview_row(1)]
    first = build(write_preview(tmp_path, rows, name="one.csv"))
    second = build(write_preview(tmp_path, list(reversed(rows)), name="two.csv"))
    assert first["plan_digest"] == second["plan_digest"]
    assert first["folder_manifest_digest"] == second["folder_manifest_digest"]


def test_digest_is_identical_across_lf_and_crlf_representations(tmp_path):
    rows = [preview_row(1), preview_row(2, year=None)]
    lf = write_preview(tmp_path, rows, name="lf.csv", newline="\n")
    crlf = write_preview(tmp_path, rows, name="crlf.csv", newline="\r\n")
    assert lf.read_bytes() != crlf.read_bytes(), "the two files must differ in bytes"
    assert build(lf)["plan_digest"] == build(crlf)["plan_digest"]
    assert build(lf)["folder_manifest_digest"] == build(crlf)["folder_manifest_digest"]


def test_confirmation_phrases_are_deterministic():
    assert plan_mod.confirm_phrase(16304) == "APPLY-DOCUMENT-FILING-BATCH1-16304"
    assert plan_mod.rollback_phrase(16304) == "ROLLBACK-DOCUMENT-FILING-BATCH1-16304"


# --- the real frozen artifact ---------------------------------------------------

def test_the_real_frozen_preview_reproduces_the_approved_census():
    if not FROZEN.is_file():
        pytest.skip("the frozen production preview is not present on this machine")
    plan = plan_mod.build_plan(FROZEN)
    assert plan["frozen_csv_sha256"] == plan_mod.FROZEN_CSV_SHA256
    assert plan["census"]["preview_rows"] == plan_mod.EXPECTED_PREVIEW_ROWS == 73240
    assert plan["census"]["auto_file_safe"] == plan_mod.EXPECTED_AUTO_ROWS == 16304
    assert plan["census"]["client_nodes"] == 845
    assert plan["census"]["category_nodes"] == 913
    assert plan["census"]["year_nodes"] == 1376
    assert plan["census"]["folder_nodes"] == 3134
    assert plan["census"]["by_category"] == plan_mod.EXPECTED_CATEGORY_CENSUS
    assert plan["census"]["by_depth"] == plan_mod.EXPECTED_DEPTH_CENSUS
    assert len({f["code"] for f in plan["folders"]}) == 3134
    assert ap.confirm_phrase_for(plan) == "APPLY-DOCUMENT-FILING-BATCH1-16304"


# --- database-backed ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean():
    yield
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"file:{_TAG}%")))]
        if ids:
            c.execute(documents.update().where(documents.c.id.in_(ids)).values(folder_id=None))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(folders).where(folders.c.code.like(f"client-%{_TAG}%")))
        c.execute(delete(folders).where(folders.c.code.like("client-person-%")))
        c.execute(delete(people).where(people.c.last_name == _TAG))


def _person(first) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=_TAG, full_name=f"{first} {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()


def _document(person_id, *, name=None, archived=False, status="active", folder_id=None) -> int:
    filename = name or f"{_TAG}-{uuid.uuid4().hex[:6]}.pdf"
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=filename, stored_name=f"file:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status="not_required", current_version=1, person_id=person_id,
            folder_id=folder_id, tags={}).returning(documents.c.id)).scalar_one()


@pytest.fixture
def batch(tmp_path):
    """Three live documents for one person: two years plus one category-level row."""
    person_id = _person("Ada")
    name = f"Ada {_TAG}"
    d1 = _document(person_id)
    d2 = _document(person_id)
    d3 = _document(person_id)
    rows = [preview_row(d1, scope_id=person_id, scope_name=name, year=2023),
            preview_row(d2, scope_id=person_id, scope_name=name, year=2024),
            preview_row(d3, scope_id=person_id, scope_name=name, year=None)]
    path = write_preview(tmp_path, rows)
    plan = build(path)
    return {"person": person_id, "name": name, "ids": sorted([d1, d2, d3]), "preview": path,
            "plan": plan, "sha": plan_mod.sha256_of(path), "out": tmp_path / "out"}


def _run(batch, **kw):
    kw.setdefault("output_root", batch["out"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["preview"], _plan_overrides=_overrides(batch), **kw)


def _overrides(batch):
    return {"expect_sha": batch["sha"], "expect_rows": None, "expect_auto_rows": None,
            "enforce_census": False}


def _apply(batch, **kw):
    return _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(len(batch["ids"])),
                actor_user_id=1, **kw)


def _folder_of(document_id):
    with engine.connect() as c:
        return c.execute(text(
            "select f.code from documents d left join document_folders f on f.id = d.folder_id "
            "where d.id = :i"), {"i": document_id}).scalar()


def test_dry_run_writes_nothing(batch):
    report = _run(batch)
    assert report["committed"] is False
    assert report["state"] == "PRISTINE"
    assert report["folders_created"] == 0 and report["assigned"] == 0
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    with engine.connect() as c:
        assert c.execute(text("select count(*) from document_folders where code like :p"),
                         {"p": f"client-person-{batch['person']}%"}).scalar() == 0


def test_apply_refuses_without_the_apply_flag(batch):
    report = _run(batch, confirm=plan_mod.confirm_phrase(3), actor_user_id=1)
    assert report["committed"] is False and report["assigned"] == 0


def test_apply_refuses_a_wrong_confirmation_or_actor(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, confirm="APPLY-SOMETHING-ELSE", actor_user_id=1)
    with pytest.raises(SystemExit, match="--actor-user-id 1"):
        _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(3), actor_user_id=7)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_pristine_apply_creates_the_tree_and_files_every_document(batch):
    before = {i: dict(_row(i)) for i in batch["ids"]}
    report = _apply(batch)
    assert report["committed"] is True
    assert report["folders_created"] == 4          # 1 client + 1 category + 2 years
    assert report["assigned"] == 3
    assert report["audit_rows"] == 3
    destinations = {d["document_id"]: d["folder_code"] for d in batch["plan"]["documents"]}
    for document_id in batch["ids"]:
        assert _folder_of(document_id) == destinations[document_id]
    with engine.connect() as c:
        rows = c.execute(text(
            "select code, name, parent_folder_id, classification, created_by "
            "from document_folders where code like :p order by code"),
            {"p": f"client-person-{batch['person']}%"}).mappings().all()
    assert len(rows) == 4
    assert all(r["classification"] is None for r in rows), "folder classification must stay NULL"
    assert all(r["created_by"] == 1 for r in rows)
    roots = [r for r in rows if r["parent_folder_id"] is None]
    assert len(roots) == 1 and roots[0]["code"] == f"client-person-{batch['person']}"

    # protected metadata untouched
    for document_id in batch["ids"]:
        after, prior = dict(_row(document_id)), before[document_id]
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"folder_id", "updated_at", "updated_by_user_id"}, changed


def _row(document_id):
    with engine.connect() as c:
        return c.execute(select(documents).where(documents.c.id == document_id)).mappings().one()


def test_audit_rows_match_assignments_exactly(batch):
    report = _apply(batch)
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        events = c.execute(select(audit.c.entity_id, audit.c.action, audit.c.metadata)
                           .where(audit.c.request_id == report["request_id"])).mappings().all()
    assert len(events) == len(batch["ids"])
    assert {e["action"] for e in events} == {ap.AUDIT_ACTION}
    assert {int(e["entity_id"]) for e in events} == set(batch["ids"])
    for event in events:
        assert event["metadata"]["batch"] == "DOCUMENT-FILING-BATCH1"
        assert event["metadata"]["previous_folder_id"] is None
        assert event["metadata"]["frozen_csv_sha256"] == plan_mod.FROZEN_CSV_SHA256
        assert event["metadata"]["folder_code"]


def test_an_archived_or_deleted_document_aborts(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(archived=True))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(archived=False, status="deleted"))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)


def test_a_missing_document_aborts(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=None))
        c.execute(delete(documents).where(documents.c.id == batch["ids"][0]))
    with pytest.raises(SystemExit, match="locked set is not the reviewed set"):
        _apply(batch)


def test_ownership_drift_aborts(batch):
    other = _person("Grace")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="no longer validate"):
        _apply(batch)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_a_document_that_is_already_filed_aborts(batch):
    folders = metadata.tables["document_folders"]
    with engine.begin() as c:
        stray = c.execute(folders.insert().values(code=f"client-person-{_TAG}-stray",
                                                  name="stray").returning(folders.c.id)).scalar_one()
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=stray))
    with pytest.raises(SystemExit, match="partial or conflicting|no longer validate"):
        _apply(batch)


def test_a_colliding_pre_existing_folder_aborts(batch):
    folders = metadata.tables["document_folders"]
    code = batch["plan"]["folders"][0]["code"]
    with engine.begin() as c:
        c.execute(folders.insert().values(code=code, name="somebody else's folder"))
    with pytest.raises(SystemExit, match="partial or conflicting"):
        _apply(batch)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None


def test_an_exact_rerun_is_a_no_op(batch):
    first = _apply(batch)
    assert first["committed"] is True
    second = _run(batch, apply_changes=True,
                  confirm=plan_mod.confirm_phrase(len(batch["ids"])), actor_user_id=1)
    assert second["state"] == "ALREADY_APPLIED"
    assert second["committed"] is False
    assert second["folders_created"] == 0 and second["assigned"] == 0
    with engine.connect() as c:
        assert c.execute(text("select count(*) from document_folders where code like :p"),
                         {"p": f"client-person-{batch['person']}%"}).scalar() == 4


def test_a_partial_state_aborts_rather_than_repairing(batch):
    _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(folder_id=None))
    with pytest.raises(SystemExit, match="partial or conflicting"):
        _run(batch, apply_changes=True, confirm=plan_mod.confirm_phrase(len(batch["ids"])),
             actor_user_id=1)


def test_a_late_invariant_failure_rolls_everything_back(batch, monkeypatch):
    """Folders and assignments share one transaction: a failed post-write check undoes both."""
    snapshots = {"n": 0}
    original = ap.write_snapshot

    def counting(plan, locked, out_dir):
        snapshots["n"] += 1
        return original(plan, locked, out_dir)

    monkeypatch.setattr(ap, "write_snapshot", counting)
    # Break the audit-count invariant: the assignments happen, the audit rows do not.
    monkeypatch.setattr("app.security.audit.write_audit_event",
                        lambda **kwargs: None)
    with pytest.raises(RuntimeError, match="audit rows"):
        _apply(batch)
    assert snapshots["n"] == 1, "the snapshot is taken before any write"
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None, "no assignment may survive"
    with engine.connect() as c:
        assert c.execute(text("select count(*) from document_folders where code like :p"),
                         {"p": f"client-person-{batch['person']}%"}).scalar() == 0, \
            "no folder may survive"


def test_snapshot_and_receipt_are_written(batch):
    report = _apply(batch)
    snapshot = Path(report["snapshot"])
    assert snapshot.is_file()
    rows = list(csv.DictReader(snapshot.open(encoding="utf-8")))
    assert len(rows) == len(batch["ids"])
    assert all(r["previous_folder_id"] == "" for r in rows)
    receipt = json.loads((snapshot.parent / "apply_receipt.json").read_text(encoding="utf-8"))
    assert receipt["committed"] is True
    assert receipt["document_assignments"] == len(batch["ids"])
    assert receipt["snapshot_sha256"] == plan_mod.sha256_of(snapshot)
    assert (snapshot.parent / "folder_manifest.json").is_file()
    assert (snapshot.parent / "plan_manifest.json").is_file()


# --- rollback -------------------------------------------------------------------

def _receipt_path(report):
    return Path(report["snapshot"]).parent / "apply_receipt.json"


def test_rollback_dry_run_changes_nothing(batch):
    report = _apply(batch)
    result = rb.run(_receipt_path(report), out=lambda *_a, **_k: None)
    assert result["committed"] is False and result["restored"] == 0
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is not None


def test_rollback_requires_phrase_and_actor(batch):
    report = _apply(batch)
    with pytest.raises(SystemExit, match="--confirm"):
        rb.run(_receipt_path(report), apply_changes=True, confirm="nope", actor_user_id=1,
               out=lambda *_a, **_k: None)
    with pytest.raises(SystemExit, match="--actor-user-id 1"):
        rb.run(_receipt_path(report), apply_changes=True,
               confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=9,
               out=lambda *_a, **_k: None)


def test_rollback_verifies_the_snapshot_sha(batch):
    report = _apply(batch)
    snapshot = Path(report["snapshot"])
    snapshot.write_bytes(snapshot.read_bytes() + b"\n")
    with pytest.raises(SystemExit, match="snapshot has been modified"):
        rb.run(_receipt_path(report), out=lambda *_a, **_k: None)


def test_rollback_restores_folder_id_and_deletes_children_first(batch):
    report = _apply(batch)
    result = rb.run(_receipt_path(report), apply_changes=True,
                    confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1,
                    out=lambda *_a, **_k: None)
    assert result["committed"] is True
    assert result["restored"] == len(batch["ids"])
    assert result["folders_deleted"] == 4
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is None
    with engine.connect() as c:
        assert c.execute(text("select count(*) from document_folders where code like :p"),
                         {"p": f"client-person-{batch['person']}%"}).scalar() == 0


def test_rollback_refuses_when_an_outside_document_is_filed_in_a_batch_folder(batch):
    report = _apply(batch)
    outsider = _document(batch["person"])
    target = _folder_of(batch["ids"][0])
    with engine.begin() as c:
        folder_id = c.execute(text("select id from document_folders where code = :c"),
                              {"c": target}).scalar()
        c.execute(documents.update().where(documents.c.id == outsider)
                  .values(folder_id=folder_id))
    with pytest.raises(SystemExit, match="outside this batch"):
        rb.run(_receipt_path(report), apply_changes=True,
               confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1,
               out=lambda *_a, **_k: None)
    for document_id in batch["ids"]:
        assert _folder_of(document_id) is not None


def test_rollback_refuses_an_unexpected_child_folder(batch):
    report = _apply(batch)
    folders = metadata.tables["document_folders"]
    with engine.connect() as c:
        parent = c.execute(text("select id from document_folders where code = :c"),
                           {"c": f"client-person-{batch['person']}"}).scalar()
    with engine.begin() as c:
        c.execute(folders.insert().values(code=f"client-person-{_TAG}-intruder",
                                          name="intruder", parent_folder_id=parent))
    with pytest.raises(SystemExit, match="beneath its folders"):
        rb.run(_receipt_path(report), apply_changes=True,
               confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1,
               out=lambda *_a, **_k: None)


def test_rollback_changes_no_protected_metadata(batch):
    before = {i: dict(_row(i)) for i in batch["ids"]}
    report = _apply(batch)
    rb.run(_receipt_path(report), apply_changes=True,
           confirm=plan_mod.rollback_phrase(len(batch["ids"])), actor_user_id=1,
           out=lambda *_a, **_k: None)
    for document_id in batch["ids"]:
        after, prior = dict(_row(document_id)), before[document_id]
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"updated_at", "updated_by_user_id"}, changed
