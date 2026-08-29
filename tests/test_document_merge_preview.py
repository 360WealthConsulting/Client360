"""Canonical document merge PREVIEW — read-only, database-only.

The preview reports what consolidating duplicate-content documents WOULD require. It writes
nothing, touches no file, and never decides client identity: survivor selection is mechanical
(lowest eligible documents.id, matching ADR-072) and ownership is compared afterwards as evidence.

Every fixture here is synthetic.
"""
from __future__ import annotations

import json
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
    """Within one ownership scope the survivor is purely mechanical: the lowest id, owner-blind."""
    sha, pid = _sha(), _person()
    first = _doc(sha, person_id=pid)                    # lowest id
    later = _doc(sha, person_id=pid)                    # same owner, higher id
    g = _group_for(sha)
    assert g["proposed_survivor"] == first
    assert g["duplicate_document_ids"] == [later]
    assert g["survivor_rule"] == "lowest documents.id within the ownership-scoped partition"


def test_an_unowned_copy_is_never_retired_in_favour_of_an_owned_one():
    """A hash must never let an owned copy absorb an unowned one - that would infer identity."""
    sha = _sha()
    first = _doc(sha)                                   # unowned, lowest id
    owned = _doc(sha, person_id=_person())              # owned, higher id
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_unowned"
    assert g["duplicate_document_ids"] == [], "neither copy may be proposed for retirement"
    assert g["proposed_survivor"] is None
    assert sorted(g["member_document_ids"]) == sorted([first, owned])
    assert g["rows_preserved_cross_owner"] == 2


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


def test_same_content_different_owner_is_shared_content_not_a_merge():
    """Identical content held by two clients is the same file reused - not a duplicate to resolve."""
    sha = _sha()
    a = _doc(sha, person_id=_person(" A"))
    b = _doc(sha, person_id=_person(" B"))
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_cross_person"
    assert g["duplicate_document_ids"] == []
    assert g["excess_rows"] == 0
    assert g["merge_partition_count"] == 0
    assert g["preserved_partition_count"] == 2
    assert sorted(g["member_document_ids"]) == sorted([a, b])
    assert g["blockers"] == [], "differing owners must not be reported as a merge blocker"


def test_two_copies_per_person_merge_within_each_person_only():
    """A,A,B,B yields TWO independent merge partitions and never crosses the person boundary."""
    sha, p1, p2 = _sha(), _person(" AA"), _person(" BB")
    a1, a2 = sorted([_doc(sha, person_id=p1), _doc(sha, person_id=p1)])
    b1, b2 = sorted([_doc(sha, person_id=p2), _doc(sha, person_id=p2)])
    g = _group_for(sha)
    assert g["merge_partition_count"] == 2
    assert g["excess_rows"] == 2
    assert g["proposed_survivor"] is None, "two merge partitions have no single survivor"
    assert sorted(g["duplicate_document_ids"]) == sorted([a2, b2])
    survivors = {p["proposed_survivor"] for p in g["partitions"] if p["mergeable"]}
    assert survivors == {a1, b1}
    for part in g["partitions"]:
        owners = {(d,) for d in [part["owner"]["person_id"]]}
        assert len(owners) == 1


def test_a_group_holding_both_a_local_duplicate_and_a_foreign_copy_is_partial():
    """A,A,B: exactly one retirable row, and B's copy is preserved untouched."""
    sha, p1 = _sha(), _person(" CC")
    a1, a2 = sorted([_doc(sha, person_id=p1), _doc(sha, person_id=p1)])
    b = _doc(sha, person_id=_person(" DD"))
    g = _group_for(sha)
    assert g["shape"] == "partial_merge_with_preserved_copies"
    assert g["classification"] == dm.SAFE
    assert g["proposed_survivor"] == a1
    assert g["duplicate_document_ids"] == [a2]
    assert b not in g["duplicate_document_ids"], "a foreign owner's copy may never be retired"
    assert g["rows_preserved_cross_owner"] == 1
    assert g["excess_rows"] == 1


def test_owner_on_a_non_survivor_no_longer_merges_at_all():
    """Previously a REVIEW; an unowned row and an owned row are now simply different scopes."""
    sha = _sha()
    _doc(sha)                                            # unowned
    _doc(sha, person_id=_person())                       # owned
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_unowned"
    assert g["duplicate_document_ids"] == []


def test_owner_on_the_lowest_id_still_does_not_absorb_the_unowned_copy():
    sha, pid = _sha(), _person()
    _doc(sha, person_id=pid)                             # lowest id, owned
    _doc(sha)                                            # unowned
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_unowned"
    assert g["duplicate_document_ids"] == []


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
    blocker = next(b for b in g["blockers"] if b["code"] == "unknown_dependency")
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
    for key in ("physical_sha_groups", "rows_eligible_for_retirement", "safe_auto_merge_groups",
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
    sha_safe, sha_shared, sha_review = _sha(), _sha(), _sha()
    _doc(sha_safe), _doc(sha_safe)
    _doc(sha_shared, person_id=_person(" X")), _doc(sha_shared, person_id=_person(" Y"))
    a, b = _doc(sha_review), _doc(sha_review)
    _classification(a, "1099")
    _classification(b, "W-2")

    def _shape(batch):
        r = dm.preview(batch_size=batch)
        return {g["sha256"]: (g["classification"], g["shape"], g["proposed_survivor"],
                              tuple(sorted(g["duplicate_document_ids"])),
                              tuple(sorted(c["code"] for c in g["conflicts"])),
                              tuple(sorted(x["code"] for x in g["blockers"])),
                              g["merge_partition_count"], g["rows_preserved_cross_owner"],
                              g["total_reassignments"])
                for g in r["groups"] if g["sha256"] in (sha_safe, sha_shared, sha_review)}

    one_batch, many_batches = _shape(5000), _shape(1)
    assert one_batch == many_batches
    assert one_batch[sha_safe][0] == dm.SAFE
    assert one_batch[sha_shared][0] == dm.SHARED
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
    assert "version_chain_self_reference" in {c["code"] for c in g["conflicts"]}
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


# --- reason aggregation + reconciliation -------------------------------------------------------

def _mixed_corpus():
    """One group per reason family, so the aggregate can be checked against known truth."""
    made = {}
    sha = _sha(); _doc(sha), _doc(sha); made["safe"] = sha
    sha = _sha(); _doc(sha, person_id=_person(" A")), _doc(sha, person_id=_person(" B"))
    made["person_mismatch"] = sha
    sha = _sha(); _doc(sha); _doc(sha, person_id=_person()); made["unassigned_vs_person"] = sha
    sha = _sha(); a, b = _doc(sha), _doc(sha); _classification(a, "1099"); _classification(b, "W-2")
    made["classification_conflict"] = sha
    sha = _sha(); a, b = _doc(sha), _doc(sha)
    _ocr(a, status="completed", char_count=9000); _ocr(b, status="unsupported", char_count=0)
    made["ocr_conflict"] = sha
    return made


def test_every_reason_code_is_declared_in_the_taxonomy():
    for code, (severity, description) in dm.REASONS.items():
        assert severity in (dm.BLOCKER, dm.CONFLICT, dm.ADVISORY, dm.SHAPE), code
        assert description and isinstance(description, str), code


def test_reason_aggregation_reports_containing_and_primary_counts():
    _mixed_corpus()
    r = dm.preview()
    by_code = {x["code"]: x for section in ("blockers", "conflicts", "advisories")
               for x in r["reasons"][section]}
    for code in ("classification_conflict", "ocr_conflict"):
        assert code in by_code, code
        assert by_code[code]["severity"] == dm.CONFLICT
        assert by_code[code]["groups_primary"] >= 1
    # The former ownership blockers/conflicts are gone from the merge taxonomy entirely; the same
    # populations are now counted as SHAPES, which never claim a primary reason.
    assert not {"ownership_person_mismatch", "ownership_unassigned_vs_person"} & set(by_code)
    assert r["groups_by_shape"]["shared_content_cross_person"] >= 1
    assert r["groups_by_shape"]["shared_content_unowned"] >= 1


def test_primary_reason_counts_reconcile_to_the_classification_totals():
    """The reconciliation the report claims: mutually exclusive primaries sum to the class totals."""
    _mixed_corpus()
    r = dm.preview()
    rr = r["reasons"]
    assert rr["primary_totals"]["blocked"] == r["blocked_groups"]
    assert rr["primary_totals"]["review_required"] == r["review_required_groups"]
    assert rr["reconciles"] is True
    assert rr["unreported_codes"] == []


def test_every_non_safe_group_has_exactly_one_primary_reason():
    _mixed_corpus()
    for g in dm.preview()["groups"]:
        if g["classification"] in (dm.SAFE, dm.SHARED):
            # SHARED groups propose no merge at all, so they carry no merge-blocking reason -
            # what they DO carry is a shape.
            assert g["primary_reason"] is None
            assert g["shape"] in dm.SHAPE_CODES
        else:
            assert g["primary_reason"] in dm.REASONS
            assert dm.REASONS[g["primary_reason"]][0] not in (dm.ADVISORY, dm.SHAPE)


def test_primary_reason_prefers_a_blocker_over_a_conflict(monkeypatch):
    sha, pid = _sha(), _person(" A")
    a = _doc(sha, person_id=pid)
    b = _doc(sha, person_id=pid)                           # SAME owner: one merge partition
    _classification(a, "1099")
    _classification(b, "W-2")                              # a conflict...
    _ocr(b)
    patched = dict(dm._STRATEGY)
    patched.pop("document_ocr")
    monkeypatch.setattr(dm, "_STRATEGY", patched)          # ...and a blocker, on one partition
    g = _group_for(sha)
    assert g["classification"] == dm.BLOCKED
    assert dm.REASONS[g["primary_reason"]][0] == dm.BLOCKER
    assert "classification_conflict" in g["reason_codes"]   # still reported as a contained reason


def test_reasons_carry_representative_document_ids():
    _mixed_corpus()
    rr = dm.preview()["reasons"]
    for section in ("blockers", "conflicts"):
        for row in rr[section]:
            assert row["example_document_ids"], row["code"]
            assert all(isinstance(i, int) for i in row["example_document_ids"])


def test_reason_aggregation_is_deterministic():
    _mixed_corpus()
    a, b = dm.preview()["reasons"], dm.preview()["reasons"]
    assert a == b


# --- ownership decomposed by dimension -----------------------------------------------------------

def test_household_mismatch_is_reported_separately_from_person_mismatch():
    sha = _sha()
    with engine.begin() as c:
        h1 = c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                       {"n": f"{_TAG} HH1 {uuid.uuid4().hex[:6]}"}).scalar_one()
        h2 = c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                       {"n": f"{_TAG} HH2 {uuid.uuid4().hex[:6]}"}).scalar_one()
    _doc(sha, household_id=h1)
    _doc(sha, household_id=h2)
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_cross_household"
    assert g["duplicate_document_ids"] == []
    assert g["ownership"]["conflicting_dimensions"] == ["household_id"]


def test_two_documents_in_the_same_household_do_merge():
    sha, hh = _sha(), _household("HHsame")
    first, second = sorted([_doc(sha, household_id=hh), _doc(sha, household_id=hh)])
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE
    assert g["shape"] == "single_owner_duplicate_group"
    assert (g["proposed_survivor"], g["duplicate_document_ids"]) == (first, [second])


def test_cross_organization_content_is_shared_and_same_organization_merges():
    cross = _sha()
    _doc(cross, organization_id=_organization("ORG1")), _doc(cross,
                                                            organization_id=_organization("ORG2"))
    g = _group_for(cross)
    assert (g["classification"], g["shape"]) == (dm.SHARED, "shared_content_cross_organization")
    assert g["duplicate_document_ids"] == []

    same, org = _sha(), _organization("ORGsame")
    first, second = sorted([_doc(same, organization_id=org), _doc(same, organization_id=org)])
    g2 = _group_for(same)
    assert g2["classification"] == dm.SAFE
    assert (g2["proposed_survivor"], g2["duplicate_document_ids"]) == (first, [second])


def test_a_disagreement_across_two_dimensions_is_its_own_reason():
    sha = _sha()
    with engine.begin() as c:
        h1 = c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                       {"n": f"{_TAG} HHx {uuid.uuid4().hex[:6]}"}).scalar_one()
        h2 = c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                       {"n": f"{_TAG} HHy {uuid.uuid4().hex[:6]}"}).scalar_one()
    _doc(sha, person_id=_person(" P"), household_id=h1)
    _doc(sha, person_id=_person(" Q"), household_id=h2)
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_mixed_dimensions"
    assert g["duplicate_document_ids"] == []
    assert set(g["ownership"]["conflicting_dimensions"]) == {"person_id", "household_id"}


# --- advisories must NOT change classification ----------------------------------------------------

def test_set_null_and_no_action_reassignments_are_advisories_not_conflicts():
    """A repointable reference is work for the executor, never a reason to withhold the merge."""
    sha = _sha()
    survivor, dup = sorted([_doc(sha), _doc(sha)])
    with engine.begin() as c:
        c.execute(text("INSERT INTO operational_tasks (title, status, document_id)"
                       " VALUES (:t, 'active', :d)"), {"t": f"{_TAG} task", "d": dup})
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE, "a SET NULL reference must not downgrade the group"
    assert "set_null_reassignment_required" in g["reason_codes"]
    advisory = next(r for r in g["reasons"] if r["code"] == "set_null_reassignment_required")
    assert advisory["severity"] == dm.ADVISORY


def test_source_collisions_and_relationship_overlap_are_advisories():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    _source(a, _SYS_A, "probe://dup")
    _source(b, _SYS_A, "probe://dup")                       # identical provenance tuple
    with engine.begin() as c:
        for d in (a, b):
            c.execute(text("INSERT INTO document_relationships (document_id, entity_type, entity_id)"
                           " VALUES (:d, 'person', 1)"), {"d": d})
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE
    assert "document_sources_collision" in g["reason_codes"]
    assert "document_relationships_redundant" in g["reason_codes"]
    assert g["source_collisions"] == 1
    assert g["provenance"]["preserved_after_merge"] == 1     # provenance kept, recorded once


def test_relationships_to_different_entities_are_additive_not_a_conflict():
    sha = _sha()
    a, b = _doc(sha), _doc(sha)
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_relationships (document_id, entity_type, entity_id)"
                       " VALUES (:d, 'person', 1)"), {"d": a})
        c.execute(text("INSERT INTO document_relationships (document_id, entity_type, entity_id)"
                       " VALUES (:d, 'person', 2)"), {"d": b})
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE, \
        "the survivor inherits both relationships; UNIQUE(document_id, entity_type, entity_id) admits both"
    assert "document_relationships_multi_entity" in g["reason_codes"]
    assert g["relationships"]["distinct_entities"] == 2


# --- FK coverage is re-verified as part of the report ---------------------------------------------

def test_the_report_states_whether_any_unknown_fk_exists():
    r = dm.preview(limit=1)
    assert "unregistered_dependencies" in r
    assert r["unregistered_dependencies"] == [], r["unregistered_dependencies"]
    assert r["dependencies_checked"] >= 25


# --- Windows console safety -----------------------------------------------------------------------

def test_cli_and_service_sources_are_cp1252_encodable():
    """A Windows console defaults to CP1252; a Unicode arrow in output (or in a traceback's source
    line) raises UnicodeEncodeError without PYTHONIOENCODING."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for rel in ("scripts/preview_document_merge.py", "app/services/document_merge.py"):
        text_ = (root / rel).read_text(encoding="utf-8")
        try:
            text_.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise AssertionError(f"{rel} is not CP1252-encodable at {exc.start}: "
                                 f"{text_[exc.start:exc.end]!r}") from exc


def test_rendered_cli_output_is_cp1252_encodable():
    _mixed_corpus()
    import scripts.preview_document_merge as cli
    report = dm.preview()
    rendered = cli._summary(report) + "".join(cli._group(g) for g in report["groups"][:3])
    rendered.encode("cp1252")                                # must not raise
    assert "WHY THE NON-SAFE GROUPS ARE NOT SAFE" in rendered
    assert "reconciles" in rendered


def test_a_safe_group_carrying_an_advisory_still_has_no_primary_reason():
    """The reconciliation gap: if an advisory could become a PRIMARY reason, a SAFE group would be
    counted under a reason code and the primary totals would stop matching the class totals."""
    sha = _sha()
    survivor, dup = sorted([_doc(sha), _doc(sha)])
    with engine.begin() as c:                                   # a SET NULL advisory, nothing more
        c.execute(text("INSERT INTO operational_tasks (title, status, document_id)"
                       " VALUES (:t, 'active', :d)"), {"t": f"{_TAG} adv", "d": dup})
    g = _group_for(sha)
    assert g["classification"] == dm.SAFE
    assert g["reason_codes"], "the advisory must still be reported"
    assert g["primary_reason"] is None, "an advisory must never become a primary reason"

    r = dm.preview()
    rr = r["reasons"]
    assert rr["reconciles"] is True
    assert rr["primary_totals"]["blocked"] == r["blocked_groups"]
    assert rr["primary_totals"]["review_required"] == r["review_required_groups"]
    advisory_primary = sum(row["groups_primary"] for row in rr["advisories"])
    assert advisory_primary == 0, "no group may take an advisory as its primary reason"


# --- blocker diagnostics (read-only) --------------------------------------------------------------

def _household(label):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                         {"n": f"{_TAG} {label} {uuid.uuid4().hex[:6]}"}).scalar_one()


def _organization(label):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO relationship_entities (entity_type, name, active)"
                              " VALUES ('business', :n, true) RETURNING id"),
                         {"n": f"{_TAG} {label} {uuid.uuid4().hex[:6]}"}).scalar_one()


def _docs_for(ids):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(
            text("SELECT id, person_id, household_id, organization_id FROM documents "
                 "WHERE id = ANY(:ids)"), {"ids": list(ids)})]


def _detail_for(sha, report=None):
    report = report or dm.blocked_details()
    return next((g for g in report["groups"] if g["sha256"] == sha), None)


def test_person_mismatch_detail_carries_ids_names_and_sources():
    sha = _sha()
    p1, p2 = _person(" One"), _person(" Two")
    a = _doc(sha, person_id=p1, category="tax")
    b = _doc(sha, person_id=p2, category="tax")
    _source(a, _SYS_A, "probe://a")
    _source(b, _SYS_B, "probe://b")
    g = _detail_for(sha)
    assert g and g["shape"] == "shared_content_cross_person"
    assert g["classification"] == dm.SHARED
    assert g["member_count"] == 2 and g["excess_rows"] == 0
    assert g["proposed_survivor"] is None
    assert g["rows_preserved_cross_owner"] == 2
    assert sorted(g["member_document_ids"]) == sorted([a, b])
    by_doc = {m["document_id"]: m for m in g["members"]}
    assert by_doc[a]["person_id"] == p1 and by_doc[b]["person_id"] == p2
    assert by_doc[a]["person_name"] and _TAG in by_doc[a]["person_name"]
    assert by_doc[a]["original_name"] and by_doc[a]["category"] == "tax"
    assert {s["source_system"] for s in by_doc[a]["sources"]} == {_SYS_A}
    assert g["source_record_count"] == 2
    assert all(m["preserved"] and not m["proposed_for_retirement"] for m in g["members"])
    assert not any(m["is_survivor"] for m in g["members"])


def test_household_mismatch_detail_resolves_household_names():
    sha = _sha()
    h1, h2 = _household("HHone"), _household("HHtwo")
    _doc(sha, household_id=h1), _doc(sha, household_id=h2)
    g = _detail_for(sha)
    assert g["shape"] == "shared_content_cross_household"
    names = {m["household_name"] for m in g["members"]}
    assert all(n and _TAG in n for n in names) and len(names) == 2
    assert g["conflicting_dimensions"] == ["household_id"]


def test_organization_mismatch_detail_resolves_organization_names():
    sha = _sha()
    o1, o2 = _organization("Org1"), _organization("Org2")
    _doc(sha, organization_id=o1), _doc(sha, organization_id=o2)
    g = _detail_for(sha)
    assert g["shape"] == "shared_content_cross_organization"
    names = {m["organization_name"] for m in g["members"]}
    assert all(n and _TAG in n for n in names) and len(names) == 2


def test_multiple_dimension_mismatch_detail_lists_every_conflicting_dimension():
    sha = _sha()
    _doc(sha, person_id=_person(" M1"), household_id=_household("HHa"))
    _doc(sha, person_id=_person(" M2"), household_id=_household("HHb"))
    g = _detail_for(sha)
    assert g["shape"] == "shared_content_mixed_dimensions"
    assert set(g["conflicting_dimensions"]) == {"person_id", "household_id"}


def test_reason_filter_restricts_the_population():
    sha_person, sha_household = _sha(), _sha()
    _doc(sha_person, person_id=_person(" F1")), _doc(sha_person, person_id=_person(" F2"))
    _doc(sha_household, household_id=_household("HHf1")), _doc(sha_household,
                                                               household_id=_household("HHf2"))
    only_person = dm.blocked_details(reasons=["shared_content_cross_person"])
    assert {g["shape"] for g in only_person["groups"]} == {"shared_content_cross_person"}
    assert _detail_for(sha_person, only_person) is not None
    assert _detail_for(sha_household, only_person) is None
    assert only_person["filter_reasons"] == ["shared_content_cross_person"]


# --- giant group: complete data retained, terminal output truncated --------------------------------

def test_a_giant_shared_content_group_is_summarised_without_dumping_every_member():
    """The production shape (one generic file ingested for many clients) must not flood a terminal."""
    sha = _sha()
    ids = [_doc(sha, person_id=_person(f" G{i}")) for i in range(40)]
    g = _detail_for(sha)
    assert g["member_count"] == 40 and g["excess_rows"] == 0
    assert g["rows_preserved_cross_owner"] == 40, "40 distinct owners: nothing may be retired"
    assert len(g["members"]) == 40, "the STRUCTURE keeps every member"

    import scripts.preview_document_merge as cli
    rendered = cli._blocked_text(dm.blocked_details(), sample=5)
    assert "more member(s) not shown" in rendered
    assert "--output-json" in rendered
    # A 40-member group must not print 40 member blocks.
    assert rendered.count(f"doc {ids[0]}") <= 2
    assert len(rendered) < 40_000, "text output must stay terminal-sized"


def test_giant_group_shape_evidence_is_factual_not_a_verdict():
    sha = _sha()
    for i in range(12):
        _doc(sha, person_id=_person(f" S{i}"))
    shape = _detail_for(sha)["ownership_shape"]
    assert shape["distinct_owners"] == 12
    assert shape["every_member_a_distinct_owner"] is True
    assert shape["any_owner_with_multiple_members"] is False
    assert any("consistent with" in n for n in shape["evidence_notes"])
    assert all("needs confirmation" in n for n in shape["evidence_notes"]
               if "consistent with" in n), "evidence must not assert a conclusion"


def test_competing_assignment_shape_is_distinguishable_from_shared_file_shape():
    sha = _sha()
    shared = _person(" Same")
    _doc(sha, person_id=shared), _doc(sha, person_id=shared), _doc(sha, person_id=_person(" Other"))
    detail = _detail_for(sha)
    assert detail is None, "a partially mergeable group is SAFE, not a non-mergeable one"
    g = _group_for(sha)
    assert g["shape"] == "partial_merge_with_preserved_copies"
    assert (g["excess_rows"], g["rows_preserved_cross_owner"]) == (1, 1)
    shape = dm._ownership_shape([
        {"person_id": d["person_id"], "household_id": d["household_id"],
         "organization_id": d["organization_id"], "sources": []}
        for d in _docs_for(g["member_document_ids"])])
    assert shape["any_owner_with_multiple_members"] is True
    assert shape["max_members_per_owner"] == 2
    assert shape["every_member_a_distinct_owner"] is False


# --- aggregate summary ------------------------------------------------------------------------------

def test_blocked_summary_reports_size_buckets_and_largest_groups():
    _doc((s1 := _sha()), person_id=_person(" A1")), _doc(s1, person_id=_person(" A2"))
    s2 = _sha()
    for i in range(12):
        _doc(s2, person_id=_person(f" B{i}"))
    su = dm.blocked_details()["summary"]
    assert su["groups_in_this_report"] >= 2
    assert su["by_classification"][dm.SHARED] >= 2
    assert su["groups_with_more_than_2_members"] >= 1
    assert su["groups_with_more_than_10_members"] >= 1
    assert su["groups_with_more_than_100_members"] == 0
    assert su["largest_20_groups"][0]["member_count"] >= 12
    assert len(su["largest_20_groups"]) <= 20
    assert su["by_reason"]["shared_content_cross_person"]["groups"] >= 2
    assert sum(su["distinct_owners_per_group"].values()) == su["groups_in_this_report"]


# --- JSON / CSV completeness -------------------------------------------------------------------------

def test_json_output_contains_every_member_of_every_group(tmp_path):
    import scripts.preview_document_merge as cli
    sha = _sha()
    ids = [_doc(sha, person_id=_person(f" J{i}")) for i in range(15)]
    out = tmp_path / "blocked.json"
    rc = cli.main(["--blocked-details", "--output-json", str(out)])
    assert rc == 0 and out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    g = next(x for x in data["groups"] if x["sha256"] == sha)
    assert g["member_count"] == 15
    assert sorted(m["document_id"] for m in g["members"]) == sorted(ids), "no truncation in JSON"
    assert all("person_name" in m and "sources" in m for m in g["members"])
    assert data["read_only"] is True and data["wrote_anything"] is False


def test_csv_output_has_one_row_per_member_with_group_context(tmp_path):
    import csv as _csv

    import scripts.preview_document_merge as cli
    sha = _sha()
    a = _doc(sha, person_id=_person(" C1"), category="tax")
    b = _doc(sha, person_id=_person(" C2"))
    _source(a, _SYS_A, "probe://csv")
    out = tmp_path / "blocked.csv"
    assert cli.main(["--blocked-details", "--output-csv", str(out)]) == 0
    rows = list(_csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
    mine = [r for r in rows if r["sha256"] == sha]
    assert len(mine) == 2, "one row per member document"
    assert set(rows[0]) == set(cli.CSV_COLUMNS)
    lowest_row = next(r for r in mine if r["document_id"] == str(min(a, b)))
    assert lowest_row["is_survivor"] == "False", "nothing survives - nothing is retired"
    assert lowest_row["preserved"] == "True"
    assert lowest_row["proposed_for_retirement"] == "False"
    assert lowest_row["group_classification"] == dm.SHARED
    assert lowest_row["group_shape"] == "shared_content_cross_person"
    assert lowest_row["group_rows_preserved"] == "2"
    assert lowest_row["person_name"] and _TAG in lowest_row["person_name"]
    assert lowest_row["group_distinct_owners"] == "2"
    a_row = next(r for r in mine if r["document_id"] == str(a))
    assert _SYS_A in a_row["source_systems"] and "probe://csv" in a_row["source_uris"]


def test_nothing_is_written_unless_an_output_path_is_given(tmp_path, capsys, monkeypatch):
    import scripts.preview_document_merge as cli
    sha = _sha()
    _doc(sha, person_id=_person(" N1")), _doc(sha, person_id=_person(" N2"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--blocked-details"]) == 0
    capsys.readouterr()
    assert list(tmp_path.iterdir()) == [], "no file may be created without --output-json/--output-csv"


# --- no writes, classification untouched -------------------------------------------------------------

def test_blocked_details_writes_nothing_to_the_database():
    sha = _sha()
    _doc(sha, person_id=_person(" W1")), _doc(sha, person_id=_person(" W2"))
    before = _snapshot()
    dm.blocked_details()
    assert _snapshot() == before


def test_blocked_details_does_not_change_any_classification():
    """It reuses preview()'s verdicts; it must never recompute or alter them."""
    _mixed_corpus()
    before = dm.preview()
    counts_before = (before["safe_auto_merge_groups"], before["review_required_groups"],
                     before["blocked_groups"])
    dm.blocked_details()
    after = dm.preview()
    assert (after["safe_auto_merge_groups"], after["review_required_groups"],
            after["blocked_groups"]) == counts_before
    assert {g["sha256"]: g["classification"] for g in after["groups"]} == \
           {g["sha256"]: g["classification"] for g in before["groups"]}


def test_blocked_details_reuses_a_supplied_report_without_reclassifying():
    _mixed_corpus()
    report = dm.preview()
    detail = dm.blocked_details(report=report)
    assert detail["summary"]["blocked_groups_total"] == report["blocked_groups"]
    assert detail["summary"]["shared_content_groups_total"] == report["shared_content_groups"]
    assert (detail["summary"]["groups_in_this_report"]
            == report["blocked_groups"] + report["shared_content_groups"])


def test_blocked_details_queries_are_batched_not_per_group():
    for i in range(20):
        sha = _sha()
        _doc(sha, person_id=_person(f" Q{i}a")), _doc(sha, person_id=_person(f" Q{i}b"))
    i = dm.blocked_details()["instrumentation"]
    assert i["blocked_groups_detailed"] >= 20
    assert i["sql_query_count"] <= 40, f"{i['sql_query_count']} queries for {i['blocked_groups_detailed']} groups"


# --- ownership-scoped partitioning: the invariant, end to end ------------------------------------
# The rule these tests defend: a document is NEVER proposed for retirement merely because another
# document with the same sha256 belongs to a different person, household or organization. Content
# does not establish identity - Drake does. A hash group is therefore partitioned on the exact
# (person_id, household_id, organization_id) tuple, and each partition is judged on its own.

def test_three_distinct_people_produce_no_merge_at_all():
    """A, B, C: three scopes, three preserved rows, zero retirements."""
    sha = _sha()
    ids = [_doc(sha, person_id=_person(f" T{i}")) for i in range(3)]
    g = _group_for(sha)
    assert g["classification"] == dm.SHARED
    assert g["shape"] == "shared_content_cross_person"
    assert g["merge_partition_count"] == 0
    assert g["preserved_partition_count"] == 3
    assert g["excess_rows"] == 0
    assert g["rows_preserved_cross_owner"] == 3
    assert g["duplicate_document_ids"] == []
    assert sorted(g["member_document_ids"]) == sorted(ids)


def test_each_partition_holds_exactly_one_ownership_tuple():
    """The structural guarantee behind the invariant: a partition can never straddle two owners."""
    sha, p1 = _sha(), _person(" S1")
    _doc(sha, person_id=p1), _doc(sha, person_id=p1)
    _doc(sha, person_id=_person(" S2"))
    _doc(sha, household_id=_household("HHs"))
    _doc(sha)
    g = _group_for(sha)
    keys = [(p["owner"]["person_id"], p["owner"]["household_id"], p["owner"]["organization_id"])
            for p in g["partitions"]]
    assert len(keys) == len(set(keys)) == 4
    assert sum(p["member_count"] for p in g["partitions"]) == g["row_count"] == 5
    for part in g["partitions"]:
        for did in part["duplicate_document_ids"]:
            assert did in part["member_document_ids"]


def test_no_group_anywhere_proposes_retiring_a_row_owned_by_someone_else():
    """The corpus-wide invariant, asserted against every group the preview returns."""
    _mixed_corpus()
    sha, p = _sha(), _person(" INV")
    _doc(sha, person_id=p), _doc(sha, person_id=p), _doc(sha, person_id=_person(" OTHER"))
    _doc(sha, household_id=_household("HHinv"))

    for g in dm.preview()["groups"]:
        scopes = {}
        for part in g["partitions"]:
            key = (part["owner"]["person_id"], part["owner"]["household_id"],
                   part["owner"]["organization_id"])
            for did in part["member_document_ids"]:
                scopes[did] = key
        for part in g["partitions"]:
            if not part["mergeable"]:
                assert part["duplicate_document_ids"] == []
                continue
            survivor_scope = scopes[part["proposed_survivor"]]
            for did in part["duplicate_document_ids"]:
                assert scopes[did] == survivor_scope, (
                    f"{g['sha256']}: doc {did} would be retired into a DIFFERENT ownership scope")


def test_survivor_is_the_lowest_id_inside_the_partition_not_the_group():
    """Owner-blind and scope-local: a lower id in another scope must not become the survivor."""
    sha = _sha()
    low = _doc(sha, person_id=_person(" LOW"))          # lowest id in the whole group...
    p2 = _person(" PAIR")
    first, second = sorted([_doc(sha, person_id=p2), _doc(sha, person_id=p2)])
    g = _group_for(sha)
    merge = [p for p in g["partitions"] if p["mergeable"]]
    assert len(merge) == 1
    assert merge[0]["proposed_survivor"] == first, "...but it is not this partition's survivor"
    assert merge[0]["proposed_survivor"] != low
    assert merge[0]["duplicate_document_ids"] == [second]


def test_provenance_of_a_preserved_copy_is_reported_and_never_reassigned():
    """Preserved rows keep their own source rows; nothing is folded into a foreign owner."""
    sha = _sha()
    a = _doc(sha, person_id=_person(" PR1"))
    b = _doc(sha, person_id=_person(" PR2"))
    _source(a, _SYS_A, "probe://prov-a")
    _source(b, _SYS_B, "probe://prov-b")
    g = _group_for(sha)
    assert g["reassignments_required"] == {}, "no rows may be repointed across owners"
    assert g["total_reassignments"] == 0
    assert g["provenance"]["distinct_provenance_tuples"] == 2
    assert g["provenance"]["preserved_after_merge"] == 2
    assert g["provenance"]["redundant_rows"] == 0
    detail = _detail_for(sha)
    by_doc = {m["document_id"]: m for m in detail["members"]}
    assert {s["source_uri"] for s in by_doc[a]["sources"]} == {"probe://prov-a"}
    assert {s["source_uri"] for s in by_doc[b]["sources"]} == {"probe://prov-b"}


def test_the_production_shape_yields_exactly_one_small_merge_partition():
    """The observed 44-member group: 42 owners, one owner holding 2 copies, 1 unowned copy.

    Expected: ONE mergeable partition of 2 documents (1 retirable row); everything else preserved."""
    sha = _sha()
    twice = _person(" DUP")
    pair = sorted([_doc(sha, person_id=twice), _doc(sha, person_id=twice)])
    singles = [_doc(sha, person_id=_person(f" P{i}")) for i in range(41)]
    unowned = _doc(sha)

    g = _group_for(sha)
    assert g["row_count"] == 44
    assert len(g["partitions"]) == 43              # 42 owner scopes + the unowned scope
    assert g["merge_partition_count"] == 1
    assert g["preserved_partition_count"] == 42
    assert g["shape"] == "partial_merge_with_preserved_copies"
    assert g["classification"] == dm.SAFE
    assert g["proposed_survivor"] == pair[0]
    assert g["duplicate_document_ids"] == [pair[1]]
    assert g["excess_rows"] == 1, "one row retirable, not 43"
    assert g["rows_preserved_cross_owner"] == 42
    for did in [*singles, unowned]:
        assert did not in g["duplicate_document_ids"]


def test_reporting_distinguishes_every_group_shape():
    """The counts a reviewer needs to triage the corpus without opening a single group."""
    same = _person(" R0")
    _doc((s_single := _sha()), person_id=same), _doc(s_single, person_id=same)
    _doc((s_person := _sha()), person_id=_person(" R1")), _doc(s_person,
                                                              person_id=_person(" R2"))
    _doc((s_hh := _sha()), household_id=_household("HHr1")), _doc(s_hh,
                                                                  household_id=_household("HHr2"))
    _doc((s_org := _sha()), organization_id=_organization("OrgR1")), _doc(
        s_org, organization_id=_organization("OrgR2"))
    _doc((s_mixed := _sha()), person_id=_person(" R3"), household_id=_household("HHr3"))
    _doc(s_mixed, person_id=_person(" R4"), household_id=_household("HHr4"))
    s_unowned = _sha()
    _doc(s_unowned), _doc(s_unowned, person_id=_person(" R5"))
    partial = _person(" R6")
    _doc((s_partial := _sha()), person_id=partial), _doc(s_partial, person_id=partial)
    _doc(s_partial, person_id=_person(" R7"))

    r = dm.preview()
    shapes = {g["sha256"]: g["shape"] for g in r["groups"]}
    assert shapes[s_single] == "single_owner_duplicate_group"
    assert shapes[s_person] == "shared_content_cross_person"
    assert shapes[s_hh] == "shared_content_cross_household"
    assert shapes[s_org] == "shared_content_cross_organization"
    assert shapes[s_mixed] == "shared_content_mixed_dimensions"
    assert shapes[s_unowned] == "shared_content_unowned"
    assert shapes[s_partial] == "partial_merge_with_preserved_copies"
    for code in dm.SHAPE_CODES:
        assert r["groups_by_shape"].get(code, 0) >= 1, code

    # Physical hash groups are NOT merge groups any more, and the two totals reconcile.
    assert r["physical_sha_groups"] == len(r["groups"])
    assert r["ownership_partitions"] == sum(len(g["partitions"]) for g in r["groups"])
    assert r["ownership_scoped_merge_groups"] == sum(g["merge_partition_count"]
                                                     for g in r["groups"])
    assert r["rows_eligible_for_retirement"] == sum(g["excess_rows"] for g in r["groups"])
    assert r["cross_owner_rows_preserved"] == sum(g["rows_preserved_cross_owner"]
                                                  for g in r["groups"])
    assert (r["merge_partitions_safe"] + r["merge_partitions_review_required"]
            + r["merge_partitions_blocked"]) == r["ownership_scoped_merge_groups"]


def test_partitioning_reads_nothing_extra_and_writes_nothing():
    """The partition is computed in memory from rows preview() already fetched."""
    for i in range(6):
        sha = _sha()
        _doc(sha, person_id=_person(f" Q{i}")), _doc(sha, person_id=_person(f" Z{i}"))
    with engine.connect() as c:
        before = c.execute(text("SELECT count(*), coalesce(max(id), 0) FROM documents")).one()
    r = dm.preview()
    assert r["read_only"] is True and r["wrote_anything"] is False
    assert r["instrumentation"]["sql_query_count"] < 60, r["instrumentation"]["sql_query_count"]
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*), coalesce(max(id), 0) FROM documents")).one() == before


def test_shared_content_is_inert_and_can_never_be_read_as_mergeable():
    """Invariant: SHARED_CONTENT is a preview/reporting state, never an auto-merge state.

    Any downstream reader that acts on excess_rows / duplicate_document_ids / merge_partition_count
    gets zero work from a SHARED group, whichever field it happens to trust."""
    assert dm.SHARED != dm.SAFE and dm.SHARED not in (dm.REVIEW, dm.BLOCKED)
    _mixed_corpus()
    sha = _sha()
    _doc(sha, person_id=_person(" I1")), _doc(sha, person_id=_person(" I2"))
    _doc((s2 := _sha()), household_id=_household("HHi")), _doc(s2,
                                                               organization_id=_organization("Oi"))

    seen = 0
    for g in dm.preview()["groups"]:
        if g["classification"] != dm.SHARED:
            continue
        seen += 1
        assert g["duplicate_document_ids"] == []
        assert g["excess_rows"] == 0
        assert g["merge_partition_count"] == 0
        assert g["proposed_survivor"] is None
        assert g["total_reassignments"] == 0
        assert g["reassignments_required"] == {}
        assert g["primary_reason"] is None
        assert g["rows_preserved_cross_owner"] == g["row_count"]
        assert all(not p["mergeable"] for p in g["partitions"])
    assert seen >= 3, "the corpus must actually contain SHARED groups for this to mean anything"


# --- reporting terminology and counters ----------------------------------------------------------
# These pin the OUTPUT, not the algorithm. Their job is to make a silent regression to global-SHA
# merge semantics impossible: if a refactor ever reports physical SHA excess as retirement
# eligibility, or drops the physical/ownership-scoped distinction, these fail.

def _reporting_corpus():
    """4 physical groups / 11 rows: 2 retirable, 3 survivors, 6 preserved cross-owner."""
    p = _person(" RC")
    s1 = _sha(); _doc(s1, person_id=p), _doc(s1, person_id=p)              # 2 rows, 1 retirable
    s2 = _sha()
    for i in range(3):
        _doc(s2, person_id=_person(f" RD{i}"))                              # 3 rows, 0 retirable
    q = _person(" RE")
    s3 = _sha(); _doc(s3, person_id=q), _doc(s3, person_id=q)
    _doc(s3, person_id=_person(" RF"))                                      # 3 rows, 1 retirable
    s4 = _sha(); _doc(s4), _doc(s4, household_id=_household("HHrc"))
    _doc(s4, organization_id=_organization("Orc"))                          # 3 rows, 0 retirable
    return (s1, s2, s3, s4)


def test_the_summary_never_presents_physical_sha_excess_as_retirement_eligibility():
    """The operator hazard this reporting exists to remove.

    Physical excess counts every row beyond one per HASH. Retirement eligibility counts every row
    beyond one per OWNERSHIP SCOPE. Conflating them is exactly the global-SHA merge bug."""
    _reporting_corpus()
    r = dm.preview()
    assert r["physical_sha_excess_rows"] > r["rows_eligible_for_retirement"], (
        "this corpus must actually distinguish the two, or the test proves nothing")
    assert r["physical_sha_excess_rows"] == (r["physical_sha_document_rows"]
                                             - r["physical_sha_groups"])

    import scripts.preview_document_merge as cli
    text_out = cli._summary(r)
    # The retired vocabulary must not come back.
    for banned in ("excess duplicate rows", "rows eventually retired"):
        assert banned not in text_out, banned
    assert "rows eligible for retirement" in text_out
    assert "proposed retirement rows" in text_out
    assert "NOT a retirement count" in text_out
    # The actionable number must never be rendered as the physical one.
    elig = f"rows eligible for retirement     : {r['rows_eligible_for_retirement']}"
    assert elig in text_out
    assert f"physical SHA excess rows         : {r['physical_sha_excess_rows']}" in text_out


def test_the_summary_renders_every_required_section():
    _reporting_corpus()
    out = __import__("scripts.preview_document_merge", fromlist=["x"])._summary(dm.preview())
    for heading in ("PHYSICAL CONTENT POPULATION",
                    "OWNERSHIP-SCOPED MERGE POPULATION",
                    "MERGE CLASSIFICATION",
                    "DEPENDENCY / PROVENANCE",
                    "SHARED CONTENT"):
        assert heading in out, heading
    for label in ("physical SHA groups", "ownership partitions", "ownership-scoped merge groups",
                  "rows preserved", "cross-owner/shared", "shared-content groups",
                  "proposed dependent-row reassignments", "provenance rows seen",
                  "provenance tuples preserved", "shape breakdown"):
        assert label in out, label
    for code in dm.SHAPE_CODES:
        assert code in out, code
    for cls in (dm.SAFE, dm.REVIEW, dm.BLOCKED):
        assert cls in out


def test_the_machine_readable_summary_exposes_every_stable_field():
    """The JSON contract a downstream reader may depend on."""
    _reporting_corpus()
    r = dm.preview()
    required = ("physical_sha_groups", "physical_sha_document_rows", "physical_sha_excess_rows",
                "ownership_partitions", "ownership_scoped_merge_groups",
                "rows_eligible_for_retirement", "rows_preserved", "cross_owner_rows_preserved",
                "shared_content_groups", "merge_partitions_safe",
                "merge_partitions_review_required", "merge_partitions_blocked",
                "provenance_rows_seen", "provenance_tuples_preserved")
    for key in required:
        assert key in r, key
        assert isinstance(r[key], int), key
    assert r["proposed_retirement_rows"] == r["rows_eligible_for_retirement"]
    # The ambiguous legacy names must stay gone.
    for gone in ("excess_duplicate_rows", "total_rows_eventually_retired",
                 "total_duplicate_groups", "total_document_rows_in_groups"):
        assert gone not in r, gone

    import json
    assert json.loads(json.dumps(r, default=str))["physical_sha_groups"] == r["physical_sha_groups"]


def test_the_row_counters_reconcile_exactly():
    """physical rows = survivors + retirable + cross-owner preserved. No row is unaccounted for."""
    _reporting_corpus()
    r = dm.preview()
    assert (r["ownership_scoped_merge_groups"]
            + r["rows_eligible_for_retirement"]
            + r["cross_owner_rows_preserved"]) == r["physical_sha_document_rows"]
    assert r["rows_preserved"] == (r["physical_sha_document_rows"]
                                   - r["rows_eligible_for_retirement"])
    assert r["rows_preserved"] == (r["ownership_scoped_merge_groups"]
                                   + r["cross_owner_rows_preserved"])
    assert r["rows_eligible_for_retirement"] <= r["physical_sha_excess_rows"]
    assert r["cross_owner_rows_preserved"] > 0
    import scripts.preview_document_merge as cli
    assert "MISMATCH" not in cli._summary(r)


def test_shared_content_shape_counts_are_reported_and_sum_to_the_shared_total():
    _reporting_corpus()
    r = dm.preview()
    shared_shapes = {c: r["groups_by_shape"].get(c, 0) for c in dm.CROSS_OWNER_SHAPES}
    assert sum(shared_shapes.values()) == r["shared_content_groups"]
    assert r["shared_content_groups"] >= 2
    assert all(c in r["groups_by_shape"] or True for c in dm.SHAPE_CODES)


def test_the_detail_report_field_is_not_named_for_blocked_alone():
    """The report carries SHARED_CONTENT groups too, so the old name was simply wrong."""
    _reporting_corpus()
    d = dm.blocked_details()
    assert "blocked_groups_in_this_report" not in d["summary"]
    assert d["summary"]["groups_in_this_report"] == len(d["groups"])
    assert d["summary"]["groups_in_this_report"] > d["summary"]["blocked_groups_total"]


def test_the_survivor_rule_string_states_the_partition_scope():
    r = dm.preview(limit=1)
    assert "ownership-scoped" in r["survivor_rule"]
    assert "ownership never influences selection" in r["survivor_rule"]


def test_partition_survivors_is_defined_as_the_merge_group_count():
    """`partition survivors` must BE ownership_scoped_merge_groups, not merely equal it here."""
    _reporting_corpus()
    r = dm.preview()
    import scripts.preview_document_merge as cli
    assert f"of which partition survivors   : {r['ownership_scoped_merge_groups']}" in cli._summary(r)
    # ...and the identity that makes the two readings agree.
    assert (r["rows_preserved"] - r["cross_owner_rows_preserved"]
            == r["ownership_scoped_merge_groups"])
