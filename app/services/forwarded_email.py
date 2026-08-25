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

#: Marks where a mail client wrapped a QUOTED thread. A private-use codepoint, so nothing in a real
#: message can forge it. It exists only inside the flattened text this module parses and is never
#: rendered -- the page shows Graph's own bodyPreview, not this.
QUOTE_SENTINEL = "quoted"

#: Structural quote containers, in the markup the major clients actually emit. Recognising the
#: CONTAINER matters more than recognising its text: clients disagree about attribution wording but
#: all of them wrap the quoted thread in something. Flattening used to discard these outright, which
#: is how the prospect's region ran on into the forwarder's quoted signature.
_QUOTE_CONTAINERS = (
    re.compile(r"<\s*blockquote\b[^>]*>", re.I),                      # Gmail, Apple Mail, generic
    re.compile(r'<\s*div\b[^>]*class="[^"]*gmail_quote[^"]*"[^>]*>', re.I),
    re.compile(r'<\s*div\b[^>]*id="?divRplyFwdMsg"?[^>]*>', re.I),    # Outlook / OWA
    re.compile(r'<\s*div\b[^>]*style="[^"]*border-top[^"]*"[^>]*>', re.I),   # Outlook desktop
)

#: A horizontal rule is PRESENTATION, not evidence of quoting. Outlook does draw one above a
#: forwarded header, but people also put one above their own signature -- and treating the two
#: alike cut the prospect's region off 17 characters before his own name. It gets a separate,
#: WEAKER marker that only counts as a boundary when quoted content actually follows it.
RULE_SENTINEL = "\ue001rule\ue001"
_RULE_CONTAINERS = (re.compile(r"<\s*hr\b[^>]*>", re.I),)


def html_to_text(body: str | None) -> str:
    """Flatten an HTML mail body to line-oriented text, PRESERVING quote boundaries.

    Deliberately small and dependency-free: drop script/style, mark quoted containers with
    :data:`QUOTE_SENTINEL`, turn block ends into newlines, strip the remaining tags, unescape
    entities, collapse blank runs. This is not a general HTML renderer and is never used to display
    anything -- only to read.
    """
    if not body:
        return ""
    text = _DROP_ELEMENTS.sub(" ", body)
    # Before the tags are stripped: a quote container becomes a sentinel LINE, so the structural
    # boundary survives into the flattened text even when the client's attribution wording does not.
    for pattern in _QUOTE_CONTAINERS:
        text = pattern.sub(f"\n{QUOTE_SENTINEL}\n", text)
    for pattern in _RULE_CONTAINERS:
        text = pattern.sub(f"\n{RULE_SENTINEL}\n", text)
    text = _BREAKS.sub("\n", text)
    text = _TAG.sub("", text)
    text = _html.unescape(text)
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_sentinels(value: str | None) -> str | None:
    """Remove the internal marker from anything that could reach a caller."""
    if value is None:
        return None
    return " ".join(value.replace(QUOTE_SENTINEL, " ")
                    .replace(RULE_SENTINEL, " ").split()) or None


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


#: Attribution lines that open a quoted thread WITHOUT any From:/Sent:/Subject: block. Gmail and
#: Apple Mail use this form, which is why the production message -- an Outlook forward wrapping a
#: GMAIL reply -- had no second header block for the old code to find.
_QUOTE_ATTRIBUTION = re.compile(
    r"^\s*(?:On\b.{0,300}?\bwrote\s*:|Le\b.{0,300}?\ba \u00e9crit\s*:|"
    r"Am\b.{0,300}?\bschrieb\s*:)\s*$", re.I | re.M)
#: A rule of dashes/underscores on its own line -- Outlook's plain-text separator.
_SEPARATOR_LINE = re.compile(r"^\s*[-_=]{4,}\s*$", re.M)
_SENTINEL_LINE = re.compile(re.escape(QUOTE_SENTINEL))
_RULE_LINE = re.compile(re.escape(RULE_SENTINEL))


def _quoted_content_follows(text: str, pos: int) -> bool:
    """Whether QUOTED content begins immediately after a presentation marker at ``pos``.

    "Immediately" is the whole point. Looking merely *somewhere* ahead would re-break the case this
    exists for: a rule above the prospect's signature is followed a line later by his name and then,
    a little further on, by the real quoted thread -- so any lookahead wide enough to be useful
    would find the quote and cut his name off anyway. The next non-blank content itself has to be
    the quote.
    """
    after = text[pos:]
    newline = after.find("\n")
    rest = after[newline + 1:] if newline != -1 else ""
    head = rest.lstrip(" \t\n")[:600]
    if head.startswith(QUOTE_SENTINEL) or head.startswith(RULE_SENTINEL):
        return True
    if _QUOTE_ATTRIBUTION.match(head):
        return True
    if _FROM_LINE.match(head):
        return bool(_SENT_LINE.search(head) and _SUBJECT_LINE.search(head))
    return False


def _uncorroborated_weak_markers(text: str, after: int) -> list[int]:
    """Presentation rules after ``after`` that were NOT accepted as boundaries.

    Each one is a place the prospect's message might have ended. Individually harmless; but if the
    parser never finds a trustworthy boundary afterwards either, it genuinely cannot say where his
    message stops, and anything below could belong to somebody else.
    """
    out = []
    for pattern in (_RULE_LINE, _SEPARATOR_LINE):
        out += [m.start() for m in pattern.finditer(text)
                if m.start() > after and not _quoted_content_follows(text, m.start())]
    return sorted(out)


def _quote_boundaries(text: str) -> list[int]:
    """Indexes where a quoted thread begins, from structure or attribution rather than headers.

    These only ever END the candidate region; they never start it, because none of them tells us who
    the original sender was.

    STRONG boundaries stand alone: a quote container is markup a client emits *because* the content
    is quoted. WEAK ones -- a horizontal rule, a row of dashes or underscores -- are presentation a
    sender may use anywhere, including above their own signature, so they only count when quoted
    content genuinely follows.
    """
    out = [m.start() for m in _SENTINEL_LINE.finditer(text)]
    out += [m.start() for m in _QUOTE_ATTRIBUTION.finditer(text)]
    for pattern in (_RULE_LINE, _SEPARATOR_LINE):
        out += [m.start() for m in pattern.finditer(text) if _quoted_content_follows(text, m.start())]
    return sorted(out)


def original_message_region(text: str) -> tuple[int, int | None] | None:
    """(start, end) of the ORIGINAL message -- the prospect's own content -- or None.

    Everything BEFORE ``start`` is the forwarder's preamble and signature; everything from ``end``
    onwards is the older thread the prospect quoted.

    ``end`` is the EARLIEST trustworthy nested-quote boundary after the prospect's own header: the
    next header block, a structural quote container, or an attribution line. ``end`` is ``None``
    when no boundary can be established -- which the caller treats as a reason for caution, NOT as
    licence to read to the bottom of the message. Defaulting to end-of-text is exactly what let the
    forwarder's quoted signature supply the candidate's name and phone.
    """
    starts = _header_block_starts(text)
    if not starts:
        return None
    start = starts[0]
    candidates = [s for s in starts if s > start] + [b for b in _quote_boundaries(text) if b > start]
    return start, (min(candidates) if candidates else None)


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
    body = region[last:] if last is not None else region
    # The markers are boundary metadata, not content. Blank the lines rather than delete them so
    # line positions -- which the sign-off rule depends on -- are preserved.
    return body.replace(QUOTE_SENTINEL, "").replace(RULE_SENTINEL, "")


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


def identity_tokens(value: str | None) -> frozenset[str]:
    """Order- and punctuation-insensitive tokens of a person name.

    ``"Curry, Lauren"`` and ``"Lauren Curry"`` reduce to the same set, which is the point: the old
    guard compared raw strings and an Exchange "Last, First" display name walked straight past it.
    """
    cleaned = re.sub(r"[^A-Za-z\s]", " ", value or "")
    return frozenset(t.casefold() for t in cleaned.split() if len(t) >= 2)


def same_identity(a: str | None, b: str | None) -> bool:
    """Whether two name strings denote the same person, generically.

    Equal token sets, or one a subset of the other so a credential/middle-name variant
    (``"Lauren Curry RFC"``) cannot slip past. The subset rule needs at least two tokens on the
    smaller side, so a shared first name alone never collapses two different people.
    """
    ta, tb = identity_tokens(a), identity_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    return min(len(ta), len(tb)) >= 2 and (ta <= tb or tb <= ta)


def _phones_in(text: str) -> set[str]:
    """Every normalised phone number in a chunk of text."""
    return {re.sub(r"[^\d]", "", p) for p in _PHONE.findall(text or "")
            if len(re.sub(r"[^\d]", "", p)) in (10, 11)}


#: Sign-off words that immediately precede a name. The strongest evidence a line IS a name.
_SIGNOFF = re.compile(
    r"^\s*(thanks|thank you|many thanks|thanks again|regards|best|best regards|kind regards|"
    r"warm regards|sincerely|cheers|warmly|respectfully|talk soon)\s*[,.!]?\s*$", re.I)


def _signature_name(body: str, *, forwarder_name=None, email=None) -> str | None:
    """A human name from the prospect's OWN sign-off, or None.

    Scans the prospect's body only, and takes the LAST qualifying line because that is where a
    sign-off sits. Every candidate must additionally clear ``looks_like_human_name``, so a machine
    handle cannot arrive by this route either. The forwarder's own name is refused outright.
    """
    qualifying = []          # (index, line) for every line that could be a name
    signoff_at = None
    lines = [" ".join(r.split()).strip(" ,;:-") for r in (body or "").splitlines()]
    for i, line in enumerate(lines):
        if _SIGNOFF.match(line or ""):
            signoff_at = i
            continue
        if not line or len(line) > _SIG_MAX_CHARS:
            continue
        tokens = line.split()
        if not 2 <= len(tokens) <= _SIG_MAX_TOKENS:
            continue
        if not all(t[:1].isupper() for t in tokens):
            continue
        if not looks_like_human_name(line, email=email):
            continue
        if same_identity(line, forwarder_name):
            continue
        qualifying.append((i, line))

    if not qualifying:
        return None
    # Strongest evidence: the first qualifying line AFTER a sign-off ("Thanks," / "Regards,").
    # Taking the LAST qualifying line instead is what let a job title on the line beneath the name
    # win -- "Wealth Advisor" is two capitalised words and looks exactly like a name to a predicate.
    if signoff_at is not None:
        after = [line for i, line in qualifying if i > signoff_at]
        if after:
            return after[0]
    # No sign-off anchor: accept a name only when the body offers exactly ONE candidate. Several
    # capitalised two-word lines with nothing to choose between them is ambiguity, and a wrong
    # identity is worse than an empty field.
    return qualifying[0][1] if len(qualifying) == 1 else None


def _quoted_header_identities(text: str, starts: list[int]) -> list[tuple[str | None, str | None]]:
    """(display name, email) from every RECOGNISED quoted header block after the prospect's own.

    Only parsed ``From:`` lines of corroborated header blocks -- never free text. A quoted header is
    structured metadata a mail client wrote, so a display name found there is an assertion about who
    sent that message, not a guess about a line that happens to look like a name.
    """
    out = []
    for i, block_start in enumerate(starts[1:], start=1):
        block_end = starts[i + 1] if i + 1 < len(starts) else len(text)
        value = _first(_FROM_LINE, text[block_start:block_end])
        if value:
            out.append(_split_address(value))
    return out


def _corroborated_name(text, starts, *, email, forwarder_name):
    """A fuller display name for the SAME address, taken from a later quoted header block.

    Real threads routinely carry the prospect's own earlier message further down, and that quoted
    header often spells out the name their current client abbreviates. The production case: the
    forwarded header reads ``ctbvmi01 <tillmanbowling@gmail.com>`` and the body is signed only
    "- Tillman", while the quoted header below reads
    ``Tillman Bowling <tillmanbowling@gmail.com>``.

    The address is the key, and it must match EXACTLY. It was already established from the
    recognised original ``From:`` header, so a later header bearing the same address is another
    statement about the same person -- whereas a different address is a different person, and quoted
    prose is not a statement about anyone. The name still has to clear the human-name predicate and
    the forwarder-identity gate; nothing here is derived from the local part of the address.
    """
    if not email:
        return None
    target = email.strip().casefold()
    for qname, qemail in _quoted_header_identities(text, starts):
        if not qname or not qemail or qemail.strip().casefold() != target:
            continue
        if not looks_like_human_name(qname, email=email):
            continue
        if same_identity(qname, forwarder_name):
            continue
        return qname
    return None


def _apply_forwarder_gate(result: dict, forwarder_phones: set[str]) -> None:
    """Last line of defence: the candidate may never resolve to the KNOWN forwarder.

    Deliberately independent of region parsing. If a future mail format defeats boundary detection
    the way Gmail's attribution line defeated the previous version, this still holds -- so the
    failure mode becomes an empty field rather than a colleague filed as a prospect.

    Generic by construction: it compares the candidate against whatever Graph reported as the
    sender of THIS message, and against the phone numbers in THIS message's forwarder region. No
    name, address or number is hard-coded.
    """
    if result["candidate_name"] and same_identity(result["candidate_name"],
                                                  result["forwarder_name"]):
        result["candidate_name"] = None
        result["warnings"].append(
            "The detected name matched the person who forwarded the message, so it was discarded. "
            "Enter the prospect's name manually.")

    cand_email = (result["candidate_email"] or "").strip().casefold()
    fwd_email = (result["forwarder_email"] or "").strip().casefold()
    if cand_email and fwd_email and cand_email == fwd_email:
        result["candidate_email"] = None
        result["warnings"].append(
            "The detected email address is the forwarder's own, so it was discarded.")

    if result["candidate_phone"] and result["candidate_phone"] in forwarder_phones:
        result["candidate_phone"] = None
        result["warnings"].append(
            "The detected phone number appears in the forwarder's own signature, so it was "
            "discarded. It is not the prospect's number.")


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
    forwarder_region = text[:start]
    # No trustworthy end means the "region" may run straight into a quoted thread. Read the headers
    # from it (those are anchored at the top and safe) but treat the free text with suspicion.
    region = text[start:end] if end is not None else text[start:]
    body = _body_after_headers(region)
    #: Phones the FORWARDER is answerable for. Independent of boundary detection, so it still holds
    #: if a future client format defeats the region logic entirely.
    forwarder_phones = _phones_in(forwarder_region)
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

    # Fail closed: an unbounded region that still mentions the forwarder is contaminated. The header
    # fields stay (they precede any quote); heuristic name and phone are abandoned rather than
    # guessed out of somebody else's signature.
    contaminated = end is None and bool(
        (forwarder_email and forwarder_email in body.casefold())
        or (forwarder_phones and forwarder_phones & _phones_in(body)))
    if contaminated:
        result["warnings"].append(
            "The end of the original message could not be determined and the forwarder's own "
            "details appear inside it, so no name or phone was taken from the body. Enter them "
            "manually.")

    # AMBIGUOUS: a presentation rule sits inside the region that MIGHT have ended the message, and
    # no trustworthy boundary was ever found after it. Whatever follows that rule cannot be
    # attributed to the prospect -- and unlike the contaminated case there is no forwarder detail to
    # notice, so nothing else would catch it. Quoted content below such a rule was the one shape
    # that could still hand over somebody else's phone number.
    ambiguous = end is None and bool(_uncorroborated_weak_markers(text, start))
    if ambiguous and not contaminated:
        result["warnings"].append(
            "Quoted-message boundaries could not be determined -- the message contains a divider "
            "with no recognisable quote after it -- so no name or phone was taken from the body. "
            "Enter them manually.")
    # Heuristics read free text; the header fields above do not, and are unaffected.
    heuristics_unsafe = contaminated or ambiguous

    # Name, in order of trust: the original From display name when it is actually a name, otherwise
    # the prospect's own sign-off. A handle like "ctbvmi01" fails the first and is never substituted
    # by the email local part -- if the sign-off yields nothing, the field stays empty for staff.
    if looks_like_human_name(name, email=email):
        result["candidate_name"] = name
    elif not heuristics_unsafe:
        signature = _signature_name(body, forwarder_name=forwarder_name, email=email)
        if signature:
            result["candidate_name"] = signature
            result["candidate_source"] = "original_signature"
        else:
            # Last resort, and only for the SAME address: a later quoted header for this exact
            # candidate may spell out the name their current signature abbreviates. Gated on
            # heuristics_unsafe with everything else, so a contaminated or ambiguous thread still
            # fails closed exactly as it did before.
            corroborated = _corroborated_name(text, _header_block_starts(text), email=email,
                                              forwarder_name=forwarder_name)
            if corroborated:
                result["candidate_name"] = corroborated
                result["candidate_source"] = "quoted_header_same_email"
        if not result["candidate_name"] and name:
            result["warnings"].append(
                f"The original sender is shown as \u201c{name}\u201d, which does not look like a "
                "person's name, and no name was found in their message. Enter it manually.")

    # Phone comes ONLY from the prospect's own body. Scanning further picked up the forwarder's
    # office number out of her quoted signature and produced a false existing-client match.
    result["candidate_phone"] = None if heuristics_unsafe else _phone_in(body)
    if result["candidate_phone"] and result["candidate_source"] == "forwarded_header":
        result["candidate_source"] = "forwarded_header+signature"

    _apply_forwarder_gate(result, forwarder_phones)

    result["warnings"].append(
        "Detected from the forwarded message text. Every field is a suggestion and must be "
        "confirmed before anything is created.")
    if not email:
        result["warnings"].append("No email address was found for the detected prospect.")
    return result
