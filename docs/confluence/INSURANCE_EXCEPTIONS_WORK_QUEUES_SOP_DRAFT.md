# Insurance Exceptions & Work Queues — Standard Operating Procedure (DRAFT)

> **Status: DRAFT — not published.** Staged for the 360OS Operations Manual (Confluence, space
> `3WCO`, section **🛡️ Insurance Operations**, cross-listed under **⚖️ Compliance**). Do **not**
> publish until Release 0.10.0 Phase 6 is RC-validated and the page owner approves. Documents
> only current, tested, non-regulated functionality. Contains **no** suitability,
> replacement/1035, or licensing/CE determination — those remain behind the **AD-5** gate.

| Field | Value |
|---|---|
| **Proposed page title** | Insurance Exceptions & Work Queues — Operating Procedure |
| **Proposed 360OS Id** | `INS-SOP-06` (pending page-owner registry) |
| **Manual section** | 🛡️ Insurance Operations (cross-listed: ⚖️ Compliance) |
| **Owner** | Michael Shelton |
| **Status** | Draft (unpublished) |
| **GitHub source of truth** | `app/services/insurance_detectors.py` (`run_insurance_scan`), `app/services/insurance_work.py`, `app/jobs/scheduler.py`, `app/routes/insurance.py`; migration `c9k0m1n2h3j4`; shared: `app/services/exception_*`, `app/services/work_management.py` |
| **Applicable release** | v0.10.0 · Phase 6 |
| **Publication gate** | Phase 6 RC-validated **and** page-owner approval; regulated content excluded (AD-5) |
| **Review cycle** | Quarterly (next review: 2026-10-16) |

## 1. Purpose

Give operations and oversight staff one procedure for monitoring insurance exceptions and
working the insurance queues. Insurance monitoring **reuses the firm's shared platform** — the
Exception Engine, Work Management, and the background scheduler — so the concepts here are the
same as tax and benefits; only the insurance queues and codes are new.

## 2. Scope

**In scope:** the automated insurance scan, the insurance exception codes, the insurance work
queues, and how work is assigned and worked.

**Out of scope:** any suitability/replacement/1035/licensing determination (AD-5-blocked); any
**client-facing** exception visibility — insurance exceptions are **firm-internal** and never
appear on a client's timeline or portal.

## 3. What the scan does

A single scheduled job, **`insurance-detector-scan`** (every 30 minutes by default), runs
`run_insurance_scan()`, which checks the whole insurance book through the **shared Exception
Engine** and raises/refreshes these operational exceptions:

| Code | Raised when | Queue |
|---|---|---|
| `INS_REVIEW_OVERDUE` | An in-force policy review is past due | Insurance — In-Force Reviews |
| `INS_LICENSE_EXPIRING` | A producer license nears expiry | Insurance — Licensing and CE |
| `INS_CE_PERIOD_ENDING` | A CE period nears its end | Insurance — Licensing and CE |
| `INS_COMMISSION_VARIANCE` | A reconciled commission differs from expected | Insurance — Commissions |
| `INS_COMMISSION_OUTSTANDING` | An expected commission is past due and unpaid | Insurance — Commissions |

The scan is **idempotent** (re-running never duplicates exceptions), **auto-resolves** an
exception when its condition clears (and **reopens** it if the condition recurs), and is
**failure-isolated** — one detector or one organization's bad data never stops the rest. Staff
can also trigger it on demand from **Insurance → Scan** (`POST /api/v1/insurance/scan`).

Each run reports honestly: **organizations scanned, exceptions opened / resolved / reopened /
skipped, and any failures.**

## 4. The insurance work queues

Insurance exceptions appear in the standard Work Management queues (same UI as tax/benefits):

- **Insurance — Unassigned** — items with no assignee yet (start here).
- **Insurance — Exceptions** — all open insurance exceptions.
- **Insurance — In-Force Reviews / Licensing and CE / Commissions** — by area.
- **Insurance — High Priority or Blockers** — blocker/high-severity items first.

Queue membership is **firm-internal** and requires `insurance.read`; it never grants record
scope (scope is enforced separately) and is never client-visible.

## 5. Procedure

### 5.1 Work the queues
1. Open **Work → Insurance — Unassigned**; claim or assign items.
2. Move to the area queue (Reviews / Licensing / Commissions) and resolve the underlying
   condition (complete the review, renew the license, reconcile the commission).
3. When you fix the condition, the next scan **auto-resolves** the exception — you do not close
   it by hand.

### 5.2 Assignment
Assignment uses the firm's **existing assignment rules**. When a rule matches an insurance
exception's attributes (domain, code, severity, organization), the scan assigns it
automatically; unmatched items stay in **Insurance — Unassigned** for manual assignment. There
is no insurance-specific assignment model.

### 5.3 Record scope
Commission and review exceptions carry the client **organization** for record scope where the
policy is organization-owned, so org-scoped staff see their book's items. **Compensation and
commission detail never reach the client timeline or portal** — these are internal operations
and oversight items only.

## 6. Controls & audit

- Every raise/resolve/reopen is an immutable audit event through the shared engine.
- The scan is idempotent and auto-resolving; overlap is prevented by the scheduler.
- Reporting is honest (opened/resolved/reopened/skipped/failures) — no silent drops.

## 7. Compliance boundary (AD-5)

This procedure detects and routes **operational** conditions only. It makes **no** suitability,
replacement/1035, or licensing/CE determination and blocks nothing. Those are **regulated** and
remain blocked under **AD-5** until a qualified, named compliance reviewer and an approved
sign-off are in place. If a queue item seems to require such a judgement, **stop and escalate**.
