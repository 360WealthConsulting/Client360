"""Fail-closed guarantees for the guarded ownership TRANSFER tool.

A transfer is more dangerous than an assignment: an assignment can only fill an empty owner, while
a transfer overwrites a decision somebody already made. These tests pin the properties that make it
safe to run once against production:

* the dry run performs the whole transaction and rolls it back, so it proves the apply would
  succeed without persisting anything;
* the reviewed owner is repeated in the UPDATE's own WHERE clause, so a row that moved between
  review and apply matches nothing and aborts the batch;
* ONE bad row aborts the WHOLE manifest, including a failure on the last row;
* every expectation — CSV digest, JSON digest, plan digest, row count — is SUPPLIED, never derived;
* the confirmation phrase carries the plan digest and the row count together;
* a disposable test database can never be mistaken for production, in either direction;
* the audit row lands in the same transaction as the write, so the ledger and the data cannot
  disagree;
* and the ORIGINAL unowned-assignment path stays fail-closed — this tool does not weaken it.
"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from scripts import apply_ownership_transfer as ax

REVIEWER = "michael@360wealthconsulting.com"


# ------------------------------------------------------------------ fixtures

def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_manifest(tmp_path, rows, *, reviewer=REVIEWER):
    """rows: [(document_id, exp_type, exp_id, new_type, new_id)]"""
    p = tmp_path / f"m-{uuid.uuid4().hex[:6]}.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ax.MANIFEST_COLUMNS))
        w.writeheader()
        for did, et, ei, nt, ni in rows:
            w.writerow({"document_id": did, "expected_owner_type": et, "expected_owner_id": ei,
                        "new_owner_type": nt, "new_owner_id": ni,
                        "source_system": "TaxDome Drive", "source_key": "taxdome_folder:unit",
                        "evidence": "unit test fixture", "reviewed_by": reviewer})
    return p


def _write_sidecar(tmp_path, digest, row_count, *, version=ax.MANIFEST_VERSION, reviewer=REVIEWER):
    p = tmp_path / f"m-{uuid.uuid4().hex[:6]}.json"
    p.write_text(json.dumps({"manifest_version": version, "digest": digest,
                             "row_count": row_count, "reviewed_by": reviewer,
                             "created_at": "2026-09-12T00:00:00Z"}), encoding="utf-8")
    return p


@pytest.fixture
def world():
    """Two people, a household and three documents, all disposable."""
    made = {"documents": [], "people": [], "households": []}
    with engine.begin() as c:
        hh = c.execute(text(
            "insert into households (name) values ('Transfer Test Household') returning id"
        )).scalar_one()
        made["households"].append(hh)
        src = c.execute(text(
            "insert into people (full_name, first_name, last_name, active) "
            "values ('Stub Owner', 'Stub', 'Owner', true) returning id")).scalar_one()
        dst = c.execute(text(
            "insert into people (full_name, first_name, last_name, household_id, active) "
            "values ('Real Client', 'Real', 'Client', :h, true) returning id"),
            {"h": hh}).scalar_one()
        made["people"] += [src, dst]
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            did = c.execute(text(
                "insert into documents (original_name, stored_name, storage_path, size_bytes, "
                "sha256, person_id, status, archived) "
                "values (:n, :s, :sp, 1, :h, :p, 'active', false) returning id"),
                {"n": name, "s": f"unit-{uuid.uuid4().hex}", "sp": f"unit/{name}",
                 "h": uuid.uuid4().hex * 2, "p": src}).scalar_one()
            made["documents"].append(did)
    yield {"src": src, "dst": dst, "household": hh, "docs": made["documents"]}
    with engine.begin() as c:
        # audit_events is append-only by database trigger, which is exactly right: a test must not
        # be able to erase a ledger entry. The rows stay in the disposable database, and they carry
        # no foreign key to documents, so the document rows below still delete cleanly.
        c.execute(text("delete from documents where id = any(:ids)"),
                  {"ids": made["documents"]})
        c.execute(text("update people set household_id = null where id = any(:ids)"),
                  {"ids": made["people"]})
        c.execute(text("delete from people where id = any(:ids)"), {"ids": made["people"]})
        c.execute(text("delete from households where id = any(:ids)"),
                  {"ids": made["households"]})


def _plan(world):
    """The canonical three-row plan: person/person, person/person, person/household."""
    d = world["docs"]
    return [(d[0], "person", world["src"], "person", world["dst"]),
            (d[1], "person", world["src"], "person", world["dst"]),
            (d[2], "person", world["src"], "household", world["household"])]


def _prepared(tmp_path, plan):
    csv_path = _write_manifest(tmp_path, plan)
    rows, _, digest = ax.load_manifest(
        csv_path, expect_sha=_sha(csv_path), expect_rows=len(plan),
        expect_digest=ax.plan_digest([{"document_id": p[0], "expected_owner_type": p[1],
                                       "expected_owner_id": p[2], "new_owner_type": p[3],
                                       "new_owner_id": p[4]} for p in plan]))
    json_path = _write_sidecar(tmp_path, digest, len(plan))
    return csv_path, json_path, digest


def _run(csv_path, json_path, digest, plan, **kw):
    with engine.connect() as c:
        dbname = c.execute(text("select current_database()")).scalar_one()
    kw.setdefault("production_database", dbname)
    kw.setdefault("allow_disposable_database", True)
    kw.setdefault("out", lambda *_a: None)
    return ax.run(csv_path, manifest_json=json_path, expect_sha=_sha(csv_path),
                  expect_json_sha=_sha(json_path), expect_digest=digest,
                  expect_rows=len(plan), **kw)


def _owners(ids):
    with engine.connect() as c:
        return {r["id"]: (r["person_id"], r["household_id"], r["organization_id"])
                for r in c.execute(text(
                    "select id, person_id, household_id, organization_id from documents "
                    "where id = any(:ids)"), {"ids": ids}).mappings()}


# ------------------------------------------------------------------ 1. dry run writes nothing

def test_dry_run_performs_zero_writes(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    before = _owners(world["docs"])
    report = _run(csv_path, json_path, digest, plan)
    assert report["dry_run"] is True and report["committed"] is False
    assert report["validated"] == 3 and report["transferred"] == 0
    assert _owners(world["docs"]) == before
    with engine.connect() as c:
        assert c.execute(text(
            "select count(*) from audit_events where entity_type='document' "
            "and entity_id = any(:ids)"),
            {"ids": [str(d) for d in world["docs"]]}).scalar_one() == 0


# ------------------------------------------------------------------ 2. the three-row transfer

def test_exact_three_row_person_person_household_transfer(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    report = _run(csv_path, json_path, digest, plan, apply_changes=True,
                  confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    assert report["committed"] is True
    assert report["transferred"] == 3 and report["audit_rows"] == 3
    got = _owners(world["docs"])
    assert got[world["docs"][0]] == (world["dst"], None, None)
    assert got[world["docs"][1]] == (world["dst"], None, None)
    assert got[world["docs"][2]] == (None, world["household"], None)
    for owner in got.values():
        assert sum(1 for v in owner if v is not None) == 1


# ------------------------------------------------------------------ 3. stale owner refuses

def test_stale_current_owner_refuses_and_rolls_back(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with engine.begin() as c:          # somebody moves a row after review
        c.execute(text("update documents set person_id = :d where id = :i"),
                  {"d": world["dst"], "i": world["docs"][1]})
    before = _owners(world["docs"])
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    assert _owners(world["docs"]) == before


# ------------------------------------------------------------------ 4. missing target refuses

def test_missing_target_entity_refuses_and_rolls_back(world, tmp_path):
    d = world["docs"]
    plan = [(d[0], "person", world["src"], "person", 2_000_000_001)]
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    before = _owners(world["docs"])
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 1), actor_user_id=1)
    assert _owners(world["docs"]) == before


def test_inactive_target_entity_refuses(world, tmp_path):
    d = world["docs"]
    with engine.begin() as c:
        c.execute(text("update people set active = false where id = :i"), {"i": world["dst"]})
    plan = [(d[0], "person", world["src"], "person", world["dst"])]
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 1), actor_user_id=1)


# ------------------------------------------------------------------ 5. retired documents refuse

@pytest.mark.parametrize("column,value", [("archived", True), ("status", "deleted")])
def test_archived_or_deleted_document_refuses(world, tmp_path, column, value):
    d = world["docs"]
    with engine.begin() as c:
        c.execute(text(f"update documents set {column} = :v where id = :i"),
                  {"v": value, "i": d[0]})
    plan = [(d[0], "person", world["src"], "person", world["dst"])]
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    before = _owners([d[0]])
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 1), actor_user_id=1)
    assert _owners([d[0]]) == before


def test_deleted_at_alone_refuses(world, tmp_path):
    d = world["docs"]
    with engine.begin() as c:
        c.execute(text("update documents set deleted_at = now() where id = :i"), {"i": d[0]})
    plan = [(d[0], "person", world["src"], "person", world["dst"])]
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 1), actor_user_id=1)


# ------------------------------------------------------------------ 6. mixed owner columns

def test_mixed_owner_columns_refuse(world, tmp_path):
    """A doubly-owned document does not equal any single expected owner, so it cannot transfer."""
    d = world["docs"]
    with engine.begin() as c:
        c.execute(text("update documents set household_id = :h where id = :i"),
                  {"h": world["household"], "i": d[0]})
    plan = [(d[0], "person", world["src"], "person", world["dst"])]
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    before = _owners([d[0]])
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 1), actor_user_id=1)
    assert _owners([d[0]]) == before


def test_a_transfer_always_leaves_exactly_one_owner_column(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    _run(csv_path, json_path, digest, plan, apply_changes=True,
         confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    for owner in _owners(world["docs"]).values():
        assert sum(1 for v in owner if v is not None) == 1


# ------------------------------------------------------------------ 7. digest / confirmation

def test_wrong_csv_digest_refuses(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="SHA256"):
        ax.run(csv_path, manifest_json=json_path, expect_sha="0" * 64,
               expect_json_sha=_sha(json_path), expect_digest=digest, expect_rows=3,
               production_database="x", out=lambda *_a: None)


def test_wrong_plan_digest_refuses(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, _ = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="plan digest"):
        _run(csv_path, json_path, "0" * 64, plan)


def test_wrong_json_digest_refuses(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="json SHA256"):
        ax.run(csv_path, manifest_json=json_path, expect_sha=_sha(csv_path),
               expect_json_sha="0" * 64, expect_digest=digest, expect_rows=3,
               production_database="x", out=lambda *_a: None)


def test_row_count_mismatch_refuses(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="rows, approved"):
        ax.run(csv_path, manifest_json=json_path, expect_sha=_sha(csv_path),
               expect_json_sha=_sha(json_path), expect_digest=digest, expect_rows=2,
               production_database="x", out=lambda *_a: None)


def test_confirmation_phrase_carries_digest_and_row_count(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    want = ax.confirm_phrase(digest, 3)
    assert digest[:12].upper() in want and want.endswith("-3")
    with pytest.raises(SystemExit, match="requires --confirm"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm="APPLY-OWNERSHIP-TRANSFER-WRONG-3", actor_user_id=1)


def test_apply_requires_an_actor(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="requires --actor-user-id"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 3), actor_user_id=None)


# ------------------------------------------------------------------ 8. database identity

def test_wrong_database_name_refuses(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="--production-database says"):
        _run(csv_path, json_path, digest, plan, production_database="not_the_database")


def test_a_test_database_is_refused_as_production(world, tmp_path):
    """The suite runs on a disposable database; without the test opt-in it must be refused."""
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with pytest.raises(SystemExit, match="disposable test database"):
        _run(csv_path, json_path, digest, plan, allow_disposable_database=False)


# ------------------------------------------------------------------ 9. audit

def test_audit_rows_are_written_in_the_same_transaction_and_chain(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    _run(csv_path, json_path, digest, plan, apply_changes=True,
         confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    with engine.connect() as c:
        rows = c.execute(text(
            "select entity_id, action, actor_user_id, metadata, entry_hash, prev_hash "
            "from audit_events where entity_type='document' and entity_id = any(:ids) "
            "order by id"), {"ids": [str(d) for d in world["docs"]]}).mappings().all()
    assert len(rows) == 3
    for r in rows:
        assert r["action"] == "document.ownership_conflict_resolved"
        assert r["actor_user_id"] == 1
        m = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])
        for key in ("former_owner_type", "former_owner_id", "new_owner_type", "new_owner_id",
                    "actor_user_id", "source_system", "source_key", "evidence", "reviewed_by",
                    "manifest_digest", "applied_at"):
            assert key in m, f"audit metadata is missing {key}"
        assert m["manifest_digest"] == digest
        assert m["former_owner_id"] == world["src"]
        assert r["entry_hash"], "the audit entry is not hash-chained"


def test_no_audit_row_survives_a_rolled_back_batch(world, tmp_path):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    with engine.begin() as c:
        c.execute(text("update documents set person_id = :d where id = :i"),
                  {"d": world["dst"], "i": world["docs"][2]})
    with pytest.raises(RuntimeError):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    with engine.connect() as c:
        assert c.execute(text(
            "select count(*) from audit_events where entity_type='document' "
            "and entity_id = any(:ids)"),
            {"ids": [str(d) for d in world["docs"]]}).scalar_one() == 0


# ------------------------------------------------------------------ 10. out-of-scope rows

def test_no_document_outside_the_manifest_changes(world, tmp_path):
    """A fourth document on the same former owner must be untouched."""
    with engine.begin() as c:
        extra = c.execute(text(
            "insert into documents (original_name, stored_name, storage_path, size_bytes, "
            "sha256, person_id, status, archived) "
            "values ('outside.pdf', :s, 'unit/outside.pdf', 1, :h, :p, 'active', false) "
            "returning id"),
            {"s": f"unit-{uuid.uuid4().hex}", "h": uuid.uuid4().hex * 2,
             "p": world["src"]}).scalar_one()
    try:
        plan = _plan(world)
        csv_path, json_path, digest = _prepared(tmp_path, plan)
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
        assert _owners([extra])[extra] == (world["src"], None, None)
    finally:
        with engine.begin() as c:
            c.execute(text("delete from documents where id = :i"), {"i": extra})


# ------------------------------------------------------------------ 11. third-row rollback

def test_a_failure_on_the_third_row_rolls_back_the_first_two(world, tmp_path, monkeypatch):
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    before = _owners(world["docs"])
    real = ax.verify_row
    seen = {"n": 0}

    def failing(conn, row, doc):
        seen["n"] += 1
        return ["synthetic late failure"] if seen["n"] == 3 else real(conn, row, doc)

    monkeypatch.setattr(ax, "verify_row", failing)
    with pytest.raises(RuntimeError, match="failed verification"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    assert _owners(world["docs"]) == before


def test_a_row_that_moves_under_the_lock_aborts_everything(world, tmp_path, monkeypatch):
    """The UPDATE's own WHERE clause is the last line of defence."""
    plan = _plan(world)
    csv_path, json_path, digest = _prepared(tmp_path, plan)
    before = _owners(world["docs"])
    monkeypatch.setattr(ax, "verify_row", lambda conn, row, doc: [])
    bad = ax._TRANSFER_SQL.replace("IS NOT DISTINCT FROM :exp_person",
                                   "IS NOT DISTINCT FROM -1")
    monkeypatch.setattr(ax, "_TRANSFER_SQL", bad)
    with pytest.raises(RuntimeError, match="did not transfer"):
        _run(csv_path, json_path, digest, plan, apply_changes=True,
             confirm=ax.confirm_phrase(digest, 3), actor_user_id=1)
    assert _owners(world["docs"]) == before


# ------------------------------------------------------------------ 12. the old path is intact

def test_the_unowned_assignment_path_remains_fail_closed(world, tmp_path):
    """This tool must not weaken resolve_document_ownership, which never overwrites an owner."""
    from app.services.households import resolve_document_ownership

    owned = world["docs"][0]
    result = resolve_document_ownership(owned, person_id=world["dst"], actor_user_id=1)
    assert result["assigned"] is False and result["reason"] == "already_owned"
    assert _owners([owned])[owned] == (world["src"], None, None)


def test_apply_owner_manifest_still_refuses_an_owned_document(world):
    """The sibling assignment tool's guard is unchanged by the existence of a transfer path.

    Exercised through its real verify_row against a live connection, so this fails if the guard is
    ever relaxed rather than merely reworded.
    """
    from scripts import apply_owner_manifest as ap

    with engine.connect() as c:
        doc = c.execute(text(
            "select d.id, d.person_id, d.household_id, d.organization_id, d.status, "
            "d.deleted_at, null as sp, 0 as avail, 0 as nsrc "
            "from documents d where d.id = :i"), {"i": world["docs"][0]}).mappings().one()
        idx = {"owner_eligible": {world["dst"]}, "staff": set(), "org_eligible": set(),
               "firm_entities": set(), "members": {}}
        fails = ap.verify_row(c, {"document_id": doc["id"], "owner_type": "person",
                                  "owner_id": world["dst"]}, idx, doc)
    assert "document already has an owner" in fails


# ------------------------------------------------------------------ manifest validation

def test_a_no_op_transfer_is_refused(world, tmp_path):
    d = world["docs"]
    plan = [(d[0], "person", world["src"], "person", world["src"])]
    csv_path = _write_manifest(tmp_path, plan)
    with pytest.raises(SystemExit, match="no-op"):
        ax.load_manifest(csv_path, expect_sha=_sha(csv_path), expect_rows=1,
                         expect_digest="whatever")


def test_evidence_and_reviewer_are_required(world, tmp_path):
    d = world["docs"]
    plan = [(d[0], "person", world["src"], "person", world["dst"])]
    csv_path = _write_manifest(tmp_path, plan, reviewer="")
    with pytest.raises(SystemExit, match="reviewed_by is required"):
        ax.load_manifest(csv_path, expect_sha=_sha(csv_path), expect_rows=1,
                         expect_digest="whatever")


def test_duplicate_document_rows_are_refused(world, tmp_path):
    d = world["docs"]
    plan = [(d[0], "person", world["src"], "person", world["dst"]),
            (d[0], "person", world["src"], "household", world["household"])]
    csv_path = _write_manifest(tmp_path, plan)
    with pytest.raises(SystemExit, match="duplicate document_id"):
        ax.load_manifest(csv_path, expect_sha=_sha(csv_path), expect_rows=2,
                         expect_digest="whatever")


def test_unknown_owner_type_is_refused(world, tmp_path):
    d = world["docs"]
    plan = [(d[0], "person", world["src"], "wormhole", 1)]
    csv_path = _write_manifest(tmp_path, plan)
    with pytest.raises(SystemExit, match="unknown new_owner_type"):
        ax.load_manifest(csv_path, expect_sha=_sha(csv_path), expect_rows=1,
                         expect_digest="whatever")


def test_plan_digest_is_row_order_independent(world, tmp_path):
    plan = _plan(world)
    a = ax.plan_digest([{"document_id": p[0], "expected_owner_type": p[1],
                         "expected_owner_id": p[2], "new_owner_type": p[3],
                         "new_owner_id": p[4]} for p in plan])
    b = ax.plan_digest([{"document_id": p[0], "expected_owner_type": p[1],
                         "expected_owner_id": p[2], "new_owner_type": p[3],
                         "new_owner_id": p[4]} for p in reversed(plan)])
    assert a == b


def test_manifest_version_must_match(world, tmp_path):
    plan = _plan(world)
    csv_path, _, digest = _prepared(tmp_path, plan)
    bad = _write_sidecar(tmp_path, digest, 3, version=99)
    with pytest.raises(SystemExit, match="manifest_version"):
        ax.run(csv_path, manifest_json=bad, expect_sha=_sha(csv_path),
               expect_json_sha=_sha(bad), expect_digest=digest, expect_rows=3,
               production_database="x", out=lambda *_a: None)
