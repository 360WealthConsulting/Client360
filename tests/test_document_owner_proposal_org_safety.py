"""Owner-proposal safety: the ORGANIZATION path must be as fail-closed as the person path.

Production evidence this pins down. The entity-level audit of the 1,747 contradiction-free HIGH
recoveries found 1,277 of them (73%) produced by the organization branch, which read in full::

    if business name appears in document:
        HIGH if any zip/street appears anywhere in the document

No owner-eligibility test, no name-uniqueness test, and no requirement that the corroborating
address belong to the proposed business. The accounting firm's own canonical entity absorbed 544
client documents — 1099s and payroll summaries — spread across 108 DIFFERENT client folders, not one
of which anchored to the firm. They matched because the firm's legal name and address sit on the
letterhead as PREPARER. That is the same letterhead failure the person path already fixed for the
office phone, left open on the organization side.

The rules asserted here:

* an organization reaches HIGH only with POSITIVE client evidence (Drake taxpayer/spouse return role
  or a CRM "Client*" contact_type) — existing in ``relationship_entities`` proves only that somebody
  recorded a name;
* the firm's OWN entities can never own a client's paperwork, and they are detected from deployed
  data (firm mail domain, SharePoint library roots), never from a name or id list;
* corroboration must be an identifier that BELONGS to the proposed business and is not shared;
* an address or ZIP found somewhere in the document corroborates nothing about which business owns
  it — that is precisely the defect being replaced;
* a name carried by two businesses, or two businesses named in one document, fails closed.

No entity id and no firm name appears anywhere in this file. The defence is structural, so it must
hold for any firm and any client.
"""
from __future__ import annotations

import pytest

from app.services.document_owner_proposal import (
    _mark_shared_values,
    _org_folder_anchor,
    analyze_identity,
)

CLIENT_ORG, OTHER_ORG, FIRM_ORG, INELIGIBLE_ORG = 21, 22, 23, 24
CLIENT_ORG_PHONE = "5405551111"
OTHER_ORG_PHONE = "5405552222"
FIRM_PHONE = "5405620123"          # the practice switchboard: on the firm AND on client letterhead
CLIENT_ORG_ZIP, FIRM_ZIP = "24592", "24017"


def _idx():
    """A canonical index with one eligible client business, one look-alike, the firm, and a
    business nobody has evidenced as a client."""
    idx = {
        "email": {}, "phone": {}, "name": {}, "first_last": {}, "members": {}, "hh_name": {},
        "pid": {}, "inst": set(),
        "biz": {
            "acme fuel mart": (CLIENT_ORG, "Acme Fuel Mart"),
            "borden holdings": (OTHER_ORG, "Borden Holdings"),
            "hilltop tax partners": (FIRM_ORG, "Hilltop Tax Partners"),
            "quarry lane supply": (INELIGIBLE_ORG, "Quarry Lane Supply"),
        },
    }
    _mark_shared_values(idx)
    idx["owner_eligible"], idx["staff"] = set(), set()
    # Set positively, exactly as _mark_org_eligibility derives it: a taxpayer/spouse return role or a
    # CRM "Client*" contact_type. INELIGIBLE_ORG is absent because nothing says it is a client, and
    # FIRM_ORG is absent because a firm entity is never owner-eligible even though the firm does file
    # its own returns.
    idx["org_eligible"] = {CLIENT_ORG, OTHER_ORG}
    idx["firm_entities"] = {FIRM_ORG}
    idx["org_ident"] = {
        CLIENT_ORG: {"phones": {CLIENT_ORG_PHONE}, "emails": {"ap@acmefuel.example"},
                     "zips": {CLIENT_ORG_ZIP}},
        OTHER_ORG: {"phones": {OTHER_ORG_PHONE}, "emails": set(), "zips": {CLIENT_ORG_ZIP}},
        FIRM_ORG: {"phones": {FIRM_PHONE}, "emails": {"partner@hilltoptax.example"},
                   "zips": {FIRM_ZIP}},
        INELIGIBLE_ORG: {"phones": {"5405553333"}, "emails": set(), "zips": {FIRM_ZIP}},
    }
    # The switchboard reaches two businesses, so it can never corroborate either.
    idx["org_shared"] = {"phone": {FIRM_PHONE}, "email": set(),
                         "zip": {CLIENT_ORG_ZIP, FIRM_ZIP}}
    idx["org_name_counts"] = {k: 1 for k in idx["biz"]}
    return idx


def _analyze(text, idx=None, folder=None):
    return analyze_identity(text, "scan.pdf", folder, idx or _idx())


# ------------------------------------------------------- the production failure, reproduced

def test_firm_name_on_letterhead_with_a_client_address_is_not_high():
    """THE #160 PATTERN. A client's payroll summary prepared by the firm: the firm's legal name and
    the CLIENT's address both appear. The old branch emitted HIGH for the firm on 544 documents."""
    r = _analyze("Hilltop Tax Partners\n88 Client Road, Danville VA 24592\n"
                 "Payroll Summary — Watkins Market\n")
    assert r["confidence"] != "HIGH"
    assert "firm's own entity" in " ".join(r["evidence"]).lower()


def test_firm_name_with_the_firms_own_address_is_still_not_high():
    """Even when the address genuinely is the firm's, a firm entity is not a client owner."""
    r = _analyze(f"Hilltop Tax Partners\n1 Practice Plaza, Roanoke VA {FIRM_ZIP}\n"
                 f"Phone {FIRM_PHONE}\n")
    assert r["confidence"] != "HIGH"


def test_arbitrary_address_anywhere_in_the_document_is_not_corroboration():
    """The exact replaced defect: name + SOME address => HIGH. An address that belongs to nobody in
    particular says nothing about which business owns the document."""
    r = _analyze("Acme Fuel Mart\nRemit to: 900 Unrelated Ave, Elsewhere TX 75001\n")
    assert r["confidence"] != "HIGH"
    assert "no identifier belonging to this business" in " ".join(r["evidence"]).lower()


def test_zip_alone_never_corroborates_an_organization():
    r = _analyze(f"Acme Fuel Mart\nDanville VA {CLIENT_ORG_ZIP}\n")
    assert r["confidence"] != "HIGH"


# ------------------------------------------------------- what SHOULD still reach HIGH

def test_client_organization_with_its_own_phone_is_high():
    r = _analyze(f"Acme Fuel Mart\nTel {CLIENT_ORG_PHONE}\nInvoice 41\n")
    assert r["confidence"] == "HIGH"
    assert r["proposed_entity_id"] == CLIENT_ORG
    assert r["proposed_entity_type"] == "organization"


def test_client_organization_with_its_own_email_is_high():
    r = _analyze("Acme Fuel Mart\nap@acmefuel.example\n")
    assert r["confidence"] == "HIGH"
    assert r["proposed_entity_id"] == CLIENT_ORG


def test_client_folder_anchor_corroborates():
    """A source folder that structurally names the business is real evidence about ownership."""
    r = _analyze("Acme Fuel Mart\nquarterly report\n",
                 folder="/drives/x/root:/Firm, LLC/Clients/Bookkeeping/Acme Fuel Mart/2021")
    assert r["confidence"] == "HIGH"
    assert r["proposed_entity_id"] == CLIENT_ORG


# ------------------------------------------------------- shared identifiers

def test_shared_switchboard_does_not_corroborate():
    """The firm's line appears on every prepared document. It reaches two businesses, so it is
    context and never an owner signal — the organization analogue of the person-path office line."""
    idx = _idx()
    idx["org_ident"][CLIENT_ORG]["phones"].add(FIRM_PHONE)
    r = _analyze(f"Acme Fuel Mart\nPrepared by your accountant, {FIRM_PHONE}\n", idx)
    assert r["confidence"] != "HIGH"
    assert "shared across businesses" in " ".join(r["evidence"]).lower()


# ------------------------------------------------------- eligibility

def test_organization_without_positive_client_evidence_is_not_high():
    r = _analyze("Quarry Lane Supply\nTel 5405553333\n")
    assert r["confidence"] != "HIGH"
    assert "no authoritative client evidence" in " ".join(r["evidence"]).lower()


def test_existing_in_the_entity_table_is_not_eligibility():
    """Being recorded in relationship_entities proves a name was captured, nothing more."""
    idx = _idx()
    idx["org_eligible"] = set()
    r = _analyze(f"Acme Fuel Mart\nTel {CLIENT_ORG_PHONE}\n", idx)
    assert r["confidence"] != "HIGH"


# ------------------------------------------------------- fail closed

def test_two_businesses_named_in_one_document_is_ambiguous():
    r = _analyze(f"Acme Fuel Mart\nBorden Holdings\nTel {CLIENT_ORG_PHONE}\n")
    assert r["confidence"] == "AMBIGUOUS"
    assert r["proposed_entity_id"] is None


def test_a_name_carried_by_two_businesses_is_not_high():
    idx = _idx()
    idx["org_name_counts"]["acme fuel mart"] = 2
    r = _analyze(f"Acme Fuel Mart\nTel {CLIENT_ORG_PHONE}\n", idx)
    assert r["confidence"] != "HIGH"


def test_a_named_person_still_wins_over_the_organization_branch():
    """The organization branch runs only when no person matched, so an organization proposal can
    never silently displace a person identification."""
    idx = _idx()
    idx["name"] = {"jane doe": [91]}
    idx["first_last"] = {("jane", "doe"): [91]}
    idx["pid"] = {91: {"name": "Jane Doe", "email": None, "phone": None, "household_id": None,
                       "zips": set(), "streets": set()}}
    idx["owner_eligible"] = {91}
    r = _analyze("Jane Doe\nAcme Fuel Mart\n", idx)
    assert r["proposed_entity_type"] != "organization"


def test_tax_documents_still_bypass_the_organization_branch():
    """A preparer/ERO firm name appears throughout a return; the branch stays closed there."""
    r = analyze_identity("Form 1120S\nAcme Fuel Mart\nTel " + CLIENT_ORG_PHONE,
                         "return.pdf", None, _idx())
    assert r["proposed_entity_type"] != "organization"


# ------------------------------------------------------- folder anchoring

@pytest.mark.parametrize("folder", [
    "/drives/x/root:/Firm, LLC",                                   # firm root
    "/drives/x/root:/Firm, LLC/Clients",                           # service-line root
    "/drives/x/root:/Firm, LLC/Clients/Bookkeeping",               # category folder
    "/drives/x/root:/Firm, LLC/Clients/Bookkeeping/2021",          # year folder
    "/drives/x/root:/Firm, LLC/Clients/Bookkeeping/WEB UPLOAD",    # generic drop box
    "/drives/x/root:/Firm, LLC/Clients/Bookkeeping/1179",          # numeric id
])
def test_structural_folders_never_anchor(folder):
    assert _org_folder_anchor(folder, "Acme Fuel Mart") is False


def test_matching_client_folder_anchors():
    assert _org_folder_anchor(
        "/drives/x/root:/Firm, LLC/Clients/Bookkeeping/Acme Fuel Mart/2021",
        "Acme Fuel Mart") is True


def test_a_different_client_folder_does_not_anchor():
    assert _org_folder_anchor(
        "/drives/x/root:/Firm, LLC/Clients/Bookkeeping/Watkins Market/2021",
        "Acme Fuel Mart") is False
