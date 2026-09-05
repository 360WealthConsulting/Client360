"""Explicit trust semantics for ``person_source_links``.

THE PROBLEM THIS REPLACES
-------------------------
``person_source_links.confirmed`` is a single boolean that means seven different things at once, and
it is set to ``TRUE`` unconditionally by automated code — ``app/matching/promote.py::_link`` hardcodes
``confirmed=True``, and so does ``scripts/link_drake_to_people.py``. Nothing ever wrote ``False`` for
a Drake link: measured against production, **all 3,607 Drake links carry ``confirmed = true``**, and
the table has no ``confirmed_by_user_id``, no ``confirmed_at`` and no provenance column. So "confirmed"
cannot answer the only question that matters at a tax-return read surface — *did a human decide this,
or did a string match?*

Measured breakdown of those 3,607 confirmed Drake links:

    unique_exact_name                            1,404   39.0%   NAME-DERIVED
    ssn/identifier-hash keyed methods               533   14.8%
    canonical_repair_*                              526   14.6%   evidence not recorded
    email + phone                                   461   12.8%
    phone                                           318    8.8%
    manual_* / drake_identity_review                171    4.7%
    email                                           115    3.2%
    exact_name_city_state                            79    2.2%   NAME-DERIVED

41.1% of "confirmed" Drake linkage is name-derived. A resolver that reads ``confirmed`` and calls the
result "resolved by identifier, never by name" would be making a claim the data does not support.

THE DESIGN
----------
Trust becomes EXPLICIT and RECORDED, rather than inferred from a boolean:

    ``trust_level``          what class of evidence established this link (the enum below)
    ``confirmation_source``  ``human`` / ``machine`` / ``unknown`` — who decided
    ``evidence_method``      the concrete method string, kept alongside the classified level
    ``confirmed_by_user_id`` the person who approved it, when a person did
    ``confirmed_at``         when that approval happened

``confirmed`` is left exactly as it is. It is not redefined, not repurposed and not rewritten — a
deployed reader that still consults it sees precisely what it saw before.

LEGACY ROWS ARE NOT RECLASSIFIED
--------------------------------
Every existing row keeps ``trust_level = NULL``, which reads as :data:`UNKNOWN_LEGACY`. Nothing is
back-filled and no production row is restated. ``derive_legacy_trust`` exists so those rows can still
be *reported on* — it is a read-only, best-effort reading of the method string, and it is deliberately
NOT the same thing as recorded trust. Two rows that both come back ``IDENTIFIER_VERIFIED`` are not
equally trustworthy if one was recorded and the other was guessed from a string, so the two are kept
apart at every call site by :func:`link_trust`, which reports which of the two it used.
"""
from __future__ import annotations

from typing import Any

# --- trust levels ---------------------------------------------------------------------------------

#: Established from a Drake SSN/EIN-derived identifier hash. The strongest machine evidence there is.
IDENTIFIER_VERIFIED = "identifier_verified"
#: A named human approved THIS link. The strongest evidence, full stop.
HUMAN_APPROVED = "human_approved"
#: A unique exact name match. A name is not an identifier: production holds 117 duplicate name-groups.
MACHINE_EXACT_NAME = "machine_exact_name"
#: Name plus city/state. Still a name match, with a weak locality corroborator.
MACHINE_NAME_LOCATION = "machine_name_location"
#: Email and/or phone. A real identifier, but for a MAILBOX or a HANDSET, not for a taxpayer.
MACHINE_CONTACT = "machine_contact"
#: Written by a canonical-repair pass. Asserts provenance was matched, without recording on what.
CANONICAL_REPAIR = "canonical_repair"
#: No trust was ever recorded, and the method string does not say. The default for every legacy row.
UNKNOWN_LEGACY = "unknown_legacy"

TRUST_LEVELS = (
    IDENTIFIER_VERIFIED, HUMAN_APPROVED, MACHINE_EXACT_NAME, MACHINE_NAME_LOCATION,
    MACHINE_CONTACT, CANONICAL_REPAIR, UNKNOWN_LEGACY,
)

# --- confirmation source --------------------------------------------------------------------------

SOURCE_HUMAN = "human"
SOURCE_MACHINE = "machine"
SOURCE_UNKNOWN = "unknown"

CONFIRMATION_SOURCES = (SOURCE_HUMAN, SOURCE_MACHINE, SOURCE_UNKNOWN)

# --- the policy -----------------------------------------------------------------------------------

#: The ONLY trust levels that may put a Drake tax return on a staff read surface.
#:
#: A tax return carries AGI, filing status and acknowledgements, so the bar is an identifier or a
#: person. Name, name+location and contact matches stay VALUABLE — they are exactly what the identity
#: review queue should propose — but a candidate is not a decision, and none of them may cross this
#: line on their own.
TRUSTED_FOR_TAX_RETURN_VISIBILITY = frozenset({IDENTIFIER_VERIFIED, HUMAN_APPROVED})

# --- legacy derivation ----------------------------------------------------------------------------

#: Method strings whose linkage is keyed on the Drake identifier hash. Enumerated rather than pattern
#: matched: "drake" appearing in a method name does not make the method identifier-grade.
_IDENTIFIER_METHODS = frozenset({
    "confirmed_identifier_hash",
    "drake_identity_promotion",
    "drake_spouse_identity_promotion",
    "drake_spouse_identity_repair",
    "repeated_drake_1040_taxpayer_promotion",
    "repeated_drake_spouse_identity_promotion",
    "repeated_drake_spouse_identifier_repair",
    "canonical_repair_repeated_drake_taxpayer",
    "canonical_repair_repeated_drake_spouse",
    "canonical_repair_exact_drake_taxpayer",
    "dameron_identity_split_repair",
    "dameron_spouse_identity_repair",
    "manual_drake_ssn_hash_continuity",
    "manual_drake_exact_identity_provenance",
})

#: The one method that means a reviewer approved this specific link in the identity-review UI
#: (``POST /matches/drake/{hash}/{person_id}/approve``).
_HUMAN_REVIEW_METHODS = frozenset({"drake_identity_review"})


def derive_legacy_trust(match_method: Any) -> str:
    """Best-effort READ-ONLY reading of a legacy ``match_method``. Never written to the database.

    This is evidence about a row, not a restatement of it. Anything the string does not clearly
    establish comes back :data:`UNKNOWN_LEGACY` — the classification fails closed like everything else
    on this path.
    """
    if match_method is None:
        return UNKNOWN_LEGACY

    method = str(match_method).strip().lower()
    if not method:
        return UNKNOWN_LEGACY

    if method in _IDENTIFIER_METHODS:
        return IDENTIFIER_VERIFIED
    if method in _HUMAN_REVIEW_METHODS:
        return HUMAN_APPROVED

    # A ``manual_`` prefix means a human RAN something, which is not the same as a human approving
    # THIS link. It is recorded as human-sourced but is not silently promoted to HUMAN_APPROVED.
    if method.startswith("manual_"):
        return CANONICAL_REPAIR if "repair" in method else UNKNOWN_LEGACY

    if "email" in method or "phone" in method:
        return MACHINE_CONTACT
    if method == "auto_promote":
        # app/matching/promote.py matches on normalized email or phone.
        return MACHINE_CONTACT
    if "name_city_state" in method:
        return MACHINE_NAME_LOCATION
    if "exact_name" in method:
        return MACHINE_EXACT_NAME
    if method.startswith("canonical_repair"):
        return CANONICAL_REPAIR

    return UNKNOWN_LEGACY


def derive_legacy_confirmation_source(match_method: Any) -> str:
    """Who decided a legacy link, as far as the method string can say."""
    if match_method is None:
        return SOURCE_UNKNOWN
    method = str(match_method).strip().lower()
    if not method:
        return SOURCE_UNKNOWN
    if method in _HUMAN_REVIEW_METHODS or method.startswith("manual_"):
        return SOURCE_HUMAN
    return SOURCE_MACHINE


def link_trust(row) -> dict[str, Any]:
    """Resolve one ``person_source_links`` row to a trust decision, saying HOW it was reached.

    ``row`` is any mapping carrying ``trust_level`` / ``confirmation_source`` / ``match_method``
    (a SQLAlchemy ``RowMapping`` or a plain dict).

    The returned ``recorded`` flag is the point of this function: a recorded level is an assertion the
    platform made deliberately, a derived one is this module reading a string written by a script that
    in several cases no longer exists in the tree. Callers that care about the difference — and the
    tax-return resolver does — must not have to guess which they were handed.
    """
    recorded_level = (row.get("trust_level") or "").strip() or None

    if recorded_level:
        return {
            "trust_level": recorded_level,
            "confirmation_source": (row.get("confirmation_source") or SOURCE_UNKNOWN),
            "recorded": True,
            "trusted_for_tax_return_visibility":
                recorded_level in TRUSTED_FOR_TAX_RETURN_VISIBILITY,
        }

    derived = derive_legacy_trust(row.get("match_method"))
    return {
        "trust_level": derived,
        "confirmation_source": derive_legacy_confirmation_source(row.get("match_method")),
        "recorded": False,
        # A DERIVED level never grants visibility on its own. Whether legacy rows may be honoured is a
        # deployment policy decision, made explicitly by the resolver's caller — never a default here.
        "trusted_for_tax_return_visibility": False,
    }


def is_trusted_for_tax_return_visibility(row, *, accept_derived_legacy: bool = False) -> bool:
    """May this link put a Drake tax return in front of staff?

    ``accept_derived_legacy`` is the ONE switch that lets pre-existing rows through, and it must be
    passed explicitly at every call site. It exists because production currently holds no recorded
    trust at all — nothing has been back-filled — so a strict reading resolves nothing. Turning it on
    is a deliberate, reviewable choice to honour a classification derived from a method string, and
    the measured consequence is documented in ``drake_return_resolution``.
    """
    trust = link_trust(row)
    if trust["trusted_for_tax_return_visibility"]:
        return True
    if accept_derived_legacy and not trust["recorded"]:
        return trust["trust_level"] in TRUSTED_FOR_TAX_RETURN_VISIBILITY
    return False
