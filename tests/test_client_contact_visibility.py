"""Contact visibility on the client Overview: canonical values only, and "Not on file" otherwise.

WHAT THIS GUARDS

The Contact card shows primary email, primary phone, mailing address, preferred contact method and
household. Three properties matter more than the layout:

  * every value is CANONICAL — read from ``people``, ``portal_accounts`` and ``households``. Nothing
    is inferred from message traffic. A client whose only email address appears in an Outlook thread
    still reads "Not on file", because the firm has not actually recorded one.
  * an absent field says "Not on file" rather than disappearing. A missing row and a blank row look
    identical to a reader, and only one of them is the truth.
  * it is record-scoped. One client's contact details never appear on another's page.

NO REAL DATA. Fixtures are synthetic: ``@example.test`` addresses and the 555 exchange reserved for
fiction.
"""
from __future__ import annotations

import uuid

import pytest

from app.db import engine, households, people, portal_accounts
from app.security.models import Principal
from app.services.client360 import profile_overview as po

FIRM = Principal(1, "m@e.test", "M", frozenset({"client.read", "tax.read", "record.read_all"}))
SCOPED = Principal(2, "s@e.test", "S", frozenset({"client.read", "tax.read"}))


def _tag() -> str:
    return uuid.uuid4().hex[:10]


def _person(**values) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(active=True, **values)
                         .returning(people.c.id)).scalar_one()


# --- all fields present -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def complete():
    tag = _tag()
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"Complete HH {tag}")
                        .returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Complete Contact {tag}", first_name="Complete", last_name=f"Contact{tag}",
            primary_email=f"complete-{tag}@example.test",
            normalized_email=f"complete-{tag}@example.test",
            primary_phone="(540) 555-0110", normalized_phone="5405550110",
            address_line_1="1 Example Way", city="Roanoke", state="VA", postal_code="24011",
            household_id=hid, active=True).returning(people.c.id)).scalar_one()
    return {"pid": pid, "hid": hid, "tag": tag}


def test_every_contact_field_is_reported_when_all_are_on_file(complete):
    block = po.contact_block(complete["pid"], FIRM)
    assert block["in_scope"] is True
    assert block["phones"] and block["phones"][0]["display"] == "(540) 555-0110"
    assert block["emails"] and block["emails"][0]["display"].endswith("@example.test")
    assert block["address"] and block["address"]["city"] == "Roanoke"
    assert block["address"]["postal_code"] == "24011"
    assert block["household"] and block["household"]["id"] == complete["hid"]


def test_the_primary_email_offers_a_send_action(complete):
    email = po.contact_block(complete["pid"], FIRM)["emails"][0]
    assert email["primary"] is True
    assert email["mailto"] == f"mailto:{email['display']}"


# --- partially missing ------------------------------------------------------------------------

def test_a_person_with_email_and_phone_but_no_address_reports_each_honestly():
    """The shape of the record this work started from: contactable, but no address on file."""
    tag = _tag()
    pid = _person(full_name=f"Partial {tag}", first_name="Partial", last_name=f"Case{tag}",
                  primary_email=f"partial-{tag}@example.test",
                  normalized_email=f"partial-{tag}@example.test",
                  primary_phone="(540) 555-0111", normalized_phone="5405550111")
    block = po.contact_block(pid, FIRM)
    assert block["phones"] and block["emails"]
    assert block["address"] is None
    assert block["household"] is None
    assert block["preferred_contact_method"] is None


def test_a_person_with_an_address_but_no_phone_still_reports_the_address():
    tag = _tag()
    pid = _person(full_name=f"Addr Only {tag}", first_name="Addr", last_name=f"Only{tag}",
                  address_line_1="2 Example Way", city="Salem", state="VA")
    block = po.contact_block(pid, FIRM)
    assert block["phones"] == [] and block["emails"] == []
    assert block["address"] and block["address"]["city"] == "Salem"


# --- nothing on file --------------------------------------------------------------------------

def test_a_person_with_no_contact_information_reports_every_field_absent():
    """Absent must be a stated fact, not an empty structure the template can misread."""
    tag = _tag()
    pid = _person(full_name=f"Empty {tag}", first_name="Empty", last_name=f"Case{tag}")
    block = po.contact_block(pid, FIRM)
    assert block["in_scope"] is True
    assert block["phones"] == []
    assert block["emails"] == []
    assert block["address"] is None
    assert block["preferred_contact_method"] is None
    assert block["household"] is None


def test_the_empty_and_out_of_scope_shapes_carry_the_same_keys():
    """The template walks one shape. A key present in one case and missing in the other is how a
    blank page becomes a traceback."""
    tag = _tag()
    pid = _person(full_name=f"Shape {tag}", first_name="Shape", last_name=f"Case{tag}")
    assert set(po.contact_block(pid, FIRM)) == set(po.contact_block(pid, SCOPED))


# --- canonical vs synchronized ----------------------------------------------------------------

def test_contact_reads_the_canonical_record_not_message_traffic():
    """A client who has exchanged email but has no recorded address is NOT contactable, and the
    Overview must say so. Correspondence is evidence of a conversation, not a stored address."""
    import inspect
    source = inspect.getsource(po.contact_block)
    for forbidden in ("communication_messages", "communication_conversations",
                      "microsoft_accounts", "sender_ref", "microsoft_messages"):
        assert forbidden not in source, \
            f"contact_block reads {forbidden}; contact details must come from the canonical record"


def test_the_only_sources_contact_names_are_canonical_stores():
    """people / portal_accounts / households, plus the Drake import's own record. No mail store."""
    import inspect
    source = inspect.getsource(po.contact_block)
    assert "FROM people" in source or "_person_row" in source
    assert "portal_accounts" in source
    assert "households" in source


def test_a_synchronized_mailbox_address_never_becomes_a_contact_row():
    """Concretely: a person with no primary_email reports no email, even though the firm's mailbox
    may hold messages naming them."""
    tag = _tag()
    pid = _person(full_name=f"Mailbox Only {tag}", first_name="Mailbox", last_name=f"Only{tag}")
    assert po.contact_block(pid, FIRM)["emails"] == []


# --- preferred contact method -------------------------------------------------------------------

def test_preferred_contact_method_is_read_from_the_portal_account():
    tag = _tag()
    pid = _person(full_name=f"Preferred {tag}", first_name="Preferred", last_name=f"Case{tag}")
    with engine.begin() as c:
        c.execute(portal_accounts.insert().values(
            person_id=pid, email=f"pref-{tag}@example.test",
            normalized_email=f"pref-{tag}@example.test", display_name=f"Preferred {tag}",
            status="active", preferred_contact_method="email"))
    assert po.contact_block(pid, FIRM)["preferred_contact_method"] == "email"


def test_a_client_with_no_portal_account_has_no_recorded_preference():
    """Never asked is a different fact from asked and answered, so it is not defaulted."""
    tag = _tag()
    pid = _person(full_name=f"NoPortal {tag}", first_name="NoPortal", last_name=f"Case{tag}")
    assert po.contact_block(pid, FIRM)["preferred_contact_method"] is None


# --- isolation and authorization ------------------------------------------------------------------

def test_one_clients_contact_details_never_appear_on_another(complete):
    """Cross-client isolation, asserted on the values themselves rather than on a count."""
    tag = _tag()
    other = _person(full_name=f"Other {tag}", first_name="Other", last_name=f"Case{tag}",
                    primary_email=f"other-{tag}@example.test",
                    primary_phone="(540) 555-0112", normalized_phone="5405550112")
    block = po.contact_block(other, FIRM)
    assert all(p["display"] != "(540) 555-0110" for p in block["phones"])
    assert all(not e["display"].startswith("complete-") for e in block["emails"])
    assert block["household"] is None, "the other client's household must not leak"


def test_an_out_of_scope_caller_gets_no_contact_details_at_all(complete):
    block = po.contact_block(complete["pid"], SCOPED)
    assert block["in_scope"] is False
    assert block["phones"] == [] and block["emails"] == []
    assert block["address"] is None
    assert block["preferred_contact_method"] is None
    assert block["household"] is None


def test_the_overview_route_still_requires_client_read():
    """Authorization is unchanged by this work — the page gate is the same capability."""
    import inspect

    from app.routes.client360 import client_workspace
    assert 'require_capability("client.read")' in inspect.getsource(client_workspace)


# --- the rendered card ------------------------------------------------------------------------------

def test_the_card_labels_every_absent_field_not_on_file():
    """A row that vanishes and a row that says nothing is recorded read identically to a user, and
    only one of them is honest."""
    from pathlib import Path
    markup = Path(__file__).parents[1].joinpath(
        "app/templates/client360/workspace.html").read_text(encoding="utf-8")
    card = markup.split('class="card c360-profile-contact"', 1)[1].split("</article>", 1)[0]
    assert card.count("Not on file") >= 5, \
        "phone, email, address, preferred contact and household each need an absent state"
    for label in ("Mailing address", "Preferred contact", "Household"):
        assert label in card, f"the Contact card is missing the {label} row"


def test_household_is_stated_once_on_the_overview():
    """It moved from Identity to Contact. Showing it in both stated one fact twice on one screen."""
    from pathlib import Path
    markup = Path(__file__).parents[1].joinpath(
        "app/templates/client360/workspace.html").read_text(encoding="utf-8")
    assert "prof.identity.household" not in markup
    assert "prof.contact.household" in markup
