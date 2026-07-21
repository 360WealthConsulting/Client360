# Phase D.6 — Compliance Enablement Framework

The governance layer **above** Advisor Intelligence. It provides the infrastructure
that lets governed rules exist and be reviewed; it does **not** automate compliance,
make suitability decisions, approve recommendations, or enforce anything. Implemented
on `release/0.13.0`.

## Architecture
A new, dedicated package `app/services/compliance/` reads Advisor Intelligence
registry metadata and never the reverse. Advisor Intelligence remains the execution
layer, unchanged; the Compliance Framework is a read-only governance view over it.

```
Advisor Intelligence Registry   (execution layer — unchanged)
            │  list_registered_signals()  (one-way read)
            ▼
       Rule Catalog               (app/services/compliance/rule_catalog.py)
            │
            ▼
     Read-only UI                 (GET /admin/rule-catalog)
```

**Dependency direction is strictly one-way.** `advisor_intelligence.py` imports no
compliance module (enforced by a test). The Compliance Framework must never be
imported by, or modify, Advisor Intelligence.

## RuleDefinition
An immutable dataclass — metadata only, no persistence — with the full governance
field set: `rule_id`, `title`, `description`, `category`, `governing_rule`, `version`,
`policy_gate`, `owner_role`, `owner_name`, `approval_status`, `approved_date`,
`effective_date`, `expiration_date`, `source_documents`, `implementation_status`,
`superseded_by`, `deprecated_reason`. It has a JSON-safe `to_dict()`.

Each `RuleDefinition` is **projected** from one `RegisteredSignal`, with **no
fabrication**:

| Field | Source |
|---|---|
| rule_id / category / description / policy_gate / governing_rule | registry (verbatim) |
| version | registry `rule_version`, or `1.0.0` (initial-release convention when a rule omits one) |
| title | presentation-derived from `rule_id` (title-cased) |
| owner_role | the role from the registry `compliance_owner` (the `"(unassigned — …)"` annotation is stripped) |
| owner_name | **`None`** — no individual is assigned; never fabricated |
| approval_status | mapped into the governance vocabulary (below) |
| approved / effective / expiration_date | **`None`** — no governance decision has been recorded |
| source_documents | real files that exist in the repo (below) |
| implementation_status | `implemented` — the rule has a registered producer |
| superseded_by / deprecated_reason | `None` — nothing is deprecated |

## RuleCatalog
`RuleCatalog.from_registry()` builds the catalog from the live registry. Responsibilities
(read-only; it never executes a rule, generates a recommendation, or modifies Advisor
Intelligence):
- `list_rules()`, `get_rule(rule_id)` — enumerate / retrieve.
- `categories()`, `policy_gates()`, `approval_statuses()` — expose governance facets.
- `validate_uniqueness()` — defensive rule_id uniqueness invariant.
- `verify_versions()` — every rule carries a valid semantic version.
- `query(search, category, policy_gate, approval_status, sort, descending)` — the
  search/filter/sort used by the UI, all resolved in Python (the template only renders).

## Governance metadata

### Ownership
Owner roles come from the registry: `advisor_operations` (operational owner) and
`compliance_reviewer` (for policy-gated rules, currently unassigned). `owner_name` is
always `None` — no individual is invented. Per the D.6 rule, a rule with no recorded
owner/approval surfaces as `pending_assignment`.

### Approval states
Governance vocabulary (display-only, no workflow): `draft`, `pending_assignment`,
`pending_review`, `approved`, `deprecated`, `retired`. The registry vocabulary is
mapped in: `approved → approved`, `pending_compliance_review → pending_review`, and a
rule with no recorded approval → `pending_assignment`.

### Versioning
Semantic versions (`MAJOR.MINOR.PATCH`). Helpers `is_valid_semver`, `parse_version`,
`compare_versions` provide **comparison only** — no migrations. Sorting by version is
semantic (`1.10.0 > 1.9.0`).

### Lifecycle
Every rule exposes `status` (approval), `effective_date`, `expiration_date`,
`superseded_by`, and `deprecated_reason`. All lifecycle metadata is **informational
only** and never affects Advisor Intelligence execution. No rule is currently
deprecated or superseded, so those fields are `None`.

### Documentation references
Each rule references real documents (pointers only — nothing is parsed or evaluated):
all rules reference `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`; governed recommendation
rules additionally reference `docs/V1_RISK_REGISTER.md` (GOV-2) and
`docs/PRODUCT_DECISIONS.md` (PD-4). The model supports architecture / compliance-manual
/ SOP / regulatory reference types; only those that exist are populated.

## Rule Catalog UI
A read-only page at **`GET /admin/rule-catalog`** (Oversight nav group). Columns: Rule
(+ description + governing rule), Category, Version, Policy Gate, Owner, Approval
Status, Effective Date, Documentation. Supports **search** (rule id / title /
description / governing rule), **filtering** (category, policy gate, approval status),
and **sorting** (sortable column headers; version sorts semantically) — all via GET
query params. There are **no** edit, approve/reject, workflow, or inline-action
controls, and no POST endpoint.

## Authorization
Reuses the existing administrative read capability **`audit.read`** (the same
capability that gates `/admin/audit`). The middleware maps `^/admin/rule-catalog →
audit.read` (before the `^/admin → identity.manage` catch-all), and the route depends
on `require_capability("audit.read")`. No new authorization model; access is not
broadened.

## Relationship to Advisor Intelligence
Advisor Intelligence is the **execution** layer (unchanged in this phase). The
Compliance Framework is the **governance** layer that reads its registry. This phase
made **zero** edits to `advisor_intelligence.py`; the D.5E golden regression and all
D.5A–D.5D tests re-run green, proving no behavioral change.

## Exclusions honored
No rule editing, approval workflow, electronic signatures, notifications, audit logging
of catalog reads beyond the existing middleware, task creation, workflow execution,
recommendation execution, rule persistence, database tables, migrations, compliance
automation, suitability/licensing engine, or AI/ML/predictive/embeddings/vector search.
The Rule Catalog is strictly read-only.

## Future D.7 integration
The governance layer is the seam a future D.7 (governed disposition / approval records)
plugs into: `RuleDefinition` already models `approval_status`, `owner_role`/`owner_name`,
`approved_date`, and the full lifecycle; a D.7 disposition store could **record** real
owner assignments, approval decisions, and effective/expiration dates and feed them into
the projection (replacing the current `None` placeholders) — without changing Advisor
Intelligence or the read-only nature of the catalog view.

## Remaining technical debt
- No real owners, approval dates, or effective/expiration dates are recorded yet
  (GOV-2/PD-4 assignment is a business decision); they surface as `pending_assignment`/
  `None` until D.7 provides a governed record.
- Compliance-manual / internal-SOP / regulatory-citation documents do not exist yet, so
  only the architecture and governance-decision references are populated.
- The catalog derives everything live from the registry each request; if the registry
  grows large, a future phase could add an in-memory build cache (explicitly out of
  scope here — no caching, no persistence).
