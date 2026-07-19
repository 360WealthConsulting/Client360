# ADR-017 — Notifications & Communications Architecture

- **Status:** Accepted
- **Date:** 2026-07-19
- **Governs:** Epic 5 — Notifications & Communications Foundation (F5.1–F5.7).
- **Relates to:** [ADR-013](ADR-013-repository-reconciliation.md) (in-place reconciliation),
  [ADR-014](ADR-014-engineering-backlog-and-roadmap-governance.md) (roadmap governance),
  [ADR-015](ADR-015-tamper-evident-audit-architecture.md) (audit), [ADR-016](ADR-016-workflow-orchestration-architecture.md)
  (workflow orchestration). Epic scope and sequencing are recorded in the canonical
  [`../REIMPLEMENTATION_ROADMAP.md`](../REIMPLEMENTATION_ROADMAP.md).

## 1. Context and problem statement
Client360 needs a **platform-level Notifications & Communications Foundation**: a
provider-neutral, event-driven capability that turns domain and workflow events into
governed notifications, delivered through pluggable channels, recorded once, and auditable.
Today the capability exists only as **scattered, domain-coupled fragments** that are not
wired to the Epic 1 outbox / Epic 4 workflow-event stream, have no preferences/consent, no
dispatch/retry engine, and inconsistent audit/evidence. The problem is **reconciliation**
into one canonical platform service — not invention — mirroring how Epic 4 reconciled the
legacy workflow engine (ADR-016 bounded hybrid).

## 2. Existing notification/provider implementations
- **`app/portal/providers.py`** — a `NotificationProvider` ABC (`deliver(recipient, title,
  body, metadata) -> dict`), `InAppNotificationProvider` (`in_app`, delivers), and
  `DisabledNotificationHook` for `email`/`sms`/`push` (returns
  `{"delivered": False, "reason": "provider_not_configured"}`). A generic `ProviderRegistry`
  already exists (used for identity/signature).
- **`app/portal/service.py::notify(...)`** — the portal send path: **idempotent** (dedupe on
  `idempotency_key` against `portal_notifications`), calls `provider.deliver`, and writes a
  `portal_notifications` row with `status = "delivered" | "disabled"`, `delivery_metadata`,
  and the recipient-facing `title`/`body`. Portal-account-scoped.
- **`app/services/benefits_notifications.py::record_scan_health(...)`** — **reuses** the same
  provider registry for internal staff notifications; records an **honest outcome**
  (delivered/disabled/skipped/unavailable) plus a `write_audit_event`, carries **counts only**
  (no PII), and writes **no delivery ledger**.
- **Gaps:** two surfaces (portal ledger vs. benefits audit-only); no outbox/event integration;
  no preferences/consent/suppression; no dispatch/retry/dead-letter; no evidence; providers
  disabled by design.

## 3. Decision drivers
ADR-013 (additive, in-place reconciliation; preserve behavior); a genuine **platform**
capability (consume F1.3/F4.3); safety (deterministic, idempotent, append-only audit/evidence);
**content-vs-reference** discipline (notifications carry content — unlike prior epics);
least-privilege authorization + object-scope; regulatory/AD-5 constraints (no external
provider enabled); operational conservatism (dark-launch; disabled by default).

## 4. Options considered
- **Option A — extend the portal notification/provider implementation directly.** Add outbox
  triggers, preferences, retry, and audit/evidence inside the portal module.
- **Option B — canonical platform notification service that wraps and reconciles the existing
  portal + benefits implementations.** Preserve the provider registry, honest-outcome, and
  in-app behavior; add a platform delivery ledger, event-driven triggers, preferences/consent,
  dispatch/retry, and audit/evidence; portal and benefits delegate to it.
- **Option C — replace the existing notification implementations with a new subsystem.**

## 5. Selected architecture and rationale
**Adopt Option B — a canonical platform notification service (bounded hybrid).** It preserves
the working provider/honest-outcome pattern and the portal in-app behavior, delivers the
platform objective additively (consume F1.3/F4.3; unify portal + benefits), and reconciles —
rather than abandons — the existing code, exactly as ADR-016 did for workflow and F2.2 did for
RBAC. Rationale vs. rejected options:
- **A is inferior:** perpetuates portal coupling and the two-surface split; benefits and future
  domains keep re-solving delivery; the platform substrate (outbox/events) stays unconsumed.
- **C is inferior:** discards validated portal/benefits code and `portal_notifications` history,
  breaks the portal, and contradicts ADR-013 (reconcile in place, never a broad rewrite).

## 6. Component and module boundaries
- **`app/platform/` (or `app/services/`) notifications service** — canonical: create → resolve
  preferences/consent → enqueue → dispatch → record outcome; owns the platform delivery ledger.
- **Channel provider registry** — reconciled from `app/portal/providers.py` (reuse the ABC +
  `ProviderRegistry` pattern); in-app enabled; email/sms/push disabled hooks.
- **Event consumers** — subscribe to F1.3 outbox / F4.3 workflow (and domain) events; map events
  → notification intents per rules + preferences (dark-launched).
- **Reconciliation adapters** — `portal.notify` and `benefits_notifications` delegate to the
  platform service; `portal_notifications` preserved (portal view/read path unchanged).
- The service **calls** the platform (events/audit/evidence); nothing in the engine/domain
  depends on notifications for state.

## 7. Event and outbox integration
Notification **triggers** are idempotent **consumers** of the F1.3 transactional outbox and the
F4.3/F5 event envelopes (workflow lifecycle/approval/SLA events, and domain events). Consumers
are registered only when the outbox dispatcher is enabled (dark-launch), exactly like F4.4.
Notifications **observe** events; they **never** publish events that drive workflow/domain state
and never mutate workflow/domain records. Direction is strictly one-way.

### Event ownership (normative)
- **Domain services own domain events** — a domain service is the sole authoritative publisher of
  its own domain events.
- **Workflow owns workflow events** — the workflow engine (ADR-016) is the sole authoritative
  publisher of workflow lifecycle / approval / SLA events.
- **Notifications subscribe** to those events as idempotent consumers.
- **Notifications never become event owners** — they never publish domain or workflow events, and
  they never emit any business event that alters workflow or domain state. (Any purely
  notification-internal/observability signal a consumer might emit is never workflow- or
  domain-authoritative.)

One-way dependency (event flow):

```
 Domain services ──(domain events)──┐
                                     ├─▶  F1.3 outbox / F1.4 envelopes  ─▶  Notification consumers ─▶  channel delivery + notification ledger
 Workflow engine ─(workflow events)─┘         (authoritative                    (subscribers only)
        ▲                                        publishers)                             │
        └──────────────  NO back-edge: notifications never publish events  ──────────────┘
                         that drive or alter workflow / domain / evidence state
```

Notifications are strictly **downstream**: authoritative publishers → outbox → notification
subscribers → delivery/ledger. There is no edge from notifications back into workflow, domain, or
evidence state.

## 8. Notification lifecycle and delivery ledger
Deterministic lifecycle: `pending → (suppressed | delivered | disabled | failed | dead)`. A new
platform **`notifications`** append-oriented delivery ledger records: id, notification_uid
(stable), recipient reference, channel, notification_type, status, attempts, outcome/honest
reason, idempotency_key, references, timestamps — and the recipient-facing `title`/`body`
(content; see §14). `portal_notifications` is **retained** and reconciled (portal reads unchanged;
new sends flow through the platform ledger, with a documented mapping). Creation is idempotent
(dedupe on a deterministic key); delivery outcome is recorded once.

### Ledger authority (normative)
The notification ledger records **notification intent and delivery outcomes only**. It is **never
authoritative** for workflow state, business/domain state, or evidence state. Specifically:
- **Deleting or purging notifications never changes workflow or domain state.**
- **Reading notifications never completes work** — read paths are side-effect-free.
- **Successful delivery never implies business/workflow completion** — delivery is not a
  completion signal.
- **Delivery failures never roll back a domain or workflow transaction** — delivery is downstream
  of, and independent from, the committed business change (the outbox guarantees an event exists
  iff the business change committed; notification and delivery happen after that commit and cannot
  reverse it).

The authoritative records remain elsewhere: **workflow state** in the workflow engine (ADR-016),
**domain state** in the domain services, and **evidence state** in the F3.3 evidence store
(ADR-015). The notification ledger is a downstream, non-authoritative record of intent and outcome.

## 9. Channel provider model
Provider-neutral **ports** keyed by channel, reusing the existing `NotificationProvider` ABC and
`ProviderRegistry`. `in_app` is enabled and functional; `email`, `sms`, `push` are **disabled
hooks by default**. New channels are added as ports without changing the service. **No external
provider is enabled by this ADR**; enabling one is a future, separately-authorized configuration
change (never a code default).

## 10. Disabled-provider and honest-outcome behavior
Disabled providers return an **explicit, honest outcome** (`delivered=False`,
`reason="provider_not_configured"`), which the service records as ledger `status="disabled"` (or
`skipped`/`unavailable` where applicable) — never a silent success and never an exception that
looks like a real failure. Honest outcomes are the contract for every dispatch (delivered /
disabled / skipped / failed), preserving the existing benefits/portal behavior.

## 11. Recipient preferences, consent, and suppression
A new **`notification_preferences`** model holds per-recipient, per-channel opt-in/out and
consent. **Channel resolution is deterministic**: a notification is delivered on a channel only
if the recipient is opted in / not suppressed for that (type, channel). **Consent and suppression
are enforced in the service layer, independently of capabilities** — an authorized sender cannot
override a recipient's opt-out. Suppressed notifications are recorded with an honest
`status="suppressed"` (auditable), never silently dropped.

## 12. Idempotency and retry semantics
**Deterministic, duplicate-safe.** Notification creation uses a deterministic idempotency key
derived from the triggering event/outcome (e.g. `notif:<event_id>:<recipient>:<type>`), so an
event redelivery cannot create a duplicate notification. Delivery uses the existing outbox
`outbox_processed_events` dedupe (per consumer) plus a per-notification delivery key, so a retry
**never double-delivers**. Failures raise so the outbox/scheduler retries with backoff and
dead-letters after the max attempts (operator-visible); because delivery is idempotent, retries
never duplicate.

## 13. Audit and evidence requirements
Every material notification outcome (created, delivered, disabled, suppressed, failed) produces a
**hash-chained audit record (F3.1/ADR-015)** and, where warranted, a **write-once evidence record
(F3.3)** linked by `audit_event_id` — reusing the F4.7 `record_workflow_evidence` pattern (a
sibling `record_notification_evidence`). **Append-only**: audit and evidence records reject
UPDATE/DELETE (existing triggers). Records are **reference-only** (§14) and deterministic /
idempotent (a repeated outcome does not duplicate audit/evidence).

## 14. Content-versus-reference boundary (normative)
Notifications are the **first** platform capability that legitimately carries recipient-facing
**content** (a `title`/`body`), unlike the strictly reference-only events/evidence of Epics 1–4.
The boundary is therefore explicit and normative:
- **Delivery may carry content:** the delivery ledger and the actual channel delivery may store
  and transmit the necessary `title`/`body`.
- **Observability stays reference-only:** **events, audit records, evidence, and logs record only
  references** — notification id/uid, type, channel, recipient reference, and honest outcome —
  and **never** the `title`/`body` or any PII. No notification content leaks into events, audit,
  evidence, or logs.

## 15. Authorization and object-scope model
Least-privilege, object-scoped where appropriate. Introduce a minimal **`notification.*`** family
only where a documented gap exists:
- `notification.read` — read notification status/history (object-scoped to the caller's own
  notifications; admin read only with an explicit admin capability, never `record.read_all`).
- `notification.manage` — manage **own** preferences/consent (object-scoped to the recipient's own
  identity).
- `notification.administer` — administrative inspect/resend (separated; least-privilege).
No `record.read_all` or broad administrative shortcut. Consent/suppression is enforced in the
service independent of capabilities (holding a send capability does not override opt-out). Existing
portal object-scope for portal notifications is preserved.

## 16. Compatibility and migration approach
Additive and reversible. Expected migrations: **`notifications`** ledger (F5.1) and
**`notification_preferences`** (F5.3) — idempotent DDL, down_revision chaining from
`f41b2n3d4c5e`, single head maintained, reflected tables not promoted to Core metadata,
pristine base→head + downgrade/re-upgrade validated. `portal_notifications` is **retained and
reconciled**, not dropped; the portal read path and existing in-app behavior remain functional.
Events reuse `outbox_events.payload`; evidence reuses the `evidence` table. No breaking API/route
change (F5.7 adds routes additively, enumerated).

## 17. Dark-launch and rollout strategy
Consumers/dispatch register **only when the outbox dispatcher is enabled**
(`OUTBOX_DISPATCHER_ENABLED`), like F4.4 — so by default no notification consumers exist and
runtime behavior is unchanged. In-app remains the only enabled channel. Event-driven notification
is opt-in; existing imperative `portal.notify` continues to work throughout. External channels stay
disabled hooks until a separate, authorized configuration step.

## 18. Regulatory and AD-5 boundaries
**No external provider (email/SMS/push) is enabled by this ADR** and none is enabled by default.
No new **regulated Insurance** communications are built or enabled (AD-5 — existing insurance is
frozen legacy). No marketing/bulk campaigns. Content transmitted to recipients must respect
consent/suppression and the reference-only observability boundary.

## 19. Risks and mitigations
1. **Content leakage into observability (highest).** *Mitigation:* the §14 boundary is normative
   and test-enforced (events/audit/evidence/logs assert no `title`/`body`).
2. **Duplicate notification/delivery on retry.** *Mitigation:* deterministic idempotency keys +
   outbox dedupe + per-notification delivery key.
3. **Duplication with portal/benefits (abandonment risk).** *Mitigation:* Option B reconciles;
   portal/benefits delegate; `portal_notifications` retained.
4. **Consent bypass via capabilities.** *Mitigation:* service-layer enforcement independent of
   capabilities.
5. **Accidental external send / AD-5.** *Mitigation:* disabled-by-default hooks; no provider
   enabled in code; honest outcomes.
6. **Scope-theme confirmation.** Recorded in the roadmap; Epic 5 scope already adopted.

## 20. Consequences
**Positive:** one canonical, event-driven notification platform; portal + benefits reconciled;
outbox/event substrate consumed; deterministic, idempotent, append-only audit/evidence; content
contained to delivery; disabled-by-default, dark-launched, reversible. **Neutral/limits:** two
ledgers coexist during reconciliation (`portal_notifications` + platform `notifications`) with a
documented mapping; external channels remain non-functional until separately enabled; content is
stored in the delivery ledger (protected, not in observability).

## 21. Proposed F5.1–F5.7 implementation sequence
- **F5.1** Notification data model & append-only delivery ledger (`notifications`); migration.
- **F5.2** Provider-neutral channel registry (reconcile `portal/providers.py`; in-app on; hooks disabled).
- **F5.3** Recipient preferences & consent/suppression (`notification_preferences`; deterministic resolution); migration.
- **F5.4** Event-driven triggers (idempotent outbox/F4.3 consumers; dark-launched).
- **F5.5** Dispatch & retry engine (honest outcomes; retry/dead-letter; idempotent).
- **F5.6** Notification audit & evidence (append-only, reference-only; reuse F3.1/F3.3/F4.7).
- **F5.7** Notification API & administrative surface (least-privilege, object-scoped; additive routes).
- **E5.C / E5.R / E5.P** closeout, release, publish (mirror E4.C; tag `v0.5-…`).

## 22. Acceptance criteria for beginning F5.1
ADR-017 **reviewed and accepted** (status Proposed → Accepted); Epic 5 scope confirmed in
`REIMPLEMENTATION_ROADMAP.md`; the §14 content/reference boundary, §12 idempotency/retry
semantics, and §15 authorization model agreed; F5.1 scoped to the additive `notifications` ledger
migration + model only (no channel/trigger/dispatch behavior). No F5.x code begins until this ADR
is accepted.

## Required-constraints conformance
Notifications observe (never drive) workflow/domain state · in-app remains functional · email/SMS/
push disabled by default · no external provider enabled here · disabled providers return honest
outcomes · events/audit/evidence/logs reference-only · content may be delivered but must not leak
into observability · creation/delivery deterministic & duplicate-safe · retries never duplicate ·
consent/suppression enforced in the service independent of capabilities · audit/evidence append-only
· authorization least-privileged & object-scoped · no `record.read_all` · portal + benefits
reconciled, not abandoned · references `REIMPLEMENTATION_ROADMAP.md` · no Epic 5 implementation until
accepted.

## References
ADR-013, ADR-014, ADR-015, ADR-016; `../REIMPLEMENTATION_ROADMAP.md`; `docs/OUTBOX.md` (F1.3),
`docs/EVENTS.md` (F1.4), `docs/WORKFLOW_EVENTS.md` (F4.3), `docs/WORKFLOW_EVIDENCE_AUDIT.md` (F4.7),
`docs/AUTHORIZATION.md` (F2.2); `app/portal/providers.py`, `app/portal/service.py`,
`app/services/benefits_notifications.py`.
