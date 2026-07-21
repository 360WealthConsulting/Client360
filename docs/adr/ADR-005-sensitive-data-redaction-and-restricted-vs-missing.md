# ADR-005 — Sensitive-data redaction and restricted versus missing

## Status
Accepted

## Date
2026-07-21

## Decision owners
Security / Authorization; Platform Architecture; Compliance Architecture (for compliance
comments/evidence).

## Context
Sensitive fields (EIN, policy numbers, benefits PHI/PII, tax content, compliance comments,
evidence, documents, planning notes) must not leak to unauthorized users. Separately, a
data-quality/missing-information feature must not tell a user that a value is *missing* when in
fact it exists but the user lacks permission to see it — that would both mislead and leak the
existence/absence signal incorrectly.

## Decision
- Redaction or omission of sensitive data **must** happen **server-side**; templates **receive
  only authorized or already-redacted values**.
- **Restricted data is not missing data.** Missing-information / data-quality logic **must not**
  flag a restricted value as missing.
- Where a value is encrypted at rest, a **present/not-present indicator** derived from the
  ciphertext **may** be shown without exposing the value.
- **No encryption keys or secrets** may appear in documentation or templates.

Field handling (capability → behavior without it): EIN → `benefits.sensitive.read` (value
withheld; presence flag from Fernet ciphertext); policy numbers/values →
`insurance.sensitive.read`; benefits PHI/PII → `benefits.sensitive.read`; tax content → `tax.read`
(section marked *restricted*); compliance comments/evidence → `compliance.review.read` (timeline
shows "Additional details are restricted."; workspaces show counts only); documents →
`document.read`.

## Alternatives considered
1. **Template-level `{% if cap %}` hiding of raw values.** Rejected: the value still reaches the
   template context (leak risk, and not real enforcement).
2. **Treat "no permission" the same as "no data".** Rejected: misleads advisors and conflates two
   distinct states; the D.12 workspace explicitly distinguishes them.

## Reasons for the decision
Server-side redaction is the only trustworthy boundary; the restricted-vs-missing distinction is
required for honest data-quality reporting and to avoid leaking presence via the missing-info list.

## Consequences
### Positive consequences
- Sensitive values never reach an unauthorized template.
- Data-quality output is honest: "Restricted" and "Missing" are different states.

### Negative consequences and tradeoffs
- Services must compute presence flags separately from values (e.g. `_ein_display`).
- Some sections show "Restricted" to otherwise-authorized advisors lacking the sensitive cap.

## Enforcement
- EIN: `app/services/business_owner.py::_ein_display` (present-flag from ciphertext; decrypt only
  with `benefits.sensitive.read`); `organization_service.get_organization` mirrors this.
- Policy numbers gated by `insurance.sensitive.read` in `business_owner._insurance_section`.
- Missing-info uses the present-flag, not view permission: `business_owner._person_missing_information`.
- Tests: `tests/test_business_owner.py` (`test_ein_restricted_not_mislabeled_missing`,
  `test_ein_missing_is_flagged`, `test_sections_gated_without_capabilities`).

## Exceptions
None currently approved.

## Revisit conditions
If a new sensitive field is added, extend the table and add a redaction test; revisit only to add
fields, not to relax the restricted-vs-missing rule.

## References
- `app/services/business_owner.py`, `app/services/organization_service.py`,
  `app/services/insurance.py`, `app/services/activity_timeline/service.py`
- `docs/PLATFORM_ARCHITECTURE.md` §11 (Sensitive-data and redaction model)
- `docs/FIELD_SECURITY.md`, `tests/test_business_owner.py`
