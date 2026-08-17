"""Read-only document integrity verifier (V4) — helpers + DB-backed classification + read-only safety."""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select

from app.db import documents, engine
from app.deploy import document_integrity as di


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _insert_document(*, storage_path="", storage_uri=None, sha256=None, provider="local", size=1):
    # documents.storage_path is NOT NULL, so an "unresolvable" row is modeled with an EMPTY path
    # (which resolves to nothing) rather than NULL.
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name="d.pdf", stored_name=f"s-{sfx}", storage_provider=provider,
            storage_uri=storage_uri, storage_path=storage_path, size_bytes=size,
            sha256=sha256 or (sfx + sfx)[:64], status="active", archived=False)
            .returning(documents.c.id)).scalar_one()


def _rec_for(report, kind, doc_id):
    return next(r for r in report["records"] if r["kind"] == kind and r["id"] == doc_id)


# --- pure helpers ------------------------------------------------------------

def test_evaluate_found_missing_invalid_and_hash(tmp_path):
    f = tmp_path / "real.pdf"
    f.write_bytes(b"%PDF-1.4 hello")
    good = _sha(b"%PDF-1.4 hello")

    ok = di._evaluate(kind="documents", doc_id=1, expected_sha=good, resolved=f,
                      invalid_reason=None, compute_hash=True)
    assert ok["status"] == "found" and ok["hash"] == "match"

    bad = di._evaluate(kind="documents", doc_id=2, expected_sha="0" * 64, resolved=f,
                       invalid_reason=None, compute_hash=True)
    assert bad["status"] == "mismatch" and bad["actual_sha256"] == good

    missing = di._evaluate(kind="documents", doc_id=3, expected_sha=good,
                           resolved=tmp_path / "nope.pdf", invalid_reason=None, compute_hash=True)
    assert missing["status"] == "missing"

    invalid = di._evaluate(kind="documents", doc_id=4, expected_sha=None, resolved=None,
                           invalid_reason="no storage_uri or storage_path", compute_hash=True)
    assert invalid["status"] == "invalid"

    skipped = di._evaluate(kind="documents", doc_id=5, expected_sha=good, resolved=f,
                           invalid_reason=None, compute_hash=False)
    assert skipped["status"] == "found" and skipped["hash"] == "skipped"


def test_resolve_vault_path_shape_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_STORAGE_ROOT", str(tmp_path))
    key = "ab/" + "a" * 32 + ".pdf"
    resolved, invalid = di._resolve_vault_path(key)
    assert invalid is None and resolved == (tmp_path / key).resolve()
    # Bad shapes / traversal are rejected without touching disk.
    assert di._resolve_vault_path("../etc/passwd")[0] is None
    assert di._resolve_vault_path("not-a-key")[0] is None
    assert di._resolve_vault_path(None)[0] is None


# --- DB-backed classification ------------------------------------------------

def test_verify_classifies_documents_rows(tmp_path):
    good_bytes = b"genuine document bytes"
    good_file = tmp_path / "good.pdf"
    good_file.write_bytes(good_bytes)

    found_id = _insert_document(storage_path=str(good_file), sha256=_sha(good_bytes))
    mismatch_id = _insert_document(storage_path=str(good_file), sha256="f" * 64)  # same file, wrong hash
    missing_id = _insert_document(storage_path=str(tmp_path / "absent.pdf"), sha256=_sha(b"x"))
    invalid_id = _insert_document(storage_path="", storage_uri=None, sha256=_sha(b"x"))

    report = di.verify(stores=("documents",), compute_hash=True)

    assert _rec_for(report, "documents", found_id)["status"] == "found"
    assert _rec_for(report, "documents", found_id)["hash"] == "match"
    assert _rec_for(report, "documents", mismatch_id)["status"] == "mismatch"
    assert _rec_for(report, "documents", missing_id)["status"] == "missing"
    assert _rec_for(report, "documents", invalid_id)["status"] == "invalid"

    # found_id and mismatch_id both point at good_file → duplicate physical reference reported.
    dup_paths = {d["path"] for d in report["duplicate_references"]}
    assert str(good_file.resolve()) in dup_paths or str(good_file) in dup_paths


def test_verify_no_hash_mode_does_not_flag_mismatch(tmp_path):
    f = tmp_path / "f.pdf"
    f.write_bytes(b"data")
    doc_id = _insert_document(storage_path=str(f), sha256="0" * 64)   # deliberately wrong hash
    report = di.verify(stores=("documents",), compute_hash=False)
    rec = _rec_for(report, "documents", doc_id)
    assert rec["status"] == "found" and rec["hash"] == "skipped"      # existence only, no mismatch


def test_remote_provider_is_skipped_not_missing(tmp_path):
    doc_id = _insert_document(storage_path="/remote/placeholder",
                              storage_uri="https://sp/site/doc", provider="sharepoint")
    report = di.verify(stores=("documents",), compute_hash=True)
    assert report["skipped_remote_provider"] >= 1
    assert all(r["id"] != doc_id for r in report["records"])          # not evaluated as a local file


# --- orphan scan -------------------------------------------------------------

def test_scan_orphans_reports_unreferenced_files(tmp_path):
    referenced = tmp_path / "referenced.pdf"
    referenced.write_bytes(b"a")
    orphan = tmp_path / "orphan.pdf"
    orphan.write_bytes(b"b")
    orphans = di.scan_orphans({str(referenced)}, [tmp_path])
    assert str(orphan.resolve()) in orphans
    assert str(referenced.resolve()) not in orphans


# --- read-only safety --------------------------------------------------------

def test_path_resolution_never_creates_the_root(tmp_path, monkeypatch):
    # The critical safety property: unlike storage.storage_root(), the verifier's root resolution
    # must NOT mkdir. Point the vault root at a non-existent dir and confirm it stays absent.
    target = tmp_path / "does_not_exist_yet"
    monkeypatch.setenv("VAULT_STORAGE_ROOT", str(target))
    _ = di._vault_root()
    di._resolve_vault_path("ab/" + "a" * 32 + ".pdf")
    assert not target.exists()          # no directory was created


def test_cli_main_runs_and_returns_exit_code(capsys):
    # Exercises argparse + reporting + exit-code path end to end (existence-only, small sample).
    rc = di.main(["--no-hash", "--sample", "3", "--stores", "documents"])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "Document integrity" in out and "records checked" in out


def test_cli_json_output(capsys):
    rc = di.main(["--no-hash", "--sample", "1", "--stores", "vault", "--json"])
    assert rc in (0, 1)
    import json
    payload = json.loads(capsys.readouterr().out)
    assert "documents_checked" in payload and "database" in payload


def test_verify_only_reads_documents_are_unchanged(tmp_path):
    f = tmp_path / "f.pdf"
    f.write_bytes(b"data")
    doc_id = _insert_document(storage_path=str(f), sha256=_sha(b"data"))
    with engine.connect() as c:
        before = c.execute(select(documents.c.status, documents.c.storage_path)
                           .where(documents.c.id == doc_id)).mappings().one()
    di.verify(stores=("documents",), compute_hash=True)
    with engine.connect() as c:
        after = c.execute(select(documents.c.status, documents.c.storage_path)
                          .where(documents.c.id == doc_id)).mappings().one()
    assert dict(before) == dict(after)   # verification mutated no DB row
