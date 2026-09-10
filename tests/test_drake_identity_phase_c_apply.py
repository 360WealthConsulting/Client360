"""D7 Phase C — the guarded apply and its scoped rollback.

The apply is the first batch in this codebase that DELETES a domain row, so the tests concentrate on
the two properties that makes necessary: nothing is written unless every identifier passes, and the
information needed to reverse it exists — hashed, on disk — before the commit that makes it real.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db import engine
from app.services import drake_identity_phase_c as phase_c
from scripts import apply_drake_identity_phase_c as ap
from scripts import rollback_drake_identity_phase_c as rb
from tests.test_drake_identity_phase_c import (  # reuse the proven seeding helpers
    make_contact,
    make_identity,
    make_person,
    make_return,
)

pytestmark = pytest.mark.skipif(
    sa_inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)


@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "phase-c-apply-tests-not-a-production-key")


@pytest.fixture()
def conn():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def seed(conn, *, return_type="1120S", person=None):
    """One relocatable identity, with a hash unique to this test."""
    tag = uuid.uuid4().hex[:10]
    hash_value = hashlib.sha256(tag.encode()).hexdigest()
    name = f"SEEDED {tag} LLC"
    contacts = []
    for year in (2021, 2022):
        rid = make_return(conn, year=year, return_type=return_type, name=name, tp_hash=hash_value)
        contacts.append(make_contact(conn, year=year, return_id=rid, hash_value=hash_value,
                                     name=name))
    row = dict(make_identity(conn, hash_value=hash_value, name=name, person_id=person))
    psl = []
    if person is not None:
        psl = [conn.execute(text(
            "INSERT INTO person_source_links (person_id, source_contact_id, match_method, confirmed) "
            "VALUES (:p, :c, 'exact_email+exact_phone', true) RETURNING id"),
            {"p": person, "c": c}).scalar_one() for c in contacts]
    return {"hash": hash_value, "row": row, "contacts": contacts, "psl": psl, "name": name}


def write_manifest(tmp_path, cases, *, name="manifest.csv"):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ap.MANIFEST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        row = case["row"]
        writer.writerow({
            **{c: ("" if row[c] is None else row[c]) for c in phase_c.IDENTITY_COLUMNS},
            "expected_psl_ids": "|".join(str(i) for i in case["psl"]),
            "cohort": "B" if case["psl"] else "A",
        })
    path = tmp_path / name
    path.write_bytes(buffer.getvalue().encode("utf-8"))
    return path


def run(path, conn, monkeypatch, **kw):
    """Drive the apply against the test transaction rather than a fresh connection."""
    class _Engine:
        def connect(self):
            class _Proxy:
                def __init__(self, inner):
                    self._inner = inner

                def __getattr__(self, item):
                    return getattr(self._inner, item)

                def begin(self):
                    return self._inner.begin_nested()

                def close(self):
                    return None
            return _Proxy(conn)

    monkeypatch.setattr("app.db.engine", _Engine())
    kw.setdefault("out", lambda *a, **k: None)
    return ap.run(path, **kw)


# --- the manifest gate --------------------------------------------------------------------------

def test_a_tampered_manifest_is_refused(conn, tmp_path, monkeypatch):
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    with pytest.raises(ap.Abort, match="SHA256"):
        run(path, conn, monkeypatch, expect_sha="0" * 64)


def test_a_duplicate_identifier_is_refused(conn, tmp_path, monkeypatch):
    case = seed(conn)
    path = write_manifest(tmp_path, [case, case], name="dupe.csv")
    with pytest.raises(ap.Abort, match="duplicate identifier_hash"):
        run(path, conn, monkeypatch)


def test_the_confirmation_phrase_is_tied_to_the_row_count(conn, tmp_path, monkeypatch):
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    assert ap.confirm_phrase(1) == "APPLY-D7-PHASE-C-1"
    with pytest.raises(ap.Abort, match="requires --confirm"):
        run(path, conn, monkeypatch, apply_changes=True, confirm="APPLY-D7-PHASE-C-2")


# --- dry run ------------------------------------------------------------------------------------

def test_the_dry_run_writes_nothing(conn, tmp_path, monkeypatch):
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    before = conn.execute(text("select count(*) from drake_identity")).scalar()
    dbi = conn.execute(text("select count(*) from drake_business_identity")).scalar()

    with pytest.raises(ap.Abort, match="DRY RUN"):
        run(path, conn, monkeypatch)

    assert conn.execute(text("select count(*) from drake_identity")).scalar() == before
    assert conn.execute(text("select count(*) from drake_business_identity")).scalar() == dbi


def test_the_dry_run_reports_refusals_without_writing(conn, tmp_path, monkeypatch):
    """A cohort-C row in the input must surface as a refusal, not a silent skip."""
    person = make_person(conn, uuid.uuid4().hex[:8])
    case = seed(conn, person=person)          # linked, but no PSLs seeded -> cohort C
    case["psl"] = []
    path = write_manifest(tmp_path, [case])

    with pytest.raises(ap.Abort, match="DRY RUN"):
        run(path, conn, monkeypatch)
    assert conn.execute(text("select count(*) from drake_identity where identifier_hash = :h"),
                        {"h": case["hash"]}).scalar() == 1


# --- apply --------------------------------------------------------------------------------------

def test_a_clean_batch_applies_and_writes_the_rollback_manifest_first(conn, tmp_path, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)

    report = run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(2),
                 output_root=tmp_path / "out")

    assert report["relocated"] == 2 and report["committed"] is True
    manifest = json.loads(open(report["rollback_manifest"], encoding="utf-8").read())
    assert len(manifest["rows"]) == 2
    assert report["rollback_manifest_sha256"] == hashlib.sha256(
        open(report["rollback_manifest"], "rb").read()).hexdigest()
    for row, case in zip(sorted(manifest["rows"], key=lambda r: r["identifier_hash"]),
                         sorted(cases, key=lambda c: c["hash"]), strict=True):
        assert row["identifier_hash"] == case["hash"]
        assert row["created_business_identity_id"] > 0
        snapshot = row["removed_drake_identity"]
        assert set(snapshot) == set(phase_c.IDENTITY_COLUMNS)      # lossless
        assert snapshot["taxpayer_name"] == case["name"]


def test_one_refusal_aborts_the_whole_batch(conn, tmp_path, monkeypatch):
    """No partial completion: a good row must not be relocated beside a refused one."""
    good = seed(conn)
    bad = seed(conn)
    conn.execute(text("update drake_identity set confidence = 42 where identifier_hash = :h"),
                 {"h": bad["hash"]})                                   # drift vs the frozen row
    path = write_manifest(tmp_path, [good, bad])

    with pytest.raises(ap.Abort, match="refused"):
        run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(2),
            output_root=tmp_path / "out")

    for case in (good, bad):
        assert conn.execute(text(
            "select count(*) from drake_identity where identifier_hash = :h"),
            {"h": case["hash"]}).scalar() == 1
        assert conn.execute(text(
            "select count(*) from drake_business_identity where identifier_hash = :h"),
            {"h": case["hash"]}).scalar() == 0


def test_forbidden_tables_are_fingerprinted(conn, tmp_path, monkeypatch):
    person = make_person(conn, uuid.uuid4().hex[:8])
    case = seed(conn, person=person)
    path = write_manifest(tmp_path, [case])
    before = {t: conn.execute(text(f"select count(*) from {t}")).scalar()
              for t in ap.FORBIDDEN_TABLES}

    run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(1),
        output_root=tmp_path / "out")

    for table, count in before.items():
        assert conn.execute(text(f"select count(*) from {table}")).scalar() == count, table


# --- the round trip -----------------------------------------------------------------------------

def test_rollback_restores_the_row_byte_for_byte(conn, tmp_path, monkeypatch):
    case = seed(conn)
    before = dict(conn.execute(text(
        "select identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at from drake_identity "
        "where identifier_hash = :h"), {"h": case["hash"]}).mappings().one())
    path = write_manifest(tmp_path, [case])
    report = run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(1),
                 output_root=tmp_path / "out")

    monkeypatch.setattr("app.db.engine", _proxy_engine(conn))
    rb.run(report["rollback_manifest"], apply_changes=True, confirm=rb.confirm_phrase(1),
           out=lambda *a, **k: None)

    after = dict(conn.execute(text(
        "select identifier_hash, primary_person_id, first_year, last_year, return_count, "
        "taxpayer_name, spouse_name, confidence, created_at from drake_identity "
        "where identifier_hash = :h"), {"h": case["hash"]}).mappings().one())
    assert after == before
    assert conn.execute(text(
        "select count(*) from drake_business_identity where identifier_hash = :h"),
        {"h": case["hash"]}).scalar() == 0


def test_rollback_refuses_an_identity_attributed_since(conn, tmp_path, monkeypatch):
    """Reversing must not destroy an attribution made after this batch ran."""
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    report = run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(1),
                 output_root=tmp_path / "out")
    created = json.loads(open(report["rollback_manifest"], encoding="utf-8").read())
    dbi_id = created["rows"][0]["created_business_identity_id"]
    conn.execute(text("update drake_business_identity set trust_level = 'identifier_verified', "
                      "confirmation_source = 'machine' where id = :i"), {"i": dbi_id})

    monkeypatch.setattr("app.db.engine", _proxy_engine(conn))
    with pytest.raises(rb.Abort, match="attributed since"):
        rb.run(report["rollback_manifest"], apply_changes=True, confirm=rb.confirm_phrase(1),
               out=lambda *a, **k: None)

    assert conn.execute(text("select count(*) from drake_business_identity where id = :i"),
                        {"i": dbi_id}).scalar() == 1


def test_rollback_verifies_its_own_manifest_hash(conn, tmp_path, monkeypatch):
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    report = run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(1),
                 output_root=tmp_path / "out")
    monkeypatch.setattr("app.db.engine", _proxy_engine(conn))
    with pytest.raises(rb.Abort, match="SHA256"):
        rb.run(report["rollback_manifest"], expect_sha="0" * 64, out=lambda *a, **k: None)


def _proxy_engine(conn):
    class _Engine:
        def connect(self):
            class _Proxy:
                def __init__(self, inner):
                    self._inner = inner

                def __getattr__(self, item):
                    return getattr(self._inner, item)

                def begin(self):
                    return self._inner.begin_nested()

                def close(self):
                    return None
            return _Proxy(conn)
    return _Engine()
