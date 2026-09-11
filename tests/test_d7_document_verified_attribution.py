"""D7 document-corroborated attribution — the guarded apply and its scoped rollback.

WHAT THESE PIN

This runner lowers the source-contact floor to one, which Batch 1 deliberately refuses. The whole
justification for that is the document evidence, so the tests concentrate hardest there: a missing
document, a changed one, one that moved, one that no longer names the entity beside the identifier,
and one that carries a *different* identifier beside the entity's name must each take the entire
batch down before anything is written.

The identifier tokeniser gets its own tests. Depreciation schedules contain runs like
``DUMP 196810-01-2013100.0``, and a looser boundary reads the in-service date as an identifier and
invents a contradiction. That happened on real evidence during review, so it is pinned here.

Batch 1's own policy must remain untouched: a test asserts its floor is still two and that this
runner refuses to load its manifest.

Rows are seeded inside a transaction and rolled back. Nothing is committed, and no test touches
production.
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
from app.services.drake_identifier import identifier_hash
from app.services.link_trust import IDENTIFIER_VERIFIED
from app.services.relationships import create_named_entity
from scripts import apply_d7_attribution_batch1 as batch1
from scripts import apply_d7_document_verified_attribution as ap
from scripts import rollback_d7_document_verified_attribution as rb
from tests.test_drake_identity_phase_c import make_contact, make_return

pytestmark = pytest.mark.skipif(
    sa_inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)

TEST_HASH_KEY = "docverified-tests-not-a-production-key"
_EIN_SEQUENCE = iter(range(8_300_001, 8_399_999))


@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", TEST_HASH_KEY)


@pytest.fixture()
def conn():
    """A stable snapshot. ``client360_test`` is shared, and a concurrent session's committed writes
    would otherwise appear inside this transaction and look like the runner's own."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def seed(conn, tmp_path, *, docs=1, name_in_doc=True, matching=True, extra_identifier=None):
    """One attributable single-contact row, its entity, and its corroborating document(s)."""
    ein = f"99-{next(_EIN_SEQUENCE)}"
    hash_value = identifier_hash(ein)
    tag = uuid.uuid4().hex[:8]
    name = f"DOCVERIFIED {tag} INC"

    rid = make_return(conn, year=2021, return_type="1120S", name=name, tp_hash=hash_value)
    conn.execute(text("UPDATE drake_client_returns SET raw_data = raw_data || "
                      "jsonb_build_object('TP_Social', :e) WHERE id = :i"), {"e": ein, "i": rid})
    contact = make_contact(conn, year=2021, return_id=rid, hash_value=hash_value, name=name)
    dbi_id = conn.execute(text(
        "INSERT INTO drake_business_identity (identifier_hash, subject_type, subject_name, "
        " first_year, last_year, return_count, return_types) "
        "VALUES (:h, 'business_entity', :n, 2021, 2021, 1, CAST('{1120S}' AS text[])) "
        "RETURNING id"), {"h": hash_value, "n": name.lower()}).scalar_one()

    entity = create_named_entity(conn, "business", name)
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"canonical_creation_reason":
                                   "verified_structured_business_provenance",
                                   "source_systems": ["Drake", "SharePoint"],
                                   "verified_sharepoint_folder": name,
                                   "source_contact_ids": [contact]}), "e": entity})

    documents, bodies = [], {}
    for n in range(docs):
        path = tmp_path / f"doc_{dbi_id}_{n}.pdf"
        path.write_bytes(f"%PDF-1.4 seeded evidence {dbi_id} {n}".encode())
        doc_id = conn.execute(text(
            "INSERT INTO documents (original_name, stored_name, storage_path, storage_uri, "
            " organization_id, sha256, size_bytes) "
            "VALUES (:o, :s, :p, :u, :e, :h, :z) RETURNING id"),
            {"o": f"2021 Tax Return Documents ({name}).pdf", "s": f"seed-{dbi_id}-{n}",
             "p": str(path), "u": str(path), "e": entity,
             "h": hashlib.sha256(path.read_bytes()).hexdigest(),
             "z": path.stat().st_size}).scalar_one()
        subject = name if name_in_doc else "SOMEONE ELSE INC"
        token = ein if matching else "99-0000001"
        body = (f"Form 1120S 2021 ... {subject} {token} ... 4525 MELROSE AVENUE\n"
                "Preparer 360 Tax Solutions LLC 82-3042005\n"
                "DUMP 196810-01-2013100.0 1,500 CHEVY SIL04-01-2018100.0 16,995\n")
        if extra_identifier:
            body += f"{subject} {extra_identifier} additional line\n"
        bodies[str(path)] = body
        documents.append({"document_id": doc_id, "document_name": f"2021 return ({name})",
                          "document_path": str(path),
                          "document_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"dbi_id": dbi_id, "hash": hash_value, "ein": ein, "name": name, "entity": entity,
            "contact": contact, "return_id": rid, "documents": documents, "bodies": bodies,
            "record": f"2021:{rid}:taxpayer"}


CSV_COLS = ["dbi_id", "identifier_hash", "subject_name", "subject_type",
            "proposed_relationship_entity_id", "proposed_entity_name", "first_year", "last_year",
            "return_count", "return_types", "drake_return_ids", "tax_years", "source_contact_ids",
            "source_record_ids", "source_contact_count", "provenance_form", "complete_coverage",
            "document_corroboration_count", "document_year", "document_type",
            "contemporaneous_with_filing", "identifier_entity_document_match",
            "expected_esl_inserts", "expected_trust_level", "expected_confirmation_source",
            "expected_evidence_method", "dbi_row_sha256", "target_entity_sha256",
            "evidence_review_artifact_sha256"]
EVIDENCE_SHA = "e" * 64


def write_pair(tmp_path, cases, *, mutate_row=None, mutate_sidecar=None, evidence=EVIDENCE_SHA,
               name="manifest"):
    rows, sidecar_rows = [], []
    for c in cases:
        row = {"dbi_id": c["dbi_id"], "identifier_hash": c["hash"],
               "subject_name": c["name"].lower(), "subject_type": "business_entity",
               "proposed_relationship_entity_id": c["entity"], "proposed_entity_name": c["name"],
               "first_year": 2021, "last_year": 2021, "return_count": 1, "return_types": "1120S",
               "drake_return_ids": c["return_id"], "tax_years": 2021,
               "source_contact_ids": c["contact"], "source_record_ids": c["record"],
               "source_contact_count": 1, "provenance_form": "details.source_contact_ids",
               "complete_coverage": "True", "document_corroboration_count": len(c["documents"]),
               "document_year": "2021", "document_type": "filed tax return package (Form 1120S)",
               "contemporaneous_with_filing": "yes",
               "identifier_entity_document_match": "True", "expected_esl_inserts": 1,
               "expected_trust_level": IDENTIFIER_VERIFIED,
               "expected_confirmation_source": "machine",
               "expected_evidence_method": "drake_entity_provenance",
               "dbi_row_sha256": "0" * 64, "target_entity_sha256": "0" * 64,
               "evidence_review_artifact_sha256": evidence}
        rows.append(mutate_row(row) if mutate_row else row)
        sidecar_rows.append({"dbi_id": c["dbi_id"], "identifier_hash": c["hash"],
                             "proposed_relationship_entity_id": c["entity"],
                             "source_contact_ids": [c["contact"]],
                             "document_corroboration": list(c["documents"])})
    if mutate_sidecar:
        sidecar_rows = mutate_sidecar(sidecar_rows)

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    csv_path = tmp_path / f"{name}.csv"
    csv_path.write_bytes(buf.getvalue().encode("utf-8"))

    sidecar = {"batch": ap.BATCH_ID, "row_count": len(rows),
               "manifest_csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
               "evidence_review_artifact_sha256": evidence,
               "plan_digest": ap.plan_digest(rows), "rows": sidecar_rows}
    json_path = tmp_path / f"{name}.json"
    json_path.write_bytes(json.dumps(sidecar, indent=2, sort_keys=True).encode("utf-8"))
    return csv_path, json_path


def extractor(*cases):
    bodies = {}
    for c in cases:
        bodies.update(c["bodies"])
    return lambda path: bodies.get(str(path), "")


def run(csv_path, json_path, conn, monkeypatch, cases, *, module=ap, **kw):
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

                def execution_options(self, **kwargs):
                    return self        # isolation is already set on the real connection
            return _Proxy(conn)

    monkeypatch.setattr("app.db.engine", _Engine())
    kw.setdefault("out", lambda *a, **k: None)
    if module is ap:
        kw.setdefault("expect_sha", None)
        kw.setdefault("expect_sidecar", None)
        kw.setdefault("expect_digest", None)
        kw.setdefault("expect_rows", None)
        kw.setdefault("expect_evidence", EVIDENCE_SHA)
        kw.setdefault("extract", extractor(*cases))
        return ap.run(csv_path, json_path, **kw)
    return rb.run(csv_path, **kw)


def counts(conn, ids):
    return {
        "dbi": conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar(),
        "attributed": conn.execute(text(
            "SELECT count(*) FROM drake_business_identity "
            "WHERE relationship_entity_id IS NOT NULL")).scalar(),
        "links": conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar(),
        "audits": conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                               {"a": "drake.entity_attribution_machine"}).scalar(),
        "mine": conn.execute(text(
            "SELECT count(*) FROM drake_business_identity WHERE id = ANY(:i) "
            "AND relationship_entity_id IS NOT NULL"), {"i": ids}).scalar(),
    }


# --- the identifier tokeniser ---------------------------------------------------------------------

@pytest.mark.parametrize("blob, expected", [
    ("EIN 12-3456789 filed", ["12-3456789"]),
    ("DUMP 196810-01-2013100.0 1,500", []),          # an in-service date inside an asset run
    ("CHEVY SIL04-01-2018100.0 16,995", []),
    # A name running straight into its own EIN, exactly as the extracted 1120S header reads.
    ("PRECISION WINDOWS AND DOORS INC74-3152500\nDUMP", ["74-3152500"]),
    ("(12-3456789)", ["12-3456789"]),
    ("12-3456789-0", []),                            # trailing dash: part of something longer
])
def test_the_identifier_tokeniser_ignores_numeric_runs(blob, expected):
    """The looser boundary read asset-schedule dates as identifiers and invented contradictions."""
    assert ap.IDENTIFIER_TOKEN.findall(blob) == expected


# --- Batch 1 must be untouched --------------------------------------------------------------------

def test_batch1_policy_is_unchanged():
    assert batch1.MIN_SOURCE_CONTACTS == 2
    assert batch1.APPROVED_ROWS == 39
    assert ap.MIN_SOURCE_CONTACTS == 1
    assert ap.DOCUMENT_CORROBORATION_REQUIRED is True
    assert ap.ADVISORY_LOCK_KEY != batch1.ADVISORY_LOCK_KEY


def test_this_runner_refuses_batch1s_manifest():
    """Different batch, different sidecar; the pair gate stops it before any database access."""
    with pytest.raises(ap.Abort):
        ap.load_manifest(batch1.DEFAULT_MANIFEST, batch1.DEFAULT_MANIFEST)


# --- the manifest / sidecar gate ------------------------------------------------------------------

def test_altered_manifest_bytes_are_refused_before_any_database_access(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    with pytest.raises(ap.Abort, match="manifest SHA256"):
        ap.load_manifest(c, j, expect_sha="0" * 64, expect_sidecar=None, expect_digest=None,
                         expect_rows=None, expect_evidence=EVIDENCE_SHA)


def test_a_wrong_plan_digest_is_refused(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    with pytest.raises(ap.Abort, match="plan digest"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest="f" * 64,
                         expect_rows=None, expect_evidence=EVIDENCE_SHA)


def test_a_wrong_evidence_review_hash_is_refused(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    with pytest.raises(ap.Abort, match="evidence-review artifact"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None, expect_evidence="a" * 64)


def test_a_sidecar_that_does_not_pin_this_manifest_is_refused(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    doc = json.loads(j.read_text(encoding="utf-8"))
    doc["manifest_csv_sha256"] = "0" * 64
    j.write_bytes(json.dumps(doc).encode())
    with pytest.raises(ap.Abort, match="does not pin this manifest"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None, expect_evidence=EVIDENCE_SHA)


def test_a_row_without_document_corroboration_is_refused(tmp_path, conn):
    case = seed(conn, tmp_path, docs=0)
    c, j = write_pair(tmp_path, [case])
    with pytest.raises(ap.Abort, match="carries no document corroboration"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None, expect_evidence=EVIDENCE_SHA)


def test_a_wrong_row_count_is_refused(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    with pytest.raises(ap.Abort, match="rows, approved is"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=7, expect_evidence=EVIDENCE_SHA)


def test_two_rows_targeting_one_entity_are_refused(tmp_path, conn):
    a, b = seed(conn, tmp_path), seed(conn, tmp_path)
    b["entity"] = a["entity"]
    c, j = write_pair(tmp_path, [a, b])
    with pytest.raises(ap.Abort, match="one entity more than once"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None, expect_evidence=EVIDENCE_SHA)


# --- dry run --------------------------------------------------------------------------------------

def test_the_default_writes_nothing(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    before = counts(conn, [x["dbi_id"] for x in cases])
    c, j = write_pair(tmp_path, cases)
    with pytest.raises(ap.Abort, match="DRY RUN"):
        run(c, j, conn, monkeypatch, cases)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_apply_without_the_confirmation_phrase_writes_nothing(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    before = counts(conn, [x["dbi_id"] for x in cases])
    c, j = write_pair(tmp_path, cases)
    with pytest.raises(ap.Abort, match="requires --confirm"):
        run(c, j, conn, monkeypatch, cases, apply_changes=True, confirm="APPLY-D7-DOCVERIFIED-99")
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


# --- the happy path -------------------------------------------------------------------------------

@pytest.fixture()
def applied(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path, docs=2), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    report = run(c, j, conn, monkeypatch, cases, apply_changes=True,
                 confirm=ap.confirm_phrase(3), output_root=tmp_path / "var")
    return {"cases": cases, "csv": c, "json": j, "before": before, "report": report}


def test_the_whole_batch_applies_together(applied, conn):
    before, report = applied["before"], applied["report"]
    after = counts(conn, [x["dbi_id"] for x in applied["cases"]])
    assert report["attributed"] == 3
    assert report["links_created"] == 3
    assert report["documents_verified"] == 4          # one row carries two documents
    assert after["dbi"] == before["dbi"]              # UPDATE, never INSERT
    assert after["attributed"] == before["attributed"] + 3
    assert after["links"] == before["links"] + 3
    assert after["audits"] == before["audits"] + 3
    assert after["mine"] == 3


def test_every_row_carries_machine_trust_and_no_approver(applied, conn):
    for case in applied["cases"]:
        row = conn.execute(text(
            "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method, "
            "confirmed_by_user_id, confirmed_at FROM drake_business_identity WHERE id = :i"),
            {"i": case["dbi_id"]}).mappings().one()
        assert row["relationship_entity_id"] == case["entity"]
        assert row["trust_level"] == IDENTIFIER_VERIFIED
        assert row["confirmation_source"] == "machine"
        assert row["evidence_method"] == "drake_entity_provenance"
        assert row["confirmed_by_user_id"] is None
        assert row["confirmed_at"] is None


def test_the_audit_entries_are_unattended(applied, conn):
    rows = conn.execute(text(
        "SELECT actor_user_id FROM audit_events WHERE action = :a AND entity_id = ANY(:e)"),
        {"a": "drake.entity_attribution_machine",
         "e": [str(c["entity"]) for c in applied["cases"]]}).mappings().all()
    assert len(rows) == 3
    assert all(r["actor_user_id"] is None for r in rows)


def test_the_rollback_manifest_is_hashed_before_the_commit_and_records_the_evidence(applied):
    report = applied["report"]
    assert report["rollback_manifest"] and report["rollback_manifest_sha256"]
    payload = json.loads(open(report["rollback_manifest"], encoding="utf-8").read())
    assert len(payload["rows"]) == 3
    created = [i for r in payload["rows"] for i in r["created_entity_source_link_ids"]]
    assert len(created) == 3 and len(set(created)) == 3 and all(i > 0 for i in created)
    assert all(r["audit_event_id"] > 0 for r in payload["rows"])
    assert all(r["document_evidence"] for r in payload["rows"])
    for row in payload["rows"]:
        assert row["dbi_pre_image"] == {"relationship_entity_id": None, "trust_level": None,
                                        "confirmation_source": None, "evidence_method": None}


def test_no_raw_identifier_reaches_the_rollback_manifest_or_receipt(applied):
    report = applied["report"]
    for path in (report["rollback_manifest"], report["report_dir"] + "/receipt.json"):
        body = open(path, encoding="utf-8").read()
        assert not ap.IDENTIFIER_TOKEN.search(body)
        for case in applied["cases"]:
            assert case["ein"] not in body


def test_no_raw_identifier_reaches_the_console(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    spoken = []
    run(c, j, conn, monkeypatch, cases, apply_changes=True, confirm=ap.confirm_phrase(1),
        output_root=tmp_path / "var2", out=lambda *a, **k: spoken.append(" ".join(str(x)
                                                                                 for x in a)))
    blob = "\n".join(spoken)
    assert cases[0]["ein"] not in blob
    assert not ap.IDENTIFIER_TOKEN.search(blob)


def test_no_forbidden_table_is_written(applied, conn):
    for case in applied["cases"]:
        assert conn.execute(text(
            "SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
            {"h": case["hash"]}).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM person_source_links WHERE source_contact_id = :c"),
            {"c": case["contact"]}).scalar() == 0


# --- document corroboration is mandatory ----------------------------------------------------------

def apply_ok(c, j, conn, monkeypatch, cases, rows, tmp_path):
    return run(c, j, conn, monkeypatch, cases, apply_changes=True,
               confirm=ap.confirm_phrase(rows), output_root=tmp_path / "var")


def test_a_missing_corroborating_document_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    conn.execute(text("DELETE FROM documents WHERE id = :i"),
                 {"i": cases[1]["documents"][0]["document_id"]})
    with pytest.raises(ap.Abort, match="no longer exists"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_changed_document_fingerprint_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    path = cases[1]["documents"][0]["document_path"]
    open(path, "ab").write(b"tampered")
    with pytest.raises(ap.Abort, match="changed since the freeze"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_document_that_moved_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    conn.execute(text("UPDATE documents SET storage_uri = :u WHERE id = :i"),
                 {"u": str(tmp_path / "somewhere_else.pdf"),
                  "i": cases[0]["documents"][0]["document_id"]})
    with pytest.raises(ap.Abort, match="has moved since the freeze"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_document_refiled_under_another_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE documents SET organization_id = :e WHERE id = :i"),
                 {"e": other, "i": cases[0]["documents"][0]["document_id"]})
    with pytest.raises(ap.Abort, match="no longer filed under"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_document_whose_identifier_no_longer_matches_aborts_the_batch(tmp_path, conn, monkeypatch):
    """A different identifier beside the entity's name is a contradiction, not a silent miss."""
    cases = [seed(conn, tmp_path), seed(conn, tmp_path, matching=False)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    with pytest.raises(ap.Abort, match="not this identifier"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_document_that_does_not_name_the_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    """The identifier is present, but beside somebody else's name — that is not corroboration."""
    cases = [seed(conn, tmp_path), seed(conn, tmp_path, name_in_doc=False)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    with pytest.raises(ap.Abort, match="no longer corroborates"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_contradictory_identifier_beside_the_entity_name_aborts_the_batch(
        tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path, extra_identifier="55-5555555")]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    with pytest.raises(ap.Abort, match="not this identifier"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_the_preparers_identifier_is_not_treated_as_contradictory(tmp_path, conn, monkeypatch):
    """Every seeded body carries the preparer's identifier; it is not beside the entity name."""
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    report = apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)
    assert report["attributed"] == 1


# --- state drift ----------------------------------------------------------------------------------

def test_an_already_attributed_row_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE drake_business_identity SET relationship_entity_id = :e "
                      "WHERE id = :i"), {"e": other, "i": cases[1]["dbi_id"]})
    with pytest.raises(ap.Abort, match="already attributed"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases])["mine"] == 1


def test_trust_drift_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text("UPDATE drake_business_identity SET evidence_method = 'something' "
                      "WHERE id = :i"), {"i": cases[0]["dbi_id"]})
    with pytest.raises(ap.Abort, match="already carries evidence_method"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_source_contact_drift_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    extra = make_return(conn, year=2022, return_type="1120S", name=cases[0]["name"],
                        tp_hash=cases[0]["hash"])
    make_contact(conn, year=2022, return_id=extra, hash_value=cases[0]["hash"],
                 name=cases[0]["name"])
    with pytest.raises(ap.Abort, match="source-contact set changed"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_return_drift_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text("UPDATE drake_client_returns SET taxpayer_identifier_hash = :h "
                      "WHERE id = :i"),
                 {"h": identifier_hash("99-7000001"), "i": cases[0]["return_id"]})
    with pytest.raises(ap.Abort):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_an_inactive_target_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text("UPDATE relationship_entities SET active = false WHERE id = :e"),
                 {"e": cases[0]["entity"]})
    with pytest.raises(ap.Abort, match="inactive"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_a_conflicting_link_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id, "
        "match_method, match_score, confirmed) VALUES (:e, :c, 'manual', 100.00, true)"),
        {"e": other, "c": cases[1]["contact"]})
    with pytest.raises(ap.Abort, match="already linked"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases])["mine"] == 0


def test_a_pending_candidate_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    person = conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"Candidate {uuid.uuid4().hex[:6]}"}).scalar_one()
    conn.execute(text(
        "INSERT INTO drake_identity_match_candidates (identifier_hash, person_id, score, status, "
        " reasons) VALUES (:h, :p, 50, 'pending', CAST('[]' AS jsonb))"),
        {"h": cases[0]["hash"], "p": person})
    with pytest.raises(ap.Abort, match="pending match candidate"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_reappearance_in_drake_identity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text(
        "INSERT INTO drake_identity (identifier_hash, first_year, last_year, return_count, "
        " taxpayer_name, confidence) VALUES (:h, 2021, 2021, 1, 'x', 100)"),
        {"h": cases[0]["hash"]})
    with pytest.raises(ap.Abort, match="back in drake_identity"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_a_person_link_on_the_contact_aborts_the_batch(tmp_path, conn, monkeypatch):
    """A single-contact row whose contact also denotes a person is exactly case 7379's shape."""
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    person = conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"Person {uuid.uuid4().hex[:6]}"}).scalar_one()
    conn.execute(text(
        "INSERT INTO person_source_links (person_id, source_contact_id, match_method, confirmed) "
        "VALUES (:p, :c, 'exact_email+exact_phone', true)"),
        {"p": person, "c": cases[0]["contact"]})
    with pytest.raises(ap.Abort, match="linked to a person"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_a_target_already_holding_another_identifier_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text(
        "INSERT INTO drake_business_identity (identifier_hash, subject_type, subject_name, "
        " first_year, last_year, return_count, return_types, relationship_entity_id, trust_level, "
        " confirmation_source, evidence_method) "
        "VALUES (:h, 'business_entity', 'other', 2020, 2020, 1, CAST('{1120S}' AS text[]), :e, "
        " :t, 'machine', 'drake_entity_provenance')"),
        {"h": identifier_hash("99-7100001"), "e": cases[0]["entity"], "t": IDENTIFIER_VERIFIED})
    with pytest.raises(ap.Abort, match="already holds"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


# --- rollback -------------------------------------------------------------------------------------

def test_rollback_restores_the_exact_pre_image(applied, conn, monkeypatch):
    before = applied["before"]
    report = run(applied["report"]["rollback_manifest"], None, conn, monkeypatch,
                 applied["cases"], module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    assert report["reversed"] == 3 and report["links_deleted"] == 3
    after = counts(conn, [x["dbi_id"] for x in applied["cases"]])
    assert after["attributed"] == before["attributed"]
    assert after["links"] == before["links"]
    assert after["mine"] == 0
    for case in applied["cases"]:
        row = conn.execute(text(
            "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
            "FROM drake_business_identity WHERE id = :i"), {"i": case["dbi_id"]}).mappings().one()
        assert all(v is None for v in row.values())


def test_rollback_deletes_only_the_links_it_recorded(applied, conn, monkeypatch, tmp_path):
    bystander = seed(conn, tmp_path)
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    survivor = conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id, "
        "match_method, match_score, confirmed) VALUES (:e, :c, 'manual', 100.00, true) "
        "RETURNING id"), {"e": other, "c": bystander["contact"]}).scalar_one()
    payload = json.loads(open(applied["report"]["rollback_manifest"], encoding="utf-8").read())
    recorded = {int(i) for r in payload["rows"] for i in r["created_entity_source_link_ids"]}
    before = {int(i) for i in conn.execute(text("SELECT id FROM entity_source_links")).scalars()}
    run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
        module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    after = {int(i) for i in conn.execute(text("SELECT id FROM entity_source_links")).scalars()}
    assert before - after == recorded
    assert survivor in after


def test_rollback_keeps_the_attribution_audits_and_adds_compensating_ones(
        applied, conn, monkeypatch):
    before = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                          {"a": "drake.entity_attribution_machine"}).scalar()
    run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
        module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": "drake.entity_attribution_machine"}).scalar() == before
    comp = conn.execute(text("SELECT actor_user_id FROM audit_events WHERE action = :a"),
                        {"a": rb.ROLLBACK_AUDIT_ACTION}).mappings().all()
    assert len(comp) == 3 and all(r["actor_user_id"] is None for r in comp)


def test_rollback_is_dry_by_default(applied, conn, monkeypatch):
    before = counts(conn, [x["dbi_id"] for x in applied["cases"]])
    with pytest.raises(rb.Abort, match="DRY RUN"):
        run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
            module=rb)
    assert counts(conn, [x["dbi_id"] for x in applied["cases"]]) == before


def test_rollback_refuses_a_retargeted_row(applied, conn, monkeypatch):
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE drake_business_identity SET relationship_entity_id = :e "
                      "WHERE id = :i"), {"e": other, "i": applied["cases"][1]["dbi_id"]})
    before = counts(conn, [x["dbi_id"] for x in applied["cases"]])
    with pytest.raises(rb.Abort, match="later decision"):
        run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
            module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    assert counts(conn, [x["dbi_id"] for x in applied["cases"]]) == before


def test_rollback_refuses_a_manifest_from_another_batch(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"batch": "something_else", "rows": [{"dbi_id": 1}]}),
                    encoding="utf-8")
    with pytest.raises(rb.Abort, match="is for"):
        rb.load_rollback_manifest(path)
