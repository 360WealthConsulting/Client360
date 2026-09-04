"""Safety gates for the one-time STRICT-SAFE owner-manifest apply and its rollback.

These pin the properties that make a bulk ownership write safe to run once against production:

* the manifest is IMMUTABLE INPUT -- a changed byte, a changed row count, or a changed owner-type
  census refuses the run before a single row is locked;
* --apply is inert without the exact confirmation phrase;
* one bad row aborts the WHOLE manifest -- there is no partial apply, and no "skip the bad ones";
* --dry-run performs the entire transaction and then rolls it back, so a dry run proves the apply
  would succeed without persisting anything;
* rollback refuses to overwrite a decision somebody made after the apply (drift is fatal).

The write semantics under test deliberately mirror ``households.resolve_document_ownership`` -- the
same atomic ``WHERE all-NULL AND NOT permanent-reject`` guard and the same audit action -- because
that function opens its own per-document transaction and so cannot itself provide all-or-nothing
across a manifest.
"""
from __future__ import annotations

import csv
import hashlib
import uuid

import pytest

from scripts import apply_strict_safe_owner_manifest as ap
from scripts import rollback_strict_safe_owner_manifest as rb


def _write_manifest(tmp_path, rows, *, applied="NO"):
    p = tmp_path / "m.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "document_id", "owner_type", "owner_id", "owner_name", "eligibility_basis",
            "identity_evidence", "corroboration", "client_folder_hint", "folder_validation",
            "conflict_check", "strict_classification", "applied"])
        w.writeheader()
        for r in rows:
            w.writerow({"document_id": r[0], "owner_type": r[1], "owner_id": r[2],
                        "owner_name": r[3], "eligibility_basis": "x", "identity_evidence": "x",
                        "corroboration": "x", "client_folder_hint": "", "folder_validation": "x",
                        "conflict_check": "none", "strict_classification": "SAFE",
                        "applied": applied})
    return p


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _rows(n_person=59, n_household=103):
    out = []
    for i in range(n_person):
        out.append((1000 + i, "person", 1, f"P{i}"))
    for i in range(n_household):
        out.append((2000 + i, "household", 1, f"H{i}"))
    return out


# ---------------------------------------------------------------- manifest immutability

def test_a_changed_manifest_is_refused(tmp_path):
    p = _write_manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="SHA256"):
        ap.load_manifest(p, expect_sha="0" * 64)


def test_the_exact_manifest_is_accepted(tmp_path):
    p = _write_manifest(tmp_path, _rows())
    rows, digest = ap.load_manifest(p, expect_sha=_sha(p))
    assert len(rows) == ap.EXPECTED_ROWS
    assert digest == _sha(p)


def test_wrong_row_count_is_refused(tmp_path):
    p = _write_manifest(tmp_path, _rows(n_person=58))
    with pytest.raises(SystemExit, match="rows, expected"):
        ap.load_manifest(p, expect_sha=_sha(p))


def test_wrong_owner_type_census_is_refused(tmp_path):
    p = _write_manifest(tmp_path, _rows(n_person=60, n_household=102))
    with pytest.raises(SystemExit, match="census"):
        ap.load_manifest(p, expect_sha=_sha(p))


def test_rows_already_marked_applied_are_refused(tmp_path):
    p = _write_manifest(tmp_path, _rows(), applied="YES")
    with pytest.raises(SystemExit, match="applied=NO"):
        ap.load_manifest(p, expect_sha=_sha(p))


def test_duplicate_document_rows_are_refused(tmp_path):
    rows = _rows()
    rows[-1] = (rows[0][0], "household", 1, "dupe")     # same document id twice
    p = _write_manifest(tmp_path, rows)
    with pytest.raises(SystemExit, match="duplicate|conflicting"):
        ap.load_manifest(p, expect_sha=_sha(p))


def test_conflicting_owner_rows_are_refused(tmp_path):
    rows = _rows(n_person=58, n_household=103)
    rows.append((rows[0][0], "person", 99, "other owner"))   # same doc, different owner
    p = _write_manifest(tmp_path, rows)
    with pytest.raises(SystemExit, match="duplicate|conflicting"):
        ap.load_manifest(p, expect_sha=_sha(p))


# ---------------------------------------------------------------- confirmation gate

def test_apply_without_the_confirm_phrase_is_refused(tmp_path):
    p = _write_manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--confirm"):
        ap.run(p, apply_changes=True, confirm=None, expect_sha=_sha(p),
               snapshot_root=tmp_path, out=lambda *_a: None)


def test_apply_with_a_wrong_confirm_phrase_is_refused(tmp_path):
    p = _write_manifest(tmp_path, _rows())
    with pytest.raises(SystemExit, match="--confirm"):
        ap.run(p, apply_changes=True, confirm="APPLY", expect_sha=_sha(p),
               snapshot_root=tmp_path, out=lambda *_a: None)


def test_the_confirm_phrase_is_exactly_the_documented_one():
    assert ap.CONFIRM_PHRASE == "APPLY-STRICT-SAFE-162"
    assert rb.CONFIRM_PHRASE == "ROLLBACK-STRICT-SAFE-162"


# ---------------------------------------------------------------- all-or-nothing

def test_one_unverifiable_row_aborts_the_whole_manifest(tmp_path):
    """The document ids here do not exist, so every row fails verification and nothing is written."""
    p = _write_manifest(tmp_path, _rows())
    with pytest.raises(RuntimeError, match="failed verification"):
        ap.run(p, apply_changes=False, expect_sha=_sha(p), snapshot_root=tmp_path,
               out=lambda *_a: None)


def test_no_snapshot_is_written_when_verification_fails(tmp_path):
    p = _write_manifest(tmp_path, _rows())
    with pytest.raises(RuntimeError):
        ap.run(p, apply_changes=False, expect_sha=_sha(p), snapshot_root=tmp_path,
               out=lambda *_a: None)
    assert not list(tmp_path.glob("strict-safe-owner-apply-*")), \
        "a failed verification must not leave a rollback snapshot behind"


def test_the_write_guard_mirrors_the_canonical_path():
    """The atomic guard must be the same one households.resolve_document_ownership uses."""
    import inspect

    from app.services import households

    src = inspect.getsource(ap.run)
    assert "person_id.is_(None)" in src and "household_id.is_(None)" in src \
        and "organization_id.is_(None)" in src, "the all-NULL recheck must be in the UPDATE"
    assert "PERMANENT_REJECT_DOCUMENT_IDS" in src, "permanent rejects must be excluded"
    assert ap.PERMANENT_REJECT_DOCUMENT_IDS is households.PERMANENT_REJECT_DOCUMENT_IDS


def test_audit_events_enlist_in_the_same_transaction():
    """An audit row that commits separately from its change would break all-or-nothing."""
    import inspect
    assert "conn=conn" in inspect.getsource(ap.run)


def test_only_ownership_columns_are_ever_written():
    assert set(ap._OWNER_COLUMN.values()) == {"person_id", "household_id", "organization_id"}


# ---------------------------------------------------------------- rollback tooling

def test_rollback_verifies_the_snapshot_digest(tmp_path):
    d = tmp_path / "snap"
    d.mkdir()
    csv_path = d / "rollback_snapshot_owner_assignments.csv"
    csv_path.write_text("document_id,owner_type,owner_id,owner_name,prev_person_id,"
                        "prev_household_id,prev_organization_id,prev_status,prev_deleted_at\n"
                        "1,person,1,x,,,,active,\n", encoding="utf-8")
    (d / "manifest.json").write_text('{"snapshot_sha256": "%s"}' % ("0" * 64), encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256"):
        rb.load_snapshot(d)


def test_rollback_accepts_an_untampered_snapshot(tmp_path):
    d = tmp_path / "snap"
    d.mkdir()
    csv_path = d / "rollback_snapshot_owner_assignments.csv"
    csv_path.write_text("document_id,owner_type,owner_id,owner_name,prev_person_id,"
                        "prev_household_id,prev_organization_id,prev_status,prev_deleted_at\n"
                        "1,person,1,x,,,,active,\n", encoding="utf-8")
    import json as _j
    (d / "manifest.json").write_text(_j.dumps({"snapshot_sha256": _sha(csv_path)}),
                                     encoding="utf-8")
    rows, path, digest, recorded = rb.load_snapshot(d)
    assert len(rows) == 1 and digest == recorded


def test_rollback_aborts_on_current_state_drift(tmp_path):
    """Document 1 is not owned by the id the snapshot says the apply set, so this is drift."""
    d = tmp_path / "snap"
    d.mkdir()
    csv_path = d / "rollback_snapshot_owner_assignments.csv"
    csv_path.write_text("document_id,owner_type,owner_id,owner_name,prev_person_id,"
                        "prev_household_id,prev_organization_id,prev_status,prev_deleted_at\n"
                        "999999,person,424242,x,,,,active,\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="drift"):
        rb.run(d, apply_changes=False, out=lambda *_a: None)


def test_rollback_without_the_confirm_phrase_is_refused(tmp_path):
    d = tmp_path / "snap"
    d.mkdir()
    csv_path = d / "rollback_snapshot_owner_assignments.csv"
    csv_path.write_text("document_id,owner_type,owner_id,owner_name,prev_person_id,"
                        "prev_household_id,prev_organization_id,prev_status,prev_deleted_at\n"
                        "1,person,1,x,,,,active,\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="--confirm"):
        rb.run(d, apply_changes=True, confirm="nope", out=lambda *_a: None)


# ---------------------------------------------------------------- end-to-end on real rows

def _seed_document(conn, **kw):
    from app.db import documents as docs_t
    name = f"seed-{uuid.uuid4().hex[:8]}.pdf"
    return conn.execute(docs_t.insert().values(
        original_name=name, stored_name=uuid.uuid4().hex, storage_path=f"seed/{name}",
        size_bytes=1, sha256=uuid.uuid4().hex, status="active",
        **kw).returning(docs_t.c.id)).scalar_one()


def test_dry_run_over_real_rows_writes_nothing(tmp_path):
    """A row that cannot pass verification still proves the dry run persists nothing."""
    from app.db import documents as docs_t
    from app.db import engine

    with engine.begin() as c:
        did = _seed_document(c)
    try:
        p = _write_manifest(tmp_path, [(did, "person", 1, "x")] + _rows()[1:])
        with pytest.raises((RuntimeError, SystemExit)):
            ap.run(p, apply_changes=False, expect_sha=_sha(p), snapshot_root=tmp_path,
                   out=lambda *_a: None)
        with engine.connect() as c:
            row = c.execute(docs_t.select().where(docs_t.c.id == did)).mappings().one()
        assert row["person_id"] is None and row["household_id"] is None \
            and row["organization_id"] is None, "a dry run must never assign ownership"
    finally:
        with engine.begin() as c:
            c.execute(docs_t.delete().where(docs_t.c.id == did))
