"""Safety conditions for read-only filing evidence (PART 5).

Each test here exists because the alternative is a wrong write onto a real client's record. None of
these paths writes anything; the assertions are that the unsafe answer is REFUSED and handed to a
person, not that a plausible answer is produced.
"""
import pytest

from app.services.document_classification import classify_document
from app.services.document_filing_evidence import (
    attribute_person,
    business_relationship_verdict,
    filing_evidence,
    household_aliases_match,
    is_duplicate_shell_folder,
    normalise_household_alias,
)
from app.services.document_high_validation import has_only_weak_shared_evidence

_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_MEMBERS = {"MBW": "Michael Blaine White", "DGW": "Debra Gregory White",
            "HGW": "Hudson G White", "EWW": "Emerson W White"}


def _row(name, *, folder="", tree="360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual/"
                                 "WHITE,%20MICHAEL%20AND%20DEBRA", household_id=1, **extra):
    url = f"{_SITE}/{tree}{('/' + folder) if folder else ''}/{name.replace(' ', '%20')}"
    row = {"id": 1, "original_name": name, "storage_path": "", "household_id": household_id,
           "person_id": None, "organization_id": None, "category": None,
           "tags": {"source_system": "SharePoint", "web_url": url}}
    row.update(extra)
    return row


# --- folder year vs filename year conflict --------------------------------------------------------

def test_folder_year_and_filename_year_conflict_requires_review():
    ev = filing_evidence(_row("2019 carryforward.pdf", folder="2021"), member_initials=_MEMBERS)
    assert ev["proposed_tax_year"] is None, "neither year may be chosen automatically"
    assert ev["tax_year_conflict"] is True
    assert ev["requires_review"] is True
    assert any("disagree" in e for e in ev["tax_year_evidence"])


def test_folder_year_and_filename_year_agreeing_is_strong():
    ev = filing_evidence(_row("2021 Tax Return Documents.pdf", folder="2021"),
                         member_initials=_MEMBERS)
    assert ev["proposed_tax_year"] == 2021
    assert ev["tax_year_confidence"] == "strong"
    assert ev["tax_year_conflict"] is False


def test_yearless_wealth_account_documents_never_get_an_invented_year():
    """Wealth Consulting account documents are largely yearless. Silence is the correct answer."""
    wealth = "360%20Wealth%20Consulting,%20LLC/Accounts/White,%20Michael%20%26%20Debra"
    for name, folder in [("Accord Transfer Form.pdf", ""), ("Binder1.pdf", "401%20Statements"),
                         ("Deb's 401(k)s.pdf", ""), ("All MetLife documents combined.pdf", "")]:
        ev = filing_evidence(_row(name, folder=folder, tree=wealth), member_initials=_MEMBERS)
        assert ev["proposed_tax_year"] is None, f"{name} must not receive a year"
        assert ev["tax_year_confidence"] == "none"


# --- owner linkage guardrails ----------------------------------------------------------------------

def test_weak_phone_or_zip_evidence_cannot_auto_link():
    assert has_only_weak_shared_evidence({"evidence": ["✓ phone ending 0123 matched"]}) is True
    assert has_only_weak_shared_evidence({"evidence": ["✓ address/ZIP matched"]}) is True
    assert has_only_weak_shared_evidence(
        {"evidence": ["✓ phone ending 0123 matched", "✓ address/ZIP matched"]}) is True


def test_surname_only_owner_match_is_rejected_for_a_business():
    """A shared surname is a coincidence, not a relationship."""
    verdict = business_relationship_verdict("White Willow Homestead LLC", client_surname="White")
    assert verdict.verdict == "rejected_surname_only"
    assert verdict.may_auto_link is False


def test_a_business_with_corroborating_evidence_is_a_review_candidate_only():
    """White House Management LLC — the client's own email is whitehousellc@yahoo.com, which is a
    real cross-document signal. It still goes to a person; it is never linked automatically."""
    verdict = business_relationship_verdict(
        "White House Management LLC", client_surname="White",
        corroborating_evidence=["client primary_email whitehousellc@yahoo.com matches the "
                                "business name"])
    assert verdict.verdict == "candidate_for_review"
    assert verdict.may_auto_link is False, "no business relationship is ever created automatically"
    assert verdict.evidence


def test_a_business_with_no_link_at_all_is_not_proposed():
    assert business_relationship_verdict("Amerasia LLC", client_surname="White").verdict == "no_evidence"


# --- person attribution ------------------------------------------------------------------------------

@pytest.mark.parametrize("name,initials,person", [
    ("MBW 2023 W-2.pdf", "MBW", "Michael Blaine White"),
    ("DGW Sunoco K1 - 2023 Taxes.pdf", "DGW", "Debra Gregory White"),
    ("HGW - Sallie Mae 2023 1098-E.pdf", "HGW", "Hudson G White"),
    ("EWW college.pdf", "EWW", "Emerson W White"),
])
def test_known_member_initials_attribute_within_the_household(name, initials, person):
    a = attribute_person(name, member_initials=_MEMBERS, household_owned=True)
    assert a.initials == initials and a.person_name == person
    assert a.confidence == "strong" and a.requires_review is False


def test_dlw_is_never_resolved_automatically():
    a = attribute_person("DLW MetLife - Signed.pdf", member_initials=_MEMBERS, household_owned=True)
    assert a.person_name is None, "DLW must not be resolved to a person"
    assert a.confidence == "unresolved" and a.requires_review is True


def test_person_attribution_never_applies_outside_an_owning_household():
    a = attribute_person("MBW 2023 W-2.pdf", member_initials=_MEMBERS, household_owned=False)
    assert a.person_name is None and a.confidence == "none"


def test_person_attribution_does_not_override_household_ownership():
    ev = filing_evidence(_row("MBW 2023 W-2.pdf", household_id=1), member_initials=_MEMBERS)
    assert ev["current_owner"] == "household:1", "ownership is unchanged"
    assert ev["proposed_person"] == "Michael Blaine White"   # an annotation alongside it
    assert ev["would_write"] is False


def test_several_member_initials_in_one_filename_requires_review():
    a = attribute_person("MBW & DGW Separate accounts.pdf", member_initials=_MEMBERS,
                         household_owned=True)
    assert a.person_name is None and a.requires_review is True


# --- household alias normalisation ---------------------------------------------------------------

@pytest.mark.parametrize("alias", [
    "WHITE, MICHAEL AND DEBRA",
    "White, Michael & Debra",
    "White, Michael & Debra(1)",
    "Michael and Debra White",
])
def test_all_known_household_spellings_normalise_together(alias):
    assert household_aliases_match(alias, "WHITE, MICHAEL AND DEBRA")
    assert normalise_household_alias(alias) == "debra michael white"


def test_a_different_white_household_does_not_collide():
    assert not household_aliases_match("WHITE, JOSHUA A & SARAH", "WHITE, MICHAEL AND DEBRA")
    assert not household_aliases_match("WHITE, HENRY", "WHITE, MICHAEL AND DEBRA")


def test_the_duplicate_shell_folder_is_recognised_not_reported_missing():
    """``White, Michael & Debra(1)`` is an empty SharePoint duplicate, not an un-ingested tree."""
    assert is_duplicate_shell_folder("White, Michael & Debra(1)") is True
    assert is_duplicate_shell_folder("White, Michael & Debra") is False
    # It still resolves to the same household, so it can never look like a different client.
    assert household_aliases_match("White, Michael & Debra(1)", "White, Michael & Debra")


# --- category evidence ------------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("MBW 2023 W-2.pdf", "W-2"),
    ("Deb's 2022 W2.pdf", "W-2"),
    ("HGW - Sallie Mae 2023 1098-E.pdf", "1098-E"),
    ("Rocket Mortgage 2023 Form 1098.pdf", "1098"),
    ("Bank of NY Mellon 1099-SA.pdf", "1099"),
    ("SSA-1099 2023.pdf", "SSA-1099"),
    ("Signed 8879s.pdf", "8879"),
    ("DGW Sunoco K1 - 2023 Taxes.pdf", "K-1"),
    ("Form 5498 IRA contribution.pdf", "5498"),
    ("2553 S-corp election.pdf", "2553"),
    ("8824 like-kind exchange.pdf", "8824"),
    ("1095-C coverage.pdf", "1095-C"),
    ("CP2000 notice.pdf", "irs_notice"),
])
def test_strong_filename_tokens_classify(name, expected):
    doc_type, confidence = classify_document(name, None)
    assert doc_type == expected and confidence > 0


def test_a_filename_with_no_token_is_left_unclassified():
    """No token, no guess — an uncategorized document is better than a wrong category."""
    for name in ("Voided Check.pdf", "Binder1.pdf", "Emails.pdf", "Cash Flow (2).pdf"):
        assert classify_document(name, None) == ("unknown", 0.0)


# --- zero-byte and same-name/different-size ---------------------------------------------------------

def test_zero_byte_source_is_handled_without_raising():
    """Michael White has one: Arrington/2021 Tax Return.pdf, 0 bytes."""
    ev = filing_evidence(_row("2021 Tax Return.pdf", folder="Arrington", size_bytes=0),
                         member_initials=_MEMBERS)
    assert ev["proposed_tax_year"] == 2021          # evidence still reads normally
    assert ev["would_write"] is False


def test_same_filename_different_size_produces_independent_evidence():
    """Two files may legitimately share a name. Evidence is per document; nothing is merged."""
    a = filing_evidence(_row("2021 Tax Return.pdf", folder="2021", size_bytes=100, id=1),
                        member_initials=_MEMBERS)
    b = filing_evidence(_row("2021 Tax Return.pdf", folder="2022", size_bytes=250, id=2),
                        member_initials=_MEMBERS)
    assert a["document_id"] != b["document_id"]
    assert a["tax_year_conflict"] is False and b["tax_year_conflict"] is True   # 2021 name vs 2022 folder


def test_inference_is_total_and_never_raises():
    for row in ({}, {"original_name": None}, {"tags": None}, {"tags": "not-a-dict"}):
        out = filing_evidence(row, member_initials=_MEMBERS)
        assert out["would_write"] is False
