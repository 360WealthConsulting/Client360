# Insurance Reporting & Operations Dashboard — Standard Operating Procedure (DRAFT)

> **Status: DRAFT — not published.** Staged for the 360OS Operations Manual (Confluence, space
> `3WCO`, section **🛡️ Insurance Operations**, cross-listed under **📊 Reporting**). Do **not**
> publish until Release 0.10.0 Phase 8 is RC-validated and the page owner approves. Documents only
> current, tested, non-regulated functionality. Contains **no** suitability, replacement/1035, or
> licensing determination — those remain behind the **AD-5** gate.

| Field | Value |
|---|---|
| **Proposed page title** | Insurance Reporting & Operations Dashboard — Operating Procedure |
| **Proposed 360OS Id** | `INS-SOP-08` (pending page-owner registry) |
| **Manual section** | 🛡️ Insurance Operations (cross-listed: 📊 Reporting) |
| **Owner** | Michael Shelton |
| **Status** | Draft (unpublished) |
| **GitHub source of truth** | `app/services/insurance_reporting.py`; routes (`app/routes/insurance.py`); reuses `exception_engine.list_exceptions`, Work Management `work_items`, and portal grants |
| **Applicable release** | v0.10.0 · Phase 8 |
| **Publication gate** | Phase 8 RC-validated **and** page-owner approval; regulated content excluded (AD-5) |
| **Review cycle** | Quarterly (next review: 2026-10-16) |

## 1. Purpose

Give staff the procedure for reading the **Insurance Operations Dashboard** — one firm-internal
view of the insurance book's operational health. It **reuses** the firm's reporting,
authorization, and record-scope patterns; nothing here is a new reporting system.

## 2. Scope

**In scope:** the consolidated dashboard and its drill-down reports (pipeline, reviews,
commissions, licensing, exceptions, work queues, portal adoption).

**Out of scope:** this is a **staff** surface — it is **not** shown to policyholders. It presents
**operational counts, workflow status, and financial reconciliation only**. It makes no
suitability, replacement/1035, or licensing determination and shows no compliance metrics (AD-5).

## 3. Who sees what (proportional)

The dashboard shows **only the sections your role permits**, and every figure is limited to the
records you may see (your book; oversight roles see firm-wide):

| Section | Requires capability |
|---|---|
| Pipeline (policies/cases) · Reviews | `insurance.read` |
| Commissions (financial) | `insurance.commissions.read` |
| Licensing & CE | `insurance.licensing.read` |
| Exceptions | `exception.read` |
| Work queues | `work.read` |
| Policyholder portal adoption | `record.read_all` (oversight) |

If you lack a capability, that section simply does not appear — the dashboard lists which
sections it included.

## 4. Procedure — read the dashboard

1. Open **Insurance → Dashboard** (`/insurance/dashboard`).
2. **Pipeline** — how many policies/cases are in flight and how many requirements are open.
3. **Reviews** — in-force review completion rate and overdue count; work overdue reviews from the
   Reviews queue.
4. **Exceptions / Work queues** — current operational exceptions by type and the depth of each
   insurance work queue; claim and work items from the queues.
5. **Commissions** (if permitted) — expected vs received, outstanding, and variance; drill into
   the Commissions report to reconcile.
6. **Licensing & CE** (if permitted) — record counts and upcoming expiries; follow up on expiring
   licenses.
7. **Portal adoption** (oversight) — how many policyholders have portal access to their policies.

## 5. Boundaries & privacy

- **Staff-only.** The dashboard is never part of the policyholder portal; producer compensation,
  commissions, licensing, exceptions, and queue internals are shown only to staff who hold the
  relevant capability.
- **Scoped.** Figures reflect your record scope; you never see another book's data.
- **No compliance conclusions.** Numbers are operational; they do not assert suitability,
  replacement/1035, or licensing/CE determinations.

## 6. Compliance boundary (AD-5)

This dashboard reports operational, workflow, and financial data only. It performs **no**
regulated determination and shows **no** compliance metric. Those are **regulated** and remain
blocked under **AD-5** until a qualified, named compliance reviewer and an approved sign-off are
in place.
