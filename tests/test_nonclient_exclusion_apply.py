"""Batch 1 apply/rollback — the gates, the invariants, and the exact reversal.

The apply writes 2,990 rows in production against a manifest a human approved. Every test here
pins one way that could go wrong: a manifest that is not the reviewed one, a corpus that moved
since review, a row that should never have been in the batch, a partial write, or a rollback that
restores something other than exactly what was there.

The manifest is built per-test at the size of the fixture, and the census gate is patched to match,
so the tests exercise the real code path without needing the 2,990-row production manifest.

Temp rows only, all tagged, all cleaned up.
"""
from __future__ import annotations

import builtins
import csv
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata
from app.services import document_nonclient_exclusion as nx
from scripts import apply_nonclient_exclusion as ap
from scripts import rollback_nonclient_exclusion as rb

_TAG = f"NCXA{uuid.uuid4().hex[:6]}"

MANIFEST_COLUMNS = ["document_id", "original_name", "source_system", "source_path",
                    "content_type", "route", "matched_rule", "proposed_classification",
                    "current_owner_state", "current_review_status"]


@pytest.fixture(autouse=True)
def _clean():
    yield
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"ncxa:{_TAG}%")))]
        if ids:
            # audit_events is append-only (enforced by prevent_audit_event_mutation), so the
            # ledger rows these tests write are deliberately left in place — that immutability
            # is the same property the apply relies on.
            c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _doc(name, *, route="UNSUPPORTED", review_status="not_required", tags=None) -> int:
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"ncxa:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local",
            storage_uri=f"/x/{name}", size_bytes=10, sha256=uuid.uuid4().hex * 2,
            status="active", archived=False, review_status=review_status, current_version=1,
            tags=tags if tags is not None else {"source_system": "SharePoint"},
        ).returning(documents.c.id)).scalar_one()
        facts = metadata.tables["document_facts"]
        c.execute(facts.insert().values(
            document_id=did, fact_type="owner_proposal",
            fact_value=json.dumps({"route": route}), confidence=0.0,
            extraction_engine="owner_proposal", extractor_version="test",
            version=1, is_current=True))
    return did


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.review_status, documents.c.tags,
                                documents.c.person_id, documents.c.status,
                                documents.c.archived, documents.c.sha256,
                                documents.c.storage_uri, documents.c.deleted_at)
                         .where(documents.c.id == did)).mappings().one()


def _write_manifest(tmp_path, rows) -> tuple[Path, str]:
    """rows: [(document_id, original_name, matched_rule)] -> (csv path, sha256)."""
    path = tmp_path / "nonclient_exclusion_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        w.writeheader()
        for did, name, rule in rows:
            w.writerow({"document_id": did, "original_name": name, "source_system": "SharePoint",
                        "source_path": f"/p/{name}", "content_type": "",
                        "route": "UNSUPPORTED", "matched_rule": rule,
                        "proposed_classification": nx.EXCLUDED_REVIEW_STATUS,
                        "current_owner_state": "unowned", "current_review_status": "not_required"})
    return path, ap.sha256_of(path)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Three documents, one per rule family, with a matching manifest and patched census."""
    a = _doc("vendor.css")
    b = _doc("uswds.min.js.download")
    c = _doc("theme.ttf")
    rows = [(a, "vendor.css", nx.REASON_TECHNICAL_EXTENSION),
            (b, "uswds.min.js.download", nx.REASON_WEB_ASSET_DOWNLOAD),
            (c, "theme.ttf", nx.REASON_TECHNICAL_EXTENSION)]
    path, sha = _write_manifest(tmp_path, rows)
    monkeypatch.setattr(ap, "EXPECTED_CENSUS",
                        {nx.REASON_TECHNICAL_EXTENSION: 2, nx.REASON_WEB_ASSET_DOWNLOAD: 1})
    return {"ids": sorted([a, b, c]), "rows": rows, "path": path, "sha": sha,
            "snapshot_root": tmp_path / "snap"}


def _live_digest():
    from scripts.preview_nonclient_exclusion import collect, digest
    with engine.connect() as conn:
        plan, _ = collect(conn)
    return digest(plan)


def _run(batch, **kw):
    kw.setdefault("expect_sha", batch["sha"])
    kw.setdefault("expect_plan_digest", _live_digest())
    kw.setdefault("expect_rows", len(batch["rows"]))
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["path"], **kw)


# --- the apply defaults to no write -------------------------------------------

def test_apply_writes_nothing_by_default(batch):
    report = _run(batch)
    assert report["committed"] is False and report["applied"] == 0
    assert report["validated"] == len(batch["rows"])
    for did in batch["ids"]:
        assert _row(did)["review_status"] == "not_required"
    assert not batch["snapshot_root"].exists()      # dry run takes no snapshot


# --- manifest gates ------------------------------------------------------------

def test_wrong_manifest_sha_is_refused(batch):
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha="0" * 64)


def test_wrong_plan_digest_is_refused(batch):
    with pytest.raises(SystemExit, match="corpus has moved"):
        _run(batch, expect_plan_digest="0" * 64)


def test_wrong_row_count_is_refused(batch):
    with pytest.raises(SystemExit, match="rows, approved"):
        _run(batch, expect_rows=len(batch["rows"]) + 1)


def test_duplicate_document_id_is_refused(tmp_path, monkeypatch):
    did = _doc("dup.css")
    rows = [(did, "dup.css", nx.REASON_TECHNICAL_EXTENSION)] * 2
    path, sha = _write_manifest(tmp_path, rows)
    monkeypatch.setattr(ap, "EXPECTED_CENSUS", {nx.REASON_TECHNICAL_EXTENSION: 2})
    with pytest.raises(SystemExit, match="duplicate document_id"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_live_digest(), expect_rows=2,
               out=lambda *_a, **_k: None)


def test_reason_mismatch_between_manifest_and_engine_is_refused(tmp_path, monkeypatch):
    """The manifest says web_asset_download; the file is really a technical extension."""
    did = _doc("vendor.css")
    rows = [(did, "vendor.css", nx.REASON_WEB_ASSET_DOWNLOAD)]
    path, sha = _write_manifest(tmp_path, rows)
    monkeypatch.setattr(ap, "EXPECTED_CENSUS", {nx.REASON_WEB_ASSET_DOWNLOAD: 1})
    with pytest.raises(SystemExit, match="rule drift"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_live_digest(), expect_rows=1,
               out=lambda *_a, **_k: None)


def test_held_back_row_injected_into_the_manifest_is_refused(tmp_path, monkeypatch):
    """A .zip is one of the 264 held back — the engine must refuse it even if a manifest lists it."""
    did = _doc("Apr 12, 2019 to May 10, 2019.zip")
    rows = [(did, "Apr 12, 2019 to May 10, 2019.zip", nx.REASON_TECHNICAL_EXTENSION)]
    path, sha = _write_manifest(tmp_path, rows)
    monkeypatch.setattr(ap, "EXPECTED_CENSUS", {nx.REASON_TECHNICAL_EXTENSION: 1})
    with pytest.raises(SystemExit, match="not_an_approved_artifact|no longer validate"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_live_digest(), expect_rows=1,
               out=lambda *_a, **_k: None)
    assert _row(did)["review_status"] == "not_required"


def test_unapproved_rule_in_the_manifest_is_refused(tmp_path, monkeypatch):
    did = _doc("thing.css")
    rows = [(did, "thing.css", "because_i_said_so")]
    path, sha = _write_manifest(tmp_path, rows)
    monkeypatch.setattr(ap, "EXPECTED_CENSUS", {"because_i_said_so": 1})
    with pytest.raises(SystemExit, match="unapproved rule"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_live_digest(), expect_rows=1,
               out=lambda *_a, **_k: None)


def test_census_mismatch_is_refused(batch, monkeypatch):
    monkeypatch.setattr(ap, "EXPECTED_CENSUS", {nx.REASON_TECHNICAL_EXTENSION: 99})
    with pytest.raises(SystemExit, match="census"):
        _run(batch)


def test_eligibility_drift_between_review_and_apply_is_refused(batch):
    """A row that gained an owner after review aborts the whole batch."""
    from app.db import people
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Owner", last_name=_TAG, full_name=f"Owner {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=pid))
    try:
        with pytest.raises(SystemExit, match="no longer validate|corpus has moved"):
            _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=1)
        for did in batch["ids"][1:]:
            assert _row(did)["review_status"] == "not_required"   # nothing partially applied
    finally:
        with engine.begin() as c:
            c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                      .values(person_id=None))
            c.execute(delete(people).where(people.c.id == pid))


# --- confirmation and actor ----------------------------------------------------

def test_apply_without_confirm_phrase_is_refused(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=1)


def test_apply_without_actor_is_refused(batch):
    with pytest.raises(SystemExit, match="actor"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3))


def test_the_production_confirm_phrase_is_the_documented_one():
    assert ap.confirm_phrase(2990) == "APPLY-NONCLIENT-BATCH1-2990"
    assert rb.confirm_phrase(2990) == "ROLLBACK-NONCLIENT-BATCH1-2990"


# --- the successful apply ------------------------------------------------------

def test_successful_apply_commits_exactly_the_manifest(batch):
    report = _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    assert report["committed"] is True
    assert report["applied"] == 3
    assert report["audit_rows"] == 3
    for did in batch["ids"]:
        r = _row(did)
        assert r["review_status"] == nx.EXCLUDED_REVIEW_STATUS
        assert r["tags"][nx.TAGS_KEY]["excluded_by_user_id"] == 7


def test_apply_leaves_ownership_archive_status_and_provenance_untouched(batch):
    before = {did: dict(_row(did)) for did in batch["ids"]}
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    for did in batch["ids"]:
        a, b = before[did], _row(did)
        assert b["person_id"] is None and a["person_id"] is None
        assert b["status"] == a["status"] == "active"
        assert b["archived"] is False and a["archived"] is False
        assert b["deleted_at"] is None and a["deleted_at"] is None
        assert b["sha256"] == a["sha256"]
        assert b["storage_uri"] == a["storage_uri"]


def test_apply_leaves_source_links_and_ocr_rows_untouched(batch):
    ids = batch["ids"]
    ds = metadata.tables["document_sources"]
    ocr = metadata.tables["document_ocr"]
    with engine.begin() as c:
        for did in ids:
            c.execute(ds.insert().values(
                document_id=did, source_system="SharePoint", source_uri=f"sp://{did}",
                source_external_id=f"EXT{did}", source_hash="f" * 64, available=True, metadata={}))
            c.execute(ocr.insert().values(document_id=did, status="unsupported", char_count=0))

    def fp():
        with engine.connect() as c:
            s = c.execute(text(
                "select md5(string_agg(document_id::text||coalesce(source_hash,''), ',' "
                "order by id)) from document_sources where document_id = any(:i)"),
                {"i": ids}).scalar()
            o = c.execute(text(
                "select md5(string_agg(document_id::text||coalesce(status,''), ',' order by id)) "
                "from document_ocr where document_id = any(:i)"), {"i": ids}).scalar()
        return s, o

    before = fp()
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    assert fp() == before
    with engine.begin() as c:
        c.execute(delete(ocr).where(ocr.c.document_id.in_(ids)))
        c.execute(delete(ds).where(ds.c.document_id.in_(ids)))


def test_audit_rows_are_exact_and_transactionally_coupled(batch):
    audit = metadata.tables["audit_events"]
    report = _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    with engine.connect() as c:
        classified = c.execute(select(documents.c.id).where(
            documents.c.id.in_(batch["ids"]),
            documents.c.review_status == nx.EXCLUDED_REVIEW_STATUS)).all()
        rows = c.execute(select(audit.c.id).where(
            audit.c.action == "document.nonclient_excluded",
            audit.c.entity_id.in_([str(i) for i in batch["ids"]]))).all()
    assert report["audit_rows"] == 3
    assert len(rows) == 3 and len(classified) == 3   # changes and audit committed together


def test_repeated_apply_is_refused(batch):
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    with pytest.raises(SystemExit, match="already been applied"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)


def test_mid_batch_failure_rolls_the_whole_transaction_back(batch, monkeypatch):
    """If any row refuses after writes began, nothing is left classified."""
    real = nx.exclude_document
    calls = {"n": 0}

    def flaky(document_id, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"document_id": document_id, "excluded": False, "outcome": "no_longer_eligible",
                    "reason": None, "dry_run": False, "route": None}
        return real(document_id, **kw)

    monkeypatch.setattr(nx, "exclude_document", flaky)
    with pytest.raises(RuntimeError, match="refused mid-batch"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    for did in batch["ids"]:
        assert _row(did)["review_status"] == "not_required"


def test_apply_writes_no_file_outside_the_snapshot_directory(batch):
    """The apply path touches the database and its own snapshot — never document storage."""
    written = []
    real_open, real_path_open, real_write_text = builtins.open, Path.open, Path.write_text

    def _is_write(mode):
        return any(m in str(mode) for m in ("w", "a", "x", "+"))

    def spy_open(file, mode="r", *a, **kw):
        if _is_write(mode):
            written.append(str(file))
        return real_open(file, mode, *a, **kw)

    def spy_path_open(self, mode="r", *a, **kw):
        if _is_write(mode):
            written.append(str(self))
        return real_path_open(self, mode, *a, **kw)

    def spy_write_text(self, *a, **kw):
        written.append(str(self))
        return real_write_text(self, *a, **kw)

    builtins.open, Path.open, Path.write_text = spy_open, spy_path_open, spy_write_text
    try:
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    finally:
        builtins.open, Path.open, Path.write_text = real_open, real_path_open, real_write_text

    root = str(batch["snapshot_root"].resolve())
    assert written, "the snapshot itself must be written"
    for p in written:
        assert str(Path(p).resolve()).startswith(root), f"wrote outside the snapshot dir: {p}"


# --- rollback ------------------------------------------------------------------

def _snapshot_dir(batch):
    return next(Path(batch["snapshot_root"]).glob("nonclient-apply-*"))


def test_rollback_restores_exact_prior_state_and_exact_tags(batch):
    prior = {did: dict(_row(did)) for did in batch["ids"]}
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)

    report = rb.run(_snapshot_dir(batch), apply_changes=True,
                    confirm=rb.confirm_phrase(3), actor_user_id=7, out=lambda *_a, **_k: None)

    assert report["committed"] is True and report["restored"] == 3
    for did in batch["ids"]:
        now = _row(did)
        assert now["review_status"] == prior[did]["review_status"]
        assert now["tags"] == prior[did]["tags"]          # exact, not merely key-removed


def test_rollback_defaults_to_no_write(batch):
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    report = rb.run(_snapshot_dir(batch), out=lambda *_a, **_k: None)
    assert report["committed"] is False and report["restored"] == 0
    for did in batch["ids"]:
        assert _row(did)["review_status"] == nx.EXCLUDED_REVIEW_STATUS


def test_rollback_is_scoped_to_the_snapshot_ids_only(batch):
    """A document classified outside this batch must survive the rollback untouched."""
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    other = _doc("other.hlp")
    nx.exclude_document(other, actor_user_id=1)

    rb.run(_snapshot_dir(batch), apply_changes=True, confirm=rb.confirm_phrase(3),
           actor_user_id=7, out=lambda *_a, **_k: None)

    assert _row(other)["review_status"] == nx.EXCLUDED_REVIEW_STATUS   # NOT reversed
    for did in batch["ids"]:
        assert _row(did)["review_status"] == "not_required"


def test_rollback_refuses_a_tampered_snapshot(batch):
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    snap = _snapshot_dir(batch) / rb.SNAPSHOT_CSV
    snap.write_text(snap.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has been modified"):
        rb.run(_snapshot_dir(batch), out=lambda *_a, **_k: None)


def test_rollback_refuses_when_a_row_has_drifted(batch):
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    nx.restore_document(batch["ids"][0], actor_user_id=1)     # someone reversed one by hand
    with pytest.raises(SystemExit, match="drifted"):
        rb.run(_snapshot_dir(batch), apply_changes=True, confirm=rb.confirm_phrase(3),
               actor_user_id=7, out=lambda *_a, **_k: None)
    assert _row(batch["ids"][1])["review_status"] == nx.EXCLUDED_REVIEW_STATUS  # untouched


def test_rollback_without_confirm_is_refused(batch):
    _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7)
    with pytest.raises(SystemExit, match="--confirm"):
        rb.run(_snapshot_dir(batch), apply_changes=True, actor_user_id=7,
               out=lambda *_a, **_k: None)


def test_snapshot_records_exact_prior_tags_including_nested_objects(tmp_path, monkeypatch):
    """The snapshot is the rollback's only source of truth, so it must capture tags verbatim."""
    custom = {"source_system": "SharePoint", "taxdome_folder": "X", "nested": {"a": 1, "b": [2, 3]}}
    did = _doc("bespoke.hlp", tags=custom)
    rows = [(did, "bespoke.hlp", nx.REASON_TECHNICAL_EXTENSION)]
    path, sha = _write_manifest(tmp_path, rows)
    monkeypatch.setattr(ap, "EXPECTED_CENSUS", {nx.REASON_TECHNICAL_EXTENSION: 1})
    snapshot_root = tmp_path / "snap"

    ap.run(path, expect_sha=sha, expect_plan_digest=_live_digest(), expect_rows=1,
           apply_changes=True, confirm=ap.confirm_phrase(1), actor_user_id=7,
           snapshot_root=snapshot_root, out=lambda *_a, **_k: None)

    snap_csv = next(snapshot_root.glob("nonclient-apply-*")) / rb.SNAPSHOT_CSV
    recorded = list(csv.DictReader(snap_csv.open(encoding="utf-8")))[0]
    assert json.loads(recorded["prev_tags_json"]) == custom
    assert recorded["prev_review_status"] == "not_required"

    rb.run(snap_csv.parent, apply_changes=True, confirm=rb.confirm_phrase(1),
           actor_user_id=7, out=lambda *_a, **_k: None)
    assert _row(did)["tags"] == custom          # nested structure restored exactly
