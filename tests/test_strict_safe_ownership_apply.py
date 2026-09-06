"""Strict-safe ownership batch — the service refactor, the gates, and the exact reversal.

This batch assigns real client documents to real people in production. Every test below pins one way
that could go wrong: a manifest that is not the approved one, a proposal that moved since review, a
document that gained an owner, a partial write, or a rollback that restores something other than
exactly what was there.

The fixture builds a small batch and patches the approved composition to match, so the real code
path runs without needing the 541-row production manifest. The digest and composition gates are
exercised against their real production values in test_production_manifest_controls_are_reproduced.

Temp rows only, all tagged, all cleaned up.
"""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services import document_strict_safe_ownership as sso
from app.services.households import resolve_document_ownership
from scripts import apply_strict_safe_ownership as ap
from scripts import rollback_strict_safe_ownership as rb

_TAG = f"SSO{uuid.uuid4().hex[:6]}"

MANIFEST_COLUMNS = ["document_id", "original_name", "person_id", "person_name",
                    "corroborator_count", "email_match", "phone_match", "address_match", "evidence"]


@pytest.fixture(autouse=True)
def _clean():
    yield
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"sso:{_TAG}%")))]
        if ids:
            c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))


def _person(name: str) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=name, last_name=_TAG, full_name=f"{name} {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()


def _evidence(person_name, *, exact_name=True, email=True, phone=False, address=True):
    ev = []
    if exact_name:
        ev.append(f"✓ exact name '{person_name}'")
    if email:
        ev.append("✓ email someone@example.com matched")
    if phone:
        ev.append("✓ phone ending 0123 matched")
    if address:
        ev.append("✓ address/ZIP matched")
    ev.append("context only (not an owner): irs")
    return ev


def _doc(person_id, person_name, *, route="HIGH", entity_type="person", evidence=None,
         review_status="not_required", archived=False, status="active", owner=None) -> int:
    name = f"{_TAG}-{uuid.uuid4().hex[:6]}.pdf"
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"sso:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{name}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status=review_status, current_version=1, person_id=owner,
            tags={"source_system": "SharePoint"},
        ).returning(documents.c.id)).scalar_one()
        facts = metadata.tables["document_facts"]
        c.execute(facts.insert().values(
            document_id=did, fact_type="owner_proposal",
            fact_value=json.dumps({
                "route": route, "confidence": "HIGH", "entity_type": entity_type,
                "entity_id": person_id, "entity_name": person_name,
                "evidence": evidence if evidence is not None else _evidence(person_name),
            }),
            confidence=0.0, extraction_engine="owner_proposal", extractor_version="test",
            version=1, is_current=True))
    return did


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.person_id, documents.c.household_id,
                                documents.c.organization_id, documents.c.review_status,
                                documents.c.tags, documents.c.status, documents.c.archived,
                                documents.c.sha256, documents.c.storage_uri,
                                documents.c.original_name, documents.c.deleted_at)
                         .where(documents.c.id == did)).mappings().one()


def _write_manifest(tmp_path, plan_rows) -> tuple[Path, str]:
    path = tmp_path / "strict_safe_ownership_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(plan_rows, key=lambda x: x["document_id"]):
            w.writerow({
                "document_id": r["document_id"], "original_name": r["original_name"],
                "person_id": r["person_id"], "person_name": r["person_name"],
                "corroborator_count": r["corroborator_count"],
                "email_match": r["email_match"], "phone_match": r["phone_match"],
                "address_match": r["address_match"],
                "evidence": json.dumps(r["evidence"], ensure_ascii=False)})
    return path, ap.sha256_of(path)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Three strict-safe documents across two people, with a manifest that matches them."""
    p1, p2 = _person("Ada"), _person("Grace")
    d1 = _doc(p1, f"Ada {_TAG}")
    d2 = _doc(p1, f"Ada {_TAG}", evidence=_evidence(f"Ada {_TAG}", phone=True))   # 3 corroborators
    d3 = _doc(p2, f"Grace {_TAG}")
    ids = sorted([d1, d2, d3])
    plan = [r for r in sso.build_plan() if r["document_id"] in ids]
    assert len(plan) == 3, f"fixture must be strict-safe; got {len(plan)}"
    path, sha = _write_manifest(tmp_path, plan)
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {2: 2, 3: 1})
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 2)
    return {"ids": ids, "plan": plan, "path": path, "sha": sha, "p1": p1, "p2": p2,
            "snapshot_root": tmp_path / "snap"}


def _digest():
    return sso.plan_digest(sso.build_plan())


def _run(batch, **kw):
    kw.setdefault("expect_sha", batch["sha"])
    kw.setdefault("expect_plan_digest", _digest())
    kw.setdefault("expect_rows", len(batch["ids"]))
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["path"], **kw)


def _apply(batch, **kw):
    return _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7, **kw)


# --- the production manifest's own controls -----------------------------------

def test_production_manifest_controls_are_reproduced():
    """The approved digest, row count, people and composition must come out of our own code."""
    src = Path(r"C:\Client360\reports\strict-safe-ownership-20260905-224012")
    if not (src / "strict_safe_ownership_manifest.json").is_file():
        pytest.skip("approved manifest not present on this machine")
    hdr = json.loads((src / "strict_safe_ownership_manifest.json").read_text(encoding="utf-8"))
    docs = hdr["documents"]
    assert sso.plan_digest(docs) == "aef8a82847689621e259f7abe9d3541b921ffbc1a7ea52f2befd10cf498da377"
    census = sso.plan_census(docs)
    assert census == {"rows": 541, "distinct_people": 205,
                      "by_corroborator_count": {2: 437, 3: 104}}
    assert ap.confirm_phrase(541) == "APPLY-STRICT-SAFE-OWNERSHIP-1-541"
    assert rb.confirm_phrase(541) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-1-541"
    # and every approved row satisfies the rule as this codebase states it
    for d in docs:
        assert sso.is_strict_safe(
            {"route": d["route"], "entity_type": "person", "entity_id": d["person_id"]},
            d["evidence"]), d["document_id"]


# --- the service refactor -----------------------------------------------------

def test_resolve_document_ownership_remains_backward_compatible():
    """No conn: still opens its own transaction and commits, exactly as before."""
    pid = _person("Backcompat")
    did = _doc(pid, f"Backcompat {_TAG}")
    result = resolve_document_ownership(did, person_id=pid, actor_user_id=7,
                                        request_id="compat-test")
    assert result["assigned"] is True
    assert _row(did)["person_id"] == pid          # committed without a caller transaction


def test_resolve_document_ownership_dry_run_still_writes_nothing():
    pid = _person("Dry")
    did = _doc(pid, f"Dry {_TAG}")
    result = resolve_document_ownership(did, person_id=pid, dry_run=True)
    assert result["assigned"] is False and result["would_assign"] is True
    assert _row(did)["person_id"] is None


def test_resolve_document_ownership_refuses_owned_and_rejects_unchanged():
    pid, other = _person("Owner"), _person("Other")
    did = _doc(pid, f"Owner {_TAG}", owner=other)
    result = resolve_document_ownership(did, person_id=pid)
    assert result["assigned"] is False and result["reason"] == "already_owned"
    with pytest.raises(ValueError):
        resolve_document_ownership(did)            # no destination at all


def test_conn_parameter_defers_the_commit_to_the_caller():
    """The whole point of the refactor: the caller's transaction owns the write."""
    pid = _person("Deferred")
    did = _doc(pid, f"Deferred {_TAG}")
    conn = engine.connect()
    trans = conn.begin()
    try:
        result = resolve_document_ownership(did, person_id=pid, actor_user_id=7,
                                            request_id="conn-test", conn=conn)
        assert result["assigned"] is True
        assert _row(did)["person_id"] is None      # not visible outside the transaction yet
    finally:
        trans.rollback()
        conn.close()
    assert _row(did)["person_id"] is None          # rolled back with the caller's transaction


def test_audit_rolls_back_with_the_caller_transaction():
    audit = metadata.tables["audit_events"]
    pid = _person("Audit")
    did = _doc(pid, f"Audit {_TAG}")
    rid = f"audit-rollback-{_TAG}"
    conn = engine.connect()
    trans = conn.begin()
    try:
        resolve_document_ownership(did, person_id=pid, actor_user_id=7, request_id=rid, conn=conn)
    finally:
        trans.rollback()
        conn.close()
    with engine.connect() as c:
        rows = c.execute(select(audit.c.id).where(audit.c.request_id == rid)).all()
    assert rows == [], "a rolled-back assignment must not leave a committed audit row"


# --- apply gates ---------------------------------------------------------------

def test_apply_is_read_only_by_default(batch):
    report = _run(batch)
    assert report["committed"] is False and report["applied"] == 0
    assert report["validated"] == 3
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None
    assert not batch["snapshot_root"].exists()


def test_wrong_manifest_sha_aborts(batch):
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha="0" * 64)


def test_wrong_plan_digest_aborts(batch):
    with pytest.raises(SystemExit, match="plan has moved"):
        _run(batch, expect_plan_digest="0" * 64)


def test_wrong_row_count_aborts(batch):
    with pytest.raises(SystemExit, match="rows, approved"):
        _run(batch, expect_rows=99)


def test_duplicate_document_id_aborts(tmp_path, monkeypatch, batch):
    dup_dir = tmp_path / "dup"
    dup_dir.mkdir()
    path, sha = _write_manifest(dup_dir, [batch["plan"][0], batch["plan"][0]])
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {2: 2})
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 1)
    with pytest.raises(SystemExit, match="duplicate document_id"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_digest(), expect_rows=2,
               out=lambda *_a, **_k: None)


def test_composition_mismatch_aborts(batch, monkeypatch):
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {2: 99})
    with pytest.raises(SystemExit, match="composition"):
        _run(batch)


def test_distinct_people_mismatch_aborts(batch, monkeypatch):
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 99)
    with pytest.raises(SystemExit, match="distinct people"):
        _run(batch)


def test_apply_without_confirm_or_actor_aborts(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=7)
    with pytest.raises(SystemExit, match="actor"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3))


# --- drift aborts --------------------------------------------------------------

def _expect_drift(batch, match="no longer validate|plan has moved"):
    with pytest.raises(SystemExit, match=match):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None, "no row may be written when the batch aborts"


def test_missing_person_aborts(batch):
    with engine.begin() as c:
        c.execute(delete(people).where(people.c.id == batch["p2"]))
    _expect_drift(batch)


def test_route_drift_aborts(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["ids"][0]).values(
            fact_value=json.dumps({"route": "MEDIUM", "confidence": "MEDIUM",
                                   "entity_type": "person", "entity_id": batch["p1"],
                                   "entity_name": "x", "evidence": _evidence("x")})))
    _expect_drift(batch)


def test_entity_drift_aborts(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["ids"][0]).values(
            fact_value=json.dumps({"route": "HIGH", "confidence": "HIGH",
                                   "entity_type": "person", "entity_id": batch["p2"],
                                   "entity_name": "x", "evidence": _evidence("x")})))
    _expect_drift(batch)


def test_evidence_drift_aborts(batch):
    """Drop a corroborator so the row no longer clears the two-corroborator bar."""
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["ids"][0]).values(
            fact_value=json.dumps({"route": "HIGH", "confidence": "HIGH",
                                   "entity_type": "person", "entity_id": batch["p1"],
                                   "entity_name": "x",
                                   "evidence": _evidence("x", email=False, address=False)})))
    _expect_drift(batch)


def test_ownership_drift_aborts(batch):
    other = _person("Interloper")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="no longer validate|plan has moved"):
        _apply(batch)
    for did in batch["ids"][1:]:
        assert _row(did)["person_id"] is None


@pytest.mark.parametrize("field,value", [
    ("archived", True), ("status", "deleted"), ("review_status", "pending"),
])
def test_lifecycle_and_review_status_drift_aborts(batch, field, value):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(**{field: value}))
    _expect_drift(batch)


def test_permanent_reject_aborts(batch, monkeypatch):
    """A manifest row that is a permanent V2 reject must never be assigned."""
    monkeypatch.setattr(sso, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({batch["ids"][0]}))
    _expect_drift(batch)


# --- the successful apply ------------------------------------------------------

def test_successful_apply_commits_exact_ownership(batch):
    report = _apply(batch)
    assert report["committed"] is True and report["applied"] == 3 and report["audit_rows"] == 3
    want = {r["document_id"]: r["person_id"] for r in batch["plan"]}
    for did in batch["ids"]:
        row = _row(did)
        assert row["person_id"] == want[did]
        assert row["household_id"] is None and row["organization_id"] is None
        assert row["review_status"] == "not_required"


def test_apply_leaves_every_other_field_and_non_targets_untouched(batch):
    before = {did: dict(_row(did)) for did in batch["ids"]}
    bystander = _doc(batch["p1"], f"Ada {_TAG}", route="MEDIUM")
    bystander_before = dict(_row(bystander))

    _apply(batch)

    for did in batch["ids"]:
        a, b = before[did], _row(did)
        assert b["status"] == a["status"] and b["archived"] == a["archived"]
        assert b["deleted_at"] == a["deleted_at"] and b["sha256"] == a["sha256"]
        assert b["storage_uri"] == a["storage_uri"] and b["original_name"] == a["original_name"]
        assert b["tags"] == a["tags"]
    assert _row(bystander) == bystander_before      # non-target completely unchanged


def test_apply_leaves_sources_and_ocr_untouched(batch):
    ds, ocr = metadata.tables["document_sources"], metadata.tables["document_ocr"]
    with engine.begin() as c:
        for did in batch["ids"]:
            c.execute(ds.insert().values(
                document_id=did, source_system="SharePoint", source_uri=f"sp://{did}",
                source_external_id=f"E{did}", source_hash="f" * 64, available=True, metadata={}))
            c.execute(ocr.insert().values(document_id=did, status="completed", char_count=99))

    def fp():
        with engine.connect() as c:
            return (
                c.execute(text("select md5(string_agg(document_id::text||coalesce(source_hash,''),"
                               "',' order by id)) from document_sources where document_id=any(:i)"),
                          {"i": batch["ids"]}).scalar(),
                c.execute(text("select md5(string_agg(document_id::text||coalesce(status,''),"
                               "',' order by id)) from document_ocr where document_id=any(:i)"),
                          {"i": batch["ids"]}).scalar())

    before = fp()
    _apply(batch)
    assert fp() == before
    with engine.begin() as c:
        c.execute(delete(ocr).where(ocr.c.document_id.in_(batch["ids"])))
        c.execute(delete(ds).where(ds.c.document_id.in_(batch["ids"])))


def test_failure_on_document_n_rolls_back_documents_before_it(batch, monkeypatch):
    """Nothing is left assigned when a later row refuses — rows 1..N-1 roll back with it."""
    real_fn = resolve_document_ownership
    calls = {"n": 0}

    def flaky(document_id, **kw):
        calls["n"] += 1
        if calls["n"] == 3:                       # the LAST of the three rows refuses
            return {"document_id": document_id, "assigned": False, "reason": "no_longer_eligible"}
        return real_fn(document_id, **kw)

    monkeypatch.setattr("app.services.households.resolve_document_ownership", flaky)
    with pytest.raises(RuntimeError, match="was not assigned"):
        _apply(batch)
    assert calls["n"] == 3, "the first two rows must have been attempted before the failure"
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


def test_snapshot_is_written_before_any_write_and_records_prior_state(batch):
    _apply(batch)
    snap_dir = next(Path(batch["snapshot_root"]).glob("strict-safe-ownership-apply-*"))
    rows = list(csv.DictReader((snap_dir / rb.SNAPSHOT_CSV).open(encoding="utf-8")))
    assert len(rows) == 3
    for r in rows:
        assert r["prior_person_id"] == ""            # captured BEFORE the assignment
        assert r["prior_review_status"] == "not_required"
        assert json.loads(r["prior_tags_json"])["source_system"] == "SharePoint"
        assert int(r["destination_person_id"]) in (batch["p1"], batch["p2"])
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    assert meta["snapshot_sha256"] == ap.sha256_of(snap_dir / rb.SNAPSHOT_CSV)


# --- rollback ------------------------------------------------------------------

def _snap_dir(batch):
    return next(Path(batch["snapshot_root"]).glob("strict-safe-ownership-apply-*"))


def test_rollback_restores_exact_prior_state(batch):
    prior = {did: dict(_row(did)) for did in batch["ids"]}
    _apply(batch)
    report = rb.run(_snap_dir(batch), apply_changes=True, confirm=rb.confirm_phrase(3),
                    actor_user_id=7, out=lambda *_a, **_k: None)
    assert report["committed"] is True and report["restored"] == 3
    for did in batch["ids"]:
        now = _row(did)
        assert now["person_id"] == prior[did]["person_id"]
        assert now["household_id"] == prior[did]["household_id"]
        assert now["organization_id"] == prior[did]["organization_id"]
        assert now["review_status"] == prior[did]["review_status"]
        assert now["tags"] == prior[did]["tags"]


def test_rollback_is_read_only_by_default(batch):
    _apply(batch)
    report = rb.run(_snap_dir(batch), out=lambda *_a, **_k: None)
    assert report["committed"] is False and report["restored"] == 0
    assert _row(batch["ids"][0])["person_id"] is not None


def test_rollback_refuses_a_tampered_snapshot(batch):
    _apply(batch)
    snap = _snap_dir(batch) / rb.SNAPSHOT_CSV
    snap.write_text(snap.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has been modified"):
        rb.run(_snap_dir(batch), out=lambda *_a, **_k: None)


def test_rollback_drift_blocks_the_entire_rollback(batch):
    _apply(batch)
    other = _person("Reassigned")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="drifted"):
        rb.run(_snap_dir(batch), apply_changes=True, confirm=rb.confirm_phrase(3),
               actor_user_id=7, out=lambda *_a, **_k: None)
    assert _row(batch["ids"][1])["person_id"] is not None    # untouched, not partially reverted


def test_rollback_leaves_non_targets_untouched(batch):
    bystander = _doc(batch["p1"], f"Ada {_TAG}", route="MEDIUM")
    _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == bystander).values(person_id=batch["p1"]))
    rb.run(_snap_dir(batch), apply_changes=True, confirm=rb.confirm_phrase(3),
           actor_user_id=7, out=lambda *_a, **_k: None)
    assert _row(bystander)["person_id"] == batch["p1"]       # not reverted by this snapshot
