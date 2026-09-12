"""The publication policy and the read-only corpus preview.

The policy tests pin the three rules the module exists to enforce: only an explicit classification
can propose client-visible, a filename may only push toward staff-only, and source system is not an
input at all. The preview tests pin band precedence and, most importantly, that running the preview
writes nothing.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import documents, engine, households, metadata, people
from app.services.publication import policy
from app.services.publication import preview as corpus

document_sources = metadata.tables.get("document_sources")


# --- policy: only an explicit classification can propose client-visible ------

@pytest.mark.parametrize("doc_type", ["1040", "1065", "1120S", "1041", "760X", "1040-X"])
def test_filed_returns_are_proposed_client_visible(doc_type):
    assert policy.propose(document_type=doc_type, original_name="x.pdf") == policy.CLIENT_VISIBLE


@pytest.mark.parametrize("doc_type", ["W-2", "1099", "1099-R", "1098-T", "K-1", "SSA-1099", "1095-C"])
def test_source_documents_are_proposed_client_visible(doc_type):
    assert policy.propose(document_type=doc_type, original_name="x.pdf") == policy.CLIENT_VISIBLE


def test_organizers_and_accepted_acknowledgements_are_proposed_client_visible():
    assert policy.propose(document_type="organizer") == policy.CLIENT_VISIBLE
    assert policy.propose(document_type="efile_acknowledgement") == policy.CLIENT_VISIBLE


def test_type_matching_is_case_insensitive():
    assert policy.propose(document_type="w-2") == policy.CLIENT_VISIBLE
    assert policy.propose(document_type="W-2") == policy.CLIENT_VISIBLE


@pytest.mark.parametrize("doc_type", ["workpaper", "diagnostic", "preparer_note", "draft_return",
                                      "efile_rejection", "duplicate", "administrative"])
def test_preparer_and_administrative_types_are_proposed_staff_only(doc_type):
    assert policy.propose(document_type=doc_type, original_name="x.pdf") == policy.STAFF_ONLY


@pytest.mark.parametrize("doc_type", [None, "", "unknown", "unclassified", "other",
                                      "some_type_nobody_registered"])
def test_unclassified_and_unrecognized_types_are_review_required(doc_type):
    assert policy.propose(document_type=doc_type, original_name="2024 Tax Return.pdf") \
        == policy.REVIEW_REQUIRED


def test_a_suggestive_filename_never_produces_client_visible():
    """Rule 1. "Probably a filed return" is not a basis for showing a document to a client."""
    assert policy.propose(document_type=None,
                          original_name="2024 1040 (EXAMPLE TAXPAYER AND SPOUSE).pdf") \
        == policy.REVIEW_REQUIRED


@pytest.mark.parametrize("name", [
    "2024 Tax Return Mock Up (EXAMPLE TAXPAYER).pdf",
    "2021 -For Paper Filing- Documents.pdf",
    "1040 workpaper.pdf",
    "Return DRAFT v3.pdf",
    "diagnostics report.pdf",
])
def test_a_staff_only_filename_marker_overrides_a_client_visible_type(name):
    """Withholding is the recoverable direction, so the marker wins even over an explicit 1040."""
    assert policy.propose(document_type="1040", original_name=name) == policy.STAFF_ONLY


def test_sensitive_types_stay_review_required_by_design():
    for doc_type in ("drivers_license", "bank_statement", "brokerage_statement",
                     "irs_notice", "insurance_policy"):
        assert policy.propose(document_type=doc_type) == policy.REVIEW_REQUIRED


def test_source_system_is_not_an_input_to_the_policy():
    """Rule 2, asserted against the signature itself: there is no parameter to pass it through."""
    import inspect as _inspect

    params = set(_inspect.signature(policy.propose).parameters)
    assert params == {"document_type", "original_name"}
    assert "source_system" not in params


# --- preview banding ---------------------------------------------------------

def _row(**kwargs):
    base = {"audience_span": 1, "audience_key": "person:1", "doc_type": None,
            "original_name": "x.pdf"}
    return {**base, **kwargs}


def test_cross_client_conflict_outranks_a_clean_classification():
    """A well-classified 1040 whose content is attributed to two audiences is still a conflict."""
    assert corpus.band_for(_row(audience_span=2, doc_type="1040")) == corpus.CROSS_CLIENT_CONFLICT


def test_missing_audience_outranks_the_policy_bands():
    assert corpus.band_for(_row(audience_key=None, doc_type="1040")) == corpus.MISSING_AUDIENCE


def test_conflict_outranks_missing_audience():
    assert corpus.band_for(_row(audience_span=2, audience_key=None)) == corpus.CROSS_CLIENT_CONFLICT


def test_clean_rows_fall_through_to_the_policy():
    assert corpus.band_for(_row(doc_type="W-2")) == policy.CLIENT_VISIBLE
    assert corpus.band_for(_row(doc_type="workpaper")) == policy.STAFF_ONLY
    assert corpus.band_for(_row(doc_type=None)) == policy.REVIEW_REQUIRED


def test_every_band_is_declared():
    assert set(corpus.BANDS) == {
        corpus.CROSS_CLIENT_CONFLICT, corpus.MISSING_AUDIENCE,
        policy.CLIENT_VISIBLE, policy.STAFF_ONLY, policy.REVIEW_REQUIRED}


# --- preview against real rows ----------------------------------------------

class _Corpus:
    """A small synthetic corpus in the real tables, tagged with its own source system so the
    preview's source filter isolates it from anything else in the database."""

    SOURCE = None

    def __init__(self):
        self.suffix = uuid.uuid4().hex[:10]
        self.SOURCE = f"PreviewTest-{self.suffix}"
        self.people, self.households, self.documents = [], [], []

    def person(self, *, active=True):
        with engine.begin() as c:
            hid = c.execute(insert(households).values(
                name=f"Preview HH {self.suffix}-{len(self.households)}"
            ).returning(households.c.id)).scalar_one()
            pid = c.execute(insert(people).values(
                household_id=hid, full_name=f"Preview P {self.suffix}-{len(self.people)}",
                active=active).returning(people.c.id)).scalar_one()
        self.households.append(hid)
        self.people.append(pid)
        return pid, hid

    def document(self, *, person_id=None, household_id=None, name="doc.pdf", sha=None,
                 doc_type=None, status="active", archived=False, deleted=False):
        sha = sha or (uuid.uuid4().hex * 2)
        with engine.begin() as c:
            doc_id = c.execute(insert(documents).values(
                original_name=name, stored_name=f"{uuid.uuid4().hex}-{name}",
                storage_path=f"/tmp/{uuid.uuid4().hex}.pdf", storage_provider="local",
                size_bytes=1, sha256=sha, person_id=person_id, household_id=household_id,
                status=status, archived=archived,
                deleted_at=datetime.now(UTC) if deleted else None,
            ).returning(documents.c.id)).scalar_one()
            if document_sources is not None:
                c.execute(document_sources.insert().values(
                    document_id=doc_id, source_system=self.SOURCE,
                    source_uri=f"preview://{doc_id}", available=True))
            if doc_type is not None:
                classifications = metadata.tables["document_classifications"]
                c.execute(classifications.insert().values(
                    document_id=doc_id, doc_type=doc_type, confidence=0.9,
                    classifier_version="test"))
        self.documents.append(doc_id)
        return doc_id

    def cleanup(self):
        def _try(stmt):
            try:
                with engine.begin() as c:
                    c.execute(stmt)
            except Exception:
                pass

        if self.documents:
            classifications = metadata.tables["document_classifications"]
            _try(delete(classifications).where(
                classifications.c.document_id.in_(self.documents)))
            if document_sources is not None:
                _try(delete(document_sources).where(
                    document_sources.c.document_id.in_(self.documents)))
            _try(delete(documents).where(documents.c.id.in_(self.documents)))
        if self.people:
            _try(delete(people).where(people.c.id.in_(self.people)))
        if self.households:
            _try(delete(households).where(households.c.id.in_(self.households)))


@pytest.fixture
def sample():
    c = _Corpus()
    try:
        yield c
    finally:
        c.cleanup()


def _preview(sample):
    with engine.connect() as conn:
        return corpus.corpus_preview(conn, source_systems=(sample.SOURCE,))


def test_preview_bands_a_realistic_corpus(sample):
    alice, _ = sample.person()
    bob, _ = sample.person()

    sample.document(person_id=alice, doc_type="1040", name="2024 1040.pdf")        # visible
    sample.document(person_id=alice, doc_type="W-2", name="W-2 2024.pdf")          # visible
    sample.document(person_id=alice, doc_type="workpaper", name="wp.pdf")          # staff-only
    sample.document(person_id=alice, name="mystery.pdf")                           # review
    sample.document(person_id=bob, doc_type="drivers_license", name="dl.pdf")      # review

    shared = uuid.uuid4().hex * 2                                                  # conflict pair
    sample.document(person_id=alice, sha=shared, doc_type="1040", name="dup a.pdf")
    sample.document(person_id=bob, sha=shared, doc_type="1040", name="dup b.pdf")

    result = _preview(sample)
    assert result["total"] == 7
    assert result["bands"][policy.CLIENT_VISIBLE] == 2
    assert result["bands"][policy.STAFF_ONLY] == 1
    assert result["bands"][policy.REVIEW_REQUIRED] == 2
    assert result["bands"][corpus.CROSS_CLIENT_CONFLICT] == 2
    assert result["conflict_groups"] == 1
    assert sum(result["bands"].values()) == result["total"]


def test_preview_excludes_documents_that_are_not_correctly_owned(sample):
    alice, _ = sample.person()
    sample.document(person_id=alice, doc_type="1040")
    sample.document(doc_type="1040")                                   # unowned
    sample.document(person_id=alice, doc_type="1040", status="archived")
    sample.document(person_id=alice, doc_type="1040", deleted=True, status="deleted")
    sample.document(person_id=alice, doc_type="1040", archived=True)

    assert _preview(sample)["total"] == 1


def test_inactive_owner_lands_in_missing_audience_mapping(sample):
    inactive, _ = sample.person(active=False)
    sample.document(person_id=inactive, doc_type="1040")

    result = _preview(sample)
    assert result["bands"][corpus.MISSING_AUDIENCE] == 1
    assert result["bands"][policy.CLIENT_VISIBLE] == 0


def test_household_owned_document_resolves_an_audience(sample):
    """A household owner is a publishable audience, so it is not 'missing'."""
    _, hid = sample.person()
    sample.document(household_id=hid, doc_type="1040")

    result = _preview(sample)
    assert result["bands"][corpus.MISSING_AUDIENCE] == 0
    assert result["bands"][policy.CLIENT_VISIBLE] == 1


def test_preview_reports_per_source_breakdown(sample):
    alice, _ = sample.person()
    sample.document(person_id=alice, doc_type="1040")
    result = _preview(sample)
    assert result["by_source"][sample.SOURCE][policy.CLIENT_VISIBLE] == 1


# --- the preview writes nothing ---------------------------------------------

def test_running_the_preview_publishes_nothing(sample):
    """Requirement G, asserted as a count rather than as an intention."""
    from app.db import document_publication_events, document_publications

    alice, _ = sample.person()
    sample.document(person_id=alice, doc_type="1040")
    sample.document(person_id=alice, doc_type="workpaper")

    with engine.connect() as c:
        publications_before = c.scalar(select(func.count()).select_from(document_publications))
        events_before = c.scalar(select(func.count()).select_from(document_publication_events))
        documents_before = c.scalar(select(func.count()).select_from(documents))

    result = _preview(sample)
    assert result["total"] == 2

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(document_publications)) \
            == publications_before
        assert c.scalar(select(func.count()).select_from(document_publication_events)) \
            == events_before
        assert c.scalar(select(func.count()).select_from(documents)) == documents_before


def test_preview_runs_on_a_read_only_connection(sample):
    """The strongest form of the guarantee: the database itself refuses writes for the duration."""
    from sqlalchemy import text

    alice, _ = sample.person()
    sample.document(person_id=alice, doc_type="1040")

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        result = corpus.corpus_preview(conn, source_systems=(sample.SOURCE,))
    assert result["total"] == 1
