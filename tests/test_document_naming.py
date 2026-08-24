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
    detect_form_families,
    detect_version_markers,
    extract_year,
    has_ambiguous_year,
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
    ("1099-DIV Schwab 2024.pdf", "1099-DIV"),
    ("1099 R 2023.pdf", "1099-R"),
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
    match = resolve_document_type(None, filename)
    assert match.code == expected, f"{filename} -> {match.code}"
    assert match.confidence > 0 and match.source in ("filename_pattern", "classifier")


def test_unknown_type_for_an_unrecognizable_filename():
    assert resolve_document_type(None, "misc paperwork.pdf") == ("unknown", 0.0, "none", None)


def test_specific_category_wins_over_filename_and_vague_category_does_not():
    assert resolve_document_type("W-2", "scan001.pdf")[:2] == ("W-2", 0.95)
    # "tax" is a domain, not a document type — must fall through to the filename
    assert resolve_document_type("tax", "Form 1040 2024.pdf").code == "1040"
    assert resolve_document_type("tax", "misc.pdf").code == "unknown"


def test_form_numbers_do_not_match_inside_longer_digit_runs():
    assert resolve_document_type(None, "acct 11205 summary.pdf").code != "1120S"
    assert resolve_document_type(None, "ext 9415 memo.pdf").code != "941"


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


def test_qualifier_strips_the_matched_form_reference_as_a_unit():
    """Production debris: 8879S -> "S", 1099INT -> "INT", 1099-R -> "R". The matched span must go
    whole, not be guessed at variant-by-variant."""
    for name, code, matched in (("2021 8879S.pdf", "8879", "8879s"),
                                ("Tax_1099INT_2024_Goldman Sachs.PDF", "1099-INT", "1099int"),
                                ("Rovner 2023 1099-R.jpg", "1099-R", "1099-r")):
        q = residual_qualifier(name, year=extract_year(name), type_code=code,
                               entity="Rovner", matched_text=matched)
        assert q is None or ("S" != q and "INT" != q and "R" != q), f"{name} -> {q!r}"
    # the payer survives, the form reference does not
    assert residual_qualifier("Tax_1099INT_2024_Goldman Sachs.PDF", year=2024, type_code="1099-INT",
                              entity="Ann Rovner", matched_text="1099int") == "Goldman Sachs"


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


# ===================================================================== refinement pass regressions
# Shapes taken from the production preview at cee6583, with synthetic owner names and ids.

@pytest.mark.parametrize("filename,expected", [
    ("1099R_2024_Fidelity.pdf", "1099-R"),
    ("1099K_2024_PayPal.pdf", "1099-K"),
    ("Tax_1099INT_2024_Goldman Sachs.PDF", "1099-INT"),
    ("1099DIV 2024 Vanguard.pdf", "1099-DIV"),
    ("1099NEC_2024.pdf", "1099-NEC"),
    ("1099-SA 2024 HSA Bank.pdf", "1099-SA"),
    ("2025-01-27_1099-K_Upwork.pdf", "1099-K"),
    ("Fidelity 1099-R 2025.pdf", "1099-R"),
])
def test_1099_subtypes_are_preserved_not_flattened(filename, expected):
    assert resolve_document_type(None, filename).code == expected


def test_generic_1099_stays_generic_when_no_subtype_is_stated():
    assert resolve_document_type(None, "1099 2024 statement.pdf").code == "1099"
    assert resolve_document_type(None, "1099-MISC 2024.pdf").code == "1099"


@pytest.mark.parametrize("filename,expected", [
    ("2024 Schedule C.pdf", "schedule_c"),
    ("Income Expense Worksheet 2024.pdf", "schedule_c"),
    ("DL Front.jpg", "drivers_license"),
    ("DL_Back.jpg", "drivers_license"),
    ("Drivers License 2024.pdf", "drivers_license"),
    ("Signature Documents 2024.pdf", "signature_documents"),
    ("2024 Year End Tax Package.pdf", "year_end_tax_package"),
    ("Mortgage Interest Statement 2024.pdf", "mortgage_interest"),
])
def test_new_deterministic_common_types(filename, expected):
    assert resolve_document_type(None, filename).code == expected


def test_bare_dl_token_is_not_a_drivers_license():
    """"DL" alone is initials or an abbreviation far more often than a licence."""
    assert resolve_document_type(None, "DL Holdings statement 2024.pdf").code != "drivers_license"


def test_8879s_is_stripped_whole_leaving_no_stray_s():
    m = resolve_document_type(None, "2021 8879S.pdf")
    assert m.code == "8879"
    q = residual_qualifier("2021 8879S.pdf", year=2021, type_code="8879",
                           entity="Ann Rovner", matched_text=m.matched_text)
    assert q is None


def test_w2_workflow_noise_does_not_survive_as_a_qualifier():
    """Adam - W2_USETHISONE_2024-04-02.pdf previously produced "USETHISONE 04 02"."""
    name = "Adam - W2_USETHISONE_2024-04-02.pdf"
    m = resolve_document_type(None, name)
    q = residual_qualifier(name, year=2024, type_code=m.code, entity="Adam Davis",
                           matched_text=m.matched_text)
    assert q is None or "04" not in q                       # the date fragment is gone
    assert detect_version_markers(name)                     # USETHISONE forces REVIEW


def test_whole_dates_are_stripped_not_left_as_fragments():
    name = "2025-01-27_1099-K_Upwork.pdf"
    m = resolve_document_type(None, name)
    q = residual_qualifier(name, year=2025, type_code=m.code, entity="Ann Rovner",
                           matched_text=m.matched_text)
    assert q == "Upwork"                                    # not "01 27 K"


def test_filler_words_are_dropped_but_employer_initials_are_kept():
    name = "NMR W2 for 2023.pdf"
    m = resolve_document_type(None, name)
    q = residual_qualifier(name, year=2023, type_code=m.code, entity="Ann Rovner",
                           matched_text=m.matched_text)
    assert q == "NMR"                                       # "for" dropped, employer kept


@pytest.mark.parametrize("name,owner,expected", [
    ("1099K_2024_PayPal.pdf", "Ann Rovner", "PayPal"),
    ("Fidelity 1099-R 2025.pdf", "Ann Rovner", "Fidelity"),
    ("1099DIV 2024 Vanguard.pdf", "Ann Rovner", "Vanguard"),
    ("W-2 2024 Acme Manufacturing.pdf", "Ann Rovner", "Acme Manufacturing"),
])
def test_meaningful_payer_custodian_employer_text_is_preserved(name, owner, expected):
    m = resolve_document_type(None, name)
    q = residual_qualifier(name, year=extract_year(name), type_code=m.code, entity=owner,
                           matched_text=m.matched_text)
    assert q == expected


@pytest.mark.parametrize("name", [
    "1040 2024 amended.pdf", "1040 2024 CORRECTED.pdf", "W2 2024 revised.pdf",
    "1099NEC_2024_V1.pdf", "1040 2024 draft.pdf", "W2 USETHISONE 2024.pdf",
])
def test_version_and_amendment_markers_are_detected(name):
    assert detect_version_markers(name), name


def test_multiple_form_families_are_detected():
    assert len(detect_form_families("Pullen 1040 K-1 8879 2024.pdf")) > 1
    assert len(detect_form_families("1099R and 1099K 2024.pdf")) == 1   # same family, not multi-form
    assert len(detect_form_families("W2 2024.pdf")) == 1
    # a coarse descriptor is not a second form
    assert len(detect_form_families("2024 Tax Return 1040.pdf")) == 1


def test_ambiguous_year_detection():
    assert has_ambiguous_year("2019 organizer 2020.pdf")
    assert not has_ambiguous_year("W2 2024.pdf")
    assert not has_ambiguous_year("2024 1040 2024.pdf")     # same year twice is not ambiguous


# --------------------------------------------------------------------- bucket effects
def test_multi_form_filename_goes_to_review_not_safe():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "1040 K-1 8879 2024.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "REVIEW"
    assert row["multi_form"] is True
    assert "more than one materially different form" in row["reason"]


def test_amended_filename_goes_to_review_not_safe():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "1040 2024 amended.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "REVIEW"
    assert row["version_markers"]


def test_ambiguous_year_goes_to_review_not_safe():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "2019 organizer 2020.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "REVIEW"
    assert row["ambiguous_year"] is True


def test_distinct_1099_subtypes_for_one_owner_do_not_collide():
    """Flattening every 1099 to "1099" made a person's 1099-R and 1099-K collide. They must not."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        d_r = _doc(c, "1099R 2024.pdf", person_id=pid)
        d_k = _doc(c, "1099K 2024.pdf", person_id=pid)
    rep = build_preview()
    r_row, k_row = _by_id(rep, d_r), _by_id(rep, d_k)
    assert r_row["proposed_display_name"] != k_row["proposed_display_name"]
    assert not r_row["collision"] and not k_row["collision"]
    assert r_row["bucket"] == "SAFE" and k_row["bucket"] == "SAFE"


def test_same_subtype_twice_for_one_owner_still_collides_to_review():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        first = _doc(c, "1099R 2024.pdf", person_id=pid)
        second = _doc(c, "1099-R 2024.pdf", person_id=pid)
    rep = build_preview()
    assert "REVIEW" in {_by_id(rep, first)["bucket"], _by_id(rep, second)["bucket"]}
    assert rep["collisions"] >= 1


def test_safe_payer_document_survives_the_stricter_rules():
    """The refinement must not make everything REVIEW — a clean payer document is still SAFE."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "1099K_2024_PayPal.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "SAFE"
    assert row["proposed_display_name"] == f"2024 - 1099-K - Ann Rovner{tag} - PayPal"


def test_camera_scan_image_is_not_classified_by_guesswork():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "IMG_4821.jpg", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "SKIP"
    assert row["document_type"] == "unknown"


def test_refined_preview_still_writes_nothing_and_is_deterministic():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "1099INT 2024 Goldman Sachs.pdf", person_id=pid)

    def snapshot():
        with engine.connect() as c:
            rows = sorted(c.execute(
                select(documents.c.id, documents.c.original_name, documents.c.storage_path,
                       documents.c.sha256, documents.c.category).where(documents.c.id == did)).all())
            return rows, c.scalar(select(func.count()).select_from(documents))

    before = snapshot()
    first = _by_id(build_preview(), did)
    second = _by_id(build_preview(), did)
    assert first == second
    assert snapshot() == before


# ===================================================================== amended returns (1040-X)
@pytest.mark.parametrize("filename", ["1040X 2024.pdf", "1040-X 2024.pdf", "1040 X 2024.pdf",
                                      "2024 1040x Smith.pdf", "Form 1040-X 2024.pdf"])
def test_amended_1040x_is_its_own_type_never_plain_1040(filename):
    m = resolve_document_type(None, filename)
    assert m.code == "1040-X", f"{filename} -> {m.code}"
    assert type_label(m.code) == "Form 1040-X"


def test_ordinary_1040_is_unaffected():
    for name in ("1040 2024.pdf", "Form 1040 2024.pdf", "2024 1040 Smith.pdf"):
        assert resolve_document_type(None, name).code == "1040"
        assert type_label("1040") == "Form 1040"


def test_other_amended_federal_returns_that_actually_exist():
    """1065-X and 1120-X are real amended forms whose base forms this classifier already knows.
    1041 and 1120-S have no -X form (amended by checkbox), so they must NOT be invented."""
    assert resolve_document_type(None, "1065X 2024.pdf").code == "1065-X"
    assert resolve_document_type(None, "1120-X 2024.pdf").code == "1120-X"
    assert resolve_document_type(None, "1041 2024.pdf").code == "1041"
    assert resolve_document_type(None, "1120S 2024.pdf").code == "1120S"


def test_1040x_leaves_no_stray_x_in_the_qualifier():
    for name in ("1040X 2024.pdf", "1040-X 2024.pdf", "1040 X 2024.pdf"):
        m = resolve_document_type(None, name)
        q = residual_qualifier(name, year=2024, type_code=m.code, entity="Ann Rovner",
                               matched_text=m.matched_text)
        assert q is None, f"{name} -> {q!r}"


def test_clean_1040x_filename_is_safe_and_names_the_amended_form():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "2024 1040X.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["bucket"] == "SAFE"
    assert row["proposed_display_name"] == f"2024 - Form 1040-X - Ann Rovner{tag}"


def test_amended_and_original_1040_do_not_collide_semantically():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        original = _doc(c, "1040 2024.pdf", person_id=pid)
        amended = _doc(c, "1040X 2024.pdf", person_id=pid)
    rep = build_preview()
    o_row, a_row = _by_id(rep, original), _by_id(rep, amended)
    assert o_row["proposed_display_name"] != a_row["proposed_display_name"]
    assert "Form 1040-X" in a_row["proposed_display_name"]
    assert "Form 1040-X" not in o_row["proposed_display_name"]
    assert not o_row["collision"] and not a_row["collision"]
    assert o_row["bucket"] == "SAFE" and a_row["bucket"] == "SAFE"


def test_word_amendment_markers_still_force_review_alongside_1040x():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "1040X 2024 superseded.pdf", person_id=pid)
    row = _by_id(build_preview(), did)
    assert row["document_type"] == "1040-X"
    assert row["bucket"] == "REVIEW" and row["version_markers"]


def test_1040x_preview_stays_deterministic_and_read_only():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Ann", "Rovner")
        did = _doc(c, "1040-X 2024.pdf", person_id=pid)

    def snapshot():
        with engine.connect() as c:
            rows = sorted(c.execute(
                select(documents.c.id, documents.c.original_name, documents.c.storage_path,
                       documents.c.sha256, documents.c.category).where(documents.c.id == did)).all())
            return rows, c.scalar(select(func.count()).select_from(documents))

    before = snapshot()
    assert _by_id(build_preview(), did) == _by_id(build_preview(), did)
    assert snapshot() == before
