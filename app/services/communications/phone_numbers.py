"""The ONE place a phone number is normalized, masked, or turned into a dial link.

WHY THIS MODULE EXISTS. ``people.normalized_phone`` is the only indexed client phone column in the
schema, and matching anything against a client means agreeing with it exactly. Before this module,
:mod:`app.services.communications.sms_ingest` owned that convention and its docstring warned that a
second, "better" normalizer elsewhere would simply match nothing. The 3CX connector is that second
caller, so the convention moved here and ``sms_ingest`` re-exports it — one function, two callers,
no opportunity to drift. ``tests/test_sms_ingest.py`` still pins the agreement with the AssetMark,
Schwab and Wealthbox importers, and it pins this module by transitivity.

The module deliberately imports NOTHING from ``app``. It is string handling only, so the Jinja
environment (:mod:`app.templating`) can use :func:`dial_uri` without dragging the database layer
into template rendering.
"""
from __future__ import annotations

import re

#: What a masked number shows. Four digits is enough for a human to recognise a call they just took
#: and far too little to identify or redial a client from a log file.
MASK_VISIBLE_DIGITS = 4
MASK_PREFIX = "***"


def normalize_phone(value) -> str | None:
    """Digits only, with a leading US country code dropped — the repository's EXISTING convention.

    Deliberately NOT E.164. ``people.normalized_phone`` is populated by the AssetMark, Schwab and
    Wealthbox importers, all three of which normalize exactly this way, so this is what a client
    number must be compared against.

    A number that is not a 10-digit NANP number after stripping is returned as its digits, so an
    international caller is still recorded and compared consistently — it just will not match a
    client whose stored number was normalized under the same rule.
    """
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def mask_number(value) -> str | None:
    """``"***0100"``-style rendering for logs, audit metadata and error text.

    Every diagnostic surface in the connector goes through this. A full client phone number in an
    application log is a disclosure that outlives the call it describes, and a support engineer
    reading a log only ever needs to tell two calls apart — which the last four digits do.

    Returns ``None`` for an empty value, and masks EVERYTHING when there are too few digits to
    leave a suffix, so a short or malformed number can never fall through unredacted.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return None
    if len(digits) <= MASK_VISIBLE_DIGITS:
        return MASK_PREFIX
    return MASK_PREFIX + digits[-MASK_VISIBLE_DIGITS:]


def dial_uri(value, scheme: str = "tel") -> str | None:
    """A click-to-call URI the installed 3CX phone handler can dial, or ``None``.

    3CX's desktop app registers ``tel:`` (and, on some installs, ``callto:``) as a system URI
    handler, which is what makes a link in a web page dial from the softphone. Two things make the
    difference between a link that dials and one that silently does nothing:

      * **No punctuation.** ``tel:(555) 010-0000`` is legal RFC 3966 but handlers vary in what they
        strip. Digits and a leading ``+`` only.
      * **A country code.** 3CX dials against outbound rules, and a bare 10-digit string is
        ambiguous. A 10-digit NANP number therefore becomes ``+1``-prefixed here.

    A number that is neither 10 digits nor 11 digits starting with ``1`` is passed through as its
    digits, keeping any leading ``+`` the record already carried: guessing a country code for an
    international number would dial the wrong country, which is worse than not dialling.
    """
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    scheme = (scheme or "tel").strip().lower()
    if len(digits) == 10:
        number = f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        number = f"+{digits}"
    elif raw.startswith("+"):
        number = f"+{digits}"
    else:
        number = digits
    return f"{scheme}:{number}"
