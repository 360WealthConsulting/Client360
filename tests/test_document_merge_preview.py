"""Canonical document merge PREVIEW — read-only, database-only.

The preview reports what consolidating duplicate-content documents WOULD require. It writes
nothing, touches no file, and never decides client identity: survivor selection is mechanical
(lowest eligible documents.id, matching ADR-072) and ownership is compared afterwards as evidence.

Every fixture here is synthetic.
"""
from __future__ import annotations

import uuid

import pytest  # noqa: F401  (monkeypatch fixture)
from sqlalchemy import text

from app.db import engine
from app.services import document_merge as dm

_TAG = "DOCMERGE"
#: Synthetic source systems. Real importer names ("SharePoint", "TaxDome Drive") would be counted
#: by the SharePoint/TaxDome suites, so this module never writes one.
_SYS_A, _SYS_B = "DocMergeProbeA", "DocMergeProbeB"


@pytest.fixture(autouse=True)
def _isolate():
    """Remove every document (and its cascading dependents) this module creates.

    The preview reads the WHOLE corpus, so leftovers here would also skew a later run's counts."""
    def _wipe():
        with engine.begin() as c:
            c.execute(text("DELETE FROM documents WHERE stored_name LIKE 'dm-%'"))
            c.execute(text("DELETE FROM people WHERE full_name LIKE :p"), {"p": f"{_TAG}%"})
    _wipe()
    yield
    _wipe()


def _sha() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex          # 64 hex chars


def _doc(sha, *, person_id=None, household_id=None, organization_id=None,
         category=None, classification=None, status="active"):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO documents (original_name, stored_name, storage_path, size_bytes, sha256,"
            " status, archived, person_id, household_id, organization_id, category, classification)"
            " VALUES (:n, :s, :p, 1, :sha, :st, false, :pid, :hid, :oid, :cat, :cls) RETURNING id"),
            {"n": f"{_TAG} {tag}.pdf", "s": f"dm-{tag}", "p": f"/t/{tag}", "sha": sha, "st": status,
             "pid": person_id, "hid": household_id, "oid": organization_id,
             "cat": category, "cls": classification}).scalar_one()


def _person(name_suffix=""):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
            {"n": f"{_TAG} Client {tag}{name_suffix}"}).scalar_one()


def _ocr(document_id, *, status="completed", char_count=100, body="text"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count)"
                       " VALUES (:d, :s, :t, :n)"),
                  {"d": document_id, "s": status, "t": body, "n": char_count})


def _classification(document_id, doc_type):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_classifications (document_id, doc_type,"
                       " classifier_version) VALUES (:d, :t, 'test')"),
                  {"d": document_id, "t": doc_type})


def _fact(document_id, fact_type, fact_value):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_facts (document_id, fact_type, fact_value,"
                       " extraction_engine) VALUES (:d, :t, :v, 'test')"),
                  {"d": document_id, "t": fact_type, "v": fact_value})


def _source(document_id, system, uri):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri)"
                       " VALUES (:d, :s, :u)"), {"d": document_id, "s": system, "u": uri})


def _group_for(sha, report=None):
    report = report or dm.preview()
    return next((g for g in report["groups"] if g["sha256"] == sha), None)


# --- deterministic survivor selection ---------------------------------------------------------

def test_survivor_is_the_lowest_eligible_document_id():
    sha = _sha()
    ids = sorted([_doc(sha), _doc(sha), _doc(sha)])
    g = _group_for(sha)
    assert g["proposed_survivor"] == ids[0]
    assert sorted(g["duplicate_document_ids"]) == ids[1:]


def test_ownership_never_changes_the_survivor():
    """The whole point: canonicalization must not become an implicit identity decision."""
    sha = _sha()
    first = _doc(sha)                                   # unowned, lowest id
    owned = _doc(sha, person_id=_person())              # owned, higher id
    g = _group_for(sha)
    assert g["proposed_survivor"] == first, "an owned duplicate must NOT win survivor selection"
    assert owned in g["duplicate_document_ids"]
    assert g["ownership"]["distinct_owner_count"] == 1  # ...but the ownership is reported


def test_a_deleted_row_is_not_eligible():
    """Same predicate the ADR-072 resolver uses."""
    sha = _sha()
    _doc(sha, status="deleted")
    live = _doc(sha)
    assert _group_for(sha) is None, "one eligible row is not a duplicate group"
    other = _doc(sha)
    g = _group_for(sha)
    assert sorted(g["document_ids"] if "document_ids" in g else
                  [g["proposed_survivor"], *g["duplicate_document_ids"]]) == sorted([live, other])


# --- classification ---------------------------------------------------------------------------

def test_same_content_same_owner_is_safe_auto_merge():
    sha, pid = _sha(), _person()
    _doc(sha, person_id=pid)
    _doc(sha, person_id=pid)
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE
    assert g["conflicts"] == [] and g["blockers"] == []


def test_same_content_different_owner_is_blocked():
    """Resolving which client owns it is an identity decision — Drake's authority, not this layer's."""
    sha = _sha()
    _doc(sha, person_id=_person(" A"))
    _doc(sha, person_id=_person(" B"))
    g = _group_for(sha)
    assert g["classification"] == dm.BLOCKED
    kinds = {b["kind"] for b in g["blockers"]}
    assert "conflicting_ownership" in kinds


def test_owner_on_a_non_survivor_requires_review():
    sha = _sha()
    _doc(sha)                                            # survivor, unowned
    _doc(sha, person_id=_person())                       # owner sits on the duplicate
    g = _group_for(sha)
    assert g["classification"] == dm.REVIEW
    assert "ownership_on_non_survivor" in {c["kind"] for c in g["conflicts"]}


def test_owner_on_the_survivor_alone_is_safe():
    sha, pid = _sha(), _person()
    _doc(sha, person_id=pid)                             # survivor already holds it
    _doc(sha)
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE


# --- OCR / classification / facts: compare SUBSTANCE, do not collapse blindly -------------------

def test_equivalent_ocr_rows_are_deduplicable_not_a_conflict():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _ocr(a, status="completed", char_count=100)
    _ocr(b, status="completed", char_count=100)          # same substance
    g = _group_for(sha)
    assert g["ocr"]["rows"] == 2 and g["ocr"]["conflict"] is False
    assert g["classification"] == dm.SAFE


def test_materially_different_ocr_requires_review():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _ocr(a, status="completed", char_count=5000)
    _ocr(b, status="unsupported", char_count=0)
    g = _group_for(sha)
    assert g["ocr"]["conflict"] is True
    assert g["classification"] == dm.REVIEW


def test_conflicting_classification_requires_review():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _classification(a, "1099")
    _classification(b, "W-2")
    g = _group_for(sha)
    assert g["classification_rows"]["conflict"] is True
    assert g["classification"] == dm.REVIEW


def test_identical_facts_are_redundant_conflicting_facts_are_not():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _fact(a, "tax_year", "2025")
    _fact(b, "tax_year", "2025")                          # same statement recorded twice
    g = _group_for(sha)
    assert g["facts"]["conflict"] is False and g["facts"]["redundant_rows"] >= 1

    sha2 = _sha()
    c, d = _doc(sha2), _doc(sha2)
    _fact(c, "tax_year", "2024")
    _fact(d, "tax_year", "2025")                          # genuine disagreement
    g2 = _group_for(sha2)
    assert g2["facts"]["conflict"] is True
    assert "tax_year" in g2["facts"]["conflicting_fact_types"]
    assert g2["classification"] == dm.REVIEW


# --- provenance --------------------------------------------------------------------------------

def test_every_distinct_provenance_tuple_is_preserved():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _source(a, _SYS_A, "probe://one")
    _source(b, _SYS_B, "probe://two")               # a DIFFERENT source relationship
    g = _group_for(sha)
    assert g["provenance"]["rows"] == 2
    assert g["provenance"]["distinct_provenance_tuples"] == 2
    assert g["provenance"]["preserved_after_merge"] == 2, "no provenance may be dropped"
    assert set(g["provenance"]["source_systems"]) == {_SYS_A, _SYS_B}


def test_an_identical_provenance_tuple_deduplicates_without_losing_the_relationship():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _source(a, _SYS_A, "probe://same")
    _source(b, _SYS_A, "probe://same")                 # the SAME relationship, recorded twice
    g = _group_for(sha)
    assert g["provenance"]["rows"] == 2
    assert g["provenance"]["preserved_after_merge"] == 1  # still recorded — once
    assert g["provenance"]["redundant_rows"] == 1
    assert g["classification"] == dm.SAFE


# --- dependent-row enumeration -------------------------------------------------------------------

def test_dependent_rows_are_enumerated_per_table_and_counted_for_reassignment():
    sha = _sha()
    survivor, dup = sorted([_doc(sha), _doc(sha)])
    _ocr(dup)
    _source(dup, _SYS_A, "probe://dep")
    g = _group_for(sha)
    assert g["dependent_row_counts"]["document_ocr.document_id"][dup] == 1
    assert g["reassignments_required"]["document_ocr.document_id"]["rows"] == 1
    assert g["reassignments_required"]["document_sources.document_id"]["rows"] == 1
    assert g["total_reassignments"] >= 2
    # The survivor's own rows are not "reassignments".
    assert survivor not in [survivor] or g["reassignments_required"]["document_ocr.document_id"]["rows"] == 1


def test_set_null_dependencies_are_counted_as_required_reassignments():
    """A SET NULL cascade would null a live reference; the executor must repoint it instead."""
    deps = {}
    with engine.connect() as conn:
        for d in dm._dependencies(conn):
            deps[f"{d['table']}.{d['column']}"] = d
    set_null = [k for k, d in deps.items() if d["delete_rule"] == "SET NULL"]
    assert set_null, "the schema still has SET NULL references to documents.id"
    for k in set_null:
        assert deps[k]["strategy"] == "reassign", f"{k} must be repointed, never nulled"


def test_no_action_dependencies_are_reassignable_not_automatic_blockers():
    with engine.connect() as conn:
        deps = dm._dependencies(conn)
    no_action = [d for d in deps if d["delete_rule"] == "NO ACTION"]
    assert no_action, "the schema still has NO ACTION references to documents.id"
    for d in no_action:
        assert d["strategy"] == "reassign", \
            "a NO ACTION reference is repointable; presence alone is not a semantic conflict"


# --- dependency coverage + unknown references -------------------------------------------------------

def test_the_registry_covers_every_live_reference_to_documents():
    """Derived from the LIVE schema, not a snapshot: a new FK fails here the day it is added."""
    with engine.connect() as conn:
        deps = dm._dependencies(conn)
    unregistered = sorted({d["table"] for d in deps if d["strategy"] is None})
    assert unregistered == [], f"references with no declared strategy: {unregistered}"
    assert len(deps) >= 25


def test_an_unknown_dependency_blocks_the_group(monkeypatch):
    sha = _sha()
    survivor, dup = sorted([_doc(sha), _doc(sha)])
    _ocr(dup)
    # Simulate a reference nobody has declared a strategy for.
    patched = dict(dm._STRATEGY)
    patched.pop("document_ocr")
    monkeypatch.setattr(dm, "_STRATEGY", patched)
    g = _group_for(sha)
    assert g["classification"] == dm.BLOCKED
    blocker = next(b for b in g["blockers"] if b["kind"] == "unknown_dependency")
    assert "document_ocr" in blocker["tables"]


def test_an_unknown_dependency_with_no_rows_does_not_block(monkeypatch):
    """Only an ACTUAL reference blocks — an undeclared table nothing points at is inert."""
    sha = _sha()
    _doc(sha), _doc(sha)
    patched = dict(dm._STRATEGY)
    patched.pop("document_ocr")
    monkeypatch.setattr(dm, "_STRATEGY", patched)
    assert _group_for(sha)["classification"] == dm.SAFE


# --- no writes, and idempotent ---------------------------------------------------------------------

_WATCHED = ("documents", "document_sources", "document_ocr", "document_classifications",
            "document_facts", "document_relationships", "rm_document_status")


def _snapshot():
    with engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in _WATCHED}


def test_the_preview_writes_nothing():
    sha = _sha()
    _doc(sha), _doc(sha)
    before = _snapshot()
    dm.preview()
    assert _snapshot() == before, "the preview must not create, change or remove a single row"


def test_the_preview_is_idempotent():
    sha = _sha()
    _doc(sha), _doc(sha)
    first, second = dm.preview(), dm.preview()
    a, b = _group_for(sha, first), _group_for(sha, second)
    assert a == b
    for key in ("total_duplicate_groups", "excess_duplicate_rows", "safe_auto_merge_groups",
                "review_required_groups", "blocked_groups", "total_proposed_reassignments"):
        assert first[key] == second[key]


def test_the_report_declares_itself_read_only():
    r = dm.preview(limit=1)
    assert r["read_only"] is True and r["wrote_anything"] is False
    assert "ownership never influences selection" in r["survivor_rule"]


# --- the module must not be able to write or scan ------------------------------------------------------

def test_the_service_opens_no_write_transaction_and_touches_no_filesystem():
    """Inspect the AST, not the prose: the module documents what it refuses to do, so a plain
    substring scan would match its own docstring."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(dm.__file__).read_text(encoding="utf-8"))

    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    assert "begin" not in called, "a preview must never open a write transaction (engine.begin)"
    for forbidden in ("open", "walk", "rglob", "glob", "iterdir", "unlink", "rename", "copy"):
        assert forbidden not in called, f"preview must not call {forbidden}()"

    # No write SQL in any string literal the module actually executes.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = " ".join(node.value.split()).upper()
            for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
                assert verb not in upper, f"preview must contain no {verb.strip()} statement"

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("os", "pathlib", "shutil", "requests", "subprocess"):
        assert forbidden not in imported, f"preview must not import {forbidden}"


def test_execution_is_not_implemented_here():
    """Merge execution is deliberately a separate, later change."""
    for name in ("merge", "apply", "execute", "consolidate"):
        assert not hasattr(dm, name), f"document_merge must expose no {name}() yet"


# --- batching: query cost must scale with dependency tables x batches, not with groups ------------

def _instr(report):
    return report["instrumentation"]


def test_the_report_carries_query_instrumentation():
    sha = _sha()
    _doc(sha), _doc(sha)
    i = _instr(dm.preview())
    for key in ("sql_query_count", "duplicate_groups_processed",
                "duplicate_document_rows_processed", "elapsed_seconds", "batch_size", "id_batches"):
        assert key in i, key
    assert i["sql_query_count"] > 0 and i["batch_size"] == dm.DEFAULT_BATCH_SIZE


def test_query_count_does_not_grow_with_the_number_of_duplicate_groups():
    """The acceptance target: 30x the groups must NOT mean 30x the queries.

    Before batching the cost was groups x dependency-tables (~31 statements per group). Now every
    dependent table is read once per id BATCH across the whole corpus, so adding groups adds rows
    to existing queries rather than new queries."""
    for _ in range(2):
        sha = _sha()
        _doc(sha), _doc(sha)
    small = _instr(dm.preview())

    for _ in range(30):
        sha = _sha()
        _doc(sha), _doc(sha)
    large = _instr(dm.preview())

    assert large["duplicate_groups_processed"] >= small["duplicate_groups_processed"] + 30
    assert large["sql_query_count"] == small["sql_query_count"], (
        "query count must be independent of group count while the ids fit one batch: "
        f"{small['sql_query_count']} -> {large['sql_query_count']} for "
        f"{small['duplicate_groups_processed']} -> {large['duplicate_groups_processed']} groups")


def test_query_count_is_bounded_by_dependency_tables_times_batches():
    sha = _sha()
    _doc(sha), _doc(sha)
    with engine.connect() as conn:
        dep_count = len(dm._dependencies(conn))
    i = _instr(dm.preview())
    # 1 discovery + (deps + 6 detail reads) x batches, with a little headroom.
    ceiling = 1 + (dep_count + 6) * max(i["id_batches"], 1) + 5
    assert i["sql_query_count"] <= ceiling, f"{i['sql_query_count']} > {ceiling}"
    assert i["sql_query_count"] < dep_count * i["duplicate_groups_processed"] or \
        i["duplicate_groups_processed"] <= 1


def test_a_small_batch_size_adds_batches_not_group_scaling():
    for _ in range(6):
        sha = _sha()
        _doc(sha), _doc(sha)
    big_batch = _instr(dm.preview(batch_size=5000))
    small_batch = _instr(dm.preview(batch_size=2))
    assert small_batch["id_batches"] > big_batch["id_batches"]
    assert small_batch["sql_query_count"] > big_batch["sql_query_count"]
    # Cost tracks BATCHES, not groups: a tiny batch multiplies by batch count, and at the real
    # default it collapses to one pass. (batch_size=2 is pathological on purpose — it is the knob,
    # not the workload.)
    assert big_batch["id_batches"] == 1
    assert small_batch["sql_query_count"] <= big_batch["sql_query_count"] * small_batch["id_batches"]


def test_batching_does_not_change_any_classification_or_conflict_outcome():
    """Equivalence: the batch size is a performance knob, never a semantic one."""
    sha_safe, sha_blocked, sha_review = _sha(), _sha(), _sha()
    _doc(sha_safe), _doc(sha_safe)
    _doc(sha_blocked, person_id=_person(" X")), _doc(sha_blocked, person_id=_person(" Y"))
    a, b = _doc(sha_review), _doc(sha_review)
    _classification(a, "1099")
    _classification(b, "W-2")

    def _shape(batch):
        r = dm.preview(batch_size=batch)
        return {g["sha256"]: (g["classification"], g["proposed_survivor"],
                              tuple(sorted(g["duplicate_document_ids"])),
                              tuple(sorted(c["kind"] for c in g["conflicts"])),
                              tuple(sorted(x["kind"] for x in g["blockers"])),
                              g["total_reassignments"])
                for g in r["groups"] if g["sha256"] in (sha_safe, sha_blocked, sha_review)}

    one_batch, many_batches = _shape(5000), _shape(1)
    assert one_batch == many_batches
    assert one_batch[sha_safe][0] == dm.SAFE
    assert one_batch[sha_blocked][0] == dm.BLOCKED
    assert one_batch[sha_review][0] == dm.REVIEW


def test_analyze_group_issues_no_query_of_its_own():
    """It takes prefetched data, not a connection — the signature makes a per-group query impossible."""
    import inspect
    assert "conn" not in inspect.signature(dm.analyze_group).parameters


# --- both document FKs on document_versions stay distinguishable ------------------------------------

def test_document_versions_is_modelled_on_both_of_its_document_fks():
    with engine.connect() as conn:
        deps = dm._dependencies(conn)
    cols = sorted(d["column"] for d in deps if d["table"] == "document_versions")
    assert cols == ["document_id", "previous_document_id"], cols
    for d in deps:
        if d["table"] == "document_versions":
            assert d["strategy"] == "reassign"


def test_a_version_chain_linking_two_group_members_is_reported_as_a_conflict():
    """After consolidation previous_document_id would equal document_id — a self-referential chain."""
    sha = _sha()
    survivor, dup = sorted([_doc(sha), _doc(sha)])
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_versions (document_id, previous_document_id,"
                       " version_number) VALUES (:d, :p, 2)"), {"d": dup, "p": survivor})
    g = _group_for(sha)
    assert g["version_self_references"], "the self-reference risk must be recorded"
    assert "version_chain_self_reference" in {c["kind"] for c in g["conflicts"]}
    assert g["classification"] == dm.REVIEW


def test_catalog_introspection_matches_information_schema_exactly():
    """The fast pg_catalog query must return the SAME references information_schema does.

    information_schema is the readable reference; it is a four-way join over catalog views and cost
    ~1.3s per call, which dominated every preview. This proves the substitution is faithful."""
    reference = """
    SELECT tc.table_name, kcu.column_name, rc.delete_rule
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
    JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
    JOIN information_schema.referential_constraints rc ON tc.constraint_name = rc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND ccu.table_name = 'documents' AND ccu.column_name = 'id'
      AND tc.table_name <> 'documents'
    """
    with engine.connect() as conn:
        expected = {tuple(r) for r in conn.execute(text(reference)).fetchall()}
        actual = {tuple(r) for r in conn.execute(text(dm._FK_SQL)).fetchall()}
    assert actual == expected


def test_schema_introspection_is_not_cached():
    """An unknown-dependency BLOCK is only trustworthy if the reference list is read fresh."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(dm.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_dependencies":
            names = {d.id for d in node.decorator_list if isinstance(d, ast.Name)}
            attrs = {d.attr for d in node.decorator_list if isinstance(d, ast.Attribute)}
            assert not (names | attrs) & {"cache", "lru_cache", "cached_property"}, \
                "_dependencies must query the live catalog on every call"
            break
    else:
        raise AssertionError("_dependencies not found")
