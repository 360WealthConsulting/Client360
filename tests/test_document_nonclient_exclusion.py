"""Non-client exclusion lane — the allow-list must fail closed.

The audit that produced this batch found that the obvious rule ("exclude whatever
``is_intelligence_eligible`` rejects") would have swept in 252 REAL client documents: tax forms
saved with no extension, .zip statement archives, Apple .numbers workbooks, and QuickBooks/Access
company files. Every negative test below is one of those families, pinned so the lane can never
quietly widen into client data.

The positive tests cover each approved family and the three individually named artifacts.

Temp rows only, all tagged, all cleaned up.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import documents, engine, metadata
from app.services import document_nonclient_exclusion as nx
from app.services.document_platform.lifecycle import (
    active_documents_clause,
    excluded_nonclient_clause,
    not_excluded_nonclient_clause,
)
from app.services.document_review_inbox import inbox_summary

_TAG = f"NCX{uuid.uuid4().hex[:6]}"


@pytest.fixture(autouse=True)
def _clean():
    yield
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"ncx:{_TAG}%")))]
        if ids:
            c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _doc(name, *, route="UNSUPPORTED", content_type=None, person_id=None,
         review_status="not_required") -> int:
    """One live unowned document plus, unless route is None, a current owner_proposal fact."""
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"ncx:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local",
            storage_uri=f"/x/{name}", size_bytes=10, sha256=uuid.uuid4().hex * 2,
            person_id=person_id, status="active", archived=False,
            content_type=content_type, review_status=review_status, current_version=1,
            tags={"source_system": "SharePoint"},
        ).returning(documents.c.id)).scalar_one()
        if route is not None:
            facts = metadata.tables["document_facts"]
            c.execute(facts.insert().values(
                document_id=did, fact_type="owner_proposal",
                fact_value=json.dumps({"route": route}), confidence=0.0,
                extraction_engine="owner_proposal", extractor_version="test",
                version=1, is_current=True))
    return did


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.review_status, documents.c.tags,
                                documents.c.person_id, documents.c.status,
                                documents.c.archived, documents.c.sha256, documents.c.storage_uri)
                         .where(documents.c.id == did)).mappings().one()


# --- the allow-list, as a pure function ---------------------------------------

@pytest.mark.parametrize("ext", sorted(nx.APPROVED_EXTENSIONS))
def test_every_approved_extension_family_matches(ext):
    assert nx.exclusion_rule(1, f"asset.{ext}") == nx.REASON_TECHNICAL_EXTENSION


@pytest.mark.parametrize("name", [
    "xtree.js.download", "ui.js.download", "uswds.min.js.download", "site.css.download",
    "logo.png.download", "index.html.download", "sprite.svg.download", "font.woff2.download",
    "data.json.download", "app.map.download",
])
def test_web_asset_downloads_match(name):
    assert nx.exclusion_rule(1, name) == nx.REASON_WEB_ASSET_DOWNLOAD


@pytest.mark.parametrize("did,name", sorted(nx.APPROVED_ARTIFACT_DOCUMENTS.items()))
def test_the_three_named_artifacts_match_by_id_and_name(did, name):
    assert nx.exclusion_rule(did, name) == nx.REASON_NAMED_ARTIFACT
    assert nx.exclusion_rule(did, name.upper()) == nx.REASON_NAMED_ARTIFACT


def test_a_named_artifact_id_carrying_a_different_name_is_refused():
    """id and filename must BOTH agree — neither alone may classify a row."""
    assert nx.exclusion_rule(17155, "2019 Tax Return.pdf") is None


def test_a_thumbs_db_at_some_other_id_is_not_matched_by_rule_b():
    """Rule B is three audited rows, not a name pattern."""
    assert nx.exclusion_rule(999999, "Thumbs.db") is None


# --- the client-document families that must NEVER be excluded -----------------

@pytest.mark.parametrize("name", [
    "2019 Tax Return.pdf",
    "Cleaning Solutions of Roanoke Operating Agreement.doc",
    "Acronyms.docx",
    "transactions.csv",
    "client message.msg",
    "Tirian Powerpoint.pptx",
    "360BudgetToolV1.0.xlsb",
    "USA_941_2020_2",                       # no-extension tax document
    "2021 1099-G",                          # no-extension tax document
    "Apr 12, 2019 to May 10, 2019.zip",
    "Medical Mileage 2023.numbers",
    "CompanyFile.qbw",
    "CompanyBackup.qbb",
    "clientdata.mdb",
    "statement.oxps",
    "yearend.rpt",
    "arbitrary.download",                   # NOT a web asset — content unknown
    "report.pdf.download",                  # second-level suffix is not a web family
])
def test_client_document_families_are_never_excludable(name):
    assert nx.exclusion_rule(1, name) is None


def test_an_unknown_extension_fails_closed():
    assert nx.exclusion_rule(1, "mystery.wibble") is None


def test_a_bare_name_fails_closed():
    assert nx.exclusion_rule(1, "scan") is None


# --- eligibility against the database ----------------------------------------

def test_an_approved_artifact_is_eligible_and_excludes():
    did = _doc("uswds.min.js.download")
    result = nx.exclude_document(did)
    assert result["excluded"] is True
    assert result["reason"] == nx.REASON_WEB_ASSET_DOWNLOAD
    row = _row(did)
    assert row["review_status"] == nx.EXCLUDED_REVIEW_STATUS
    assert row["tags"][nx.TAGS_KEY]["reason"] == nx.REASON_WEB_ASSET_DOWNLOAD
    assert row["tags"][nx.TAGS_KEY]["excluded_at"]
    assert row["tags"]["source_system"] == "SharePoint"       # other tags survive


def test_a_client_document_is_refused_and_left_untouched():
    did = _doc("2019 Tax Return.pdf")
    before = _row(did)

    result = nx.exclude_document(did)
    after = _row(did)

    assert result["excluded"] is False
    assert result["outcome"] == "not_an_approved_artifact"
    assert after["review_status"] == "not_required"
    assert nx.TAGS_KEY not in (after["tags"] or {})
    assert after["sha256"] == before["sha256"]                # provenance untouched
    assert after["storage_uri"] == before["storage_uri"]      # file never moved


def test_exclusion_changes_no_lifecycle_state():
    did = _doc("helpfile.hlp")
    nx.exclude_document(did)
    row = _row(did)
    assert row["status"] == "active"                          # not a lifecycle change
    assert row["archived"] is False                           # not archived
    assert row["person_id"] is None                           # no owner invented
    with engine.connect() as c:                               # still a live document for staff
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, active_documents_clause())).scalar() == did


def test_a_non_unsupported_route_is_refused_even_for_an_approved_extension():
    """A .html that produced a real proposal is a proposal to read, not an artifact to sweep."""
    did = _doc("page.html", route="HIGH")
    result = nx.exclude_document(did)
    assert result["excluded"] is False
    assert result["outcome"] == "route_not_excludable"
    assert _row(did)["review_status"] == "not_required"


def test_an_owned_document_cannot_be_excluded():
    from app.db import people
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Owner", last_name=_TAG, full_name=f"Owner {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()
    did = _doc("asset.css", person_id=pid)
    try:
        result = nx.exclude_document(did)
        assert result["excluded"] is False
        assert result["outcome"] == "already_owned"
    finally:
        with engine.begin() as c:
            c.execute(delete(documents).where(documents.c.id == did))
            c.execute(delete(people).where(people.c.id == pid))


def test_a_document_carrying_real_review_state_is_refused():
    did = _doc("asset.css", review_status="pending")
    result = nx.exclude_document(did)
    assert result["excluded"] is False
    assert result["outcome"] == "review_status_not_excludable"
    assert _row(did)["review_status"] == "pending"


def test_a_mislabelled_reason_is_refused():
    did = _doc("asset.ttf")
    with pytest.raises(nx.ExclusionError):
        nx.exclude_document(did, reason=nx.REASON_NAMED_ARTIFACT)
    assert _row(did)["review_status"] == "not_required"


def test_an_unapproved_reason_raises():
    did = _doc("asset.ttf")
    with pytest.raises(nx.ExclusionError):
        nx.exclude_document(did, reason="because_i_said_so")
    assert _row(did)["review_status"] == "not_required"


# --- reversibility and idempotence -------------------------------------------

def test_excluding_twice_is_a_no_op():
    did = _doc("asset.lnk")
    assert nx.exclude_document(did)["excluded"] is True
    second = nx.exclude_document(did)
    assert second["excluded"] is False and second["outcome"] == "already_excluded"


def test_restore_reverses_the_classification_and_is_idempotent():
    did = _doc("asset.prf")
    nx.exclude_document(did)

    first = nx.restore_document(did)
    second = nx.restore_document(did)

    assert first["restored"] is True
    assert second["restored"] is False and second["outcome"] == "not_excluded"
    row = _row(did)
    assert row["review_status"] == nx.RESTORED_REVIEW_STATUS
    assert nx.TAGS_KEY not in row["tags"]
    assert row["tags"]["source_system"] == "SharePoint"


def test_dry_run_writes_nothing():
    did = _doc("asset.xsl")
    result = nx.exclude_document(did, dry_run=True)
    assert result["outcome"] == "would_exclude"
    assert _row(did)["review_status"] == "not_required"


# --- the lifecycle clauses ----------------------------------------------------

def test_excluded_and_not_excluded_clauses_are_complementary():
    did = _doc("asset.config")
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, excluded_nonclient_clause())).scalar() is None
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, not_excluded_nonclient_clause())).scalar() == did
    nx.exclude_document(did)
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, excluded_nonclient_clause())).scalar() == did
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, not_excluded_nonclient_clause())).scalar() is None


def test_the_exclusion_sentinel_is_distinct_from_the_deferral_sentinel():
    """Two different facts must not collapse into one queue state."""
    from app.services.document_deferral import DEFERRED_REVIEW_STATUS
    assert nx.EXCLUDED_REVIEW_STATUS != DEFERRED_REVIEW_STATUS


def test_counts_group_by_reason():
    nx.exclude_document(_doc("a.css"))
    nx.exclude_document(_doc("b.js.download"))
    counts = nx.excluded_counts()
    assert counts["total"] >= 2
    assert counts.get(nx.REASON_TECHNICAL_EXTENSION, 0) >= 1
    assert counts.get(nx.REASON_WEB_ASSET_DOWNLOAD, 0) >= 1


# --- the four operational states, as the queue metrics see them ---------------
#
# FILED / DEFERRED_OWNERSHIP / EXCLUDED_NONCLIENT / ACTIONABLE_REVIEW must be mutually
# understandable: exactly one of them describes any given unowned document, and only
# ACTIONABLE_REVIEW is work. These tests pin that against inbox_summary(), the counter the
# staff review inbox and the completion metric both read.

def test_an_ordinary_unowned_document_counts_as_actionable():
    """A. the baseline the other two states are measured against."""
    before = inbox_summary()
    did = _doc("2019 Tax Return.pdf")
    after = inbox_summary()
    assert after["unassigned_documents"] == before["unassigned_documents"] + 1
    assert after["unassigned_total"] == before["unassigned_total"] + 1
    assert did


def test_a_deferred_document_does_not_count_as_actionable():
    """B. deferral removes work from the queue without removing the document."""
    from app.services import document_deferral as dd
    did = _doc("statement.pdf", route="NO_MATCH")
    before = inbox_summary()
    dd.defer_document(did)
    after = inbox_summary()
    assert after["unassigned_documents"] == before["unassigned_documents"] - 1
    assert after["deferred_ownership"] == before["deferred_ownership"] + 1
    assert after["unassigned_total"] == before["unassigned_total"]      # still unowned


def test_an_excluded_nonclient_document_does_not_count_as_actionable():
    """C. the whole point of Batch 1."""
    did = _doc("uswds.min.js.download")
    before = inbox_summary()
    nx.exclude_document(did)
    after = inbox_summary()
    assert after["unassigned_documents"] == before["unassigned_documents"] - 1
    assert after["excluded_nonclient"] == before["excluded_nonclient"] + 1
    assert after["excluded_by_reason"].get(nx.REASON_WEB_ASSET_DOWNLOAD, 0) >= 1
    assert after["unassigned_total"] == before["unassigned_total"]      # still unowned


def test_an_excluded_document_is_still_retrievable_by_staff():
    """D. the classification withdraws work, never access."""
    from app.security.models import Principal
    from app.services.document_platform.service import list_documents
    did = _doc("vendor.css")
    nx.exclude_document(did)
    staff = Principal(1, "staff@t", "Staff",
                      frozenset({"documents.view", "record.read_all", "client.read"}))
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(
            documents.c.id == did, active_documents_clause())).scalar() == did
    found = list_documents(staff, search="vendor.css")
    assert any(d["id"] == did for d in found["rows"]), \
        "an excluded artifact must remain retrievable through the staff document library"


def test_an_excluded_document_is_not_archived_not_deleted_and_has_no_owner():
    """E, F, G — the three things this must never become."""
    did = _doc("helpfile.hlp")
    nx.exclude_document(did)
    row = _row(did)
    assert row["archived"] is False
    assert row["status"] == "active"
    assert row["person_id"] is None
    with engine.connect() as c:
        deleted_at = c.execute(select(documents.c.deleted_at)
                               .where(documents.c.id == did)).scalar()
    assert deleted_at is None


def test_restoring_returns_the_document_to_the_actionable_queue():
    """H. reversibility measured at the metric, not just the column."""
    did = _doc("asset.map")
    before = inbox_summary()
    nx.exclude_document(did)
    assert inbox_summary()["unassigned_documents"] == before["unassigned_documents"] - 1
    nx.restore_document(did)
    after = inbox_summary()
    assert after["unassigned_documents"] == before["unassigned_documents"]
    assert after["excluded_nonclient"] == before["excluded_nonclient"]


def test_deferred_and_excluded_are_counted_as_distinct_states():
    """I. two parked states, never conflated."""
    from app.services import document_deferral as dd
    deferred_doc = _doc("deferred.pdf", route="NO_MATCH")
    excluded_doc = _doc("theme.css")
    before = inbox_summary()
    dd.defer_document(deferred_doc)
    nx.exclude_document(excluded_doc)
    after = inbox_summary()
    assert after["deferred_ownership"] == before["deferred_ownership"] + 1
    assert after["excluded_nonclient"] == before["excluded_nonclient"] + 1
    assert after["unassigned_documents"] == before["unassigned_documents"] - 2
    # and neither lane claims the other's row
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(
            documents.c.id == excluded_doc, excluded_nonclient_clause())).scalar() == excluded_doc
        assert c.execute(select(documents.c.id).where(
            documents.c.id == deferred_doc, excluded_nonclient_clause())).scalar() is None


def test_raw_document_totals_do_not_change():
    """J. classification is not deletion — total_documents must be untouched."""
    did = _doc("fonts.ttf")
    before = inbox_summary()
    nx.exclude_document(did)
    after = inbox_summary()
    assert after["total_documents"] == before["total_documents"]


def test_the_folder_worklist_stops_counting_an_excluded_artifact():
    """The TaxDome resolve worklist is an ownership queue, so artifacts leave it too."""
    from app.services.households import unresolved_taxdome_folders
    folder = f"{_TAG} folder"
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name="Thumbs.db", stored_name=f"ncx:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/Thumbs.db",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status="active", archived=False,
            review_status="not_required", current_version=1,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder},
        ).returning(documents.c.id)).scalar_one()

    def files_for(name):
        return next((f["files"] for f in unresolved_taxdome_folders() if f["folder"] == name), 0)

    before = files_for(folder)
    with engine.begin() as c:                       # exclude via the named-artifact rule's shape
        c.execute(documents.update().where(documents.c.id == did).values(
            review_status=nx.EXCLUDED_REVIEW_STATUS))
    assert files_for(folder) == before - 1
