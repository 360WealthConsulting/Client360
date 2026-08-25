"""Detect the PROSPECT hiding inside a forwarded staff email. Pure, read-only, no I/O.

The problem this exists to solve: when a colleague forwards a prospect's email, Microsoft Graph
reports the FORWARDER as ``from``/``sender``. Trusting that field would file the prospect under the
staff member who pressed Forward -- the platform already has a route that does exactly that
(``/microsoft365/inbox-review`` ``create-contact``), and it is the single worst failure mode of an
email-to-CRM import.

So this module NEVER derives the prospect from Graph sender metadata. The forwarder is reported as
the forwarder and nothing more. A prospect candidate comes only from the quoted forwarded header
block inside the body -- and every field it produces is explicitly heuristic and marked as requiring
staff confirmation. When nothing parses, the candidate comes back empty with a warning; it is never
padded with the nearest available identity.

No database, no network, no Graph client: give it the body text and the sender metadata, get back a
plain dict. That keeps it exhaustively testable against real forward shapes.
"""
from __future__ import annotations

import html as _html
import re

#: Marks the start of a quoted forward. Outlook (English), Apple Mail, and the classic separator.
_FORWARD_MARKERS = (
    re.compile(r"^-+\s*Original Message\s*-+\s*$", re.I | re.M),
    re.compile(r"^-+\s*Forwarded message\s*-+\s*$", re.I | re.M),
    re.compile(r"^Begin forwarded message:\s*$", re.I | re.M),
)
#: Subject prefixes that indicate a forward even when the body quoting is unrecognisable.
_SUBJECT_FORWARD = re.compile(r"^\s*(fw|fwd|tr|wg)\s*:", re.I)

#: A quoted header block: "From:" followed within a few lines by Sent/Date and Subject. Requiring
#: the companion fields is what stops a stray "From:" in prose being read as a forwarded header.
_FROM_LINE = re.compile(r"^\s*(?:From|De|Von)\s*:\s*(?P<value>.+?)\s*$", re.I | re.M)
_SENT_LINE = re.compile(r"^\s*(?:Sent|Date|Enviado|Gesendet)\s*:\s*(?P<value>.+?)\s*$", re.I | re.M)
_TO_LINE = re.compile(r"^\s*(?:To|Para|An)\s*:\s*(?P<value>.+?)\s*$", re.I | re.M)
_SUBJECT_LINE = re.compile(r"^\s*(?:Subject|Asunto|Betreff)\s*:\s*(?P<value>.+?)\s*$", re.I | re.M)

#: "Display Name <addr@example.com>" or a bare address.
_ADDRESSED = re.compile(r"^(?P<name>.*?)[<\[]\s*(?P<email>[^@\s<>\[\]]+@[^@\s<>\[\]]+)\s*[>\]]\s*$")
_BARE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: Deterministic North-American phone shapes only. Anything looser starts matching dates,
#: reference numbers and dollar amounts out of a signature block.
_PHONE = re.compile(
    r"(?<![\d\-])(?:\+?1[\s.\-]*)?"
    r"(?:\(\d{3}\)|\d{3})[\s.\-]+\d{3}[\s.\-]+\d{4}"
    # a trailing sentence period is fine; a period FOLLOWED BY A DIGIT means the run continues and
    # this is not a phone number.
    r"(?![\d\-]|\.\d)")

_TAG = re.compile(r"<[^>]+>")
_DROP_ELEMENTS = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_BREAKS = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h[1-6])\s*/?\s*>", re.I)


def html_to_text(body: str | None) -> str:
    """Flatten an HTML mail body to line-oriented text.

    Deliberately small and dependency-free: drop script/style, turn block ends into newlines, strip
    the remaining tags, unescape entities, and collapse runs of blank lines. Outlook's quoted header
    block survives this intact, which is all the parser needs. This is not a general HTML renderer
    and is never used to display anything -- only to read.
    """
    if not body:
        return ""
    text = _DROP_ELEMENTS.sub(" ", body)
    text = _BREAKS.sub("\n", text)
    text = _TAG.sub("", text)
    text = _html.unescape(text)
    text = text.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_address(value: str | None) -> tuple[str | None, str | None]:
    """('Jane Prospect', 'jane@example.com') from a header value. Either half may be None."""
    raw = (value or "").strip().strip(";,")
    if not raw:
        return None, None
    m = _ADDRESSED.match(raw)
    if m:
        name = m.group("name").strip().strip('"').strip().strip(",")
        return (name or None), m.group("email").strip().lower()
    m = _BARE_EMAIL.search(raw)
    if m:
        # A bare address on the line; anything before it is a display name.
        name = raw[:m.start()].strip().strip('"').strip().strip("-,")
        return (name or None), m.group(0).lower()
    # A display name with no address at all is still worth surfacing for confirmation.
    return raw.strip('"') or None, None


def _header_block_starts(text: str) -> list[int]:
    """Every index where a quoted header block begins, in order.

    ALL of them, not just the first: a forwarded reply carries the prospect's message followed by the
    earlier thread quoted underneath, and each quoted level starts its own header block. Knowing
    where the SECOND one begins is what stops the parser reading the forwarder's quoted signature as
    if it were the prospect's.
    """
    markers = [m.start() for p in _FORWARD_MARKERS for m in p.finditer(text)]
    starts = set(markers)
    # A "From:" line corroborated by Sent/Date AND Subject nearby is the Outlook inline shape,
    # which often carries no separator line at all.
    for m in _FROM_LINE.finditer(text):
        window = text[m.start():m.start() + 600]
        if not (_SENT_LINE.search(window) and _SUBJECT_LINE.search(window)):
            continue
        # A separator and the From: line directly beneath it are ONE block, not two. Detected by the
        # gap between them being blank rather than by a distance guess, so a genuinely nested block
        # after a very short reply is still recognised as its own block.
        prev = max((k for k in markers if k <= m.start()), default=None)
        if prev is not None:
            line_end = text.find("\n", prev)
            gap = text[line_end + 1:m.start()] if line_end != -1 else text[prev:m.start()]
            if not gap.strip():
                continue
        starts.add(m.start())
    return sorted(starts)


def original_message_region(text: str) -> tuple[int, int] | None:
    """(start, end) of the ORIGINAL message -- the prospect's own content -- or None.

    Everything BEFORE ``start`` is the forwarder's preamble and signature; everything from ``end``
    onwards is the older thread the prospect quoted. Candidate identity and contact details are read
    only from between the two, which is the whole correction: in production the forwarder's office
    number sat in her signature quoted BELOW the prospect's reply, and a scan that ran to the end of
    the message picked it up as the prospect's phone.
    """
    starts = _header_block_starts(text)
    if not starts:
        return None
    start = starts[0]
    end = next((s for s in starts if s > start), len(text))
    return start, end


def _body_after_headers(region: str) -> str:
    """The prospect's own words: the region with its From/Sent/To/Subject block removed.

    Cut after the Subject line (the last header the corroboration requires), so a recipient name on
    the ``To:`` line can never be mistaken for the prospect's signature.
    """
    last = None
    for pattern in (_SUBJECT_LINE, _TO_LINE, _SENT_LINE, _FROM_LINE):
        m = pattern.search(region)
        if m and (last is None or m.end() > last):
            last = m.end()
    return region[last:] if last is not None else region


def _first(pattern, text):
    m = pattern.search(text)
    return m.group("value").strip() if m else None


def _phone_in(text: str) -> str | None:
    """One phone number, or None. Ambiguity is reported as no phone rather than a guess."""
    found = {re.sub(r"[^\d]", "", p) for p in _PHONE.findall(text)}
    found = {p for p in found if len(p) in (10, 11)}
    if len(found) != 1:
        return None
    digits = found.pop()
    return digits


#: One name token: starts with a letter, then letters/apostrophes/hyphens/dots. No digits anywhere.
_NAME_TOKEN = re.compile(r"^[A-Za-z][A-Za-z'\u2019.\-]+$")
_NAME_MAX = 80


def looks_like_human_name(value: str | None, *, email: str | None = None) -> bool:
    """Whether a detected string is safe to PREFILL as a person's name.

    Deliberately strict, because the cost is asymmetric: refusing a real name makes staff type it,
    while accepting a machine identifier writes it into the client record. The production case that
    motivated this is ``ctbvmi01`` -- a mailbox/user handle the parser lifted out of a signature.

    Requires at least two whitespace-separated tokens, each starting with a letter and at least two
    letters long, with no digits and no ``@`` anywhere; rejects a value identical to the candidate
    email's local part. ``Jane Prospect`` passes; ``ctbvmi01``, ``tillmanbowling`` and
    ``jane.prospect`` do not.
    """
    v = " ".join((value or "").split())
    if not v or len(v) > _NAME_MAX:
        return False
    if "@" in v or any(ch.isdigit() for ch in v):
        return False
    tokens = v.split()
    if len(tokens) < 2:
        return False
    for t in tokens:
        if not _NAME_TOKEN.match(t) or len(t.strip("'\u2019.-")) < 2:
            return False
    if email and "@" in email:
        # The local part dressed up as a name is provenance, not an identity.
        if v.casefold() == email.split("@", 1)[0].strip().casefold():
            return False
    return True


def split_human_name(value: str | None) -> tuple[str | None, str | None]:
    """(first, last) for a value that already passed :func:`looks_like_human_name`.

    Everything after the first token is the surname, so double-barrelled and multi-word family names
    survive intact rather than being silently truncated.
    """
    tokens = " ".join((value or "").split()).split()
    if len(tokens) < 2:
        return None, None
    return tokens[0], " ".join(tokens[1:])


#: A signature name line: 2-4 Capitalised alphabetic tokens and nothing else. Much stricter than
#: looks_like_human_name on its own, because ordinary prose would otherwise qualify -- "Thanks for
#: your help" is four alphabetic tokens, but its lowercase words disqualify it here.
_SIG_MAX_TOKENS = 4
_SIG_MAX_CHARS = 60


def _signature_name(body: str, *, forwarder_name=None, email=None) -> str | None:
    """A human name from the prospect's OWN sign-off, or None.

    Scans the prospect's body only, and takes the LAST qualifying line because that is where a
    sign-off sits. Every candidate must additionally clear ``looks_like_human_name``, so a machine
    handle cannot arrive by this route either. The forwarder's own name is refused outright.
    """
    forwarder = " ".join((forwarder_name or "").split()).casefold()
    found = None
    for raw in (body or "").splitlines():
        line = " ".join(raw.split()).strip(" ,;:-")
        if not line or len(line) > _SIG_MAX_CHARS:
            continue
        tokens = line.split()
        if not 2 <= len(tokens) <= _SIG_MAX_TOKENS:
            continue
        if not all(t[:1].isupper() for t in tokens):
            continue
        if not looks_like_human_name(line, email=email):
            continue
        if line.casefold() == forwarder:
            continue
        found = line
    return found


def extract_candidate(*, body: str | None, body_is_html: bool = True, subject: str | None = None,
                      graph_from_name: str | None = None,
                      graph_from_email: str | None = None) -> dict:
    """Preview-only view of who a forwarded message is actually ABOUT.

    ``graph_from_*`` is recorded as the FORWARDER and is never promoted to the candidate. Returns
    ``candidate_*`` fields that are always heuristic (``requires_confirmation`` is unconditionally
    True) and a ``warnings`` list a UI can render verbatim.
    """
    text = html_to_text(body) if body_is_html else (body or "").strip()
    forwarder_email = (graph_from_email or "").strip().lower() or None
    forwarder_name = (graph_from_name or "").strip() or None

    result = {
        "is_forwarded": False,
        "forwarder_name": forwarder_name,
        "forwarder_email": forwarder_email,
        "candidate_name": None,
        "candidate_email": None,
        "candidate_phone": None,
        "candidate_source": None,
        #: The original From display name exactly as it appeared -- provenance for the UI. It is
        #: NOT the candidate name: a machine handle lands here and nowhere else.
        "raw_from_name": None,
        "confidence": "none",
        # Unconditional: nothing this module produces may be written without a human saying so.
        "requires_confirmation": True,
        "original_subject": None,
        "original_sent": None,
        "original_to": None,
        "warnings": [],
    }

    bounds = original_message_region(text)
    subject_says_forward = bool(_SUBJECT_FORWARD.match(subject or ""))
    result["is_forwarded"] = bool(bounds is not None or subject_says_forward)

    if not result["is_forwarded"]:
        # Direct mail. The sender may well BE the prospect, but proving that is a separate decision
        # with its own confirmation step; this commit does not attribute it automatically.
        result["warnings"].append(
            "This message does not look forwarded. No prospect was detected from the sender — "
            "direct-sender attribution is not performed here.")
        return result

    if bounds is None:
        result["warnings"].append(
            "The subject looks forwarded but no original 'From:' block could be read. "
            "Enter the prospect's details manually — the sender above is the forwarder.")
        return result

    # Region A (before start) is the FORWARDER's preamble and signature and is never read for
    # candidate fields. Region B ends where the next quoted header block begins, so the older thread
    # the prospect quoted underneath -- typically containing the forwarder's own signature -- is
    # excluded too.
    start, end = bounds
    region = text[start:end]
    body = _body_after_headers(region)
    from_value = _first(_FROM_LINE, region)
    result["original_sent"] = _first(_SENT_LINE, region)
    result["original_to"] = _first(_TO_LINE, region)
    result["original_subject"] = _first(_SUBJECT_LINE, region)

    name, email = _split_address(from_value)
    result["raw_from_name"] = name          # provenance: shown, never written
    if email and forwarder_email and email == forwarder_email:
        # The quoted block names the forwarder — a reply chain rather than a third-party forward.
        result["warnings"].append(
            "The quoted message is from the forwarder themselves, so no separate prospect was "
            "detected.")
        return result

    if not name and not email:
        result["warnings"].append(
            "A forwarded block was found but the original sender could not be read. "
            "Enter the prospect's details manually.")
        return result

    result["candidate_email"] = email
    result["candidate_source"] = "forwarded_header"
    result["confidence"] = "heuristic"

    # Name, in order of trust: the original From display name when it is actually a name, otherwise
    # the prospect's own sign-off. A handle like "ctbvmi01" fails the first and is never substituted
    # by the email local part -- if the sign-off yields nothing, the field stays empty for staff.
    if looks_like_human_name(name, email=email):
        result["candidate_name"] = name
    else:
        signature = _signature_name(body, forwarder_name=forwarder_name, email=email)
        if signature:
            result["candidate_name"] = signature
            result["candidate_source"] = "original_signature"
        elif name:
            result["warnings"].append(
                f"The original sender is shown as \u201c{name}\u201d, which does not look like a "
                "person's name, and no name was found in their message. Enter it manually.")

    # Phone comes ONLY from the prospect's own body. Scanning further picked up the forwarder's
    # office number out of her quoted signature and produced a false existing-client match.
    result["candidate_phone"] = _phone_in(body)
    if result["candidate_phone"] and result["candidate_source"] == "forwarded_header":
        result["candidate_source"] = "forwarded_header+signature"

    result["warnings"].append(
        "Detected from the forwarded message text. Every field is a suggestion and must be "
        "confirmed before anything is created.")
    if not email:
        result["warnings"].append("No email address was found for the detected prospect.")
    return result
