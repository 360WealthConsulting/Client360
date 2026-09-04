"""Archetype 5 — the UNRESOLVED document set: Needs Review, never a plausible guess.

The other four archetypes assert that a document reaches the right client. This one asserts the
opposite and harder property: that a document the platform CANNOT place reaches nobody, stays
visible as unresolved work, and is never quietly filed onto whichever client looked most likely.

Three failures are in scope, and each has a real production precedent behind it:

  * **Silent misfiling.** A folder name, a filename or a shared phone number is not identity. The
    owner-proposal audit traced 2,157 HIGH proposals to one person because the firm's own switchboard
    number is printed on every letterhead it scans. Confidence gating for that lives in
    ``test_document_owner_proposal_safety``; this module asserts the acceptance-level consequence —
    an unresolved document keeps NULL anchors and appears on no client surface.
  * **Silent disappearance.** An unfiled document that nobody can see is worse than a misfiled one.
    It must stay on the admin worklist, with its folder, its file count and its candidate people.
  * **Silent resolution.** A folder that CAN be resolved is still not resolved by being read. The
    worklist reports what a resolution WOULD be; only the human confirm step writes it.

Ambiguity in the metadata is held to the same standard: disagreeing tax-year evidence files no year,
and two files that share a name but cannot be proven to be the same file are both retained and
flagged rather than merged.

Every assertion is a read. Nothing here proposes, applies or writes an owner.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    metadata,
    people,
    relationship_entities,
)
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.documents_screen import build as build_screen
from app.services.client360.household import get_household_workspace
from app.services.client360.sections import documents_view_model, enrich_documents
from app.services.document_platform.relationships import client_documents
from app.services.documents import get_person_documents
from app.services.households import unresolved_taxdome_folders

_TAG = "UNRESOLV"
_CAPS = frozenset({"client.read", "client.write", "record.read_all", "documents.view",
                   "timeline.read", "tax.read"})
_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_ROOT = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual"
#: A folder that names nobody in the canonical people table — the genuinely ambiguous case.
_AMBIGUOUS_FOLDER = f"SCANS TO SORT {_TAG}"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            docs = list(c.scalars(select(documents.c.id).where(
                documents.c.stored_name.like(f"%{_TAG}%"))))
            if docs:
                ds = metadata.tables["document_sources"]
                c.execute(ds.delete().where(ds.c.document_id.in_(docs)))
                c.execute(documents.delete().where(documents.c.id.in_(docs)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(f"%{_TAG}%"))))
            if ents:
                c.execute(delete(relationship_entities)
                          .where(relationship_entities.c.id.in_(ents)))
            if pids:
                c.execute(delete(household_relationships)
                          .where(household_relationships.c.person_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


# --- fixture builders ---------------------------------------------------------------------------

def _household(name):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{_TAG} {name}")
                         .returning(households.c.id)).scalar_one()


def _person(first, last, household_id=None):
    """The tag rides on the SURNAME so the canonical name key stays a real two-token name — the
    TaxDome folder resolver matches exact token sets, and a tag appended after the surname would
    make every fixture person unmatchable for the wrong reason."""
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name=first, last_name=f"{last}{_TAG}", full_name=f"{first} {last}{_TAG}",
            active=True, household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            c.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member",
                is_primary=True, is_primary_household=True))
    return pid


def _taxdome_doc(name, folder, *, person_id=None, household_id=None, organization_id=None,
                 status="active"):
    """An unresolved TaxDome-sourced document: the shape the admin worklist reads."""
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"taxdome-{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{uuid.uuid4().hex}", storage_provider="Client360 Local",
            size_bytes=1024, sha256=uuid.uuid4().hex * 2,
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            status=status, archived=False, current_version=1,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder},
        ).returning(documents.c.id)).scalar_one()


def _sp_doc(name, *, folder, folder_year=None, person_id=None, household_id=None,
            organization_id=None, category=None, review_status="none", sha=None,
            item_id=None, size_bytes=1024):
    parts = [folder]
    if folder_year:
        parts.append(str(folder_year))
    web_url = f"{_SITE}/{'/'.join(parts)}/{name.replace(' ', '%20')}"
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{_TAG}-{uuid.uuid4().hex}",
            storage_path=f"/x/{uuid.uuid4().hex}", storage_provider="Client360 Local",
            size_bytes=size_bytes, sha256=sha or uuid.uuid4().hex * 2,
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            status="active", archived=False, category=category, review_status=review_status,
            current_version=1,
            tags={"source_system": "SharePoint", "web_url": web_url},
        ).returning(documents.c.id)).scalar_one()
        ds = metadata.tables["document_sources"]
        c.execute(ds.insert().values(
            document_id=did, source_system="SharePoint", source_uri=web_url,
            source_path="/".join(parts), source_external_id=item_id or uuid.uuid4().hex,
            available=True))
    return did


def _principal():
    return Principal(0, "staff@e.test", "Staff", _CAPS)


def _enriched(rows):
    """The SAME enrichment chain the Documents tab builds (client360.sections.documents), so a row a
    test inspects is the row staff actually see — source references, OCR state, classification and
    version-family grouping included. Rebuilding a shorter chain here would test a shape that no
    screen ever renders."""
    from app.services.client360.sections import (
        _attach_classification,
        _attach_ocr,
        _attach_source_refs,
        _attach_version_family,
    )
    return _attach_version_family(
        _attach_classification(_attach_ocr(_attach_source_refs(enrich_documents(rows)))))


def _unfiled_screen(document_ids):
    """The staff Documents screen built over unfiled rows, exactly as the shaping layer would see
    them — no member names and no household, because these documents belong to no client."""
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(
            select(documents).where(documents.c.id.in_(document_ids))).mappings()]
    return build_screen(_enriched(rows), member_names={}, household_name=None)


def _folder_entry(folder):
    return next((f for f in unresolved_taxdome_folders(limit=1000) if f["folder"] == folder), None)


# --- A. an unresolved document reaches no client -------------------------------------------------

def test_an_unanchored_document_appears_on_no_client_surface():
    """The containment property. A document with all three anchors NULL belongs to the firm's
    resolution queue, and to no person, household or business."""
    hid = _household("Bergstrom Household")
    person = _person("Anders", "Bergstrom", hid)
    biz_name = f"Bergstrom Holdings {_TAG}"
    with engine.begin() as c:
        biz = c.execute(relationship_entities.insert().values(
            entity_type="business", name=biz_name, active=True)
            .returning(relationship_entities.c.id)).scalar_one()

    unfiled = _sp_doc("Unknown Scan 0042.pdf", folder=f"{_ROOT}/UNSORTED", folder_year=2023)

    assert unfiled not in {r["id"] for r in
                           get_workspace(_principal(), person_id=person)
                           ["sections"]["documents"]["documents"]}
    assert unfiled not in {r["id"] for r in
                           get_household_workspace(_principal(), hid)
                           ["sections"]["documents"]["documents"]}
    assert unfiled not in {r["id"] for r in client_documents(_principal(), "organization", biz)}
    assert unfiled not in {d["id"] for d in get_person_documents(person)}


def test_a_filename_naming_a_real_client_does_not_file_the_document_to_them():
    """Ownership is never read out of a filename. A scan literally titled with a client's name, with
    no anchor written, stays off that client's page — this is the H13 property at the client
    surface."""
    hid = _household("Calloway Household")
    person = _person("Marisol", "Calloway", hid)
    named = _sp_doc(f"Marisol Calloway{_TAG} 2023 Return.pdf",
                    folder=f"{_ROOT}/CALLOWAY{_TAG},%20MARISOL", folder_year=2023)
    assert named not in {r["id"] for r in
                         get_workspace(_principal(), person_id=person)
                         ["sections"]["documents"]["documents"]}
    assert named not in {d["id"] for d in get_person_documents(person)}
    with engine.connect() as c:
        row = c.execute(select(documents.c.person_id, documents.c.household_id,
                               documents.c.organization_id)
                        .where(documents.c.id == named)).mappings().one()
    assert row["person_id"] is None and row["household_id"] is None
    assert row["organization_id"] is None


# --- B. it is visibly unresolved, not visibly settled --------------------------------------------

def test_an_unfiled_row_says_it_is_unfiled():
    unfiled = _sp_doc("Unknown Scan 0043.pdf", folder=f"{_ROOT}/UNSORTED", folder_year=2023)
    row = _unfiled_screen([unfiled])["rows"][0]
    assert row["related_to"]["kind"] == "none"
    assert row["related_to"]["label"] == "Unfiled"
    assert row["related_to"]["id"] is None


def test_an_unfiled_row_is_actionable_review_work():
    """``owner_missing`` is an ACTIONABLE reason — one document, one decision, a person resolves it.
    It must not be filed away under incomplete metadata, which is bulk work."""
    unfiled = _sp_doc("Unknown Scan 0044.pdf", folder=f"{_ROOT}/UNSORTED", folder_year=2023)
    screen = _unfiled_screen([unfiled])
    row = screen["rows"][0]
    assert "owner_missing" in {r["key"] for r in row["actionable"]}
    assert "owner_missing" not in {r["key"] for r in row["incomplete"]}
    assert screen["actionable_count"] == 1


def test_an_unreviewed_document_is_never_presented_as_filed():
    """A NULL review status means "not reviewed", which reads as "—". Inventing a settled-looking
    label would be the screen asserting something no column says."""
    unfiled = _sp_doc("Unknown Scan 0045.pdf", folder=f"{_ROOT}/UNSORTED")
    row = _unfiled_screen([unfiled])["rows"][0]
    assert row["status"]["label"] == "—"
    assert row["status"]["kind"] == "none"


def test_an_unfiled_document_still_shows_a_name_and_a_way_to_open_it():
    """Unresolved is not unusable: the reviewer has to be able to read and open the document in
    order to resolve it."""
    unfiled = _sp_doc("Unknown Scan 0046.pdf", folder=f"{_ROOT}/UNSORTED", folder_year=2023)
    row = _unfiled_screen([unfiled])["rows"][0]
    assert row["name"] == "Unknown Scan 0046.pdf"
    assert row["download_url"] == f"/documents/{unfiled}/download"
    assert row["source_folder"].endswith("/2023")


# --- C. it stays on the admin worklist ------------------------------------------------------------

def test_an_unresolvable_folder_is_reported_as_unresolved_work():
    _taxdome_doc("a.pdf", _AMBIGUOUS_FOLDER)
    _taxdome_doc("b.pdf", _AMBIGUOUS_FOLDER)
    entry = _folder_entry(_AMBIGUOUS_FOLDER)
    assert entry is not None, "an unresolved folder must reach the admin worklist"
    assert entry["files"] == 2
    assert entry["resolves_to"] == {"household_id": None, "person_id": None}


def test_a_resolvable_folder_is_reported_but_not_applied():
    """The worklist says what a resolution WOULD be. Reading it must not write one — resolution
    still flows through the admin preview -> confirm endpoint."""
    person = _person("Ingeborg", "Vantongeren")
    folder = f"VANTONGEREN{_TAG}, INGEBORG"
    did = _taxdome_doc("return.pdf", folder)
    entry = _folder_entry(folder)
    assert entry is not None
    assert entry["resolves_to"]["person_id"] == person
    with engine.connect() as c:
        row = c.execute(select(documents.c.person_id, documents.c.household_id)
                        .where(documents.c.id == did)).mappings().one()
    assert row["person_id"] is None and row["household_id"] is None, "reading must not file"
    assert did not in {d["id"] for d in get_person_documents(person)}


def test_candidate_people_are_offered_as_suggestions_only():
    person = _person("Ingeborg", "Vantongeren")
    folder = f"VANTONGEREN{_TAG}, INGEBORG"
    _taxdome_doc("return.pdf", folder)
    entry = _folder_entry(folder)
    assert person in {s["id"] for s in entry["suggestions"]}


def test_a_document_that_has_been_filed_leaves_the_worklist():
    """The queue is a live re-evaluation, not a stored list: once a document carries an anchor it is
    no longer unresolved work."""
    hid = _household("Sorted Household")
    folder = f"SORTED FOLDER {_TAG}"
    _taxdome_doc("filed.pdf", folder, household_id=hid)
    assert _folder_entry(folder) is None


def test_the_client_documents_tab_does_not_host_the_resolution_queue():
    """Unassigned documents are resolved in Admin -> Document Management, before they reach a
    client. A client's Documents tab shows what that client owns and nothing else."""
    view = documents_view_model([])
    assert "unassigned" not in view
    assert "documents" in view


# --- D. ambiguous metadata is left ambiguous ------------------------------------------------------

def test_disagreeing_year_evidence_files_no_year():
    """The filename says 2021 and the folder says 2023. Neither is filed, and the disagreement
    becomes actionable review work rather than a coin flip."""
    did = _sp_doc("2021 Return Copy.pdf", folder=f"{_ROOT}/UNSORTED", folder_year=2023)
    row = _unfiled_screen([did])["rows"][0]
    assert row["tax_year"] is None
    assert row["tax_year_confidence"] == "conflict"
    assert "tax_year_conflict" in {r["key"] for r in row["actionable"]}


def test_look_alikes_that_cannot_be_resolved_are_both_kept_and_flagged():
    """Same filename, different content, no shared source item. Merging them on the strength of a
    filename would destroy a real document, so both are retained and the ambiguity is surfaced."""
    folder = f"{_ROOT}/UNSORTED"
    a = _sp_doc("Death Certificate.pdf", folder=folder, sha="1" * 64, size_bytes=1000)
    b = _sp_doc("Death Certificate.pdf", folder=folder, sha="2" * 64, size_bytes=2000)
    rows = {r["id"]: r for r in _unfiled_screen([a, b])["rows"]}
    assert a in rows and b in rows
    assert rows[a]["needs_version_review"] and rows[b]["needs_version_review"]
    assert rows[a]["version_family_size"] == 1
    assert "version_ambiguous" in {r["key"] for r in rows[a]["actionable"]}


def test_a_missing_category_is_incomplete_metadata_not_a_guessed_category():
    """No filed category means the row says so. It never acquires one by inference."""
    did = _sp_doc("Unlabelled Scan.pdf", folder=f"{_ROOT}/UNSORTED")
    row = _unfiled_screen([did])["rows"][0]
    assert "category_missing" in {r["key"] for r in row["incomplete"]}
    with engine.connect() as c:
        assert c.execute(select(documents.c.category)
                         .where(documents.c.id == did)).scalar_one() is None


# --- E. the automatic-filing gate -----------------------------------------------------------------

def test_a_name_alone_can_never_authorise_an_automatic_owner():
    """The acceptance-level statement of the confidence gate: a name in a document, without proof
    that the named record is a client whose documents the firm files, is a lead for a human — never
    a HIGH proposal. The full tier matrix is asserted in test_document_owner_proposal_safety."""
    from app.services.document_owner_proposal import _confidence
    assert _confidence({"name"}, unique_name=True, owner_eligible=False) != "HIGH"
    assert _confidence({"name", "phone"}, unique_name=True, shared={"phone"},
                       owner_eligible=True) != "HIGH"


def test_evidence_that_only_identifies_the_firm_cannot_carry_an_automatic_write():
    """A phone number or ZIP printed on the firm's own letterhead identifies the firm, not a client.
    Such a proposal stays reviewable by a person and is never written automatically."""
    from app.services.document_high_validation import has_only_weak_shared_evidence
    # Evidence reaches the validator as the proposal's own human-readable strings.
    assert has_only_weak_shared_evidence({"evidence": ["phone 555-123-4567 matched"]})
    assert has_only_weak_shared_evidence({"evidence": ["zip 55416 matched", "phone matched"]})
    assert has_only_weak_shared_evidence({"evidence": []})
    # A name is IDENTIFYING evidence, so a proposal carrying one is not weak-shared-only.
    assert not has_only_weak_shared_evidence(
        {"evidence": ["exact name match", "phone 555-123-4567 matched"]})


# --- F. lifecycle -----------------------------------------------------------------------------

def test_a_deleted_unresolved_document_does_not_linger_on_a_client_surface():
    hid = _household("Deleted Household")
    person = _person("Toma", "Ilyichev", hid)
    did = _sp_doc("Gone.pdf", folder=f"{_ROOT}/UNSORTED", household_id=hid)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == did)
                  .values(status="deleted", deleted_at=datetime.now(UTC)))
    names = {r["original_name"] for r in
             get_workspace(_principal(), person_id=person)["sections"]["documents"]["documents"]}
    assert "Gone.pdf" not in names


def test_the_unresolved_worklist_reports_the_folder_it_actually_read():
    """The queue's folder key is the recorded ``taxdome_folder`` tag, never a path this app
    reconstructed — a reviewer navigates to it by hand."""
    _taxdome_doc("x.pdf", _AMBIGUOUS_FOLDER)
    entry = _folder_entry(_AMBIGUOUS_FOLDER)
    assert entry["folder"] == _AMBIGUOUS_FOLDER
