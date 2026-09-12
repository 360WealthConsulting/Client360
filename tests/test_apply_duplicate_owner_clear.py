"""Fail-closed guarantees for the guarded duplicate-owner clearing tool.

Clearing is the inverse risk of assigning. An assignment can only fill an empty column, and a
transfer at least names where a document is going; a clear silently removes an owner, and if the
reviewer was wrong about which owner was redundant it removes the real one. These tests pin the
properties that make it safe to run once against production:

* the expected MULTI-owner tuple is repeated in the UPDATE's own WHERE clause, so a row that moved
  between review and apply matches nothing and aborts the batch;
* only the named column is cleared, and every other owner column is asserted unchanged afterwards;
* a document can never be left with no owner at all;
* the manifest ASSERTS the state of the owner being cleared and the database must agree, so
  clearing a record the reviewer believed retired but which is still live is refused;
* every expectation is SUPPLIED, never derived, and the confirmation phrase carries the plan digest
  and the row count together;
* one bad row aborts the whole manifest, including a failure on the last row;
* and the sibling assignment and transfer tools stay fail-closed after this exists.

All fixtures are synthetic. No production identifier appears here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from scripts import apply_duplicate_owner_clear as dc

REVIEWER = "reviewer@example.invalid"


# ------------------------------------------------------------------ helpers

def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest(tmp_path, rows, *, applied="NO", reviewer=REVIEWER):
    """rows: [(doc_id, person|None, household|None, organization|None, clear, state)]"""
    p = tmp_path / f"m-{uuid.uuid4().hex[:6]}.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dc.MANIFEST_COLUMNS))
        w.writeheader()
        for did, per, hh, org, clear, state in rows:
            w.writerow({"document_id": did,
                        "expected_person_id": "" if per is None else per,
                        "expected_household_id": "" if hh is None else hh,
                        "expected_organization_id": "" if org is None else org,
                        "clear_column": clear, "cleared_owner_state": state,
                        "evidence": "synthetic fixture", "reviewed_by": reviewer,
                        "applied": applied})
    return p


def _sidecar(tmp_path, digest, count, *, version=dc.MANIFEST_VERSION):
    p = tmp_path / f"m-{uuid.uuid4().hex[:6]}.json"
    p.write_text(json.dumps({"manifest_version": version, "digest": digest,
                             "row_count": count, "reviewed_by": REVIEWER,
                             "created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    return p


def _digest_for(rows):
    return dc.plan_digest([
        {"document_id": r[0], "expected_person_id": r[1], "expected_household_id": r[2],
         "expected_organization_id": r[3], "clear_column": r[4]} for r in rows])


def _prepare(tmp_path, rows):
    cp = _manifest(tmp_path, rows)
    digest = _digest_for(rows)
    return cp, _sidecar(tmp_path, digest, len(rows)), digest


def _run(cp, jp, digest, rows, **kw):
    with engine.connect() as c:
        dbname = c.execute(text("select current_database()")).scalar_one()
    kw.setdefault("production_database", dbname)
    kw.setdefault("allow_disposable_database", True)
    kw.setdefault("out", lambda *_a: None)
    return dc.run(cp, manifest_json=jp, expect_sha=_sha(cp), expect_json_sha=_sha(jp),
                  expect_digest=digest, expect_rows=len(rows), **kw)


def _owners(ids):
    with engine.connect() as c:
        return {r["id"]: (r["person_id"], r["household_id"], r["organization_id"])
                for r in c.execute(text(
                    "select id, person_id, household_id, organization_id from documents "
                    "where id = any(:ids)"), {"ids": ids}).mappings()}


@pytest.fixture
def world():
    """A household with a member, a business entity, a retired shell person, and documents."""
    made = {"documents": [], "people": [], "households": [], "entities": []}
    with engine.begin() as c:
        hh = c.execute(text(
            "insert into households (name) values ('Synthetic Household') returning id"
        )).scalar_one()
        member = c.execute(text(
            "insert into people (full_name, first_name, last_name, household_id, active) "
            "values ('Synthetic Member', 'Synthetic', 'Member', :h, true) returning id"),
            {"h": hh}).scalar_one()
        shell = c.execute(text(
            "insert into people (full_name, first_name, last_name, active) "
            "values ('Synthetic Shell Co', 'Synthetic', 'Shell', false) returning id")).scalar_one()
        live_person = c.execute(text(
            "insert into people (full_name, first_name, last_name, active) "
            "values ('Synthetic Live', 'Synthetic', 'Live', true) returning id")).scalar_one()
        org = c.execute(text(
            "insert into relationship_entities (entity_type, name, active) "
            "values ('business', 'Synthetic Business', true) returning id")).scalar_one()
        made["households"].append(hh)
        made["people"] += [member, shell, live_person]
        made["entities"].append(org)

        def doc(person=None, household=None, organization=None):
            did = c.execute(text(
                "insert into documents (original_name, stored_name, storage_path, size_bytes, "
                "sha256, person_id, household_id, organization_id, status, archived) "
                "values ('s.pdf', :s, 'unit/s.pdf', 1, :h, :p, :hh, :o, 'active', false) "
                "returning id"),
                {"s": f"unit-{uuid.uuid4().hex}", "h": uuid.uuid4().hex * 2,
                 "p": person, "hh": household, "o": organization}).scalar_one()
            made["documents"].append(did)
            return did

        ids = {
            "person_org": doc(person=shell, organization=org),
            "person_hh": doc(person=member, household=hh),
            "org_hh": doc(household=hh, organization=org),
            "person_org_live": doc(person=live_person, organization=org),
            "single": doc(person=member),
            "outside": doc(person=shell),
        }
    yield {"hh": hh, "member": member, "shell": shell, "live": live_person, "org": org, **ids}
    with engine.begin() as c:
        # audit_events is append-only by trigger; the rows stay in the disposable database and
        # carry no foreign key to documents, so the documents below still delete cleanly.
        c.execute(text("delete from documents where id = any(:i)"), {"i": made["documents"]})
        c.execute(text("update people set household_id = null where id = any(:i)"),
                  {"i": made["people"]})
        c.execute(text("delete from relationship_entities where id = any(:i)"),
                  {"i": made["entities"]})
        c.execute(text("delete from people where id = any(:i)"), {"i": made["people"]})
        c.execute(text("delete from households where id = any(:i)"), {"i": made["households"]})


# ------------------------------------------------------- supported combinations

def test_person_plus_organization_becomes_organization_only(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    rep = _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1),
               actor_user_id=1)
    assert rep["committed"] and rep["cleared"] == 1
    assert _owners([world["person_org"]])[world["person_org"]] == (None, None, world["org"])


def test_person_plus_household_becomes_household_only(world, tmp_path):
    rows = [(world["person_hh"], world["member"], world["hh"], None, "person", "active")]
    cp, jp, d = _prepare(tmp_path, rows)
    _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1), actor_user_id=1)
    assert _owners([world["person_hh"]])[world["person_hh"]] == (None, world["hh"], None)


def test_household_plus_organization_is_supported(world, tmp_path):
    rows = [(world["org_hh"], None, world["hh"], world["org"], "household", "active")]
    cp, jp, d = _prepare(tmp_path, rows)
    _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1), actor_user_id=1)
    assert _owners([world["org_hh"]])[world["org_hh"]] == (None, None, world["org"])


def test_the_retained_owner_column_is_never_written(world, tmp_path):
    before = _owners([world["person_org"]])[world["person_org"]]
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1), actor_user_id=1)
    after = _owners([world["person_org"]])[world["person_org"]]
    assert after[2] == before[2], "the retained organization must be byte-identical"
    assert after[1] is None


# ------------------------------------------------------- refusals

def test_clearing_an_owner_the_manifest_calls_retired_but_is_active_refuses(world, tmp_path):
    """The manifest asserts 'inactive'; the person is active. That disagreement must abort."""
    rows = [(world["person_org_live"], world["live"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    before = _owners([world["person_org_live"]])
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1), actor_user_id=1)
    assert _owners([world["person_org_live"]]) == before


def test_tuple_drift_refuses_and_rolls_back(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with engine.begin() as c:            # somebody edits the row after review
        c.execute(text("update documents set household_id = :h where id = :i"),
                  {"h": world["hh"], "i": world["person_org"]})
    before = _owners([world["person_org"]])
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1), actor_user_id=1)
    assert _owners([world["person_org"]]) == before


def test_a_row_that_moves_under_the_lock_aborts_everything(world, tmp_path, monkeypatch):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive"),
            (world["person_hh"], world["member"], world["hh"], None, "person", "active")]
    cp, jp, d = _prepare(tmp_path, rows)
    before = _owners([world["person_org"], world["person_hh"]])
    monkeypatch.setattr(dc, "verify_row", lambda conn, row, doc: [])
    monkeypatch.setattr(dc, "_where_and_params",
                        lambda row: ("UPDATE documents SET person_id = NULL WHERE id = -1 "
                                     "RETURNING id", {}))
    with pytest.raises(RuntimeError, match="did not clear"):
        _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 2), actor_user_id=1)
    assert _owners([world["person_org"], world["person_hh"]]) == before


def test_a_single_owner_document_is_refused(world, tmp_path):
    rows = [(world["single"], world["member"], None, None, "person", "active")]
    cp = _manifest(tmp_path, rows)
    with pytest.raises(SystemExit, match="only clears a DUPLICATE owner"):
        dc.load_manifest(cp, expect_sha=_sha(cp), expect_rows=1, expect_digest="x")


def test_clearing_a_column_not_in_the_expected_tuple_is_refused(world, tmp_path):
    rows = [(world["person_hh"], world["member"], world["hh"], None, "organization", "active")]
    cp = _manifest(tmp_path, rows)
    with pytest.raises(SystemExit, match="is not set in the expected tuple"):
        dc.load_manifest(cp, expect_sha=_sha(cp), expect_rows=1, expect_digest="x")


def test_a_missing_document_refuses(world, tmp_path):
    rows = [(2_000_000_003, 1, None, 1, "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="documents not found"):
        _run(cp, jp, d, rows)


# ------------------------------------------------------- row-set and expectations

def test_extra_manifest_row_changes_the_digest_and_refuses(world, tmp_path):
    one = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    two = one + [(world["person_hh"], world["member"], world["hh"], None, "person", "active")]
    cp, jp, d = _prepare(tmp_path, one)
    cp2 = _manifest(tmp_path, two)
    with pytest.raises(SystemExit, match="rows, approved"):
        dc.load_manifest(cp2, expect_sha=_sha(cp2), expect_rows=1, expect_digest=d)
    with pytest.raises(SystemExit, match="plan digest"):
        dc.load_manifest(cp2, expect_sha=_sha(cp2), expect_rows=2, expect_digest=d)


def test_missing_manifest_row_refuses(world, tmp_path):
    two = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive"),
           (world["person_hh"], world["member"], world["hh"], None, "person", "active")]
    cp, jp, d = _prepare(tmp_path, two)
    one = _manifest(tmp_path, two[:1])
    with pytest.raises(SystemExit, match="rows, approved"):
        dc.load_manifest(one, expect_sha=_sha(one), expect_rows=2, expect_digest=d)


def test_wrong_csv_hash_refuses(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="SHA256"):
        dc.load_manifest(cp, expect_sha="0" * 64, expect_rows=1, expect_digest=d)


def test_wrong_json_hash_refuses(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="json SHA256"):
        dc.run(cp, manifest_json=jp, expect_sha=_sha(cp), expect_json_sha="0" * 64,
               expect_digest=d, expect_rows=1, production_database="x", out=lambda *_a: None)


def test_wrong_digest_refuses(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, _ = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="plan digest"):
        _run(cp, jp, "0" * 64, rows)


def test_wrong_confirmation_refuses(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    assert d[:12].upper() in dc.confirm_phrase(d, 1)
    with pytest.raises(SystemExit, match="requires --confirm"):
        _run(cp, jp, d, rows, apply_changes=True, confirm="NOPE", actor_user_id=1)


def test_apply_requires_an_actor(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="requires --actor-user-id"):
        _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1),
             actor_user_id=None)


def test_applied_column_must_say_no(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp = _manifest(tmp_path, rows, applied="YES")
    with pytest.raises(SystemExit, match="not applied=NO"):
        dc.load_manifest(cp, expect_sha=_sha(cp), expect_rows=1, expect_digest="x")


def test_plan_digest_is_row_order_independent(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive"),
            (world["person_hh"], world["member"], world["hh"], None, "person", "active")]
    assert _digest_for(rows) == _digest_for(list(reversed(rows)))


# ------------------------------------------------------- database identity

def test_wrong_database_name_refuses(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="--production-database says"):
        _run(cp, jp, d, rows, production_database="not_the_database")


def test_a_disposable_database_is_refused_as_production(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(SystemExit, match="disposable test database"):
        _run(cp, jp, d, rows, allow_disposable_database=False)


# ------------------------------------------------------- dry run, rollback, audit

def test_dry_run_writes_nothing(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    before = _owners([world["person_org"]])
    rep = _run(cp, jp, d, rows)
    assert rep["dry_run"] and not rep["committed"] and rep["validated"] == 1
    assert _owners([world["person_org"]]) == before


def test_a_failure_on_the_last_row_rolls_back_the_earlier_ones(world, tmp_path, monkeypatch):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive"),
            (world["person_hh"], world["member"], world["hh"], None, "person", "active"),
            (world["org_hh"], None, world["hh"], world["org"], "household", "active")]
    cp, jp, d = _prepare(tmp_path, rows)
    before = _owners([world["person_org"], world["person_hh"], world["org_hh"]])
    real, seen = dc.verify_row, {"n": 0}

    def failing(conn, row, doc):
        seen["n"] += 1
        return ["synthetic late failure"] if seen["n"] == 3 else real(conn, row, doc)

    monkeypatch.setattr(dc, "verify_row", failing)
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 3), actor_user_id=1)
    assert _owners([world["person_org"], world["person_hh"], world["org_hh"]]) == before


def test_audit_events_are_chained_and_carry_the_decision(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive"),
            (world["person_hh"], world["member"], world["hh"], None, "person", "active")]
    cp, jp, d = _prepare(tmp_path, rows)
    _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 2), actor_user_id=1)
    with engine.connect() as c:
        au = c.execute(text(
            "select entity_id, action, actor_user_id, metadata, entry_hash, prev_hash "
            "from audit_events where request_id = :r order by id"),
            {"r": f"duplicate-owner-clear:{d[:12]}:2"}).mappings().all()
    assert len(au) == 2
    for r in au:
        assert r["action"] == "document.ownership_conflict_resolved"
        assert r["actor_user_id"] == 1 and r["entry_hash"]
        m = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])
        for key in ("cleared_owner_type", "cleared_owner_id", "cleared_owner_state",
                    "retained_owners", "expected_owner_tuple", "evidence", "reviewed_by",
                    "manifest_digest", "applied_at"):
            assert key in m, f"audit metadata is missing {key}"
        assert m["manifest_digest"] == d
    assert au[1]["prev_hash"] == au[0]["entry_hash"], "entries must chain"


def test_no_audit_row_survives_a_rolled_back_batch(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive"),
            (world["person_org_live"], world["live"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    with pytest.raises(RuntimeError):
        _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 2), actor_user_id=1)
    with engine.connect() as c:
        assert c.execute(text("select count(*) from audit_events where request_id = :r"),
                         {"r": f"duplicate-owner-clear:{d[:12]}:2"}).scalar_one() == 0


def test_no_document_outside_the_manifest_changes(world, tmp_path):
    rows = [(world["person_org"], world["shell"], None, world["org"], "person", "inactive")]
    cp, jp, d = _prepare(tmp_path, rows)
    before = _owners([world["outside"], world["person_hh"], world["single"]])
    _run(cp, jp, d, rows, apply_changes=True, confirm=dc.confirm_phrase(d, 1), actor_user_id=1)
    assert _owners([world["outside"], world["person_hh"], world["single"]]) == before


# ------------------------------------------------------- siblings stay fail-closed

def test_the_unowned_assignment_path_remains_fail_closed(world, tmp_path):
    from app.services.households import resolve_document_ownership

    result = resolve_document_ownership(world["person_hh"], person_id=world["member"],
                                        actor_user_id=1)
    assert result["assigned"] is False and result["reason"] == "already_owned"


def test_the_transfer_tool_still_refuses_a_multi_owner_row(world, tmp_path):
    """A doubly-owned row cannot match the transfer tool's single-column expected tuple."""
    from scripts import apply_ownership_transfer as tx

    with engine.connect() as c:
        doc = c.execute(text(
            "select id, person_id, household_id, organization_id, status, archived, archived_at, "
            "deleted_at from documents where id = :i"), {"i": world["person_hh"]}).mappings().one()
        fails = tx.verify_row(c, {"document_id": doc["id"], "expected_owner_type": "person",
                                  "expected_owner_id": world["member"],
                                  "new_owner_type": "household",
                                  "new_owner_id": world["hh"]}, doc)
    assert any("current owner" in f for f in fails)
