# Insurance Commissions — Standard Operating Procedure (DRAFT)

> **Status: DRAFT — not published.** Staged for the 360OS Operations Manual (Confluence,
> space `3WCO`, section **🛡️ Insurance Operations**, cross-listed under **💰 Finance &
> Accounting**). Do **not** publish until Release 0.10.0 Phase 5 is RC-validated and the page
> owner approves. This SOP documents **only** current, tested, non-regulated functionality.
> It contains **no** suitability, replacement/1035, licensing/CE, or other compliance
> determination — those remain blocked behind the **AD-5** compliance gate.

| Field | Value |
|---|---|
| **Proposed page title** | Insurance Commissions — Operating Procedure |
| **Proposed 360OS Id** | `INS-SOP-05` (pending page-owner registry) |
| **Manual section** | 🛡️ Insurance Operations (cross-listed: 💰 Finance & Accounting) |
| **Owner** | Michael Shelton |
| **Status** | Draft (unpublished) |
| **GitHub source of truth** | `app/services/insurance_commissions.py`, `app/services/insurance_detectors.py`, `app/services/insurance_reporting.py`, `app/routes/insurance.py`; migration `b8i9k1l2g3j4`; `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md` §4/§12 |
| **Applicable release** | v0.10.0 · Phase 5 |
| **Publication gate** | Phase 5 RC-validated **and** page-owner approval; regulated content excluded (AD-5) |
| **Review cycle** | Quarterly (next review: 2026-10-16) |

## 1. Purpose

Give operations staff one repeatable procedure for the insurance commission lifecycle:
recording what the firm **expects** to earn, posting what carriers actually **pay**,
reconciling the two, resolving variances, and reading the revenue rollup. This is an
operational/financial procedure — the platform records and reconciles money; it makes no
regulatory judgement.

## 2. Scope

**In scope:** expected-commission entry (including split/override crediting), received-payment
posting, carrier-statement import and reconciliation, commission exceptions
(variance / outstanding), and the commission revenue rollup.

**Out of scope (do not attempt in this procedure):** any suitability, replacement/1035,
licensing, or continuing-education determination; deciding whether a producer may be paid as a
matter of compliance; chargeback economics beyond a simple write-off. Regulated matters are
**blocked (AD-5)** until a qualified compliance reviewer is named.

## 3. Roles & access

| Task | Capability required | Typical role |
|---|---|---|
| View ledger, statements, revenue rollup | `insurance.commissions.read` | insurance_agent, insurance_operations, insurance_compliance, administrator |
| Record/generate expected, post received, adjust/reverse/chargeback, import & reconcile statements, write off | `insurance.commissions.write` | insurance_agent, insurance_operations, administrator |

Ledger entries are **record-scoped to the policy** (household / person / organization): staff
see and act on commissions only for policies within their book. Carrier statements are
firm-internal documents.

## 4. Prerequisites

1. The policy exists and its producers are attached with **split percentages**
   (`/insurance/policies/{id}` → Producers). Overrides are attached with the `override` role.
2. You know the commission **basis** (the dollar amount to split) and the **schedule**
   (`first_year`, `renewal`, `trail`, or `override`).

## 5. Procedure

### 5.1 Create the expected commission

**Preferred — generate from splits (credits every producer automatically):**
1. Go to the policy → **Commissions → Generate**.
2. Enter the **basis amount**, **schedule**, and (optionally) a **period label** and
   **due date**.
3. Submit. The system writes one expected entry per active producer, crediting each by their
   split percentage; an `override` producer is credited to the upline entity. The basis is
   fully distributed across the producers.

**Alternative — single manual entry:** use **Commissions → Add expected** when you need one
entry for one producer (enter producer, expected amount, schedule).

### 5.2 Post a received payment

When a carrier pays: open the entry → **Record received** → enter the received amount. The
entry's status recomputes automatically:

| Result | Status |
|---|---|
| Received within $0.01 of expected | **received** (cleanly reconciled) |
| Received less than expected | **partial** (under-payment — a variance) |
| Received more than expected | **variance** (over-payment) |
| Uncollectible expected | **written_off** (use **Write off**) |

**Corrections — adjustment / reversal / chargeback.** Use **Adjust** to apply a signed
correction to an entry's net received amount, choosing the **kind**:
- **adjustment** — a true-up (±) for a miscounted payment.
- **reversal** — back out a payment posted in error (negative).
- **chargeback** — a carrier claws paid commission back (negative).

The entry's net received and status recompute automatically, and the correction flows
straight through to the revenue rollup. Every correction is recorded in the audit trail with
its kind and reason.

### 5.3 Import & reconcile a carrier statement

1. **Statements → Import.** Select the carrier, statement date, and reference; add one line
   per payment (policy number, schedule, amount).
2. **Reconcile.** Use **Reconcile statement** to auto-match every line to an outstanding
   expected entry (matched by policy + schedule, oldest expected first) and post the line
   amount as received. Unmatched lines are reported for manual follow-up; match them with
   **Reconcile line** and choose the correct ledger entry.
3. The statement rolls up to **reconciled**, **partially_reconciled**, or stays **imported**.

### 5.4 Work commission exceptions

Run **Commissions → Scan** (or wait for the scheduled scan once Phase 6 wires it live). The
shared exception queue then shows:

- **`INS_COMMISSION_VARIANCE`** — a reconciled entry whose payment differs from expected.
  Investigate (carrier underpayment, wrong basis, wrong split), correct the ledger, and the
  exception auto-resolves on the next scan.
- **`INS_COMMISSION_OUTSTANDING`** — an expected entry past its due date with nothing received.
  Follow up with the carrier; posting the payment (or writing it off) auto-resolves it.

The scan is **idempotent** — running it repeatedly never creates duplicate exceptions. These
exceptions are **firm-internal**: they appear only in the operations and oversight exception
queues and are **never** published to the client's Timeline. Commission and compensation
information is strictly internal and not client-facing.

### 5.5 Read the revenue rollup

**Commissions → Report** displays expected, received, outstanding, and variance totals, with
breakdowns by schedule, organization, and producer. It also reports **producer payouts**
(individual producers) and **agency-retained revenue** (agency, broker-of-record, and override
compensation) under the **`insurance_commissions`** revenue category.

Every figure is derived directly from the canonical commission ledger, which serves as the
single source of truth. The report is idempotent, cannot double-count transactions, reflects
adjustments, reversals, chargebacks, and write-offs automatically, and contains no compliance
determinations.

## 6. Controls & audit

- Every mutation writes an immutable audit event (`insurance.commission.*`).
- Reconciliation is deterministic and re-runnable; the scan is idempotent and auto-resolving.
- Amounts reconcile to the cent (a $0.01 tolerance separates "clean" from "variance").

## 7. Compliance boundary (AD-5)

This procedure moves and reconciles money only. It does **not** determine suitability, approve
replacements/1035s, validate licensing/CE, or block any policy action. Those are **regulated**
and remain blocked under **AD-5** until a qualified, named compliance reviewer and an approved
sign-off are in place. If a task seems to require such a judgement, **stop and escalate** — do
not record a compliance conclusion here.
