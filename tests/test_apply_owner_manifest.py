"""Fail-closed guarantees for the GENERIC owner-manifest apply and rollback.

The properties that make a bulk ownership write safe to run once against production:

* every expectation -- digest, row count, per-type census -- is SUPPLIED, never derived. Deriving
  them from the file and then checking the file against them proves nothing;
* the confirmation phrase is unique per batch, so it cannot be reused from a previous run;
* --apply names a real operator; a NULL actor is refused for a human-decision action;
* ONE bad row aborts the WHOLE manifest -- a failed row is never demoted to a skip;
* --dry-run performs the entire transaction and rolls it back, so a dry run proves the apply would
  succeed without persisting anything;
* rollback refuses to overwrite a decision made after the apply.

The write semantics deliberately mirror ``households.resolve_document_ownership`` -- the same atomic
``WHERE all-NULL AND NOT permanent-reject`` guard and the same audit action -- because that function
opens its own per-document transaction and so cannot provide all-or-nothing across a manifest.
"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid

import pytest

from scripts import apply_owner_manifest as ap
from scripts import rollback_owner_manifest as rb

BATCH = "unit-batch"


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _manifest(tmp_path, rows, *, applied="NO", cols="entity"):
    """`cols` exercises both column vocabularies real manifests have used."""
    t, i, n = (("proposed_entity_type", "proposed_entity_id", "proposed_entity_name")
               if cols == "entity" else ("owner_type", "owner_id", "owner_name"))
    p = tmp_path / f"m-{uuid.uuid4().hex[:6]}.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["document_id", t, i, n, "applied"])
        w.writeheader()
        for r in rows:
            w.writerow({"document_id": r[0], t: r[1], i: r[2], n: r[3], "applied": applied})
    return p


def _rows(person=2, household=1):
    out = [(9000 + k, "person", 1, f"P{k}") for k in range(person)]
    out += [(9500 + k, "household", 1, f"H{k}") for k in range(household)]
    return out


def _census(person=2, household=1, organization=0):
    return {"person": person, "household": household, "organization": organization}


def _run(p, **kw):
    kw.setdefault("batch_id", BATCH)
    kw.setdefault("expect_sha", _sha(p))
    kw.setdefault("expect_rows", 3)
    kw.setdefault("expect_census", _census())
    kw.setdefault("out", lambda *_a: None)
    return ap.run(p, **kw)


# ---------------------------------------------------------------- supplied expectations

def test_digest_must_be_supplied_and_match(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--expect-sha256 is required"):
        ap.load_manifest(p, expect_sha=None, expect_rows=3, expect_census=_census())
    with pytest.raises(SystemExit, match="SHA256"):
        ap.load_manifest(p, expect_sha="0" * 64, expect_rows=3, expect_census=_census())


def test_row_count_must_be_supplied_and_match(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--expect-rows is required"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=None, expect_census=_census())
    with pytest.raises(SystemExit, match="rows, approved"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=4,
                         expect_census=_census(person=3))


def test_census_must_be_supplied_and_match(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--expect-person is required"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3,
                         expect_census={"person": None, "household": 1, "organization": 0})
    with pytest.raises(SystemExit, match="census"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3,
                         expect_census=_census(person=1, household=2))


def test_census_must_sum_to_the_row_count(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="sums to"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3,
                         expect_census=_census(person=2, household=2))


def test_a_correct_manifest_loads(tmp_path):
    p = _manifest(tmp_path, _rows())
    rows, digest = ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3,
                                    expect_census=_census())
    assert len(rows) == 3 and digest == _sha(p)


@pytest.mark.parametrize("cols", ["entity", "owner"])
def test_both_manifest_column_vocabularies_are_accepted(tmp_path, cols):
    p = _manifest(tmp_path, _rows(), cols=cols)
    rows, _ = ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3, expect_census=_census())
    assert {r["owner_type"] for r in rows} == {"person", "household"}


def test_a_manifest_with_neither_vocabulary_is_refused(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("document_id,who,applied\n1,person,NO\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no owner type column"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=1,
                         expect_census={"person": 1, "household": 0, "organization": 0})


def test_rows_already_applied_are_refused(tmp_path):
    p = _manifest(tmp_path, _rows(), applied="YES")
    with pytest.raises(SystemExit, match="applied=NO"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3, expect_census=_census())


def test_duplicate_and_conflicting_rows_are_refused(tmp_path):
    dupe = _rows()
    dupe[-1] = (dupe[0][0], "household", 1, "dupe")
    p = _manifest(tmp_path, dupe)
    with pytest.raises(SystemExit, match="duplicate|conflicting"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=3,
                         expect_census=_census(person=2, household=1))


def test_unknown_owner_type_is_refused(tmp_path):
    p = _manifest(tmp_path, [(9000, "vendor", 1, "X")])
    with pytest.raises(SystemExit, match="unknown owner_type|census"):
        ap.load_manifest(p, expect_sha=_sha(p), expect_rows=1,
                         expect_census={"person": 1, "household": 0, "organization": 0})


# ---------------------------------------------------------------- confirmation + actor

def test_confirmation_phrase_is_batch_specific():
    assert ap.confirm_phrase("next-deterministic", 17) == "APPLY-NEXT-DETERMINISTIC-17"
    assert ap.confirm_phrase("next-deterministic", 18) != ap.confirm_phrase("next-deterministic", 17)
    assert ap.confirm_phrase("other", 17) != ap.confirm_phrase("next-deterministic", 17)


def test_the_previous_batches_phrase_is_refused(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--confirm"):
        _run(p, apply_changes=True, confirm="APPLY-STRICT-SAFE-162", actor_user_id=1)


def test_apply_without_confirmation_is_refused(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--confirm"):
        _run(p, apply_changes=True, confirm=None, actor_user_id=1)


def test_apply_without_an_actor_is_refused(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="actor-user-id"):
        _run(p, apply_changes=True, confirm=ap.confirm_phrase(BATCH, 3), actor_user_id=None)


# ---------------------------------------------------------------- all-or-nothing

def test_one_bad_row_aborts_the_whole_batch(tmp_path):
    """These document ids do not exist, so verification fails and NOTHING is applied or skipped."""
    p = _manifest(tmp_path, _rows())
    with pytest.raises(RuntimeError, match="all-or-nothing"):
        _run(p, apply_changes=False, snapshot_root=tmp_path)


def test_no_snapshot_is_written_when_verification_fails(tmp_path):
    p = _manifest(tmp_path, _rows())
    with pytest.raises(RuntimeError):
        _run(p, apply_changes=False, snapshot_root=tmp_path)
    assert not list(tmp_path.glob("owner-apply-*")), \
        "a failed verification must not leave a rollback snapshot behind"


def test_a_failed_row_is_never_demoted_to_a_skip():
    import inspect
    src = inspect.getsource(ap.run)
    assert "all-or-nothing" in src
    assert "continue" not in src.split("verification:")[0].split("for r in rows:")[-1][:400], \
        "the verification loop must not skip rows"


# ---------------------------------------------------------------- write-path guarantees

def test_the_write_guard_mirrors_the_canonical_path():
    import inspect

    from app.services import households
    src = inspect.getsource(ap.run)
    assert "person_id.is_(None)" in src and "household_id.is_(None)" in src \
        and "organization_id.is_(None)" in src
    assert "PERMANENT_REJECT_DOCUMENT_IDS" in src
    assert ap.PERMANENT_REJECT_DOCUMENT_IDS is households.PERMANENT_REJECT_DOCUMENT_IDS


def test_rows_are_locked_before_verification():
    import inspect
    src = inspect.getsource(ap._lock_documents)
    assert "for update" in src.lower()


def test_audit_events_enlist_in_the_same_transaction():
    import inspect
    assert "conn=conn" in inspect.getsource(ap.run)


def test_set_equality_and_fingerprint_guards_exist():
    import inspect
    src = inspect.getsource(ap.run)
    assert "written != set(ids)" in src, "exact set equality must be asserted"
    assert "_non_target_fingerprint" in src, "non-target ownership must be fingerprinted"


def test_only_ownership_columns_are_written():
    assert set(ap._OWNER_COLUMN.values()) == {"person_id", "household_id", "organization_id"}


# ---------------------------------------------------------------- rollback

def _snap(tmp_path, *, batch="unit-batch", owner_id=424242, doc=999999):
    d = tmp_path / f"snap-{uuid.uuid4().hex[:6]}"
    d.mkdir()
    c = d / "rollback_snapshot_owner_assignments.csv"
    c.write_text("document_id,owner_type,owner_id,owner_name,prev_person_id,prev_household_id,"
                 f"prev_organization_id,prev_status,prev_deleted_at\n"
                 f"{doc},person,{owner_id},x,,,,active,\n", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(
        {"batch_id": batch, "snapshot_sha256": _sha(c)}), encoding="utf-8")
    return d, c


def test_rollback_verifies_the_snapshot_digest(tmp_path):
    d, c = _snap(tmp_path)
    (d / "manifest.json").write_text(json.dumps(
        {"batch_id": "unit-batch", "snapshot_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256"):
        rb.load_snapshot(d)


def test_rollback_accepts_an_untampered_snapshot(tmp_path):
    d, c = _snap(tmp_path)
    rows, path, digest, meta = rb.load_snapshot(d)
    assert len(rows) == 1 and meta["snapshot_sha256"] == digest


def test_rollback_confirmation_is_batch_specific(tmp_path):
    d, _c = _snap(tmp_path)
    with pytest.raises(SystemExit, match="--confirm"):
        rb.run(d, apply_changes=True, confirm="ROLLBACK-STRICT-SAFE-162",
               out=lambda *_a: None)


def test_rollback_aborts_on_drift(tmp_path):
    d, _c = _snap(tmp_path)
    with pytest.raises(RuntimeError, match="drift"):
        rb.run(d, apply_changes=False, out=lambda *_a: None)


# ---------------------------------------------------------------- historical artifacts intact

def test_the_162_tool_is_untouched_and_still_constant():
    from scripts import apply_strict_safe_owner_manifest as old
    assert old.EXPECTED_ROWS == 162
    assert old.EXPECTED_TYPES == {"person": 59, "household": 103, "organization": 0}
    assert old.CONFIRM_PHRASE == "APPLY-STRICT-SAFE-162"
    assert old.EXPECTED_SHA256 == \
        "3eea23501da57618d6ff2a0dc405cde02230691fe10db160298e546d6c7ccc85"


def test_the_old_rollback_tool_still_loads_its_snapshot_format(tmp_path):
    from scripts import rollback_strict_safe_owner_manifest as oldrb
    d = tmp_path / "old"
    d.mkdir()
    c = d / "rollback_snapshot_owner_assignments.csv"
    c.write_text("document_id,owner_type,owner_id,owner_name,prev_person_id,prev_household_id,"
                 "prev_organization_id,prev_status,prev_deleted_at\n"
                 "1,person,1,x,,,,active,\n", encoding="utf-8")
    rows, path, digest, recorded = oldrb.load_snapshot(d)
    assert len(rows) == 1 and recorded is None


# ---------------------------------------------------------------- documented invocation

@pytest.mark.parametrize("script", ["apply_owner_manifest.py", "rollback_owner_manifest.py"])
def test_the_documented_command_resolves_repository_imports(script):
    """Run each script by file path from the repo root with PYTHONPATH CLEARED."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(ap.__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, str(Path("scripts") / script), "--help"],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=120)
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_every_expectation_flag_is_required_on_the_cli():
    """argparse must refuse a partial invocation rather than default anything."""
    for missing in ("--expect-sha256", "--expect-rows", "--expect-person",
                    "--expect-household", "--expect-organization", "--batch-id"):
        argv = ["--manifest", "m.csv", "--batch-id", "b", "--expect-sha256", "x",
                "--expect-rows", "1", "--expect-person", "1", "--expect-household", "0",
                "--expect-organization", "0"]
        i = argv.index(missing)
        del argv[i:i + 2]
        with pytest.raises(SystemExit):
            ap.main(argv)
