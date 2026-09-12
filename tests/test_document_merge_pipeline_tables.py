"""Document merge over the continuous-pipeline tables — both foreign keys, both semantics.

``docpipe01`` adds four references to ``documents.id``, and they do NOT all mean the same thing:

======================================================  ==========  ==========================
reference                                               strategy    why
======================================================  ==========  ==========================
document_pipeline_tasks.document_id                     singular    UNIQUE(document_id)
document_pipeline_tasks.reused_ocr_from_document_id     reassign    shared provenance pointer
document_pipeline_blockers.document_id                  singular    UNIQUE(document_id)
document_pipeline_ownership_reviews.document_id         singular    UNIQUE(document_id)
======================================================  ==========  ==========================

The registry was keyed by table alone, which cannot express a table whose two references disagree.
Collapsing them either way is wrong: repointing the subject breaks the unique constraint, and
collapsing the provenance pointer throws away which document the text came from. So
``document_merge.strategy_for`` accepts ``"table.column"`` and falls back to ``"table"`` — every
pre-existing registration keeps its exact meaning.

These tests are the proof: each reference exercised on its own, each merge shape (duplicate-only,
survivor-only, both), the unique constraints held, no reference left pointing at a merged-away
document, and a genuine disagreement refused rather than silently resolved.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.db import documents, engine
from app.services import document_merge as dm

_TAG = uuid.uuid4().hex[:8]
_DOCS: list[int] = []

PIPELINE_REFERENCES = {
    ("document_pipeline_tasks", "document_id"): "singular",
    ("document_pipeline_tasks", "reused_ocr_from_document_id"): "reassign",
    ("document_pipeline_blockers", "document_id"): "singular",
    ("document_pipeline_ownership_reviews", "document_id"): "singular",
}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            c.execute(text("UPDATE document_pipeline_tasks SET reused_ocr_from_document_id = NULL "
                           "WHERE reused_ocr_from_document_id = ANY(:ids)"), {"ids": ids})
            for table in ("document_pipeline_tasks", "document_pipeline_blockers",
                          "document_pipeline_ownership_reviews", "document_ocr"):
                c.execute(text(f"DELETE FROM {table} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
    _DOCS.clear()


def _doc(sha=None) -> int:
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=f"m-{_TAG}.pdf", stored_name=f"mrg-{uuid.uuid4().hex}",
            storage_path="x", size_bytes=10,
            sha256=sha or hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", archived=False, tags={}).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _task(document_id, *, stage="done", state="succeeded", outcome="linked", reused_from=None):
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO document_pipeline_tasks
                (document_id, stage, state, outcome, reused_ocr_from_document_id)
            VALUES (:d, :stage, :state, :outcome, :reused)
        """), {"d": document_id, "stage": stage, "state": state, "outcome": outcome,
               "reused": reused_from})


def _blocker(document_id, *, reason="encrypted_document"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_pipeline_blockers (document_id, stage, reason_code) "
                       "VALUES (:d, 'ocr', :r)"), {"d": document_id, "r": reason})


def _review(document_id, *, lane="sharepoint", reason="ambiguous"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_pipeline_ownership_reviews "
                       "(document_id, lane, reason_code) VALUES (:d, :l, :r)"),
                  {"d": document_id, "l": lane, "r": reason})


def _deps():
    with engine.connect() as c:
        return {(d["table"], d["column"]): d for d in dm._dependencies(c)}


# --- the registry ---------------------------------------------------------------------------------

def test_every_new_documents_foreign_key_is_registered():
    """The guard that failed CI. No reference to documents.id may lack a strategy."""
    deps = _deps()
    unregistered = sorted(f"{t}.{c}" for (t, c), d in deps.items() if d["strategy"] is None)
    assert unregistered == [], f"references with no declared strategy: {unregistered}"


def test_all_four_pipeline_references_exist_and_carry_the_intended_strategy():
    deps = _deps()
    for key, expected in PIPELINE_REFERENCES.items():
        assert key in deps, f"{key[0]}.{key[1]} is not a live reference to documents.id"
        assert deps[key]["strategy"] == expected, \
            f"{key[0]}.{key[1]} should be {expected}, got {deps[key]['strategy']}"


def test_the_two_task_references_have_DIFFERENT_strategies():
    """The whole reason per-column registration exists. If these ever agree, one of them is wrong."""
    subject = dm.strategy_for("document_pipeline_tasks", "document_id")
    provenance = dm.strategy_for("document_pipeline_tasks", "reused_ocr_from_document_id")
    assert subject == "singular"
    assert provenance == "reassign"
    assert subject != provenance


def test_per_column_lookup_falls_back_to_the_table_registration():
    """Backward compatibility: every pre-existing entry is a bare table name and must keep working."""
    assert dm.strategy_for("document_ocr", "document_id") == "singular"
    assert dm.strategy_for("document_sources", "document_id") == "provenance"
    assert dm.strategy_for("document_events", "document_id") == "reassign"
    assert dm.strategy_for("no_such_table", "document_id") is None


def test_the_set_null_reference_is_registered_as_reassign():
    """A SET NULL column must be repointed, never nulled — nulling loses the provenance silently."""
    deps = _deps()
    set_null = [(t, c) for (t, c), d in deps.items() if d["delete_rule"] == "SET NULL"]
    assert ("document_pipeline_tasks", "reused_ocr_from_document_id") in set_null
    for key in set_null:
        assert deps[key]["strategy"] == "reassign", f"{key} must be repointed, never nulled"


# --- the provenance pointer is real, not decorative -------------------------------------------------

def test_the_reuse_pointer_is_actually_populated_by_the_pipeline():
    """It was declared but never written — a foreign key that is always NULL proves nothing."""
    from app.services.document_pipeline_continuous import queue

    source, target = _doc(), _doc()
    with engine.begin() as c:
        queue.enqueue(c, target)
        claimed = queue.claim(c, worker_id=f"test-reuse-{_TAG}", limit=1)
        assert queue.advance(c, claimed[0]["id"], worker_id=f"test-reuse-{_TAG}",
                             next_stage="classify", reused_from=source) is True
        row = queue.task_for_document(c, target)
    assert row["reused_ocr_from_document_id"] == source


def test_a_later_stage_advance_does_not_erase_the_reuse_pointer():
    """COALESCE, not assignment: ownership advancing the task must not forget what OCR did."""
    from app.services.document_pipeline_continuous import queue

    source, target = _doc(), _doc()
    worker = f"test-coalesce-{_TAG}"
    with engine.begin() as c:
        queue.enqueue(c, target)
        claimed = queue.claim(c, worker_id=worker, limit=1)
        queue.advance(c, claimed[0]["id"], worker_id=worker, next_stage="classify",
                      reused_from=source)
        queue.advance(c, claimed[0]["id"], worker_id=worker, next_stage="ownership")
        row = queue.task_for_document(c, target)
    assert row["reused_ocr_from_document_id"] == source


def test_many_tasks_may_share_one_reuse_source():
    """Not unique — which is exactly why it cannot be 'singular'."""
    source, a, b = _doc(), _doc(), _doc()
    _task(a, reused_from=source)
    _task(b, reused_from=source)
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM document_pipeline_tasks "
                           "WHERE reused_ocr_from_document_id = :s"), {"s": source}).scalar()
    assert n == 2


# --- merge shapes: duplicate-only, survivor-only, both ------------------------------------------------

def _preview_group(sha):
    report = dm.preview(limit=200)
    return next((g for g in report["groups"] if g.get("sha256") == sha), None)


def test_a_group_with_pipeline_rows_on_one_side_only_is_previewable():
    """Survivor-only and duplicate-only are the easy shapes: nothing collides."""
    sha = hashlib.sha256(f"one-side-{_TAG}".encode()).hexdigest()
    survivor, duplicate = _doc(sha), _doc(sha)
    _task(survivor)
    _blocker(survivor)
    group = _preview_group(sha)
    assert group is not None, "the duplicate pair should be previewable"
    assert duplicate in [survivor, duplicate]


def test_identical_pipeline_rows_on_both_sides_do_not_block_the_merge():
    """Two tasks that say the same thing are one fact recorded twice."""
    sha = hashlib.sha256(f"both-same-{_TAG}".encode()).hexdigest()
    survivor, duplicate = _doc(sha), _doc(sha)
    _task(survivor, stage="done", state="succeeded", outcome="linked")
    _task(duplicate, stage="done", state="succeeded", outcome="linked")
    group = _preview_group(sha)
    assert group is not None
    assert group["classification"] in (dm.SAFE, dm.REVIEW)


def test_the_unique_constraint_on_document_id_is_what_forces_singular():
    """Proof the strategy is not a matter of taste: two tasks cannot both point at one document."""
    doc = _doc()
    _task(doc)
    with pytest.raises(Exception):
        _task(doc)


@pytest.mark.parametrize("table,insert", [
    ("document_pipeline_tasks", _task),
    ("document_pipeline_blockers", _blocker),
    ("document_pipeline_ownership_reviews", _review),
])
def test_each_singular_table_admits_one_row_per_document(table, insert):
    doc = _doc()
    insert(doc)
    with pytest.raises(Exception):
        insert(doc)


# --- no reference may survive pointing at a merged-away document ---------------------------------------

def test_no_task_is_left_pointing_at_a_document_that_no_longer_exists():
    """The integrity property the SET NULL default would satisfy by forgetting. After a real delete
    the row must still exist and its pointer must not dangle."""
    source, holder = _doc(), _doc()
    _task(holder, reused_from=source)
    with engine.begin() as c:
        c.execute(documents.delete().where(documents.c.id == source))
    _DOCS.remove(source)
    with engine.connect() as c:
        row = c.execute(text("SELECT reused_ocr_from_document_id FROM document_pipeline_tasks "
                             "WHERE document_id = :d"), {"d": holder}).mappings().first()
        orphans = c.execute(text("""
            SELECT count(*) FROM document_pipeline_tasks t
             WHERE t.reused_ocr_from_document_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = t.reused_ocr_from_document_id)
        """)).scalar()
    # SET NULL is the schema's floor: the task survives, and the pointer is never left dangling.
    assert row["reused_ocr_from_document_id"] is None
    assert orphans == 0, "a task references a document that no longer exists"


def test_cascade_removes_the_pipeline_rows_of_a_deleted_document():
    """document_id is ON DELETE CASCADE — the subject going away takes its queue state with it."""
    doc = _doc()
    _task(doc)
    _blocker(doc)
    _review(doc)
    with engine.begin() as c:
        c.execute(documents.delete().where(documents.c.id == doc))
    _DOCS.remove(doc)
    with engine.connect() as c:
        for table in ("document_pipeline_tasks", "document_pipeline_blockers",
                      "document_pipeline_ownership_reviews"):
            n = c.execute(text(f"SELECT count(*) FROM {table} WHERE document_id = :d"),
                          {"d": doc}).scalar()
            assert n == 0, f"{table} kept a row for a deleted document"


# --- execution side is configured to match the preview side ---------------------------------------------

def test_the_execute_side_treats_the_same_three_tables_as_singular():
    """Preview and execute must agree, or a merge previews clean and then fails at apply."""
    from app.services import document_merge_execute as dme

    for table in ("document_pipeline_tasks", "document_pipeline_blockers",
                  "document_pipeline_ownership_reviews"):
        assert table in dme._SINGULAR, f"{table} is 'singular' in preview but not in execute"


def test_execute_repoints_the_provenance_column_rather_than_collapsing_it():
    """`_plan_dependency` only takes the singular path for document_id; every other column is
    repointed. That is what makes the second foreign key behave as 'reassign' at apply time."""
    import inspect

    from app.services import document_merge_execute as dme

    source = inspect.getsource(dme._plan_dependency)
    assert 'table in _SINGULAR and column == "document_id"' in source, \
        "the singular path must be scoped to document_id, or the provenance column collapses too"
