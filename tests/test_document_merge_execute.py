"""Guarded document-merge executor.

The property every test here defends: a document is retired ONLY when another document with the
SAME sha256 AND the SAME exact (person_id, household_id, organization_id) tuple survives it. A
matching hash alone must never retire anything - content does not establish client identity.
"""
import json
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from app.services import document_merge as dm
from app.services import document_merge_execute as dx

_TAG = "DMXTEST"
_SYS_A, _SYS_B = "DmxSystemA", "DmxSystemB"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        c.execute(text("DELETE FROM document_sources WHERE source_system IN (:a, :b)"),
                  {"a": _SYS_A, "b": _SYS_B})
        c.execute(text("DELETE FROM documents WHERE original_name LIKE :p"), {"p": f"{_TAG}%"})
        c.execute(text("DELETE FROM people WHERE full_name LIKE :p"), {"p": f"{_TAG}%"})
        c.execute(text("DELETE FROM households WHERE name LIKE :p"), {"p": f"{_TAG}%"})
        c.execute(text("DELETE FROM relationship_entities WHERE name LIKE :p"), {"p": f"{_TAG}%"})


# --- fixtures ------------------------------------------------------------------------------------

def _sha():
    return uuid.uuid4().hex + uuid.uuid4().hex


def _person(s=""):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO people (full_name) VALUES (:n) RETURNING id"),
                         {"n": f"{_TAG}{s} {uuid.uuid4().hex[:8]}"}).scalar_one()


def _household(s=""):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                         {"n": f"{_TAG}{s} {uuid.uuid4().hex[:8]}"}).scalar_one()


def _org(s=""):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO relationship_entities (entity_type, name, active)"
                              " VALUES ('business', :n, true) RETURNING id"),
                         {"n": f"{_TAG}{s} {uuid.uuid4().hex[:8]}"}).scalar_one()


def _doc(sha, *, person_id=None, household_id=None, organization_id=None, category=None,
         status="active"):
    t = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO documents (original_name, stored_name, storage_path, storage_uri,"
            " size_bytes, sha256, status, archived, person_id, household_id, organization_id,"
            " category) VALUES (:n,:s,:p,:u,1,:sha,:st,false,:pid,:hid,:oid,:cat) RETURNING id"),
            {"n": f"{_TAG} {t}.pdf", "s": f"dmx-{t}", "p": f"/store/{t}", "u": f"file:///{t}",
             "sha": sha, "st": status, "pid": person_id, "hid": household_id,
             "oid": organization_id, "cat": category}).scalar_one()


def _source(d, system, uri):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri,"
                       " source_path) VALUES (:d,:s,:u,:p)"),
                  {"d": d, "s": system, "u": uri, "p": f"/p/{uri}"})


def _ocr(d, *, status="completed", chars=100):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count)"
                       " VALUES (:d,:s,'t',:n)"), {"d": d, "s": status, "n": chars})


def _classification(d, doc_type):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_classifications (document_id, doc_type,"
                       " classifier_version) VALUES (:d,:t,'test-1')"), {"d": d, "t": doc_type})


def _fact(d, ftype, value):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_facts (document_id, fact_type, fact_value,"
                       " extraction_engine) VALUES (:d,:t,:v,'test')"),
                  {"d": d, "t": ftype, "v": value})


def _relationship(d, entity_type, entity_id):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_relationships (document_id, entity_type, entity_id)"
                       " VALUES (:d,:t,:e)"), {"d": d, "t": entity_type, "e": entity_id})


def _version(d, n, previous=None):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_versions (document_id, version_number,"
                       " previous_document_id) VALUES (:d,:n,:p)"),
                  {"d": d, "n": n, "p": previous})


def _status(d):
    with engine.begin() as c:
        return c.execute(text("SELECT status FROM documents WHERE id = :i"), {"i": d}).scalar()


def _exists(d):
    with engine.begin() as c:
        return c.execute(text("SELECT count(*) FROM documents WHERE id = :i"),
                         {"i": d}).scalar_one() == 1


def _plan_for(sha, plan_doc=None):
    plan_doc = plan_doc or dx.plan()
    return [p for p in plan_doc["partitions"] if p["sha256"] == sha]


def _apply(sha=None, *, plan_doc=None, **kw):
    """Apply with the guards satisfied from the CURRENT plan, so tests exercise the real path."""
    plan_doc = plan_doc or dx.plan()
    if sha is not None:
        plan_doc = {**plan_doc, "partitions": _plan_for(sha, plan_doc)}
        plan_doc["safe_partitions"] = len(plan_doc["partitions"])
        plan_doc["rows_to_retire"] = sum(p["rows_to_retire"] for p in plan_doc["partitions"])
    kw.setdefault("expected_safe_partitions", plan_doc["safe_partitions"])
    kw.setdefault("expected_retirement_rows", plan_doc["rows_to_retire"])
    return dx.apply(plan_doc=plan_doc, apply_writes=True, **kw)


# === 1-8: the ownership boundary ==================================================================

def test_1_same_owner_same_sha_safe_dependencies_succeeds():
    sha, p = _sha(), _person(" A")
    a, b = sorted([_doc(sha, person_id=p), _doc(sha, person_id=p)])
    _source(a, _SYS_A, "u://a"), _source(b, _SYS_B, "u://b")
    r = _apply(sha)
    assert r["partitions_applied"] == 1 and r["partitions_refused"] == 0
    assert r["rows_retired"] == 1
    assert _status(a) == "active" and _status(b) == "deleted"
    assert r["applied"][0]["survivor_document_id"] == a
    assert r["applied"][0]["retired_document_ids"] == [b]


@pytest.mark.parametrize("dimension", ["person", "household", "organization"])
def test_2_3_4_different_owner_on_any_dimension_is_impossible_to_execute(dimension):
    sha = _sha()
    make = {"person": _person, "household": _household, "organization": _org}[dimension]
    kwarg = {"person": "person_id", "household": "household_id",
             "organization": "organization_id"}[dimension]
    a = _doc(sha, **{kwarg: make(" 1")})
    b = _doc(sha, **{kwarg: make(" 2")})
    assert _plan_for(sha) == [], "a cross-owner pair must produce NO executable plan entry"
    r = _apply(sha)
    assert r["partitions_applied"] == 0 and r["rows_retired"] == 0
    assert _status(a) == "active" and _status(b) == "active"


def test_5_mixed_owner_dimensions_are_distinct_scopes_and_never_cross():
    sha = _sha()
    a = _doc(sha, person_id=_person(" M1"), household_id=_household("Ha"))
    b = _doc(sha, person_id=_person(" M2"), household_id=_household("Hb"))
    assert _plan_for(sha) == []
    _apply(sha)
    assert _status(a) == "active" and _status(b) == "active"


def test_6_unowned_shared_content_is_never_executable_merely_because_the_sha_matches():
    sha = _sha()
    unowned = _doc(sha)
    owned = _doc(sha, person_id=_person(" U"))
    g = next(x for x in dm.preview()["groups"] if x["sha256"] == sha)
    assert g["classification"] == dm.SHARED
    assert _plan_for(sha) == []
    _apply(sha)
    assert _status(unowned) == "active" and _status(owned) == "active"


def test_7_two_owner_local_partitions_execute_independently_and_never_cross():
    sha, p1, p2 = _sha(), _person(" P1"), _person(" P2")
    a1, a2 = sorted([_doc(sha, person_id=p1), _doc(sha, person_id=p1)])
    b1, b2 = sorted([_doc(sha, person_id=p2), _doc(sha, person_id=p2)])
    plans = _plan_for(sha)
    assert len(plans) == 2
    assert {p["survivor_document_id"] for p in plans} == {a1, b1}
    for p in plans:                                   # no plan may name a foreign document
        assert set(p["duplicate_document_ids"]) <= ({a2} if p["survivor_document_id"] == a1
                                                    else {b2})
    r = _apply(sha)
    assert r["partitions_applied"] == 2 and r["rows_retired"] == 2
    assert _status(a1) == "active" and _status(a2) == "deleted"
    assert _status(b1) == "active" and _status(b2) == "deleted"


def test_8_survivor_is_exactly_the_preview_selected_survivor():
    sha, p = _sha(), _person(" S")
    ids = sorted([_doc(sha, person_id=p) for _ in range(4)])
    part = next(x for g in dm.preview()["groups"] if g["sha256"] == sha
                for x in g["partitions"] if x["mergeable"])
    r = _apply(sha)
    assert r["applied"][0]["survivor_document_id"] == part["proposed_survivor"] == ids[0]
    assert sorted(r["applied"][0]["retired_document_ids"]) == ids[1:]


# === 9-11: only SAFE_AUTO_MERGE is executable =====================================================

def test_9_review_required_is_rejected():
    sha, p = _sha(), _person(" R")
    a, b = sorted([_doc(sha, person_id=p, category="tax"),
                   _doc(sha, person_id=p, category="estate")])
    part = next(x for g in dm.preview()["groups"] if g["sha256"] == sha
                for x in g["partitions"] if x["mergeable"])
    assert part["classification"] == dm.REVIEW
    assert _plan_for(sha) == []
    _apply(sha)
    assert _status(a) == "active" and _status(b) == "active"


def test_10_blocked_is_rejected(monkeypatch):
    sha, p = _sha(), _person(" B")
    a, b = sorted([_doc(sha, person_id=p), _doc(sha, person_id=p)])
    _ocr(b)
    patched = dict(dm._STRATEGY); patched.pop("document_ocr")
    monkeypatch.setattr(dm, "_STRATEGY", patched)
    part = next(x for g in dm.preview()["groups"] if g["sha256"] == sha
                for x in g["partitions"] if x["mergeable"])
    assert part["classification"] == dm.BLOCKED
    assert _plan_for(sha) == []
    _apply(sha)
    assert _status(a) == "active" and _status(b) == "active"


def test_11_shared_content_is_rejected():
    sha = _sha()
    ids = [_doc(sha, person_id=_person(f" SC{i}")) for i in range(3)]
    assert _plan_for(sha) == []
    r = dx.plan()
    assert r["refused_partitions"].get(dm.SHARED, 0) >= 1
    _apply(sha)
    assert all(_status(i) == "active" for i in ids)


def test_only_safe_is_in_the_executable_set():
    assert dx.EXECUTABLE_CLASSIFICATIONS == frozenset({dm.SAFE})
    assert dx.NON_EXECUTABLE_CLASSIFICATIONS == frozenset({dm.REVIEW, dm.BLOCKED, dm.SHARED})
    assert dm.SHARED not in dx.EXECUTABLE_CLASSIFICATIONS


# === 12-15: a stale plan is REJECTED, never silently regenerated ==================================

def _safe_pair(cat=None):
    sha, p = _sha(), _person(" ST")
    a, b = sorted([_doc(sha, person_id=p, category=cat), _doc(sha, person_id=p, category=cat)])
    return sha, p, a, b


def test_12_stale_plan_after_ownership_change_is_rejected():
    sha, p, a, b = _safe_pair()
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha)}
    with engine.begin() as c:                       # b moves to a different client
        c.execute(text("UPDATE documents SET person_id = :n WHERE id = :i"),
                  {"n": _person(" MOVED"), "i": b})
    r = dx.apply(plan_doc={**plan_doc, "safe_partitions": 1, "rows_to_retire": 1},
                 apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1)
    assert r["partitions_applied"] == 0 and r["partitions_refused"] == 1
    assert r["refused"][0]["refused"] == "StalePlanError"
    assert _status(a) == "active" and _status(b) == "active"


def test_13_stale_plan_after_category_change_is_rejected():
    sha, p, a, b = _safe_pair()
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha)}
    with engine.begin() as c:                       # introduces a category_conflict -> REVIEW
        c.execute(text("UPDATE documents SET category = 'tax' WHERE id = :i"), {"i": a})
        c.execute(text("UPDATE documents SET category = 'estate' WHERE id = :i"), {"i": b})
    r = dx.apply(plan_doc={**plan_doc, "safe_partitions": 1, "rows_to_retire": 1},
                 apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1)
    assert r["partitions_refused"] == 1
    assert "REVIEW_REQUIRED" in r["refused"][0]["detail"] or "fingerprint" in r["refused"][0]["detail"]
    assert _status(a) == "active" and _status(b) == "active"


def test_14_stale_plan_after_sha_change_is_rejected():
    sha, p, a, b = _safe_pair()
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha)}
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET sha256 = :s WHERE id = :i"), {"s": _sha(), "i": b})
    r = dx.apply(plan_doc={**plan_doc, "safe_partitions": 1, "rows_to_retire": 1},
                 apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1)
    assert r["partitions_refused"] == 1
    assert _status(a) == "active" and _status(b) == "active"


def test_15_stale_plan_after_a_new_dependency_appears_is_rejected():
    sha, p, a, b = _safe_pair()
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha)}
    _ocr(b)                                          # dependency shape moved after planning
    r = dx.apply(plan_doc={**plan_doc, "safe_partitions": 1, "rows_to_retire": 1},
                 apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1)
    assert r["partitions_refused"] == 1
    assert "fingerprint" in r["refused"][0]["detail"]
    assert _status(a) == "active" and _status(b) == "active"


def test_a_fresh_plan_after_the_same_change_succeeds():
    """The rejection is about staleness, not about the change itself."""
    sha, p, a, b = _safe_pair()
    _ocr(b)
    r = _apply(sha)                                  # plan regenerated deliberately
    assert r["partitions_applied"] == 1
    assert _status(b) == "deleted"


# === 16: transaction rollback =====================================================================

def _fail_on_nth(monkeypatch, n):
    """Make the Nth partition explode INSIDE the write phase, after earlier ones succeeded."""
    real = dx._execute_prepared
    calls = {"n": 0}

    def _boom(conn, prepared, run_id, actor, request_id):
        calls["n"] += 1
        if calls["n"] == n:
            raise RuntimeError("simulated failure")
        return real(conn, prepared, run_id, actor, request_id)

    monkeypatch.setattr(dx, "_execute_prepared", _boom)
    return calls


def test_16_a_failure_mid_batch_rolls_the_whole_batch_back(monkeypatch):
    shas = [_safe_pair()[0] for _ in range(3)]
    plan_doc = dx.plan()
    mine = [p for p in plan_doc["partitions"] if p["sha256"] in shas]
    assert len(mine) == 3
    dups = [d for p in mine for d in p["duplicate_document_ids"]]
    _fail_on_nth(monkeypatch, 3)
    r = dx.apply(plan_doc={**plan_doc, "partitions": mine, "safe_partitions": 3,
                           "rows_to_retire": 3},
                 apply_writes=True, batch_size=10,
                 expected_safe_partitions=3, expected_retirement_rows=3)
    assert r["failed_batch"]["batch"] == 1
    assert r["committed_batches"] == []
    assert r["partial_apply"] is False, "nothing committed, so this is a total failure not partial"
    for d in dups:
        assert _status(d) == "active", "a failed batch must roll back completely"


def test_a_later_batch_failure_is_reported_as_a_PARTIAL_APPLY(monkeypatch):
    """Batches commit independently, so this must never look like 'nothing was written'."""
    shas = [_safe_pair()[0] for _ in range(4)]
    plan_doc = dx.plan()
    mine = sorted([p for p in plan_doc["partitions"] if p["sha256"] in shas],
                  key=lambda p: p["survivor_document_id"])
    _fail_on_nth(monkeypatch, 3)
    r = dx.apply(plan_doc={**plan_doc, "partitions": mine, "safe_partitions": 4,
                           "rows_to_retire": 4},
                 apply_writes=True, batch_size=2,
                 expected_safe_partitions=4, expected_retirement_rows=4)
    assert r["partial_apply"] is True
    assert r["committed_batches"] == [1]
    assert r["failed_batch"]["batch"] == 2
    assert r["failed_batch"]["error"] == "RuntimeError"
    assert r["partitions_applied"] == 2 and r["rows_committed"] == 2
    assert r["wrote_anything"] is True
    # batch 1 durable, batch 2 rolled back, batches 3-4 never attempted
    assert all(_status(d) == "deleted" for d in mine[0]["duplicate_document_ids"])
    assert all(_status(d) == "deleted" for d in mine[1]["duplicate_document_ids"])
    assert all(_status(d) == "active" for d in mine[2]["duplicate_document_ids"])
    assert all(_status(d) == "active" for d in mine[3]["duplicate_document_ids"])
    assert [b["committed"] for b in r["batches"]] == [True, False]


def test_the_cli_reports_partial_apply_and_exits_nonzero(monkeypatch, capsys):
    import scripts.execute_document_merge as cli
    shas = [_safe_pair()[0] for _ in range(4)]
    plan_doc = dx.plan()
    mine = sorted([p for p in plan_doc["partitions"] if p["sha256"] in shas],
                  key=lambda p: p["survivor_document_id"])
    _fail_on_nth(monkeypatch, 3)
    # apply() builds its own plan via dx.plan(); pin it to MY partitions so the test does not
    # depend on whatever else the shared test database happens to contain.
    monkeypatch.setattr(dx, "plan", lambda **kw: {**plan_doc, "partitions": mine,
                                                  "safe_partitions": 4, "rows_to_retire": 4})
    code = cli.main(["--apply", "--batch-size", "2", "--expected-safe-partitions", "4",
                     "--expected-retirement-rows", "4"])
    out = capsys.readouterr().out
    assert code == 3, "a partial apply must exit non-zero"
    assert "PARTIAL" in out and "APPLIED - SUCCESS" not in out
    assert "FINAL STATUS        : PARTIAL" in out
    assert "committed batches   : 1" in out
    assert "failed batch        : 2" in out
    assert "partitions committed: 2" in out
    assert "retirement rows committed: 2" in out
    assert "nothing was written" not in out, "must never claim nothing happened"


# === 17-25: dependency handling ===================================================================

def test_17_hard_fk_dependencies_are_repointed_to_the_survivor():
    sha, p, a, b = _safe_pair()
    _version(b, 1)
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_events (document_id, event_type, occurred_at)"
                       " VALUES (:d, 'test_event', now())"), {"d": b})
    r = _apply(sha)
    assert r["partitions_applied"] == 1
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM document_versions WHERE document_id = :i"),
                         {"i": a}).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM document_events WHERE document_id = :i"
                              " AND event_type = 'test_event'"), {"i": a}).scalar_one() == 1


def test_18_a_unique_collision_with_no_certified_rule_refuses_the_partition():
    """document_versions(document_id, version_number): two copies both at v1 cannot both move."""
    sha, p, a, b = _safe_pair()
    _version(a, 1), _version(b, 1)
    r = _apply(sha)
    assert r["partitions_applied"] == 0 and r["partitions_refused"] == 1
    assert "uncertified unique collision" in r["refused"][0]["detail"]
    assert "document_versions" in r["refused"][0]["detail"]
    assert _status(a) == "active" and _status(b) == "active", "refusal must precede every write"


def test_19_document_sources_provenance_is_fully_preserved():
    sha, p, a, b = _safe_pair()
    _source(a, _SYS_A, "u://shared")
    _source(b, _SYS_A, "u://shared")                 # identical tuple -> redundant, dedupes to one
    _source(b, _SYS_B, "u://only-on-b")              # distinct tuple -> MUST survive
    r = _apply(sha)
    assert r["partitions_applied"] == 1
    with engine.begin() as c:
        rows = c.execute(text("SELECT source_system, source_uri FROM document_sources"
                              " WHERE document_id = :i ORDER BY source_system"),
                         {"i": a}).fetchall()
    assert {(x[0], x[1]) for x in rows} == {(_SYS_A, "u://shared"), (_SYS_B, "u://only-on-b")}
    assert len(rows) == 2, "no duplicate provenance row"
    # The PRE-merge provenance is captured as evidence; all 3 original tuples are recorded.
    before = r["applied"][0]["pre_merge_provenance_tuples"]
    assert {(x["source_system"], x["source_uri"]) for x in before} == {
        (_SYS_A, "u://shared"), (_SYS_B, "u://only-on-b")}
    assert len(before) == 3, "every original (document, system, uri) row is recorded"


def test_20_ocr_is_preserved_and_promoted_when_the_survivor_has_none():
    sha, p, a, b = _safe_pair()
    _ocr(b, chars=4321)                              # only the duplicate carries OCR
    r = _apply(sha)
    assert r["partitions_applied"] == 1
    with engine.begin() as c:
        row = c.execute(text("SELECT status, char_count FROM document_ocr WHERE document_id = :i"),
                        {"i": a}).one()
    assert row[1] == 4321, "OCR must be promoted onto the survivor, never lost"


def test_21_classification_rows_are_preserved():
    sha, p, a, b = _safe_pair()
    _classification(b, "1099")
    _apply(sha)
    with engine.begin() as c:
        assert c.execute(text("SELECT doc_type FROM document_classifications"
                              " WHERE document_id = :i"), {"i": a}).scalar() == "1099"


def test_21b_equivalent_singular_rows_collapse_to_one_without_a_unique_violation():
    sha, p, a, b = _safe_pair()
    _ocr(a, chars=50), _ocr(b, chars=50)             # equivalent -> preview says SAFE
    _classification(a, "W-2"), _classification(b, "W-2")
    _apply(sha)
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM document_ocr WHERE document_id = :i"),
                         {"i": a}).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM document_classifications"
                              " WHERE document_id = :i"), {"i": a}).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM document_ocr WHERE document_id = :i"),
                         {"i": b}).scalar_one() == 0


def test_22_facts_are_preserved_and_redundant_current_facts_dedupe():
    sha, p, a, b = _safe_pair()
    _fact(a, "tax_year", "2024")
    _fact(b, "tax_year", "2024")                     # identical current fact -> redundant
    _fact(b, "employer", "Acme")                     # distinct -> must survive
    _apply(sha)
    with engine.begin() as c:
        rows = c.execute(text("SELECT fact_type, fact_value FROM document_facts"
                              " WHERE document_id = :i ORDER BY fact_type"), {"i": a}).fetchall()
    assert {(x[0], x[1]) for x in rows} == {("tax_year", "2024"), ("employer", "Acme")}
    assert len(rows) == 2


def _derivative(d, *, kind="normalized_image", status="completed", source_hash=None,
                derivative_hash=None, path=None):
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO document_derivatives (document_id, kind, status, source_mime, source_hash,"
            " derivative_mime, derivative_path, derivative_hash) VALUES"
            " (:d,:k,:s,'image/heic',:sh,'image/jpeg',:p,:dh)"),
            {"d": d, "k": kind, "s": status, "sh": source_hash,
             "p": path or f"/deriv/{derivative_hash or 'x'}.jpg", "dh": derivative_hash})


def test_22b_normalized_image_derivatives_transfer_to_the_survivor():
    """A duplicate's derivative row is repointed, not dropped, when the survivor has none."""
    sha, p, a, b = _safe_pair()
    _derivative(b, source_hash=sha, derivative_hash="d" * 64, path="/deriv/dd.jpg")
    _apply(sha)
    with engine.begin() as c:
        rows = c.execute(text("SELECT kind, derivative_hash FROM document_derivatives"
                              " WHERE document_id = :i"), {"i": a}).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("normalized_image", "d" * 64)]


def test_22c_a_colliding_derivative_kind_dedupes_to_one_row():
    """Both documents carry the same rendition of the same content — one row survives, no duplicate.

    The derivative FILE is content-addressed on the source sha, which the merge partition proves is
    identical, so the two rows describe the same artifact and neither document loses anything."""
    sha, p, a, b = _safe_pair()
    _derivative(a, source_hash=sha, derivative_hash="e" * 64, path="/deriv/ee.jpg")
    _derivative(b, source_hash=sha, derivative_hash="e" * 64, path="/deriv/ee.jpg")
    _apply(sha)
    with engine.begin() as c:
        n = c.execute(text("SELECT count(*) FROM document_derivatives WHERE document_id = :i"),
                      {"i": a}).scalar_one()
        orphaned = c.execute(text("SELECT count(*) FROM document_derivatives WHERE document_id = :i"),
                             {"i": b}).scalar_one()
    assert n == 1 and orphaned == 0


def test_18b_relationship_dedup_key_collision_is_handled_without_duplicates():
    sha, p, a, b = _safe_pair()
    org = _org(" REL")
    _relationship(a, "business", org)
    _relationship(b, "business", org)                # same tuple -> dedupes
    _relationship(b, "business", _org(" REL2"))      # different entity -> survives (additive)
    _apply(sha)
    with engine.begin() as c:
        n = c.execute(text("SELECT count(*) FROM document_relationships WHERE document_id = :i"),
                      {"i": a}).scalar_one()
    assert n == 2


def test_23_a_version_self_reference_between_members_is_never_executable():
    """preview() calls this a conflict, so the executor never sees it as SAFE."""
    sha, p, a, b = _safe_pair()
    _version(b, 1, previous=a)
    part = next(x for g in dm.preview()["groups"] if g["sha256"] == sha
                for x in g["partitions"] if x["mergeable"])
    assert part["classification"] == dm.REVIEW
    assert _plan_for(sha) == []
    _apply(sha)
    assert _status(a) == "active" and _status(b) == "active"


def test_23b_previous_document_id_pointing_at_a_retired_row_is_repointed():
    sha, p, a, b = _safe_pair()
    other = _doc(_sha(), person_id=p)
    _version(other, 2, previous=b)                   # an OUTSIDE row points at the duplicate
    _apply(sha)
    with engine.begin() as c:
        assert c.execute(text("SELECT previous_document_id FROM document_versions"
                              " WHERE document_id = :i"), {"i": other}).scalar() == a


def test_24_set_null_references_are_repointed_not_nulled():
    sha, p, a, b = _safe_pair()
    with engine.begin() as c:
        rule = c.execute(text("""
            SELECT c.relname, a2.attname FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_class rc ON rc.oid = con.confrelid
            JOIN pg_attribute a2 ON a2.attrelid = con.conrelid AND a2.attnum = con.conkey[1]
            WHERE rc.relname = 'documents' AND con.contype = 'f' AND con.confdeltype = 'n'
            LIMIT 1""")).first()
    assert rule is not None, "the corpus must have at least one SET NULL reference to test"
    r = _apply(sha)
    assert r["partitions_applied"] == 1
    # The policy itself: every declared dependency is repointed by UPDATE, never nulled.
    assert "SET NULL" not in json.dumps(r["applied"][0]["reassigned_by_table"])


def test_25_the_soft_reference_rm_document_status_is_handled():
    sha, p, a, b = _safe_pair()
    with engine.begin() as c:
        if c.execute(text("SELECT to_regclass('rm_document_status')")).scalar() is None:
            pytest.skip("rm_document_status not present")
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'rm_document_status'"))}
        c.execute(text("INSERT INTO rm_document_status (document_id) VALUES (:d)"), {"d": b})
    assert "document_id" in cols
    r = _apply(sha)
    assert r["partitions_applied"] == 1
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM rm_document_status WHERE document_id = :i"),
                         {"i": b}).scalar_one() == 0


def test_an_unknown_dependency_at_execution_time_stops_the_partition(monkeypatch):
    sha, p, a, b = _safe_pair()
    _ocr(b)
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha)}
    patched = dict(dm._STRATEGY); patched.pop("document_ocr")
    monkeypatch.setattr(dm, "_STRATEGY", patched)    # strategy vanishes AFTER planning
    r = dx.apply(plan_doc={**plan_doc, "safe_partitions": 1, "rows_to_retire": 1},
                 apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1)
    assert r["partitions_applied"] == 0 and r["partitions_refused"] == 1
    assert _status(a) == "active" and _status(b) == "active"


# === 26-31: idempotency, dry-run, and the write guards =============================================

def test_26_re_running_an_applied_partition_is_a_no_op_and_never_duplicates_provenance():
    sha, p, a, b = _safe_pair()
    _source(a, _SYS_A, "u://x"), _source(b, _SYS_B, "u://y")
    first = _apply(sha)
    assert first["partitions_applied"] == 1

    def _prov():
        with engine.begin() as c:
            return c.execute(text("SELECT count(*) FROM document_sources WHERE document_id = :i"),
                             {"i": a}).scalar_one()

    after_first = _prov()
    assert after_first == 2
    # The retired row left the eligible population, so the group is no longer a duplicate group.
    assert _plan_for(sha) == [], "an applied partition must not be re-proposed"
    second = dx.apply(plan_doc={**dx.plan(), "partitions": first["applied"] and _plan_for(sha),
                                "safe_partitions": 0, "rows_to_retire": 0},
                      apply_writes=True, expected_safe_partitions=0, expected_retirement_rows=0)
    assert second["partitions_applied"] == 0 and second["rows_retired"] == 0
    assert _prov() == after_first, "no duplicate provenance on retry"


def test_26b_replaying_the_original_plan_is_refused_as_stale_not_reapplied():
    sha, p, a, b = _safe_pair()
    stale = {**dx.plan(), "partitions": _plan_for(sha), "safe_partitions": 1, "rows_to_retire": 1}
    dx.apply(plan_doc=stale, apply_writes=True, expected_safe_partitions=1,
             expected_retirement_rows=1)
    assert _status(b) == "deleted"
    again = dx.apply(plan_doc=stale, apply_writes=True, expected_safe_partitions=1,
                     expected_retirement_rows=1)
    assert again["partitions_applied"] == 0 and again["partitions_refused"] == 1
    assert again["refused"][0]["refused"] == "StalePlanError"


def test_27_no_filesystem_mutation_capability_exists():
    import ast
    import pathlib
    banned = {"unlink", "remove", "rmtree", "move", "rename", "removedirs", "rmdir", "copy",
              "copyfile", "copytree", "system", "popen", "run", "call", "check_output"}
    for rel in ("app/services/document_merge_execute.py", "scripts/execute_document_merge.py"):
        tree = ast.parse(pathlib.Path(rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    assert n.name.split(".")[0] not in {"shutil", "subprocess", "os", "pathlib",
                                                        "glob", "socket", "requests", "httpx"}, \
                        f"{rel} imports {n.name}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in {"shutil", "subprocess", "os", "glob",
                                                         "socket", "requests", "httpx"}, \
                    f"{rel} imports from {node.module}"
            if isinstance(node, ast.Attribute) and node.attr in banned:
                raise AssertionError(f"{rel} calls .{node.attr}()")


def test_27b_storage_columns_of_a_retired_row_are_left_untouched():
    sha, p, a, b = _safe_pair()
    with engine.begin() as c:
        before = c.execute(text("SELECT storage_path, storage_uri, stored_name FROM documents"
                                " WHERE id = :i"), {"i": b}).one()
    _apply(sha)
    with engine.begin() as c:
        after = c.execute(text("SELECT storage_path, storage_uri, stored_name FROM documents"
                               " WHERE id = :i"), {"i": b}).one()
    assert before == after, "every physical location stays represented on the retired row"
    assert _exists(b), "the row itself is preserved - soft retirement, never a hard delete"


def _snapshot():
    with engine.begin() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in
                ("documents", "document_sources", "document_ocr", "document_classifications",
                 "document_facts", "document_relationships", "document_versions",
                 "document_events", "audit_events")}


def test_28_a_dry_run_writes_absolutely_nothing():
    sha, p, a, b = _safe_pair()
    _source(a, _SYS_A, "u://d1"), _source(b, _SYS_B, "u://d2")
    before = _snapshot()
    r = dx.apply(plan_doc={**dx.plan(), "partitions": _plan_for(sha)})   # no apply_writes
    assert r["dry_run"] is True and r["wrote_anything"] is False
    assert r["partitions_applied"] == 0, "a dry run applies nothing"
    assert r["partitions_prepared"] == 1, "but it still revalidates and reports what WOULD apply"
    assert r["would_retire_rows"] == 1
    assert _snapshot() == before, "a dry run must not change a single row"
    assert _status(a) == "active" and _status(b) == "active"


def test_28b_dry_run_is_the_default():
    sha, p, a, b = _safe_pair()
    r = dx.apply(plan_doc={**dx.plan(), "partitions": _plan_for(sha)})
    assert r["dry_run"] is True
    assert _status(b) == "active"


def test_29_apply_without_the_expected_count_guards_is_refused_before_any_write():
    sha, p, a, b = _safe_pair()
    before = _snapshot()
    for kwargs in ({}, {"expected_safe_partitions": 1}, {"expected_retirement_rows": 1}):
        with pytest.raises(dx.MergeExecutionError, match="expected-safe-partitions|requires"):
            dx.apply(plan_doc={**dx.plan(), "partitions": _plan_for(sha)},
                     apply_writes=True, **kwargs)
    assert _snapshot() == before
    assert _status(b) == "active"


def test_30_a_count_mismatch_fails_before_any_write():
    sha, p, a, b = _safe_pair()
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha), "safe_partitions": 1,
                "rows_to_retire": 1}
    before = _snapshot()
    with pytest.raises(dx.MergeExecutionError, match="expectation mismatch"):
        dx.apply(plan_doc=plan_doc, apply_writes=True, expected_safe_partitions=999,
                 expected_retirement_rows=1)
    with pytest.raises(dx.MergeExecutionError, match="expectation mismatch"):
        dx.apply(plan_doc=plan_doc, apply_writes=True, expected_safe_partitions=1,
                 expected_retirement_rows=999)
    assert _snapshot() == before, "the guard must run before a single row is touched"
    assert _status(b) == "active"


def test_31_a_tampered_fingerprint_fails_before_any_write():
    sha, p, a, b = _safe_pair()
    parts = _plan_for(sha)
    parts[0]["fingerprint"] = "0" * 64
    before = _snapshot()
    r = dx.apply(plan_doc={**dx.plan(), "partitions": parts, "safe_partitions": 1,
                           "rows_to_retire": 1},
                 apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1)
    assert r["partitions_applied"] == 0 and r["partitions_refused"] == 1
    assert "fingerprint mismatch" in r["refused"][0]["detail"]
    assert _snapshot() == before
    assert _status(b) == "active"


# === 32-34: audit and the standing boundary ========================================================

def _audits(run_id=None):
    with engine.begin() as c:
        rows = c.execute(text("SELECT metadata, outcome, entity_id FROM audit_events"
                              " WHERE action = :a ORDER BY id DESC LIMIT 50"),
                         {"a": dx.AUDIT_ACTION}).mappings().all()
    out = [dict(r) for r in rows]
    return [r for r in out if run_id is None
            or (json.loads(r["metadata"]) if isinstance(r["metadata"], str)
                else r["metadata"]).get("run_id") == run_id]


def test_32_a_successful_apply_writes_a_reconstructable_audit_record():
    sha, p, a, b = _safe_pair()
    _source(b, _SYS_A, "u://aud")
    r = _apply(sha)
    entries = _audits(r["run_id"])
    assert len(entries) == 1
    md = entries[0]["metadata"]
    md = json.loads(md) if isinstance(md, str) else md
    for key in ("run_id", "sha256", "owner", "survivor_document_id", "retired_document_ids",
                "classification_at_execution", "reassigned_by_table", "deleted_by_table",
                "dependency_actions", "pre_merge_documents", "pre_merge_provenance_tuples",
                "fingerprint", "rows_retired"):
        assert key in md, key
    assert md["sha256"] == sha
    assert md["survivor_document_id"] == a and md["retired_document_ids"] == [b]
    assert md["classification_at_execution"] == dm.SAFE
    assert md["owner"]["person_id"] == p
    assert entries[0]["outcome"] == "success"
    assert entries[0]["entity_id"] == f"{sha}:{a}"


def test_33_a_failed_transaction_leaves_no_success_audit(monkeypatch):
    sha, p, a, b = _safe_pair()
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha), "safe_partitions": 1,
                "rows_to_retire": 1}
    seen = {}
    real = dx._execute_prepared

    def _boom(conn, prepared, run_id, actor, request_id):
        seen["run_id"] = run_id
        real(conn, prepared, run_id, actor, request_id)     # writes, including the audit row
        raise RuntimeError("simulated post-write failure")

    monkeypatch.setattr(dx, "_execute_prepared", _boom)
    r = dx.apply(plan_doc=plan_doc, apply_writes=True, expected_safe_partitions=1,
                 expected_retirement_rows=1)
    assert r["failed_batch"]["error"] == "RuntimeError"
    assert _audits(seen["run_id"]) == [], "a rolled-back merge must leave no success audit"
    assert _status(b) == "active"


def test_28c_a_dry_run_leaves_no_audit_record():
    sha, p, a, b = _safe_pair()
    r = dx.apply(plan_doc={**dx.plan(), "partitions": _plan_for(sha)})
    assert _audits(r["run_id"]) == []


def test_34_the_ownership_partition_boundary_holds_across_a_whole_mixed_corpus():
    """Every retirement anywhere in the corpus stays inside one exact ownership tuple."""
    p1, p2 = _person(" X1"), _person(" X2")
    s_ok = _sha(); _doc(s_ok, person_id=p1), _doc(s_ok, person_id=p1)
    s_cross = _sha(); _doc(s_cross, person_id=_person(" Y1")), _doc(s_cross,
                                                                    person_id=_person(" Y2"))
    s_part = _sha(); _doc(s_part, person_id=p2), _doc(s_part, person_id=p2)
    _doc(s_part, person_id=_person(" Z"))
    s_unowned = _sha(); _doc(s_unowned), _doc(s_unowned, person_id=_person(" W"))
    s_hh = _sha(); _doc(s_hh, household_id=_household("H1")), _doc(s_hh,
                                                                   household_id=_household("H2"))

    owners = {}
    with engine.begin() as c:
        for row in c.execute(text("SELECT id, person_id, household_id, organization_id"
                                  " FROM documents WHERE sha256 = ANY(:s)"),
                             {"s": [s_ok, s_cross, s_part, s_unowned, s_hh]}).mappings():
            owners[row["id"]] = (row["person_id"], row["household_id"], row["organization_id"])

    for p in dx.plan()["partitions"]:
        if p["sha256"] not in (s_ok, s_cross, s_part, s_unowned, s_hh):
            continue
        survivor_scope = owners[p["survivor_document_id"]]
        for did in p["duplicate_document_ids"]:
            assert owners[did] == survivor_scope, (
                f"{p['sha256'][:12]}: doc {did} would be retired into a DIFFERENT ownership scope")


def test_the_executor_never_reimplements_ownership_or_classification():
    """Structural: the executor must have no partition key and no classification rule of its own."""
    import pathlib
    src = pathlib.Path("app/services/document_merge_execute.py").read_text(encoding="utf-8")
    for banned in ("person_id\") ==", "GROUP BY sha256", "def _owner_key", "def _group_shape",
                   "def _analyze_partition", "SAFE_AUTO_MERGE\" if", "classification ="):
        assert banned not in src, banned
    assert "from app.services.document_merge import" in src
    assert "preview(" in src


def test_mutating_the_partition_key_to_global_sha_breaks_the_boundary_tests(monkeypatch):
    """The regression proof: if ownership stops keying the partition, these tests MUST fail.

    Rather than trusting a comment, this drives the real code with a global-SHA key and asserts
    that a cross-person pair then becomes executable - which is exactly the bug the suite exists
    to catch."""
    sha = _sha()
    a = _doc(sha, person_id=_person(" G1"))
    b = _doc(sha, person_id=_person(" G2"))
    assert _plan_for(sha) == [], "with ownership keying, a cross-person pair is NOT executable"

    monkeypatch.setattr(dm, "_owner_key", lambda doc: ("GLOBAL", None, None))   # the regression
    poisoned = _plan_for(sha)
    assert len(poisoned) == 1, "sanity: the mutation really does make it executable"
    assert set(poisoned[0]["duplicate_document_ids"]) == {max(a, b)}
    # ...and that is precisely what the ownership tests above would catch.


# === locking =======================================================================================

def test_participating_documents_are_row_locked_before_they_are_changed():
    """A second session must not be able to take a conflicting lock while the merge holds it.

    Uses NOWAIT so the probe fails immediately instead of hanging the suite."""
    import sqlalchemy.exc
    sha, p, a, b = _safe_pair()
    with engine.begin() as held:
        dx._lock_documents(held, [a, b])
        with engine.connect() as probe, pytest.raises(sqlalchemy.exc.OperationalError):
            probe.execute(text("SELECT id FROM documents WHERE id = :i FOR UPDATE NOWAIT"),
                          {"i": b}).all()


def test_locks_are_taken_in_ascending_document_id_order():
    """Deterministic ordering is what keeps two concurrent runs from deadlocking."""
    sha, p, a, b = _safe_pair()
    c = _doc(sha, person_id=p)
    with engine.begin() as conn:
        assert dx._lock_documents(conn, [c, a, b]) == sorted([a, b, c])


def test_the_lock_holds_no_filesystem_or_network_resource():
    import inspect
    src = inspect.getsource(dx._lock_documents)
    assert "FOR UPDATE" in src and "ORDER BY id" in src
    for banned in ("open(", "requests", "httpx", "socket", "Path"):
        assert banned not in src


# === CLI ===========================================================================================

def test_the_cli_defaults_to_dry_run_and_writes_nothing(capsys):
    import scripts.execute_document_merge as cli
    sha, p, a, b = _safe_pair()
    before = _snapshot()
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN - NO DATABASE WRITE WAS ISSUED" in out
    assert "no --apply: no database write was issued" in out
    assert _snapshot() == before
    assert _status(b) == "active"


def test_the_cli_refuses_apply_without_guards_and_exits_nonzero(capsys):
    import scripts.execute_document_merge as cli
    sha, p, a, b = _safe_pair()
    before = _snapshot()
    assert cli.main(["--apply"]) == 2
    out = capsys.readouterr().out
    assert "REFUSED - nothing was written" in out
    assert _snapshot() == before
    assert _status(b) == "active"


def test_the_cli_plan_mode_writes_nothing_and_can_be_saved_and_replayed(tmp_path, capsys):
    import scripts.execute_document_merge as cli
    sha, p, a, b = _safe_pair()
    out_file = tmp_path / "plan.json"
    before = _snapshot()
    assert cli.main(["--plan", "--output-json", str(out_file)]) == 0
    assert _snapshot() == before
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["safe_partitions"] >= 1
    mine = [x for x in saved["partitions"] if x["sha256"] == sha]
    assert len(mine) == 1 and mine[0]["survivor_document_id"] == a
    assert len(mine[0]["fingerprint"]) == 64
    capsys.readouterr()
    assert cli.main(["--plan-file", str(out_file)]) == 0     # dry run of the SAVED plan
    assert "DRY RUN" in capsys.readouterr().out
    assert _status(b) == "active"


def test_the_cli_output_is_plain_ascii_for_a_cp1252_console(capsys):
    import scripts.execute_document_merge as cli
    _safe_pair()
    cli.main(["--plan"])
    cli.main([])
    out = capsys.readouterr().out
    out.encode("cp1252")            # raises if any character is non-ASCII
    assert out.isascii()


# === dry run issues ZERO mutation SQL =============================================================
# Asserting "no rows changed" is not enough: a write-then-rollback still advances sequences and
# fires triggers. These tests instrument the CURSOR and prove no mutating statement is ever sent.

_MUTATING = ("insert", "update", "delete", "truncate", "merge", "copy",
             "nextval", "setval", "create", "drop", "alter", "grant")


def _record_statements():
    """Capture every SQL statement the engine executes, at the cursor level."""
    from sqlalchemy import event
    seen = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    return seen, lambda: event.remove(engine, "before_cursor_execute", _before)


def _mutating(statements):
    out = []
    for st in statements:
        head = " ".join(st.strip().split()).lower()
        # Ignore the word appearing inside a quoted literal or an identifier such as
        # information_schema; only a leading verb (or a nested one) actually mutates.
        for verb in _MUTATING:
            if head.startswith(verb) or f") {verb} " in head or f"; {verb} " in head:
                out.append(st.strip()[:200])
                break
    return out


def test_a_dry_run_issues_no_insert_update_or_delete_statement_at_all():
    sha, p, a, b = _safe_pair()
    _source(a, _SYS_A, "u://dz1"), _source(b, _SYS_A, "u://dz1"), _source(b, _SYS_B, "u://dz2")
    _ocr(a), _ocr(b)
    _fact(b, "tax_year", "2024")
    plan_doc = {**dx.plan(), "partitions": _plan_for(sha)}

    seen, stop = _record_statements()
    try:
        r = dx.apply(plan_doc=plan_doc)                      # dry run
    finally:
        stop()
    assert r["dry_run"] is True and r["wrote_anything"] is False
    assert r["partitions_prepared"] == 1, "it must still do the full validation work"
    offenders = _mutating(seen)
    assert offenders == [], f"dry run issued mutating SQL: {offenders}"
    assert any("SELECT" in s.upper() for s in seen), "sanity: it really did query"
    assert any("FOR UPDATE" in s.upper() for s in seen), "it still takes the same locks"


def test_a_dry_run_computes_the_exact_mutations_without_performing_them():
    sha, p, a, b = _safe_pair()
    _source(a, _SYS_A, "u://same"), _source(b, _SYS_A, "u://same")   # redundant -> would delete
    _source(b, _SYS_B, "u://moves")                                   # distinct  -> would repoint
    r = dx.apply(plan_doc={**dx.plan(), "partitions": _plan_for(sha)})
    assert r["would_retire_rows"] == 1
    assert r["would_delete_by_table"]["document_sources.document_id"] == 1
    assert r["would_reassign_by_table"]["document_sources.document_id"] == 1
    assert r["reassigned_by_table"] == {} and r["deleted_by_table"] == {}
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM document_sources WHERE document_id = :i"),
                         {"i": b}).scalar_one() == 2, "nothing moved"
    assert _status(b) == "active"


def test_an_apply_issues_mutations_so_the_instrument_is_proven_to_work():
    """Control for the test above: the same instrument DOES see writes on a real apply."""
    sha, p, a, b = _safe_pair()
    seen, stop = _record_statements()
    try:
        _apply(sha)
    finally:
        stop()
    offenders = _mutating(seen)
    assert offenders, "the instrument must be able to detect mutating SQL"
    assert any(s.upper().startswith("UPDATE DOCUMENTS") for s in offenders)


def test_the_only_module_code_that_writes_is_the_execute_phase():
    """Structural: no mutating SQL may appear outside _execute_prepared."""
    import ast
    import pathlib
    src = pathlib.Path("app/services/document_merge_execute.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    writers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = (ast.get_source_segment(src, node) or "").replace("FOR UPDATE", "")
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            if verb in body:
                writers.add(node.name)
    assert writers <= {"_execute_prepared"}, f"unexpected writers: {sorted(writers)}"


# === pre-merge evidence is sufficient for forensic reconstruction ==================================

def test_the_audit_record_carries_everything_needed_to_reconstruct_the_pre_merge_state():
    sha, p, a, b = _safe_pair()
    _source(a, _SYS_A, "u://ev"), _source(b, _SYS_A, "u://ev")    # redundant -> deleted
    _source(b, _SYS_B, "u://ev2")                                  # distinct  -> repointed
    _fact(b, "tax_year", "2024")
    _version(b, 7)
    r = _apply(sha)
    assert r["partitions_applied"] == 1

    md = _audits(r["run_id"])[0]["metadata"]
    md = json.loads(md) if isinstance(md, str) else md

    # identity of the merge
    assert md["survivor_document_id"] == a and md["retired_document_ids"] == [b]
    assert md["sha256"] == sha and md["owner"]["person_id"] == p
    assert md["run_id"] == r["run_id"] and len(md["fingerprint"]) == 64
    assert md["classification_at_execution"] == dm.SAFE

    # original status / deleted_at of every participating document, BEFORE the merge
    pre = {d["document_id"]: d for d in md["pre_merge_documents"]}
    assert set(pre) == {a, b}
    assert pre[b]["status"] == "active" and pre[b]["deleted_at"] is None
    assert pre[b]["person_id"] == p and pre[b]["sha256"] == sha

    # every dependency action, with original row ids and original reference values
    actions = {f"{x['table']}.{x['column']}": x for x in md["dependency_actions"]}
    assert "document_sources.document_id" in actions
    src_action = actions["document_sources.document_id"]
    assert all(m["original_value"] == b for m in src_action["repoint"])
    assert all(isinstance(m["row_id"], int) for m in src_action["repoint"])
    # a row DELETED as redundant is recorded in full, so it can be re-inserted verbatim
    gone = src_action["delete"]
    assert len(gone) == 1
    row = gone[0]["original_row"]
    assert row["document_id"] == b
    assert row["source_system"] == _SYS_A and row["source_uri"] == "u://ev"
    assert "id" in row and "source_path" in row, "the FULL original row, not a digest"

    # provenance as it stood before the merge
    tuples = {(t["document_id"], t["source_system"], t["source_uri"])
              for t in md["pre_merge_provenance_tuples"]}
    assert (b, _SYS_A, "u://ev") in tuples and (a, _SYS_A, "u://ev") in tuples
    assert (b, _SYS_B, "u://ev2") in tuples

    # repointed rows elsewhere are recorded too
    assert "document_versions.document_id" in actions


def test_a_deleted_redundant_row_is_reconstructible_from_evidence_plus_the_retained_twin():
    """Reconstruction, actually performed: non-content columns from the record, content from the
    retained twin the record names."""
    sha, p, a, b = _safe_pair()
    body = "OCR BODY TEXT for reconstruction"
    _ocr(a, chars=31), _ocr(b, chars=31)
    with engine.begin() as c:
        c.execute(text("UPDATE document_ocr SET text = :t WHERE document_id = ANY(:i)"),
                  {"t": body, "i": [a, b]})
    r = _apply(sha)
    assert r["partitions_applied"] == 1
    rec = next(x for x in r["applied"][0]["dependency_actions"]
               if x["table"] == "document_ocr")["delete"][0]

    assert rec["original_document_id"] == b
    assert "text" not in rec["original_row"], "content is not in the record"
    assert rec["content_columns_omitted"] == ["last_error", "text"]
    assert rec["content_matches_retained"] is True
    kept_id = rec["identical_to_retained_row_id"]
    assert kept_id is not None

    with engine.begin() as c:
        kept = c.execute(text("SELECT * FROM document_ocr WHERE id = :i"),
                         {"i": kept_id}).mappings().one()
        assert kept["document_id"] == a, "the retained twin lives on the survivor"
        # Rebuild the deleted row: structural columns from the record, content from the twin.
        rebuilt = {k: v for k, v in rec["original_row"].items() if k != "id"}
        rebuilt["text"] = kept["text"]
        rebuilt["last_error"] = kept["last_error"]
        cols = list(rebuilt)
        c.execute(text(f"INSERT INTO document_ocr ({', '.join(cols)}) VALUES "
                       f"({', '.join(':' + k for k in cols)})"), rebuilt)
        back = c.execute(text("SELECT document_id, status, char_count, text FROM document_ocr"
                              " WHERE document_id = :i"), {"i": b}).one()
    assert back[0] == b and back[1] == "completed" and back[2] == 31
    assert back[3] == body, "the original content is recovered exactly"
    import hashlib
    assert rec["content_digest"]["text"]["sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_no_document_or_ocr_body_text_reaches_the_audit_chain_or_the_run_artifact():
    secret = "SSN 123-45-6789 CONFIDENTIAL W2 BODY"
    sha, p, a, b = _safe_pair()
    _ocr(a), _ocr(b)
    _fact(a, "wages", secret), _fact(b, "wages", secret)      # redundant current fact
    with engine.begin() as c:
        c.execute(text("UPDATE document_ocr SET text = :t WHERE document_id = ANY(:i)"),
                  {"t": secret, "i": [a, b]})
    r = _apply(sha)
    assert r["partitions_applied"] == 1

    with engine.begin() as c:
        raw = c.execute(text("SELECT metadata::text FROM audit_events WHERE action = :x"
                             " ORDER BY id DESC LIMIT 1"), {"x": dx.AUDIT_ACTION}).scalar()
    assert secret not in raw, "OCR/fact body text must never enter the hash-chained audit"
    assert secret not in json.dumps(r, default=str), "nor the run artifact"
    # ...while the evidence that IS kept remains sufficient.
    ocr = next(x for x in r["applied"][0]["dependency_actions"]
               if x["table"] == "document_ocr")["delete"][0]
    assert ocr["content_digest"]["text"]["sha256"] and ocr["content_digest"]["text"]["length"] > 0
    assert ocr["identical_to_retained_row_id"] and ocr["content_matches_retained"] is True
    facts = next(x for x in r["applied"][0]["dependency_actions"]
                 if x["table"] == "document_facts")["delete"][0]
    assert "fact_value" not in facts["original_row"]
    assert facts["content_digest"]["fact_value"]["sha256"]
    assert facts["identical_to_retained_row_id"]


def test_ocr_content_that_is_not_actually_identical_refuses_the_partition():
    """The whole redaction rests on the twin being identical; prove a difference is refused."""
    sha, p, a, b = _safe_pair()
    _ocr(a, chars=40), _ocr(b, chars=40)                      # preview sees equal char_count
    with engine.begin() as c:
        c.execute(text("UPDATE document_ocr SET text = 'ALPHA' WHERE document_id = :i"), {"i": a})
        c.execute(text("UPDATE document_ocr SET text = 'OMEGA' WHERE document_id = :i"), {"i": b})
    r = _apply(sha)
    assert r["partitions_applied"] == 0 and r["partitions_refused"] == 1
    assert "document_ocr row" in r["refused"][0]["detail"]
    assert "text" in r["refused"][0]["detail"]
    assert _status(a) == "active" and _status(b) == "active", "refusal precedes every write"
    with engine.begin() as c:
        assert c.execute(text("SELECT text FROM document_ocr WHERE document_id = :i"),
                         {"i": b}).scalar() == "OMEGA", "distinct content is never discarded"


def test_repointed_rows_never_carry_any_column_value_into_the_evidence():
    sha, p, a, b = _safe_pair()
    _fact(b, "employer", "Acme Payroll Inc")                  # repointed, not deleted
    r = _apply(sha)
    actions = r["applied"][0]["dependency_actions"]
    for act in actions:
        for moved in act["repoint"]:
            assert set(moved) == {"row_id", "original_value"}
    assert "Acme Payroll Inc" not in json.dumps(r, default=str)


def test_the_evidence_is_json_serialisable_so_it_survives_in_the_plan_and_the_audit_chain():
    sha, p, a, b = _safe_pair()
    _ocr(b, chars=7), _classification(b, "1099"), _fact(b, "y", "2024")
    r = _apply(sha)
    blob = json.dumps(r["applied"][0])              # no default= fallback: must already be clean
    assert json.loads(blob)["survivor_document_id"] == a


def test_the_reported_retirement_mechanism_does_not_claim_full_restorability():
    r = dx.apply(plan_doc={**dx.plan(), "partitions": []})
    mech = r["retirement_mechanism"]
    assert "status='deleted'" in mech and "deleted_at" in mech
    assert "merged_into_canonical" in mech and "audit" in mech
    assert "NOT undone by document_platform.restore()" in mech
    assert "pre_merge_" in mech
    import pathlib
    src = pathlib.Path("app/services/document_merge_execute.py").read_text(encoding="utf-8")
    assert "THE MERGE IS NOT REVERSIBLE BY document_platform.restore()" in src
    assert "THERE IS NO ROLLBACK EXECUTOR" in src
    assert "The row is preserved and restorable." not in src


#: Unbounded-text columns on deletable tables that are STRUCTURAL (identifiers, labels, locators,
#: engine names) rather than document-derived content. Declaring them here is what makes the guard
#: below meaningful: a NEW text column on one of these tables fails until someone classifies it.
_KNOWN_STRUCTURAL = {
    "document_ocr": {"status", "engine"},
    "document_facts": {"fact_type", "extraction_engine", "extractor_version"},
    "document_classifications": {"doc_type", "classifier_version"},
    "document_sources": {"source_system", "source_uri", "source_path", "source_external_id"},
    "document_relationships": {"entity_type", "relationship_type"},
    # Normalized image renditions: derivative kind/state, the source + derivative MIME types, the
    # content-addressed derivative locator and the engine name. No document content — the only
    # free-text column that can echo the upload (last_error, which names the original file) is
    # declared in _CONTENT_COLUMNS instead.
    "document_derivatives": {"kind", "status", "source_mime", "derivative_mime",
                             "derivative_path", "engine"},
    # A disposable read-model projection: lifecycle/status labels, no document content.
    "rm_document_status": {"status", "classification", "last_event_type"},
}


def test_every_text_column_on_a_deletable_table_is_classified_content_or_structural():
    """Future-proofing: a new free-text column must be triaged, not silently recorded."""
    deletable = set(dx._SINGULAR) | set(dx._DEDUP_KEYS)
    assert deletable == set(_KNOWN_STRUCTURAL), (
        "the set of tables rows are deleted from changed; re-triage their text columns")
    with engine.begin() as c:
        for table in sorted(deletable):
            cols = {r[0] for r in c.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = :t "
                "AND data_type IN ('text','character varying') "
                "AND character_maximum_length IS NULL"), {"t": table})}
            classified = set(dx._CONTENT_COLUMNS.get(table, ())) | _KNOWN_STRUCTURAL[table]
            unclassified = cols - classified
            assert not unclassified, (
                f"{table}: unclassified free-text column(s) {sorted(unclassified)} - declare them "
                f"in _CONTENT_COLUMNS if they hold document content")


def test_content_columns_are_declared_for_every_table_that_holds_document_body_text():
    assert dx._CONTENT_COLUMNS["document_ocr"] == ("text", "last_error")
    assert dx._CONTENT_COLUMNS["document_facts"] == ("fact_value",)
    # Tables rows are only ever REPOINTED from carry no row content at all, so they need no rule.
    assert set(dx._CONTENT_COLUMNS) <= set(dx._SINGULAR) | set(dx._DEDUP_KEYS)


def test_the_match_proof_is_computed_from_the_two_rows_not_asserted():
    """content_matches_retained must be derived. A hardcoded True would be a lying proof.

    No public path can reach a mismatch (singular refuses first, dedup matches by key), so the
    record builder is exercised directly with rows that do NOT match."""
    same_a = {"id": 1, "document_id": 10, "status": "completed", "text": "IDENTICAL",
              "last_error": None}
    same_b = {"id": 2, "document_id": 11, "status": "completed", "text": "IDENTICAL",
              "last_error": None}
    rec = dx._deleted_record("document_ocr", same_b, same_a)
    assert rec["content_matches_retained"] is True
    assert rec["identical_to_retained_row_id"] == 1
    assert "text" not in rec["original_row"]

    different = {**same_a, "id": 3, "text": "DIFFERENT CONTENT"}
    bad = dx._deleted_record("document_ocr", same_b, different)
    assert bad["content_matches_retained"] is False, "a mismatch must be reported, not claimed"
    assert bad["content_digest"]["text"]["sha256"] != \
        bad["retained_content_digest"]["text"]["sha256"]

    orphan = dx._deleted_record("document_ocr", same_b, None)
    assert orphan["content_matches_retained"] is None
    assert orphan["identical_to_retained_row_id"] is None


def test_a_content_digest_never_embeds_the_value_it_summarises():
    body = "PAYROLL BODY 987-65-4321"
    d = dx._digest(body)
    assert d["sha256"] == __import__("hashlib").sha256(body.encode()).hexdigest()
    assert d["length"] == len(body)
    assert body not in json.dumps(d)
    assert dx._digest(None) == {"sha256": None, "length": 0, "is_null": True}


# === run STATUS and exit code =====================================================================
# The production defect this section exists to prevent: a run that applied 1,068 partitions and
# refused 931 printed "APPLIED" and exited 0, because the status was derived from whether a batch
# had RAISED rather than from what happened to every planned partition.

def _cli_run(monkeypatch, capsys, plan_rows, plan_doc):
    import scripts.execute_document_merge as cli
    doc = {**plan_doc, "partitions": plan_rows, "safe_partitions": len(plan_rows),
           "rows_to_retire": sum(p["rows_to_retire"] for p in plan_rows)}
    monkeypatch.setattr(dx, "plan", lambda **kw: doc)
    code = cli.main(["--apply", "--expected-safe-partitions", str(len(plan_rows)),
                     "--expected-retirement-rows", str(doc["rows_to_retire"])])
    return code, capsys.readouterr().out


def test_status_success_when_every_planned_partition_applies(monkeypatch, capsys):
    shas = [_safe_pair()[0] for _ in range(3)]
    plan_doc = dx.plan()
    mine = [p for p in plan_doc["partitions"] if p["sha256"] in shas]
    code, out = _cli_run(monkeypatch, capsys, mine, plan_doc)
    assert code == 0
    assert "APPLIED - SUCCESS" in out
    assert "FINAL STATUS        : SUCCESS   (exit 0)" in out


def _good_and_doomed(n_good=2, n_bad=2):
    """n_good partitions that will apply, plus n_bad whose fingerprints are tampered so they are
    refused at revalidation - a refusal, with nothing raised anywhere."""
    good = [_safe_pair()[0] for _ in range(n_good)]
    bad = [_safe_pair()[0] for _ in range(n_bad)]
    plan_doc = dx.plan()
    keep = [p for p in plan_doc["partitions"] if p["sha256"] in good]
    doomed = [{**p, "fingerprint": "0" * 64} for p in plan_doc["partitions"]
              if p["sha256"] in bad]
    assert len(keep) == n_good and len(doomed) == n_bad
    return plan_doc, keep, doomed


def _apply_mixed(plan_doc, rows):
    total = sum(p["rows_to_retire"] for p in rows)
    return dx.apply(plan_doc={**plan_doc, "partitions": rows, "safe_partitions": len(rows),
                              "rows_to_retire": total},
                    apply_writes=True, expected_safe_partitions=len(rows),
                    expected_retirement_rows=total)


def test_status_partial_when_some_partitions_are_refused_even_though_nothing_raised():
    """The exact production shape: applied + refused, no exception anywhere."""
    plan_doc, keep, doomed = _good_and_doomed()
    r = _apply_mixed(plan_doc, keep + doomed)
    assert r["failed_batch"] is None, "nothing raised - this is the production shape"
    assert r["partitions_applied"] == 2 and r["partitions_refused"] == 2
    assert r["status"] == dx.STATUS_PARTIAL
    assert r["exit_code"] == 3
    assert r["partial_apply"] is True


def test_the_cli_exits_3_and_never_prints_applied_for_a_partial_run(monkeypatch, capsys):
    plan_doc, keep, doomed = _good_and_doomed()
    code, out = _cli_run(monkeypatch, capsys, keep + doomed, plan_doc)
    assert code == 3, "a partial run must exit 3"
    assert "PARTIAL" in out
    assert "APPLIED - SUCCESS" not in out
    assert "FINAL STATUS        : PARTIAL   (exit 3)" in out
    assert "partitions applied  : 2" in out and "partitions refused  : 2" in out


def test_status_failed_and_exit_4_when_nothing_commits(monkeypatch, capsys):
    plan_doc, _keep, doomed = _good_and_doomed(0, 2)
    code, out = _cli_run(monkeypatch, capsys, doomed, plan_doc)
    assert code == 4, "a zero-commit run must exit 4"
    assert "FAILED - NOTHING COMMITTED" in out
    assert "FINAL STATUS        : FAILED   (exit 4)" in out
    assert "APPLIED" not in out.split("FINAL STATUS")[0].replace("partitions applied", "")


def test_a_keyboard_interrupt_is_never_reported_as_success(monkeypatch):
    """Production lost a run to Ctrl-C; the operator still needed the committed batch list."""
    shas = [_safe_pair()[0] for _ in range(4)]
    plan_doc = dx.plan()
    mine = sorted([p for p in plan_doc["partitions"] if p["sha256"] in shas],
                  key=lambda p: p["survivor_document_id"])
    real = dx._execute_prepared
    calls = {"n": 0}

    def _interrupt(conn, prepared, run_id, actor, request_id):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt()
        return real(conn, prepared, run_id, actor, request_id)

    monkeypatch.setattr(dx, "_execute_prepared", _interrupt)
    # apply() must RETURN a result rather than let the interrupt escape - otherwise the operator
    # loses the committed-batch list, which is exactly what happened in production.
    try:
        r = dx.apply(plan_doc={**plan_doc, "partitions": mine, "safe_partitions": 4,
                               "rows_to_retire": 4},
                     apply_writes=True, batch_size=2, expected_safe_partitions=4,
                     expected_retirement_rows=4)
    except BaseException as exc:                       # noqa: BLE001 - the point of the test
        raise AssertionError(
            f"apply() let {type(exc).__name__} escape; the committed-batch list is lost") from None
    assert r["status"] == dx.STATUS_PARTIAL and r["exit_code"] == 3
    assert r["failed_batch"]["error"] == "KeyboardInterrupt"
    assert r["committed_batches"] == [1]
    assert r["partitions_applied"] == 2 and r["partitions_failed"] == 2
    assert r["wrote_anything"] is True


def test_a_keyboard_interrupt_before_any_commit_is_FAILED_not_success(monkeypatch):
    _safe_pair()
    plan_doc = dx.plan()

    def _interrupt(conn, prepared, run_id, actor, request_id):
        raise KeyboardInterrupt()

    monkeypatch.setattr(dx, "_execute_prepared", _interrupt)
    try:
        r = dx.apply(plan_doc=plan_doc, apply_writes=True,
                     expected_safe_partitions=plan_doc["safe_partitions"],
                     expected_retirement_rows=plan_doc["rows_to_retire"])
    except BaseException as exc:                       # noqa: BLE001
        raise AssertionError(f"apply() let {type(exc).__name__} escape") from None
    assert r["status"] == dx.STATUS_FAILED and r["exit_code"] == 4
    assert r["partitions_applied"] == 0


def test_the_result_artifact_and_the_human_heading_always_agree(monkeypatch, capsys):
    """The heading is rendered FROM the status field, so text and artifact cannot diverge."""
    import scripts.execute_document_merge as cli
    plan_doc, keep, doomed = _good_and_doomed(2, 1)
    doc = {**plan_doc, "partitions": keep + doomed, "safe_partitions": 3,
           "rows_to_retire": sum(p["rows_to_retire"] for p in keep + doomed)}
    monkeypatch.setattr(dx, "plan", lambda **kw: doc)
    code = cli.main(["--apply", "--json", "--expected-safe-partitions", "3",
                     "--expected-retirement-rows", str(doc["rows_to_retire"])])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "PARTIAL"
    assert payload["exit_code"] == code == 3
    assert payload["partitions_applied"] == 2 and payload["partitions_refused"] == 1


def test_the_json_artifact_carries_the_status_and_every_required_count():
    plan_doc, keep, doomed = _good_and_doomed(2, 1)
    r = _apply_mixed(plan_doc, keep + doomed)
    for key in ("status", "exit_code", "partitions_planned", "partitions_applied",
                "partitions_refused", "partitions_failed", "partitions_not_attempted",
                "planned_retirement_rows", "rows_retired", "reassignments_total"):
        assert key in r, key
    assert json.loads(json.dumps(r, default=str))["status"] == "PARTIAL"
    assert r["partitions_planned"] == (r["partitions_applied"] + r["partitions_refused"]
                                       + r["partitions_failed"] + r["partitions_not_attempted"])


def test_a_dry_run_status_is_dry_run_and_exits_zero(monkeypatch, capsys):
    import scripts.execute_document_merge as cli
    _safe_pair()
    r = dx.apply(plan_doc=dx.plan())
    assert r["status"] == dx.STATUS_DRY_RUN and r["exit_code"] == 0
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "APPLIED - SUCCESS" not in out
