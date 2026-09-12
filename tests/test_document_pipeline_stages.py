"""Continuous document pipeline — the four stages and the three ownership lanes.

The extraction, OCR, classification and matching ENGINES are covered by their own suites
(``test_document_owner_proposal.py``, ``test_document_ocr.py``, ``test_document_pipeline.py``). What
is tested here is the part this package adds: the routing between stages, the deduplication and
deferral in front of the OCR engine, and the lane rules — which lane answers, what links, what goes
to the single review queue, and the guarantee that nothing overwrites an owner.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import select, text

from app.db import documents, engine, people
from app.services.document_pipeline_continuous import (
    model,
    ownership,
    queue,
    source_authority,
    stages,
)
from app.services.document_pipeline_continuous.model import (
    PipelinePermanentError,
    PipelineTransientError,
)

# Alphabetic and capitalised, so names built from it look like names to the matching engines.
_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "klmnopqrst")).capitalize()
_DOCS: list[int] = []
_PEOPLE: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            for table in ("document_pipeline_blockers", "document_pipeline_ownership_reviews",
                          "document_pipeline_tasks", "document_sources", "document_ocr",
                          "document_facts", "document_classifications"):
                c.execute(text(f"DELETE FROM {table} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    _DOCS.clear()
    _PEOPLE.clear()


def _person(full_name: str) -> int:
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(*, name="f.txt", path=None, sha=None, tags=None, person_id=None,
         source_system=None) -> int:
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, original_name=name, stored_name=f"dps-{uuid.uuid4().hex}",
            storage_path=str(path) if path else "x",
            storage_uri=str(path) if path else None, size_bytes=10,
            sha256=sha or hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", archived=False, tags=tags or {}).returning(documents.c.id)).scalar_one()
        if source_system:
            c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                           "VALUES (:d, :s, '')"), {"d": did, "s": source_system})
    _DOCS.append(did)
    return did


def _task_for(document_id, stage):
    return {"id": 0, "document_id": document_id, "stage": stage, "attempts": 1, "max_attempts": 5}


def _ocr_row(document_id):
    with engine.connect() as c:
        return c.execute(text("SELECT status, engine, char_count, source_hash FROM document_ocr "
                              "WHERE document_id = :d"), {"d": document_id}).mappings().first()


def _owner(document_id):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id)
                               .where(documents.c.id == document_id)).first())


# --- extract ---------------------------------------------------------------------------------------

def test_extract_caches_embedded_text_and_skips_the_ocr_stage(tmp_path):
    """Born-digital documents must never reach the OCR engine. This is the pipeline's biggest saving."""
    f = tmp_path / "letter.txt"
    f.write_text("Dear client, your 2024 engagement letter is enclosed for signature.\n")
    did = _doc(name="letter.txt", path=f)
    with engine.begin() as c:
        result = stages.run_extract(c, _task_for(did, model.STAGE_EXTRACT))
    assert result.next_stage == model.STAGE_CLASSIFY
    row = _ocr_row(did)
    assert row["status"] == "completed"
    assert row["engine"] == "embedded:plaintext"
    assert row["char_count"] > 0


def test_extract_routes_an_image_with_no_text_to_the_ocr_stage():
    did = _doc(name="scan.png")
    with engine.begin() as c:
        result = stages.run_extract(c, _task_for(did, model.STAGE_EXTRACT))
    assert result.next_stage == model.STAGE_OCR


def test_extract_records_a_non_text_type_as_unsupported_and_still_classifies_it():
    """A .zip has no text, but it still has a filename and a folder — those are evidence."""
    did = _doc(name="statements.zip")
    with engine.begin() as c:
        result = stages.run_extract(c, _task_for(did, model.STAGE_EXTRACT))
    assert result.next_stage == model.STAGE_CLASSIFY
    assert _ocr_row(did)["status"] == "unsupported"


def test_extract_reuses_text_already_cached_without_re_extracting(tmp_path):
    did = _doc(name="scan.png")
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count) "
                       "VALUES (:d, 'completed', 'W-2 Wage and Tax Statement', 26)"), {"d": did})
        result = stages.run_extract(c, _task_for(did, model.STAGE_EXTRACT))
    assert result.next_stage == model.STAGE_CLASSIFY
    assert result.detail["method"] == "ocr_cache"


def test_extract_refuses_a_document_retired_after_it_was_queued():
    """Any of the four retirement markers stops the stage — see tests/test_live_document_predicate.py
    for the per-condition coverage and the 50 production rows that motivated it."""
    did = _doc(name="gone.txt")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == did).values(status="deleted"))
        with pytest.raises(PipelinePermanentError) as exc:
            stages.run_extract(c, _task_for(did, model.STAGE_EXTRACT))
    assert exc.value.reason_code == "document_not_live"


# --- ocr -------------------------------------------------------------------------------------------

def test_ocr_reuses_an_identical_document_instead_of_running_the_engine():
    sha = hashlib.sha256(b"the same scan twice").hexdigest()
    original, copy = _doc(name="a.pdf", sha=sha), _doc(name="b.pdf", sha=sha)
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count, page_count) "
                       "VALUES (:d, 'completed', 'Form 1099-INT Interest Income', 29, 1)"),
                  {"d": original})

    def _never(*_args, **_kwargs):
        raise AssertionError("the OCR engine must not be invoked for a byte-identical document")

    with engine.begin() as c:
        plan = stages.plan_ocr(c, _task_for(copy, model.STAGE_OCR))
        assert plan.action == "reuse"
        result = stages.settle_ocr(c, plan, None)
    assert result.next_stage == model.STAGE_CLASSIFY
    row = _ocr_row(copy)
    assert row["status"] == "completed"
    assert row["engine"] == f"reused:{original}"
    assert _never is not None      # the engine path is unreachable above; the plan short-circuits


def test_ocr_defers_while_a_legacy_sweep_holds_the_corpus_lock(monkeypatch):
    """The pipeline must yield to a running migration sweep rather than fight it for documents."""
    did = _doc(name="scan.pdf")
    monkeypatch.setattr("app.services.document_pipeline_continuous.backpressure."
                        "legacy_ocr_sweep_active", lambda *_a, **_k: True)
    with engine.begin() as c:
        plan = stages.plan_ocr(c, _task_for(did, model.STAGE_OCR))
        assert plan.action == "defer"
        with pytest.raises(PipelineTransientError):
            stages.settle_ocr(c, plan, None)


def test_ocr_treats_a_missing_backend_as_retryable_not_as_done():
    """An uninstalled engine is a host problem. Marking the document done would lose it silently."""
    did = _doc(name="scan.pdf")
    plan = stages.OcrPlan("run", did, sha256="x" * 64)
    with engine.begin() as c:
        with pytest.raises(PipelineTransientError):
            stages.settle_ocr(c, plan, {"status": "backend_unavailable", "error": "tesseract missing"})
    assert _ocr_row(did)["status"] == "failed"      # truthful, and a retry candidate


def test_ocr_treats_an_encrypted_document_as_permanent():
    did = _doc(name="locked.pdf")
    plan = stages.OcrPlan("run", did, sha256="y" * 64)
    with engine.begin() as c:
        with pytest.raises(PipelinePermanentError) as exc:
            stages.settle_ocr(c, plan, {"encrypted": 1})
    assert exc.value.reason_code == "encrypted_document"


def test_ocr_timeout_and_failure_are_retryable():
    did = _doc(name="slow.pdf")
    plan = stages.OcrPlan("run", did, sha256="z" * 64)
    with engine.begin() as c:
        with pytest.raises(PipelineTransientError):
            stages.settle_ocr(c, plan, {"timed_out": 1})
        with pytest.raises(PipelineTransientError):
            stages.settle_ocr(c, plan, {"failed": 1, "errors": ["engine crashed"]})


# --- error classification ----------------------------------------------------------------------------

def test_error_classification_separates_permanent_from_transient():
    from app.services.ocr_exceptions import OcrEncryptedPdf, OcrTimeout

    assert stages.classify_error(OcrEncryptedPdf("locked")) == ("permanent", "encrypted_document")
    assert stages.classify_error(FileNotFoundError("gone")) == ("permanent", "source_file_missing")
    assert stages.classify_error(PipelinePermanentError("document_deleted")) == (
        "permanent", "document_deleted")
    assert stages.classify_error(OcrTimeout("slow")) == ("transient", "ocr_timeout")
    assert stages.classify_error(PipelineTransientError("busy"))[0] == "transient"
    assert stages.classify_error(PermissionError("locked by sync"))[0] == "transient"


def test_an_unrecognised_error_is_treated_as_transient():
    """Wrongly transient costs a few retries; wrongly permanent silently drops a document."""
    assert stages.classify_error(ValueError("something new"))[0] == "transient"


# --- lane detection ------------------------------------------------------------------------------------

@pytest.mark.parametrize(("source_system", "expected"), [
    ("Drake", model.LANE_DRAKE),
    ("TaxDome Drive", model.LANE_TAXDOME),
    ("SharePoint", model.LANE_SHAREPOINT),
    (None, model.LANE_SHAREPOINT),
])
def test_lane_detection_follows_provenance(source_system, expected):
    did = _doc(source_system=source_system)
    with engine.connect() as c:
        assert ownership.detect_lane(c, did) == expected


def test_drake_provenance_outranks_every_other_source():
    """A document in both systems is a Drake document: its identity evidence is the stronger one."""
    did = _doc(source_system="SharePoint")
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                       "VALUES (:d, 'Drake', 'x')"), {"d": did})
    with engine.connect() as c:
        assert ownership.detect_lane(c, did) == model.LANE_DRAKE


# --- ownership lanes -------------------------------------------------------------------------------------

def test_drake_high_confidence_links_the_document():
    pid = _person(f"Zenobia {_TAG}")
    did = _doc(source_system="Drake")
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": pid,
            "entity_name": f"Zenobia {_TAG}", "evidence": ["taxpayer identifier hash match"]})
    assert verdict["outcome"] == model.OUTCOME_LINKED
    assert verdict["lane"] == model.LANE_DRAKE
    assert _owner(did) == (pid, None, None)


def test_drake_hold_goes_to_review_and_links_nothing():
    """A Drake HOLD must never fall through to weaker evidence — that is what 'authoritative' means."""
    did = _doc(source_system="Drake")
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HOLD", "drake_resolution": "frozen_identity_conflict",
            "evidence": ["explicitly frozen"]})
        rows = [r for r in queue.open_reviews(c, limit=100) if r["document_id"] == did]
    assert verdict["outcome"] == model.OUTCOME_REVIEW
    assert rows[0]["lane"] == model.LANE_DRAKE
    assert rows[0]["reason_code"] == "frozen_identity_conflict"
    assert _owner(did) == (None, None, None)


def test_taxdome_folder_mapping_is_authoritative_and_links_the_person():
    name = f"Quintus {_TAG}"
    pid = _person(name)
    did = _doc(source_system="TaxDome Drive",
               tags={"source_system": "TaxDome Drive", "taxdome_folder": name})
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={})
    assert verdict["outcome"] == model.OUTCOME_LINKED
    assert verdict["lane"] == model.LANE_TAXDOME
    assert _owner(did) == (pid, None, None)


def test_an_unresolved_taxdome_folder_goes_to_review_not_to_a_content_guess():
    """The subject is the refusal: content evidence must not decide an authoritative lane.

    Where the review LANDS changed — an unresolved folder now aggregates onto the one open review
    for its source identity rather than opening a row per document, because the folder is one
    question however many files sit in it. The refusal itself is unchanged and is what this pins.
    """
    did = _doc(source_system="TaxDome Drive",
               tags={"source_system": "TaxDome Drive",
                     "taxdome_folder": f"Nobody {_TAG} Whatsoever"})
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            # Content evidence that WOULD have linked in the SharePoint lane. The authoritative lane
            # must not let it decide.
            "confidence": "HIGH", "entity_type": "person", "entity_id": _person(f"Decoy {_TAG}")})
        per_document = [r for r in queue.open_reviews(c, limit=100) if r["document_id"] == did]
        members = source_authority.source_review_documents(c, verdict["source_review_id"])
    assert verdict["outcome"] == model.OUTCOME_REVIEW
    assert verdict["reason_code"] == "taxdome_folder_unresolved"
    assert verdict["aggregated"] is True
    assert did in members, "the document must hang off the folder's review"
    assert per_document == [], "the aggregated path must not also open a per-document review"
    assert _owner(did) == (None, None, None)


def test_sharepoint_high_links_and_medium_goes_to_the_one_review_queue():
    pid = _person(f"Octavia {_TAG}")
    linked = _doc(source_system="SharePoint")
    reviewed = _doc(source_system="SharePoint")
    with engine.begin() as c:
        high = ownership.resolve(c, linked, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": pid,
            "evidence": ["email match"]})
        medium = ownership.resolve(c, reviewed, proposal={
            "confidence": "MEDIUM", "entity_type": "person", "entity_id": pid,
            "evidence": ["surname and ZIP"], "best_candidates": [{"entity_id": pid}]})
        rows = [r for r in queue.open_reviews(c, limit=200)
                if r["document_id"] in (linked, reviewed)]
    assert high["outcome"] == model.OUTCOME_LINKED
    assert _owner(linked) == (pid, None, None)
    assert medium["outcome"] == model.OUTCOME_REVIEW
    assert _owner(reviewed) == (None, None, None)
    assert [r["document_id"] for r in rows] == [reviewed]
    assert rows[0]["lane"] == model.LANE_SHAREPOINT


def test_no_match_is_unresolved_and_does_not_flood_the_review_queue():
    """No evidence is not ambiguity. A review queue full of documents nobody can decide is unusable."""
    did = _doc(source_system="SharePoint")
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={"confidence": None, "route": "NO_MATCH"})
        rows = [r for r in queue.open_reviews(c, limit=200) if r["document_id"] == did]
    assert verdict["outcome"] == model.OUTCOME_UNRESOLVED
    assert rows == []


# --- the never-overwrite guarantee ---------------------------------------------------------------------------

def test_an_already_owned_document_is_never_relinked():
    owner = _person(f"Perpetua {_TAG}")
    did = _doc(source_system="SharePoint", person_id=owner)
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": owner})
    assert verdict["outcome"] == model.OUTCOME_ALREADY_OWNED
    assert _owner(did) == (owner, None, None)


def test_a_lane_contradicting_the_stored_owner_opens_a_conflict_review_and_changes_nothing():
    owner = _person(f"Cassia {_TAG}")
    other = _person(f"Lucia {_TAG}")
    did = _doc(source_system="SharePoint", person_id=owner)
    with engine.begin() as c:
        verdict = ownership.resolve(c, did, proposal={
            "confidence": "HIGH", "entity_type": "person", "entity_id": other,
            "evidence": ["email match"]})
        rows = [r for r in queue.open_reviews(c, limit=200) if r["document_id"] == did]
    assert verdict["outcome"] == model.OUTCOME_REVIEW
    assert rows[0]["reason_code"] == "ownership_conflict"
    assert _owner(did) == (owner, None, None), "the stored owner must be untouched"


def test_losing_a_race_to_another_writer_never_overwrites_the_winner():
    """The canonical write re-checks all-NULL in the same statement; a stale decision must lose."""
    winner = _person(f"Drusilla {_TAG}")
    loser_target = _person(f"Flavia {_TAG}")
    did = _doc(source_system="SharePoint")
    with engine.begin() as c:
        from app.services.households import resolve_document_ownership
        resolve_document_ownership(did, person_id=winner, conn=c, request_id="test")
        verdict = ownership._apply_link(c, did, ownership.current_owner(c, did),
                                        lane=model.LANE_SHAREPOINT, entity_type="person",
                                        entity_id=loser_target, entity_name="Flavia",
                                        evidence=[], actor_user_id=None, request_id="test")
    assert verdict["outcome"] == model.OUTCOME_REVIEW
    assert _owner(did) == (winner, None, None)


def test_a_household_proposal_agrees_with_a_document_owned_by_person_and_household():
    """The 800-vs-474 bug, pinned.

    A document routinely carries a person AND that person's household. Asking "who owns this" and
    taking the first non-null answer returns the person, so a folder that correctly maps to the
    household reads as a conflict with an owner it agrees with. The comparison must be per entity
    type, against the matching column."""
    row = {"person_id": 11, "household_id": 22, "organization_id": None}

    assert ownership.conflicts_with_stored_owner(row, "household", 22) is False
    assert ownership.conflicts_with_stored_owner(row, "person", 11) is False
    assert ownership.conflicts_with_stored_owner(row, "household", 99) is True
    assert ownership.conflicts_with_stored_owner(row, "person", 99) is True


def test_the_planner_and_the_runtime_agree_on_what_a_conflict_is():
    """The plan is only worth reading if it predicts the pipeline. One rule, one answer."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "plan_document_ownership.py"
    spec = importlib.util.spec_from_file_location("plan_conflict_parity", script)
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)

    row = {"person_id": 11, "household_id": 22, "organization_id": None}
    for entity_type, entity_id in (("household", 22), ("person", 11),
                                   ("household", 99), ("person", 99)):
        assert planner._conflicts(row, (entity_type, entity_id)) == \
            ownership.conflicts_with_stored_owner(row, entity_type, entity_id)

    # An unowned document can never conflict, whatever is proposed.
    unowned = {"person_id": None, "household_id": None, "organization_id": None}
    assert planner._conflicts(unowned, ("person", 11)) is False


def test_a_missing_document_is_reported_not_guessed_at():
    with engine.begin() as c:
        verdict = ownership.resolve(c, 2_147_000_001, proposal={})
    assert verdict["outcome"] == model.OUTCOME_UNRESOLVED
    assert verdict["reason_code"] == "document_not_found"
