"""Post-OCR document reconciliation (TaxDome exit) — read-only reconcile job.

Proves the job classifies the migrated SharePoint population correctly and NEVER mutates anything:
population reconciliation to an explicit expected count, mutually-exclusive OCR buckets (no double count),
the password_required split, missing-OCR-row → pending, duplicate/cross-owner classification, ownership
classifications, source-reference integrity, completed-with-empty-text search exception, TaxDome
candidates, and deterministic output.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine, households, metadata, people
from app.services.migration.config import MigrationConfig
from app.services.migration.document_reconciliation import (
    DocumentReconciliationJob,
    _bucket_for,
    reconcile_documents,
)
from app.services.ocr_exceptions import ENCRYPTED_PDF_LAST_ERROR

document_sources = metadata.tables["document_sources"]

_TAG = "DOCRECON"


def _cfg(tmp_path) -> MigrationConfig:
    # A config whose report root is a throwaway tmp dir (never writes into the repo's Migration/).
    return MigrationConfig(
        migration_root=Path(tmp_path), wealthbox_export=Path(tmp_path), taxdome_root=Path(tmp_path),
        sharepoint_root=Path(tmp_path), scanner_root=Path(tmp_path), document_root=Path(tmp_path))


@pytest.fixture(autouse=True)
def _cleanup():
    with engine.connect() as c:
        hi_doc = c.execute(select(func.max(documents.c.id))).scalar() or 0
        hi_person = c.execute(select(func.max(people.c.id))).scalar() or 0
    yield
    with engine.begin() as c:
        dids = [r[0] for r in c.execute(select(documents.c.id).where(documents.c.id > hi_doc))]
        if dids:
            c.execute(delete(document_sources).where(document_sources.c.document_id.in_(dids)))
            c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(dids)))
            c.execute(delete(documents).where(documents.c.id.in_(dids)))
        c.execute(delete(people).where(people.c.id > hi_person))


def _person():
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(insert(people).values(full_name=f"P {sfx} {_TAG}", active=True)
                         .returning(people.c.id)).scalar_one()


def _doc(*, name="scan.pdf", sha=None, person_id=None, household_id=None, organization_id=None,
         status="active"):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=f"{_TAG} {name}", stored_name=f"s-{sfx}", storage_provider="SharePoint Online",
            storage_path=f"/x/{sfx}", size_bytes=1, sha256=sha or (sfx + sfx)[:64],
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            status=status, archived=False).returning(documents.c.id)).scalar_one()


def _src(doc_id, *, system="SharePoint", uri=None, external_id=None):
    with engine.begin() as c:
        c.execute(insert(document_sources).values(
            document_id=doc_id, source_system=system, source_uri=uri or f"/sp/{uuid.uuid4().hex}",
            source_external_id=external_id, available=True))


def _ocr(doc_id, *, status, text=None, last_error=None):
    with engine.begin() as c:
        c.execute(insert(document_ocr).values(
            document_id=doc_id, status=status, text=text, char_count=len(text or ""),
            last_error=last_error, attempts=1))


def _run(tmp_path, **kw):
    return reconcile_documents(config=_cfg(tmp_path), **kw)


# --- baseline recovery scope (uploaded_by + created_at window) ---------------

def _batch_doc(*, uploaded_by, created_at, status="active", name="b.pdf"):
    """A document with explicit uploaded_by + created_at, for baseline-scope tests."""
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=f"{_TAG} {name}", stored_name=f"s-{sfx}", storage_provider="SharePoint Online",
            storage_path=f"/x/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64],
            status=status, archived=False, uploaded_by=uploaded_by, created_at=created_at).returning(
            documents.c.id)).scalar_one()


# --- unit: the bucket mapper is total + mutually exclusive --------------------

@pytest.mark.parametrize("st,err,expected", [
    (None, None, "pending"), ("pending", None, "pending"), ("processing", None, "pending"),
    ("completed", None, "completed"), ("failed", "boom", "failed"), ("timed_out", "x", "timed_out"),
    ("skipped", None, "skipped"),
    ("unsupported", ENCRYPTED_PDF_LAST_ERROR, "password_required"),
    ("unsupported", "not an ocr type", "unsupported"),
])
def test_bucket_mapper(st, err, expected):
    assert _bucket_for(st, err) == expected


# --- population reconciliation + invariant -----------------------------------

def test_population_reconciles_to_expected_and_buckets_sum():
    sp = [_doc(name=f"c{i}.pdf") for i in range(5)]
    for d in sp:
        _src(d)
    _ocr(sp[0], status="completed", text="real text")
    _ocr(sp[1], status="failed", last_error="engine boom")
    _ocr(sp[2], status="timed_out", last_error="stalled")
    _ocr(sp[3], status="unsupported", last_error=ENCRYPTED_PDF_LAST_ERROR)   # password_required
    # sp[4] intentionally has NO document_ocr row → pending
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=5)
    c = r.counts
    assert c["scoped_population"] == 5 and c["expected_population"] == 5
    assert c["population_difference"] == 0 and c["invariant_ok"] is True
    assert c["reconciliation_status"] == "PASS"
    assert c["ocr_bucket_total"] == 5                       # buckets reconcile, no double count
    assert c["ocr_completed"] == 1 and c["ocr_failed"] == 1 and c["ocr_timed_out"] == 1
    assert c["ocr_password_required"] == 1 and c["ocr_unsupported"] == 0
    assert c["ocr_pending"] == 1                            # the row with no document_ocr entry


def test_population_mismatch_is_a_failed_invariant():
    d = _doc(); _src(d); _ocr(d, status="completed", text="t")
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=17448)
    assert r.counts["invariant_ok"] is False
    assert r.counts["reconciliation_status"] == "FAILED_POPULATION_MISMATCH"
    assert any(e["category"] == "population_mismatch" for e in r.exceptions)


def test_password_required_split_from_unsupported():
    d_pw = _doc(name="locked.pdf"); _src(d_pw); _ocr(d_pw, status="unsupported",
                                                     last_error=ENCRYPTED_PDF_LAST_ERROR)
    d_un = _doc(name="notes.txt"); _src(d_un); _ocr(d_un, status="unsupported", last_error=None)
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=2)
    assert r.counts["ocr_password_required"] == 1 and r.counts["ocr_unsupported"] == 1
    cats = {e["category"] for e in r.exceptions}
    assert "password_required" in cats and "unsupported" in cats


def test_genuine_unsupported_is_informational_not_operator_review():
    # Genuine unsupported (native-handled types) appears in exceptions.csv but must NOT inflate the
    # actionable operator-review total; password_required DOES count as review work.
    d_un = _doc(name="notes.txt"); _src(d_un); _ocr(d_un, status="unsupported", last_error=None)
    d_pw = _doc(name="locked.pdf"); _src(d_pw); _ocr(d_pw, status="unsupported",
                                                     last_error=ENCRYPTED_PDF_LAST_ERROR)
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=2)
    assert r.counts["informational_exceptions"] == 1                 # the .txt
    assert r.counts["total_operator_review_exceptions"] == len(r.exceptions) - 1
    assert (r.counts["total_operator_review_exceptions"] + r.counts["informational_exceptions"]
            == r.counts["total_exception_rows"] == len(r.exceptions))
    # password_required is actionable and included; unsupported is excluded.
    review_cats = {e["category"] for e in r.exceptions if e["category"] != "unsupported"}
    assert "password_required" in review_cats


# --- ownership ---------------------------------------------------------------

def test_ownership_missing_and_multiple_fields():
    d_missing = _doc(name="noowner.pdf"); _src(d_missing); _ocr(d_missing, status="completed", text="t")
    pid = _person()
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"HH {uuid.uuid4().hex[:6]}")
                        .returning(households.c.id)).scalar_one()
    d_multi = _doc(name="multi.pdf", person_id=pid, household_id=hid)
    _src(d_multi); _ocr(d_multi, status="completed", text="t")
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=2)
    assert r.counts["owner_missing"] == 1 and r.counts["multiple_owner_fields"] == 1
    cats = {e["category"] for e in r.exceptions}
    assert "owner_missing" in cats and "multiple_owner_fields" in cats


def test_owner_proposal_pass_is_bounded_and_reuses_engine(monkeypatch):
    # Bounded deep-pass reuses document_owner_proposal; stub it so the test is deterministic + file-free.
    d = _doc(name="owned.pdf", person_id=_person()); _src(d); _ocr(d, status="completed", text="t")
    import app.services.migration.document_reconciliation as mod
    monkeypatch.setattr("app.services.document_owner_proposal.build_match_indexes", lambda conn: {})
    monkeypatch.setattr("app.services.document_owner_proposal.propose_document_owner",
                        lambda did, **k: {"confidence": "HIGH", "proposed_entity_type": "person",
                                          "proposed_entity_id": -999})   # disagrees with the assigned owner
    r = mod.reconcile_documents(config=_cfg(pytest.importorskip("tempfile").mkdtemp()),
                                expected_population=1, owner_proposal_limit=10)
    assert r.counts["owner_proposal_analyzed"] == 1
    assert r.counts["possible_wrong_owner"] == 1
    assert any(e["category"] == "possible_wrong_owner" for e in r.exceptions)


# --- duplicates --------------------------------------------------------------

def test_duplicate_classification_same_owner_vs_cross_owner():
    sha_same = "aa" * 32
    p1 = _person()
    a = _doc(name="dupA.pdf", sha=sha_same, person_id=p1); _src(a); _ocr(a, status="completed", text="t")
    b = _doc(name="dupB.pdf", sha=sha_same, person_id=p1); _src(b); _ocr(b, status="completed", text="t")
    sha_cross = "bb" * 32
    c1 = _doc(name="xA.pdf", sha=sha_cross, person_id=_person()); _src(c1); _ocr(c1, status="completed", text="t")
    c2 = _doc(name="xB.pdf", sha=sha_cross, person_id=_person()); _src(c2); _ocr(c2, status="completed", text="t")
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=4)
    assert r.counts["duplicate_canonical_groups"] == 1        # same owner
    assert r.counts["cross_owner_duplicate_groups"] == 1      # differing owners
    cats = {e["category"] for e in r.exceptions}
    assert "duplicate_canonical" in cats and "cross_owner_duplicate" in cats


def test_multiple_source_refs_is_reuse_not_a_duplicate():
    d = _doc(name="reuse.pdf"); _src(d, uri="/sp/one"); _src(d, uri="/sp/two")   # two SP refs, one canonical
    _ocr(d, status="completed", text="t")
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=1)
    assert r.counts["documents_with_multiple_sharepoint_refs"] == 1
    assert r.counts["duplicate_canonical_groups"] == 0        # NOT flagged as a duplicate
    assert not any(e["category"] in ("duplicate_canonical", "cross_owner_duplicate") for e in r.exceptions)


# --- source-reference integrity ----------------------------------------------

def test_source_integrity_orphan_and_duplicate_source():
    # orphan_source: a SharePoint ref whose document is soft-deleted (unusable canonically).
    dead = _doc(name="dead.pdf", status="deleted"); _src(dead, uri="/sp/dead")
    # duplicate_source: same external id mapped to TWO distinct canonical documents.
    x = _doc(name="x.pdf"); _src(x, external_id="EXT-1", uri="/sp/x"); _ocr(x, status="completed", text="t")
    y = _doc(name="y.pdf"); _src(y, external_id="EXT-1", uri="/sp/y"); _ocr(y, status="completed", text="t")
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=2)   # dead excluded from scope
    assert r.counts["orphan_source"] >= 1
    assert r.counts["duplicate_source"] >= 1
    cats = {e["category"] for e in r.exceptions}
    assert "orphan_source" in cats and "duplicate_source" in cats
    assert r.counts["scoped_population"] == 2                 # the deleted doc is NOT in the scoped set


# --- searchability -----------------------------------------------------------

def test_completed_with_empty_text_is_search_missing():
    ok = _doc(name="ok.pdf"); _src(ok); _ocr(ok, status="completed", text="has content")
    empty = _doc(name="empty.pdf"); _src(empty); _ocr(empty, status="completed", text="   ")   # whitespace
    none = _doc(name="none.pdf"); _src(none); _ocr(none, status="completed", text=None)
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=3)
    assert r.counts["ocr_completed"] == 3
    assert r.counts["search_searchable"] == 1 and r.counts["search_missing"] == 2
    assert sum(1 for e in r.exceptions if e["category"] == "search_missing") == 2


# --- TaxDome exit ------------------------------------------------------------

def test_taxdome_only_candidate_and_owner_unresolved():
    td = _doc(name="td.pdf"); _src(td, system="TaxDome", uri="/td/1")   # ONLY a TaxDome ref, no owner
    both = _doc(name="both.pdf"); _src(both, system="TaxDome"); _src(both, system="SharePoint")  # not TD-only
    _ocr(both, status="completed", text="t")
    r = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=1)   # only `both` is SharePoint
    assert r.counts["taxdome_only_candidate"] == 1           # `td` (TaxDome only)
    assert r.counts["taxdome_owner_unresolved"] == 1
    cats = {e["category"] for e in r.exceptions}
    assert "taxdome_only_candidate" in cats and "taxdome_owner_unresolved" in cats


# --- read-only + determinism -------------------------------------------------

def test_read_only_no_mutation():
    d = _doc(); _src(d); _ocr(d, status="failed", last_error="x")
    with engine.connect() as c:
        before = (c.execute(select(func.count()).select_from(documents)).scalar(),
                  c.execute(select(func.count()).select_from(document_ocr)).scalar(),
                  c.execute(select(func.count()).select_from(document_sources)).scalar())
    _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=1, owner_proposal_limit=5)
    with engine.connect() as c:
        after = (c.execute(select(func.count()).select_from(documents)).scalar(),
                 c.execute(select(func.count()).select_from(document_ocr)).scalar(),
                 c.execute(select(func.count()).select_from(document_sources)).scalar())
    assert before == after                                    # no rows created/removed by the reconcile


def test_deterministic_counts_and_exception_ordering_across_runs():
    # Mix of failure states so there are several exception rows whose ORDER must be stable (ORDER BY id).
    for i in range(6):
        d = _doc(name=f"d{i}.pdf")
        _src(d)
        _ocr(d, status="failed" if i % 2 else "completed",
             text=None if i % 2 else "t", last_error="boom" if i % 2 else None)
    r1 = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=6)
    r2 = _run(pytest.importorskip("tempfile").mkdtemp(), expected_population=6)
    keys = ("scoped_population", "ocr_completed", "ocr_failed", "ocr_bucket_total", "invariant_ok",
            "total_operator_review_exceptions")
    assert {k: r1.counts[k] for k in keys} == {k: r2.counts[k] for k in keys}
    # Exception rows are emitted in a stable order → byte-identical queues across runs.
    seq1 = [(e["category"], e.get("document_id")) for e in r1.exceptions]
    seq2 = [(e["category"], e.get("document_id")) for e in r2.exceptions]
    assert seq1 == seq2
    # Within a category the document_ids are ascending (proves the ORDER BY id is applied, not DB order).
    failed_ids = [did for cat, did in seq1 if cat == "OCR_failed"]
    assert failed_ids == sorted(failed_ids) and len(failed_ids) == 3


def test_supported_modes_are_reconcile_only():
    from app.services.migration.base import Mode, ModeNotSupported
    job = DocumentReconciliationJob(_cfg(pytest.importorskip("tempfile").mkdtemp()))
    assert job.supported_modes == frozenset({Mode.RECONCILE})
    with pytest.raises(ModeNotSupported):
        job.run(Mode.APPLY)                                   # write modes are refused before any DB access


_TZ = timezone(timedelta(hours=-4))
_START = datetime(2026, 8, 17, 11, 0, 0, tzinfo=_TZ)
_END = datetime(2026, 8, 17, 15, 0, 0, tzinfo=_TZ)
_UPLOADER = "SharePoint Sync"


def test_baseline_scope_selects_exactly_the_intended_records(tmp_path):
    # Exactly the in-window 'SharePoint Sync' non-deleted documents are scoped.
    intended = [_batch_doc(uploaded_by=_UPLOADER, created_at=_START + timedelta(hours=1, minutes=i))
                for i in range(4)]
    r = _run(tmp_path, expected_population=4, baseline_uploaded_by=_UPLOADER,
             baseline_created_from=_START, baseline_created_to=_END)
    c = r.counts
    assert c["scope_mode"] == "baseline"
    assert c["scoped_population"] == 4 and c["population_difference"] == 0
    assert c["invariant_ok"] is True and c["reconciliation_status"] == "PASS"
    assert c["baseline_uploaded_by"] == _UPLOADER
    _ = intended


def test_baseline_scope_excludes_older_newer_other_uploader_and_deleted(tmp_path):
    _batch_doc(uploaded_by=_UPLOADER, created_at=_START + timedelta(hours=1))          # IN (1)
    _batch_doc(uploaded_by=_UPLOADER, created_at=_START - timedelta(minutes=1))        # older → excluded
    _batch_doc(uploaded_by=_UPLOADER, created_at=_END)                                 # >= end (half-open) → excluded
    _batch_doc(uploaded_by=_UPLOADER, created_at=_END + timedelta(hours=1))            # newer → excluded
    _batch_doc(uploaded_by="Manual Upload", created_at=_START + timedelta(hours=1))    # other uploader → excluded
    _batch_doc(uploaded_by=_UPLOADER, created_at=_START + timedelta(hours=1), status="deleted")  # deleted → excluded
    r = _run(tmp_path, expected_population=1, baseline_uploaded_by=_UPLOADER,
             baseline_created_from=_START, baseline_created_to=_END)
    assert r.counts["scoped_population"] == 1 and r.counts["invariant_ok"] is True


def test_baseline_upper_bound_is_half_open(tmp_path):
    # A document created exactly at `created_to` is EXCLUDED (window is [from, to)).
    _batch_doc(uploaded_by=_UPLOADER, created_at=_START + timedelta(hours=1))          # IN
    _batch_doc(uploaded_by=_UPLOADER, created_at=_END)                                 # at boundary → OUT
    r = _run(tmp_path, expected_population=1, baseline_uploaded_by=_UPLOADER,
             baseline_created_from=_START, baseline_created_to=_END)
    assert r.counts["scoped_population"] == 1


def test_generic_source_system_scope_is_broader_than_baseline(tmp_path):
    # The same records, but generic source_system scope also picks up SharePoint-referenced documents that
    # the baseline window would exclude — proving the modes are distinct and generic can be broader.
    in_window = _batch_doc(uploaded_by=_UPLOADER, created_at=_START + timedelta(hours=1))
    older = _batch_doc(uploaded_by=_UPLOADER, created_at=_START - timedelta(days=30))
    other_uploader = _batch_doc(uploaded_by="Manual Upload", created_at=_START + timedelta(hours=1))
    for d in (in_window, older, other_uploader):
        _src(d, system="SharePoint")                     # all three carry a SharePoint source ref
    baseline = _run(tmp_path, baseline_uploaded_by=_UPLOADER,
                    baseline_created_from=_START, baseline_created_to=_END)
    generic = _run(tmp_path, sharepoint_source="SharePoint")
    assert baseline.counts["scope_mode"] == "baseline" and generic.counts["scope_mode"] == "source_system"
    assert baseline.counts["scoped_population"] == 1                      # only the in-window batch
    assert generic.counts["scoped_population"] >= 3                       # broader: all SharePoint-referenced
    assert generic.counts["scoped_population"] > baseline.counts["scoped_population"]


def test_baseline_requires_all_three_parameters(tmp_path):
    with pytest.raises(ValueError):
        _run(tmp_path, baseline_uploaded_by=_UPLOADER, baseline_created_from=_START)   # missing created_to
