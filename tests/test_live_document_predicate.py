"""The ONE definition of "a pipeline may act on this document", pinned on all four conditions.

WHY THIS FILE EXISTS
--------------------
The continuous pipeline originally filtered on ``status <> 'deleted'`` alone. Against the production
corpus of 121,865 documents that accepted **50 rows the firm had already retired**:

* 49 with ``deleted_at`` stamped AND ``archived = true`` while ``status`` still said ``'active'`` —
  the half-written soft deletes ``document_platform.lifecycle``'s header describes, left behind when
  a merge run is interrupted between its two statements;
* 1 archived through the older ``services.documents.archive_document`` path, which sets ``archived``
  and ``archived_at`` and never touches ``status``.

The pipeline writes ownership. Processing a document the firm has put away is not a cosmetic
miscount, so the predicate is defined once in
``app.services.document_platform.lifecycle.live_document_clause`` and every consumer imports it.

Each of the four conditions gets its own test. A test that only checked the conjunction would still
pass if someone dropped one condition and another happened to reject the fixture.
"""
import uuid

import pytest
import sqlalchemy as sa

from app.db import documents, engine
from app.services.document_pipeline_continuous import discovery, model, queue, stages
from app.services.document_platform.lifecycle import (
    LIVE_DOCUMENT_CONDITIONS,
    is_live_document,
    live_document_clause,
)

_DOCS: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            for table in ("document_pipeline_tasks", "document_ocr", "document_sources"):
                c.execute(sa.text(f"DELETE FROM {table} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        c.execute(sa.text("UPDATE document_pipeline_checkpoints SET cursor_document_id = 0 "
                          "WHERE name = :n"), {"n": model.DISCOVERY_CHECKPOINT})
    _DOCS.clear()


def _doc(**overrides) -> int:
    values = {"original_name": "live.pdf", "stored_name": f"ld-{uuid.uuid4().hex}",
              "storage_path": "x", "size_bytes": 10, "sha256": uuid.uuid4().hex * 2,
              "status": "active", "archived": False, "tags": {}}
    values.update(overrides)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(**values).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _selected_by_clause(document_id) -> bool:
    with engine.connect() as c:
        return c.execute(
            sa.select(documents.c.id)
            .where(documents.c.id == document_id, live_document_clause())).first() is not None


def _row(document_id):
    with engine.connect() as c:
        return c.execute(
            sa.select(documents.c.status, documents.c.deleted_at, documents.c.archived,
                      documents.c.archived_at).where(documents.c.id == document_id)).mappings().first()


# --- the four conditions, one test each -------------------------------------------------------------

def test_a_fully_live_document_is_selected():
    did = _doc()
    assert _selected_by_clause(did) is True
    assert is_live_document(_row(did)) is True


def test_condition_status_must_be_active():
    """A status nobody has thought about yet must be excluded by DEFAULT, not processed."""
    for status in ("deleted", "archived"):
        did = _doc(status=status)
        assert _selected_by_clause(did) is False, f"status={status!r} must not be live"
        assert is_live_document(_row(did)) is False


def test_condition_deleted_at_must_be_null():
    """The 49-document case: deleted_at stamped while status was never moved."""
    did = _doc(status="active", deleted_at=sa.func.now())
    assert _selected_by_clause(did) is False
    assert is_live_document(_row(did)) is False


def test_condition_archived_must_be_false():
    did = _doc(status="active", archived=True)
    assert _selected_by_clause(did) is False
    assert is_live_document(_row(did)) is False


def test_condition_archived_at_must_be_null():
    """The 1-document case: archived through the path that stamps archived_at only."""
    did = _doc(status="active", archived=False, archived_at=sa.func.now())
    assert _selected_by_clause(did) is False
    assert is_live_document(_row(did)) is False


def test_the_exact_production_shape_that_caused_the_fifty_document_gap():
    """deleted_at + archived, status still 'active' — 49 production rows looked like this."""
    did = _doc(status="active", deleted_at=sa.func.now(), archived=True)
    assert _selected_by_clause(did) is False
    assert is_live_document(_row(did)) is False


def test_all_four_conditions_are_named_in_the_shared_constant():
    """The constant is what the planner reports and what a reader greps for."""
    assert set(LIVE_DOCUMENT_CONDITIONS) == {
        "status = 'active'", "deleted_at IS NULL", "archived = false", "archived_at IS NULL"}


# --- the SQL and the Python reading must agree --------------------------------------------------------

@pytest.mark.parametrize("overrides", [
    {},
    {"status": "deleted"},
    {"status": "archived"},
    {"deleted_at": sa.func.now()},
    {"archived": True},
    {"archived_at": sa.func.now()},
    {"deleted_at": sa.func.now(), "archived": True},
    {"archived": True, "archived_at": sa.func.now()},
])
def test_the_sql_clause_and_the_python_predicate_always_agree(overrides):
    """One rule expressed twice. Drift between them is the bug this pins."""
    did = _doc(**overrides)
    assert _selected_by_clause(did) == is_live_document(_row(did))


# --- every consumer uses it -----------------------------------------------------------------------------

def test_discovery_does_not_enqueue_a_retired_document():
    live = _doc()
    retired = _doc(status="active", deleted_at=sa.func.now(), archived=True)
    with engine.begin() as c:
        c.execute(sa.text("UPDATE document_pipeline_checkpoints SET cursor_document_id = :v "
                          "WHERE name = :n"),
                  {"v": min(live, retired) - 1, "n": model.DISCOVERY_CHECKPOINT})

    discovery.discover(page_size=100)

    with engine.connect() as c:
        assert queue.task_for_document(c, live) is not None
        assert queue.task_for_document(c, retired) is None, \
            "a retired document must never enter the queue"


def test_the_backlog_estimate_counts_only_live_documents():
    live = _doc()
    retired = _doc(status="active", archived=True, archived_at=sa.func.now())
    with engine.begin() as c:
        c.execute(sa.text("UPDATE document_pipeline_checkpoints SET cursor_document_id = :v "
                          "WHERE name = :n"),
                  {"v": min(live, retired) - 1, "n": model.DISCOVERY_CHECKPOINT})
    with engine.connect() as c:
        estimate = discovery.backlog_estimate(c)
    assert estimate["undiscovered"] == 1, "the retired document must not read as outstanding work"


def test_the_extract_stage_refuses_a_document_retired_after_it_was_queued():
    """Discovery and the stage must apply the SAME rule, or a document retired mid-flight is
    processed by the half of the pipeline that was not looking."""
    did = _doc()
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == did)
                  .values(archived=True, archived_at=sa.func.now()))
        with pytest.raises(model.PipelinePermanentError) as raised:
            stages.run_extract(c, {"id": 0, "document_id": did, "stage": model.STAGE_EXTRACT,
                                   "attempts": 1, "max_attempts": 5})
    assert raised.value.reason_code == "document_not_live"


def test_discovery_does_not_requeue_a_retired_document_whose_content_changed():
    did = _doc(sha256="a" * 64)
    with engine.begin() as c:
        c.execute(sa.text("UPDATE document_pipeline_checkpoints SET cursor_document_id = :v "
                          "WHERE name = :n"), {"v": did - 1, "n": model.DISCOVERY_CHECKPOINT})
    discovery.discover(page_size=100)
    with engine.begin() as c:
        claimed = queue.claim(c, worker_id="test-live-predicate", limit=1)
        queue.finish(c, claimed[0]["id"], worker_id="test-live-predicate",
                     outcome=model.OUTCOME_LINKED)
        # The content changes AND the document is retired, in that order.
        c.execute(documents.update().where(documents.c.id == did)
                  .values(sha256="b" * 64, deleted_at=sa.func.now()))

    result = discovery.discover(page_size=100)
    assert result["requeued"] == 0
    with engine.connect() as c:
        assert queue.task_for_document(c, did)["state"] == model.STATE_SUCCEEDED
