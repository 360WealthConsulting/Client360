# Domain Event Catalog (Phase D.34 + D.35)

The complete registry of typed domain-event contracts. Each is a `schema_version`-1 canonical
`Envelope` published to the transactional outbox. Payloads are **references-only** (ids/codes/statuses/
timestamps/actor refs). The registry (`domain_event_contracts`) mirrors the in-code contracts
(`app/services/events/contracts.py`, from `app/database/event_seed.py`).

## D.34 — platform events

| Event type | Category | Producer | Payload (references only) |
|---|---|---|---|
| `workflow.transition` | workflow | workflow.execution | instance_id, from, to, action |
| `workflow.approval` | workflow | workflow.approvals | approval_id, step_id, decision |
| `workflow.sla` | workflow | workflow.sla | escalation_id, step_id, level |
| `orchestration.lifecycle` | orchestration | orchestration.engine | instance_id, definition, event, stage |
| `runtime.coordination` | runtime | runtime.coordination | generation, worker, event |

## D.35 — business producer adoption

| Event type | Category | Owner / Producer | Payload (references only) |
|---|---|---|---|
| `people.person_created` | people | people / people.promotion | person_id, match_method |
| `people.person_updated` | people | people / people.service | person_id, changed_fields (names only) |
| `people.identity_merged` | people | people / people.merge | person_id, source_contact_count |
| `households.household_created` | households | households | household_id |
| `households.membership_changed` | households | households | household_id, person_id, relationship_type |
| `opportunity.created` | opportunity | opportunity | opportunity_id, pipeline_id, stage_id, status |
| `opportunity.stage_changed` | opportunity | opportunity | opportunity_id, to_stage_id, from_status, to_status |
| `opportunity.won` | opportunity | opportunity | opportunity_id, status |
| `opportunity.lost` | opportunity | opportunity | opportunity_id, status |
| `referral.recorded` | referral | referral | referral_source_id, source_type, status |
| `operations.task_created` | operations | operations.tasks | task_id, project_id, status, priority |
| `operations.task_completed` | operations | operations.tasks | task_id, from_status, to_status |
| `operations.project_created` | operations | operations.projects | project_id, category, status |
| `operations.project_status_changed` | operations | operations.projects | project_id, from_status, to_status |
| `exception.opened` | exceptions | operations / exception.engine | exception_id, code, domain, category, severity, status |
| `exception.resolved` | exceptions | operations / exception.engine | exception_id, resolution_code, from_status, to_status |
| `document.registered` | documents | document_platform | document_id, classification, status |
| `document.status_changed` | documents | document_platform | document_id, from_status, to_status |
| `document.archived` | documents | document_platform | document_id, from_status, to_status |
| `compliance.review_opened` | compliance | compliance.reviews | review_id, status, governing_rule, rule_version |
| `compliance.approval_granted` | compliance | compliance.reviews | review_id, decision_id, decision |
| `compliance.approval_denied` | compliance | compliance.reviews | review_id, decision_id, decision |
| `tax.engagement_created` | tax | tax.domain | engagement_id, return_id, tax_year, return_type_code |
| `tax.return_status_changed` | tax | tax.lifecycle | return_id, from_status, to_status |
| `tax.filing_submitted` | tax | tax.lifecycle | return_id, filing_status, provider_key |
| `tax.filing_acknowledged` | tax | tax.lifecycle | return_id, filing_status |
| `insurance.case_created` | insurance | insurance | case_id, case_type, status |
| `insurance.application_status_changed` | insurance | insurance | case_id, from_status, to_status |
| `insurance.policy_issued` | insurance | insurance | policy_id, status, carrier_id |
| `benefits.enrollment_created` | benefits | benefits.enrollment | enrollment_id, plan_year_id, coverage_tier, status |
| `benefits.enrollment_status_changed` | benefits | benefits.enrollment | enrollment_id, from_status, to_status |

Each D.35 contract has a dark-launched `analytics.projection` subscription (a future read-model
consumer) so the model is complete and governable without changing behavior. See
`docs/EVENT_REGISTRY.md` for the registry mechanics and `docs/DOMAIN_EVENT_PAYLOAD_SAFETY.md` for the
references-only rules.

## Client Portal (D.43)

The Client Portal adds **no** domain-event contracts. It is an external composition + delegated-action
surface: reads are not events, and every mutation delegates to the authoritative owning service, which is
the producer of any lifecycle event. Portal activity is recorded on the append-only audit ledger
(references only), not the transactional outbox — the portal never introduces a second event bus. See
`docs/CLIENT_PORTAL_GOVERNANCE.md` and ADR-048.

## Adding a contract

Append to `D35_CONTRACTS_SEED` (or the D.34 seed), add a seeding migration (single Alembic head), add a
publishing site in the authoritative service, and run governance — see `docs/EVENT_AUTHORING_GUIDE.md`
and `docs/DOMAIN_EVENT_PRODUCER_ADOPTION.md`.
