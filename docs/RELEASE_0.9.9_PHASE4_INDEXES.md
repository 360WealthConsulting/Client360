# Release 0.9.9 Phase 4 — Database Optimization (Indexes)

PostgreSQL does not automatically index foreign-key columns. The per-request
client, household, portal, and workflow read paths were sequential-scanning these
tables (RC9 H20; `PRODUCTION_ARCHITECTURE.md` §5/§23). Phase 4 adds indexes only
for columns confirmed to be actual query predicates — audit back-references
(`created_by_user_id`, `updated_by_user_id`, `reviewer_*`, `approver_*`) are
deliberately **not** indexed because no hot path filters on them.

Indexes are built with `CREATE INDEX CONCURRENTLY` inside an Alembic
`autocommit_block`, so production builds hold no write lock; downgrades use
`DROP INDEX CONCURRENTLY`. Production builds should be run during a low-traffic
window and monitored (an interrupted `CONCURRENTLY` build can leave an INVALID
index that must be dropped and rebuilt).

## Migrations

- `n4e25b3c2f1d` — batch 1 (hot path), 14 indexes.
- `o5f36c4d3e2a` — batch 2 (remaining query-justified scope columns), 10 indexes.

## Indexes and the query each supports

### Batch 1 — `n4e25b3c2f1d`

| Index | Column(s) | Query it supports |
|---|---|---|
| `ix_people_household_id` | `people(household_id)` | household member expansion (`portal/service.py:102`) |
| `ix_tasks_person_id` | `tasks(person_id)` | per-client task lists / client summary |
| `ix_activities_person_id` | `activities(person_id)` | per-client activity feed |
| `ix_documents_person_id` | `documents(person_id)` | per-client documents (`portal/service.py:228`, `work_management.py:232`) |
| `ix_timeline_events_person_id` | `timeline_events(person_id)` | per-client timeline (`portal/service.py:229`, `work_management.py:233`) |
| `ix_timeline_events_household_id` | `timeline_events(household_id)` | household timeline (`portal/service.py:229`) |
| `ix_household_relationships_person_id` | `household_relationships(person_id)` | person → households reverse lookup |
| `ix_portal_notifications_portal_account_id` | `portal_notifications(portal_account_id)` | portal notifications (`portal/service.py:226`) |
| `ix_portal_threads_household_id` | `portal_threads(household_id)` | portal thread scope (`portal/service.py:128/227`) |
| `ix_portal_threads_person_id` | `portal_threads(person_id)` | portal thread scope (`portal/service.py:128/227`) |
| `ix_tax_engagements_person_id` | `tax_engagements(person_id)` | per-client engagements |
| `ix_tax_engagements_household_id` | `tax_engagements(household_id)` | household engagements |
| `ix_audit_events_actor_user_id` | `audit_events(actor_user_id)` | actor activity audit queries |
| `ix_audit_events_entity` | `audit_events(entity_type, entity_id)` | per-record audit trail lookup |

### Batch 2 — `o5f36c4d3e2a`

| Index | Column(s) | Query it supports |
|---|---|---|
| `ix_accounts_person_id` | `accounts(person_id)` | portfolio per-client join (`portfolio.py:23/38`, `people.py:67/263`) |
| `ix_accounts_household_id` | `accounts(household_id)` | household AUM rollup (`portfolio.py:22/24/31`) |
| `ix_microsoft_documents_person_id` | `microsoft_documents(person_id)` | person document match/sync |
| `ix_portal_accounts_person_id` | `portal_accounts(person_id)` | portal account resolution by person |
| `ix_portal_document_requests_person_id` | `portal_document_requests(person_id)` | portal open requests (`portal/service.py:225`) |
| `ix_portal_message_receipts_portal_account_id` | `portal_message_receipts(portal_account_id)` | read-receipt lookups (`portal/service.py:162`) |
| `ix_portal_sessions_portal_account_id` | `portal_sessions(portal_account_id)` | portal session validation (per-request) |
| `ix_workflow_events_workflow_instance_id` | `workflow_events(workflow_instance_id)` | workflow history load (`workflow_automation.py:175`) |
| `ix_workflow_instances_person_id` | `workflow_instances(person_id)` | portal workflow scope join (`portal/service.py:204`) |
| `ix_workflow_instances_household_id` | `workflow_instances(household_id)` | portal workflow scope join (`portal/service.py:204`) |

## Explicitly excluded (already indexed)

`tax_engagement_return_id` is already indexed on all 11 child tables;
`workflow_steps(workflow_instance_id)` and `portal_access_grants(portal_account_id)`
already have leading-column indexes. No duplicate indexes were added.

## Measured impact

Representative benchmark on `timeline_events` loaded to ~63k rows, filtered by
`person_id` (700 matching rows):

| | Plan | Est. cost | Execution |
|---|---|---|---|
| Before | Seq Scan | 1576.89 | 2.314 ms |
| After | Bitmap Index Scan (`ix_timeline_events_person_id`) | 805.83 | 0.852 ms |

~2.7× faster on this query; the improvement scales with table size (seed test
tables are small, so absolute times are modest but planner selection is
confirmed for all 24 indexes). No query result changed.
