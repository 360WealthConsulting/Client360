"""Centralized filename safety — PURE, deterministic, no I/O.

ONE place decides whether a piece of text may be shown to a user or delivered as a filename, and ONE
place removes sensitive identifiers from it. Every naming, display and delivery path in Client360
routes through this module, so a protection added here is a protection everywhere.

Why this exists
    The naming engine composes a display name partly from text it does not recognise in the original
    filename (``residual_qualifier``). That is deliberate — an employer, custodian or payer is real
    provenance worth keeping. But "text I do not recognise" also covers an SSN, an EIN, a bank account
    or a card number, and a display name becomes a DOWNLOAD filename and an EMAIL ATTACHMENT filename.
    Before this module, ``2024 W2 SSN 123-45-6789.pdf`` produced the SAFE candidate
    ``2024 - W-2 - Adam Davis - SSN 123 45 6789``.

Two guarantees
    * ``scan`` never returns a matched value — only a stable, non-sensitive reason code. A reason code
      is safe to persist in an audit event, a preview row or a log line; the value never is.
    * ``scrub`` removes the LABEL AND THE VALUE as one unit and leaves everything else alone, so
      ``Acct 4471002983 Chase`` becomes ``Chase`` rather than ``Chase`` being lost with it.

Nothing here reads a database, a file, or the network, and nothing here mutates its arguments.
``original_name``, ``stored_name``, ``storage_path``, ``storage_uri``, ``sha256`` and ``tags`` are
never touched by any caller of this module — provenance is preserved exactly, and only what is
DISPLAYED or DELIVERED is affected.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Reason codes. Stable, non-sensitive, safe to persist. NEVER carries a value.
# ---------------------------------------------------------------------------
SSN = "ssn"
ITIN = "itin"
EIN = "ein"
TAX_ID = "tax_id"
ACCOUNT_NUMBER = "account_number"
ROUTING_NUMBER = "routing_number"
CARD_NUMBER = "card_number"
CVV = "cvv"
POLICY_NUMBER = "policy_number"
MEMBER_ID = "member_id"
DATE_OF_BIRTH = "date_of_birth"
UNLABELED_IDENTIFIER = "unlabeled_identifier"

#: Every code this module can emit. Useful for tests and for documenting coverage.
REASON_CODES = (SSN, ITIN, EIN, TAX_ID, ACCOUNT_NUMBER, ROUTING_NUMBER, CARD_NUMBER, CVV,
                POLICY_NUMBER, MEMBER_ID, DATE_OF_BIRTH, UNLABELED_IDENTIFIER)

#: Human-readable, value-free explanation for a preview/apply row.
UNSAFE_REASON = "candidate contains a sensitive identifier and was withheld"

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
# Separator between a label and its value: spaces, colon, hash, dot, slash, dash, underscore.
_SEP = r"[\s:#._/\\-]{0,4}"

# A value that is identifier-shaped: at least 4 characters and at least 3 digits somewhere, so a
# label followed by an ordinary WORD ("Account Statement") is never mistaken for an account number.
_IDVAL = r"(?=[A-Za-z0-9\-]*\d{3})[A-Za-z0-9\-]{4,}"

# 9 digits, optionally split 3-2-4 by a single separator. Covers dashed, spaced and undashed SSNs.
_SSNVAL = r"\d{3}[-\s.]?\d{2}[-\s.]?\d{4}"
_EINVAL = r"\d{2}[-\s.]?\d{7}"

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
# A date in any ordinary written form. Only ever used when a birth-date LABEL precedes it.
_DATEVAL = (rf"(?:\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{2,4}}"
            rf"|\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}"
            rf"|{_MONTH}\s*\d{{1,2}},?\s*\d{{4}}"
            rf"|\d{{1,2}}\s*{_MONTH}\s*\d{{4}}"
            rf"|\d{{8}})")

# Label alternations.
_L_SSN = r"(?:ssn|s\.?\s?s\.?\s?n\.?|social\s*security(?:\s*(?:no\.?|number|#|card))?)"
_L_ITIN = r"(?:itin|i\.?t\.?i\.?n\.?)"
_L_EIN = r"(?:ein|fein|e\.?i\.?n\.?|employer\s*id(?:entification)?(?:\s*(?:no\.?|number|#))?)"
_L_TAX = r"(?:tin|tax\s*(?:payer)?\s*id(?:entification)?(?:\s*(?:no\.?|number|#))?)"
_L_ACCT = r"(?:acct\.?(?:\s*(?:no\.?|number|#))?|account(?:\s*(?:no\.?|number|#))?|a/c|acc\.?\s*no\.?)"
_L_ROUTING = r"(?:routing(?:\s*(?:no\.?|number|#))?|aba(?:\s*(?:no\.?|number|#))?|rtn)"
_L_CARD = r"(?:credit\s*card|debit\s*card|card(?:\s*(?:no\.?|number|#))?|visa|mastercard|amex|discover)"
_L_CVV = r"(?:cvv2?|cvc2?|cid|security\s*code)"
_L_POLICY = r"(?:policy(?:\s*(?:no\.?|number|#|id))?)"
_L_MEMBER = r"(?:member(?:\s*(?:no\.?|number|#|id))?|subscriber(?:\s*(?:no\.?|number|#|id))?|group\s*(?:no\.?|number|#|id))"
_L_DOB = r"(?:dob|d\.?\s?o\.?\s?b\.?|date\s*of\s*birth|birth\s*date|birthdate|born(?:\s*on)?)"


def _lv(label: str, value: str) -> str:
    """A label-plus-value span, matched as ONE unit so scrubbing removes both together."""
    return rf"(?<![A-Za-z0-9]){label}{_SEP}{value}(?![A-Za-z0-9])"


# ---------------------------------------------------------------------------
# Detectors, most specific first. Labeled forms precede bare ones so the LABEL is
# consumed with its value; bare forms catch what carries no label at all.
# ---------------------------------------------------------------------------
_RULES: list[tuple[str, re.Pattern[str], bool]] = [
    # (reason code, pattern, requires_luhn)
    (SSN, re.compile(_lv(_L_SSN, _SSNVAL), re.I), False),
    (ITIN, re.compile(_lv(_L_ITIN, _SSNVAL), re.I), False),
    (EIN, re.compile(_lv(_L_EIN, _EINVAL), re.I), False),
    (EIN, re.compile(_lv(_L_EIN, _IDVAL), re.I), False),
    (TAX_ID, re.compile(_lv(_L_TAX, _IDVAL), re.I), False),
    (ROUTING_NUMBER, re.compile(_lv(_L_ROUTING, r"\d{9}"), re.I), False),
    (ROUTING_NUMBER, re.compile(_lv(_L_ROUTING, _IDVAL), re.I), False),
    (CVV, re.compile(_lv(_L_CVV, r"\d{3,4}"), re.I), False),
    (CARD_NUMBER, re.compile(_lv(_L_CARD, r"(?:\d[ -]?){12,18}\d"), re.I), False),
    (CARD_NUMBER, re.compile(_lv(_L_CARD, _IDVAL), re.I), False),
    (ACCOUNT_NUMBER, re.compile(_lv(_L_ACCT, _IDVAL), re.I), False),
    (POLICY_NUMBER, re.compile(_lv(_L_POLICY, _IDVAL), re.I), False),
    (MEMBER_ID, re.compile(_lv(_L_MEMBER, _IDVAL), re.I), False),
    (DATE_OF_BIRTH, re.compile(_lv(_L_DOB, _DATEVAL), re.I), False),
    # Bare, unlabeled forms.
    (SSN, re.compile(r"(?<![A-Za-z0-9])\d{3}[-\s]\d{2}[-\s]\d{4}(?![A-Za-z0-9])"), False),
    (EIN, re.compile(r"(?<![A-Za-z0-9])\d{2}-\d{7}(?![A-Za-z0-9])"), False),
    # A grouped 13-19 digit run that passes Luhn is a payment card, not a reference number.
    (CARD_NUMBER, re.compile(r"(?<![A-Za-z0-9])(?:\d[ -]?){12,18}\d(?![A-Za-z0-9])"), True),
    # Any remaining long digit run. Deliberately last: everything above gives a better reason code.
    (UNLABELED_IDENTIFIER, re.compile(r"(?<![A-Za-z0-9])\d{7,}(?![A-Za-z0-9])"), False),
]

# A compact YYYYMMDD date is a date, not an identifier. Checked before the bare long-run rule fires.
_COMPACT_DATE = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")


def _luhn(text: str) -> bool:
    """True when the digits in ``text`` satisfy the Luhn checksum (payment-card check)."""
    digits = [int(c) for c in text if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_currency(text: str, start: int, end: int) -> bool:
    """True when a digit run is part of an ordinary money amount rather than an identifier.

    Money is excluded two ways: a leading currency symbol or grouping separator, and a trailing
    decimal fraction. ``$1234567`` and ``1234567.89`` are amounts; ``4471002983`` is not.
    """
    before = text[:start].rstrip()
    if before.endswith(("$", "€", "£", ".", ",")):
        return True
    after = text[end:]
    return bool(re.match(r"[.,]\d{1,2}(?![\d])", after))


def _bare_run_is_benign(text: str, match: re.Match[str]) -> bool:
    """True when an unlabeled long digit run is a date or a money amount, not an identifier."""
    value = match.group(0)
    if _COMPACT_DATE.match(value):
        return True
    return _is_currency(text, match.start(), match.end())


def scan(text: str | None) -> tuple[str, ...]:
    """Sorted, de-duplicated reason codes for every sensitive identifier in ``text``.

    Returns an EMPTY tuple when the text is safe. **Never returns a matched value** — the codes are
    safe to persist in an audit event, a preview row, or a log line.
    """
    s = text or ""
    if not s:
        return ()
    found: set[str] = set()
    for code, pattern, needs_luhn in _RULES:
        for m in pattern.finditer(s):
            if needs_luhn and not _luhn(m.group(0)):
                continue
            if code == UNLABELED_IDENTIFIER and _bare_run_is_benign(s, m):
                continue
            found.add(code)
    return tuple(sorted(found))


def is_safe(text: str | None) -> bool:
    """True when ``text`` carries no detectable sensitive identifier."""
    return not scan(text)


def scrub(text: str | None) -> str:
    """``text`` with every sensitive label-and-value span removed, other wording preserved.

    The label is removed WITH its value, so ``Acct 4471002983 Chase`` yields ``Chase`` — the custodian
    survives, the account number does not. Separators left behind by a removal are collapsed, and a
    dangling ``-`` separator between display-name segments is not left stranded.
    """
    s = text or ""
    if not s:
        return ""
    for code, pattern, needs_luhn in _RULES:
        # Every loop variable is bound as a default argument: the closure must see THIS rule and the
        # text as it stands for THIS pass, not whatever the last iteration left behind.
        def _replace(m: re.Match[str], _code=code, _luhn_required=needs_luhn, _text=s) -> str:
            if _luhn_required and not _luhn(m.group(0)):
                return m.group(0)
            if _code == UNLABELED_IDENTIFIER and _bare_run_is_benign(_text, m):
                return m.group(0)
            return " "
        s = pattern.sub(_replace, s)
    # Collapse the gaps a removal leaves: repeated separators, then stranded segment dashes.
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?:\s*-\s*){2,}", " - ", s)
    s = re.sub(r"^(?:\s*[-_]\s*)+|(?:\s*[-_]\s*)+$", "", s)
    return s.strip(" -_,.").strip()
