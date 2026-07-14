# Tax Return Lifecycle and Production Automation

## Purpose

Sprint 5.3 implements the operational production pipeline for tax returns. It
keeps `tax_engagement_returns.status` as the canonical lifecycle state and adds
append-only lifecycle and filing event ledgers, review/correction records,
client decisions, provider-neutral filing status, production queues, dashboards,
portal actions, and workflow synchronization.

## Lifecycle

The supported ordered states are:

`received → ready_to_prepare → in_preparation → manager_review → partner_review
→ client_review → awaiting_efile_authorization → ready_to_file → filed →
accepted → delivered → completed → archived`.

`awaiting_information` can pause received/preparation work and return it to the
appropriate preparation state. Review or client rejection returns work to
preparation. Filing rejection moves to `rejected`; resubmission returns to
`filed` while awaiting the next provider response. Invalid transitions are
rejected unless a controlled migration/administrative operation explicitly
requests a forced transition.

Every transition records its prior state, target state, reason, actor, portal
identity when applicable, and timestamp in an immutable event ledger. It also
publishes an existing workflow-history event, client timeline event, and
immutable audit event.

## Workflow automation

The lifecycle service observes existing workflow execution snapshots:

- completed intake and document steps make a received return ready to prepare;
- completed preparation moves active preparation to manager review;
- completed review moves review work to client review;
- completed filing work moves ready-to-file work to filed.

The service does not create another workflow engine. Review decisions and client
actions use the same transition service so automated and manual operations share
one state machine.

## Review management

Preparer, manager, and partner review records link to existing `work_approvals`.
Reviewer users or teams are routed through the existing approval dashboard.
Approval advances the return; returned work creates correction items and moves
the return back to preparation. Review notes, correction resolution, approver,
decision, and timestamps preserve the complete history.

## Client actions

The existing portal grant model protects return approval, e-file authorization,
and delivery acknowledgement. Decisions record the portal account, status,
notes, and time. Rejection returns the return to preparation. Approval advances
to e-file authorization or ready-to-file; delivery acknowledgement completes
the return.

## Filing provider boundary

Core status supports `ready`, `submitted`, `accepted`, `rejected`, and
`resubmitted`. Filing events contain provider key, external ID, submission ID,
reason code, message, metadata, and an idempotency key. The provider protocol
returns normalized filing results. Only the manual provider is enabled; no
Drake, IRS, or other vendor API is bound to business logic.

## Queues and dashboards

Nine reusable `work_queues` cover ready to prepare, preparing, awaiting client,
manager review, partner review, ready to file, rejected, delivery, and completed
today. Existing assignment records identify preparers and teams.

The production, review, filing, and metrics dashboards expose authorized:

- counts by lifecycle and filing status;
- workload by preparer and pending reviewer;
- overdue stages and client/filer waiting counts;
- average preparation time and 30-day production velocity;
- manager/partner review bottlenecks.

## APIs and UI

Staff APIs under `/api/v1/tax/returns` provide lifecycle detail/transitions,
workflow synchronization, review requests/decisions, correction resolution,
filing events, and production metrics. Staff pages are:

- `/tax/returns` — production;
- `/tax/returns/reviews` — review;
- `/tax/returns/filing` — filing;
- `/tax/returns/metrics` — metrics.

Portal APIs under `/api/v1/portal/tax/returns` expose scoped return status and
client decisions. `/portal/tax-returns` displays return, filing, and approval
status using the existing portal session and household authorization boundary.

## Security and audit

Staff access requires existing `tax.read`, `tax.write`, or `tax.review`
capabilities plus Release v0.9.5 record filtering. Portal access requires an
active person/household grant with task permission. Lifecycle and filing ledgers
are append-only at the database layer; all material changes also publish
immutable audit and client timeline events.

## Operational checklist

1. Confirm preparer, manager, and partner assignments and team capacity.
2. Review the nine production queue definitions and SLA expectations.
3. Exercise one return through review return/correction and approval paths.
4. Exercise client return approval, e-file authorization, and delivery receipt.
5. Record a rejected filing, resubmit it, and accept the second submission.
6. Confirm dashboard metrics and portal visibility using staging identities.
7. Approve the filing-provider adapter before enabling automated submission.

## Known limitations

- Only the provider-neutral manual filing adapter is enabled.
- Production velocity uses current lifecycle timestamps rather than a separate
  materialized analytics warehouse.
- Dashboard preparer/reviewer groupings expose IDs; enriched staff names and
  capacity forecasts are future presentation work.
- Bulk lifecycle operations, electronic signature for e-file authorization,
  and provider polling are deferred.
