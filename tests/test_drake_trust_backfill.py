"""Drake link-trust backfill — what may be recorded, and what must stay unrecorded.

Phase 1 back-filled nothing: all 11,124 links carry `trust_level = NULL`. This backfill decides which
of them the stored evidence can justify recording. Measured against production, that is 533 of 3,607
Drake links, and ZERO human approvals — `drake_identity_match_candidates` holds 435 rows, all still
`pending`, none carrying a reviewer or a timestamp.

Every test here asserts either that evidence is genuinely required, or that a write is refused.

Temp/test rows only, all tagged and torn down.
"""
import contextlib
import csv
import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, event, insert, select, text

from app.db import engine, metadata, people, person_source_links, source_contacts
from app.services.drake_trust_backfill import (
    INELIGIBLE_ALREADY_RECORDED,
    INELIGIBLE_HASH_NOT_ON_RETURN,
    INELIGIBLE_NO_IDENTIFIER_HASH,
    INELIGIBLE_WEAK_EVIDENCE,
    apply_planned_row,
    build_plan,
    classify_link,
    current_state,
)
from app.services.link_trust import (
    HUMAN_APPROVED,
    IDENTIFIER_VERIFIED,
    SOURCE_HUMAN,
    SOURCE_MACHINE,
)

drake_client_returns = metadata.tables["drake_client_returns"]
drake_candidates = metadata.tables["drake_identity_match_candidates"]
users = metadata.tables["users"]


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


# ==================================================================================================
# classify_link — pure, given its two evidence indexes
# ==================================================================================================

def _row(**kw):
    row = {"link_id": 1, "person_id": 10, "source_contact_id": 100,
           "match_method": "drake_identity_promotion", "confirmed": True,
           "trust_level": None, "identifier_hash": _hash("h")}
    row.update(kw)
    return row


def test_identifier_keyed_method_with_hash_on_a_return_is_eligible():
    v = classify_link(_row(), return_hash_sides={_hash("h"): {"taxpayer"}}, approvals={})
    assert v["eligible"] and v["proposed_trust_level"] == IDENTIFIER_VERIFIED
    assert v["proposed_confirmation_source"] == SOURCE_MACHINE


def test_identifier_keyed_method_whose_hash_is_on_no_return_is_refused():
    """The method claims the identifier; nothing corroborates it. Refuse rather than record."""
    v = classify_link(_row(), return_hash_sides={}, approvals={})
    assert not v["eligible"] and v["reason"] == INELIGIBLE_HASH_NOT_ON_RETURN


def test_spouse_side_hash_is_valid_identifier_evidence():
    """A spouse is bound to a joint return through their OWN identifier."""
    v = classify_link(_row(), return_hash_sides={_hash("h"): {"spouse"}}, approvals={})
    assert v["eligible"] and v["proposed_trust_level"] == IDENTIFIER_VERIFIED


def test_contact_without_an_identifier_hash_is_refused():
    v = classify_link(_row(identifier_hash=None), return_hash_sides={}, approvals={})
    assert not v["eligible"] and v["reason"] == INELIGIBLE_NO_IDENTIFIER_HASH


@pytest.mark.parametrize("method", [
    "unique_exact_name", "exact_name_city_state", "exact_email", "exact_phone",
    "exact_email+exact_phone", "auto_promote", "canonical_repair_exact_person_provenance",
    "manual_drake_household_provenance", None, "",
])
def test_weak_or_unrecorded_evidence_is_never_recorded(method):
    """1,404 name matches, 894 contact matches and 543 canonical repairs all land here."""
    v = classify_link(_row(match_method=method),
                      return_hash_sides={_hash("h"): {"taxpayer"}}, approvals={})
    assert not v["eligible"] and v["reason"] == INELIGIBLE_WEAK_EVIDENCE


def test_an_already_recorded_link_is_never_re_planned():
    """Backfill only moves NULL -> value, so a human correction cannot be restated by a re-run."""
    v = classify_link(_row(trust_level=HUMAN_APPROVED),
                      return_hash_sides={_hash("h"): {"taxpayer"}}, approvals={})
    assert not v["eligible"] and v["reason"] == INELIGIBLE_ALREADY_RECORDED


def test_a_proven_approval_outranks_a_weak_method():
    approvals = {(_hash("h"), 10): {"reviewed_by_user_id": 7,
                                    "reviewed_at": datetime(2026, 1, 1, tzinfo=UTC)}}
    v = classify_link(_row(match_method="unique_exact_name"), return_hash_sides={},
                      approvals=approvals)
    assert v["eligible"] and v["proposed_trust_level"] == HUMAN_APPROVED
    assert v["proposed_confirmation_source"] == SOURCE_HUMAN
    assert v["confirmed_by_user_id"] == 7 and v["confirmed_at"] is not None


def test_confirmed_true_alone_never_makes_a_link_eligible():
    """The whole point: `confirmed` is hardcoded True by automated code and proves nothing."""
    v = classify_link(_row(match_method="unique_exact_name", confirmed=True),
                      return_hash_sides={_hash("h"): {"taxpayer"}}, approvals={})
    assert not v["eligible"]


# ==================================================================================================
# Database-backed: plan, apply, refuse, roll back
# ==================================================================================================

@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    year = 2100 + (int(tag, 16) % 800)
    w = {"tag": tag, "year": year, "person": {}, "contact": [], "ret": [], "link": {},
         "candidate": [], "user": None}

    cases = {
        "identifier": "drake_identity_promotion",       # eligible
        "identifier_no_return": "drake_identity_promotion",  # hash on no return -> refused
        "exact_name": "unique_exact_name",              # refused
        "contact": "exact_email+exact_phone",           # refused
        "repair": "canonical_repair_exact_person_provenance",   # refused
        "approved": "unique_exact_name",                # weak method, real approval -> eligible
        "already": "drake_identity_promotion",          # already recorded -> refused
    }

    with engine.begin() as c:
        # Reuse an existing user rather than creating one. A committed apply writes an audit_events
        # row naming the actor, and audit_events is append-only (enforced by a trigger), so the FK's
        # ON DELETE SET NULL cannot fire and a freshly-created user becomes undeletable in teardown.
        w["user"] = c.execute(select(users.c.id).order_by(users.c.id).limit(1)).scalar()
        w["created_user"] = w["user"] is None
        if w["created_user"]:
            w["user"] = c.execute(insert(users).values(
                email=f"rev-{tag}@example.test", normalized_email=f"rev-{tag}@example.test",
                display_name=f"Reviewer {tag}").returning(users.c.id)).scalar_one()

        for key, method in cases.items():
            pid = c.execute(insert(people).values(
                first_name=f"P{key}{tag}", last_name=f"L{tag}", full_name=f"P{key}{tag} L{tag}",
                active=True).returning(people.c.id)).scalar_one()
            w["person"][key] = pid

            identifier = _hash(tag + key)
            sid = c.execute(insert(source_contacts).values(
                source_system="Drake", source_file=f"TRUSTBF {tag}",
                source_hash=uuid.uuid4().hex, source_record_id=uuid.uuid4().hex,
                raw_data={"identifier_hash": identifier, "role": "taxpayer"},
            ).returning(source_contacts.c.id)).scalar_one()
            w["contact"].append(sid)

            values = {"person_id": pid, "source_contact_id": sid, "match_method": method,
                      "match_score": 100, "confirmed": True}
            if key == "already":
                values |= {"trust_level": HUMAN_APPROVED, "confirmation_source": SOURCE_HUMAN,
                           "confirmed_by_user_id": w["user"], "confirmed_at": datetime.now(UTC)}
            w["link"][key] = c.execute(insert(person_source_links).values(**values)
                                       .returning(person_source_links.c.id)).scalar_one()

            # Every case except `identifier_no_return` gets a Drake return carrying its hash.
            if key != "identifier_no_return":
                rid = c.execute(insert(drake_client_returns).values(
                    tax_year=year, source_row_number=len(w["ret"]) + 1,
                    taxpayer_identifier_hash=identifier, return_type="1040", filing_status="1",
                    source_updated_at=datetime.now(UTC), raw_data={},
                ).returning(drake_client_returns.c.id)).scalar_one()
                w["ret"].append(rid)

        # A real, attributed approval for the `approved` case.
        w["candidate"].append(c.execute(insert(drake_candidates).values(
            identifier_hash=_hash(tag + "approved"), person_id=w["person"]["approved"],
            score=100, reasons=["test"], rank=1, status="approved",
            reviewed_by_user_id=w["user"], reviewed_at=datetime.now(UTC),
        ).returning(drake_candidates.c.id)).scalar_one())

    yield w

    with engine.begin() as c:
        c.execute(delete(drake_candidates).where(drake_candidates.c.id.in_(w["candidate"])))
        c.execute(delete(drake_client_returns).where(drake_client_returns.c.id.in_(w["ret"])))
        c.execute(delete(person_source_links).where(
            person_source_links.c.source_contact_id.in_(w["contact"])))
        c.execute(delete(source_contacts).where(source_contacts.c.id.in_(w["contact"])))
        c.execute(delete(people).where(people.c.id.in_(list(w["person"].values()))))
        if w.get("created_user"):
            c.execute(delete(users).where(users.c.id == w["user"]))


def _plan_for(world_):
    with engine.connect() as c:
        plan = build_plan(c)
    ours = {e["person_source_link_id"] for e in plan["planned"]} & set(world_["link"].values())
    refused = {e["person_source_link_id"]: e["reason"] for e in plan["refused"]}
    return plan, ours, refused


def test_only_provable_links_are_planned(world):
    _plan, planned_ids, refused = _plan_for(world)
    assert planned_ids == {world["link"]["identifier"], world["link"]["approved"]}
    assert refused[world["link"]["exact_name"]] == INELIGIBLE_WEAK_EVIDENCE
    assert refused[world["link"]["contact"]] == INELIGIBLE_WEAK_EVIDENCE
    assert refused[world["link"]["repair"]] == INELIGIBLE_WEAK_EVIDENCE
    assert refused[world["link"]["already"]] == INELIGIBLE_ALREADY_RECORDED
    assert refused[world["link"]["identifier_no_return"]] == INELIGIBLE_HASH_NOT_ON_RETURN


def test_the_plan_reports_refusals_not_just_writes(world):
    plan, _, _ = _plan_for(world)
    assert plan["refused_rows"] > 0
    assert sum(plan["refusal_census"].values()) == plan["refused_rows"]


def test_applying_writes_only_the_planned_columns(world):
    plan, _, _ = _plan_for(world)
    entry = next(e for e in plan["planned"]
                 if e["person_source_link_id"] == world["link"]["identifier"])
    with engine.begin() as c:
        assert apply_planned_row(c, entry) is True
    try:
        with engine.connect() as c:
            row = c.execute(select(person_source_links).where(
                person_source_links.c.id == world["link"]["identifier"])).mappings().one()
        assert row["trust_level"] == IDENTIFIER_VERIFIED
        assert row["confirmation_source"] == SOURCE_MACHINE
        assert row["evidence_method"] == "drake_identity_promotion"
        # Untouched:
        assert row["confirmed"] is True
        assert row["match_method"] == "drake_identity_promotion"
        assert row["person_id"] == world["person"]["identifier"]
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE person_source_links SET trust_level=NULL, "
                           "confirmation_source=NULL, evidence_method=NULL WHERE id=:i"),
                      {"i": world["link"]["identifier"]})


def test_apply_is_idempotent_and_cannot_restate_a_recorded_row(world):
    plan, _, _ = _plan_for(world)
    entry = next(e for e in plan["planned"]
                 if e["person_source_link_id"] == world["link"]["identifier"])
    try:
        with engine.begin() as c:
            assert apply_planned_row(c, entry) is True
        with engine.begin() as c:
            assert apply_planned_row(c, entry) is False, "a recorded row must not be rewritten"
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE person_source_links SET trust_level=NULL, "
                           "confirmation_source=NULL, evidence_method=NULL WHERE id=:i"),
                      {"i": world["link"]["identifier"]})


def test_a_human_approval_is_written_with_its_reviewer_and_timestamp(world):
    plan, _, _ = _plan_for(world)
    entry = next(e for e in plan["planned"]
                 if e["person_source_link_id"] == world["link"]["approved"])
    assert entry["trust_level"] == HUMAN_APPROVED
    with engine.begin() as c:
        assert apply_planned_row(c, entry) is True
    try:
        with engine.connect() as c:
            row = c.execute(select(person_source_links).where(
                person_source_links.c.id == world["link"]["approved"])).mappings().one()
        # The psl02 check constraint refuses an anonymous human_approved; this proves it was fed.
        assert row["confirmed_by_user_id"] == world["user"]
        assert row["confirmed_at"] is not None
    finally:
        with engine.begin() as c:
            c.execute(text("UPDATE person_source_links SET trust_level=NULL, "
                           "confirmation_source=NULL, evidence_method=NULL, "
                           "confirmed_by_user_id=NULL, confirmed_at=NULL WHERE id=:i"),
                      {"i": world["link"]["approved"]})


def test_writing_a_trust_level_outside_the_allowed_set_is_refused(world):
    bad = {"person_source_link_id": world["link"]["exact_name"],
           "trust_level": "machine_exact_name", "confirmation_source": SOURCE_MACHINE,
           "evidence_method": "unique_exact_name", "confirmed_by_user_id": None,
           "confirmed_at": None}
    with engine.begin() as c, pytest.raises(ValueError, match="refusing to write"):
        apply_planned_row(c, bad)


def test_current_state_captures_what_rollback_needs(world):
    with engine.connect() as c:
        state = current_state(c, [world["link"]["identifier"], world["link"]["already"]])
    assert state[world["link"]["identifier"]]["trust_level"] is None
    assert state[world["link"]["already"]]["trust_level"] == HUMAN_APPROVED


# ==================================================================================================
# CLI: preview writes a plan + digest; apply refuses a plan that does not match its approval
# ==================================================================================================

def test_preview_writes_a_plan_and_a_digest(world, tmp_path):
    from scripts.apply_drake_trust_backfill import run
    result = run(preview=True, out_dir=tmp_path, emit=lambda *_: None)
    plan_path = tmp_path / "drake_trust_backfill_plan.csv"
    assert plan_path.is_file()
    assert result["plan_sha256"] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    with plan_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == result["planned_rows"]
    assert {r["trust_level"] for r in rows} <= {IDENTIFIER_VERIFIED, HUMAN_APPROVED}
    assert (tmp_path / "drake_trust_backfill_refusals.csv").is_file()


def test_apply_refuses_a_plan_whose_digest_does_not_match(world, tmp_path):
    from scripts.apply_drake_trust_backfill import run
    run(preview=True, out_dir=tmp_path, emit=lambda *_: None)
    with pytest.raises(SystemExit, match="SHA256"):
        run(plan_path=tmp_path / "drake_trust_backfill_plan.csv",
            expect_sha="0" * 64, expect_rows=1,
            expect_census={IDENTIFIER_VERIFIED: 1, HUMAN_APPROVED: 0},
            apply_changes=False, confirm="x", batch_id="t", actor_user_id=1,
            emit=lambda *_: None)


def test_apply_refuses_when_the_row_count_does_not_match_the_approval(world, tmp_path):
    from scripts.apply_drake_trust_backfill import run
    result = run(preview=True, out_dir=tmp_path, emit=lambda *_: None)
    with pytest.raises(SystemExit, match="--expect-rows"):
        run(plan_path=result["plan_path"], expect_sha=result["plan_sha256"],
            expect_rows=result["planned_rows"] + 1,
            expect_census={IDENTIFIER_VERIFIED: 0, HUMAN_APPROVED: 0},
            apply_changes=False, confirm="x", batch_id="t", actor_user_id=1,
            emit=lambda *_: None)


def test_apply_requires_the_exact_confirm_phrase(world, tmp_path):
    from scripts.apply_drake_trust_backfill import run
    result = run(preview=True, out_dir=tmp_path, emit=lambda *_: None)
    census = {IDENTIFIER_VERIFIED: sum(1 for e in result["planned"]
                                       if e["trust_level"] == IDENTIFIER_VERIFIED),
              HUMAN_APPROVED: sum(1 for e in result["planned"]
                                  if e["trust_level"] == HUMAN_APPROVED)}
    with pytest.raises(SystemExit, match="--confirm"):
        run(plan_path=result["plan_path"], expect_sha=result["plan_sha256"],
            expect_rows=result["planned_rows"], expect_census=census,
            apply_changes=True, confirm="WRONG", batch_id="t", actor_user_id=1,
            emit=lambda *_: None)


# --- the full round trip: preview -> dry run -> apply -> rollback -----------------------------------

def _approved_plan(tmp_path):
    from scripts.apply_drake_trust_backfill import confirm_phrase, run
    result = run(preview=True, out_dir=tmp_path, emit=lambda *_: None)
    census = {IDENTIFIER_VERIFIED: sum(1 for e in result["planned"]
                                       if e["trust_level"] == IDENTIFIER_VERIFIED),
              HUMAN_APPROVED: sum(1 for e in result["planned"]
                                  if e["trust_level"] == HUMAN_APPROVED)}
    return result, census, confirm_phrase("roundtrip", result["planned_rows"])


def _trust_of(link_ids):
    with engine.connect() as c:
        return {r["id"]: r["trust_level"] for r in c.execute(text(
            "SELECT id, trust_level FROM person_source_links WHERE id = ANY(:ids)"),
            {"ids": list(link_ids)}).mappings()}


@contextlib.contextmanager
def _captured_sql():
    """Every statement the engine sends to Postgres while the block runs.

    Proving "no UPDATE" by inspecting the database afterwards cannot distinguish "never wrote" from
    "wrote and rolled back" — and that distinction IS the fix under test. Capturing the statements
    proves the stronger claim: the UPDATE was never sent, so no row lock was ever taken.
    """
    seen = []

    def listen(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", listen)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", listen)


def _writes_in(statements):
    return [s for s in statements
            if re.match(r"\s*(update|insert|delete)\b", s, re.IGNORECASE)]


def _audit_count():
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM audit_events")).scalar()


def _dry_run(world_, result, census, phrase, tmp_path):
    from scripts.apply_drake_trust_backfill import run
    return run(plan_path=result["plan_path"], expect_sha=result["plan_sha256"],
               expect_rows=result["planned_rows"], expect_census=census,
               apply_changes=False, confirm=phrase, batch_id="roundtrip",
               actor_user_id=world_["user"], snapshot_root=tmp_path / "dr",
               emit=lambda *_: None)


# --- A/B/C/D: the dry-run safety contract ----------------------------------------------------------

def test_dry_run_sends_no_update_insert_or_delete(world, tmp_path):
    """A. Not 'rolled back' — never sent. No write lock is taken by an attempted UPDATE."""
    result, census, phrase = _approved_plan(tmp_path)
    with _captured_sql() as statements:
        report = _dry_run(world, result, census, phrase, tmp_path)
    assert _writes_in(statements) == []
    assert report["committed"] is False
    assert report["written"] == 0
    assert report["would_write"] == result["planned_rows"]


def test_dry_run_creates_no_rollback_snapshot(world, tmp_path):
    """B. A snapshot implies an apply happened. A dry run must not leave one."""
    result, census, phrase = _approved_plan(tmp_path)
    _dry_run(world, result, census, phrase, tmp_path)
    assert not (tmp_path / "dr").exists(), "dry run left a rollback snapshot on disk"


def test_dry_run_creates_no_audit_event(world, tmp_path):
    """C."""
    result, census, phrase = _approved_plan(tmp_path)
    before = _audit_count()
    _dry_run(world, result, census, phrase, tmp_path)
    assert _audit_count() == before


def test_dry_run_leaves_every_trust_column_unchanged(world, tmp_path):
    """D."""
    result, census, phrase = _approved_plan(tmp_path)
    ids = list(world["link"].values())
    with engine.connect() as c:
        before = {r["id"]: dict(r) for r in c.execute(text(
            "SELECT id, trust_level, confirmation_source, evidence_method, "
            "confirmed_by_user_id, confirmed_at FROM person_source_links WHERE id = ANY(:ids)"),
            {"ids": ids}).mappings()}
    _dry_run(world, result, census, phrase, tmp_path)
    with engine.connect() as c:
        after = {r["id"]: dict(r) for r in c.execute(text(
            "SELECT id, trust_level, confirmation_source, evidence_method, "
            "confirmed_by_user_id, confirmed_at FROM person_source_links WHERE id = ANY(:ids)"),
            {"ids": ids}).mappings()}
    assert after == before


def test_dry_run_still_detects_drift(world, tmp_path):
    """A dry run that writes nothing must still be a real check, not a rubber stamp."""
    from scripts.apply_drake_trust_backfill import run
    result, census, phrase = _approved_plan(tmp_path)
    with engine.begin() as c:
        c.execute(delete(drake_client_returns).where(drake_client_returns.c.id.in_(world["ret"])))
    try:
        with pytest.raises(SystemExit, match="no longer match the live evidence"):
            run(plan_path=result["plan_path"], expect_sha=result["plan_sha256"],
                expect_rows=result["planned_rows"], expect_census=census,
                apply_changes=False, confirm=phrase, batch_id="roundtrip",
                actor_user_id=world["user"], snapshot_root=tmp_path / "dr",
                emit=lambda *_: None)
    finally:
        world["ret"].clear()


# --- E: preview is read-only -----------------------------------------------------------------------

def test_preview_sends_no_write_statements(world, tmp_path):
    """E."""
    from scripts.apply_drake_trust_backfill import run
    audit_before = _audit_count()
    with _captured_sql() as statements:
        run(preview=True, out_dir=tmp_path, emit=lambda *_: None)
    assert _writes_in(statements) == []
    assert _audit_count() == audit_before


def test_apply_then_rollback_restores_every_row(world, tmp_path):
    from scripts.apply_drake_trust_backfill import run as apply_run
    from scripts.rollback_drake_trust_backfill import confirm_phrase as rb_phrase
    from scripts.rollback_drake_trust_backfill import run as rollback_run

    result, census, phrase = _approved_plan(tmp_path)
    before = _trust_of(world["link"].values())
    ours = {world["link"]["identifier"], world["link"]["approved"]}

    report = apply_run(plan_path=result["plan_path"], expect_sha=result["plan_sha256"],
                       expect_rows=result["planned_rows"], expect_census=census,
                       apply_changes=True, confirm=phrase, batch_id="roundtrip",
                       actor_user_id=world["user"], snapshot_root=tmp_path / "dr",
                       emit=lambda *_: None)
    try:
        assert report["committed"] is True
        # G: an ACTUAL apply does leave a rollback snapshot, and it hashes to what it recorded.
        snapshot = Path(report["snapshot"])
        assert snapshot.is_file()
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == report["snapshot_sha256"]
        after = _trust_of(world["link"].values())
        assert after[world["link"]["identifier"]] == IDENTIFIER_VERIFIED
        assert after[world["link"]["approved"]] == HUMAN_APPROVED
        # Everything ineligible is untouched.
        for key in ("exact_name", "contact", "repair", "identifier_no_return"):
            assert after[world["link"][key]] is None
        assert after[world["link"]["already"]] == before[world["link"]["already"]]
    finally:
        rollback_run(report["snapshot"], apply_changes=True,
                     confirm=rb_phrase("roundtrip", report["written"] + report["skipped"]),
                     actor_user_id=world["user"], emit=lambda *_: None)

    restored = _trust_of(world["link"].values())
    assert {k: restored[k] for k in ours} == {k: before[k] for k in ours}


def test_apply_aborts_when_the_evidence_drifted_since_the_plan(world, tmp_path):
    """A plan is a snapshot of evidence. If the evidence moved, the plan must not write."""
    from scripts.apply_drake_trust_backfill import run
    result, census, phrase = _approved_plan(tmp_path)
    before = _trust_of(world["link"].values())

    # Withdraw the Drake return that corroborated the identifier link, after the plan was approved.
    with engine.begin() as c:
        c.execute(delete(drake_client_returns).where(
            drake_client_returns.c.id.in_(world["ret"])))
    try:
        with _captured_sql() as statements, \
                pytest.raises(SystemExit, match="no longer match the live evidence"):
            run(plan_path=result["plan_path"], expect_sha=result["plan_sha256"],
                expect_rows=result["planned_rows"], expect_census=census,
                apply_changes=True, confirm=phrase, batch_id="roundtrip",
                actor_user_id=world["user"], snapshot_root=tmp_path / "dr", emit=lambda *_: None)
        assert _trust_of(world["link"].values()) == before, "an aborted apply must write nothing"
        # H: the abort happens in the read-only validation pass, so nothing was even attempted.
        assert _writes_in(statements) == []
        assert not (tmp_path / "dr").exists(), "an aborted apply left a rollback snapshot"
    finally:
        world["ret"].clear()
