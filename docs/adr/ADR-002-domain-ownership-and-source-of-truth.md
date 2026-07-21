# ADR-002 — Domain ownership and source of truth

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (each domain); Business Operations Owner (Michael Shelton).

## Context
As domains multiplied (people, households, organizations, tax, benefits, insurance, advisor
intelligence, work, compliance, timeline, annual review, business owner planning), ambiguity
about *who owns which datum* would let two services write the same fact, produce conflicting
values, and make authorization and redaction inconsistent.

## Decision
Every data type **must** have exactly **one authoritative owning domain and service**. That
owner **owns** writes and query semantics; all other layers **consume** it. Representative
ownership (full matrix in `docs/PLATFORM_ARCHITECTURE.md §5`):

- client identity → **People**; household membership → **Households**
- business identity → **Relationship Entities / Organizations** (`organization_service`)
- business ownership → **Relationships / Relationship Ownership**
- benefits/retirement → **Benefits**; insurance policies → **Insurance**
- tax engagement metadata → **Tax**; recommendations → **Advisor Intelligence**
- work lifecycle → **Advisor Work**; compliance decisions → **Compliance**
- reviewer authority → **Reviewer Authority**; timeline → **Activity Timeline** (projection)
- annual-review sessions → **Annual Review**; succession/continuity profile → **Business Owner
  Planning**

Data with no authoritative owner **must** be shown as **"Not currently modeled"** and **must
not** be assigned to a fabricated domain (e.g. tax-return financial content, owner compensation,
insurance policy purpose, business valuation).

## Alternatives considered
1. **Shared/god tables** written by multiple services. Rejected: guarantees write conflicts and
   diffuse authorization.
2. **Ownership by convention only** (no enforcement). Rejected: drifts silently; the D.12A audit
   showed enforcement (imports, additive-read placement) is what actually holds the line.

## Reasons for the decision
Single ownership is the precondition for coherent authorization, redaction, and composition. It
makes "who do I ask / who do I fix" unambiguous.

## Consequences
### Positive consequences
- Unambiguous responsibility; no double-writes; coherent scope/redaction.
- Composition layers can trust each domain's outputs.

### Negative consequences and tradeoffs
- Cross-domain features require additive reads on several owners (ADR-013) rather than one query.
- Genuinely unowned data must be explicitly surfaced as unavailable rather than guessed.

## Enforcement
- Source-of-truth matrix: `docs/PLATFORM_ARCHITECTURE.md §5`; manifest `not_currently_modeled`.
- Additive reads live on owning services (ADR-013), verified by
  `tests/test_platform_architecture.py`.
- Import-direction tests keep consumers from re-owning producer data.

## Exceptions
None currently approved. Unowned data is documented as "Not currently modeled", not reassigned.

## Revisit conditions
When a new domain is introduced for currently-unmodeled data (e.g. a Succession domain, tax-return
structured content), add a new ADR assigning ownership and update the matrix and manifest.

## References
- `docs/PLATFORM_ARCHITECTURE.md` §4 (Domain map), §5 (Source-of-truth matrix)
- `docs/platform_architecture_manifest.yaml` (`not_currently_modeled`)
- `app/services/*` owning services; `tests/test_platform_architecture.py`
