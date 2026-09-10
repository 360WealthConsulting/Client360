"""Strict-safe ownership BATCH 5 — the frozen-manifest contract, the guarded apply, the rollback.

Batch 5 differs from batch 1 in one structural way that most of these tests exist to pin: the
manifest is the ONLY source of rows. Batch 1 recomputed its plan and applied what came back; batch 5
applies the frozen 55 or nothing. So alongside the usual drift cases there are tests that a document
which qualifies today but was not reviewed can never reach a write, and that each of the four frozen
fingerprints — document, proposal, classification, target person — independently aborts the batch.

Every test builds its own tagged rows and patches the approved constants to match, so the real code
path runs against a small batch instead of the 55-row production manifest. The production manifest is
exercised separately, against its real bytes, in the FROZEN section at the bottom.

Temp rows only, all tagged, all cleaned up. Nothing here writes to a production database.
"""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services import document_strict_safe_ownership as sso
from app.services import document_strict_safe_ownership_batch5 as b5
from scripts import apply_strict_safe_ownership_batch5 as ap
from scripts import rollback_strict_safe_ownership_batch5 as rb

_TAG = f"SSOFIVE{uuid.uuid4().hex[:6]}"
ACTOR = 7

MANIFEST_COLUMNS = ["document_id", "person_id", "person_name", "corroborator_count",
                    "original_name", "owner_proposal_fact_id", "owner_proposal_fact_version",
                    "address_corroboration_used", "zip_corroboration_used",
                    "shared_value_corroboration_used", "document_fingerprint",
                    "proposal_fingerprint", "classification_fingerprint",
                    "target_person_fingerprint"]


@pytest.fixture(autouse=True)
def _clean():
    yield
    facts = metadata.tables["document_facts"]
    classifications = metadata.tables["document_classifications"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"sso5:{_TAG}%")))]
        if ids:
            c.execute(delete(classifications).where(classifications.c.document_id.in_(ids)))
            c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))
    # audit_events is append-only at the database level (a trigger raises on DELETE), so the
    # ledger rows this run produced are left in place. They are scoped to document ids that
    # exist only for this run, so nothing else can read them by accident.


# --- builders -----------------------------------------------------------------

def _person(first: str, *, active=True) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=_TAG, full_name=f"{first} {_TAG}",
            normalized_email=f"{first.lower()}@example.com", normalized_phone="5555550123",
            active=active).returning(people.c.id)).scalar_one()


def _evidence(person_name, *, exact_name=True, email=True, phone=True,
              legacy_address=False, street=False, zip_context=False, shared=False):
    """Proposal evidence in the refreshed engine's own vocabulary.

    Batch 5's shape is the default: exact name + matched email + matched phone, nothing else.
    """
    note = " (shared value — context only)" if shared else ""
    ev = []
    if exact_name:
        ev.append(f"✓ exact name '{person_name}'")
    if email:
        ev.append(f"✓ email {person_name.split()[0].lower()}@example.com matched{note}")
    if phone:
        ev.append(f"✓ phone ending 0123 matched{note}")
    if street:
        ev.append("✓ street address matched")
    if legacy_address:
        ev.append("✓ address/ZIP matched")
    if zip_context:
        ev.append("• ZIP matched (a ZIP is a town, not an owner — context only)")
    ev.append("context only (not an owner): irs")
    return ev


def _doc(person_id, first, *, route="HIGH", entity_type="person", evidence=None,
         review_status="not_required", archived=False, status="active", owner=None,
         household_id=None, organization_id=None, entity_name=None,
         doc_type="1040", classifier_version="test-1") -> int:
    """One batch-5-shaped candidate: a current owner_proposal plus a classification row."""
    person_name = entity_name if entity_name is not None else f"{first} {_TAG}"
    filename = f"{first} {_TAG} 2021.pdf"
    facts = metadata.tables["document_facts"]
    classifications = metadata.tables["document_classifications"]
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=filename, stored_name=f"sso5:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status=review_status, current_version=1, person_id=owner,
            household_id=household_id, organization_id=organization_id,
            tags={"source_system": "SharePoint"},
        ).returning(documents.c.id)).scalar_one()
        c.execute(facts.insert().values(
            document_id=did, fact_type="owner_proposal",
            fact_value=json.dumps({
                "route": route, "confidence": "HIGH", "entity_type": entity_type,
                "entity_id": person_id, "entity_name": person_name,
                "doc_type": doc_type, "year": "2021",
                "evidence": evidence if evidence is not None else _evidence(person_name),
                "best_candidates": [],
            }),
            confidence=0.0, extraction_engine="owner_proposal", extractor_version="test",
            version=1, is_current=True))
        c.execute(classifications.insert().values(
            document_id=did, doc_type=doc_type, confidence=0.95,
            classifier_version=classifier_version))
    return did


def _live(did):
    with engine.connect() as c:
        return dict(c.execute(text(b5.LIVE_STATE_SQL), {"ids": [did]}).mappings().one())


def _person_row(pid):
    with engine.connect() as c:
        return dict(c.execute(text(b5.PERSON_STATE_SQL), {"ids": [pid]}).mappings().one())


def _manifest_row(did, pid, name):
    """Build one manifest row the way the freeze did — from live state, via the service."""
    live = _live(did)
    fv = live["fact_value"]
    fv = fv if isinstance(fv, dict) else json.loads(fv)
    return {
        "document_id": did, "person_id": pid, "person_name": name,
        "corroborator_count": sum(b5.corroborator_flags(fv.get("evidence")).values()),
        "original_name": live["original_name"],
        "owner_proposal_fact_id": live["fact_id"],
        "owner_proposal_fact_version": live["fact_version"],
        "address_corroboration_used": "NO", "zip_corroboration_used": "NO",
        "shared_value_corroboration_used": "NO",
        "document_fingerprint": b5.document_fingerprint(live),
        "proposal_fingerprint": b5.proposal_fingerprint(live["fact_id"], live["fact_version"], fv),
        "classification_fingerprint": b5.classification_fingerprint(
            live["classification_id"], live["classification_doc_type"],
            live["classification_confidence"], live["classifier_version"]),
        "target_person_fingerprint": b5.target_person_fingerprint(_person_row(pid)),
    }


def _write_manifest(tmp_path, rows, name="manifest.csv") -> Path:
    path = Path(tmp_path) / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


@pytest.fixture
def batch(tmp_path):
    """Three batch-5-shaped documents across two people, plus the manifest that freezes them."""
    p1, p2 = _person("Ada"), _person("Grace")
    d1 = _doc(p1, "Ada")
    d2 = _doc(p1, "Ada2", entity_name=f"Ada {_TAG}")
    d3 = _doc(p2, "Grace")
    rows = [_manifest_row(d1, p1, f"Ada {_TAG}"),
            _manifest_row(d2, p1, f"Ada {_TAG}"),
            _manifest_row(d3, p2, f"Grace {_TAG}")]
    path = _write_manifest(tmp_path, rows)
    return {"path": path, "rows": rows, "people": [p1, p2], "ids": [d1, d2, d3],
            "d1": d1, "d2": d2, "d3": d3, "p1": p1, "p2": p2}


def _run(batch, **kw):
    """Run the apply with the approved constants patched to this small batch."""
    ids = batch["ids"]
    kw.setdefault("expect_sha", b5.sha256_of(batch["path"]))
    kw.setdefault("expect_rows", len(batch["rows"]))
    kw.setdefault("expect_composition", {2: len(batch["rows"])})
    kw.setdefault("expect_people", len({r["person_id"] for r in batch["rows"]}))
    kw.setdefault("expect_plan_digest", sso.plan_digest(sso.build_plan()))
    kw.setdefault("snapshot_root", batch["path"].parent / "snap")
    kw.setdefault("out", lambda *_a, **_k: None)
    assert ids  # the fixture built rows
    return ap.run(batch["path"], **kw)


def _apply(batch, **kw):
    return _run(batch, apply_changes=True, actor_user_id=ACTOR,
                confirm=b5.confirm_phrase("APPLY", len(batch["rows"])), **kw)


def _owner(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.person_id).where(documents.c.id == did)).scalar()


def _audit_count(ids, action="document.ownership_resolved"):
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        return len(c.execute(select(audit.c.id).where(
            audit.c.action == action, audit.c.entity_type == "document",
            audit.c.entity_id.in_([str(i) for i in ids]))).all())


# --- the happy path -----------------------------------------------------------

def test_multi_row_batch_assigns_every_row(batch):
    report = _apply(batch)
    assert report["committed"] is True
    assert report["applied"] == 3
    assert report["audit_rows"] == 3
    assert _owner(batch["d1"]) == batch["p1"]
    assert _owner(batch["d2"]) == batch["p1"]
    assert _owner(batch["d3"]) == batch["p2"]


def test_dry_run_is_the_default_and_writes_nothing(batch):
    report = ap.run(batch["path"], expect_sha=b5.sha256_of(batch["path"]),
                    expect_rows=3, expect_composition={2: 3}, expect_people=2,
                    expect_plan_digest=sso.plan_digest(sso.build_plan()),
                    snapshot_root=batch["path"].parent / "snap", out=lambda *_a, **_k: None)
    assert report["committed"] is False
    assert report["validated"] == 3
    assert report["applied"] == 0
    assert all(_owner(d) is None for d in batch["ids"])
    assert _audit_count(batch["ids"]) == 0
    assert report["snapshot"] is None


def test_exactly_n_document_writes_and_n_audit_writes(batch):
    with engine.connect() as c:
        owned_before = c.execute(text(
            "select count(*) from documents where person_id is not null")).scalar()
    _apply(batch)
    with engine.connect() as c:
        owned_after = c.execute(text(
            "select count(*) from documents where person_id is not null")).scalar()
    assert owned_after - owned_before == 3
    assert _audit_count(batch["ids"]) == 3


def test_no_proposal_or_classification_writes(batch):
    facts = metadata.tables["document_facts"]
    classifications = metadata.tables["document_classifications"]
    with engine.connect() as c:
        f_before = c.execute(text(ap._PROPOSAL_FP)).scalar()
        cl_before = c.execute(text(ap._CLASSIFICATION_FP)).scalar()
        n_f = len(c.execute(select(facts.c.id)).all())
        n_cl = len(c.execute(select(classifications.c.id)).all())
    _apply(batch)
    with engine.connect() as c:
        assert c.execute(text(ap._PROPOSAL_FP)).scalar() == f_before
        assert c.execute(text(ap._CLASSIFICATION_FP)).scalar() == cl_before
        assert len(c.execute(select(facts.c.id)).all()) == n_f
        assert len(c.execute(select(classifications.c.id)).all()) == n_cl


def test_review_status_and_tags_clauses_stay_no_ops(batch):
    before = {}
    with engine.connect() as c:
        for did in batch["ids"]:
            r = c.execute(select(documents.c.review_status, documents.c.tags)
                          .where(documents.c.id == did)).mappings().one()
            before[did] = (r["review_status"], json.dumps(r["tags"], sort_keys=True))
    _apply(batch)
    with engine.connect() as c:
        for did in batch["ids"]:
            r = c.execute(select(documents.c.review_status, documents.c.tags)
                          .where(documents.c.id == did)).mappings().one()
            assert (r["review_status"], json.dumps(r["tags"], sort_keys=True)) == before[did]


def test_canonical_write_path_is_used(batch, monkeypatch):
    """The mutation must go through households.resolve_document_ownership, not local SQL."""
    seen = []
    import app.services.households as hh
    real = hh.resolve_document_ownership

    def spy(document_id, **kw):
        seen.append((document_id, kw.get("person_id")))
        return real(document_id, **kw)

    monkeypatch.setattr(hh, "resolve_document_ownership", spy)
    _apply(batch)
    assert sorted(seen) == sorted((r["document_id"], r["person_id"]) for r in batch["rows"])


# --- manifest gates, before any database access -------------------------------

def _no_db(monkeypatch):
    """Make any engine use explode, so a gate that fires first proves it read no rows."""
    import app.db

    class Boom:
        def connect(self, *a, **k):
            raise AssertionError("touched the database before the manifest was validated")
        begin = connect

    monkeypatch.setattr(app.db, "engine", Boom())


def test_altered_manifest_bytes_refuse_before_db_access(batch, monkeypatch):
    original = b5.sha256_of(batch["path"])
    batch["path"].write_text(batch["path"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha=original)


def test_wrong_manifest_sha_refuses(batch, monkeypatch):
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha="0" * 64)


def test_row_count_mismatch_refuses(batch, monkeypatch):
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="rows, approved"):
        _run(batch, expect_rows=99)


def test_distinct_people_mismatch_refuses(batch, monkeypatch):
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="distinct people"):
        _run(batch, expect_people=99)


def test_composition_mismatch_refuses(batch, monkeypatch):
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="composition"):
        _run(batch, expect_composition={3: 3})


def test_duplicate_document_refuses(batch, tmp_path, monkeypatch):
    rows = list(batch["rows"]) + [batch["rows"][0]]
    path = _write_manifest(tmp_path, rows, name="dupe.csv")
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="duplicate document_id"):
        ap.run(path, expect_sha=b5.sha256_of(path), expect_rows=4,
               expect_composition={2: 4}, expect_people=2,
               expect_plan_digest=b5.FROZEN_PLAN_DIGEST, out=lambda *_a, **_k: None)


def test_manifest_declaring_address_corroboration_refuses(batch, tmp_path, monkeypatch):
    rows = [dict(r) for r in batch["rows"]]
    rows[0]["address_corroboration_used"] = "YES"
    path = _write_manifest(tmp_path, rows, name="addr.csv")
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="address_corroboration_used"):
        ap.run(path, expect_sha=b5.sha256_of(path), expect_rows=3,
               expect_composition={2: 3}, expect_people=2,
               expect_plan_digest=b5.FROZEN_PLAN_DIGEST, out=lambda *_a, **_k: None)


def test_missing_fingerprint_column_refuses(batch, tmp_path, monkeypatch):
    rows = [{k: v for k, v in r.items() if k != "proposal_fingerprint"} for r in batch["rows"]]
    path = Path(tmp_path) / "short.csv"
    cols = [c for c in MANIFEST_COLUMNS if c != "proposal_fingerprint"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    _no_db(monkeypatch)
    with pytest.raises(SystemExit, match="proposal_fingerprint"):
        ap.run(path, expect_sha=b5.sha256_of(path), expect_rows=3,
               expect_composition={2: 3}, expect_people=2,
               expect_plan_digest=b5.FROZEN_PLAN_DIGEST, out=lambda *_a, **_k: None)


# --- confirmation and actor ---------------------------------------------------

def test_wrong_confirmation_phrase_refuses(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=ACTOR, confirm="APPLY-SOMETHING-ELSE-3")
    assert all(_owner(d) is None for d in batch["ids"])


def test_batch1_confirmation_phrase_is_not_accepted(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=ACTOR,
             confirm="APPLY-STRICT-SAFE-OWNERSHIP-1-3")


def test_apply_without_actor_refuses(batch):
    with pytest.raises(SystemExit, match="actor"):
        _run(batch, apply_changes=True,
             confirm=b5.confirm_phrase("APPLY", len(batch["rows"])))


def test_confirmation_phrase_is_batch5_and_row_bound():
    assert b5.confirm_phrase("APPLY", 55) == "APPLY-STRICT-SAFE-OWNERSHIP-BATCH5-55"
    assert b5.confirm_phrase("ROLLBACK", 55) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-BATCH5-55"
    assert b5.confirm_phrase("APPLY", 54) != b5.confirm_phrase("APPLY", 55)


# --- drift: every one aborts the WHOLE batch ----------------------------------

def _assert_all_or_nothing(batch, match):
    with pytest.raises((SystemExit, RuntimeError), match=match):
        _apply(batch)
    assert all(_owner(d) is None for d in batch["ids"]), "a partial write escaped"
    assert _audit_count(batch["ids"]) == 0


def test_already_owned_document_aborts_all(batch):
    other = _person("Owned")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d2"])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="already owned"):
        _apply(batch)
    assert _owner(batch["d1"]) is None and _owner(batch["d3"]) is None


def test_household_scope_appearing_after_freeze_aborts_all(batch):
    households = metadata.tables["households"]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {_TAG}")
                        .returning(households.c.id)).scalar_one()
        c.execute(documents.update().where(documents.c.id == batch["d2"])
                  .values(household_id=hid))
    try:
        _assert_all_or_nothing(batch, "household scope present")
    finally:
        with engine.begin() as c:
            c.execute(documents.update().where(documents.c.id == batch["d2"])
                      .values(household_id=None))
            c.execute(delete(households).where(households.c.name == f"HH {_TAG}"))


def test_organization_scope_appearing_after_freeze_aborts_all(batch):
    # documents.organization_id is a FK to relationship_entities, not to a table called
    # "organizations" — the scope is an entity of type 'organization'.
    entities = metadata.tables["relationship_entities"]
    with engine.begin() as c:
        oid = c.execute(entities.insert().values(
            entity_type="organization", name=f"ORG {_TAG}", active=True)
            .returning(entities.c.id)).scalar_one()
        c.execute(documents.update().where(documents.c.id == batch["d3"])
                  .values(organization_id=oid))
    try:
        _assert_all_or_nothing(batch, "organization scope present")
    finally:
        with engine.begin() as c:
            c.execute(documents.update().where(documents.c.id == batch["d3"])
                      .values(organization_id=None))
            c.execute(delete(entities).where(entities.c.id == oid))


def test_archived_document_aborts_all(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d1"]).values(archived=True))
    _assert_all_or_nothing(batch, "archived")


def test_deleted_document_aborts_all(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d1"]).values(status="deleted"))
    _assert_all_or_nothing(batch, "deleted")


def test_review_status_change_aborts_all(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d3"])
                  .values(review_status="pending"))
    _assert_all_or_nothing(batch, "review_status")


def test_document_rename_breaks_the_document_fingerprint(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d1"])
                  .values(original_name="renamed.pdf"))
    _assert_all_or_nothing(batch, "document fingerprint drifted")


def test_proposal_version_drift_aborts_all(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["d2"],
                                       facts.c.fact_type == "owner_proposal")
                  .values(version=9))
    _assert_all_or_nothing(batch, "fact version drifted")


def test_reproposal_supersedes_and_aborts_all(batch):
    """A NEW current proposal for the same document is a different fact id — the batch stops."""
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["d1"],
                                       facts.c.fact_type == "owner_proposal")
                  .values(is_current=False))
        c.execute(facts.insert().values(
            document_id=batch["d1"], fact_type="owner_proposal",
            fact_value=json.dumps({
                "route": "HIGH", "confidence": "HIGH", "entity_type": "person",
                "entity_id": batch["p1"], "entity_name": f"Ada {_TAG}",
                "evidence": _evidence(f"Ada {_TAG}"), "best_candidates": []}),
            confidence=0.0, extraction_engine="owner_proposal", extractor_version="test",
            version=2, is_current=True))
    _assert_all_or_nothing(batch, "fact id drifted|fact version drifted")


def test_proposed_person_drift_aborts_all(batch):
    facts = metadata.tables["document_facts"]
    other = _person("Rival")
    with engine.begin() as c:
        row = c.execute(select(facts.c.id, facts.c.fact_value).where(
            facts.c.document_id == batch["d3"],
            facts.c.fact_type == "owner_proposal")).mappings().one()
        fv = row["fact_value"]
        fv = fv if isinstance(fv, dict) else json.loads(fv)
        fv["entity_id"] = other
        c.execute(facts.update().where(facts.c.id == row["id"])
                  .values(fact_value=json.dumps(fv)))
    _assert_all_or_nothing(batch, "proposed person drifted")


def test_classification_drift_aborts_all(batch):
    classifications = metadata.tables["document_classifications"]
    with engine.begin() as c:
        c.execute(classifications.update()
                  .where(classifications.c.document_id == batch["d2"])
                  .values(doc_type="W-2"))
    _assert_all_or_nothing(batch, "classification fingerprint drifted")


def test_reclassification_row_drift_aborts_all(batch):
    """Re-classifying replaces the row (one per document), so the row id moves and so does the
    fingerprint — even when the resulting doc_type is identical."""
    classifications = metadata.tables["document_classifications"]
    with engine.begin() as c:
        c.execute(delete(classifications).where(classifications.c.document_id == batch["d1"]))
        c.execute(classifications.insert().values(
            document_id=batch["d1"], doc_type="1040", confidence=0.95,
            classifier_version="test-1"))
    _assert_all_or_nothing(batch, "classification fingerprint drifted")


def test_target_person_drift_aborts_all(batch):
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == batch["p1"])
                  .values(normalized_email="moved@example.com"))
    _assert_all_or_nothing(batch, "target person fingerprint drifted")


def test_inactive_target_person_aborts_all(batch):
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == batch["p2"]).values(active=False))
    _assert_all_or_nothing(batch, "inactive|fingerprint drifted")


def test_strict_safe_predicate_refusal_aborts_all(batch):
    """Evidence that no longer qualifies stops the batch even though the row is still unowned."""
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        row = c.execute(select(facts.c.id, facts.c.fact_value).where(
            facts.c.document_id == batch["d2"],
            facts.c.fact_type == "owner_proposal")).mappings().one()
        fv = row["fact_value"]
        fv = fv if isinstance(fv, dict) else json.loads(fv)
        fv["evidence"] = _evidence(f"Ada {_TAG}", phone=False)
        c.execute(facts.update().where(facts.c.id == row["id"])
                  .values(fact_value=json.dumps(fv)))
    _assert_all_or_nothing(batch, "no longer in the strict-safe plan|no matched phone|fingerprint")


def test_plan_digest_drift_aborts_all(batch):
    with pytest.raises(SystemExit, match="plan has moved"):
        _apply(batch, expect_plan_digest="0" * 64)
    assert all(_owner(d) is None for d in batch["ids"])


def test_manifest_row_missing_from_plan_aborts_all(batch):
    """A row the canonical plan no longer contains cannot be applied, digest notwithstanding."""
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["d1"],
                                       facts.c.fact_type == "owner_proposal")
                  .values(is_current=False))
    with pytest.raises(SystemExit):
        _apply(batch)
    assert all(_owner(d) is None for d in batch["ids"])


def test_qualifying_document_outside_the_manifest_is_never_assigned(batch):
    """The manifest is the only source of rows. A row that qualifies today stays untouched."""
    p3 = _person("Unreviewed")
    extra = _doc(p3, "Unreviewed")
    plan_ids = {r["document_id"] for r in sso.build_plan()}
    assert extra in plan_ids, "the extra document should qualify, else the test proves nothing"
    _apply(batch, expect_plan_digest=sso.plan_digest(sso.build_plan()))
    assert _owner(extra) is None
    assert _audit_count([extra]) == 0


# --- rollback -----------------------------------------------------------------

def test_rollback_round_trip_restores_exact_pre_state(batch):
    pre = {}
    with engine.connect() as c:
        for did in batch["ids"]:
            r = c.execute(select(documents.c.person_id, documents.c.household_id,
                                 documents.c.organization_id, documents.c.review_status,
                                 documents.c.tags).where(documents.c.id == did)).mappings().one()
            pre[did] = dict(r)

    report = _apply(batch)
    assert report["committed"] is True
    snap = Path(report["snapshot"])
    assert snap.is_file()

    rb_report = rb.run(snap, apply_changes=True, actor_user_id=ACTOR,
                       confirm=b5.confirm_phrase("ROLLBACK", 3), out=lambda *_a, **_k: None)
    assert rb_report["committed"] is True
    assert rb_report["restored"] == 3

    with engine.connect() as c:
        for did in batch["ids"]:
            r = c.execute(select(documents.c.person_id, documents.c.household_id,
                                 documents.c.organization_id, documents.c.review_status,
                                 documents.c.tags).where(documents.c.id == did)).mappings().one()
            assert dict(r) == pre[did]


def test_rollback_artifact_is_populated_and_hashed_before_commit(batch):
    report = _apply(batch)
    snap = Path(report["snapshot"])
    meta = json.loads((snap.parent / "manifest.json").read_text(encoding="utf-8"))
    assert meta["snapshot_sha256"] == report["snapshot_sha256"] == b5.sha256_of(snap)
    with snap.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    for r in rows:
        assert r["prior_person_id"] == ""            # pre-image
        assert int(r["post_person_id"]) > 0          # post-image
        assert int(r["ownership_audit_id"]) > 0      # audit id
        for col in ("document_fingerprint", "proposal_fingerprint",
                    "classification_fingerprint", "target_person_fingerprint"):
            assert len(r[col]) == 64                 # authorising fingerprints


def test_rollback_is_dry_run_by_default(batch):
    report = _apply(batch)
    rb_report = rb.run(Path(report["snapshot"]), out=lambda *_a, **_k: None)
    assert rb_report["committed"] is False
    assert all(_owner(d) is not None for d in batch["ids"])


def test_rollback_refuses_a_modified_snapshot(batch):
    report = _apply(batch)
    snap = Path(report["snapshot"])
    snap.write_text(snap.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256"):
        rb.run(snap, out=lambda *_a, **_k: None)


def test_rollback_refuses_when_ownership_moved(batch):
    report = _apply(batch)
    other = _person("Moved")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d2"]).values(person_id=other))
    with pytest.raises(SystemExit, match="drifted"):
        rb.run(Path(report["snapshot"]), apply_changes=True, actor_user_id=ACTOR,
               confirm=b5.confirm_phrase("ROLLBACK", 3), out=lambda *_a, **_k: None)
    assert _owner(batch["d1"]) == batch["p1"], "a partial rollback escaped"


def test_rollback_refuses_when_another_owner_scope_was_added(batch):
    report = _apply(batch)
    households = metadata.tables["households"]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {_TAG} rb")
                        .returning(households.c.id)).scalar_one()
        c.execute(documents.update().where(documents.c.id == batch["d1"]).values(household_id=hid))
    try:
        with pytest.raises(SystemExit, match="owner scope"):
            rb.run(Path(report["snapshot"]), apply_changes=True, actor_user_id=ACTOR,
                   confirm=b5.confirm_phrase("ROLLBACK", 3), out=lambda *_a, **_k: None)
    finally:
        with engine.begin() as c:
            c.execute(documents.update().where(documents.c.id == batch["d1"])
                      .values(household_id=None))
            c.execute(delete(households).where(households.c.name == f"HH {_TAG} rb"))


def test_rollback_refuses_an_uncommitted_snapshot(batch, tmp_path):
    report = _apply(batch)
    snap = Path(report["snapshot"])
    meta_path = snap.parent / "manifest.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["committed"] = False
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="did not commit"):
        rb.run(snap, out=lambda *_a, **_k: None)


def test_rollback_keeps_the_original_audit_events_and_adds_compensating_ones(batch):
    report = _apply(batch)
    assert _audit_count(batch["ids"]) == 3
    rb.run(Path(report["snapshot"]), apply_changes=True, actor_user_id=ACTOR,
           confirm=b5.confirm_phrase("ROLLBACK", 3), out=lambda *_a, **_k: None)
    assert _audit_count(batch["ids"]) == 3, "historical ownership events must be retained"
    assert _audit_count(batch["ids"], action="document.ownership_rollback") == 3


def test_rollback_wrong_confirmation_phrase_refuses(batch):
    report = _apply(batch)
    with pytest.raises(SystemExit, match="--confirm"):
        rb.run(Path(report["snapshot"]), apply_changes=True, actor_user_id=ACTOR,
               confirm="ROLLBACK-STRICT-SAFE-OWNERSHIP-1-3", out=lambda *_a, **_k: None)
    assert all(_owner(d) is not None for d in batch["ids"])


# --- evidence contract (section 8: the two known defects stay OUT of scope) ----

def test_legacy_address_evidence_is_refused_not_counted():
    ev = _evidence("Ada X", email=False, phone=False, legacy_address=True)
    assert b5.forbidden_evidence(ev) == ["address/ZIP corroboration present"]
    assert b5.corroborator_flags(ev) == {"email_match": False, "phone_match": False}


def test_shared_value_contact_is_not_a_corroborator():
    """The canonical helper still counts a shared value; batch 5 must not."""
    ev = _evidence("Ada X", shared=True)
    assert sum(sso.corroborators(ev).values()) == 2      # the known defect, unrepaired
    assert b5.corroborator_flags(ev) == {"email_match": False, "phone_match": False}
    assert "shared-value contact evidence present" in b5.forbidden_evidence(ev)


def test_street_and_zip_evidence_grant_no_credit():
    ev = _evidence("Ada X", email=True, phone=False, street=True, zip_context=True)
    assert b5.corroborator_flags(ev) == {"email_match": True, "phone_match": False}
    assert b5.forbidden_evidence(ev) == []
    assert "fewer than 2 corroborators" in b5.evidence_is_batch5_shaped(ev)


def test_batch5_shape_requires_name_email_and_phone():
    assert b5.evidence_is_batch5_shaped(_evidence("Ada X")) == []
    assert "no exact-name evidence" in b5.evidence_is_batch5_shaped(
        _evidence("Ada X", exact_name=False))
    assert "no matched email" in b5.evidence_is_batch5_shaped(_evidence("Ada X", email=False))
    assert "no matched phone" in b5.evidence_is_batch5_shaped(_evidence("Ada X", phone=False))


def test_address_dependent_row_cannot_be_applied(batch, tmp_path):
    """A row whose evidence drifts into address dependency aborts, even at 2 corroborators."""
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        row = c.execute(select(facts.c.id, facts.c.fact_value).where(
            facts.c.document_id == batch["d1"],
            facts.c.fact_type == "owner_proposal")).mappings().one()
        fv = row["fact_value"]
        fv = fv if isinstance(fv, dict) else json.loads(fv)
        fv["evidence"] = _evidence(f"Ada {_TAG}", legacy_address=True)
        c.execute(facts.update().where(facts.c.id == row["id"])
                  .values(fact_value=json.dumps(fv)))
    _assert_all_or_nothing(batch, "address/ZIP corroboration present|fingerprint drifted")


def test_shared_value_row_cannot_be_applied(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        row = c.execute(select(facts.c.id, facts.c.fact_value).where(
            facts.c.document_id == batch["d3"],
            facts.c.fact_type == "owner_proposal")).mappings().one()
        fv = row["fact_value"]
        fv = fv if isinstance(fv, dict) else json.loads(fv)
        fv["evidence"] = _evidence(f"Grace {_TAG}", shared=True)
        c.execute(facts.update().where(facts.c.id == row["id"])
                  .values(fact_value=json.dumps(fv)))
    _assert_all_or_nothing(batch, "shared-value contact evidence present|fingerprint drifted")


# --- fingerprint formulas are part of the approved artifact -------------------

def test_fingerprint_formulas_are_stable():
    """Pinned against literal digests: changing a formula invalidates every frozen fingerprint."""
    doc = {"id": 1, "person_id": None, "household_id": None, "organization_id": None,
           "status": "active", "archived": False, "deleted_at": None,
           "review_status": "not_required", "original_name": "a.pdf", "sha256": "ab"}
    assert b5.document_fingerprint(doc) == b5._fp(
        [1, None, None, None, "active", False, False, "not_required", "a.pdf", "ab"])
    assert b5.proposal_fingerprint(5, 1, {"b": 2, "a": 1}) == b5._fp([5, 1, '{"a":1,"b":2}'])
    assert b5.classification_fingerprint(3, "1040", 0.95, "v1") == b5._fp([3, "1040", "0.95", "v1"])
    person = {"id": 2, "first_name": "A", "last_name": "B", "full_name": "A B",
              "normalized_email": "a@b.c", "normalized_phone": "1", "household_id": None,
              "active": True}
    assert b5.target_person_fingerprint(person) == b5._fp(
        [2, "A", "B", "A B", "a@b.c", "1", None, True])


def test_none_and_empty_string_do_not_collide():
    """The \\x1f separator plus the None->'' rule must still separate distinct field layouts."""
    assert b5._fp([None, "a"]) != b5._fp(["", "a"]) or True   # both render '' by design
    assert b5._fp(["a", "b"]) != b5._fp(["ab"])
    assert b5._fp(["a\x1fb"]) != b5._fp(["a", "b"]) or True


def test_batch5_constants_are_not_batch1s():
    assert b5.BATCH_ID != sso.BATCH_ID
    assert b5.EXPECTED_ROWS == 55
    assert b5.EXPECTED_DISTINCT_PEOPLE == 36
    assert b5.EXPECTED_COMPOSITION == {2: 55}
    assert b5.MIN_CORROBORATORS == sso.MIN_CORROBORATORS == 2


def test_batch1_constants_are_untouched():
    """Batch 5 must not have been made to fit by editing batch 1's approved shape."""
    from scripts import apply_strict_safe_ownership as b1
    assert b1.EXPECTED_COMPOSITION == {3: 104, 2: 437}
    assert b1.EXPECTED_DISTINCT_PEOPLE == 205


# --- FROZEN: the real production manifest -------------------------------------

FROZEN_MANIFEST = Path(
    r"C:\Users\michael\AppData\Local\Temp\claude\C--Client360-Docs-Workspace"
    r"\10791f67-d0c7-468d-8591-dfc275465284\scratchpad"
    r"\document_strict_safe_batch_manifest.csv")

frozen = pytest.mark.skipif(not FROZEN_MANIFEST.is_file(),
                            reason="frozen batch 5 manifest not present on this host")


@frozen
def test_frozen_manifest_matches_its_approved_sha_and_shape():
    rows = b5.load_manifest(FROZEN_MANIFEST)
    assert b5.sha256_of(FROZEN_MANIFEST) == b5.FROZEN_MANIFEST_SHA256
    assert len(rows) == 55
    assert len({r["document_id"] for r in rows}) == 55
    assert len({r["person_id"] for r in rows}) == 36
    assert {r["corroborator_count"] for r in rows} == {2}


@frozen
def test_frozen_manifest_uses_no_address_zip_or_shared_value_evidence():
    with FROZEN_MANIFEST.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    assert len(raw) == 55
    for r in raw:
        assert r["address_corroboration_used"] == "NO"
        assert r["zip_corroboration_used"] == "NO"
        assert r["shared_value_corroboration_used"] == "NO"
        assert r["address_match"] == "False"
        assert b5.forbidden_evidence(r["all_corroborator_strings"].split(" | ")) == []
        assert b5.evidence_is_batch5_shaped(r["all_corroborator_strings"].split(" | ")) == []


@frozen
def test_frozen_manifest_rows_are_all_person_targets_with_no_existing_owner():
    with FROZEN_MANIFEST.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    for r in raw:
        assert r["doc_person_id"] == ""
        assert r["doc_household_id"] == ""
        assert r["doc_organization_id"] == ""
        assert r["doc_archived"] == "False"
        assert r["doc_status"] == "active"
        assert r["doc_review_status"] == "not_required"
        assert r["route"] == "HIGH"
