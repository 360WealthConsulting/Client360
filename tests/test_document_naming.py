"""Document display-name composition + read-only normalization preview.

Fixtures use the PRODUCTION shape the census reported: category NULL for ~2/3 of rows, effective_date
always NULL (year must come from the filename), full_name NULL with first/last populated, and mostly
INFORMATIVE filenames (only 2% generic). The preview must never write, never rename, and never
discard filename detail silently.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    relationship_entities,
)
from app.services.document_naming import (
    canonical_display_name,
    extract_year,
    is_generic_filename,
    residual_qualifier,
    resolve_document_type,
    sanitize,
    type_label,
)
from app.services.document_normalization_preview import build_preview

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(like))))
            c.execute(documents.delete().where(documents.c.original_name.like(like)))
            if ppl:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(documents.delete().where(documents.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if ents:
                c.execute(documents.delete().where(documents.c.organization_id.in_(ents)))
                c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(ents)))
            if hhs:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(documents.delete().where(documents.c.household_id.in_(hhs)))
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "DNM" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _person(c, tag, first, last):
    return c.execute(insert(people).values(
        first_name=first, last_name=f"{last}{tag}", full_name=None, active=True
    ).returning(people.c.id)).scalar_one()


def _household(c, tag, name):
    return c.execute(insert(households).values(name=f"{name} {tag}")
                     .returning(households.c.id)).scalar_one()


def _business(c, tag, name):
    return c.execute(insert(relationship_entities).values(
        entity_type="business", name=f"{name} {tag}", active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _doc(c, name, *, person_id=None, household_id=None, organization_id=None, category=None):
    u = uuid.uuid4().hex
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"s-{u}", storage_path=f"/x/{u}",
        size_bytes=1, sha256=u.ljust(64, "0")[:64], status="active", archived=False,
        category=category, person_id=person_id, household_id=household_id,
        organization_id=organization_id).returning(documents.c.id)).scalar_one()


def _by_id(rep, doc_id):
    return next(r for r in rep["rows"] if r["document_id"] == doc_id)


# --------------------------------------------------------------------- year extraction
def test_extract_year_takes_the_trailing_plausible_year():
    assert extract_year("Pullen 1040 2024.pdf") == 2024
    assert extract_year("12345 Pullen W-2 2023.pdf") == 2023      # leading job number ignored
    assert extract_year("statement.pdf") is None
    assert extract_year("acct 123456789 stmt.pdf") is None        # long digit run is not a year
    assert extract_year("invoice 1899.pdf") is None               # out of range
    assert extract_year("2019 organizer 2020.pdf") == 2020        # last plausible year wins


def test_extract_year_rejects_years_embedded_in_hashes_and_export_ids():
    """A 4-digit run inside a longer alphanumeric token is a hash fragment, not a year."""
    assert extract_year("c4aa9e2000.pdf") is None
    assert extract_year("a1b2c32024ef.pdf") is None
    assert extract_year("FY2024 summary.pdf") == 2024             # short affix is still a year
    assert extract_year("2024Taxes.pdf") == 2024


# --------------------------------------------------------------------- type resolution
@pytest.mark.parametrize("filename,expected", [
    ("Pullen W-2 2024.pdf", "W-2"),
    ("norman w2 2024.pdf", "W-2"),
    ("1099-DIV Schwab 2024.pdf", "1099"),
    ("1099 R 2023.pdf", "1099"),
    ("Form 1040 2024.pdf", "1040"),
    ("Pullen Homes 1120S 2024.pdf", "1120S"),
    ("partnership 1065 2024.pdf", "1065"),
    ("corp 1120 2024.pdf", "1120"),
    ("K-1 Pullen Homes 2024.pdf", "K-1"),
    ("8879 signed 2024.pdf", "8879"),
    ("8879-S Pullen Homes 2024.pdf", "8879"),
    ("941 Q3 2024.pdf", "941"),
    ("1095-C 2024.pdf", "1095-C"),
    ("2024 Tax Organizer.pdf", "organizer"),
    ("Payroll Summary Q1 2024.pdf", "payroll_summary"),
    ("2024 Tax Return copy.pdf", "tax_return"),
    ("Tax Docs 2024.pdf", "tax_documents"),
    ("engagement letter 2024.pdf", "engagement_letter"),
    ("insurance policy declarations.pdf", "insurance_policy"),
])
def test_document_types_resolve_from_filename(filename, expected):
    code, conf, source = resolve_document_type(None, filename)
    assert code == expected, f"{filename} -> {code}"
    assert conf > 0 and source in ("filename_pattern", "classifier")


def test_unknown_type_for_an_unrecognizable_filename():
    assert resolve_document_type(None, "misc paperwork.pdf") == ("unknown", 0.0, "none")


def test_specific_category_wins_over_filename_and_vague_category_does_not():
    assert resolve_document_type("W-2", "scan001.pdf")[:2] == ("W-2", 0.95)
    # "tax" is a domain, not a document type — must fall through to the filename
    assert resolve_document_type("tax", "Form 1040 2024.pdf")[0] == "1040"
    assert resolve_document_type("tax", "misc.pdf")[0] == "unknown"


def test_form_numbers_do_not_match_inside_longer_digit_runs():
    assert resolve_document_type(None, "acct 11205 summary.pdf")[0] != "1120S"
    assert resolve_document_type(None, "ext 9415 memo.pdf")[0] != "941"


# --------------------------------------------------------------------- composition
def test_canonical_name_composition_and_omitted_segments():
    assert canonical_display_name(year=2025, type_code="1040", entity="Norman Pullen") == \
        "2025 - Form 1040 - Norman Pullen"
    assert canonical_display_name(year=None, type_code="W-2", entity="Norman Pullen") == \
        "W-2 - Norman Pullen"                                  # no placeholder for a missing year
    assert canonical_display_name(year=2025, type_code="1120S", entity="Pullen Homes Inc") == \
        "2025 - Form 1120S - Pullen Homes Inc"
    assert canonical_display_name(year=2025, type_code="W-2", entity="Norman Pullen",
                                  qualifier="Acme Corp") == "2025 - W-2 - Norman Pullen - Acme Corp"
    # nothing useful to say -> no candidate at all
    assert canonical_display_name(year=2025, type_code="unknown", entity="Norman Pullen") is None


def test_names_never_contain_a_document_id_and_are_sanitized():
    name = canonical_display_name(year=2025, type_code="1040", entity='Bad/Name:With"Chars')
    assert "/" not in name and ":" not in name and '"' not in name
    assert sanitize("  spaced  -  ") == "spaced"
    assert type_label("unknown") is None


def test_generic_filename_detection():
    assert is_generic_filename("doc.pdf") and is_generic_filename("scan001.pdf")
    assert is_generic_filename("IMG_4821.pdf") and is_generic_filename("d.pdf")
    assert not is_generic_filename("Pullen W-2 2024.pdf")


# --------------------------------------------------------------------- qualifier preservation
def test_qualifier_keeps_detail_not_carried_by_year_type_owner():
    q = residual_qualifier("Norman Pullen W-2 2024 Acme Corp.pdf", year=2024, type_code="W-2",
                           entity="Norman Pullen")
    assert q == "Acme Corp"


def test_qualifier_strips_separator_free_type_variants():
    """"w2" is the same token as "W-2"; leaving it behind creates a bogus qualifier and makes two
    identical documents stop colliding."""
    assert residual_qualifier("w2 2024 copy.pdf", year=2024, type_code="W-2",
                              entity="Norman Pullen") is None
    assert residual_qualifier("1095c 2024.pdf", year=2024, type_code="1095-C",
                              entity="Norman Pullen") is None
    assert residual_qualifier("k1 2024 Pullen Homes.pdf", year=2024, type_code="K-1",
                              entity="Pullen Homes") is None


def test_qualifier_drops_information_already_in_the_name():
    assert residual_qualifier("Pullen 1040 2024.pdf", year=2024, type_code="1040",
                              entity="Pullen") is None
    assert residual_qualifier("12345 1040 2024.pdf", year=2024, type_code="1040",
                              entity="Pullen") is None          # bare export id is not detail


# --------------------------------------------------------------------- preview buckets
def test_safe_bucket_for_person_household_and_business_owners():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        hid = _household(c, tag, "Norman & Julie Ann Pullen Household")
        bid = _business(c, tag, "Pullen Homes Inc")
        d_person = _doc(c, "w2 2024.pdf", person_id=pid)
        d_home = _doc(c, "1040 2024.pdf", household_id=hid)
        d_biz = _doc(c, "1120S 2024.pdf", organization_id=bid)
    rep = build_preview()
    for did, owner_type in ((d_person, "person"), (d_home, "household"), (d_biz, "business")):
        row = _by_id(rep, did)
        assert row["bucket"] == "SAFE", (owner_type, row["reason"])
        assert row["owner_type"] == owner_type
        assert row["year"] == 2024
        assert str(did) not in row["proposed_display_name"]      # never an internal id
    assert _by_id(rep, d_person)["proposed_display_name"] == f"2024 - W-2 - Norman Pullen{tag}"
    assert _by_id(rep, d_biz)["proposed_display_name"] == f"2024 - Form 1120S - Pullen Homes Inc {tag}"


def test_collision_within_the_same_owner_goes_to_review():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        first = _doc(c, "w2 2024.pdf", person_id=pid)
        second = _doc(c, "w2 2024 copy.pdf", person_id=pid)
    rep = build_preview()
    buckets = {_by_id(rep, first)["bucket"], _by_id(rep, second)["bucket"]}
    assert "REVIEW" in buckets
    assert rep["collisions"] >= 1
    assert any(r["collision"] for r in (_by_id(rep, first), _by_id(rep, second)))


def test_unchanged_when_the_existing_filename_is_already_clearer():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        did = _doc(c, f"2024 Form 1040 Norman Pullen{tag} amended second filing.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "UNCHANGED"
    assert "already states the year, type and owner" in row["reason"]


def test_skip_for_generic_filename_with_no_metadata():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        did = _doc(c, "doc.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "SKIP"
    assert row["proposed_display_name"] is None


def test_skip_for_inconsistent_ownership():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        bid = _business(c, tag, "Pullen Homes Inc")
        did = _doc(c, "1120S 2024.pdf", person_id=pid, organization_id=bid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "SKIP"
    assert "more than one owner column" in row["reason"]


def test_review_when_the_filename_holds_detail_and_no_year():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        did = _doc(c, "W-2 Acme Manufacturing.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "REVIEW"
    assert row["qualifier"] and "Acme" in row["qualifier"]        # detail preserved, not dropped


def test_unknown_type_with_informative_filename_is_reviewed_not_skipped():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        did = _doc(c, "Beneficiary designation letter.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "REVIEW"
    assert row["document_type"] == "unknown"


# --------------------------------------------------------------------- provenance + read-only
def test_preview_writes_nothing_and_preserves_provenance():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        bid = _business(c, tag, "Pullen Homes Inc")
        d1 = _doc(c, "w2 2024.pdf", person_id=pid)
        d2 = _doc(c, "1120S 2024.pdf", organization_id=bid)

    def snapshot():
        with engine.connect() as c:
            rows = sorted(c.execute(
                select(documents.c.id, documents.c.original_name, documents.c.stored_name,
                       documents.c.storage_path, documents.c.sha256, documents.c.category,
                       documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                .where(documents.c.id.in_([d1, d2]))).all())
            total = c.scalar(select(func.count()).select_from(documents))
        return rows, total

    before = snapshot()
    for _ in range(3):
        build_preview()
    assert snapshot() == before                                   # identical rows, identical count


def test_preview_report_shape_has_every_required_section():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        _doc(c, "w2 2024.pdf", person_id=pid)
    rep = build_preview(examples=5)
    assert set(rep["counts"]) == {"SAFE", "REVIEW", "UNCHANGED", "SKIP"}
    assert rep["total_reviewed"] == sum(rep["counts"].values())
    for key in ("by_owner_type", "by_document_type", "by_source_system", "collisions", "examples"):
        assert key in rep
    row = rep["rows"][0]
    for field in ("document_id", "current_filename", "proposed_display_name", "owner", "owner_type",
                  "document_type", "year", "source_system", "confidence", "reason", "collision"):
        assert field in row


def test_preview_is_deterministic_across_runs():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Norman", "Pullen")
        did = _doc(c, "1099-DIV Schwab 2024.pdf", person_id=pid)
    first = _by_id(build_preview(), did)
    second = _by_id(build_preview(), did)
    assert first == second
