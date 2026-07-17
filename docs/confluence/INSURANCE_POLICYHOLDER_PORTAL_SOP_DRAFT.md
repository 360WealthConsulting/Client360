# Insurance Policyholder Portal — Standard Operating Procedure (DRAFT)

> **Status: DRAFT — not published.** Staged for the 360OS Operations Manual (Confluence, space
> `3WCO`, section **🛡️ Insurance Operations**, cross-listed under **🤝 Client Experience**). Do
> **not** publish until Release 0.10.0 Phase 7 is RC-validated and the page owner approves.
> Documents only current, tested, non-regulated functionality. Contains **no** suitability,
> replacement/1035, or licensing determination — those remain behind the **AD-5** gate.

| Field | Value |
|---|---|
| **Proposed page title** | Insurance Policyholder Portal — Operating Procedure |
| **Proposed 360OS Id** | `INS-SOP-07` (pending page-owner registry) |
| **Manual section** | 🛡️ Insurance Operations (cross-listed: 🤝 Client Experience) |
| **Owner** | Michael Shelton |
| **Status** | Draft (unpublished) |
| **GitHub source of truth** | `app/services/insurance_portal.py`; portal routes (`app/routes/portal.py`); reuses `app/portal/service.py` + `docs/CLIENT_PORTAL.md` |
| **Applicable release** | v0.10.0 · Phase 7 |
| **Publication gate** | Phase 7 RC-validated **and** page-owner approval; regulated content excluded (AD-5) |
| **Review cycle** | Quarterly (next review: 2026-10-16) |

## 1. Purpose

Give staff the procedure for granting policyholders a **read-only view of their own insurance
policies** in the client portal. The insurance surface **reuses the existing client portal** —
same login, session, grants, and security — so there is nothing new for a client to learn.

## 2. Scope

**In scope:** granting a policyholder access to their policy list/detail, and what they see.

**Out of scope:** the portal shows **factual policy information only**. It never shows producers,
commissions or any compensation, licensing, internal notes, or **exceptions/action items** —
insurance exceptions are firm-internal and never client-facing. It makes no
suitability/replacement determination (AD-5).

## 3. What the policyholder sees

For each of **their own** policies (opt-in — see §4): carrier, product, policy number, status,
issue date, face amount, premium (amount + mode), coverages, riders, and their own
owner/insured/beneficiary designations. Nothing else. A policy the client does not own is
**invisible** (the system returns "not found" rather than revealing it exists).

## 4. Procedure — grant a policyholder access

1. Ensure the client has (or is invited to) a **portal account** for their person/household —
   this is the standard client-portal invite; no separate insurance login.
2. On their **portal access grant**, enable the **`insurance`** permission (alongside the usual
   messages/documents). Access is **opt-in**: without this permission the client sees no policies.
3. The client signs in to the portal and opens **My Policies** (`/portal/insurance`); their
   policies appear automatically, scoped to the person/household (and organization, for
   business-owned policies) their grant covers.

To remove access, disable the `insurance` permission (or deactivate the grant) — the policies
disappear immediately on the next request.

## 5. Boundaries & privacy

- **Read-only.** The portal never lets a client edit a policy, see internal financials, or see
  another household's records.
- **No compensation, ever.** Commission/producer/split data is firm-internal and is not part of
  this surface.
- **No exceptions/action items.** Insurance exceptions (reviews, licensing, commissions) are
  firm-internal operational items and never appear on any client surface.

## 6. Controls & audit

- Access is governed entirely by the existing portal grants (auditable) — no insurance-specific
  access model.
- Out-of-scope requests return 404 (existence is never disclosed); unauthenticated requests 401.

## 7. Compliance boundary (AD-5)

This surface presents factual policy data only. It performs **no** suitability, replacement/1035,
or licensing determination and gives no advice. Those are **regulated** and remain blocked under
**AD-5** until a qualified, named compliance reviewer and an approved sign-off are in place.
