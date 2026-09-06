"""Strict-safe ownership BATCH 2 — the selection rule and the guarded apply.

Batch 2 assigns real client documents to real people on ONE contact corroborator instead of Batch 1's
two, and makes up the difference with two signals that live outside the document's text: the
SharePoint folder it is filed in, and its own filename. Every test below pins one way that trade
could go wrong — folder evidence borrowed from the filename, a name spread across two folder levels,
an unavailable or non-SharePoint source, a second corroborator that would make it a Batch 1 row, one
of the two permanently excluded documents, or a manifest that is no longer the reviewed batch.

The fixture builds a small batch and patches the approved counts to match, so the real code path runs
without the 52-row production manifest. The production controls are exercised against their real
values in test_reviewed_manifest_controls_are_reproduced.

Temp rows only, all tagged, all cleaned up.
"""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services import document_strict_safe_ownership_batch2 as b2
from scripts import apply_strict_safe_ownership_batch2 as ap
from scripts import rollback_strict_safe_ownership_batch2 as rb

_TAG = f"SSOTWO{uuid.uuid4().hex[:6]}"

MANIFEST_COLUMNS = ["document_id", "person_id", "person_name", "original_name", "corroborator",
                    "matching_folder", "source_id", "source_external_id", "source_uri",
                    "review_status", "evidence_json"]

REVIEWED_MANIFEST = Path(
    r"C:\Client360\reports\strict-safe-ownership-batch2-20260905-235055"
    r"\strict_safe_ownership_batch2_manifest.csv")


@pytest.fixture(autouse=True)
def _clean():
    yield
    facts = metadata.tables["document_facts"]
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"sso2:{_TAG}%")))]
        if ids:
            c.execute(delete(sources).where(sources.c.document_id.in_(ids)))
            c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))


# --- builders -----------------------------------------------------------------

def _person(first: str) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=_TAG, full_name=f"{first} {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()


def _evidence(person_name, *, exact_name=True, email=False, phone=False, address=True):
    """Proposal evidence in the engine's own vocabulary. Default: name + ONE corroborator."""
    ev = []
    if exact_name:
        ev.append(f"✓ exact name '{person_name}'")
    if email:
        ev.append("✓ email someone@example.com matched")
    if phone:
        ev.append("✓ phone ending 0123 matched")
    if address:
        ev.append("✓ address/ZIP matched")
    ev.append("context only (not an owner): irs")
    return ev


def _uri(folder: str, filename: str, *, ancestry=("Clients", "Individual")) -> str:
    parts = "/".join(quote(p) for p in (*ancestry, folder, "2021", filename))
    return f"https://example.sharepoint.com/sites/Data/Shared%20Documents/{parts}"


def _doc(person_id, first, *, route="HIGH", entity_type="person", evidence=None,
         review_status="not_required", archived=False, status="active", owner=None,
         filename=None, folder=None, source_system="SharePoint", available=True,
         with_source=True, uri=None, document_id=None, rival_proposal_person_id=None) -> int:
    """One candidate document: a current owner_proposal plus a SharePoint filing source."""
    person_name = f"{first} {_TAG}"
    filename = filename if filename is not None else f"{first} {_TAG} 2021.pdf"
    folder = folder if folder is not None else f"{_TAG}, {first}"
    sources = metadata.tables["document_sources"]
    facts = metadata.tables["document_facts"]
    values = dict(
        original_name=filename, stored_name=f"sso2:{_TAG}{uuid.uuid4().hex}",
        storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
        size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
        review_status=review_status, current_version=1, person_id=owner,
        tags={"source_system": "SharePoint"},
    )
    if document_id is not None:
        values["id"] = document_id
    with engine.begin() as c:
        did = c.execute(documents.insert().values(**values)
                        .returning(documents.c.id)).scalar_one()
        proposals = [(person_id, person_name)]
        if rival_proposal_person_id is not None:
            proposals.append((rival_proposal_person_id, f"Rival {_TAG}"))
        for version, (entity_id, entity_name) in enumerate(proposals, start=1):
            c.execute(facts.insert().values(
                document_id=did, fact_type="owner_proposal",
                fact_value=json.dumps({
                    "route": route, "confidence": "HIGH", "entity_type": entity_type,
                    "entity_id": entity_id, "entity_name": entity_name,
                    "evidence": evidence if evidence is not None else _evidence(entity_name),
                }),
                confidence=0.0, extraction_engine="owner_proposal", extractor_version="test",
                version=version, is_current=True))
        if with_source:
            c.execute(sources.insert().values(
                document_id=did, source_system=source_system,
                source_uri=uri if uri is not None else _uri(folder, filename),
                source_external_id=f"EXT{did}", source_hash="f" * 64,
                available=available, metadata={}))
    return did


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents).where(documents.c.id == did)).mappings().one()


def _plan_ids(*dids):
    return {r["document_id"] for r in b2.build_plan()} & set(dids)


def _write_manifest(tmp_path, plan_rows, *, mutate=None) -> tuple[Path, str]:
    path = tmp_path / "strict_safe_ownership_batch2_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(plan_rows, key=lambda x: x["document_id"]):
            row = {
                "document_id": r["document_id"], "person_id": r["person_id"],
                "person_name": r["person_name"], "original_name": r["original_name"],
                "corroborator": r["corroborator"], "matching_folder": r["matching_folder"],
                "source_id": r["source_id"], "source_external_id": r["source_external_id"],
                "source_uri": r["source_uri"], "review_status": r["review_status"],
                "evidence_json": json.dumps(r["evidence"], ensure_ascii=False),
            }
            if mutate is not None:
                row = mutate(row)
            w.writerow(row)
    return path, ap.sha256_of(path)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Three Batch 2 documents across two people, with a manifest that matches them."""
    p1, p2 = _person("Ada"), _person("Grace")
    d1 = _doc(p1, "Ada")
    d2 = _doc(p1, "Ada", evidence=_evidence(f"Ada {_TAG}", address=False, email=True))
    d3 = _doc(p2, "Grace")
    ids = sorted([d1, d2, d3])
    plan = [r for r in b2.build_plan() if r["document_id"] in ids]
    assert len(plan) == 3, f"fixture must be batch-2 eligible; got {len(plan)}"
    path, sha = _write_manifest(tmp_path, plan)
    monkeypatch.setattr(ap, "EXPECTED_ROWS", 3)
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {"ADDRESS": 2, "EMAIL": 1})
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 2)
    return {"ids": ids, "plan": plan, "path": path, "sha": sha, "p1": p1, "p2": p2,
            # captured AT REVIEW TIME, exactly as the approved digest is: a test that then moves the
            # corpus must be refused by the digest gate, not by luck of recomputation order.
            "digest": b2.plan_digest(b2.build_plan()),
            "tmp_path": tmp_path, "snapshot_root": tmp_path / "snap"}


def _digest():
    return b2.plan_digest(b2.build_plan())


def _run(batch, **kw):
    kw.setdefault("expect_sha", batch["sha"])
    kw.setdefault("expect_plan_digest", batch["digest"])
    kw.setdefault("expect_rows", len(batch["ids"]))
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["path"], **kw)


def _apply(batch, **kw):
    return _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7, **kw)


def _audit_rows(request_id):
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        return c.execute(select(audit.c.id).where(audit.c.request_id == request_id)).all()


def _audit_events(request_id):
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        return c.execute(select(audit.c.action, audit.c.entity_id, audit.c.metadata)
                         .where(audit.c.request_id == request_id)).mappings().all()


def _snap_dir(batch):
    return next(Path(batch["snapshot_root"]).glob("strict-safe-ownership-batch2-apply-*"))


def _request_id(sha):
    return f"strict-safe-ownership-batch2:{b2.BATCH_ID}:{sha[:12]}"


# --- the reviewed manifest's own controls -------------------------------------

def test_reviewed_manifest_controls_are_reproduced():
    """The approved rows, people and composition must come out of our own constants."""
    if not REVIEWED_MANIFEST.is_file():
        pytest.skip("reviewed batch 2 manifest not present on this machine")
    assert ap.sha256_of(REVIEWED_MANIFEST) == \
        "14407b2ba1db61e5d05995b1d1c3249a8851b08b4068e7a58b93285489b5adb7"
    with REVIEWED_MANIFEST.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == ap.EXPECTED_ROWS == 52
    assert len({r["person_id"] for r in rows}) == ap.EXPECTED_DISTINCT_PEOPLE == 39
    composition: dict[str, int] = {}
    for r in rows:
        composition[r["corroborator"]] = composition.get(r["corroborator"], 0) + 1
    assert composition == ap.EXPECTED_COMPOSITION == {"ADDRESS": 49, "EMAIL": 1, "PHONE": 2}
    assert ap.confirm_phrase(52) == "APPLY-STRICT-SAFE-OWNERSHIP-2-52"
    assert b2.PERMANENT_EXCLUDED_DOCUMENT_IDS == frozenset({40100, 44247})
    assert ap.INDEPENDENT_DOCUMENT_CORROBORATOR_COUNT == 1
    # every reviewed row satisfies the rule's evidence clause as this codebase states it
    for r in rows:
        evidence = json.loads(r["evidence_json"])
        assert b2.has_exact_name_evidence(evidence), r["document_id"]
        assert b2.sole_corroborator(evidence) == r["corroborator"], r["document_id"]


def test_batch1_semantics_are_untouched():
    """Batch 2 borrows Batch 1's evidence predicates; it must not have changed them."""
    from app.services import document_strict_safe_ownership as sso
    assert sso.MIN_CORROBORATORS == 2
    assert sso.BATCH_ID == "STRICT-SAFE-OWNERSHIP-1" and b2.BATCH_ID == "STRICT-SAFE-OWNERSHIP-2"
    ev = _evidence("Ada Lovelace", email=True, address=True)
    assert sso.is_strict_safe({"route": "HIGH", "entity_type": "person", "entity_id": 1}, ev)
    assert b2.sole_corroborator(ev) is None, "two corroborators is a Batch 1 row, not Batch 2"


# --- normalization and the path rules, as units -------------------------------

def test_ascii_normalization_folds_accents_and_splits_apostrophes():
    assert b2.ascii_tokens("O'Gorman") == ("o", "gorman")
    assert b2.ascii_tokens("Renée MÜLLER") == ("renee", "muller")
    assert b2.ascii_tokens("EANES,  WALTER   L") == ("eanes", "walter", "l")
    assert b2.ascii_tokens(None) == ()


def test_person_without_a_surname_can_never_match():
    assert b2.person_name_tokens("JERAJH INC", None) is None
    assert b2.person_name_tokens(None, "Doe") is None
    assert b2.person_name_tokens("Amedee", "O'Gorman") == frozenset({"amedee", "o", "gorman"})


def test_parent_segments_url_decode_and_drop_the_filename():
    uri = ("https://x.sharepoint.com/sites/Data/Shared%20Documents/Clients/"
           "O%27GORMAN%2C%20AMEDEE%20AND%20ANDREA/2022/Jane%20Doe%202022.pdf")
    segments = b2.parent_folder_segments(uri)
    assert segments[-2:] == ["O'GORMAN, AMEDEE AND ANDREA", "2022"]
    assert "Jane Doe 2022.pdf" not in segments


# --- selection: document state ------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"status": "deleted"},
    {"archived": True},
    {"review_status": "pending"},
], ids=["deleted", "archived", "review_required"])
def test_deleted_archived_and_review_documents_are_rejected(kwargs):
    pid = _person("State")
    did = _doc(pid, "State", **kwargs)
    assert _plan_ids(did) == set()


def test_already_owned_documents_are_rejected():
    pid, other = _person("Owned"), _person("Other")
    did = _doc(pid, "Owned", owner=other)
    assert _plan_ids(did) == set()


# --- selection: proposal and evidence -----------------------------------------

def test_missing_exact_name_evidence_is_rejected():
    pid = _person("Nameless")
    did = _doc(pid, "Nameless", evidence=_evidence(f"Nameless {_TAG}", exact_name=False))
    assert _plan_ids(did) == set()


def test_zero_corroborators_is_rejected():
    pid = _person("Bare")
    did = _doc(pid, "Bare", evidence=_evidence(f"Bare {_TAG}", address=False))
    assert _plan_ids(did) == set()


def test_two_or_more_corroborators_is_rejected():
    """Two corroborators is a Batch 1 row. Batch 2 was reviewed as the ONE-corroborator population."""
    pid = _person("Rich")
    two = _doc(pid, "Rich", evidence=_evidence(f"Rich {_TAG}", email=True, address=True))
    three = _doc(pid, "Rich", evidence=_evidence(f"Rich {_TAG}", email=True, phone=True,
                                                 address=True))
    assert _plan_ids(two, three) == set()


@pytest.mark.parametrize("route,entity_type", [("MEDIUM", "person"), ("HIGH", "organization")])
def test_non_high_and_non_person_proposals_are_rejected(route, entity_type):
    pid = _person("Routed")
    did = _doc(pid, "Routed", route=route, entity_type=entity_type)
    assert _plan_ids(did) == set()


def test_more_than_one_current_proposal_is_rejected():
    """Two live proposals means the engine has not settled on one person. Exactly one, or nothing."""
    pid, rival = _person("Twice"), _person("Rival")
    did = _doc(pid, "Twice", rival_proposal_person_id=rival)
    assert _plan_ids(did) == set()


# --- selection: the two independent path signals ------------------------------

def test_filename_evidence_alone_cannot_satisfy_the_folder_rule():
    """The filename is removed before folder matching, so it cannot vouch for its own folder."""
    pid = _person("Solo")
    did = _doc(pid, "Solo", folder="Unrelated Family Trust")
    assert _plan_ids(did) == set()


def test_folder_ancestry_split_across_segments_cannot_satisfy_the_same_segment_rule():
    """/<last>/<first>/ is NOT a match: both tokens must name the person in ONE segment."""
    pid = _person("Split")
    filename = f"Split {_TAG} 2021.pdf"
    uri = _uri("Split", filename, ancestry=("Clients", _TAG))
    did = _doc(pid, "Split", filename=filename, uri=uri)
    assert _plan_ids(did) == set()
    # the same tokens in ONE segment do qualify
    ok = _doc(pid, "Split", filename=filename, uri=_uri(f"{_TAG}, Split", filename))
    assert _plan_ids(ok) == {ok}


def test_filename_must_independently_contain_first_and_last():
    """A perfectly filed document whose own name does not carry both tokens is out."""
    pid = _person("Quiet")
    did = _doc(pid, "Quiet", filename="2021 Form 1099-INT.pdf")
    assert _plan_ids(did) == set()
    partial = _doc(pid, "Quiet", filename=f"{_TAG} 2021.pdf")     # surname only
    assert _plan_ids(partial) == set()


def test_unavailable_sharepoint_sources_are_ignored():
    pid = _person("Gone")
    did = _doc(pid, "Gone", available=False)
    assert _plan_ids(did) == set()


def test_non_sharepoint_sources_cannot_satisfy_the_source_rule():
    pid = _person("Elsewhere")
    for system in ("TaxDome Drive", "Drake"):
        did = _doc(pid, "Elsewhere", source_system=system)
        assert _plan_ids(did) == set(), system


def test_a_document_with_no_source_at_all_is_rejected():
    pid = _person("Sourceless")
    did = _doc(pid, "Sourceless", with_source=False)
    assert _plan_ids(did) == set()


def test_the_lowest_id_matching_sharepoint_source_is_canonical():
    """Two matching folders must resolve deterministically, and never by row order."""
    pid = _person("Twin")
    filename = f"Twin {_TAG} 2021.pdf"
    did = _doc(pid, "Twin", filename=filename, folder=f"{_TAG}, Twin")
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        c.execute(sources.insert().values(
            document_id=did, source_system="SharePoint",
            source_uri=_uri(f"Twin {_TAG} and Spouse", filename, ancestry=("Archive",)),
            source_external_id=f"EXT{did}B", available=True, metadata={}))
    row = next(r for r in b2.build_plan() if r["document_id"] == did)
    with engine.connect() as c:
        lowest = c.execute(text("select min(id) from document_sources where document_id = :d"),
                           {"d": did}).scalar()
    assert row["source_id"] == lowest and row["matching_folder"] == f"{_TAG}, Twin"


# --- selection: the permanent exclusions --------------------------------------

@pytest.mark.parametrize("excluded_id", sorted(b2.PERMANENT_EXCLUDED_DOCUMENT_IDS))
def test_permanently_excluded_documents_are_rejected(excluded_id, monkeypatch):
    """40100 and 44247 have conflicting available SharePoint paths and are excluded by id.

    Whatever else is in this database, the excluded id must not appear in the plan. Where the id is
    free we also PROVE the exclusion is what removes it, by building a document that satisfies every
    other clause of the rule under that exact id.
    """
    assert excluded_id not in {r["document_id"] for r in b2.build_plan()}

    with engine.connect() as c:
        taken = c.execute(select(documents.c.id).where(documents.c.id == excluded_id)).first()

    pid = _person("Excluded")
    if taken:
        # The id is already used by this database, so stand a qualifying document in for it and
        # exclude that id instead: the clause under test is the same one, with the same operand.
        did = _doc(pid, "Excluded")
        assert _plan_ids(did) == {did}, "the stand-in must qualify on every other clause"
        monkeypatch.setattr(b2, "PERMANENT_EXCLUDED_DOCUMENT_IDS", frozenset({did}))
    else:
        did = _doc(pid, "Excluded", document_id=excluded_id)
        assert did == excluded_id
    assert _plan_ids(did) == set(), "an otherwise-qualifying excluded id must never be selected"


def test_the_manifest_may_not_carry_an_excluded_document(batch, tmp_path):
    def swap(row):
        if row["document_id"] == batch["ids"][0]:
            row = {**row, "document_id": sorted(b2.PERMANENT_EXCLUDED_DOCUMENT_IDS)[0]}
        return row
    out = tmp_path / "excluded"
    out.mkdir()
    path, sha = _write_manifest(out, batch["plan"], mutate=swap)
    with pytest.raises(SystemExit, match="permanently excluded"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_digest(), expect_rows=3,
               out=lambda *_a, **_k: None)


# --- the digest ----------------------------------------------------------------

def test_plan_digest_is_canonical_and_order_independent(batch):
    plan = batch["plan"]
    assert b2.plan_digest(plan) == b2.plan_digest(list(reversed(plan)))
    assert b2.plan_digest(plan) == b2.plan_digest([{**r, "extra": "ignored"} for r in plan])
    moved = [{**r} for r in plan]
    moved[0]["person_id"] += 1
    assert b2.plan_digest(moved) != b2.plan_digest(plan)


def test_census_names_corroborators_and_never_counts_them_as_batch1_does(batch):
    census = b2.plan_census(batch["plan"])
    assert census == {"rows": 3, "distinct_people": 2,
                      "by_corroborator": {"ADDRESS": 2, "EMAIL": 1}}
    assert "by_corroborator_count" not in census


# --- apply gates ---------------------------------------------------------------

def test_apply_is_read_only_by_default(batch):
    report = _run(batch)
    assert report["committed"] is False and report["applied"] == 0
    assert report["validated"] == 3
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None
    assert not batch["snapshot_root"].exists(), "a dry run must not write a snapshot"
    assert _audit_rows(_request_id(batch["sha"])) == []


def test_manifest_drift_aborts(batch):
    """A manifest edited after review no longer hashes to the approved digest."""
    batch["path"].write_text(batch["path"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch)
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha="0" * 64)


def test_wrong_row_count_aborts(batch):
    with pytest.raises(SystemExit, match="!= the approved"):
        _run(batch, expect_rows=52)


def test_composition_and_people_mismatches_abort(batch, monkeypatch):
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {"ADDRESS": 3})
    with pytest.raises(SystemExit, match="composition"):
        _run(batch)
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {"ADDRESS": 2, "EMAIL": 1})
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 99)
    with pytest.raises(SystemExit, match="distinct people"):
        _run(batch)


def test_unknown_corroborator_label_aborts(batch, tmp_path):
    out = tmp_path / "corro"
    out.mkdir()
    path, sha = _write_manifest(out, batch["plan"],
                                mutate=lambda r: {**r, "corroborator": "FOLDER"})
    with pytest.raises(SystemExit, match="not one of"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_digest(), expect_rows=3,
               out=lambda *_a, **_k: None)


def test_apply_without_confirm_or_actor_aborts(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=7)
    with pytest.raises(SystemExit, match="actor"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3))


def test_a_batch1_confirmation_phrase_cannot_apply_batch2(batch):
    from scripts import apply_strict_safe_ownership as batch1
    assert ap.confirm_phrase(3) != batch1.confirm_phrase(3)
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=7, confirm=batch1.confirm_phrase(3))


# --- drift aborts --------------------------------------------------------------

def _expect_drift(batch, match="no longer validate|plan has moved"):
    with pytest.raises(SystemExit, match=match):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None, "no row may be written when the batch aborts"
    assert not batch["snapshot_root"].exists()


def test_source_drift_aborts(batch):
    """A filing source that goes unavailable removes its document from the plan."""
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        c.execute(sources.update().where(sources.c.document_id == batch["ids"][0])
                  .values(available=False))
    _expect_drift(batch, match="plan has moved")


def test_folder_rename_drift_aborts(batch):
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        c.execute(sources.update().where(sources.c.document_id == batch["ids"][0])
                  .values(source_uri=_uri("Somebody Else", "renamed.pdf")))
    _expect_drift(batch, match="plan has moved")


def test_manifest_source_that_disagrees_with_live_state_aborts_under_the_lock(batch, tmp_path):
    """The under-lock source check: a manifest whose source_id is not the live one is refused."""
    out = tmp_path / "srcid"
    out.mkdir()
    path, sha = _write_manifest(out, batch["plan"], mutate=lambda r: {
        **r, "source_id": int(r["source_id"]) + 10_000_000})
    with pytest.raises(SystemExit, match="filing source drifted"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_digest(), expect_rows=3,
               apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7,
               snapshot_root=batch["snapshot_root"], out=lambda *_a, **_k: None)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


def test_wrong_proposed_person_aborts_under_the_lock(batch, tmp_path):
    out = tmp_path / "person"
    out.mkdir()
    swap = {batch["p1"]: batch["p2"], batch["p2"]: batch["p1"]}
    path, sha = _write_manifest(out, batch["plan"], mutate=lambda r: {
        **r, "person_id": swap[int(r["person_id"])]})
    with pytest.raises(SystemExit, match="proposed person drifted"):
        ap.run(path, expect_sha=sha, expect_plan_digest=_digest(), expect_rows=3,
               apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=7,
               snapshot_root=batch["snapshot_root"], out=lambda *_a, **_k: None)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


def test_evidence_drift_to_two_corroborators_aborts(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["ids"][0]).values(
            fact_value=json.dumps({
                "route": "HIGH", "confidence": "HIGH", "entity_type": "person",
                "entity_id": batch["p1"], "entity_name": f"Ada {_TAG}",
                "evidence": _evidence(f"Ada {_TAG}", email=True, address=True)})))
    _expect_drift(batch, match="plan has moved")


def test_ownership_drift_aborts(batch):
    other = _person("Interloper")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="no longer validate|plan has moved"):
        _apply(batch)
    for did in batch["ids"][1:]:
        assert _row(did)["person_id"] is None


@pytest.mark.parametrize("field,value", [
    ("archived", True), ("status", "deleted"), ("review_status", "pending"),
])
def test_lifecycle_and_review_status_drift_aborts(batch, field, value):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(**{field: value}))
    _expect_drift(batch)


# --- the successful apply ------------------------------------------------------

def test_successful_apply_changes_only_person_id_and_writes_one_audit_event_each(batch):
    before = {did: dict(_row(did)) for did in batch["ids"]}
    bystander = _doc(batch["p1"], "Ada", route="MEDIUM")
    bystander_before = dict(_row(bystander))

    report = _apply(batch)
    assert report["committed"] is True and report["applied"] == 3 and report["audit_rows"] == 3

    want = {r["document_id"]: r["person_id"] for r in batch["plan"]}
    for did in batch["ids"]:
        after, prior = dict(_row(did)), before[did]
        assert after["person_id"] == want[did]
        assert after["household_id"] is None and after["organization_id"] is None
        assert after["review_status"] == "not_required"
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"person_id", "updated_at", "updated_by_user_id"}, changed
    assert dict(_row(bystander)) == bystander_before

    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        rows = c.execute(select(audit.c.entity_id, audit.c.action)
                         .where(audit.c.request_id == _request_id(batch["sha"]))).all()
    assert len(rows) == 3
    assert {a for _, a in rows} == {"document.ownership_resolved"}


def test_apply_leaves_sources_and_ocr_untouched(batch):
    ocr = metadata.tables["document_ocr"]
    with engine.begin() as c:
        for did in batch["ids"]:
            c.execute(ocr.insert().values(document_id=did, status="completed", char_count=99))

    def fp():
        with engine.connect() as c:
            return (
                c.execute(text("select md5(string_agg(document_id::text||coalesce(source_uri,'')"
                               "||available::text,',' order by id)) from document_sources "
                               "where document_id=any(:i)"), {"i": batch["ids"]}).scalar(),
                c.execute(text("select md5(string_agg(document_id::text||coalesce(status,''),"
                               "',' order by id)) from document_ocr where document_id=any(:i)"),
                          {"i": batch["ids"]}).scalar())

    before = fp()
    _apply(batch)
    assert fp() == before
    with engine.begin() as c:
        c.execute(delete(ocr).where(ocr.c.document_id.in_(batch["ids"])))


def test_snapshot_is_written_before_any_write_and_names_the_corroborator(batch):
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    assert len(rows) == 3
    for r in rows:
        assert r["prior_person_id"] == ""              # captured BEFORE the assignment
        assert r["prior_review_status"] == "not_required"
        assert json.loads(r["prior_tags_json"])["source_system"] == "SharePoint"
        assert r["corroborator"] in b2.CORROBORATOR_KINDS
        assert r["independent_document_corroborator_count"] == "1"
        assert r["matching_folder"] and r["source_id"]
        assert int(r["destination_person_id"]) in (batch["p1"], batch["p2"])
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    assert meta["batch_id"] == "STRICT-SAFE-OWNERSHIP-2"
    assert meta["snapshot_sha256"] == ap.sha256_of(snap_dir / ap.SNAPSHOT_CSV)


# --- rollback on failure -------------------------------------------------------

def test_a_refused_assignment_rolls_back_every_earlier_row(batch, monkeypatch):
    from app.services.households import resolve_document_ownership as real_fn
    calls = {"n": 0}

    def flaky(document_id, **kw):
        calls["n"] += 1
        if calls["n"] == 3:                       # the LAST of the three rows refuses
            return {"document_id": document_id, "assigned": False, "reason": "no_longer_eligible"}
        return real_fn(document_id, **kw)

    monkeypatch.setattr("app.services.households.resolve_document_ownership", flaky)
    with pytest.raises(RuntimeError, match="was not assigned"):
        _apply(batch)
    assert calls["n"] == 3, "the first two rows must have been attempted before the failure"
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None
    assert _audit_rows(_request_id(batch["sha"])) == []


def test_a_late_invariant_failure_rolls_back_ownership_and_audit(batch, monkeypatch):
    """Every write is inside one transaction: a post-write check that fails undoes ALL of it."""
    real = ap._fingerprints
    calls = {"n": 0}

    def drifting(conn, ids):
        calls["n"] += 1
        result = real(conn, ids)
        if calls["n"] > 1:                        # the AFTER fingerprint disagrees
            result["sources"] = "tampered"
        return result

    monkeypatch.setattr(ap, "_fingerprints", drifting)
    with pytest.raises(RuntimeError, match="sources fingerprint changed"):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None, "ownership must not survive a failed invariant"
    assert _audit_rows(_request_id(batch["sha"])) == [], \
        "audit must not survive a failed invariant either"
    snap_dir = _snap_dir(batch)
    assert (snap_dir / ap.SNAPSHOT_CSV).is_file(), \
        "the snapshot is taken before the writes and survives the rollback"
    assert not (snap_dir / "apply_receipt.json").exists(), "a receipt is written only after commit"


# --- the batch 2 rollback ------------------------------------------------------

def _rollback(batch, **kw):
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return rb.run(_snap_dir(batch), **kw)


def _rollback_request_id(batch):
    return f"strict-safe-ownership-batch2-rollback:{ap.sha256_of(_snap_dir(batch) / ap.SNAPSHOT_CSV)[:12]}"


def _rewrite_snapshot(snap_dir, *, rows=None, batch_id="STRICT-SAFE-OWNERSHIP-2", columns=None):
    """Rewrite a snapshot in place, keeping manifest.json's recorded digest correct."""
    path = snap_dir / ap.SNAPSHOT_CSV
    existing = list(csv.DictReader(path.open(encoding="utf-8")))
    rows = existing if rows is None else rows
    columns = columns if columns is not None else list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    meta["batch_id"] = batch_id
    meta["snapshot_sha256"] = ap.sha256_of(path)
    (snap_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def test_rollback_confirmation_phrase_is_batch2_specific():
    """The reviewed batch's phrase, and one a batch 1 phrase can never satisfy."""
    from scripts import rollback_strict_safe_ownership as batch1_rb
    assert rb.confirm_phrase(52) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-2-52"
    assert batch1_rb.confirm_phrase(52) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-1-52"
    assert rb.confirm_phrase(52) != batch1_rb.confirm_phrase(52)
    assert rb.EXPECTED_BATCH_ID == b2.BATCH_ID == "STRICT-SAFE-OWNERSHIP-2"


def test_rollback_is_read_only_by_default(batch):
    _apply(batch)
    report = _rollback(batch)
    assert report["committed"] is False and report["restored"] == 0
    assert report["confirm_phrase"] == rb.confirm_phrase(3)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None, "a dry run must not revert anything"
    assert _audit_rows(_rollback_request_id(batch)) == []
    assert not (_snap_dir(batch) / "rollback_receipt.json").exists()


def test_rollback_requires_the_batch2_phrase_and_an_actor(batch):
    _apply(batch)
    from scripts import rollback_strict_safe_ownership as batch1_rb
    with pytest.raises(SystemExit, match="ROLLBACK-STRICT-SAFE-OWNERSHIP-2-3"):
        _rollback(batch, apply_changes=True, actor_user_id=7,
                  confirm=batch1_rb.confirm_phrase(3))
    with pytest.raises(SystemExit, match="--confirm"):
        _rollback(batch, apply_changes=True, actor_user_id=7)
    with pytest.raises(SystemExit, match="actor"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3))
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_restores_exact_prior_state_and_audits_as_batch2(batch):
    prior = {did: dict(_row(did)) for did in batch["ids"]}
    _apply(batch)
    request_id = _rollback_request_id(batch)

    report = _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=7)
    assert report["committed"] is True and report["restored"] == 3

    for did in batch["ids"]:
        now, was = _row(did), prior[did]
        assert now["person_id"] == was["person_id"] is None
        assert now["household_id"] == was["household_id"]
        assert now["organization_id"] == was["organization_id"]
        assert now["review_status"] == was["review_status"]
        assert now["tags"] == was["tags"]

    events = _audit_events(request_id)
    assert len(events) == 3
    assert {e["action"] for e in events} == {"document.ownership_rollback"}
    assert {int(e["entity_id"]) for e in events} == set(batch["ids"])
    assert {e["metadata"]["batch_id"] for e in events} == {"STRICT-SAFE-OWNERSHIP-2"}
    assert (_snap_dir(batch) / "rollback_receipt.json").is_file()


def test_rollback_refuses_a_tampered_snapshot(batch):
    _apply(batch)
    snap = _snap_dir(batch) / ap.SNAPSHOT_CSV
    snap.write_text(snap.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has been modified"):
        _rollback(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_refuses_a_batch1_snapshot_by_its_manifest(batch):
    _apply(batch)
    _rewrite_snapshot(_snap_dir(batch), batch_id="STRICT-SAFE-OWNERSHIP-1")
    with pytest.raises(SystemExit, match="not 'STRICT-SAFE-OWNERSHIP-2'"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=7)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_refuses_a_batch1_shaped_snapshot_by_its_columns(batch):
    """Even a manifest that CLAIMS batch 2: batch 1's snapshot carries corroborator_count."""
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    batch1_shaped = [{**{k: v for k, v in r.items()
                         if k not in b2.CORROBORATOR_KINDS and k != "corroborator"},
                      "corroborator_count": 2} for r in rows]
    _rewrite_snapshot(snap_dir, rows=batch1_shaped, columns=list(batch1_shaped[0]))
    with pytest.raises(SystemExit, match="batch 1 column"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=7)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_refuses_a_snapshot_outside_the_batch2_root(batch):
    """The default root is var/strict_safe_ownership_batch2; anything else needs a deliberate flag."""
    _apply(batch)
    with pytest.raises(SystemExit, match="not under the batch 2 snapshot root"):
        rb.run(_snap_dir(batch), out=lambda *_a, **_k: None)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_drift_blocks_the_entire_rollback(batch):
    _apply(batch)
    other = _person("Reassigned")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="drifted"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=7)
    assert _row(batch["ids"][0])["person_id"] == other
    for did in batch["ids"][1:]:
        assert _row(did)["person_id"] is not None, "no row may revert when the rollback aborts"
    assert _audit_rows(_rollback_request_id(batch)) == []


def test_rollback_scope_never_broadens_beyond_the_snapshot_ids(batch):
    """A document this batch never touched must survive the rollback exactly as it was."""
    bystander = _doc(batch["p1"], "Ada", route="MEDIUM")
    _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == bystander)
                  .values(person_id=batch["p1"]))
    before = dict(_row(bystander))
    _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=7)
    assert dict(_row(bystander)) == before, "the bystander is not in the snapshot and must not move"


def test_rollback_of_a_partial_snapshot_reverts_only_its_own_ids(batch):
    """Scope is the snapshot's ids, not "everything this batch might have touched"."""
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    kept = [r for r in rows if int(r["document_id"]) != batch["ids"][-1]]
    _rewrite_snapshot(snap_dir, rows=kept)
    report = _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(2), actor_user_id=7)
    assert report["restored"] == 2
    for did in batch["ids"][:-1]:
        assert _row(did)["person_id"] is None
    assert _row(batch["ids"][-1])["person_id"] is not None, "an id not in the snapshot is out of scope"
