# Data Stewardship Registry (Phase D.66)

`DATA_STEWARDSHIP_REGISTRY` in `app/services/data_governance_intelligence/registry.py` is a declarative catalog
of the **5 stewardship domains** the firm's platform actually has. Metadata only — it defines no stewardship
platform and never assigns a steward. Three domains are **configured** (composed from the Governance catalog +
retention); two are genuinely absent (`not_configured`).

## Stewardship domains (5)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| domain_stewards | governance.catalog | `list_domains` (steward_user_id) | `create_domain` | configured |
| stewardship_coverage | governance.catalog | `list_domains` (steward_user_id) | `create_domain` | configured |
| remediation_cases | governance.retention | `list_cases` | `create_case` | configured |
| stewardship_assignment_workflow | not_configured | n/a | n/a | **not_configured** |
| data_product_ownership | not_configured | n/a | n/a | **not_configured** |

## What it exposes (and never exposes)

Stewardship is derived from the `steward_user_id` presence on each data domain (the Governance catalog owns it).
The panels expose **counts and coverage ratios only** — how many domains have an assigned steward vs
unstewarded, and a DERIVED stewardship-coverage ratio. **A steward identity is never exposed**, and **a steward
assignment is not a governance guarantee.** Remediation cases (stewardship follow-up) are composed from the
Governance retention / case owner. The layer never assigns a steward — assignment is owned by the authoritative
`create_domain` (steward_user_id) surface.

## The honest gaps

A formal stewardship-assignment workflow and data-product ownership have **no authoritative owner** in the
platform — declared `not_configured`, never a fabricated stewardship assignment.

## References
- `app/services/data_governance_intelligence/registry.py` (`DATA_STEWARDSHIP_REGISTRY`, `_e`)
- `docs/ENTERPRISE_DATA_GOVERNANCE.md`, `docs/DATA_GOVERNANCE.md`, ADR-071
