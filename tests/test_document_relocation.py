"""Guarded canonical document-storage relocation.

The properties defended here: a source file is never destroyed, a plan/dry run changes nothing at
all (database OR filesystem), a pathname never decides a destination on its own, and no row is
repointed until the destination bytes verify.
"""
import hashlib
import json
import os
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from app.services import document_relocation as dr

_TAG = "DMRTEST"
_SYS_SP, _SYS_TD, _SYS_DR = "SharePoint", "TaxDome", "Drake"


@pytest.fixture(autouse=True)
def _canonical_root(tmp_path, monkeypatch):
    """A real temporary tree standing in for D:\\360PlusData, via the EXISTING env var."""
    root = tmp_path / "360PlusData"
    monkeypatch.setenv("CLIENT360_DATA_ROOT", str(root))
    for var in ("CLIENT360_TAXDOME_DOCUMENT_ROOT", "CLIENT360_SHAREPOINT_DOCUMENT_ROOT",
                "CLIENT360_DRAKE_DOCUMENT_ROOT", "CLIENT360_MIGRATION_DEST_ROOT"):
        monkeypatch.delenv(var, raising=False)
    yield root
    with engine.begin() as c:
        c.execute(text("DELETE FROM document_sources WHERE document_id IN"
                       " (SELECT id FROM documents WHERE original_name LIKE :p)"), {"p": f"{_TAG}%"})
        c.execute(text("DELETE FROM documents WHERE original_name LIKE :p"), {"p": f"{_TAG}%"})


def _legacy(tmp_path, *parts):
    """A stand-in for C:\\Client360\\Data\\Documents\\... - monkeypatched onto the same constant."""
    return os.path.join(str(tmp_path), *parts)


@pytest.fixture(autouse=True)
def _legacy_roots(tmp_path, monkeypatch):
    doc_root = str(tmp_path / "Client360" / "Data" / "Documents")
    content = str(tmp_path / "Client360D" / "Content")
    monkeypatch.setattr(dr, "_LEGACY_DOC_ROOT", doc_root)
    monkeypatch.setattr(dr, "_LEGACY_D_CONTENT", content)
    return doc_root, content


def _write(path, body: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


def _doc(*, uri, sha=None, size=None, path=None, provider="local", status="active"):
    t = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO documents (original_name, stored_name, storage_path, storage_uri,"
            " storage_provider, size_bytes, sha256, status, archived)"
            " VALUES (:n,:s,:p,:u,:prov,:sz,:sha,:st,false) RETURNING id"),
            {"n": f"{_TAG} {t}.pdf", "s": f"dmr-{t}", "p": path if path is not None else (uri or ""),
             "u": uri, "prov": provider, "sz": size if size is not None else 1,
             "sha": sha or hashlib.sha256(t.encode()).hexdigest(), "st": status}).scalar_one()


def _source(document_id, system, uri="probe://x"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri)"
                       " VALUES (:d,:s,:u)"), {"d": document_id, "s": system, "u": uri})


def _entry(doc_id, plan_doc=None):
    plan_doc = plan_doc or dr.plan()
    return next((r for r in plan_doc["rows"] if r["document_id"] == doc_id), None)


def _make(tmp_path, body: bytes, *parts, system=None, root="docs", rel_path=None):
    """Create a real source file + a documents row pointing at it, with optional provenance.

    ``rel_path`` reproduces the PRODUCTION shape: an absolute storage_uri alongside a
    source-relative storage_path (forward slashes), rather than two absolute duplicates."""
    base = str(tmp_path / "Client360" / "Data" / "Documents") if root == "docs" \
        else str(tmp_path / "Client360D" / "Content")
    src = _write(os.path.join(base, *parts), body)
    did = _doc(uri=src, sha=hashlib.sha256(body).hexdigest(), size=len(body), path=rel_path)
    if system:
        _source(did, system)
    return did, src


def _apply(doc_id=None, *, plan_doc=None, **kw):
    plan_doc = plan_doc or dr.plan()
    if doc_id is not None:
        rows = [r for r in plan_doc["rows"] if r["document_id"] == doc_id]
        plan_doc = {**plan_doc, "rows": rows,
                    "expected_safe_rows": sum(1 for r in rows if r["classification"] == dr.SAFE),
                    "expected_safe_bytes": sum(r["size_bytes"] for r in rows
                                               if r["classification"] == dr.SAFE)}
    kw.setdefault("expected_safe_rows", plan_doc["expected_safe_rows"])
    kw.setdefault("expected_safe_bytes", plan_doc["expected_safe_bytes"])
    kw.setdefault("expected_plan_fingerprint", plan_doc["plan_fingerprint"])
    return dr.apply(plan_doc=plan_doc, apply_writes=True, **kw)


def _row(doc_id):
    with engine.begin() as c:
        return dict(c.execute(text("SELECT * FROM documents WHERE id = :i"),
                              {"i": doc_id}).mappings().one())


# === mapping rules ================================================================================

@pytest.mark.parametrize("folder", ["TaxDome", "taxdome", "TAXDOME"])
def test_taxdome_path_case_variants_all_map_to_one_canonical_root(tmp_path, folder):
    """Requirement A/B: Windows path comparison is case-insensitive."""
    did, src = _make(tmp_path, b"td", folder, "2024", "f.pdf", system=_SYS_TD)
    e = _entry(did)
    assert e["classification"] == dr.SAFE
    assert e["current_root"] == "legacy:TaxDome"
    assert dr._norm(e["destination"]).startswith(dr._norm(dr.canonical_roots()["TaxDome"]))
    assert e["destination"].endswith(os.path.join("2024", "f.pdf"))


def test_sharepoint_path_maps_to_the_sharepoint_canonical_root(tmp_path):
    did, _ = _make(tmp_path, b"sp", "SharePoint", "Clients", "a.pdf", system=_SYS_SP)
    e = _entry(did)
    assert e["classification"] == dr.SAFE
    assert dr._norm(e["destination"]).startswith(dr._norm(dr.canonical_roots()["SharePoint"]))


def test_drake_path_maps_to_the_drake_canonical_root(tmp_path):
    did, _ = _make(tmp_path, b"dk", "Drake", "2024", "r.pdf", system=_SYS_DR)
    e = _entry(did)
    assert e["classification"] == dr.SAFE
    assert dr._norm(e["destination"]).startswith(dr._norm(dr.canonical_roots()["Drake"]))


def test_old_d_content_with_sharepoint_provenance_resolves_by_provenance(tmp_path):
    did, _ = _make(tmp_path, b"c1", "Clients", "x.pdf", system=_SYS_SP, root="content")
    e = _entry(did)
    assert e["classification"] == dr.SAFE
    assert e["current_root"] == "legacy:Content"
    assert "d_content_resolved_by_provenance" in e["reason_codes"]
    assert dr._norm(e["destination"]).startswith(dr._norm(dr.canonical_roots()["SharePoint"]))


def test_old_d_content_with_absent_provenance_is_review_required(tmp_path):
    did, _ = _make(tmp_path, b"c2", "Clients", "y.pdf", root="content")
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert e["destination"] is None, "a pathname alone must never infer a destination"
    assert "d_content_requires_provenance" in e["reason_codes"]
    assert "provenance_absent" in e["reason_codes"]


def test_old_d_content_with_ambiguous_provenance_is_review_required(tmp_path):
    did, _ = _make(tmp_path, b"c3", "Clients", "z.pdf", system=_SYS_SP, root="content")
    _source(did, _SYS_TD, "probe://second")
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert e["destination"] is None
    assert "provenance_ambiguous_multiple_sources" in e["reason_codes"]


def test_provenance_conflicting_with_the_path_source_is_blocked(tmp_path):
    did, _ = _make(tmp_path, b"c4", "TaxDome", "q.pdf", system=_SYS_SP)
    e = _entry(did)
    assert e["classification"] == dr.BLOCKED
    assert e["destination"] is None
    assert "provenance_conflicts_with_path_source" in e["reason_codes"]


def test_a_legacy_documents_row_without_provenance_is_review_required(tmp_path):
    did, _ = _make(tmp_path, b"c5", "TaxDome", "n.pdf")
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert "provenance_cannot_confirm_path_source" in e["reason_codes"]


def test_a_row_already_under_the_canonical_root_is_already_canonical(_canonical_root):
    dest = os.path.join(dr.canonical_roots()["TaxDome"], "2024", "already.pdf")
    did = _doc(uri=dest, size=3, sha=hashlib.sha256(b"abc").hexdigest())
    _source(did, _SYS_TD)
    e = _entry(did)
    assert e["classification"] == dr.ALREADY_CANONICAL
    assert e["destination"] is None


def test_an_empty_storage_uri_is_its_own_classification():
    did = _doc(uri="", path="")
    e = _entry(did)
    assert e["classification"] == dr.EMPTY_URI
    assert e["destination"] is None
    assert "empty_storage_uri" in e["reason_codes"]


def test_an_unrecognised_root_is_review_required_never_relocated():
    did = _doc(uri=r"E:\Somewhere\Else\file.pdf")
    _source(did, _SYS_SP)
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert "unrecognised_storage_root" in e["reason_codes"]
    assert e["destination"] is None


def test_a_curated_repository_item_is_review_required(_canonical_root):
    did = _doc(uri=os.path.join(dr.repository_root(), "Clients", "curated.pdf"))
    _source(did, _SYS_SP)
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert "curated_repository_item" in e["reason_codes"]


def test_a_safe_row_missing_sha_or_size_falls_back_to_review(tmp_path):
    did, src = _make(tmp_path, b"m", "Drake", "m.pdf", system=_SYS_DR)
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET size_bytes = 0 WHERE id = :i"), {"i": did})
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert "missing_sha_or_size" in e["reason_codes"]


# === zero-mutation plan and dry run ===============================================================

_MUTATING = ("insert", "update", "delete", "truncate", "merge", "copy", "create", "drop", "alter")


def _record_sql():
    from sqlalchemy import event
    seen = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    return seen, lambda: event.remove(engine, "before_cursor_execute", _before)


def _mutating(statements):
    out = []
    for st in statements:
        head = " ".join(st.strip().split()).lower().replace("for update", "")
        if any(head.startswith(v) for v in _MUTATING):
            out.append(st.strip()[:160])
    return out


def _snapshot():
    with engine.begin() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
                for t in ("documents", "document_sources", "audit_events", "document_events")}


def _tree(root):
    out = set()
    for base, _dirs, files in os.walk(root):
        for f in files:
            out.add(os.path.join(base, f))
    return out


def test_plan_issues_no_database_mutation_and_touches_no_file(tmp_path, _canonical_root):
    _make(tmp_path, b"p1", "TaxDome", "a.pdf", system=_SYS_TD)
    _make(tmp_path, b"p2", "SharePoint", "b.pdf", system=_SYS_SP)
    before_db, before_fs = _snapshot(), _tree(tmp_path)
    seen, stop = _record_sql()
    try:
        p = dr.plan()
    finally:
        stop()
    assert _mutating(seen) == [], "plan issued mutating SQL"
    assert p["hashed_any_file"] is False and p["wrote_anything"] is False
    assert _snapshot() == before_db
    assert _tree(tmp_path) == before_fs, "plan created or changed a file"
    assert not os.path.exists(_canonical_root), "plan must not even create the destination root"


def test_a_dry_run_issues_no_database_and_no_filesystem_mutation(tmp_path, _canonical_root):
    did, src = _make(tmp_path, b"dry-run-body", "Drake", "d.pdf", system=_SYS_DR)
    before_db, before_fs = _snapshot(), _tree(tmp_path)
    storage = dr.RelocationStorage()
    seen, stop = _record_sql()
    try:
        r = dr.apply(plan_doc=dr.plan(), storage=storage)          # dry run
    finally:
        stop()
    assert r["dry_run"] is True and r["wrote_anything"] is False
    assert r["rows_verified"] == 1, "it still verifies"
    assert _mutating(seen) == [], "dry run issued mutating SQL"
    assert r["filesystem_mutations"] == 0
    assert [op for op, _ in storage.operations if op in ("copy", "makedirs")] == []
    assert _snapshot() == before_db
    assert _tree(tmp_path) == before_fs
    assert not os.path.exists(_canonical_root)
    assert _row(did)["storage_uri"] == src


def test_an_apply_does_mutate_so_the_instruments_are_proven_to_work(tmp_path):
    did, _ = _make(tmp_path, b"control", "Drake", "c.pdf", system=_SYS_DR)
    storage = dr.RelocationStorage()
    seen, stop = _record_sql()
    try:
        _apply(did, storage=storage)
    finally:
        stop()
    assert _mutating(seen), "the SQL instrument must be able to see writes"
    assert [op for op, _ in storage.operations if op == "copy"], "and the FS instrument too"


# === copy / verify / repoint ======================================================================

def test_copy_verify_and_repoint_moves_the_location_and_leaves_the_source(tmp_path):
    body = b"canonical body bytes"
    did, src = _make(tmp_path, body, "TaxDome", "2024", "r.pdf", system=_SYS_TD)
    before = _row(did)
    r = _apply(did)
    assert r["rows_relocated"] == 1 and r["rows_refused"] == 0
    after = _row(did)

    dest = after["storage_uri"]
    assert dr._norm(dest).startswith(dr._norm(dr.canonical_roots()["TaxDome"]))
    assert after["storage_path"] == dest
    assert os.path.exists(dest) and open(dest, "rb").read() == body
    assert os.path.exists(src), "the SOURCE must remain as the rollback"
    assert open(src, "rb").read() == body, "and must be byte-identical"

    for field in ("id", "person_id", "household_id", "organization_id", "sha256", "size_bytes",
                  "original_name", "stored_name", "status", "storage_provider", "category",
                  "classification"):
        assert after[field] == before[field], f"{field} must not change"


def test_a_missing_source_is_refused_before_anything_is_written(tmp_path):
    did, src = _make(tmp_path, b"gone", "Drake", "g.pdf", system=_SYS_DR)
    os.remove(src)
    r = _apply(did)
    assert r["rows_relocated"] == 0 and r["rows_refused"] == 1
    assert "source missing" in r["refused"][0]["detail"]
    assert _row(did)["storage_uri"] == src


def test_a_source_size_mismatch_is_refused(tmp_path):
    did, src = _make(tmp_path, b"1234567890", "Drake", "s.pdf", system=_SYS_DR)
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET size_bytes = 999 WHERE id = :i"), {"i": did})
    r = _apply(did)
    assert r["rows_refused"] == 1 and "source size" in r["refused"][0]["detail"]
    assert _row(did)["storage_uri"] == src


def test_a_source_hash_mismatch_is_refused(tmp_path):
    did, src = _make(tmp_path, b"realbody", "Drake", "h.pdf", system=_SYS_DR)
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET sha256 = :s WHERE id = :i"),
                  {"s": hashlib.sha256(b"different").hexdigest(), "i": did})
    r = _apply(did)
    assert r["rows_refused"] == 1
    assert "source sha256 does not match" in r["refused"][0]["detail"]
    assert _row(did)["storage_uri"] == src


def test_an_identical_existing_destination_is_idempotent_success(tmp_path):
    body = b"already there"
    did, src = _make(tmp_path, body, "TaxDome", "i.pdf", system=_SYS_TD)
    dest = _entry(did)["destination"]
    _write(dest, body)                                   # pre-existing identical bytes
    storage = dr.RelocationStorage()
    r = _apply(did, storage=storage)
    assert r["rows_relocated"] == 1
    assert r["relocated"][0]["copy_verification"]["reused_existing_destination"] is True
    assert r["relocated"][0]["copy_verification"]["copy_performed"] is False
    assert [op for op, _ in storage.operations if op == "copy"] == [], "no copy was needed"
    assert _row(did)["storage_uri"] == dest


def test_a_conflicting_existing_destination_blocks_and_never_overwrites(tmp_path):
    did, src = _make(tmp_path, b"mine", "TaxDome", "conf.pdf", system=_SYS_TD)
    dest = _entry(did)["destination"]
    _write(dest, b"SOMEONE ELSES BYTES")
    r = _apply(did)
    assert r["rows_relocated"] == 0 and r["rows_refused"] == 1
    assert "already exists with DIFFERENT content" in r["refused"][0]["detail"]
    assert open(dest, "rb").read() == b"SOMEONE ELSES BYTES", "destination must not be overwritten"
    assert _row(did)["storage_uri"] == src


# === failure, idempotency, guards =================================================================

def test_a_db_failure_after_a_successful_copy_leaves_the_original_location_valid(tmp_path,
                                                                                monkeypatch):
    body = b"copied but not repointed"
    did, src = _make(tmp_path, body, "TaxDome", "fail.pdf", system=_SYS_TD)
    dest = _entry(did)["destination"]

    real = dr._repoint

    def _boom(*a, **k):
        raise RuntimeError("simulated DB failure after copy")

    monkeypatch.setattr(dr, "_repoint", _boom)
    r = _apply(did)
    assert r["failed_batch"]["error"] == "RuntimeError"
    assert r["rows_relocated"] == 0
    # DB location untouched, source intact, verified destination bytes left in place (never deleted)
    assert _row(did)["storage_uri"] == src
    assert os.path.exists(src) and open(src, "rb").read() == body
    assert os.path.exists(dest) and open(dest, "rb").read() == body

    monkeypatch.setattr(dr, "_repoint", real)
    again = _apply(did)                                  # rerun must reuse the verified bytes
    assert again["rows_relocated"] == 1
    assert again["relocated"][0]["copy_verification"]["reused_existing_destination"] is True
    assert _row(did)["storage_uri"] == dest


def test_rerunning_a_completed_relocation_is_a_no_op(tmp_path):
    did, _ = _make(tmp_path, b"idem", "Drake", "idem.pdf", system=_SYS_DR)
    first = _apply(did)
    assert first["rows_relocated"] == 1
    dest = _row(did)["storage_uri"]
    e = _entry(did)
    assert e["classification"] == dr.ALREADY_CANONICAL, "it left the SAFE population"
    second = _apply(did)
    assert second["rows_relocated"] == 0 and second["rows_planned"] == 0
    assert _row(did)["storage_uri"] == dest


def test_a_stale_plan_is_refused_row_by_row(tmp_path):
    did, src = _make(tmp_path, b"stale", "Drake", "st.pdf", system=_SYS_DR)
    plan_doc = dr.plan()
    with engine.begin() as c:                            # the row moves after planning
        c.execute(text("UPDATE documents SET storage_uri = :u WHERE id = :i"),
                  {"u": src + ".moved", "i": did})
    rows = [r for r in plan_doc["rows"] if r["document_id"] == did]
    r = dr.apply(plan_doc={**plan_doc, "rows": rows, "expected_safe_rows": 1,
                           "expected_safe_bytes": rows[0]["size_bytes"]},
                 apply_writes=True, expected_safe_rows=1,
                 expected_safe_bytes=rows[0]["size_bytes"],
                 expected_plan_fingerprint=plan_doc["plan_fingerprint"])
    assert r["rows_relocated"] == 0 and r["rows_refused"] == 1
    assert r["refused"][0]["refused"] == "StalePlanError"
    assert "storage_uri changed" in r["refused"][0]["detail"]


@pytest.mark.parametrize("guard", ["rows", "bytes", "fingerprint", "missing"])
def test_the_expected_guards_must_all_match_before_any_write(tmp_path, guard):
    did, src = _make(tmp_path, b"guard", "Drake", "g2.pdf", system=_SYS_DR)
    plan_doc = dr.plan()
    rows = [r for r in plan_doc["rows"] if r["document_id"] == did]
    doc = {**plan_doc, "rows": rows, "expected_safe_rows": 1,
           "expected_safe_bytes": rows[0]["size_bytes"]}
    kw = {"expected_safe_rows": 1, "expected_safe_bytes": rows[0]["size_bytes"],
          "expected_plan_fingerprint": plan_doc["plan_fingerprint"]}
    if guard == "rows":
        kw["expected_safe_rows"] = 999
    elif guard == "bytes":
        kw["expected_safe_bytes"] = 999999
    elif guard == "fingerprint":
        kw["expected_plan_fingerprint"] = "0" * 64
    else:
        kw = {}
    before_fs = _tree(tmp_path)
    with pytest.raises(dr.RelocationError):
        dr.apply(plan_doc=doc, apply_writes=True, **kw)
    assert _tree(tmp_path) == before_fs, "the guard must run before any copy"
    assert _row(did)["storage_uri"] == src


def test_a_partial_apply_across_batches_is_reported_and_committed_batches_named(tmp_path,
                                                                               monkeypatch):
    made = [_make(tmp_path, f"body{i}".encode(), "Drake", f"p{i}.pdf", system=_SYS_DR)
            for i in range(4)]
    ids = [d for d, _ in made]
    plan_doc = dr.plan()
    rows = sorted([r for r in plan_doc["rows"] if r["document_id"] in ids],
                  key=lambda r: r["document_id"])
    real = dr._copy_and_verify
    calls = {"n": 0}

    def _boom(prepared, storage):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated copy failure")
        return real(prepared, storage)

    monkeypatch.setattr(dr, "_copy_and_verify", _boom)
    r = dr.apply(plan_doc={**plan_doc, "rows": rows, "expected_safe_rows": 4,
                           "expected_safe_bytes": sum(x["size_bytes"] for x in rows)},
                 apply_writes=True, batch_size=2, expected_safe_rows=4,
                 expected_safe_bytes=sum(x["size_bytes"] for x in rows),
                 expected_plan_fingerprint=plan_doc["plan_fingerprint"])
    assert r["partial_apply"] is True
    assert r["committed_batches"] == [1] and r["failed_batch"]["batch"] == 2
    assert r["rows_relocated"] == 2
    assert [b["committed"] for b in r["batches"]] == [True, False]
    assert _row(ids[0])["storage_uri"] != made[0][1] and _row(ids[1])["storage_uri"] != made[1][1]
    assert _row(ids[2])["storage_uri"] == made[2][1], "batch 2 rolled back"
    assert all(os.path.exists(src) for _d, src in made), "no source was ever removed"


# === collisions ===================================================================================

def test_two_rows_with_identical_bytes_may_share_one_destination(tmp_path):
    body = b"shared identical bytes"
    a = _write(os.path.join(str(tmp_path), "Client360", "Data", "Documents", "TaxDome", "s.pdf"),
               body)
    d1 = _doc(uri=a, sha=hashlib.sha256(body).hexdigest(), size=len(body))
    _source(d1, _SYS_TD)
    b = _write(os.path.join(str(tmp_path), "Client360D", "Content", "s.pdf"), body)
    d2 = _doc(uri=b, sha=hashlib.sha256(body).hexdigest(), size=len(body))
    _source(d2, _SYS_TD)
    e1, e2 = _entry(d1), _entry(d2)
    assert e1["destination"] == e2["destination"], "identical content may share bytes"
    assert "destination_shared_identical_content" in e2["reason_codes"]
    assert dr.plan()["summary"]["destination_collisions"] == 0


def test_two_rows_with_different_bytes_get_deterministic_non_destructive_destinations(tmp_path):
    p1 = _write(os.path.join(str(tmp_path), "Client360", "Data", "Documents", "TaxDome", "c.pdf"),
                b"first")
    d1 = _doc(uri=p1, sha=hashlib.sha256(b"first").hexdigest(), size=5)
    _source(d1, _SYS_TD)
    p2 = _write(os.path.join(str(tmp_path), "Client360D", "Content", "c.pdf"), b"secnd")
    d2 = _doc(uri=p2, sha=hashlib.sha256(b"secnd").hexdigest(), size=5)
    _source(d2, _SYS_TD)
    e1, e2 = _entry(d1), _entry(d2)
    assert e1["destination"] != e2["destination"], "differing hashes may never share a path"
    assert e2["destination"].endswith(f"__doc{d2}.pdf")
    assert "destination_collision_disambiguated" in e2["reason_codes"]
    assert dr.plan()["summary"]["destination_collisions"] >= 1

    r = _apply(plan_doc={**dr.plan(),
                         "rows": [x for x in dr.plan()["rows"] if x["document_id"] in (d1, d2)]})
    assert r["rows_relocated"] == 2
    assert open(_row(d1)["storage_uri"], "rb").read() == b"first"
    assert open(_row(d2)["storage_uri"], "rb").read() == b"secnd"


# === preservation and audit =======================================================================

def test_ownership_provenance_ocr_classifications_and_facts_are_untouched(tmp_path):
    did, _ = _make(tmp_path, b"preserve", "SharePoint", "pres.pdf", system=_SYS_SP)
    with engine.begin() as c:
        pid = c.execute(text("INSERT INTO people (full_name) VALUES (:n) RETURNING id"),
                        {"n": f"{_TAG} Owner"}).scalar_one()
        c.execute(text("UPDATE documents SET person_id = :p WHERE id = :i"), {"p": pid, "i": did})
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count)"
                       " VALUES (:d,'completed','SECRET OCR BODY',14)"), {"d": did})
        c.execute(text("INSERT INTO document_classifications (document_id, doc_type,"
                       " classifier_version) VALUES (:d,'W-2','v1')"), {"d": did})
        c.execute(text("INSERT INTO document_facts (document_id, fact_type, fact_value,"
                       " extraction_engine) VALUES (:d,'wages','SECRET FACT','t')"), {"d": did})

    def _deps():
        with engine.begin() as c:
            return {
                "person": c.execute(text("SELECT person_id, household_id, organization_id,"
                                         " sha256 FROM documents WHERE id = :i"),
                                    {"i": did}).one(),
                "sources": c.execute(text("SELECT source_system, source_uri FROM document_sources"
                                          " WHERE document_id = :i ORDER BY id"),
                                     {"i": did}).fetchall(),
                "ocr": c.execute(text("SELECT status, text, char_count FROM document_ocr"
                                      " WHERE document_id = :i"), {"i": did}).fetchall(),
                "cls": c.execute(text("SELECT doc_type FROM document_classifications"
                                      " WHERE document_id = :i"), {"i": did}).fetchall(),
                "facts": c.execute(text("SELECT fact_type, fact_value FROM document_facts"
                                        " WHERE document_id = :i"), {"i": did}).fetchall(),
            }

    before = _deps()
    r = _apply(did)
    assert r["rows_relocated"] == 1
    assert _deps() == before, "only the storage location may change"


def test_the_audit_records_paths_and_digests_but_no_document_content(tmp_path):
    did, src = _make(tmp_path, b"audit body", "TaxDome", "aud.pdf", system=_SYS_TD)
    secret = "SECRET OCR BODY TEXT 123-45-6789"
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count)"
                       " VALUES (:d,'completed',:t,31)"), {"d": did, "t": secret})
        c.execute(text("INSERT INTO document_facts (document_id, fact_type, fact_value,"
                       " extraction_engine) VALUES (:d,'wages',:v,'t')"), {"d": did, "v": secret})
    assert _apply(did)["rows_relocated"] == 1
    with engine.begin() as c:
        raw = c.execute(text("SELECT metadata::text FROM audit_events WHERE action = :a"
                             " ORDER BY id DESC LIMIT 1"), {"a": dr.AUDIT_ACTION}).scalar()
    assert secret not in raw, "no OCR/fact content may enter the audit chain"
    md = json.loads(raw)
    for key in ("run_id", "plan_fingerprint", "document_id", "original_storage_provider",
                "original_storage_uri", "original_storage_path", "new_storage_provider",
                "new_storage_uri", "new_storage_path", "expected_sha256", "expected_size",
                "provenance_source", "copy_verification", "db_update", "timestamp"):
        assert key in md, key
    assert md["original_storage_uri"] == src
    assert md["new_storage_uri"] == _row(did)["storage_uri"]
    assert md["copy_verification"]["verified"] is True
    assert md["expected_sha256"] == hashlib.sha256(b"audit body").hexdigest()


# === structural safety ============================================================================

def test_no_source_deletion_or_move_code_path_exists():
    import ast
    import pathlib
    banned = {"unlink", "remove", "rmtree", "removedirs", "rmdir", "move", "rename", "truncate",
              "system", "popen", "run", "call", "check_output"}
    for rel in ("app/services/document_relocation.py", "scripts/relocate_document_storage.py"):
        src = pathlib.Path(rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in banned:
                raise AssertionError(f"{rel} calls .{node.attr}()")
            if isinstance(node, ast.Import):
                for n in node.names:
                    assert n.name.split(".")[0] not in {"shutil", "subprocess"}, n.name
        assert "DELETE FROM" not in src, rel


def test_the_storage_surface_exposes_no_delete_or_move():
    surface = {n for n in dir(dr.RelocationStorage) if not n.startswith("_")}
    assert surface == {"stat", "exists", "sha256", "makedirs", "copy_into_place"}
    for banned in ("delete", "remove", "move", "rename", "unlink", "truncate"):
        assert not any(banned in n for n in surface)
    from app.services.migration.storage import StorageService
    backend = {n for n in dir(StorageService) if not n.startswith("_")}
    assert not any(b in n for n in backend for b in ("delete", "remove", "unlink"))


def test_only_storage_location_columns_are_writable():
    assert dr.WRITABLE_COLUMNS == ("storage_uri", "storage_path", "updated_at")
    assert dr.ALWAYS_WRITTEN_COLUMNS == ("storage_uri", "updated_at")
    assert dr.CONDITIONALLY_WRITTEN_COLUMNS == ("storage_path",)
    assert "storage_provider" not in dr.WRITABLE_COLUMNS, "the provider is never written"
    import pathlib
    src = pathlib.Path("app/services/document_relocation.py").read_text(encoding="utf-8")
    updates = [ln for ln in src.splitlines() if "UPDATE documents" in ln]
    assert len(updates) == 1
    stmt = src[src.index("sets = ["):src.index("conn.execute(text(f\"UPDATE documents")]
    for col in ("person_id", "household_id", "organization_id", "sha256", "size_bytes",
                "category", "classification", "status", "original_name"):
        assert col not in stmt, f"{col} must never be written by relocation"


def test_only_safe_rows_are_ever_executable():
    assert dr.EXECUTABLE_CLASSIFICATIONS == frozenset({dr.SAFE})
    assert dr.NON_EXECUTABLE_CLASSIFICATIONS == frozenset(
        {dr.REVIEW, dr.BLOCKED, dr.ALREADY_CANONICAL, dr.EMPTY_URI})


def test_the_plan_summary_reports_every_required_figure(tmp_path):
    _make(tmp_path, b"s1", "TaxDome", "x.pdf", system=_SYS_TD)
    _make(tmp_path, b"s2", "Clients", "y.pdf", root="content")
    _doc(uri="", path="")
    s = dr.plan()["summary"]
    for key in ("rows_examined", "SAFE", "REVIEW_REQUIRED", "BLOCKED", "ALREADY_CANONICAL",
                "EMPTY_URI", "counts_per_current_root", "counts_per_proposed_destination_root",
                "safe_bytes_total", "destination_collisions", "conflicting_hashes",
                "missing_required_metadata"):
        assert key in s, key
    assert s["rows_examined"] >= 3 and s["SAFE"] >= 1 and s["EMPTY_URI"] >= 1


def test_the_cli_defaults_to_dry_run(tmp_path, capsys, _canonical_root):
    import scripts.relocate_document_storage as cli
    did, src = _make(tmp_path, b"cli", "Drake", "cli.pdf", system=_SYS_DR)
    before = _tree(tmp_path)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN - NO DATABASE OR FILESYSTEM WRITE WAS ISSUED" in out
    assert "no --apply: no file was copied and no row was changed" in out
    assert _tree(tmp_path) == before and _row(did)["storage_uri"] == src
    assert out.isascii()


def test_the_cli_refuses_apply_without_guards(tmp_path, capsys):
    import scripts.relocate_document_storage as cli
    did, src = _make(tmp_path, b"cli2", "Drake", "cli2.pdf", system=_SYS_DR)
    before = _tree(tmp_path)
    assert cli.main(["--apply"]) == 2
    assert "REFUSED - nothing was copied or written" in capsys.readouterr().out
    assert _tree(tmp_path) == before and _row(did)["storage_uri"] == src


def test_a_copy_that_silently_corrupts_the_bytes_never_repoints_the_row(tmp_path):
    """The destination-hash gate: verification is what stands between a bad copy and the DB."""
    body = b"the true bytes"
    did, src = _make(tmp_path, body, "TaxDome", "corrupt.pdf", system=_SYS_TD)

    class CorruptingStorage(dr.RelocationStorage):
        def copy_into_place(self, source, destination):
            # Same LENGTH, different content: the size check cannot catch this, so only the
            # destination hash comparison stands between a bad copy and the database.
            self.operations.append(("copy", f"{source} -> {destination}"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as fh:
                fh.write(b"X" * len(body))

    r = _apply(did, storage=CorruptingStorage())
    assert r["rows_relocated"] == 0 and r["rows_refused"] == 1
    assert "destination sha256" in r["refused"][0]["detail"]
    assert _row(did)["storage_uri"] == src, "a corrupt copy must never repoint the row"
    assert open(src, "rb").read() == body, "and the source is still intact"


def test_a_destination_whose_size_differs_from_the_source_never_repoints(tmp_path):
    body = b"sized body bytes"
    did, src = _make(tmp_path, body, "Drake", "size.pdf", system=_SYS_DR)

    class ShortStorage(dr.RelocationStorage):
        def copy_into_place(self, source, destination):
            self.operations.append(("copy", f"{source} -> {destination}"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as fh:
                fh.write(body[:3])                          # truncated copy

    r = _apply(did, storage=ShortStorage())
    assert r["rows_relocated"] == 0 and r["rows_refused"] == 1
    assert "size" in r["refused"][0]["detail"]
    assert _row(did)["storage_uri"] == src


def test_path_comparison_is_case_insensitive_by_construction():
    """Explicitly, not via os.path.normcase - which is a no-op off Windows."""
    assert dr._norm(r"C:\Client360\Data\Documents\TaxDome") == \
        dr._norm(r"c:\client360\DATA\documents\taxdome")
    assert dr._under(dr._norm(r"C:\A\B\c.pdf"), dr._norm(r"c:\a\b"))
    assert not dr._under(dr._norm(r"C:\A\BB\c.pdf"), dr._norm(r"c:\a\b"))
    # ...and the destination keeps its ORIGINAL casing.
    assert dr._relative_under(r"C:\x\TaxDome\2024\Return.PDF", dr._norm(r"c:\x\taxdome")) == \
        os.path.join("2024", "Return.PDF")


def test_a_row_with_no_recorded_sha_still_requires_destination_to_match_the_source(tmp_path):
    """documents.sha256 is NOT NULL but may be empty. With no expected hash to compare against,
    the destination-vs-SOURCE comparison is the only thing left - it must still refuse."""
    body = b"no recorded sha"
    did, src = _make(tmp_path, body, "Drake", "nosha.pdf", system=_SYS_DR)
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET sha256 = '' WHERE id = :i"), {"i": did})
    entry = _entry(did)
    assert entry["classification"] == dr.REVIEW, "a row with no sha is not auto-relocatable"
    assert "missing_sha_or_size" in entry["reason_codes"]

    # Force the row through the verify path anyway, to prove the source/destination comparison
    # stands on its own when there is no expected hash.
    forced = {**entry, "classification": dr.SAFE,
              "destination": os.path.join(dr.canonical_roots()["Drake"], "nosha.pdf")}
    forced["fingerprint"] = entry["fingerprint"]

    class CorruptingStorage(dr.RelocationStorage):
        def copy_into_place(self, source, destination):
            self.operations.append(("copy", f"{source} -> {destination}"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as fh:
                fh.write(b"Y" * len(body))          # same size, no expected sha to catch it

    storage = CorruptingStorage()
    with engine.begin() as conn:
        prepared = dr._prepare_row(conn, forced, storage)
    assert prepared["expected_sha256"] == ""
    with pytest.raises(dr.RelocationError, match="destination sha256 does not match the source"):
        dr._copy_and_verify(prepared, storage)
    assert _row(did)["storage_uri"] == src


def test_every_destination_is_absolute_and_inside_the_configured_root(tmp_path, _canonical_root):
    """Regression: storage_paths._join strips leading separators (fine for D:\\..., wrong for a
    POSIX root). A relative destination would resolve against the process CWD and write bytes
    wherever the tool happened to be launched from."""
    _make(tmp_path, b"abs1", "TaxDome", "a.pdf", system=_SYS_TD)
    _make(tmp_path, b"abs2", "SharePoint", "b.pdf", system=_SYS_SP)
    _make(tmp_path, b"abs3", "Clients", "c.pdf", system=_SYS_DR, root="content")
    for root in dr.canonical_roots().values():
        assert dr._is_absolute(root), root
    for entry in dr.plan()["rows"]:
        if entry["classification"] == dr.SAFE:
            assert dr._is_absolute(entry["destination"]), entry["destination"]
            assert str(tmp_path) in entry["destination"]


def test_a_relative_destination_is_refused_before_any_copy(tmp_path):
    did, src = _make(tmp_path, b"rel", "Drake", "rel.pdf", system=_SYS_DR)
    entry = _entry(did)
    forced = {**entry, "destination": os.path.join("relative", "path", "rel.pdf")}
    storage = dr.RelocationStorage()
    with engine.begin() as conn, pytest.raises(dr.RelocationError, match="is not absolute"):
        dr._prepare_row(conn, forced, storage)
    assert [op for op, _ in storage.operations if op in ("copy", "makedirs")] == []
    assert _row(did)["storage_uri"] == src


def test_the_relocation_writes_nothing_into_the_repository_working_tree(tmp_path):
    """No test may create files in the repo - that is how the CWD bug showed itself."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    before = {p.name for p in repo.iterdir()}
    did, _ = _make(tmp_path, b"cwd", "TaxDome", "cwd.pdf", system=_SYS_TD)
    _apply(did)
    assert {p.name for p in repo.iterdir()} == before, "relocation created files in the repo"


# === storage_path is a SOURCE-RELATIVE pointer, not a second absolute one ==========================
# Production rows prove the two columns differ:
#   storage_uri  C:\Client360\data\Documents\TaxDome\Aaron Casper\...\2023\file.pdf
#   storage_path Aaron Casper/Client uploaded documents/2023/file.pdf
# Relocation changes the physical ROOT, which leaves a relative storage_path still correct.

def test_taxdome_uri_moves_c_to_d_while_the_relative_storage_path_stays_relative(tmp_path):
    body = b"taxdome production shape"
    rel = "Aaron Casper/Client uploaded documents/2023/file.pdf"
    did, src = _make(tmp_path, body, "TaxDome", "Aaron Casper", "Client uploaded documents",
                     "2023", "file.pdf", system=_SYS_TD, rel_path=rel)
    e = _entry(did)
    assert e["classification"] == dr.SAFE
    assert e["new_storage_path"] is None, "a valid relative path must NOT be rewritten"
    assert "storage_path_relative_preserved" in e["reason_codes"]

    assert _apply(did)["rows_relocated"] == 1
    after = _row(did)
    assert dr._norm(after["storage_uri"]).startswith(dr._norm(dr.canonical_roots()["TaxDome"]))
    assert after["storage_uri"] != src, "the URI moved to the canonical root"
    assert after["storage_path"] == rel, "byte-for-byte unchanged: slashes and casing preserved"
    assert not dr._is_absolute(after["storage_path"])


def test_sharepoint_uri_moves_while_the_relative_storage_path_stays_relative(tmp_path):
    rel = "Clients/Acme Holdings/2024/agreement.pdf"
    did, src = _make(tmp_path, b"sp shape", "SharePoint", "Clients", "Acme Holdings", "2024",
                     "agreement.pdf", system=_SYS_SP, rel_path=rel)
    assert _entry(did)["new_storage_path"] is None
    assert _apply(did)["rows_relocated"] == 1
    after = _row(did)
    assert dr._norm(after["storage_uri"]).startswith(dr._norm(dr.canonical_roots()["SharePoint"]))
    assert after["storage_path"] == rel and not dr._is_absolute(after["storage_path"])


def test_a_relative_storage_path_with_different_slashes_and_casing_is_still_preserved(tmp_path):
    rel = "aaron casper/2023/File.PDF"
    did, _ = _make(tmp_path, b"casing", "TaxDome", "Aaron Casper", "2023", "File.PDF",
                   system=_SYS_TD, rel_path=rel)
    e = _entry(did)
    assert e["classification"] == dr.SAFE and e["new_storage_path"] is None
    _apply(did)
    assert _row(did)["storage_path"] == rel, "existing slash/casing semantics are preserved"


def test_no_relocated_storage_path_becomes_absolute_unless_it_already_was(tmp_path):
    """Rule 4: a relative storage_path is never promoted to absolute."""
    rel_id, _ = _make(tmp_path, b"r1", "TaxDome", "A", "r1.pdf", system=_SYS_TD,
                      rel_path="A/r1.pdf")
    abs_id, abs_src = _make(tmp_path, b"r2", "Drake", "B", "r2.pdf", system=_SYS_DR)  # path == uri
    _apply(plan_doc={**dr.plan(),
                     "rows": [r for r in dr.plan()["rows"]
                              if r["document_id"] in (rel_id, abs_id)]})
    rel_after, abs_after = _row(rel_id), _row(abs_id)
    assert not dr._is_absolute(rel_after["storage_path"])
    assert dr._norm(dr.canonical_roots()["TaxDome"]) not in dr._norm(rel_after["storage_path"])
    # The row that genuinely carried an absolute pointer keeps absolute semantics, repointed.
    assert dr._is_absolute(abs_after["storage_path"])
    assert abs_after["storage_path"] == abs_after["storage_uri"] != abs_src


def test_an_absolute_storage_path_that_is_not_the_storage_uri_is_review_required(tmp_path):
    did, src = _make(tmp_path, b"odd", "Drake", "odd.pdf", system=_SYS_DR,
                     rel_path=os.path.join(str(tmp_path), "somewhere", "else.pdf"))
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert "storage_path_absolute_and_differs_from_storage_uri" in e["reason_codes"]
    assert e["destination"] is None, "no canonical-relative path may be invented"


def test_a_relative_storage_path_that_does_not_match_the_uri_is_review_required(tmp_path):
    did, _ = _make(tmp_path, b"mismatch", "TaxDome", "A", "m.pdf", system=_SYS_TD,
                   rel_path="Totally/Different/other.pdf")
    e = _entry(did)
    assert e["classification"] == dr.REVIEW
    assert "storage_path_relative_does_not_match_storage_uri" in e["reason_codes"]
    assert e["destination"] is None


def test_d_content_row_derives_a_storage_path_only_when_unambiguous(tmp_path):
    """Rule 3: derive only when provenance gives an unambiguous relative canonical path."""
    ok_id, _ = _make(tmp_path, b"c-ok", "Acme", "2024", "ok.pdf", system=_SYS_SP, root="content",
                     rel_path="Acme/2024/ok.pdf")
    e = _entry(ok_id)
    assert e["classification"] == dr.SAFE and e["new_storage_path"] is None
    _apply(ok_id)
    assert _row(ok_id)["storage_path"] == "Acme/2024/ok.pdf"

    bad_id, _ = _make(tmp_path, b"c-bad", "Acme", "2024", "bad.pdf", system=_SYS_SP,
                      root="content", rel_path="Unrelated/path.pdf")
    assert _entry(bad_id)["classification"] == dr.REVIEW


def test_an_empty_storage_path_is_left_empty_not_invented(tmp_path):
    did, _ = _make(tmp_path, b"empty-sp", "Drake", "e.pdf", system=_SYS_DR, rel_path="")
    e = _entry(did)
    assert e["classification"] == dr.SAFE and e["new_storage_path"] is None
    assert "storage_path_empty_left_unchanged" in e["reason_codes"]
    _apply(did)
    assert _row(did)["storage_path"] == ""


def test_storage_provider_is_never_written(tmp_path):
    did, _ = _make(tmp_path, b"prov", "TaxDome", "p.pdf", system=_SYS_TD, rel_path="p.pdf")
    before = _row(did)["storage_provider"]
    _apply(did)
    assert _row(did)["storage_provider"] == before
    import pathlib
    src = pathlib.Path("app/services/document_relocation.py").read_text(encoding="utf-8")
    assert "storage_provider =" not in src, "no SQL may assign storage_provider"


def test_download_and_integrity_still_resolve_a_relocated_row(tmp_path):
    """The relocated row must remain resolvable by the EXISTING integrity path resolution."""
    from app.deploy.document_integrity import _resolve_documents_path
    body = b"resolvable after relocation"
    rel = "Client/2024/doc.pdf"
    did, src = _make(tmp_path, body, "TaxDome", "Client", "2024", "doc.pdf", system=_SYS_TD,
                     rel_path=rel)
    _apply(did)
    row = _row(did)
    resolved, reason = _resolve_documents_path(
        storage_uri=row["storage_uri"], storage_path=row["storage_path"], roots=[])
    assert reason is None
    assert resolved is not None and resolved.exists(), "the canonical URI resolves to real bytes"
    assert resolved.read_bytes() == body
    assert hashlib.sha256(resolved.read_bytes()).hexdigest() == row["sha256"]
    # ...and the relative storage_path still resolves under the canonical source root.
    import pathlib
    under_root = pathlib.Path(dr.canonical_roots()["TaxDome"]) / rel.replace("/", os.sep)
    assert under_root.exists() and under_root.read_bytes() == body
