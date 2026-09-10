"""The one definition of a Drake taxpayer identifier hash.

WHY THIS IS ITS OWN MODULE
--------------------------
The salted ``SHA-256(KEY : digits)`` that turns an SSN or EIN into the value stored in
``drake_client_returns.taxpayer_identifier_hash`` lived inside ``scripts/import_drake_all_years``,
because for a long time the nightly import was the only thing that ever needed it. Human-approved
entity adjudication (:mod:`app.services.drake_entity_adjudication`) now needs the identical value for
an EIN that the client export never carried, and a second copy of a hashing rule is a defect waiting
to happen: two implementations that drift by one character produce two identities for one taxpayer,
silently and permanently.

So the algorithm moved here, unchanged, and the import driver imports it. There is exactly one
definition, and :mod:`tests.test_drake_entity_adjudication` pins its output against the historical
expression so a future edit cannot quietly re-key production.

WHAT IS AND IS NOT IDENTITY
---------------------------
Only the DIGITS are hashed. ``87-6387267``, ``876387267`` and ``87 6387267`` are one identifier
written three ways, and Drake writes all three at different times; formatting is not identity. A
value carrying no digits at all has no identity and returns ``None`` — never a hash of the empty
string, which would collapse every unidentified row onto one key.

THE SECRET IS READ AT CALL TIME
-------------------------------
``MICROSOFT_TOKEN_KEY`` is looked up on each call rather than at import, so importing this module
needs no environment. That is what lets ``--help``, argument parsing and the pure unit tests run
without secrets present, and it is the behaviour the import driver has always had.
"""
from __future__ import annotations

import hashlib
import os

from app.importers.drake_client_csv import clean_value

#: The environment variable holding the hashing secret. Shared with the Microsoft token cache
#: because it is the one secret already provisioned everywhere the Drake import runs.
KEY_ENV_VAR = "MICROSOFT_TOKEN_KEY"


class IdentifierHashKeyMissing(RuntimeError):
    """The hashing secret is not configured, so no identifier can be derived.

    A ``RuntimeError`` subclass: the import driver has always caught ``RuntimeError`` here and
    exited 2 with a clear message, and that behaviour is preserved.
    """


def identifier_digits(value) -> str:
    """Every digit in ``value``, in order, with NULs and whitespace already stripped.

    Reuses ``clean_value`` so this and the CSV importer agree on what "the value" is, down to the
    embedded NUL bytes that Drake's fixed-width exports carry in unused fields.
    """
    return "".join(ch for ch in clean_value(value) if ch.isdigit())


def hash_key() -> str:
    """The hashing secret, or raise. Read at call time, never cached."""
    key = os.getenv(KEY_ENV_VAR, "")
    if not key:
        raise IdentifierHashKeyMissing(f"{KEY_ENV_VAR} is required.")
    return key


def identifier_hash(value) -> str | None:
    """The salted SSN/EIN hash, or ``None`` when the value carries no digits.

    The raw identifier is neither stored nor recoverable from the result. This is the only place
    the expression exists; callers must never assemble it themselves.
    """
    digits = identifier_digits(value)
    if not digits:
        return None
    return hashlib.sha256(f"{hash_key()}:{digits}".encode()).hexdigest()


__all__ = ["KEY_ENV_VAR", "IdentifierHashKeyMissing", "hash_key", "identifier_digits",
           "identifier_hash"]
