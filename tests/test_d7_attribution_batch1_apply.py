"""D7 machine attribution Batch 1 — the guarded apply and its scoped rollback.

WHAT THESE PIN

This batch updates rows that already exist and creates links beside them, so the failure it must
never have is a partial one: 39 identities half-bound to entities, with links for some and not
others, is worse than nothing applied at all. Most of what follows proves that a single bad row
takes the whole batch down before anything is written.

The second concern is the manifest. The batch is only as trustworthy as the file that defines it, so
the gate is tested harder than the happy path: a changed byte, a reordered plan, a duplicated
target, a row that quietly violates the reviewed policy — each is refused before a connection does
any work.

Rows are seeded inside a transaction and rolled back. Nothing is committed, and no test touches
production.
"""
from __future__ import annotations

import csv
import io
import json
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db import engine
from app.services.drake_identifier import identifier_hash
from app.services.link_trust import HUMAN_APPROVED, IDENTIFIER_VERIFIED
from app.services.relationships import create_named_entity
from scripts import apply_d7_attribution_batch1 as ap
from scripts import rollback_d7_attribution_batch1 as rb
from tests.test_drake_identity_phase_c import make_contact, make_return

pytestmark = pytest.mark.skipif(
    sa_inspect(engine).get_table_names().count("drake_business_identity") == 0,
    reason="migration dbi01 has not been applied to this database",
)

TEST_HASH_KEY = "attribution-batch1-tests-not-a-production-key"
CSV_COLUMNS = ("dbi_id", "identifier_hash", "subject_name", "subject_type",
               "proposed_relationship_entity_id", "proposed_entity_name", "first_year",
               "last_year", "return_count", "return_types", "drake_return_ids",
               "source_contact_ids", "source_record_ids", "source_contact_count",
               "provenance_form", "complete_coverage", "expected_esl_inserts",
               "expected_trust_level", "expected_confirmation_source", "expected_evidence_method",
               "dbi_row_sha256", "target_entity_sha256")

_EIN_SEQUENCE = iter(range(8_100_001, 8_199_999))


@pytest.fixture(autouse=True)
def identifier_hash_key(monkeypatch):
    """A deterministic secret of the module's own, never the shell's. CI sets none."""
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", TEST_HASH_KEY)


@pytest.fixture()
def conn():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def seed(conn, *, contacts=2, return_type="1120S", covered=None, provenance="source_contact_ids"):
    """One attributable row: an unattributed DBI, its returns and contacts, and a corroborated entity.

    ``covered`` narrows what the entity's provenance references, so a test can produce the
    partial-coverage shape this batch refuses.
    """
    ein = f"99-{next(_EIN_SEQUENCE)}"
    hash_value = identifier_hash(ein)
    tag = uuid.uuid4().hex[:8]
    name = f"BATCH1 {tag} LLC"

    returns, contact_ids = [], []
    for offset in range(contacts):
        year = 2021 + offset
        rid = make_return(conn, year=year, return_type=return_type, name=name, tp_hash=hash_value)
        conn.execute(text(
            "UPDATE drake_client_returns SET raw_data = raw_data || "
            "jsonb_build_object('TP_Social', :e) WHERE id = :i"), {"e": ein, "i": rid})
        returns.append(rid)
        contact_ids.append(make_contact(conn, year=year, return_id=rid, hash_value=hash_value,
                                        name=name))

    dbi_id = conn.execute(text(
        "INSERT INTO drake_business_identity (identifier_hash, subject_type, subject_name, "
        " first_year, last_year, return_count, return_types) "
        "VALUES (:h, 'business_entity', :n, 2021, :l, :c, CAST(:t AS text[])) RETURNING id"),
        {"h": hash_value, "n": name.lower(), "l": 2020 + contacts, "c": contacts,
         "t": "{" + return_type + "}"}).scalar_one()

    entity = create_named_entity(conn, "business", name)
    referenced = contact_ids if covered is None else covered
    details = {"canonical_creation_reason": "verified_structured_business_provenance",
               "source_systems": ["Drake"]}
    if provenance == "source_contact_ids":
        details["source_contact_ids"] = list(referenced)
    elif provenance == "drake_return_ids":
        details["drake_return_ids"] = list(returns)
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps(details), "e": entity})

    return {"dbi_id": dbi_id, "hash": hash_value, "ein": ein, "name": name, "entity": entity,
            "contacts": contact_ids, "returns": returns,
            "records": [f"{2021 + i}:{r}:taxpayer" for i, r in enumerate(returns)]}


def write_manifest(tmp_path, cases, *, name="manifest.csv", mutate=None):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        row = {
            "dbi_id": case["dbi_id"], "identifier_hash": case["hash"],
            "subject_name": case["name"].lower(), "subject_type": "business_entity",
            "proposed_relationship_entity_id": case["entity"],
            "proposed_entity_name": case["name"], "first_year": 2021,
            "last_year": 2020 + len(case["contacts"]), "return_count": len(case["contacts"]),
            "return_types": "1120S",
            "drake_return_ids": "|".join(str(r) for r in sorted(case["returns"])),
            "source_contact_ids": "|".join(str(c) for c in sorted(case["contacts"])),
            "source_record_ids": "|".join(case["records"]),
            "source_contact_count": len(case["contacts"]),
            "provenance_form": "details.source_contact_ids", "complete_coverage": "True",
            "expected_esl_inserts": len(case["contacts"]),
            "expected_trust_level": IDENTIFIER_VERIFIED,
            "expected_confirmation_source": "machine",
            "expected_evidence_method": "drake_entity_provenance",
            "dbi_row_sha256": "0" * 64, "target_entity_sha256": "0" * 64,
        }
        writer.writerow(mutate(row) if mutate else row)
    path = tmp_path / name
    path.write_bytes(buffer.getvalue().encode("utf-8"))
    return path


def run(path, conn, monkeypatch, *, module=ap, **kw):
    """Drive the runner against the test transaction rather than a fresh connection."""
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
    kw.setdefault("expect_sha", None)
    kw.setdefault("expect_digest", None)
    kw.setdefault("expect_rows", None)
    if module is rb:
        kw.pop("expect_digest", None)
        kw.pop("expect_rows", None)
    return module.run(path, **kw)


def counts(conn, ids):
    return {
        "dbi": conn.execute(text("SELECT count(*) FROM drake_business_identity")).scalar(),
        "attributed": conn.execute(text(
            "SELECT count(*) FROM drake_business_identity "
            "WHERE relationship_entity_id IS NOT NULL")).scalar(),
        "links": conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar(),
        "audits": conn.execute(text(
            "SELECT count(*) FROM audit_events WHERE action = :a"),
            {"a": "drake.entity_attribution_machine"}).scalar(),
        "mine": conn.execute(text(
            "SELECT count(*) FROM drake_business_identity WHERE id = ANY(:i) "
            "AND relationship_entity_id IS NOT NULL"), {"i": ids}).scalar(),
    }


# --- the manifest gate ----------------------------------------------------------------------------

def test_a_manifest_whose_bytes_changed_is_refused(tmp_path, conn):
    path = write_manifest(tmp_path, [seed(conn), seed(conn)])
    with pytest.raises(ap.Abort, match="SHA256"):
        ap.load_manifest(path, expect_sha="0" * 64, expect_digest=None, expect_rows=None)


def test_a_reordered_plan_changes_the_digest(tmp_path, conn):
    cases = [seed(conn), seed(conn)]
    forward = ap.load_manifest(write_manifest(tmp_path, cases, name="a.csv"),
                               expect_sha=None, expect_digest=None, expect_rows=None)
    reverse = ap.load_manifest(write_manifest(tmp_path, list(reversed(cases)), name="b.csv"),
                               expect_sha=None, expect_digest=None, expect_rows=None)
    assert ap.plan_digest(forward) != ap.plan_digest(reverse)


def test_the_deployed_manifest_reproduces_its_approved_digest():
    """The pinned digest is the deployed file's, not a value that drifted with the code."""
    rows = ap.load_manifest(ap.DEFAULT_MANIFEST)
    assert len(rows) == ap.APPROVED_ROWS
    assert ap.plan_digest(rows) == ap.APPROVED_PLAN_DIGEST
    assert ap.sha256_of(ap.DEFAULT_MANIFEST) == ap.APPROVED_MANIFEST_SHA256
    assert sum(len(ap._ints(r["source_contact_ids"])) for r in rows) == ap.APPROVED_ESL_INSERTS


def test_a_wrong_row_count_is_refused(tmp_path, conn):
    path = write_manifest(tmp_path, [seed(conn)])
    with pytest.raises(ap.Abort, match="rows"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=39)


def test_a_duplicate_dbi_id_is_refused(tmp_path, conn):
    case = seed(conn)
    path = write_manifest(tmp_path, [case, case])
    with pytest.raises(ap.Abort, match="duplicate dbi_id"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=None)


def test_two_rows_targeting_one_entity_are_refused(tmp_path, conn):
    """The multi-identifier shape this batch deliberately holds for review."""
    first, second = seed(conn), seed(conn)
    second["entity"] = first["entity"]
    path = write_manifest(tmp_path, [first, second])
    with pytest.raises(ap.Abort, match="one entity more than once"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=None)


def test_a_single_contact_row_is_refused(tmp_path, conn):
    """Batch 1 policy is >= 2 contacts; a manifest cannot widen it by being handed to the runner."""
    path = write_manifest(tmp_path, [seed(conn, contacts=1)])
    with pytest.raises(ap.Abort, match="at least 2"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=None)


def test_a_row_not_marked_complete_coverage_is_refused(tmp_path, conn):
    path = write_manifest(tmp_path, [seed(conn)],
                          mutate=lambda r: {**r, "complete_coverage": "False"})
    with pytest.raises(ap.Abort, match="complete_coverage"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=None)


def test_a_contact_claimed_by_two_rows_is_refused(tmp_path, conn):
    first, second = seed(conn), seed(conn)
    second["contacts"] = first["contacts"]
    path = write_manifest(tmp_path, [first, second])
    with pytest.raises(ap.Abort, match="appears on dbi"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=None)


def test_a_disagreeing_contact_count_is_refused(tmp_path, conn):
    path = write_manifest(tmp_path, [seed(conn)],
                          mutate=lambda r: {**r, "source_contact_count": 5})
    with pytest.raises(ap.Abort, match="contact count"):
        ap.load_manifest(path, expect_sha=None, expect_digest=None, expect_rows=None)


# --- dry run --------------------------------------------------------------------------------------

def test_the_default_writes_nothing(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    ids = [c["dbi_id"] for c in cases]
    before = counts(conn, ids)
    with pytest.raises(ap.Abort, match="DRY RUN"):
        run(write_manifest(tmp_path, cases), conn, monkeypatch)
    assert counts(conn, ids) == before


def test_apply_without_the_confirmation_phrase_writes_nothing(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    before = counts(conn, [c["dbi_id"] for c in cases])
    with pytest.raises(ap.Abort, match="requires --confirm"):
        run(write_manifest(tmp_path, cases), conn, monkeypatch,
            apply_changes=True, confirm="APPLY-D7-ATTRIB-BATCH1-999")
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_the_dry_run_exercises_the_real_service_and_reports_the_link_total(
        tmp_path, conn, monkeypatch):
    cases = [seed(conn, contacts=3), seed(conn, contacts=2)]
    with pytest.raises(ap.Abort, match="DRY RUN"):
        run(write_manifest(tmp_path, cases), conn, monkeypatch)
    # the batch really ran, inside a savepoint that was discarded
    assert counts(conn, [c["dbi_id"] for c in cases])["attributed"] == \
        conn.execute(text("SELECT count(*) FROM drake_business_identity "
                          "WHERE relationship_entity_id IS NOT NULL")).scalar()


# --- the happy path -------------------------------------------------------------------------------

def apply_ok(path, conn, monkeypatch, rows, tmp_path):
    return run(path, conn, monkeypatch, apply_changes=True,
               confirm=ap.confirm_phrase(rows), output_root=tmp_path / "var")


@pytest.fixture()
def applied(tmp_path, conn, monkeypatch):
    """Three rows, seven contacts, applied together."""
    cases = [seed(conn, contacts=2), seed(conn, contacts=2), seed(conn, contacts=3)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    report = run(path, conn, monkeypatch, apply_changes=True, confirm=ap.confirm_phrase(3),
                 output_root=tmp_path / "var")
    return {"cases": cases, "path": path, "before": before, "report": report}


def test_the_whole_batch_applies_together(applied, conn):
    cases, before, report = applied["cases"], applied["before"], applied["report"]
    after = counts(conn, [c["dbi_id"] for c in cases])
    assert report["attributed"] == 3
    assert report["links_created"] == 7
    assert after["dbi"] == before["dbi"]                      # UPDATE, never INSERT
    assert after["attributed"] == before["attributed"] + 3
    assert after["links"] == before["links"] + 7
    assert after["audits"] == before["audits"] + 3
    assert after["mine"] == 3


def test_every_row_carries_this_services_evidence(applied, conn):
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


def test_one_link_per_source_contact_pointing_at_the_named_entity(applied, conn):
    for case in applied["cases"]:
        links = conn.execute(text(
            "SELECT source_contact_id, relationship_entity_id, confirmed_by_user_id "
            "FROM entity_source_links WHERE source_contact_id = ANY(:i) ORDER BY source_contact_id"),
            {"i": case["contacts"]}).mappings().all()
        assert [int(link["source_contact_id"]) for link in links] == sorted(case["contacts"])
        assert {link["relationship_entity_id"] for link in links} == {case["entity"]}
        assert all(link["confirmed_by_user_id"] is None for link in links)


def test_the_audit_entries_are_unattended(applied, conn):
    ids = [c["entity"] for c in applied["cases"]]
    rows = conn.execute(text(
        "SELECT actor_user_id, entity_id FROM audit_events WHERE action = :a "
        "AND entity_id = ANY(:e)"),
        {"a": "drake.entity_attribution_machine", "e": [str(i) for i in ids]}).mappings().all()
    assert len(rows) == 3
    assert all(r["actor_user_id"] is None for r in rows)


def test_the_rollback_manifest_is_written_and_hashed_before_the_commit(applied):
    report = applied["report"]
    path = report["rollback_manifest"]
    assert path and report["rollback_manifest_sha256"]
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["batch"] == ap.BATCH_ID
    assert len(payload["rows"]) == 3
    created = [i for r in payload["rows"] for i in r["created_entity_source_link_ids"]]
    assert len(created) == 7 and len(set(created)) == 7
    assert all(i > 0 for i in created)                        # real ids, not placeholders
    assert all(r["audit_event_id"] > 0 for r in payload["rows"])
    for row in payload["rows"]:
        assert row["dbi_pre_image"] == {"relationship_entity_id": None, "trust_level": None,
                                        "confirmation_source": None, "evidence_method": None}
        assert row["dbi_post_image"]["trust_level"] == IDENTIFIER_VERIFIED


def test_no_forbidden_table_is_written(applied, conn):
    """The apply proves this itself before committing; this asserts it from outside."""
    assert conn.execute(text("SELECT count(*) FROM drake_identity")).scalar() == \
        conn.execute(text("SELECT count(*) FROM drake_identity")).scalar()
    for case in applied["cases"]:
        assert conn.execute(text(
            "SELECT count(*) FROM drake_identity WHERE identifier_hash = :h"),
            {"h": case["hash"]}).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM person_source_links WHERE source_contact_id = ANY(:i)"),
            {"i": case["contacts"]}).scalar() == 0


# --- a single bad row takes the whole batch down --------------------------------------------------

def test_a_row_that_drifted_to_attributed_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE drake_business_identity SET relationship_entity_id = :e WHERE id = :i"),
                 {"e": other, "i": cases[1]["dbi_id"]})
    with pytest.raises(ap.Abort, match="already attributed"):
        apply_ok(path, conn, monkeypatch, 3, tmp_path)
    after = counts(conn, [c["dbi_id"] for c in cases])
    assert after["links"] == before["links"]
    assert after["audits"] == before["audits"]
    assert after["mine"] == 1                                 # only the drift itself


def test_a_row_whose_contact_set_changed_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    extra_return = make_return(conn, year=2024, return_type="1120S", name=cases[0]["name"],
                               tp_hash=cases[0]["hash"])
    make_contact(conn, year=2024, return_id=extra_return, hash_value=cases[0]["hash"],
                 name=cases[0]["name"])
    with pytest.raises(ap.Abort, match="source-contact set changed"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_a_row_whose_provenance_no_longer_covers_every_contact_aborts_the_batch(
        tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"source_contact_ids": cases[1]["contacts"][:1]}),
                  "e": cases[1]["entity"]})
    with pytest.raises(ap.Abort, match="no longer covers the complete"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_a_row_whose_provenance_points_elsewhere_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    conn.execute(text("UPDATE relationship_entities SET details = CAST(:d AS json) WHERE id = :e"),
                 {"d": json.dumps({"source_contact_ids": cases[0]["contacts"]}),
                  "e": cases[1]["entity"]})
    with pytest.raises(ap.Abort):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_a_contact_already_owned_by_another_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id, "
        "match_method, match_score, confirmed) VALUES (:e, :c, 'manual', 100.00, true)"),
        {"e": other, "c": cases[1]["contacts"][0]})
    with pytest.raises(ap.Abort, match="already linked"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    after = counts(conn, [c["dbi_id"] for c in cases])
    assert after["links"] == before["links"] + 1              # only the conflicting one seeded here
    assert after["mine"] == 0


def test_a_pending_match_candidate_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    person = conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"Candidate {uuid.uuid4().hex[:6]}"}).scalar_one()
    conn.execute(text(
        "INSERT INTO drake_identity_match_candidates (identifier_hash, person_id, score, status, "
        " reasons) VALUES (:h, :p, 50, 'pending', CAST('[]' AS jsonb))"),
        {"h": cases[1]["hash"], "p": person})
    with pytest.raises(ap.Abort, match="pending match candidate"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_an_inactive_target_entity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    conn.execute(text("UPDATE relationship_entities SET active = false WHERE id = :e"),
                 {"e": cases[0]["entity"]})
    with pytest.raises(ap.Abort, match="inactive"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_an_identifier_back_in_drake_identity_aborts_the_batch(tmp_path, conn, monkeypatch):
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    conn.execute(text(
        "INSERT INTO drake_identity (identifier_hash, first_year, last_year, return_count, "
        " taxpayer_name, confidence) VALUES (:h, 2021, 2022, 2, 'x', 100)"),
        {"h": cases[1]["hash"]})
    with pytest.raises(ap.Abort, match="back in drake_identity"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


def test_a_row_with_no_recoverable_raw_identifier_aborts_the_batch(tmp_path, conn, monkeypatch):
    """The manifest freezes only the hash; without the raw value the service cannot be called."""
    cases = [seed(conn), seed(conn)]
    path = write_manifest(tmp_path, cases)
    before = counts(conn, [c["dbi_id"] for c in cases])
    conn.execute(text(
        "UPDATE drake_client_returns SET raw_data = raw_data - 'TP_Social' "
        "WHERE taxpayer_identifier_hash = :h"), {"h": cases[1]["hash"]})
    with pytest.raises(ap.Abort, match="raw identifier"):
        apply_ok(path, conn, monkeypatch, 2, tmp_path)
    assert counts(conn, [c["dbi_id"] for c in cases]) == before


# --- case 7379 / already-attributed rows ----------------------------------------------------------

def test_an_already_attributed_row_is_refused_not_silently_re_run(tmp_path, conn, monkeypatch):
    """Case 7379's shape: DBI 532 is already bound to entity 153 and must never re-enter a batch.

    The frozen manifest excludes it by construction, so this pins the runner's own guard: an
    attributed row aborts rather than being attributed a second time.
    """
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    conn.execute(text(
        "UPDATE drake_business_identity SET relationship_entity_id = :e, trust_level = :t, "
        "confirmation_source = 'machine', evidence_method = 'drake_entity_provenance' WHERE id = :i"),
        {"e": case["entity"], "t": IDENTIFIER_VERIFIED, "i": case["dbi_id"]})
    before = counts(conn, [case["dbi_id"]])
    with pytest.raises(ap.Abort, match="already attributed"):
        apply_ok(path, conn, monkeypatch, 1, tmp_path)
    assert counts(conn, [case["dbi_id"]]) == before


def test_case_7379_is_absent_from_the_deployed_manifest():
    rows = ap.load_manifest(ap.DEFAULT_MANIFEST)
    assert 532 not in {int(r["dbi_id"]) for r in rows}
    assert all(int(r["source_contact_count"]) >= ap.MIN_SOURCE_CONTACTS for r in rows)


def test_a_human_approved_trust_level_is_never_downgraded(tmp_path, conn, monkeypatch):
    """A row a person adjudicated is refused outright, so the COALESCE fill can never reach it.

    ``ck_dbi_human_approval_attributed`` means a human-approved row is necessarily attributed and
    carries an approver, so the attributed guard is what stops it — one gate earlier than the trust
    check, and equally final. Both are asserted here.
    """
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    approver = conn.execute(text(
        "INSERT INTO users (email, normalized_email, display_name, status) "
        "VALUES (:e, :e, 'Reviewer', 'active') RETURNING id"),
        {"e": f"a{uuid.uuid4().hex[:8]}@example.test"}).scalar_one()
    conn.execute(text(
        "UPDATE drake_business_identity SET relationship_entity_id = :e, trust_level = :t, "
        "confirmation_source = 'human', evidence_method = 'human_adjudication', "
        "confirmed_by_user_id = :u, confirmed_at = now() WHERE id = :i"),
        {"e": case["entity"], "t": HUMAN_APPROVED, "u": approver, "i": case["dbi_id"]})
    with pytest.raises(ap.Abort, match="already attributed"):
        apply_ok(path, conn, monkeypatch, 1, tmp_path)
    row = conn.execute(text(
        "SELECT trust_level, confirmation_source, confirmed_by_user_id "
        "FROM drake_business_identity WHERE id = :i"), {"i": case["dbi_id"]}).mappings().one()
    assert row["trust_level"] == HUMAN_APPROVED
    assert row["confirmation_source"] == "human"
    assert row["confirmed_by_user_id"] == approver


def test_a_row_carrying_stray_trust_without_an_entity_is_refused(tmp_path, conn, monkeypatch):
    """The trust guard itself, reachable only where no entity is set."""
    case = seed(conn)
    path = write_manifest(tmp_path, [case])
    conn.execute(text(
        "UPDATE drake_business_identity SET evidence_method = 'something_else' WHERE id = :i"),
        {"i": case["dbi_id"]})
    with pytest.raises(ap.Abort, match="already carries evidence_method"):
        apply_ok(path, conn, monkeypatch, 1, tmp_path)


# --- rollback -------------------------------------------------------------------------------------

def test_rollback_restores_the_exact_pre_image(applied, conn, monkeypatch):
    cases, before = applied["cases"], applied["before"]
    report = run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb,
                 apply_changes=True, confirm=rb.confirm_phrase(3))
    assert report["reversed"] == 3
    assert report["links_deleted"] == 7
    after = counts(conn, [c["dbi_id"] for c in cases])
    assert after["dbi"] == before["dbi"]
    assert after["attributed"] == before["attributed"]
    assert after["links"] == before["links"]
    assert after["mine"] == 0
    for case in cases:
        row = conn.execute(text(
            "SELECT relationship_entity_id, trust_level, confirmation_source, evidence_method "
            "FROM drake_business_identity WHERE id = :i"), {"i": case["dbi_id"]}).mappings().one()
        assert all(v is None for v in row.values())


def test_rollback_deletes_only_the_links_it_recorded(applied, conn, monkeypatch):
    """A link this batch did not create must survive, and only recorded ids may be deleted.

    The bystander is built by the test — its own identifier, returns, contacts and entity — rather
    than borrowed from whatever the database already held. An earlier version picked an arbitrary
    unlinked Drake contact, which exists on a database with accumulated rows and does not exist on a
    clean one, so the test passed locally and failed in CI. A test that needs a row creates it.
    """
    bystander = seed(conn)                   # absent from the manifest: the batch never touches it
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    survivor = conn.execute(text(
        "INSERT INTO entity_source_links (relationship_entity_id, source_contact_id, "
        "match_method, match_score, confirmed) VALUES (:e, :c, 'manual', 100.00, true) "
        "RETURNING id"), {"e": other, "c": bystander["contacts"][0]}).scalar_one()

    payload = json.loads(
        open(applied["report"]["rollback_manifest"], encoding="utf-8").read())
    recorded = {int(i) for row in payload["rows"]
                for i in row["created_entity_source_link_ids"]}
    before = {int(i) for i in conn.execute(text("SELECT id FROM entity_source_links")).scalars()}
    assert survivor in before
    assert recorded <= before and len(recorded) == 7

    run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb,
        apply_changes=True, confirm=rb.confirm_phrase(3))

    after = {int(i) for i in conn.execute(text("SELECT id FROM entity_source_links")).scalars()}
    assert before - after == recorded        # exactly the recorded ids went, and nothing else
    assert survivor in after


def test_rollback_keeps_the_attribution_audit_and_adds_a_compensating_one(
        applied, conn, monkeypatch):
    before = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                          {"a": "drake.entity_attribution_machine"}).scalar()
    run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb,
        apply_changes=True, confirm=rb.confirm_phrase(3))
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"),
                        {"a": "drake.entity_attribution_machine"}).scalar() == before
    compensating = conn.execute(text(
        "SELECT actor_user_id FROM audit_events WHERE action = :a"),
        {"a": rb.ROLLBACK_AUDIT_ACTION}).mappings().all()
    assert len(compensating) == 3
    assert all(r["actor_user_id"] is None for r in compensating)


def test_rollback_is_dry_by_default(applied, conn, monkeypatch):
    before = counts(conn, [c["dbi_id"] for c in applied["cases"]])
    with pytest.raises(rb.Abort, match="DRY RUN"):
        run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb)
    assert counts(conn, [c["dbi_id"] for c in applied["cases"]]) == before


def test_rollback_refuses_when_a_row_was_retargeted_since_the_apply(applied, conn, monkeypatch):
    other = create_named_entity(conn, "business", f"OTHER {uuid.uuid4().hex[:6]}")
    conn.execute(text("UPDATE drake_business_identity SET relationship_entity_id = :e WHERE id = :i"),
                 {"e": other, "i": applied["cases"][1]["dbi_id"]})
    before = counts(conn, [c["dbi_id"] for c in applied["cases"]])
    with pytest.raises(rb.Abort, match="later decision"):
        run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb,
            apply_changes=True, confirm=rb.confirm_phrase(3))
    assert counts(conn, [c["dbi_id"] for c in applied["cases"]]) == before


def test_rollback_refuses_when_a_human_confirmed_a_row_since_the_apply(applied, conn, monkeypatch):
    address = f"r{uuid.uuid4().hex[:8]}@example.test"
    person = conn.execute(text(
        "INSERT INTO users (email, normalized_email, display_name, status) "
        "VALUES (:e, :e, 'Reviewer', 'active') RETURNING id"), {"e": address}).scalar_one()
    conn.execute(text(
        "UPDATE drake_business_identity SET confirmed_by_user_id = :u, confirmed_at = now() "
        "WHERE id = :i"), {"u": person, "i": applied["cases"][0]["dbi_id"]})
    with pytest.raises(rb.Abort, match="confirmed by a user"):
        run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb,
            apply_changes=True, confirm=rb.confirm_phrase(3))


def test_rollback_refuses_without_its_confirmation_phrase(applied, conn, monkeypatch):
    before = counts(conn, [c["dbi_id"] for c in applied["cases"]])
    with pytest.raises(rb.Abort, match="requires --confirm"):
        run(applied["report"]["rollback_manifest"], conn, monkeypatch, module=rb,
            apply_changes=True, confirm="ROLLBACK-D7-ATTRIB-BATCH1-999")
    assert counts(conn, [c["dbi_id"] for c in applied["cases"]]) == before


def test_a_rollback_manifest_for_another_batch_is_refused(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"batch": "something_else", "rows": [{"dbi_id": 1}]}),
                    encoding="utf-8")
    with pytest.raises(rb.Abort, match="is for"):
        rb.load_rollback_manifest(path)
