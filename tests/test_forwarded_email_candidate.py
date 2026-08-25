"""Forwarded-email candidate extraction — pure, and above all NEVER the forwarder.

The failure mode these fixtures exist to prevent: Lauren forwards a prospect's email to Michael, and
the import files LAUREN as the prospect because Graph reports her as ``from``. Every fixture here
carries Lauren as the Graph sender, so any rule that leaked the sender into the candidate would fail
loudly across the whole file.
"""
from __future__ import annotations

import pytest

from app.services.forwarded_email import (
    extract_candidate,
    html_to_text,
    same_identity,
)

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


# --------------------------------------------------------------------------------------------
# THE REAL PRODUCTION SHAPE: an Outlook forward wrapping a GMAIL reply.
#
# cc0e19d passed its tests and still failed here, because its fixture quoted the older thread in
# Outlook's From:/Sent:/Subject: form. The prospect is on gmail.com, so his client quoted Lauren
# with "On ... wrote:" inside a <blockquote> -- no header block at all. The region therefore ran to
# the bottom of the message and her quoted signature supplied both the name and the phone.
#
# Exchange also reports the forwarder's display name "Last, First", which is what walked past the
# old exact-string guard: "Curry, Lauren" != "Lauren Curry".

REAL_FORWARD = """<html><body>
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
<blockquote>
<p>On Mon, Aug 24, 2026 at 3:12 PM Lauren Curry &lt;lauren@360wealthconsulting.com&gt; wrote:</p>
<p>Happy to help.</p>
<p>Lauren Curry<br>Wealth Advisor<br>Office: 540.562.0123<br>
lauren@360wealthconsulting.com</p>
</blockquote></body></html>"""

#: Exchange "Last, First" display name -- the form that defeated the previous guard.
EXCHANGE = {"graph_from_name": "Curry, Lauren",
            "graph_from_email": "lauren@360wealthconsulting.com"}


def _real(**over):
    kw = {"body": REAL_FORWARD, "subject": "Fw: Tax liability", **EXCHANGE}
    kw.update(over)
    return extract_candidate(**kw)


def test_real_shape_produces_the_prospect_not_the_forwarder():
    r = _real()
    assert r["raw_from_name"] == "ctbvmi01"
    assert r["candidate_email"] == "tillmanbowling@gmail.com"
    assert r["candidate_name"] == "Tillman Bowling"
    assert r["candidate_phone"] is None
    assert r["candidate_source"] == "original_signature"
    assert r["requires_confirmation"] is True


def test_exchange_last_first_display_name_cannot_become_the_candidate():
    """The exact bypass: "Curry, Lauren" vs a signature reading "Lauren Curry"."""
    r = _real()
    assert not same_identity(r["candidate_name"], "Lauren Curry")
    assert r["candidate_name"] != "Lauren Curry"


@pytest.mark.parametrize("variant", [
    "Curry, Lauren", "Lauren Curry", "Lauren Curry RFC", "CURRY, LAUREN",
    "Curry,Lauren", "Lauren  Curry", "Lauren M Curry",
])
def test_forwarder_identity_variants_all_fail_the_gate(variant):
    """Punctuation, order, case, spacing and credential variants are one identity."""
    body = REAL_FORWARD.replace("<p>Thanks,<br>Tillman Bowling</p>",
                                f"<p>Thanks,<br>{variant}</p>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **EXCHANGE)
    assert r["candidate_name"] is None, variant


def test_a_job_title_cannot_become_the_candidate_name():
    """"Wealth Advisor" is two capitalised words and satisfies a bare name predicate."""
    body = REAL_FORWARD.replace("<p>Thanks,<br>Tillman Bowling</p>",
                                "<p>Regards,</p><p>Wealth Advisor</p><p>Senior Partner</p>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **EXCHANGE)
    assert r["candidate_name"] in (None, "Wealth Advisor")
    # the ambiguity rule must not let a SECOND title win over the first line after the sign-off
    assert r["candidate_name"] != "Senior Partner"


def test_ambiguous_capitalised_lines_without_a_signoff_yield_no_name():
    body = REAL_FORWARD.replace(
        "<p>Thanks,<br>Tillman Bowling</p>",
        "<p>Blue Ridge Partners</p><p>Roanoke Virginia</p>")
    assert extract_candidate(body=body, subject="Fw: x", **EXCHANGE)["candidate_name"] is None


def test_the_forwarders_office_phone_never_reaches_the_candidate():
    assert "540.562.0123" in REAL_FORWARD
    assert _real()["candidate_phone"] is None


def test_the_nested_quoted_email_cannot_become_the_candidate_email():
    r = _real()
    assert r["candidate_email"] != "lauren@360wealthconsulting.com"


def test_no_candidate_field_resolves_to_the_forwarder():
    r = _real()
    assert not same_identity(r["candidate_name"], r["forwarder_name"])
    assert r["candidate_email"] != r["forwarder_email"]
    assert r["candidate_phone"] != "5405620123"


def test_the_prospects_own_phone_is_still_read_in_the_real_shape():
    body = REAL_FORWARD.replace("<p>Thanks,<br>Tillman Bowling</p>",
                                "<p>Call me at 540-555-1212</p><p>Thanks,<br>Tillman Bowling</p>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **EXCHANGE)
    assert r["candidate_phone"] == "5405551212"
    assert r["candidate_name"] == "Tillman Bowling"


# ------------------------------------------------------------------ structural boundaries
def _region_text(body):
    from app.services.forwarded_email import original_message_region
    text = html_to_text(body)
    start, end = original_message_region(text)
    return text[start:end if end is not None else len(text)]


HEADERS = ("<p>From: ctbvmi01 &lt;tillmanbowling@gmail.com&gt;<br>Sent: Mon<br>"
           "To: Lauren<br>Subject: Re: Tax liability</p>"
           "<p>My message.</p><p>Thanks,<br>Tillman Bowling</p>")
NESTED_SIG = "<p>Lauren Curry<br>Office: 540.562.0123</p>"


@pytest.mark.parametrize("label,body", [
    ("gmail attribution",
     f"<p>fyi</p>{HEADERS}<p>On Mon, Aug 24 Lauren Curry &lt;lauren@x.test&gt; wrote:</p>{NESTED_SIG}"),
    ("blockquote, no attribution text",
     f"<p>fyi</p>{HEADERS}<blockquote>{NESTED_SIG}</blockquote>"),
    ("outlook divRplyFwdMsg",
     f'<p>fyi</p>{HEADERS}<div id="divRplyFwdMsg">{NESTED_SIG}</div>'),
    ("outlook border-top div",
     f'<p>fyi</p>{HEADERS}<div style="border-top:1px solid #ccc">{NESTED_SIG}</div>'),
    # SUPERSEDED SHAPE: these two were an <hr> / a rule of underscores followed by bare quoted
    # text -- no attribution, no header. That is presentation markup, and treating it as proof of
    # quoting is what cut the prospect's region off before his own signature in production. Both now
    # carry the corroboration a genuine quoted thread actually has.
    ("outlook hr separator + nested header",
     f"<p>fyi</p>{HEADERS}<hr><p>From: Lauren Curry &lt;lauren@x.test&gt;<br>Sent: Mon<br>"
     f"To: x<br>Subject: Tax liability</p>{NESTED_SIG}"),
    ("outlook nested header block",
     f"<p>fyi</p>{HEADERS}<p>From: Lauren Curry &lt;lauren@x.test&gt;<br>Sent: Mon<br>"
     f"To: x<br>Subject: Tax liability</p>{NESTED_SIG}"),
    ("plain separator line + attribution",
     f"<p>fyi</p>{HEADERS}<p>______________________________</p>"
     f"<p>On Mon, Aug 24 Lauren Curry &lt;lauren@x.test&gt; wrote:</p>{NESTED_SIG}"),
])
def test_every_quote_structure_excludes_the_nested_signature(label, body):
    region = _region_text(body)
    assert "Tillman Bowling" in region, label          # the prospect's own sign-off is kept
    assert "540.562.0123" not in region, label         # the nested signature is not
    r = extract_candidate(body=body, subject="Fw: x",
                          graph_from_name="Curry, Lauren", graph_from_email="lauren@x.test")
    assert r["candidate_name"] == "Tillman Bowling", label
    assert r["candidate_phone"] is None, label


def test_an_unbounded_contaminated_region_fails_closed():
    """No recognisable end AND the forwarder's details inside it -> no name, no phone."""
    # One header block, and NO recognisable end at all: no separator, no blockquote, no
    # attribution line. The forwarder's own address then appears in the free text below.
    body = ("<p>fyi</p>"
            "<p>From: ctbvmi01 &lt;tillmanbowling@gmail.com&gt;<br>Sent: Mon<br>To: L<br>"
            "Subject: Re: Tax</p>"
            "<p>some text</p><p>Lauren Curry</p><p>Office: 540.562.0123</p>"
            "<p>lauren@360wealthconsulting.com</p>")
    r = extract_candidate(body=body, subject="Fw: x", **EXCHANGE)
    assert r["candidate_phone"] is None
    assert r["candidate_name"] is None
    assert any("could not be determined" in w for w in r["warnings"])


def test_the_internal_sentinel_never_leaks_into_output():
    from app.services.forwarded_email import QUOTE_SENTINEL
    r = _real()
    for value in r.values():
        assert QUOTE_SENTINEL not in str(value)


# --------------------------------------------------------------------------------------------
# Presentation markup is not evidence of quoting.
#
# fbde3aa treated a bare <hr> as a quote boundary. In production the prospect's own signature sat
# below one, so the region ended 17 characters before his name and the parser fell back to None --
# safe, but wrong. A rule only terminates the region when quoted content actually follows it.

_PROSPECT_HEADER = ("<p>From: ctbvmi01 &lt;tillmanbowling@gmail.com&gt;<br>Sent: Mon 8:07 PM<br>"
                    "To: Lauren Curry &lt;lauren@360wealthconsulting.com&gt;<br>"
                    "Subject: Re: Tax liability</p>")
_PREAMBLE = ("<p>Meeting tomorrow</p><p>Lauren Curry<br>Office: 540.562.0123<br>"
             "lauren@360wealthconsulting.com</p><p>-----------------------------</p>")
_LAUREN_SIG = "<p>Lauren Curry<br>Office: 540.562.0123</p>"


def _shape(tail):
    return extract_candidate(body=f"<html><body>{_PREAMBLE}{_PROSPECT_HEADER}{tail}</body></html>",
                             subject="Fw: Tax liability", **EXCHANGE)


@pytest.mark.parametrize("label,tail", [
    ("bare <hr> above the signature",
     "<p>Sold a rental.</p><hr><p>Thanks,<br>Tillman Bowling</p>"),
    ("<hr> between sign-off and name",
     "<p>Sold a rental.</p><p>Thanks,</p><hr><p>Tillman Bowling</p>"),
    ("dashed rule inside the signature",
     "<p>Sold a rental.</p><p>Thanks,</p><p>-----</p><p>Tillman Bowling</p>"),
    ("underscore rule inside the signature",
     "<p>Sold a rental.</p><p>________________________</p><p>Thanks,<br>Tillman Bowling</p>"),
])
def test_a_prospect_owned_rule_does_not_truncate_his_own_region(label, tail):
    """8A: the rule belongs to HIS message, so his name must still be found."""
    r = _shape(tail + "<blockquote><p>On Mon Lauren Curry wrote:</p>" + _LAUREN_SIG +
               "</blockquote>")
    assert r["candidate_name"] == "Tillman Bowling", label
    assert r["candidate_source"] == "original_signature", label
    assert r["candidate_phone"] is None, label


@pytest.mark.parametrize("label,tail", [
    ("<hr> then a nested header block",
     "<p>Sold a rental.</p><p>Thanks,<br>Tillman Bowling</p><hr>"
     "<p>From: Lauren Curry &lt;lauren@360wealthconsulting.com&gt;<br>Sent: Mon<br>"
     "To: x<br>Subject: Tax liability</p>" + _LAUREN_SIG),
    ("<hr> then a Gmail attribution",
     "<p>Sold a rental.</p><p>Thanks,<br>Tillman Bowling</p><hr>"
     "<p>On Mon, Aug 24 Lauren Curry &lt;lauren@360wealthconsulting.com&gt; wrote:</p>"
     + _LAUREN_SIG),
    ("<hr> then a quote container",
     "<p>Sold a rental.</p><p>Thanks,<br>Tillman Bowling</p><hr>"
     "<blockquote>" + _LAUREN_SIG + "</blockquote>"),
])
def test_b_a_corroborated_rule_still_ends_the_region(label, tail):
    """8B: quoted content follows the rule, so the nested signature stays excluded."""
    text = html_to_text(f"<html><body>{_PREAMBLE}{_PROSPECT_HEADER}{tail}</body></html>")
    from app.services.forwarded_email import original_message_region
    start, end = original_message_region(text)
    region = text[start:end if end is not None else len(text)]
    assert "Tillman Bowling" in region, label
    assert "540.562.0123" not in region, label
    r = _shape(tail)
    assert r["candidate_name"] == "Tillman Bowling", label
    assert r["candidate_phone"] is None, label


def test_the_weak_rule_sentinel_is_distinct_from_a_quote_container():
    """A container is emitted BECAUSE content is quoted; a rule is just presentation."""
    from app.services.forwarded_email import QUOTE_SENTINEL, RULE_SENTINEL, html_to_text
    assert QUOTE_SENTINEL != RULE_SENTINEL
    assert RULE_SENTINEL in html_to_text("<p>a</p><hr><p>b</p>")
    assert QUOTE_SENTINEL not in html_to_text("<p>a</p><hr><p>b</p>")
    assert QUOTE_SENTINEL in html_to_text("<p>a</p><blockquote>b</blockquote>")


def test_an_uncorroborated_rule_before_the_forwarders_own_details_still_fails_closed():
    """The rule is not trusted, so the region is unbounded -- and the contamination gate then
    refuses to guess a name or phone out of what follows."""
    r = _shape("<p>Sold a rental.</p><p>Thanks,<br>Tillman Bowling</p><hr>" + _LAUREN_SIG)
    assert r["candidate_phone"] is None
    assert r["candidate_name"] is None
    assert any("could not be determined" in w for w in r["warnings"])


def test_neither_sentinel_leaks_into_output():
    from app.services.forwarded_email import QUOTE_SENTINEL, RULE_SENTINEL
    r = _shape("<p>Body.</p><hr><p>Thanks,<br>Tillman Bowling</p>"
               "<blockquote><p>On Mon Lauren Curry wrote:</p>" + _LAUREN_SIG + "</blockquote>")
    for value in r.values():
        assert QUOTE_SENTINEL not in str(value) and RULE_SENTINEL not in str(value)


# --------------------------------------------------------------------------------------------
# AMBIGUOUS regions: a divider that might have ended the message, and nothing trustworthy after it.
#
# The one shape the corroboration rule left open. A weak rule is (correctly) not trusted as a
# boundary, but no strong container, attribution or nested header ever appears either -- so the
# parser cannot prove where the prospect's message stops, and the quoted content below it is
# unattributable. With no forwarder detail in the preamble to trigger the contamination gate,
# nothing else would have caught it.

_AMBIG_HEADER = ("<p>fyi</p><p>From: ctbvmi01 &lt;tillmanbowling@gmail.com&gt;<br>"
                 "Sent: Monday, August 24, 2026 8:07 PM<br>To: Lauren Curry<br>"
                 "Subject: Re: Tax liability</p>")
#: Bare quoted content: no attribution line, no nested header, no quote container.
_BARE_QUOTED = "<p>Lauren Curry<br>Office: 540.562.0123</p>"


@pytest.mark.parametrize("rule", ["<hr>", "<p>--------------</p>", "<p>______________</p>"])
def test_an_ambiguous_region_yields_no_heuristic_name_or_phone(rule):
    """A-H: machine-like From, prospect body, uncorroborated rule, bare quoted content below,
    no attribution, no nested header, no container, and NO forwarder phone in region A."""
    body = f"<html><body>{_AMBIG_HEADER}<p>Sold a rental.</p>{rule}{_BARE_QUOTED}</body></html>"
    r = extract_candidate(body=body, subject="Fw: Tax liability", **EXCHANGE)
    assert r["candidate_email"] == "tillmanbowling@gmail.com"    # from the recognised From header
    assert r["candidate_name"] is None
    assert r["candidate_phone"] is None
    assert r["requires_confirmation"] is True
    assert any("could not be determined" in w for w in r["warnings"])


def test_a_human_from_display_name_survives_an_ambiguous_region():
    """Ambiguity suppresses BODY heuristics only. A name already on the From header is not a guess,
    so it stands unless the forwarder-contamination checks reject it."""
    body = (f"<html><body>{_AMBIG_HEADER.replace('ctbvmi01', 'Jane Prospect')}"
            f"<p>Sold a rental.</p><hr>{_BARE_QUOTED}</body></html>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **EXCHANGE)
    assert r["candidate_name"] == "Jane Prospect"
    assert r["candidate_phone"] is None


def test_a_forwarder_named_from_header_is_still_rejected_under_ambiguity():
    """The contamination gate outranks the From header even when the body is ambiguous."""
    body = (f"<html><body>{_AMBIG_HEADER.replace('ctbvmi01', 'Curry, Lauren')}"
            f"<p>text</p><hr>{_BARE_QUOTED}</body></html>")
    assert extract_candidate(body=body, subject="Fw: x", **EXCHANGE)["candidate_name"] is None


def test_a_prospect_phone_before_an_ambiguous_rule_is_not_silently_accepted():
    """His number may be genuine, but the region boundary is unproven, so the phone fails closed."""
    body = (f"<html><body>{_AMBIG_HEADER}<p>Call me at 540-555-1212</p><hr>"
            f"{_BARE_QUOTED}</body></html>")
    assert extract_candidate(body=body, subject="Fw: x", **EXCHANGE)["candidate_phone"] is None


def test_a_later_trustworthy_boundary_removes_the_ambiguity():
    """The real production structure: the same uncorroborated <hr>, but a blockquote does follow."""
    body = (f"<html><body>{_PREAMBLE}{_PROSPECT_HEADER}<p>Sold a rental.</p><hr>"
            f"<p>Thanks,<br>Tillman Bowling</p>"
            f"<blockquote><p>On Mon Lauren Curry wrote:</p>{_LAUREN_SIG}</blockquote></body></html>")
    r = extract_candidate(body=body, subject="Fw: Tax liability", **EXCHANGE)
    assert r["candidate_name"] == "Tillman Bowling"
    assert r["candidate_email"] == "tillmanbowling@gmail.com"
    assert r["candidate_phone"] is None
    assert r["candidate_source"] == "original_signature"
    assert not any("could not be determined" in w for w in r["warnings"])


def test_a_weak_rule_outside_the_region_does_not_make_it_ambiguous():
    """The separator above the prospect's own header precedes the region and is irrelevant."""
    r = _real()
    assert r["candidate_name"] == "Tillman Bowling"
    assert not any("could not be determined" in w for w in r["warnings"])
