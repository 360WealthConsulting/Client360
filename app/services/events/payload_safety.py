"""Domain-event payload safety (Phase D.35) — references-only enforcement.

Domain-event payloads must carry **references only**: ids, codes, status transitions, timestamps, actor
references, and non-sensitive metadata. They must NEVER carry PII, secrets, tax figures, account/policy
values, health data, or document contents — event payloads are stored in the outbox and delivered to
consumers, so a sensitive value would leak beyond its authoritative, capability-gated home.

This module is the single place that decides whether a field name is prohibited. It is consulted by:
- the publisher (rejects a payload containing a prohibited field — a runtime sensitive-field violation),
- the contract catalog (rejects a declared schema field that is prohibited),
- governance (flags any registered contract whose schema declares a prohibited field).

Detection is by field NAME (a conservative substring match) — the model never inspects values, so it
cannot itself become a place sensitive data flows through.
"""
from __future__ import annotations

# Prohibited field-name substrings (lower-cased match). Chosen to catch PII / secrets / financials /
# health / tax / document-content WITHOUT false-positiving on legitimate reference fields (``*_id``,
# ``status``, ``code``, ``stage``, ``event``, ``*_at`` timestamps, counts).
SENSITIVE_FIELD_SUBSTRINGS = (
    # identifiers of a person / tax
    "ssn", "social_security", "ein", "tin", "tax_id", "taxid", "national_id", "passport",
    # names + direct contact PII
    "first_name", "last_name", "full_name", "middle_name", "maiden", "email", "phone", "mobile",
    "fax", "address", "street", "city", "state_code", "zip", "postal", "geo",
    # dates of birth ("age" is intentionally NOT a bare substring — it false-matches coverage/stage/
    # engagement; a standalone age field would be caught by dob/date_of_birth/birth).
    "dob", "date_of_birth", "birth",
    # financial values
    "amount", "balance", "aum", "account_value", "account_number", "acct", "market_value", "premium",
    "salary", "income", "wage", "compensation", "revenue", "valuation", "price", "cost", "fee",
    "commission", "payment", "deposit", "withdrawal",
    # policy / account sensitive numbers
    "policy_number", "certificate_number", "member_number", "routing", "iban", "card_number",
    # health / benefits PHI
    "phi", "diagnosis", "medical", "medication", "health_condition", "disability", "coverage_amount",
    # secrets
    "password", "secret", "token", "credential", "api_key", "private_key", "access_key",
    # free text / document contents
    "content", "body", "plaintext", "note", "notes", "comment", "comments", "description_text",
    "file_data", "attachment", "raw",
)

# Explicit allow-list of reference field names that could otherwise trip a substring (kept precise).
_ALLOWED_EXACT = frozenset({
    "state",          # a lifecycle state/stage name (a code), not a mailing state
    "stage",
    "status",
    "from_status",
    "to_status",
})


def sensitive_fields(field_names) -> list[str]:
    """Return the field names that are prohibited (references-only violation). Empty if all are safe."""
    bad = []
    for name in field_names or ():
        lname = str(name).lower()
        if lname in _ALLOWED_EXACT:
            continue
        if any(sub in lname for sub in SENSITIVE_FIELD_SUBSTRINGS):
            bad.append(name)
    return bad


def is_safe(field_names) -> bool:
    return not sensitive_fields(field_names)
