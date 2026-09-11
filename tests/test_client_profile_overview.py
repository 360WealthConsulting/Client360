"""Client 360 Overview profile: Contact, Identity, Investments and Tax snapshot.

WHAT THIS FILE IS PROTECTING

The Overview used to render no profile at all — nothing under ``app/templates/client360/``
referenced a phone, an email or an address — so a client whose number was sitting in
``people.primary_phone`` appeared to have none. These tests pin the four blocks, and more
importantly they pin the two properties that are easy to lose in a later edit:

  * the page carries the SSN's last four digits and never the whole number, and
  * every block refuses a caller outside the client's record scope.

NO REAL DATA. Every fixture below is synthetic and obviously so: the SSN is 900-00-xxxx (the 900
block is not issued), phones are in the 555 exchange reserved for fiction, and emails are
``@example.test``. Nothing in this file, its assertions or its failure output can carry a real
client's details.
"""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from sqlalchemy import insert, text

from app.db import engine, households, people
from app.security.models import Principal
from app.services.client360 import profile_overview as po

# record.read_all puts the caller in scope for any client; SCOPED holds neither an assignment nor
# the bypass, which is what makes it the out-of-scope case.
FIRM = Principal(1, "m@e.test", "M", frozenset({"client.read", "tax.read", "record.read_all"}))
SCOPED = Principal(2, "s@e.test", "S", frozenset({"client.read", "tax.read"}))
NO_TAX = Principal(3, "n@e.test", "N", frozenset({"client.read", "record.read_all"}))

#: Synthetic identifiers. 900-xx-xxxx is never issued by the SSA, and 555-01xx is the reserved
#: fiction exchange, so neither can collide with a real person.
FAKE_SSN = "900001234"
FAKE_SSN_LAST4 = "1234"
FAKE_CELL = "(540) 555-0101"
FAKE_DAY = "540-555-0102"
FAKE_EVE = "5405550103"

_state: dict = {}


def _tag() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture(scope="module", autouse=True)
def seeded():
    """One household, one person, one Drake row, two Schwab accounts — all synthetic."""
    tag = _tag()
    email = f"profile-{tag}@example.test"
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Profile HH {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(
            full_name=f"Profile Tester {tag}", first_name="Profile", last_name=f"Tester{tag}",
            primary_email=email, normalized_email=email,
            primary_phone=FAKE_CELL, normalized_phone="5405550101",
            address_line_1="1 Example Way", city="Roanoke", state="VA",
            birth_date=date(1980, 4, 2), active=True, household_id=hid,
        ).returning(people.c.id)).scalar_one()

        if c.execute(text("SELECT to_regclass('public.drake_client_returns')")).scalar():
            raw = json.dumps({
                "TP_Social": FAKE_SSN, "TP_DoB": "04021980",
                "TP_Cell_Phone": FAKE_CELL, "TP_Day_Phone": FAKE_DAY, "TP_Eve_Phone": FAKE_EVE,
                "Address": "1 Example Way", "City": "Roanoke", "State": "VA", "Zip": "24011",
                "Email": f"personal-{tag}@example.test",
            })
            c.execute(text("""
                INSERT INTO drake_client_returns
                  (tax_year, source_row_number, source_updated_at, taxpayer_identifier_hash,
                   taxpayer_first_name, taxpayer_last_name,
                   return_type, filing_status, agi, federal_product, federal_ack_code,
                   federal_ack_date, state_product, state_ack_code, state_ack_date, raw_data)
                VALUES (2024, 1, now(), :h, 'Profile', :ln, '1040', 'MFJ', 123456, '1040', 'A',
                        '2025-03-01', 'VA', 'R', '2025-03-02', :raw)
            """), {"h": f"hash-{tag}", "ln": f"Tester{tag}", "raw": raw})

        for name, kind, value in (("Brokerage", "Individual", 250000), ("Roth", "Roth IRA", 75000)):
            c.execute(text("""
                INSERT INTO accounts (person_id, custodian, account_name, account_number,
                                      registration_type, status, total_value)
                VALUES (:p, 'Schwab', :n, :num, :r, 'open', :v)
            """), {"p": pid, "n": f"{name} {tag}", "num": f"ACCT-{tag}-{name[:2]}",
                   "r": kind, "v": value})
    _state.update(pid=pid, hid=hid, tag=tag, email=email)
    return _state


# --- normalization and deduplication -------------------------------------------------------------

@pytest.mark.parametrize("written", ["(540) 555-0101", "540-555-0101", "540.555.0101",
                                     "5405550101", "+1 540 555 0101", "1-540-555-0101"])
def test_one_phone_written_six_ways_normalizes_to_one_key(written):
    """Client360 and Drake write the same number differently; formatting is not identity."""
    assert po.normalize_phone(written) == "5405550101"


def test_a_number_with_no_digits_has_no_identity():
    assert po.normalize_phone("n/a") is None
    assert po.normalize_phone("") is None
    assert po.normalize_phone(None) is None


def test_emails_normalize_case_and_whitespace():
    assert po.normalize_email("  Person@Example.TEST ") == "person@example.test"
    assert po.normalize_email("") is None


def test_the_same_number_from_two_systems_is_one_row_naming_both(seeded):
    """The seeded person's Client360 phone and Drake's TP_Cell_Phone are the same number written
    differently. It must appear once, credited to both — that corroboration is the signal."""
    block = po.contact_block(seeded["pid"], FIRM)
    keys = [po.normalize_phone(p["display"]) for p in block["phones"]]
    assert len(keys) == len(set(keys)), "the same phone was listed twice"
    primary = next(p for p in block["phones"] if p["primary"])
    assert set(primary["sources"]) == {"Client360", "Drake"}


# --- Contact ---------------------------------------------------------------------------------------

def test_contact_shows_every_phone_type_drake_records(seeded):
    block = po.contact_block(seeded["pid"], FIRM)
    labels = {p["label"] for p in block["phones"]}
    assert {"primary", "work", "home"} <= labels
    assert all(p["display"] for p in block["phones"])


def test_phones_are_formatted_for_reading(seeded):
    block = po.contact_block(seeded["pid"], FIRM)
    assert any(p["display"] == "(540) 555-0103" for p in block["phones"]), \
        "a bare 10-digit Drake number should be formatted, not shown raw"


def test_contact_shows_the_primary_email_first_and_labels_the_rest(seeded):
    block = po.contact_block(seeded["pid"], FIRM)
    assert block["emails"], "the seeded person has an email and it must be listed"
    assert block["emails"][0]["primary"] is True
    assert block["emails"][0]["display"] == seeded["email"]
    assert any(e["label"] == "additional" for e in block["emails"]), \
        "Drake's Email key is a second address and should be offered, labelled"


def test_every_email_carries_a_send_action_and_its_source(seeded):
    for email in po.contact_block(seeded["pid"], FIRM)["emails"]:
        assert email["mailto"] == f"mailto:{email['display']}"
        assert email["sources"], "an address with no named source cannot be judged"
        assert "verified" in email


def test_the_mailing_address_prefers_client360_and_says_so(seeded):
    address = po.contact_block(seeded["pid"], FIRM)["address"]
    assert address and address["source"] == "Client360"
    assert address["city"] == "Roanoke" and address["state"] == "VA"


# --- Identity --------------------------------------------------------------------------------------

def test_identity_reports_only_the_last_four_ssn_digits(seeded):
    block = po.identity_block(seeded["pid"], FIRM)
    assert block["ssn_last4"] == FAKE_SSN_LAST4
    assert len(block["ssn_last4"]) == 4
    assert block["ssn_on_file"] is True


def test_the_full_ssn_never_appears_anywhere_in_the_profile(seeded):
    """The load-bearing assertion. Every block is searched for the complete number in each shape it
    could take, because a masked display is worth nothing if the full value rides along elsewhere."""
    profile = po.overview_profile(seeded["pid"], FIRM)
    blob = json.dumps(profile, default=str)
    for shape in (FAKE_SSN, "900-00-1234", "900 00 1234"):
        assert shape not in blob, f"the full SSN leaked into the profile as {shape!r}"


def test_date_of_birth_prefers_client360_over_drake(seeded):
    block = po.identity_block(seeded["pid"], FIRM)
    assert block["birth_date"] == date(1980, 4, 2)
    assert block["birth_date_source"] == "Client360"


@pytest.mark.parametrize("written,expected", [
    ("04021980", date(1980, 4, 2)),      # Drake's bare MMDDYYYY, the corpus format
    ("12311999", date(1999, 12, 31)),
    ("04/02/1980", date(1980, 4, 2)),
    ("1980-04-02", date(1980, 4, 2)),
])
def test_drake_dates_parse_as_month_day_year(written, expected):
    assert po._parse_drake_date(written) == expected


@pytest.mark.parametrize("written", ["", None, "not a date", "13451980", "0402"])
def test_an_unreadable_date_stays_blank_rather_than_being_guessed(written):
    assert po._parse_drake_date(written) is None


def test_identity_reports_the_household(seeded):
    household = po.identity_block(seeded["pid"], FIRM)["household"]
    assert household and household["id"] == seeded["hid"]
    assert household["member_count"] >= 1


def test_dependents_are_reported_as_untracked_never_as_zero(seeded):
    """Nothing in the schema records dependents. A confident 0 on a client with three children is
    worse than an honest blank, so the block must say "unknown", not "none"."""
    for block in (po.identity_block(seeded["pid"], FIRM), po.tax_block(seeded["pid"], FIRM)):
        assert block["dependents"] is None
        assert block["dependents_available"] is False


# --- Investments -----------------------------------------------------------------------------------

def test_investments_group_by_custodian_and_total(seeded):
    block = po.investments_block(seeded["pid"], FIRM)
    assert block["account_count"] == 2
    schwab = next(g for g in block["custodians"] if g["custodian"] == "Schwab")
    assert len(schwab["accounts"]) == 2
    assert schwab["subtotal"] == 325000
    assert block["total_value"] == 325000


def test_each_account_carries_its_type_and_balance(seeded):
    schwab = next(g for g in po.investments_block(seeded["pid"], FIRM)["custodians"]
                  if g["custodian"] == "Schwab")
    types = {a["account_type"] for a in schwab["accounts"]}
    assert {"Individual", "Roth IRA"} == types
    assert all(a["value_available"] for a in schwab["accounts"])


def test_grouping_is_generic_so_a_new_custodian_needs_no_code_change(seeded):
    """AssetMark holds no accounts today. The grouping is driven by the data, so it appears the
    moment a row arrives rather than requiring this module to name it.

    The docstring names both custodians to explain that, so only executable code is inspected —
    a hard-coded name in a branch is the defect, a name in prose is documentation."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(po.investments_block).strip())
    function = tree.body[0]
    body = function.body[1:] if ast.get_docstring(function) else function.body
    code = "\n".join(ast.dump(node) for node in body)
    for custodian in ("AssetMark", "Schwab"):
        assert custodian not in code, f"{custodian} is named in the grouping logic"


# --- Tax snapshot ----------------------------------------------------------------------------------

def test_tax_snapshot_reports_the_latest_year_and_its_figures(seeded):
    block = po.tax_block(seeded["pid"], FIRM)
    assert block["tax_year"] == 2024
    assert block["filing_status"] == "MFJ"
    assert block["agi"] is not None


def test_federal_and_state_efile_states_are_reported_separately(seeded):
    """The seeded return is federally accepted and rejected by the state — one status for both
    would hide exactly the case staff need to act on."""
    block = po.tax_block(seeded["pid"], FIRM)
    assert block["federal"]["status"] == "accepted"
    assert block["state"]["status"] == "rejected"
    assert block["federal"]["acknowledged_at"] is not None


@pytest.mark.parametrize("code,product,expected", [
    ("A", "1040", "accepted"), ("a", "1040", "accepted"),
    ("R", "1040", "rejected"), ("D", "1040", "rejected"),
    ("", "1040", "in_progress"), (None, "1040", "in_progress"),
])
def test_acknowledgement_codes_map_to_a_status(code, product, expected):
    assert po._efile_state(code, None, product)["status"] == expected


def test_a_return_never_transmitted_reports_nothing_rather_than_in_progress():
    assert po._efile_state(None, None, None) is None


# --- scope -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("block", ["contact_block", "identity_block", "investments_block", "tax_block"])
def test_every_block_refuses_a_caller_outside_the_record_scope(seeded, block):
    """Scope is checked per block so one unavailable section cannot fail the whole page — but that
    only holds if every block actually checks."""
    result = getattr(po, block)(seeded["pid"], SCOPED)
    assert result["in_scope"] is False
    for key in ("phones", "emails", "custodians"):
        assert result.get(key, []) == []
    for key in ("ssn_last4", "birth_date", "address", "tax_year"):
        assert result.get(key) is None


def test_an_out_of_scope_caller_gets_no_ssn_digits_at_all(seeded):
    blob = json.dumps(po.overview_profile(seeded["pid"], SCOPED), default=str)
    assert FAKE_SSN_LAST4 not in blob
    assert FAKE_SSN not in blob


# --- the reveal route ------------------------------------------------------------------------------

def test_revealing_an_ssn_requires_the_tax_capability_not_merely_client_read():
    """Opening a client is client.read. Unmasking their identifier is a narrower authority, so the
    route declares tax.read — otherwise every reader of a record could unmask it."""
    import inspect

    from app.routes.client360 import reveal_ssn
    source = inspect.getsource(reveal_ssn)
    assert 'require_capability("tax.read")' in source


def test_revealing_an_ssn_is_record_scoped_and_audited_before_it_answers():
    import inspect

    from app.routes.client360 import reveal_ssn
    source = inspect.getsource(reveal_ssn)
    assert "record_in_scope" in source
    assert "write_audit_event" in source
    assert source.index("write_audit_event") < source.index("return JSONResponse"), \
        "the audit entry must be written before the value is returned"


def test_the_reveal_response_is_not_cacheable():
    import inspect

    from app.routes.client360 import reveal_ssn
    assert "no-store" in inspect.getsource(reveal_ssn)


def test_the_audit_entry_carries_no_part_of_the_number():
    import inspect

    from app.routes.client360 import reveal_ssn
    source = inspect.getsource(reveal_ssn)
    metadata = source.split("metadata={", 1)[1].split("}", 1)[0]
    assert "ssn" not in metadata.lower()


def test_the_reveal_route_takes_the_person_from_the_path_not_a_query_string():
    """A client id in a query string ends up in access logs and referrers; a path parameter is the
    same information but is at least not appended to every outbound link."""
    import inspect

    from app.routes.client360 import reveal_ssn
    assert "person_id: int" in inspect.getsource(reveal_ssn)


# --- the rendered page -----------------------------------------------------------------------------

def test_the_template_renders_only_the_masked_value_and_fetches_the_rest(seeded):
    """A CSS eye-toggle would put the whole number in every page load. The template must emit the
    mask, and the reveal must be a fetch."""
    from pathlib import Path
    markup = Path("app/templates/client360/workspace.html").read_text(encoding="utf-8")
    assert "ssn_last4" in markup
    assert "prof.identity.ssn" in markup
    assert "TP_Social" not in markup, "the template must never read the raw identifier"
    script = Path("app/static/js/client_profile.js").read_text(encoding="utf-8")
    assert "/ssn" in script and "fetch(" in script


def test_the_overview_renders_contact_details_at_all(seeded):
    """The original defect: nothing under client360/ referenced a phone, an email or an address."""
    from pathlib import Path
    markup = Path("app/templates/client360/workspace.html").read_text(encoding="utf-8")
    for needle in ("prof.contact.phones", "prof.contact.emails", "prof.contact.address"):
        assert needle in markup
    assert "mailto" in markup, "an email with no send action is a dead end"
