"""Deterministic client anchoring for the LIVE SharePoint connector.

The live delta/webhook path imported 34,553 documents and anchored 344 of them (1.0%), while the
migration cohort anchored 21,109 of 21,109 (100%). The difference was not matching quality: the
live record builders never populated ``client_folder``, so the ownership branch in
``import_sharepoint_items`` never executed, and the folder-hint helper they would have used read
the FIRM root instead of the client.

These tests pin the three properties that failure needed:

1. both live record builders emit ``client_folder`` when the path is recognisable;
2. the hint is the CLIENT segment — never the firm, a service line, an entity class, a category or
   a year — and is None whenever the structure is not understood;
3. exact-name normalisation tolerates middle initials and generation suffixes without ever
   collapsing two genuinely different people into a confident single match.

Nothing here exercises the owner PROPOSAL engine. Deterministic folder anchoring and proposals are
separate mechanisms, and this change deliberately does not touch the latter.
"""
from __future__ import annotations

import pytest

from app.connectors.microsoft365.sharepoint_content import client_folder_hint
from app.importers.taxdome_drive import _folder_person_keys, _name_key
from app.services.microsoft_ingestion import _delta_item_record, _driveitem_to_record

# A real production path shape (client name substituted).
INDIVIDUAL = "/drives/b!abc/root:/360 Tax Solutions, LLC/Clients/Tax Preparation/Individual/Sebastian, Britt/2022"
PAYROLL = "/drives/b!abc/root:/360 Tax Solutions, LLC/Clients/Payroll/Inactive/ERE Power LLC/Federal/2018"
SALES = ("/drives/b!abc/root:/360 Tax Solutions, LLC/Clients/Sales, Litter & PP Tax/"
         "Sales & Litter Tax/Needs To Be Done/Shrim Inc/2018")
BOOKKEEPING = "/drives/b!abc/root:/Documents 1/FileHistory/360 Tax/Clients/Bookkeeping/Raymonds Construction/Check Stubs"


def _graph_item(path):
    return {"id": "01ITEM", "name": "statement.pdf", "webUrl": "https://x/y/statement.pdf",
            "size": 1234, "lastModifiedDateTime": "2026-01-02T03:04:05Z",
            "createdDateTime": "2026-01-01T00:00:00Z", "file": {"mimeType": "application/pdf"},
            "parentReference": {"path": path, "siteId": "site-1", "driveId": "drive-1"}}


# ---------------------------------------------------------------- A + B: the record builders

def test_delta_record_carries_the_client_folder():
    rec = _delta_item_record("drive-1", _graph_item(INDIVIDUAL), local_path="/tmp/x.pdf")
    assert rec["client_folder"] == "Sebastian, Britt"
    assert rec["folder_path"] == INDIVIDUAL          # existing field is unchanged


def test_driveitem_record_carries_the_client_folder():
    rec = _driveitem_to_record("drive-1", _graph_item(PAYROLL))
    assert rec["client_folder"] == "ERE Power LLC"
    assert rec["dry_run"] is True                    # existing contract is unchanged


def test_record_builders_emit_none_rather_than_omitting_the_key():
    """The importer reads ``item.get("client_folder")``; an unrecognised path must yield a falsy
    value, not a wrong one."""
    unknown = "/drives/b!abc/root:/AWS Migration Backup/DRAKE15"
    assert _delta_item_record("d", _graph_item(unknown), local_path="/tmp/x")["client_folder"] is None
    assert _driveitem_to_record("d", _graph_item(unknown))["client_folder"] is None


# ---------------------------------------------------------------- C: hint extraction

@pytest.mark.parametrize("path,expected", [
    (INDIVIDUAL, "Sebastian, Britt"),
    (PAYROLL, "ERE Power LLC"),
    (SALES, "Shrim Inc"),
    (BOOKKEEPING, "Raymonds Construction"),
    # Windows separators and a bare (non-Graph) path both work.
    ("360 Tax Solutions, LLC\\Clients\\Tax Preparation\\Individual\\Warner, Stephen & Kathryn",
     "Warner, Stephen & Kathryn"),
])
def test_client_folder_hint_finds_the_client_segment(path, expected):
    assert client_folder_hint(path) == expected


@pytest.mark.parametrize("path", [
    # THE regression: the firm root is a real canonical business record. Reading the top segment
    # would have anchored ~25k documents onto the firm itself.
    "/drives/b!abc/root:/360 Tax Solutions, LLC/Clients/Tax Preparation/Individual/Sebastian, Britt",
    "/drives/b!abc/root:/360 Tax Solutions, LLC/Clients/Payroll/Inactive/ERE Power LLC",
])
def test_hint_is_never_the_firm_root(path):
    assert client_folder_hint(path) != "360 Tax Solutions, LLC"


@pytest.mark.parametrize("structural", [
    "Tax Preparation", "Individual", "Business", "Payroll", "Sales, Litter & PP Tax",
    "Sales & Litter Tax", "Bookkeeping", "Bookkeeping(1)", "Client Services", "Inactive",
    "Needs To Be Done",
])
def test_structural_segments_are_never_returned_as_a_client(structural):
    """A path that STOPS at a structural segment yields no hint, and one that continues past it
    yields the client below rather than the structure itself."""
    assert client_folder_hint(f"Firm/Clients/{structural}") is None
    assert client_folder_hint(f"Firm/Clients/{structural}/Real Client LLC") == "Real Client LLC"


@pytest.mark.parametrize("path", [
    "",                                                   # nothing at all
    None,                                                 # defensive
    "/drives/b!abc/root:/AWS Migration Backup/CWU2017",   # backup tree, no Clients root
    "/drives/b!abc/root:/Documents 1/THSTranscriptDownloads",
    "/drives/b!abc/root:/360 Tax Solutions, LLC",         # firm root alone
    "Firm/Clients",                                       # Clients root with nothing under it
    "Firm/Clients/1179",                                  # numeric client id, not a name
    "Firm/Clients/Tax Preparation/Individual/2022",       # a YEAR where the client should be
])
def test_unrecognised_structure_fails_closed(path):
    assert client_folder_hint(path) is None


def test_category_and_year_below_the_client_do_not_win():
    """Federal / State / 2019 sit BELOW the client; the walk must stop at the client."""
    assert client_folder_hint("Firm/Clients/Payroll/Active/Ariyan LLC/State/2019") == "Ariyan LLC"


@pytest.mark.parametrize("category", [
    "Fixed Asset List", "Federal", "State", "Unemployment", "Paystubs", "Bank Statements",
])
def test_category_folder_at_the_client_position_fails_closed(category):
    """A work-product folder where the client should be means the client level is missing
    (production: Clients/Sales, Litter & PP Tax/Fixed Asset List/2018, 402 documents). Returning
    the folder — or skipping it and returning the year below — would both be wrong."""
    assert client_folder_hint(f"Firm/Clients/Sales, Litter & PP Tax/{category}") is None
    assert client_folder_hint(f"Firm/Clients/Sales, Litter & PP Tax/{category}/2018") is None


# ---------------------------------------------------------------- D: exact normalisation

def test_middle_initial_does_not_break_identity():
    assert _name_key("CASHMAN, KIMBERLY S") == _name_key("Cashman, Kimberly")


def test_generation_suffixes_normalise():
    assert _name_key("Crosier, William II") == _name_key("William Crosier")
    assert _name_key("Smith, John Jr") == _name_key("John Smith")
    assert _name_key("Doe, Robert IV") == _name_key("Robert Doe")


def test_distinct_people_sharing_a_surname_stay_distinct():
    assert _name_key("Smith, John") != _name_key("Smith, Jane")
    assert _name_key("Cashman, Kimberly") != _name_key("Cashman, Kenneth")


def test_initials_only_name_keeps_its_tokens():
    """Dropping every token would yield the empty key, which matches everything's absence."""
    assert _name_key("J K") == "j k"


def test_same_first_last_different_middle_initials_collapse_and_must_fail_closed():
    """They intentionally share a key. Callers require a UNIQUE match, so two candidates under one
    key is an ambiguous folder — the conservative outcome, not a wrong link."""
    assert _name_key("Smith, John A") == _name_key("Smith, John B")


def test_joint_folders_still_split_into_two_people():
    keys = _folder_person_keys("Philips, Betty & Bill")
    assert len(keys) == 2
    assert _name_key("Betty Philips") in keys and _name_key("Bill Philips") in keys


def test_joint_folder_with_initials_still_splits():
    keys = _folder_person_keys("STOVALL, JEFFERY W & PEGGY S")
    assert _name_key("Jeffery Stovall") in keys
    assert _name_key("Peggy Stovall") in keys


def test_household_suffix_words_are_still_dropped():
    assert _name_key("White Household") == _name_key("White")


# ---------------------------------------------------------------- G: proposals stay out of it

def test_record_builders_do_not_reach_the_proposal_engine(monkeypatch):
    """Deterministic anchoring must not invoke owner proposals. The proposal engine is a separate,
    currently-unsafe mechanism (phone-alone scores HIGH); this change must not widen its reach."""
    import app.services.document_owner_proposal as proposals

    def _boom(*a, **k):                                   # pragma: no cover - must never run
        raise AssertionError("proposal engine invoked from deterministic folder anchoring")

    monkeypatch.setattr(proposals, "analyze_identity", _boom, raising=False)
    assert _delta_item_record("d", _graph_item(INDIVIDUAL), local_path="/tmp/x")["client_folder"]
    assert client_folder_hint(SALES) == "Shrim Inc"
