# Insurance Integrations — Extension Points (Reference) — DRAFT

> **Status: DRAFT — not published.** Staged for the 360OS Operations Manual (Confluence, space
> `3WCO`, section **🛡️ Insurance Operations**, cross-listed under **💻 Technology &
> Cybersecurity**). Do **not** publish until Release 0.10.0 Phase 9 is RC-validated and the page
> owner approves. This is a **reference** page — there is **no staff procedure**, because every
> integration port is **disabled** and there is nothing to operate. It describes future extension
> points only. No suitability, replacement/1035, or licensing determination — AD-5.

| Field | Value |
|---|---|
| **Proposed page title** | Insurance Integrations — Extension Points (Reference) |
| **Proposed 360OS Id** | `INS-REF-09` (pending page-owner registry) |
| **Manual section** | 🛡️ Insurance Operations (cross-listed: 💻 Technology & Cybersecurity) |
| **Owner** | Michael Shelton |
| **Status** | Draft (unpublished) — reference |
| **GitHub source of truth** | `app/services/insurance_integrations.py`; `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md` §12f |
| **Applicable release** | v0.10.0 · Phase 9 |
| **Publication gate** | Phase 9 RC-validated **and** page-owner approval |
| **Review cycle** | Semiannual (next review: 2027-01-16) |

## 1. Purpose

Record the **extension points** where future insurance integrations will plug in, so the firm has
a clear, documented map — **without** turning anything on. Every port is a **disabled stub**: no
vendor is connected, no data is exchanged, and no automation runs.

## 2. The ports (all disabled)

| Port | Direction | Future use |
|---|---|---|
| Carrier policy & in-force data feed | Inbound | Receive policy/in-force data from carriers |
| Application / case-status feed | Inbound | Receive new-business application/case status |
| Commission-statement feed | Inbound | Automated carrier commission statements (machine twin of today's manual import) |
| Licensing / appointment feed | Inbound | Producer licensing & appointment data |
| Document / evidence intake | Inbound | Inbound documents / evidence |
| Operational export hook | Outbound | Export operational data to an external system |

Each currently reports **disabled / not connected**.

## 3. What "disabled" means here

- **Nothing runs.** No network calls, file transfers, logins, polling, or vendor APIs.
- **Configuration cannot turn a port on.** A port becomes active only through an explicit
  engineering implementation and enablement decision in a future release — never because a setting
  or value exists.
- **No credentials or endpoints exist.** No secrets, tokens, certificates, or URLs are stored.
- **No scheduled jobs.** Nothing is polling or syncing on a timer.
- **Auditable.** Any attempt to invoke a port is recorded (who tried, which port, disabled
  outcome) — with no payload or secret ever logged.

## 4. How a port will be activated (future)

When a real vendor contract, credentials, and compliance review exist, engineering will implement
a concrete adapter for that one port and enable it as its own release. Activation will:
- keep Client360 the **canonical source** of record;
- enforce the same organization / record scope as the rest of the domain;
- be **idempotent** and safe to retry, and **quarantine** unprocessable records via the shared
  exception/work-queue rather than dropping them;
- store secrets in the platform secret store — never in code, logs, or audit trails.

## 5. Compliance boundary (AD-5)

These are transport extension points only. No port performs or enables suitability,
replacement/1035, licensing validation, sale/issue blocking, or compliance approval. Any live
integration carrying regulated data remains blocked under **AD-5** until a qualified, named
compliance reviewer and an approved sign-off are in place.
