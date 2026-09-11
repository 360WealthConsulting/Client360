"""D7 partial-provenance document-corroborated attribution — the guarded apply and its rollback.

WHAT THESE PIN

This runner admits rows whose Drake provenance is KNOWN to be incomplete. That is a real weakening
of the provenance contract, and the only thing standing in its place is the document evidence, so
most of what follows is about that evidence failing in every way it can fail.

The second concern is the shortcut this runner must never take. Writing the missing contact ids into
``relationship_entities.details`` would make the gap disappear and let the stricter runner accept
these rows — and it would be manufacturing the corroboration rather than recording it. A test asserts
the table is in the forbidden list, another asserts no write reaches it, and a third asserts stored
provenance is byte-identical after an apply.

The nine held rows, three of which showed contradictory documents during review, must never enter a
manifest this runner will load.

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
from scripts import apply_d7_document_verified_attribution as docverified
from scripts import apply_d7_partial_provenance_document_attribution as ap
from scripts import rollback_d7_partial_provenance_document_attribution as rb
from tests.test_drake_identity_phase_c import make_contact, make_return

pytestmark = pytest.mark.skipif(
    sa_inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)

TEST_HASH_KEY = "partialdoc-tests-not-a-production-key"
_EIN_SEQUENCE = iter(range(8_500_001, 8_599_999))


@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", TEST_HASH_KEY)


@pytest.fixture()
def conn():
    """A stable snapshot: the test database is shared, and a concurrent session's committed writes
    would otherwise appear inside this transaction and look like the runner's own."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def seed(conn, tmp_path, *, contacts=3, covered=1, docs=1, name_in_doc=True, matching=True,
         extra_identifier=None, unreadable=False):
    """One partial-coverage row: several contacts, provenance naming only some, and documents."""
    ein = f"99-{next(_EIN_SEQUENCE)}"
    hash_value = identifier_hash(ein)
    tag = uuid.uuid4().hex[:8]
    name = f"PARTIALDOC {tag} LLC"

    contact_ids, return_ids = [], []
    for offset in range(contacts):
        year = 2021 + offset
        rid = make_return(conn, year=year, return_type="1120S", name=name, tp_hash=hash_value)
        conn.execute(text("UPDATE drake_client_returns SET raw_data = raw_data || "
                          "jsonb_build_object('TP_Social', :e) WHERE id = :i"),
                     {"e": ein, "i": rid})
        return_ids.append(rid)
        contact_ids.append(make_contact(conn, year=year, return_id=rid, hash_value=hash_value,
                                        name=name))
    dbi_id = conn.execute(text(
        "INSERT INTO drake_business_identity (identifier_hash, subject_type, subject_name, "
        " first_year, last_year, return_count, return_types) "
        "VALUES (:h, 'business_entity', :n, 2021, :l, :c, CAST('{1120S}' AS text[])) "
        "RETURNING id"), {"h": hash_value, "n": name.lower(), "l": 2020 + contacts,
                          "c": contacts}).scalar_one()

    entity = create_named_entity(conn, "business", name)
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"origin": "canonical_repair",
                                   "source_contact_ids": contact_ids[:covered]}), "e": entity})

    evidence, bodies = [], {}
    for n in range(docs):
        path = tmp_path / f"pd_{dbi_id}_{n}.pdf"
        path.write_bytes(f"%PDF-1.4 partial evidence {dbi_id} {n}".encode())
        doc_id = conn.execute(text(
            "INSERT INTO documents (original_name, stored_name, storage_path, storage_uri, "
            " organization_id, sha256, size_bytes) VALUES (:o, :s, :p, :u, :e, :h, :z) "
            "RETURNING id"),
            {"o": f"2023 Tax Return Documents ({name}).pdf", "s": f"pd-{dbi_id}-{n}",
             "p": str(path), "u": str(path), "e": entity,
             "h": hashlib.sha256(path.read_bytes()).hexdigest(),
             "z": path.stat().st_size}).scalar_one()
        subject = name if name_in_doc else "SOMEBODY ELSE INC"
        token = ein if matching else "99-0000002"
        body = (f"Form 1120S 2023 {subject} {token} 4525 MELROSE AVENUE ROANOKE VA\n"
                + "Schedule K-1 " + ("filler " * 60) + "\n"
                "DUMP 196810-01-2013100.0 1,500 CHEVY SIL04-01-2018100.0 16,995\n"
                + ("more filler " * 40) + "\n"
                "Paid preparer 360 Tax Solutions LLC 82-3042005 1335 Peters Creek Road\n")
        if extra_identifier:
            body += f"{subject} {extra_identifier}\n"
        bodies[str(path)] = None if unreadable else body
        evidence.append({"document_id": doc_id, "document_name": f"2023 return ({name})",
                         "document_path": str(path), "document_year": "2023",
                         "document_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "corroborating_occurrences": 1})
    return {"dbi_id": dbi_id, "hash": hash_value, "ein": ein, "name": name, "entity": entity,
            "contacts": contact_ids, "covered": contact_ids[:covered],
            "missing": contact_ids[covered:], "returns": return_ids, "evidence": evidence,
            "bodies": bodies,
            "records": [f"{2021 + i}:{r}:taxpayer" for i, r in enumerate(return_ids)]}


CSV_COLS = ["dbi_id", "identifier_hash", "subject_name", "subject_type",
            "proposed_relationship_entity_id", "proposed_entity_name", "first_year", "last_year",
            "return_count", "return_types", "drake_return_ids", "tax_years", "source_contact_ids",
            "source_record_ids", "source_contact_count", "provenance_form",
            "provenance_covered_contacts", "missing_source_contact_ids", "missing_years",
            "coverage_numerator", "coverage_denominator", "complete_coverage",
            "document_evidence_count", "document_occurrences", "document_years",
            "document_covers_missing_year", "contradiction_count", "expected_esl_inserts",
            "expected_trust_level", "expected_confirmation_source", "expected_evidence_method",
            "dbi_row_sha256", "target_entity_sha256"]


def write_pair(tmp_path, cases, *, mutate_row=None, mutate_sidecar=None, name="manifest"):
    rows, sidecar_rows = [], []
    for c in cases:
        row = {"dbi_id": c["dbi_id"], "identifier_hash": c["hash"],
               "subject_name": c["name"].lower(), "subject_type": "business_entity",
               "proposed_relationship_entity_id": c["entity"], "proposed_entity_name": c["name"],
               "first_year": 2021, "last_year": 2020 + len(c["contacts"]),
               "return_count": len(c["contacts"]), "return_types": "1120S",
               "drake_return_ids": "|".join(str(x) for x in sorted(c["returns"])),
               "tax_years": "|".join(str(2021 + i) for i in range(len(c["contacts"]))),
               "source_contact_ids": "|".join(str(x) for x in sorted(c["contacts"])),
               "source_record_ids": "|".join(c["records"]),
               "source_contact_count": len(c["contacts"]),
               "provenance_form": "details.source_contact_ids",
               "provenance_covered_contacts": "|".join(str(x) for x in sorted(c["covered"])),
               "missing_source_contact_ids": "|".join(str(x) for x in sorted(c["missing"])),
               "missing_years": "2022", "coverage_numerator": len(c["covered"]),
               "coverage_denominator": len(c["contacts"]), "complete_coverage": "False",
               "document_evidence_count": len(c["evidence"]), "document_occurrences": 1,
               "document_years": "2023", "document_covers_missing_year": "False",
               "contradiction_count": 0, "expected_esl_inserts": len(c["contacts"]),
               "expected_trust_level": IDENTIFIER_VERIFIED,
               "expected_confirmation_source": "machine",
               "expected_evidence_method": "drake_entity_provenance",
               "dbi_row_sha256": "0" * 64, "target_entity_sha256": "0" * 64}
        rows.append(mutate_row(row) if mutate_row else row)
        sidecar_rows.append({"dbi_id": c["dbi_id"], "identifier_hash": c["hash"],
                             "proposed_relationship_entity_id": c["entity"],
                             "source_contact_ids": sorted(c["contacts"]),
                             "document_evidence": list(c["evidence"])})
    if mutate_sidecar:
        sidecar_rows = mutate_sidecar(sidecar_rows)

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    csv_path = tmp_path / f"{name}.csv"
    csv_path.write_bytes(buf.getvalue().encode("utf-8"))

    by_id = {int(r["dbi_id"]): r for r in sidecar_rows}
    sidecar = {"batch": ap.BATCH_ID, "row_count": len(rows),
               "manifest_csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
               "plan_digest": ap.plan_digest(rows, by_id), "rows": sidecar_rows}
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
                    return self
            return _Proxy(conn)

    monkeypatch.setattr("app.db.engine", _Engine())
    kw.setdefault("out", lambda *a, **k: None)
    if module is ap:
        kw.setdefault("expect_sha", None)
        kw.setdefault("expect_sidecar", None)
        kw.setdefault("expect_digest", None)
        kw.setdefault("expect_rows", None)
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


def apply_ok(c, j, conn, monkeypatch, cases, rows, tmp_path):
    return run(c, j, conn, monkeypatch, cases, apply_changes=True,
               confirm=ap.confirm_phrase(rows), output_root=tmp_path / "var")


# --- the policy boundary between the three runners -------------------------------------------------

def test_each_runner_keeps_its_own_policy():
    assert batch1.MIN_SOURCE_CONTACTS == 2
    assert docverified.MIN_SOURCE_CONTACTS == 1
    assert docverified.DOCUMENT_CORROBORATION_REQUIRED is True
    assert ap.REQUIRE_PARTIAL_COVERAGE is True
    assert ap.DOCUMENT_CORROBORATION_REQUIRED is True
    assert ap.MAX_CONTRADICTIONS == 0
    assert len({batch1.ADVISORY_LOCK_KEY, docverified.ADVISORY_LOCK_KEY,
                ap.ADVISORY_LOCK_KEY, rb.ADVISORY_LOCK_KEY}) == 4


def test_the_evidence_rules_are_imported_not_restated():
    """The two document runners must not drift apart on what corroboration means."""
    assert ap.IDENTIFIER_TOKEN is docverified.IDENTIFIER_TOKEN
    assert ap.NAME_ADJACENCY == docverified.NAME_ADJACENCY == 60
    assert ap.flat is docverified.flat


def test_relationship_entities_is_forbidden_in_both_directions():
    """Closing the provenance gap by writing to the entity is the shortcut this must never take."""
    assert "relationship_entities" in ap.FORBIDDEN_TABLES
    assert "relationship_entities" in rb.FORBIDDEN_TABLES
    source = open(ap.__file__, encoding="utf-8").read()
    assert not __import__("re").search(
        r"(?i)(insert\s+into|update)\s+relationship_entities", source)


def test_a_complete_coverage_row_is_refused(tmp_path, conn):
    """A row whose provenance covers everything belongs to the stricter runner."""
    case = seed(conn, tmp_path, contacts=3, covered=3)
    c, j = write_pair(tmp_path, [case],
                      mutate_row=lambda r: {**r, "complete_coverage": "True"})
    with pytest.raises(ap.Abort, match="claims complete coverage"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


def test_a_row_naming_no_missing_contact_is_refused(tmp_path, conn):
    case = seed(conn, tmp_path)
    c, j = write_pair(tmp_path, [case],
                      mutate_row=lambda r: {**r, "missing_source_contact_ids": ""})
    with pytest.raises(ap.Abort, match="not partial"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


# --- the nine held rows ----------------------------------------------------------------------------

def test_the_nine_held_rows_are_pinned():
    assert ap.HELD_DBI_IDS == frozenset({531, 533, 552, 555, 563, 578, 589, 620, 637})
    assert ap.CONTRADICTION_DBI_IDS == frozenset({555, 578, 620})
    assert ap.CONTRADICTION_DBI_IDS <= ap.HELD_DBI_IDS


@pytest.mark.parametrize("held", [531, 533, 552, 555, 563, 578, 589, 620, 637])
def test_a_manifest_containing_a_held_row_is_refused(tmp_path, conn, held):
    case = seed(conn, tmp_path)
    c, j = write_pair(tmp_path, [case], mutate_row=lambda r: {**r, "dbi_id": held},
                      mutate_sidecar=lambda rows: [{**rows[0], "dbi_id": held}])
    with pytest.raises(ap.Abort, match="held row"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


def test_a_row_with_a_recorded_contradiction_is_refused(tmp_path, conn):
    case = seed(conn, tmp_path)
    c, j = write_pair(tmp_path, [case], mutate_row=lambda r: {**r, "contradiction_count": 1})
    with pytest.raises(ap.Abort, match="recorded contradiction"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


# --- the manifest gate -----------------------------------------------------------------------------

def test_altered_manifest_bytes_are_refused_before_any_database_access(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    with pytest.raises(ap.Abort, match="manifest SHA256"):
        ap.load_manifest(c, j, expect_sha="0" * 64, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


def test_a_wrong_plan_digest_is_refused(tmp_path, conn):
    c, j = write_pair(tmp_path, [seed(conn, tmp_path)])
    with pytest.raises(ap.Abort, match="plan digest"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest="f" * 64,
                         expect_rows=None)


def test_the_plan_digest_covers_the_authorizing_documents(tmp_path, conn):
    """Swapping which documents authorize a row must change the plan, not pass silently."""
    case = seed(conn, tmp_path, docs=2)
    c, j = write_pair(tmp_path, [case], name="a")
    rows = list(csv.DictReader(c.read_text(encoding="utf-8").splitlines()))
    by_id = {int(r["dbi_id"]): r for r in json.loads(j.read_text(encoding="utf-8"))["rows"]}
    full = ap.plan_digest(rows, by_id)
    trimmed = {k: {**v, "document_evidence": v["document_evidence"][:1]} for k, v in by_id.items()}
    assert ap.plan_digest(rows, trimmed) != full


def test_a_row_without_document_evidence_is_refused(tmp_path, conn):
    case = seed(conn, tmp_path, docs=0)
    c, j = write_pair(tmp_path, [case])
    with pytest.raises(ap.Abort, match="no document evidence"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


def test_two_rows_targeting_one_entity_are_refused(tmp_path, conn):
    a, b = seed(conn, tmp_path), seed(conn, tmp_path)
    b["entity"] = a["entity"]
    c, j = write_pair(tmp_path, [a, b])
    with pytest.raises(ap.Abort, match="one entity more than once"):
        ap.load_manifest(c, j, expect_sha=None, expect_sidecar=None, expect_digest=None,
                         expect_rows=None)


# --- dry run ---------------------------------------------------------------------------------------

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
        run(c, j, conn, monkeypatch, cases, apply_changes=True, confirm="APPLY-D7-PARTIALDOC-99")
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


# --- the happy path --------------------------------------------------------------------------------

@pytest.fixture()
def applied(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path, contacts=3, covered=1),
             seed(conn, tmp_path, contacts=2, covered=1, docs=2),
             seed(conn, tmp_path, contacts=5, covered=3)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    entity_fp = conn.execute(text(
        "SELECT md5(string_agg(t::text, '~' ORDER BY t::text)) FROM relationship_entities t")
    ).scalar()
    report = run(c, j, conn, monkeypatch, cases, apply_changes=True,
                 confirm=ap.confirm_phrase(3), output_root=tmp_path / "var")
    return {"cases": cases, "csv": c, "json": j, "before": before, "report": report,
            "entity_fp": entity_fp}


def test_the_whole_batch_applies_together(applied, conn):
    before, report, cases = applied["before"], applied["report"], applied["cases"]
    after = counts(conn, [x["dbi_id"] for x in cases])
    assert report["attributed"] == 3
    assert report["links_created"] == 10          # 3 + 2 + 5 contacts
    assert report["documents_verified"] == 4      # one row carries two documents
    assert after["dbi"] == before["dbi"]
    assert after["attributed"] == before["attributed"] + 3
    assert after["links"] == before["links"] + 10
    assert after["audits"] == before["audits"] + 3
    assert after["mine"] == 3


def test_stored_provenance_is_untouched(applied, conn):
    """The gap stays visible. Nothing backfilled it."""
    now = conn.execute(text(
        "SELECT md5(string_agg(t::text, '~' ORDER BY t::text)) FROM relationship_entities t")
    ).scalar()
    assert now == applied["entity_fp"]
    for case in applied["cases"]:
        details = conn.execute(text("SELECT details FROM relationship_entities WHERE id = :e"),
                               {"e": case["entity"]}).scalar()
        details = details if isinstance(details, dict) else json.loads(details or "{}")
        assert sorted(details["source_contact_ids"]) == sorted(case["covered"])
        for missing in case["missing"]:
            assert missing not in details["source_contact_ids"]


def test_every_row_carries_machine_trust_and_no_approver(applied, conn):
    for case in applied["cases"]:
        row = conn.execute(text(
            "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method, "
            "confirmed_by_user_id FROM drake_business_identity WHERE id = :i"),
            {"i": case["dbi_id"]}).mappings().one()
        assert row["relationship_entity_id"] == case["entity"]
        assert row["trust_level"] == IDENTIFIER_VERIFIED
        assert row["confirmation_source"] == "machine"
        assert row["evidence_method"] == "drake_entity_provenance"
        assert row["confirmed_by_user_id"] is None


def test_links_cover_every_contact_including_the_uncovered_ones(applied, conn):
    """The links follow the identifier, not the entity's partial provenance."""
    for case in applied["cases"]:
        linked = [int(x) for x in conn.execute(text(
            "SELECT source_contact_id FROM entity_source_links WHERE source_contact_id = ANY(:i) "
            "ORDER BY source_contact_id"), {"i": case["contacts"]}).scalars()]
        assert linked == sorted(case["contacts"])
        assert set(case["missing"]) <= set(linked)


def test_the_rollback_manifest_records_the_gap_and_the_evidence(applied):
    payload = json.loads(open(applied["report"]["rollback_manifest"], encoding="utf-8").read())
    assert len(payload["rows"]) == 3
    created = [i for r in payload["rows"] for i in r["created_entity_source_link_ids"]]
    assert len(created) == 10 and len(set(created)) == 10
    for row in payload["rows"]:
        assert row["document_evidence"]
        assert row["missing_source_contact_ids"]
        assert row["dbi_pre_image"] == {"relationship_entity_id": None, "trust_level": None,
                                        "confirmation_source": None, "evidence_method": None}
        assert row["audit_event_id"] > 0


def test_no_raw_identifier_reaches_any_artifact_or_the_console(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    spoken = []
    report = run(c, j, conn, monkeypatch, cases, apply_changes=True,
                 confirm=ap.confirm_phrase(1), output_root=tmp_path / "var2",
                 out=lambda *a, **k: spoken.append(" ".join(str(x) for x in a)))
    blob = "\n".join(spoken)
    for path in (report["rollback_manifest"], report["report_dir"] + "/receipt.json"):
        blob += open(path, encoding="utf-8").read()
    assert cases[0]["ein"] not in blob
    assert not ap.IDENTIFIER_TOKEN.search(blob)


def test_the_audit_records_the_document_count_without_the_identifier(applied, conn):
    rows = conn.execute(text(
        "SELECT metadata FROM audit_events WHERE action = :a AND entity_id = ANY(:e)"),
        {"a": "drake.entity_attribution_machine",
         "e": [str(c["entity"]) for c in applied["cases"]]}).scalars().all()
    assert len(rows) == 3
    for meta in rows:
        meta = meta if isinstance(meta, dict) else json.loads(meta)
        assert "corroborating document" in str(meta.get("reason", ""))
        assert not ap.IDENTIFIER_TOKEN.search(json.dumps(meta))


# --- document evidence failing in every way it can -------------------------------------------------

def test_a_missing_document_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    conn.execute(text("DELETE FROM documents WHERE id = :i"),
                 {"i": cases[1]["evidence"][0]["document_id"]})
    with pytest.raises(ap.Abort, match="no longer exists"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_changed_document_fingerprint_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    open(cases[1]["evidence"][0]["document_path"], "ab").write(b"tampered")
    with pytest.raises(ap.Abort, match="changed since the freeze"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_moved_document_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text("UPDATE documents SET storage_uri = :u WHERE id = :i"),
                 {"u": str(tmp_path / "elsewhere.pdf"),
                  "i": cases[0]["evidence"][0]["document_id"]})
    with pytest.raises(ap.Abort, match="has moved since the freeze"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_a_document_refiled_under_another_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE documents SET organization_id = :e WHERE id = :i"),
                 {"e": other, "i": cases[0]["evidence"][0]["document_id"]})
    with pytest.raises(ap.Abort, match="no longer filed under"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_a_document_whose_identifier_changed_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path, matching=False)]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    with pytest.raises(ap.Abort, match="not this identifier"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_a_document_that_no_longer_names_the_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    """Name-only matching is refused in the other direction too: no name, no corroboration."""
    cases = [seed(conn, tmp_path, name_in_doc=False)]
    c, j = write_pair(tmp_path, cases)
    with pytest.raises(ap.Abort, match="no longer names entity"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_a_contradictory_identifier_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path, extra_identifier="55-5555555")]
    c, j = write_pair(tmp_path, cases)
    before = counts(conn, [x["dbi_id"] for x in cases])
    with pytest.raises(ap.Abort, match="not this identifier"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)
    assert counts(conn, [x["dbi_id"] for x in cases]) == before


def test_unreadable_evidence_is_refused_not_ignored(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path, unreadable=True)]
    c, j = write_pair(tmp_path, cases)
    with pytest.raises(ap.Abort, match="could not be read"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_the_preparers_identifier_is_not_treated_as_contradictory(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    assert apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)["attributed"] == 1


# --- state drift -----------------------------------------------------------------------------------

def test_provenance_coverage_drift_aborts_the_batch(tmp_path, conn, monkeypatch):
    """If somebody backfills the gap, the frozen row no longer describes what was reviewed."""
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"origin": "canonical_repair",
                                   "source_contact_ids": cases[0]["contacts"]}),
                  "e": cases[0]["entity"]})
    with pytest.raises(ap.Abort, match="coverage changed since the freeze"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_an_already_attributed_row_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path), seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE drake_business_identity SET relationship_entity_id = :e "
                      "WHERE id = :i"), {"e": other, "i": cases[1]["dbi_id"]})
    with pytest.raises(ap.Abort, match="already attributed"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)


def test_source_contact_drift_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn, tmp_path)]
    c, j = write_pair(tmp_path, cases)
    extra = make_return(conn, year=2025, return_type="1120S", name=cases[0]["name"],
                        tp_hash=cases[0]["hash"])
    make_contact(conn, year=2025, return_id=extra, hash_value=cases[0]["hash"],
                 name=cases[0]["name"])
    with pytest.raises(ap.Abort, match="source-contact set changed"):
        apply_ok(c, j, conn, monkeypatch, cases, 1, tmp_path)


def test_an_inactive_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
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
        {"e": other, "c": cases[1]["contacts"][0]})
    with pytest.raises(ap.Abort, match="already linked"):
        apply_ok(c, j, conn, monkeypatch, cases, 2, tmp_path)


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


# --- rollback --------------------------------------------------------------------------------------

def test_rollback_restores_the_exact_pre_image(applied, conn, monkeypatch):
    before = applied["before"]
    report = run(applied["report"]["rollback_manifest"], None, conn, monkeypatch,
                 applied["cases"], module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    assert report["reversed"] == 3 and report["links_deleted"] == 10
    after = counts(conn, [x["dbi_id"] for x in applied["cases"]])
    assert after["attributed"] == before["attributed"]
    assert after["links"] == before["links"]
    assert after["mine"] == 0
    for case in applied["cases"]:
        row = conn.execute(text(
            "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
            "FROM drake_business_identity WHERE id = :i"), {"i": case["dbi_id"]}).mappings().one()
        assert all(v is None for v in row.values())


def test_rollback_leaves_stored_provenance_alone(applied, conn, monkeypatch):
    run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
        module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    now = conn.execute(text(
        "SELECT md5(string_agg(t::text, '~' ORDER BY t::text)) FROM relationship_entities t")
    ).scalar()
    assert now == applied["entity_fp"]


def test_rollback_deletes_only_the_links_it_recorded(applied, conn, monkeypatch, tmp_path):
    bystander = seed(conn, tmp_path)
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    survivor = conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id, "
        "match_method, match_score, confirmed) VALUES (:e, :c, 'manual', 100.00, true) "
        "RETURNING id"), {"e": other, "c": bystander["contacts"][0]}).scalar_one()
    payload = json.loads(open(applied["report"]["rollback_manifest"], encoding="utf-8").read())
    recorded = {int(i) for r in payload["rows"] for i in r["created_entity_source_link_ids"]}
    before = {int(i) for i in conn.execute(text("SELECT id FROM entity_source_links")).scalars()}
    run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
        module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    after = {int(i) for i in conn.execute(text("SELECT id FROM entity_source_links")).scalars()}
    assert before - after == recorded
    assert survivor in after


def test_rollback_keeps_the_attribution_audits_and_records_the_documents(
        applied, conn, monkeypatch):
    before = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                          {"a": "drake.entity_attribution_machine"}).scalar()
    run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
        module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": "drake.entity_attribution_machine"}).scalar() == before
    comp = conn.execute(text("SELECT actor_user_id, metadata FROM audit_events WHERE action = :a"),
                        {"a": rb.ROLLBACK_AUDIT_ACTION}).mappings().all()
    assert len(comp) == 3
    for row in comp:
        assert row["actor_user_id"] is None
        meta = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
        assert meta["authorizing_document_ids"]
        assert meta["drake_provenance_gap_preserved"] is True


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
    with pytest.raises(rb.Abort, match="later decision"):
        run(applied["report"]["rollback_manifest"], None, conn, monkeypatch, applied["cases"],
            module=rb, apply_changes=True, confirm=rb.confirm_phrase(3))


def test_rollback_refuses_a_receipt_without_document_evidence(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"batch": ap.BATCH_ID, "rows": [
        {"dbi_id": 1, "identifier_hash": "x", "target_entity_id": 2, "source_contact_ids": [3],
         "created_entity_source_link_ids": [4], "dbi_pre_image": {
             "relationship_entity_id": None, "trust_level": None,
             "confirmation_source": None, "evidence_method": None}}]}), encoding="utf-8")
    with pytest.raises(rb.Abort, match="records no document evidence"):
        rb.load_rollback_manifest(path)
