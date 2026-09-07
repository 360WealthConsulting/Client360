"""Strict-safe document tax-year: canonical policy, plan eligibility, and the guarded apply.

Runs against ``client360_test`` (enforced by tests/conftest.py) and cleans up after itself.

The properties that matter here are the ones a pure test cannot reach: that a resolved
``documents.tax_year`` satisfies the canonical year gate WITHOUT being counted as a second signal
alongside the evidence that produced it, that the apply changes exactly the two resolved-state
columns cf01 pairs together, and that every drift the plan is supposed to catch aborts the batch.
"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services import document_tax_year_resolution as plan_mod
from app.services.document_filing_preview import _year_evidence
from scripts import apply_document_tax_year as apply_mod

_TAG = "TaxYearResTest"
folders_table = metadata.tables["document_folders"]

CANDIDATE_COLUMNS = ("document_id", "original_name", "source_path", "folder_year",
                     "extracted_year", "all_years", "verdict", "extraction_rule", "form_family",
                     "evidence_source", "ocr_pre_existing", "char_count", "anomaly_flags", "error")

_PATH_PREFIX = ("/drives/b!test/root:/360 tax solutions, llc/clients/tax preparation/individual/"
                "test client")


@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as connection:
        ids = [r[0] for r in connection.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"file:{_TAG}%")))]
        if ids:
            connection.execute(delete(metadata.tables["document_sources"])
                               .where(metadata.tables["document_sources"]
                                      .c.document_id.in_(ids)))
            connection.execute(delete(documents).where(documents.c.id.in_(ids)))
        connection.execute(delete(people).where(people.c.last_name == _TAG))


def _person() -> int:
    with engine.begin() as connection:
        return connection.execute(people.insert().values(
            first_name="Ada", last_name=_TAG, full_name=f"Ada {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()


@pytest.fixture
def pin_to_fixture(monkeypatch):
    """Let a 3-row fixture satisfy the module pin WITHOUT giving production a bypass.

    The pins are module constants read at call time, so patching them here is enough. ``run()`` and
    the CLI still take no count argument, so nothing outside a test can apply a different number.
    """
    def _pin(csv_path, count):
        monkeypatch.setattr(plan_mod, "EXPECTED_DOCUMENTS", count)
        monkeypatch.setattr(plan_mod, "CANDIDATE_CSV_SHA256", plan_mod.sha256_of(csv_path))
    return _pin


def _document(person_id, *, year, tmp_path, tags=None, archived=False, status="active",
              tax_year=None, tax_year_confidence=None) -> tuple[int, Path]:
    """A live document with a real file on disk, so the plan's content-hash binding is exercised.

    ``tax_year`` and ``tax_year_confidence`` are inserted together because migration cf01 enforces
    ``(tax_year IS NULL) = (tax_year_confidence IS NULL)``.
    """
    if tax_year is not None and tax_year_confidence is None:
        tax_year_confidence = "moderate"
    name = f"{_TAG}-{uuid.uuid4().hex[:8]}.pdf"
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 tax year " + str(year).encode() + b"\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with engine.begin() as connection:
        document_id = connection.execute(documents.insert().values(
            original_name=name, stored_name=f"file:{_TAG}{uuid.uuid4().hex}",
            storage_path=str(path), storage_provider="Client360 Local", storage_uri=str(path),
            size_bytes=path.stat().st_size, sha256=digest, status=status, archived=archived,
            review_status="not_required", current_version=1, person_id=person_id,
            folder_id=None, tags=tags or {}, tax_year=tax_year,
            tax_year_confidence=tax_year_confidence)
            .returning(documents.c.id)).scalar_one()
        connection.execute(metadata.tables["document_sources"].insert().values(
            document_id=document_id, source_system="SharePoint",
            source_path=f"{_PATH_PREFIX}/{year}/{name}",
            source_uri=f"{_PATH_PREFIX}/{year}/{name}",
            source_external_id=uuid.uuid4().hex, available=True))
    return document_id, path


def _add_source(document_id, *, system, path, first=False):
    """Attach another source record. ``first`` forces a LOWER id than the document's existing rows,
    which is what the old ``ORDER BY id LIMIT 1`` would have selected."""
    table = metadata.tables["document_sources"]
    with engine.begin() as connection:
        if first:
            lowest = connection.execute(text(
                "select min(id) from document_sources where document_id = :d"),
                {"d": document_id}).scalar()
            new_id = (lowest or 1) - 1
            connection.execute(text(
                "insert into document_sources (id, document_id, source_system, source_path, "
                "                              source_uri, source_external_id, available) "
                "values (:i, :d, :s, :p, :p, :x, true)"),
                {"i": new_id, "d": document_id, "s": system, "p": path,
                 "x": uuid.uuid4().hex})
            return new_id
        return connection.execute(table.insert().values(
            document_id=document_id, source_system=system, source_path=path, source_uri=path,
            source_external_id=uuid.uuid4().hex, available=True)
            .returning(table.c.id)).scalar_one()


def _plan_for(csv_path, expect_documents):
    with engine.connect() as connection:
        return plan_mod.build_plan(connection, str(csv_path), expect_sha=None,
                                   expect_documents=expect_documents)


def _candidate_csv(tmp_path, rows, name="candidate.csv") -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CANDIDATE_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _row(document_id, year, **overrides) -> dict:
    row = {"document_id": document_id, "original_name": "x.pdf",
           "source_path": f"{_PATH_PREFIX}/{year}", "folder_year": year, "extracted_year": year,
           "all_years": str(year), "verdict": "VERIFIED_FOLDER_YEAR",
           "extraction_rule": "calendar_year", "form_family": "8879",
           "evidence_source": "non_persistent_ocr", "ocr_pre_existing": 0, "char_count": 500,
           "anomaly_flags": "", "error": ""}
    row.update(overrides)
    return row


@pytest.fixture
def batch(tmp_path, pin_to_fixture):
    person_id = _person()
    made = [_document(person_id, year=2023, tmp_path=tmp_path) for _ in range(3)]
    ids = [m[0] for m in made]
    csv_path = _candidate_csv(tmp_path, [_row(i, 2023) for i in ids])
    pin_to_fixture(csv_path, len(ids))
    return {"person": person_id, "ids": ids, "paths": {m[0]: m[1] for m in made},
            "csv": csv_path, "sha": plan_mod.sha256_of(csv_path), "tmp": tmp_path}


def _apply(batch, **kw):
    kw.setdefault("apply_changes", True)
    kw.setdefault("confirm", plan_mod.confirm_phrase(len(batch["ids"])))
    kw.setdefault("actor_user_id", 1)
    kw.setdefault("candidate_sha256", batch["sha"])
    kw.setdefault("report_dir", batch["tmp"] / "out")
    return apply_mod.run(str(batch["csv"]), **kw)


def _tax_years(ids):
    with engine.connect() as connection:
        return {r[0]: r[1] for r in connection.execute(text(
            "select id, tax_year from documents where id = any(:ids)"), {"ids": ids})}


def _tax_pairs(ids):
    """The resolved pair per document — both halves, because cf01 binds them together."""
    with engine.connect() as connection:
        return {r[0]: (r[1], r[2]) for r in connection.execute(text(
            "select id, tax_year, tax_year_confidence from documents where id = any(:ids)"),
            {"ids": ids})}


def _documents_fingerprint(ids):
    with engine.connect() as connection:
        return connection.execute(text(
            "select md5(string_agg(id::text || '|' || coalesce(tags::text,'') || '|' || "
            "coalesce(person_id::text,'') || '|' || coalesce(folder_id::text,'') || '|' || "
            "coalesce(display_name,'') || '|' || coalesce(ocr_status,''), '~' order by id)) "
            "from documents where id = any(:ids)"), {"ids": ids}).scalar()


def _folders_fingerprint():
    with engine.connect() as connection:
        return connection.execute(text(
            "select md5(string_agg(f::text, E'\\n' order by f.id)) from document_folders f")).scalar()


# --- canonical year policy -------------------------------------------------------------------------

def test_a_resolved_tax_year_is_authoritative_and_strong():
    year = _year_evidence({"tax_year": 2023, "tags": {}, "original_name": "scan.pdf"}, [])
    assert year["year"] == 2023
    assert year["confidence"] == "strong"
    assert year["source"] == "resolved"


def test_a_null_resolved_year_preserves_existing_behaviour():
    """One raw signal is still moderate, two still strong, a tag still strong. Nothing moved."""
    one = _year_evidence({"tax_year": None, "tags": {}, "original_name": "2021 return.pdf"}, [])
    assert (one["year"], one["confidence"], one["source"]) == (2021, "moderate", "filename")

    two = _year_evidence({"tax_year": None, "tags": {}, "original_name": "2021 return.pdf"},
                        [{"available": True, "year": 2021}])
    assert (two["year"], two["confidence"]) == (2021, "strong")
    assert two["source"] == "filename+source_path"

    tagged = _year_evidence({"tax_year": None, "tags": {"tax_year": "2020"},
                            "original_name": "scan.pdf"}, [])
    assert (tagged["year"], tagged["confidence"], tagged["source"]) == (2020, "strong", "tag")


def test_a_resolved_year_agreeing_with_the_raw_signals_reports_no_conflict():
    year = _year_evidence({"tax_year": 2021, "tags": {}, "original_name": "2021 return.pdf"},
                         [{"available": True, "year": 2021}])
    assert year["year"] == 2021 and year["confidence"] == "strong"
    assert "resolved_conflicts_with" not in year["evidence"]


def test_a_resolved_year_conflicting_with_a_raw_signal_is_surfaced_not_hidden():
    """The resolution decides, but the disagreement is recorded where a reviewer will see it."""
    year = _year_evidence({"tax_year": 2021, "tags": {"tax_year": "2019"},
                          "original_name": "2020 return.pdf"},
                         [{"available": True, "year": 2022}])
    assert year["year"] == 2021 and year["confidence"] == "strong"
    assert year["evidence"]["resolved_conflicts_with"] == ["filename", "source_path", "tag"]
    # The raw signals are still reported verbatim, not overwritten by the resolution.
    assert year["evidence"]["tag"] == 2019
    assert year["evidence"]["filename"] == 2020
    assert year["evidence"]["source_path"] == 2022


def test_the_resolved_year_is_not_double_counted_as_an_independent_signal():
    """A resolved year plus ONE raw signal must not be reported as two agreeing signals."""
    year = _year_evidence({"tax_year": 2023, "tags": {}, "original_name": "scan.pdf"},
                         [{"available": True, "year": 2023}])
    assert year["source"] == "resolved"
    assert "+" not in year["source"]
    # Without the resolution the same document is only MODERATE — one signal.
    raw = _year_evidence({"tax_year": None, "tags": {}, "original_name": "scan.pdf"},
                        [{"available": True, "year": 2023}])
    assert raw["confidence"] == "moderate"


def test_a_conflicting_raw_pair_without_a_resolution_still_conflicts():
    year = _year_evidence({"tax_year": None, "tags": {}, "original_name": "2020 return.pdf"},
                         [{"available": True, "year": 2022}])
    assert year["year"] is None and year["confidence"] == "conflict"


# --- plan eligibility ------------------------------------------------------------------------------

def test_the_plan_is_built_from_evidence_not_from_document_ids(batch):
    with engine.connect() as connection:
        plan = plan_mod.build_plan(connection, str(batch["csv"]), expect_sha=batch["sha"],
                                   expect_documents=3)
    assert [r["document_id"] for r in plan["documents"]] == sorted(batch["ids"])
    assert all(r["tax_year"] == 2023 for r in plan["documents"])
    assert plan["dropped"] == []
    assert plan_mod.plan_census(plan)["folder_year_equals_tax_year"] is True


def test_a_candidate_whose_tax_year_is_already_set_is_dropped(batch):
    with engine.begin() as connection:
        connection.execute(text("update documents set tax_year = 2019, tax_year_confidence = 'moderate' where id = :i"),
                           {"i": batch["ids"][0]})
    with engine.connect() as connection:
        plan = plan_mod.build_plan(connection, str(batch["csv"]), expect_sha=batch["sha"],
                                   expect_documents=3)
    assert len(plan["documents"]) == 2
    assert plan["dropped"][0]["document_id"] == batch["ids"][0]
    assert "already set" in plan["dropped"][0]["reason"]


def test_a_candidate_whose_file_bytes_changed_is_dropped(batch):
    """The evidence binds to CONTENT. New bytes mean the extracted year was read from something else."""
    batch["paths"][batch["ids"][0]].write_bytes(b"%PDF-1.4 different bytes\n")
    with engine.connect() as connection:
        plan = plan_mod.build_plan(connection, str(batch["csv"]), expect_sha=batch["sha"],
                                   expect_documents=3)
    assert len(plan["documents"]) == 2
    assert "evidence is stale" in plan["dropped"][0]["reason"]


def test_an_archived_or_deleted_candidate_is_dropped(batch):
    with engine.begin() as connection:
        connection.execute(text("update documents set archived = true where id = :i"),
                           {"i": batch["ids"][0]})
        connection.execute(text("update documents set status = 'deleted' where id = :i"),
                           {"i": batch["ids"][1]})
    with engine.connect() as connection:
        plan = plan_mod.build_plan(connection, str(batch["csv"]), expect_sha=batch["sha"],
                                   expect_documents=3)
    assert len(plan["documents"]) == 1


def test_an_unowned_candidate_is_dropped(batch):
    with engine.begin() as connection:
        connection.execute(text("update documents set person_id = null where id = :i"),
                           {"i": batch["ids"][0]})
    with engine.connect() as connection:
        plan = plan_mod.build_plan(connection, str(batch["csv"]), expect_sha=batch["sha"],
                                   expect_documents=3)
    assert any("owner is no longer resolved" in d["reason"] for d in plan["dropped"])


def test_a_candidate_outside_the_provenance_boundary_is_dropped(batch):
    """Still dropped — the three provenance checks are now one, so the reason reads differently."""
    with engine.begin() as connection:
        connection.execute(text(
            "update document_sources set source_path = '/somewhere/else/2023/x.pdf', "
            "       source_uri = '/somewhere/else/2023/x.pdf' where document_id = :i"),
            {"i": batch["ids"][0]})
    with engine.connect() as connection:
        plan = plan_mod.build_plan(connection, str(batch["csv"]), expect_sha=batch["sha"],
                                   expect_documents=3)
    dropped = {d["document_id"]: d["reason"] for d in plan["dropped"]}
    assert batch["ids"][0] in dropped
    assert "no single SharePoint source record" in dropped[batch["ids"][0]]
    assert batch["ids"][0] not in {r["document_id"] for r in plan["documents"]}


def test_the_frozen_candidate_refuses_a_conflicting_row(batch, tmp_path):
    bad = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, extracted_year=2022)], "bad.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="conflict may never be applied"):
        plan_mod.read_frozen_candidates(bad, expect_sha=None, expect_documents=1)


def test_the_frozen_candidate_refuses_an_anomaly_row(batch, tmp_path):
    bad = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023,
                                         anomaly_flags="hash_twin_in_other_year")], "bad.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="anomaly flags"):
        plan_mod.read_frozen_candidates(bad, expect_sha=None, expect_documents=1)


# --- provenance is a property of the DOCUMENT, not of one arbitrary source row --------------------
#
# The production dry run dropped 168 of 756 candidates: 163 because ORDER BY id LIMIT 1 selected a
# TaxDome row while a qualifying SharePoint row existed, and 5 because the lower-id SharePoint row
# named a different year. All 168 were false drops. These pin the corrected semantic and, just as
# importantly, pin what must still FAIL.

def test_a_taxdome_source_with_a_lower_id_does_not_hide_a_qualifying_sharepoint_source(
        tmp_path, pin_to_fixture):
    """The 163-row failure mode: TaxDome sorts first, SharePoint qualifies."""
    person_id = _person()
    document_id, _ = _document(person_id, year=2023, tmp_path=tmp_path)
    _add_source(document_id, system="TaxDome Drive",
                path=r"Z:\Test Client\Client uploaded documents\thing.pdf", first=True)

    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert plan["dropped"] == []
    assert [r["document_id"] for r in plan["documents"]] == [document_id]


def test_an_earlier_sharepoint_source_naming_another_year_does_not_hide_the_approved_one(
        tmp_path, pin_to_fixture):
    """The 5-row failure mode: both sources are SharePoint, the lower id names the wrong year."""
    person_id = _person()
    document_id, _ = _document(person_id, year=2023, tmp_path=tmp_path)
    _add_source(document_id, system="SharePoint",
                path=f"{_PATH_PREFIX}/2019/older-copy.pdf", first=True)

    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert plan["dropped"] == []
    assert [r["document_id"] for r in plan["documents"]] == [document_id]


def test_path_and_year_must_hold_on_the_SAME_source_row(tmp_path, pin_to_fixture):
    """Evidence may not be assembled across rows: an approved path here, the year over there."""
    person_id = _person()
    document_id, _ = _document(person_id, year=2019, tmp_path=tmp_path)   # SharePoint path, 2019
    # A different source carries 2023 but is outside the Tax Preparation boundary.
    _add_source(document_id, system="SharePoint",
                path="/drives/b!test/root:/360 Tax Solutions, LLC/Clients/Bookkeeping/"
                     "Individual/Test Client/2023/thing.pdf")

    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert plan["documents"] == []
    assert "no single SharePoint source record" in plan["dropped"][0]["reason"]


def test_a_document_whose_sources_never_carry_the_approved_year_fails(tmp_path, pin_to_fixture):
    person_id = _person()
    document_id, _ = _document(person_id, year=2019, tmp_path=tmp_path)
    _add_source(document_id, system="SharePoint", path=f"{_PATH_PREFIX}/2018/thing.pdf")

    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert plan["documents"] == []
    assert "no single SharePoint source record" in plan["dropped"][0]["reason"]


def test_the_year_carried_only_by_taxdome_does_not_qualify(tmp_path, pin_to_fixture):
    """TaxDome is outside the approved provenance boundary however good its year looks."""
    person_id = _person()
    document_id, _ = _document(person_id, year=2019, tmp_path=tmp_path)
    _add_source(document_id, system="TaxDome Drive",
                path=r"Z:\Test Client\Clients\Tax Preparation\Individual\Test Client\2023\x.pdf")

    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert plan["documents"] == []


@pytest.mark.parametrize("segment", ["2023x", "x2023", "2023s", "20231", "SMITH 2023", "12023"])
def test_a_year_lookalike_segment_is_not_an_approved_year(tmp_path, pin_to_fixture, segment):
    """The old check was a SUBSTRING test. A year must be a real path SEGMENT."""
    person_id = _person()
    document_id, _ = _document(person_id, year=2019, tmp_path=tmp_path)
    _add_source(document_id, system="SharePoint", path=f"{_PATH_PREFIX}/{segment}/thing.pdf")

    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert plan["documents"] == [], segment


def test_a_single_source_sharepoint_candidate_still_passes(tmp_path, pin_to_fixture):
    person_id = _person()
    document_id, _ = _document(person_id, year=2023, tmp_path=tmp_path)
    csv_path = _candidate_csv(tmp_path, [_row(document_id, 2023)])
    pin_to_fixture(csv_path, 1)
    plan = _plan_for(csv_path, 1)
    assert [r["document_id"] for r in plan["documents"]] == [document_id]


def test_qualifying_source_reuses_the_repository_year_segment_predicate():
    """The year test must be the repo's own, not a private near-copy of it."""
    from app.services.document_filing_preview import segment_year

    assert segment_year("2023") == 2023
    assert segment_year("2023 Receipts") == 2023      # the repo allows a trailing label
    assert segment_year("SMITH 2023") is None
    sources = [{"source_id": 1, "source_system": "SharePoint",
                "source_path": f"{_PATH_PREFIX}/2023 Receipts/x.pdf", "source_uri": None}]
    assert plan_mod.qualifying_source(sources, 2023) is not None
    assert plan_mod.qualifying_source(sources, 2022) is None


def test_the_frozen_candidate_accepts_the_folder_year_verdict_label(batch, tmp_path):
    good = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, verdict="VERIFIED_FOLDER_YEAR")],
                          "folder_label.csv")
    rows = plan_mod.read_frozen_candidates(good, expect_sha=None, expect_documents=1)
    assert [r["verdict"] for r in rows] == ["VERIFIED_FOLDER_YEAR"]


def test_the_frozen_candidate_accepts_the_recovered_lane_verdict_label(batch, tmp_path):
    """The 50 rows recovered from the corrected 1,249 spell the same reviewed fact this way."""
    good = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, verdict="VERIFIED_YEAR")],
                          "recovered_label.csv")
    rows = plan_mod.read_frozen_candidates(good, expect_sha=None, expect_documents=1)
    assert [r["verdict"] for r in rows] == ["VERIFIED_YEAR"]


@pytest.mark.parametrize("verdict", ["CONFLICTING_DOCUMENT_YEAR", "AMBIGUOUS_YEAR",
                                     "NO_YEAR_FOUND", "EXTRACTION_FAILED", "SOURCE_UNAVAILABLE",
                                     "VERIFIED", "verified_year", ""])
def test_the_frozen_candidate_refuses_every_other_verdict(batch, tmp_path, verdict):
    bad = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, verdict=verdict)], "bad.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="carries verdict"):
        plan_mod.read_frozen_candidates(bad, expect_sha=None, expect_documents=1)


def test_the_accepted_verdicts_are_exactly_the_two_reviewed_labels():
    assert plan_mod.ACCEPTED_VERDICTS == frozenset({"VERIFIED_FOLDER_YEAR", "VERIFIED_YEAR"})


def test_a_verdict_label_alone_cannot_admit_a_disagreeing_row(batch, tmp_path):
    """Widening the label vocabulary must not weaken the year-agreement clause."""
    bad = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, verdict="VERIFIED_YEAR",
                                         extracted_year=2022)], "bad.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="conflict may never be applied"):
        plan_mod.read_frozen_candidates(bad, expect_sha=None, expect_documents=1)


def test_a_verdict_label_alone_cannot_admit_an_anomaly_row(batch, tmp_path):
    bad = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, verdict="VERIFIED_YEAR",
                                         anomaly_flags="hash_twin_in_other_year")], "bad.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="anomaly flags"):
        plan_mod.read_frozen_candidates(bad, expect_sha=None, expect_documents=1)


def test_the_frozen_candidate_refuses_a_weak_extraction_rule(batch, tmp_path):
    bad = _candidate_csv(tmp_path, [_row(batch["ids"][0], 2023, extraction_rule="form_header")],
                         "bad.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="not all accepted year rules"):
        plan_mod.read_frozen_candidates(bad, expect_sha=None, expect_documents=1)


def test_the_frozen_candidate_refuses_a_wrong_sha(batch):
    with pytest.raises(plan_mod.TaxYearPlanError, match="not the reviewed file"):
        plan_mod.read_frozen_candidates(batch["csv"], expect_sha="0" * 64, expect_documents=3)


# --- the guarded apply -------------------------------------------------------------------------------

def test_dry_run_writes_nothing(batch):
    before_docs = _documents_fingerprint(batch["ids"])
    report = apply_mod.run(str(batch["csv"]), candidate_sha256=batch["sha"])
    assert report["committed"] is False and report["assigned"] == 0
    assert all(v is None for v in _tax_years(batch["ids"]).values())
    assert _documents_fingerprint(batch["ids"]) == before_docs


def test_apply_sets_the_resolved_pair_and_nothing_else(batch):
    before_docs = _documents_fingerprint(batch["ids"])
    before_folders = _folders_fingerprint()
    report = _apply(batch)
    assert report["committed"] is True
    assert report["assigned"] == 3 and report["audit_rows"] == 3
    # Both halves, together — a year-only write would violate cf01's pairing constraint.
    assert set(_tax_pairs(batch["ids"]).values()) == {(2023, "strong")}
    assert _documents_fingerprint(batch["ids"]) == before_docs   # tags/owner/folder/name untouched
    assert _folders_fingerprint() == before_folders


def test_the_paired_constraint_is_satisfied_after_apply(batch):
    """cf01: (tax_year IS NULL) = (tax_year_confidence IS NULL). Prove no row breaks it."""
    _apply(batch)
    with engine.connect() as connection:
        broken = connection.execute(text(
            "select count(*) from documents "
            " where (tax_year is null) <> (tax_year_confidence is null)")).scalar()
    assert broken == 0


def test_dry_run_writes_neither_field(batch):
    report = apply_mod.run(str(batch["csv"]), candidate_sha256=batch["sha"])
    assert report["committed"] is False
    assert set(_tax_pairs(batch["ids"]).values()) == {(None, None)}


def test_apply_leaves_non_target_confidence_unchanged(batch, tmp_path):
    other_person = _person()
    untouched, _ = _document(other_person, year=2022, tmp_path=tmp_path)
    already, _ = _document(other_person, year=2021, tmp_path=tmp_path,
                           tax_year=2021, tax_year_confidence="moderate")
    _apply(batch)
    pairs = _tax_pairs([untouched, already])
    assert pairs[untouched] == (None, None)
    assert pairs[already] == (2021, "moderate")


def test_the_audit_row_records_the_resolution_under_the_existing_redaction_policy(batch):
    """Every year key is REDACTED, and the provenance that stays observable is intact.

    ``app/security/redaction.py`` redacts any metadata key matching
    ``token|secret|password|tax|ssn|content|body``. Every field naming a tax year matches ``tax``,
    so the audit trail deliberately stores ``[REDACTED]`` for all four. That is firm-wide policy and
    this batch does not get an exemption from it — the values themselves are proved against the
    database by the other tests here, and the rollback snapshot, not the audit row, is the artifact
    that records what was written.

    What the audit row must still carry is the batch's provenance: which plan, which rule, which
    evidence, which bytes.
    """
    _apply(batch)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "select metadata from audit_events where action = :a and entity_id = any(:ids)"),
            {"a": apply_mod.AUDIT_ACTION,
             "ids": [str(i) for i in batch["ids"]]}).scalars().all()
    assert len(rows) == 3
    for metadata_value in rows:
        payload = metadata_value if isinstance(metadata_value, dict) else json.loads(metadata_value)
        # Present, and redacted — the key must exist so the trail shows the field was written.
        for key in ("tax_year", "tax_year_confidence",
                    "previous_tax_year", "previous_tax_year_confidence"):
            assert key in payload, key
            assert payload[key] == "[REDACTED]", key
        # Observable provenance, asserted on its real values.
        assert payload["extraction_rule"] == "calendar_year"
        assert payload["evidence_source"] == "non_persistent_ocr"
        assert payload["form_family"] == "8879"
        assert payload["sha256"] and len(payload["sha256"]) == 64
        assert payload["batch_id"] == plan_mod.BATCH_ID
        assert payload["plan_digest"] and len(payload["plan_digest"]) == 64


def test_the_fixture_pin_does_not_weaken_the_production_pin():
    """The seam is a monkeypatchable constant, not a bypass anyone can reach from outside."""
    import importlib
    import inspect

    fresh = importlib.reload(plan_mod)
    assert fresh.EXPECTED_DOCUMENTS == 756
    assert fresh.CANDIDATE_CSV_SHA256 == (
        "b7a06200acee2d21809ffe7e40977a077ce012be7b958ea52ffd744d0c166a8b")
    # run() exposes no way to state a different count, and neither does the CLI.
    assert "expect_documents" not in inspect.signature(apply_mod.run).parameters
    source = Path(apply_mod.__file__).read_text(encoding="utf-8")
    assert "--expected-documents" not in source
    assert "expect_documents" not in source


def test_apply_does_not_touch_tags(batch):
    with engine.begin() as connection:
        connection.execute(text("update documents set tags = '{\"a\": 1}'::jsonb "
                                " where id = any(:ids)"), {"ids": batch["ids"]})
    _apply(batch)
    with engine.connect() as connection:
        tags = [r[0] for r in connection.execute(text(
            "select tags from documents where id = any(:ids)"), {"ids": batch["ids"]})]
    assert all(t == {"a": 1} for t in tags)


def test_apply_writes_an_audit_row_per_document(batch):
    _apply(batch)
    with engine.connect() as connection:
        count = connection.execute(text(
            "select count(*) from audit_events where action = :a and entity_id = any(:ids)"),
            {"a": apply_mod.AUDIT_ACTION,
             "ids": [str(i) for i in batch["ids"]]}).scalar()
    assert count == 3


def test_apply_leaves_non_target_documents_alone(batch, tmp_path):
    other_person = _person()
    other_id, _ = _document(other_person, year=2022, tmp_path=tmp_path)
    _apply(batch)
    assert _tax_years([other_id])[other_id] is None


def test_apply_refuses_a_document_that_is_not_in_the_frozen_candidate(batch, tmp_path):
    """A row added after freezing changes the file hash, so the pin refuses it."""
    extra_id, _ = _document(batch["person"], year=2023, tmp_path=tmp_path)
    widened = _candidate_csv(tmp_path, [_row(i, 2023) for i in [*batch["ids"], extra_id]],
                             "widened.csv")
    with pytest.raises(plan_mod.TaxYearPlanError, match="not the reviewed file"):
        apply_mod.run(str(widened), candidate_sha256=batch["sha"])


def test_apply_refuses_a_manifest_hash_mismatch(batch):
    with pytest.raises(plan_mod.TaxYearPlanError, match="not the reviewed file"):
        apply_mod.run(str(batch["csv"]), candidate_sha256="0" * 64)


def test_apply_refuses_when_a_candidate_drifted(batch):
    """Any dropped row aborts the whole batch rather than quietly applying the remainder."""
    with engine.begin() as connection:
        connection.execute(text("update documents set tax_year = 2019, tax_year_confidence = 'moderate' where id = :i"),
                           {"i": batch["ids"][0]})
    with pytest.raises(SystemExit, match="no longer satisfy the strict-safe rule"):
        _apply(batch)
    assert _tax_years(batch["ids"])[batch["ids"][1]] is None


def test_apply_refuses_a_wrong_confirmation_or_actor(batch):
    with pytest.raises(SystemExit, match="confirmation phrase"):
        _apply(batch, confirm="APPLY-WRONG-3")
    with pytest.raises(SystemExit, match="not the approved actor"):
        _apply(batch, actor_user_id=7)
    assert all(v is None for v in _tax_years(batch["ids"]).values())


def test_a_second_apply_is_refused_because_the_rows_are_no_longer_eligible(batch):
    """Established semantics: an already-applied row drops out, and a drifted plan aborts."""
    _apply(batch)
    with pytest.raises(SystemExit, match="no longer satisfy the strict-safe rule"):
        _apply(batch, report_dir=batch["tmp"] / "out2")
    assert set(_tax_years(batch["ids"]).values()) == {2023}


def test_the_rollback_snapshot_restores_both_previous_values(batch):
    """The snapshot carries the exact prior PAIR, and restoring it uses those values."""
    report = _apply(batch)
    snapshot = Path(report["snapshot"])
    rows = list(csv.DictReader(snapshot.open(encoding="utf-8")))
    assert len(rows) == 3
    assert all(r["previous_tax_year"] == "" for r in rows)
    assert all(r["previous_tax_year_confidence"] == "" for r in rows)
    assert all(int(r["new_tax_year"]) == 2023 for r in rows)
    assert all(r["new_tax_year_confidence"] == "strong" for r in rows)
    assert set(_tax_pairs(batch["ids"]).values()) == {(2023, "strong")}

    with engine.begin() as connection:
        for row in rows:
            connection.execute(text(
                "update documents set tax_year = :y, tax_year_confidence = :c where id = :i"),
                {"y": int(row["previous_tax_year"]) if row["previous_tax_year"] else None,
                 "c": row["previous_tax_year_confidence"] or None,
                 "i": int(row["document_id"])})
    assert set(_tax_pairs(batch["ids"]).values()) == {(None, None)}


def test_the_whole_cohort_would_pass_the_year_gate_once_resolved(batch):
    """The point of the batch: a resolved year satisfies canonical gate 7."""
    _apply(batch)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "select id, tax_year, tags, original_name from documents where id = any(:ids)"),
            {"ids": batch["ids"]}).mappings()
        for row in rows:
            year = _year_evidence(dict(row), [])
            assert year["confidence"] == "strong"
            assert year["year"] == 2023
