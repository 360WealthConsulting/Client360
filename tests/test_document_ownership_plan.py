"""The read-only full-corpus ownership plan (``scripts/plan_document_ownership.py``).

Two things have to be true of a tool that is pointed at a production client corpus, and both are
tested here rather than asserted in a docstring:

1. **It cannot write.** The guard is the SERVER's ``default_transaction_read_only``, so a bug in the
   planner cannot reach production data — it can only crash. The test proves the engine it builds
   actually refuses a write.
2. **It predicts the pipeline.** The plan is only worth reading if the lanes and the
   confidence-to-outcome mapping are the same ones ``document_pipeline_continuous.ownership`` uses at
   runtime. A planner with its own private copy of those rules eventually predicts the wrong thing.

Plus the counting itself: a document with an authoritative match, one that is ambiguous, one that
contradicts a stored owner and one nothing matches must land in four different buckets.
"""
import hashlib
import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db import documents, engine, people
from app.services.document_pipeline_continuous import ownership

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plan_document_ownership.py"
_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "efghijklmn")).capitalize()

_DOCS: list[int] = []
_PEOPLE: list[int] = []


def _load():
    spec = importlib.util.spec_from_file_location("plan_document_ownership_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan_module = _load()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            for table in ("document_sources", "document_ocr", "document_facts",
                          "document_classifications", "document_pipeline_tasks"):
                c.execute(sa.text(f"DELETE FROM {table} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    _DOCS.clear()
    _PEOPLE.clear()


def _person(full_name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(*, name="p.txt", tags=None, person_id=None, source_system=None, size_bytes=10,
         storage_path="x"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, original_name=name, stored_name=f"pln-{uuid.uuid4().hex}",
            storage_path=storage_path, size_bytes=size_bytes,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags=tags or {}).returning(documents.c.id)).scalar_one()
        if source_system:
            c.execute(sa.text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                              "VALUES (:d, :s, '')"), {"d": did, "s": source_system})
    _DOCS.append(did)
    return did


# --- the read-only guard -------------------------------------------------------------------------

def test_the_planner_engine_is_read_only_at_the_server():
    """Not "the code does not call insert()" — the server refuses the write."""
    url = str(engine.url.render_as_string(hide_password=False))
    read_only = plan_module.read_only_engine(url)
    try:
        with read_only.connect() as conn:
            assert str(conn.execute(sa.text("SHOW default_transaction_read_only")).scalar()).lower() \
                in ("on", "true")
            with pytest.raises(Exception):
                conn.execute(sa.text("CREATE TEMP TABLE _plan_guard_probe (x int)"))
    finally:
        read_only.dispose()


def test_assert_read_only_refuses_a_writable_connection():
    """The startup check has to fail loudly on an ordinary connection, or it proves nothing."""
    with engine.connect() as writable:
        with pytest.raises(SystemExit):
            plan_module.assert_read_only(writable)


def test_the_planner_never_writes():
    """No mutation verb anywhere in the file — belt as well as the server's braces."""
    source = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("engine.begin(", ".insert(", ".update(", ".delete(",
                      "resolve_document_ownership", "run_ocr(", "record_extracted_text"):
        assert forbidden not in source, f"the plan must not be able to {forbidden}"


# --- it predicts the pipeline ---------------------------------------------------------------------

def test_the_plan_shares_the_pipeline_s_lane_and_confidence_rules():
    planner_names = plan_module.Planner.__init__.__code__.co_names
    assert "LINKABLE_CONFIDENCE" in planner_names
    assert "REVIEWABLE_CONFIDENCE" in planner_names
    # The values themselves come from the runtime module, so they cannot drift apart.
    assert ownership.LINKABLE_CONFIDENCE == ("HIGH",)
    assert ownership.REVIEWABLE_CONFIDENCE == ("MEDIUM", "AMBIGUOUS")


def test_cached_ocr_text_is_truncated_to_the_pipeline_s_cap():
    """Scoring on evidence the pipeline will never see predicts matches it will never make."""
    from app.services.document_owner_proposal import _MAX_TEXT_CHARS

    did = _doc(name="long.pdf")
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO document_ocr (document_id, status, text, char_count) "
                          "VALUES (:d, 'completed', :t, :n)"),
                  {"d": did, "t": "x" * (_MAX_TEXT_CHARS + 5000), "n": _MAX_TEXT_CHARS + 5000})

    url = str(engine.url.render_as_string(hide_password=False))
    read_only = plan_module.read_only_engine(url)
    try:
        with read_only.connect() as conn:
            fetched = plan_module._ocr_for(conn, [did])
    finally:
        read_only.dispose()
    assert len(fetched[did]["text"]) == _MAX_TEXT_CHARS
    # The true length is still reported, so the truncation is visible rather than silent.
    assert fetched[did]["char_count"] == _MAX_TEXT_CHARS + 5000


def test_lane_selection_matches_the_pipeline_s_precedence():
    url = str(engine.url.render_as_string(hide_password=False))
    read_only = plan_module.read_only_engine(url)
    try:
        with read_only.connect() as conn:
            planner = plan_module.Planner(conn, check_files=False)
            assert planner.lane_for({"Drake", "SharePoint"}) == ownership.LANE_DRAKE
            assert planner.lane_for({"TaxDome Drive", "SharePoint"}) == ownership.LANE_TAXDOME
            assert planner.lane_for({"SharePoint"}) == ownership.LANE_SHAREPOINT
            assert planner.lane_for(set()) == ownership.LANE_SHAREPOINT
    finally:
        read_only.dispose()


# --- the counting ---------------------------------------------------------------------------------

def test_an_authoritative_taxdome_folder_counts_as_a_high_confidence_match():
    name = f"Theodora {_TAG}"
    pid = _person(name)
    did = _doc(source_system="TaxDome Drive",
               tags={"source_system": "TaxDome Drive", "taxdome_folder": name})
    url = str(engine.url.render_as_string(hide_password=False))
    read_only = plan_module.read_only_engine(url)
    try:
        with read_only.connect() as conn:
            planner = plan_module.Planner(conn, check_files=False)
            row = conn.execute(sa.text(
                "SELECT id, original_name, status, size_bytes, sha256, storage_uri, storage_path, "
                "person_id, household_id, organization_id, tags, category, classification, "
                "subcategory FROM documents WHERE id = :d"), {"d": did}).mappings().first()
            verdict = planner.plan_document(row, {"TaxDome Drive"}, None)
    finally:
        read_only.dispose()
    assert verdict["lane"] == ownership.LANE_TAXDOME
    assert verdict["proposed"] == ("person", pid)


def test_an_unresolvable_taxdome_folder_counts_as_ambiguous_not_unmatched():
    did = _doc(source_system="TaxDome Drive",
               tags={"source_system": "TaxDome Drive",
                     "taxdome_folder": f"Nobody {_TAG} At All"})
    url = str(engine.url.render_as_string(hide_password=False))
    read_only = plan_module.read_only_engine(url)
    try:
        with read_only.connect() as conn:
            planner = plan_module.Planner(conn, check_files=False)
            row = conn.execute(sa.text(
                "SELECT id, original_name, status, size_bytes, sha256, storage_uri, storage_path, "
                "person_id, household_id, organization_id, tags, category, classification, "
                "subcategory FROM documents WHERE id = :d"), {"d": did}).mappings().first()
            verdict = planner.plan_document(row, {"TaxDome Drive"}, None)
    finally:
        read_only.dispose()
    assert verdict["proposed"] is None
    assert verdict["reason"] == "taxdome_folder_unresolved"


def test_a_conflict_is_counted_separately_from_a_match():
    owner = _person(f"Marcella {_TAG}")
    other = _person(f"Cornelia {_TAG}")
    row = {"person_id": owner, "household_id": None, "organization_id": None}
    assert plan_module._conflicts(row, ("person", other)) is True
    assert plan_module._conflicts(row, ("person", owner)) is False
    assert plan_module._conflicts({"person_id": None, "household_id": None,
                                   "organization_id": None}, ("person", other)) is False


# --- the inventory ----------------------------------------------------------------------------------

def test_inventory_flags_zero_byte_unsupported_timed_out_and_missing_files(tmp_path):
    present = tmp_path / "here.pdf"
    present.write_bytes(b"%PDF-1.4\n")

    zero = {"original_name": "a.pdf", "size_bytes": 0, "storage_uri": str(present),
            "storage_path": str(present)}
    assert "zero_byte" in plan_module._inventory(zero, {"status": "completed"}, check_files=True)

    archive = {"original_name": "a.zip", "size_bytes": 10, "storage_uri": str(present),
               "storage_path": str(present)}
    assert "unsupported" in plan_module._inventory(archive, {"status": "completed"}, check_files=True)

    slow = {"original_name": "a.pdf", "size_bytes": 10, "storage_uri": str(present),
            "storage_path": str(present)}
    assert "timed_out" in plan_module._inventory(slow, {"status": "timed_out"}, check_files=True)

    gone = {"original_name": "a.pdf", "size_bytes": 10,
            "storage_uri": str(tmp_path / "not-here.pdf"),
            "storage_path": str(tmp_path / "not-here.pdf")}
    assert "missing_source_file" in plan_module._inventory(gone, {"status": "completed"},
                                                           check_files=True)


def test_a_document_with_no_ocr_row_yet_is_awaiting_ocr_not_a_failure():
    """OCR is still running on the corpus. 'Not done yet' must not read as 'broken'."""
    row = {"original_name": "scan.pdf", "size_bytes": 100, "storage_uri": None, "storage_path": None}
    flags = plan_module._inventory(row, None, check_files=False)
    assert "awaiting_ocr" in flags
    assert "extraction_failed" not in flags
    assert "unsupported" not in flags


# --- deleted documents are not outstanding work --------------------------------------------------------

def test_deleted_documents_are_excluded_from_every_lane_total(tmp_path):
    """Discovery skips ``status = 'deleted'``, so counting those as unmatched reports work that
    nothing will ever do. On the live corpus that mistake was worth 48,549 documents."""
    live = _doc(name="live.pdf")
    dead = _doc(name="dead.pdf")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == dead).values(status="deleted"))

    plan = plan_module.run(
        out_path=tmp_path / "plan.json", checkpoint_path=tmp_path / "plan.checkpoint.json",
        chunk_size=500, resume=False, check_files=False,
        database_url=str(engine.url.render_as_string(hide_password=False)), progress_every=0)

    with engine.connect() as c:
        corpus = c.execute(sa.text("SELECT count(*) FROM documents")).scalar()
    assert plan["excluded_deleted"] >= 1
    # Every document is accounted for exactly once: planned, or excluded as deleted.
    assert plan["documents_planned"] + plan["excluded_deleted"] == corpus
    assert sum(plan["by_lane"].values()) == plan["documents_planned"]
    assert live and dead      # both existed for the run


def test_every_planned_document_lands_in_exactly_one_bucket(tmp_path):
    """The five outcome buckets must partition the planned corpus — otherwise the totals are a
    collection of numbers rather than an account of the work."""
    _doc(name="bucket.pdf")
    plan = plan_module.run(
        out_path=tmp_path / "plan.json", checkpoint_path=tmp_path / "plan.checkpoint.json",
        chunk_size=500, resume=False, check_files=False,
        database_url=str(engine.url.render_as_string(hide_password=False)), progress_every=0)

    buckets = sum(sum(plan[section].values()) for section in (
        "high_confidence_unmatched_documents", "ambiguous",
        "conflicts_with_existing_ownership", "already_owned_agreeing", "unmatched"))
    assert buckets == plan["documents_planned"]


# --- no corpus cap ------------------------------------------------------------------------------------

def test_the_planner_exposes_no_document_limit():
    """Chunking is for checkpointing and recovery. A limit flag would be a corpus cap."""
    source = _SCRIPT.read_text(encoding="utf-8")
    parser_flags = [line for line in source.splitlines() if "add_argument" in line]
    joined = " ".join(parser_flags)
    for capping_flag in ('"--limit"', '"--max-documents"', '"--max-rows"', '"--top"'):
        assert capping_flag not in joined, f"{capping_flag} would cap the corpus"
    assert '"--chunk-size"' in joined


def test_chunking_is_documented_as_checkpointing_only():
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "NOT a corpus cap" in source
