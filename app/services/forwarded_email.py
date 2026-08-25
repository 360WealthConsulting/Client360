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


def _forward_block_start(text: str) -> int | None:
    """Index where the quoted forward begins, or None if no recognisable block exists."""
    starts = [m.start() for p in _FORWARD_MARKERS if (m := p.search(text))]
    # A "From:" line corroborated by Sent/Date AND Subject nearby is the Outlook inline shape,
    # which often carries no separator line at all.
    for m in _FROM_LINE.finditer(text):
        window = text[m.start():m.start() + 600]
        if _SENT_LINE.search(window) and _SUBJECT_LINE.search(window):
            starts.append(m.start())
            break
    return min(starts) if starts else None


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
        "confidence": "none",
        # Unconditional: nothing this module produces may be written without a human saying so.
        "requires_confirmation": True,
        "original_subject": None,
        "original_sent": None,
        "original_to": None,
        "warnings": [],
    }

    start = _forward_block_start(text)
    subject_says_forward = bool(_SUBJECT_FORWARD.match(subject or ""))
    result["is_forwarded"] = bool(start is not None or subject_says_forward)

    if not result["is_forwarded"]:
        # Direct mail. The sender may well BE the prospect, but proving that is a separate decision
        # with its own confirmation step; this commit does not attribute it automatically.
        result["warnings"].append(
            "This message does not look forwarded. No prospect was detected from the sender — "
            "direct-sender attribution is not performed here.")
        return result

    if start is None:
        result["warnings"].append(
            "The subject looks forwarded but no original 'From:' block could be read. "
            "Enter the prospect's details manually — the sender above is the forwarder.")
        return result

    block = text[start:start + 1200]
    from_value = _first(_FROM_LINE, block)
    result["original_sent"] = _first(_SENT_LINE, block)
    result["original_to"] = _first(_TO_LINE, block)
    result["original_subject"] = _first(_SUBJECT_LINE, block)

    name, email = _split_address(from_value)
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

    result["candidate_name"] = name
    result["candidate_email"] = email
    result["candidate_source"] = "forwarded_header"
    result["confidence"] = "heuristic"
    # The signature normally sits BELOW the quoted header, so look there for a phone number.
    result["candidate_phone"] = _phone_in(text[start:])
    if result["candidate_phone"]:
        result["candidate_source"] = "forwarded_header+signature"

    result["warnings"].append(
        "Detected from the forwarded message text. Every field is a suggestion and must be "
        "confirmed before anything is created.")
    if not email:
        result["warnings"].append("No email address was found for the detected prospect.")
    return result
