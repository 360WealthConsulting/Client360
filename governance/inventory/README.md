---
title: "Governance — Asset & Configuration Inventory (skeleton)"
area: "GOV"
profile: infrastructure
doc_type: "ASSET"
canonical_source: git
owner: "Michael Shelton (business owner)"
reviewer: "UNFILLED"
status: planned
last_reviewed: "TBD"
review_cycle: quarterly
next_review: "TBD"
compliance_gate: "none"
---

# `inventory/` — Asset & Configuration Inventory

> **Phase-A skeleton (Release 0.11.0 · P2).** Structure and guidance only. **No asset record,
> configuration value, license, or inventory entry is authored here yet.** Inventory authoring is
> roadmap **Phase B**.

## Purpose

Holds the Git-canonical `ASSET` inventory (CMDB-lite): systems, servers, network devices, AD/M365
tenants, endpoints; owner & lifecycle; licenses & renewals; dependencies; configuration source/link.
Prefer **generation from config** where possible.

## Permitted artifact types

`ASSET` only. Vendor/contract facts are the Vendor Register (node 90); DR is `dr/`.

## Required metadata

Per `../CONTRIBUTING.md`. Placeholders: **owner** = Michael Shelton (business), **reviewer** =
`UNFILLED`, **status** = `planned`, **review_cycle** = `quarterly`, dates = `TBD`.

## Naming examples

`servers.md`, `network-devices.md`, `m365-tenant.md` (or generated exports).

## Canonical-source guidance

Git-canonical or an authoritative system export; render into Confluence & the Asset Register.
**No secrets, credentials, IPs-as-secrets, or client data** — reference authoritative systems by
name.

## Framework standards

`02-DOCUMENT-TYPE-TEMPLATES.md` (Asset & Config Inventory type),
`01-INFORMATION-ARCHITECTURE.md` (Registers node 90), `05-IMPLEMENTATION-ROADMAP.md` (Phase B).
