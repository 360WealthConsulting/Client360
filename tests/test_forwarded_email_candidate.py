"""Forwarded-email candidate extraction — pure, and above all NEVER the forwarder.

The failure mode these fixtures exist to prevent: Lauren forwards a prospect's email to Michael, and
the import files LAUREN as the prospect because Graph reports her as ``from``. Every fixture here
carries Lauren as the Graph sender, so any rule that leaked the sender into the candidate would fail
loudly across the whole file.
"""
from __future__ import annotations

import pytest

from app.services.forwarded_email import extract_candidate, html_to_text

LAUREN = {"graph_from_name": "Lauren Ross", "graph_from_email": "lauren@360wealth.test"}

# --- A. Outlook-style inline forward, signature phone -------------------------------------------
OUTLOOK_INLINE = """<html><body>
<p>Michael — see below, new enquiry that came in this morning. Can you take it?</p>
<p>Lauren</p>
<div><hr>
<b>From:</b> Jane Prospect &lt;jane@example.com&gt;<br>
<b>Sent:</b> Monday, August 24, 2026 9:14 AM<br>
<b>To:</b> Lauren Ross &lt;lauren@360wealth.test&gt;<br>
<b>Subject:</b> Tax liability on a property sale<br>
</div>
<p>Hi Lauren,</p>
<p>We sold a rental last month and I am worried about the tax. Are you taking new clients?</p>
<p>Best,<br>Jane Prospect<br>Prospect Holdings LLC<br>(415) 555-0134<br>jane@example.com</p>
</body></html>"""

# --- B. Forwarded, but the original header is unreadable ----------------------------------------
FORWARD_NO_HEADER = """<html><body>
<p>Michael, forwarding this one — the original got mangled by their mail system.</p>
<p>-----Original Message-----</p>
<p>(content could not be displayed)</p>
</body></html>"""

# --- C. Not a forward at all --------------------------------------------------------------------
DIRECT = """<html><body><p>Michael, are we still on for Thursday at 10? — Lauren</p></body></html>"""

# --- reply chain: the quoted block is the forwarder themselves ----------------------------------
SELF_QUOTED = """<html><body>
<p>Bumping this.</p>
<div><b>From:</b> Lauren Ross &lt;lauren@360wealth.test&gt;<br>
<b>Sent:</b> Friday, August 21, 2026 4:02 PM<br>
<b>To:</b> Michael Shelton &lt;michael@360wealth.test&gt;<br>
<b>Subject:</b> Staffing<br></div>
</body></html>"""


def test_a_outlook_inline_forward_detects_the_original_sender():
    r = extract_candidate(body=OUTLOOK_INLINE, subject="FW: Tax liability on a property sale",
                          **LAUREN)
    assert r["is_forwarded"] is True
    assert r["forwarder_name"] == "Lauren Ross"
    assert r["forwarder_email"] == "lauren@360wealth.test"
    assert r["candidate_name"] == "Jane Prospect"
    assert r["candidate_email"] == "jane@example.com"
    assert r["candidate_phone"] == "4155550134"
    assert r["candidate_source"] == "forwarded_header+signature"
    assert r["confidence"] == "heuristic"
    assert r["requires_confirmation"] is True
    assert r["original_subject"] == "Tax liability on a property sale"


def test_a_carries_the_original_sent_and_to_context():
    r = extract_candidate(body=OUTLOOK_INLINE, subject="FW: Tax liability", **LAUREN)
    assert "August 24, 2026" in r["original_sent"]
    assert "Lauren Ross" in r["original_to"]


def test_b_forward_with_no_readable_header_yields_a_blank_candidate():
    r = extract_candidate(body=FORWARD_NO_HEADER, subject="FW: enquiry", **LAUREN)
    assert r["is_forwarded"] is True
    assert r["candidate_name"] is None
    assert r["candidate_email"] is None
    assert r["candidate_phone"] is None
    assert r["confidence"] == "none"
    assert r["warnings"]
    # the whole point
    assert r["candidate_email"] != r["forwarder_email"]
    assert r["candidate_name"] != r["forwarder_name"]


def test_c_non_forwarded_message_makes_no_prospect_attribution():
    r = extract_candidate(body=DIRECT, subject="Thursday", **LAUREN)
    assert r["is_forwarded"] is False
    assert r["candidate_name"] is None and r["candidate_email"] is None
    assert r["confidence"] == "none"
    assert any("does not look forwarded" in w for w in r["warnings"])


def test_quoted_block_from_the_forwarder_is_not_a_prospect():
    r = extract_candidate(body=SELF_QUOTED, subject="FW: Staffing", **LAUREN)
    assert r["candidate_email"] is None
    assert r["candidate_name"] is None
    assert any("forwarder themselves" in w for w in r["warnings"])


@pytest.mark.parametrize("subject,body", [
    ("FW: Tax liability on a property sale", OUTLOOK_INLINE),
    ("FW: enquiry", FORWARD_NO_HEADER),
    ("Thursday", DIRECT),
    ("FW: Staffing", SELF_QUOTED),
    ("Fwd: anything", None),
    ("", ""),
])
def test_the_forwarder_is_never_promoted_to_prospect(subject, body):
    """The single invariant of this module, asserted across every fixture shape."""
    r = extract_candidate(body=body, subject=subject, **LAUREN)
    assert r["candidate_email"] != LAUREN["graph_from_email"]
    assert r["candidate_name"] != LAUREN["graph_from_name"]
    assert r["forwarder_email"] == LAUREN["graph_from_email"]
    assert r["requires_confirmation"] is True


def test_graph_sender_alone_never_produces_a_candidate():
    """No body at all: a sender exists, so a naive implementation would use it."""
    r = extract_candidate(body="", subject="FW: something", **LAUREN)
    assert r["candidate_name"] is None and r["candidate_email"] is None


def test_bare_address_forward_header_is_read():
    body = ("<p>fyi</p><div>From: jane@example.com<br>Sent: Mon 9:14 AM<br>"
            "To: lauren@360wealth.test<br>Subject: Hello<br></div>")
    r = extract_candidate(body=body, subject="FW: Hello", **LAUREN)
    assert r["candidate_email"] == "jane@example.com"


def test_ambiguous_phone_numbers_yield_no_phone():
    """Two different numbers in the signature is ambiguity, not a coin flip."""
    body = OUTLOOK_INLINE.replace("(415) 555-0134", "(415) 555-0134<br>(628) 555-0199")
    r = extract_candidate(body=body, subject="FW: x", **LAUREN)
    assert r["candidate_phone"] is None
    assert r["candidate_email"] == "jane@example.com"


def test_a_from_in_prose_is_not_a_forward_header():
    body = "<p>I heard from Jane that she may call. From: the referral list.</p>"
    r = extract_candidate(body=body, subject="Notes", **LAUREN)
    assert r["is_forwarded"] is False
    assert r["candidate_email"] is None


def test_plain_text_body_is_supported():
    body = ("Michael - see below.\n\n-----Original Message-----\n"
            "From: Jane Prospect <jane@example.com>\nSent: Mon\nTo: Lauren\nSubject: Hi\n\n"
            "Call me on 415-555-0134.\n")
    r = extract_candidate(body=body, body_is_html=False, subject="FW: Hi", **LAUREN)
    assert r["candidate_email"] == "jane@example.com"
    assert r["candidate_phone"] == "4155550134"


def test_html_to_text_is_pure_and_drops_scripts():
    out = html_to_text("<style>p{color:red}</style><script>x=1</script><p>Hello</p><p>World</p>")
    assert "color:red" not in out and "x=1" not in out
    assert out.splitlines() == ["Hello", "World"]


def test_extraction_never_raises_on_odd_input():
    for body in (None, "", "<<<", "&amp;", "From:", "From: <>"):
        r = extract_candidate(body=body, subject=None, **LAUREN)
        assert r["requires_confirmation"] is True


# --------------------------------------------------------------------------------------------
# Region boundary: candidate fields come ONLY from the prospect's own message.
#
# Production shape that exposed this. The subject is "Fw:" but the quoted message is a REPLY, so
# the prospect's words sit between Lauren's forwarding preamble above and the older thread quoted
# below -- and that older thread carries Lauren's signature, including her office number. Scanning
# to the end of the message read her phone as the prospect's and produced a false client match.

PRODUCTION_FORWARD = """<html><body>
<p>Meeting tomorrow - referral from Tate</p>
<p>Lauren Curry<br>Office: 540.562.0123<br>lauren@360wealthconsulting.com<br>
1017 2nd Street SW<br>Roanoke, VA 24016</p>
<p>--------------------------------</p>
<p>From: ctbvmi01 &lt;tillmanbowling@gmail.com&gt;<br>
Sent: Monday, August 24, 2026 8:07 PM<br>
To: Lauren Curry &lt;lauren@360wealthconsulting.com&gt;<br>
Subject: Re: Tax liability</p>
<p>Lauren, thanks for getting back to me. Sold a rental and I'm worried about the hit.</p>
<p>Thanks,<br>Tillman Bowling</p>
<p>From: Lauren Curry &lt;lauren@360wealthconsulting.com&gt;<br>
Sent: Monday, August 24, 2026 3:12 PM<br>
To: ctbvmi01<br>
Subject: Tax liability</p>
<p>Happy to help. Here is my info.</p>
<p>Lauren Curry<br>Office: 540.562.0123<br>lauren@360wealthconsulting.com</p>
</body></html>"""

CURRY = {"graph_from_name": "Lauren Curry",
         "graph_from_email": "lauren@360wealthconsulting.com"}


def _production():
    return extract_candidate(body=PRODUCTION_FORWARD, subject="Fw: Tax liability", **CURRY)


def test_production_shape_reads_the_prospect_not_the_forwarder():
    r = _production()
    assert r["forwarder_email"] == "lauren@360wealthconsulting.com"
    assert r["candidate_email"] == "tillmanbowling@gmail.com"
    assert r["candidate_name"] == "Tillman Bowling"
    assert r["candidate_source"] == "original_signature"


def test_the_forwarders_office_number_is_never_the_candidate_phone():
    """Lauren's 540.562.0123 appears twice -- above the boundary and in the quoted thread below."""
    assert "540.562.0123" in PRODUCTION_FORWARD
    assert _production()["candidate_phone"] is None


def test_the_machine_handle_stays_provenance_only():
    r = _production()
    assert r["raw_from_name"] == "ctbvmi01"
    assert r["candidate_name"] != "ctbvmi01"


def test_the_prospects_own_number_is_still_read():
    body = PRODUCTION_FORWARD.replace(
        "<p>Thanks,<br>Tillman Bowling</p>",
        "<p>Call me at 540-555-1212</p><p>Thanks,<br>Tillman Bowling</p>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **CURRY)
    assert r["candidate_phone"] == "5405551212"
    assert r["candidate_name"] == "Tillman Bowling"


def test_nothing_is_read_from_the_forwarder_region():
    """Region A holds Lauren's name, email, phone and address. None may surface as the candidate."""
    r = _production()
    for field in ("candidate_name", "candidate_email", "candidate_phone"):
        assert r[field] != "Lauren Curry"
        assert r[field] != "lauren@360wealthconsulting.com"
        assert r[field] != "5405620123"


def test_region_stops_at_the_next_quoted_header_block():
    from app.services.forwarded_email import html_to_text, original_message_region
    text = html_to_text(PRODUCTION_FORWARD)
    start, end = original_message_region(text)
    region = text[start:end]
    assert "tillmanbowling@gmail.com" in region          # the prospect's own header
    assert "Tillman Bowling" in region                   # his sign-off
    assert "Office: 540.562.0123" not in region          # the quoted thread below is excluded
    assert "1017 2nd Street SW" not in text[start:]      # the preamble above is excluded


def test_a_name_is_never_invented_when_the_signature_has_none():
    body = PRODUCTION_FORWARD.replace("<p>Thanks,<br>Tillman Bowling</p>", "<p>Thanks</p>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **CURRY)
    assert r["candidate_name"] is None
    assert r["candidate_email"] == "tillmanbowling@gmail.com"    # email still read
    assert any("does not look like" in w for w in r["warnings"])


def test_prose_is_not_mistaken_for_a_signature_name():
    """Lowercase sentence words must not qualify, even though they are alphabetic tokens."""
    body = PRODUCTION_FORWARD.replace(
        "<p>Thanks,<br>Tillman Bowling</p>", "<p>thanks for your help</p>")
    assert extract_candidate(body=body, subject="Fw: x", **CURRY)["candidate_name"] is None


def test_the_forwarders_name_in_the_prospect_region_is_refused():
    body = PRODUCTION_FORWARD.replace("<p>Thanks,<br>Tillman Bowling</p>",
                                      "<p>Thanks,<br>Lauren Curry</p>")
    assert extract_candidate(body=body, subject="Fw: x", **CURRY)["candidate_name"] is None
