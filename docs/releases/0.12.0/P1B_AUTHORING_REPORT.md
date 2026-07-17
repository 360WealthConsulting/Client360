# Release 0.12.0 — Phase P1B Report: Client-Platform Operations (First Authoring Batch)

_First authoring/adaptation batch. Branch `release/0.12.0`, 2026-07-17. **No Confluence changes; no
legacy pages reconciled/archived/moved/deleted; nothing published; no app/migration change; no AD-5
content.** D1–D10 and A1–A4 unchanged; `v0.11.0` immutable. All authored pages follow the approved
Documentation Authoring Standard and are `needs_review`._

## 1. Documents created (git-canonical, `needs_review`)

| page_id | Title | Path | Adapted from (Atlas) |
|---|---|---|---|
| `WLTH-SOP-01` | Schwab Account Opening | `docs/operations-manual/wealth/schwab-account-opening.md` | SOP-006 (`24772609`) |
| `WLTH-SOP-02` | Schwab Portfolio Connect Quarterly Billing & Fee Locking | `docs/operations-manual/wealth/schwab-portfolio-connect-billing.md` | SOP-009 (`24870913`) + LL-001 (`24674305`) |
| `WLTH-SOP-03` | AssetMark Account Opening | `docs/operations-manual/wealth/assetmark-account-opening.md` | SOP-013 (`24838166`) |
| `WLTH-SOP-04` | AssetMark Proposal Generation | `docs/operations-manual/wealth/assetmark-proposal-generation.md` | SOP-011 (`25133057`) |
| `TAXOPS-SOP-01` | TaxDome Client Intake | `docs/operations-manual/tax/taxdome-intake.md` | SOP-016 (`23920691`) |
| `TAXOPS-SOP-02` | 1040 Individual Return Preparation (Drake) | `docs/operations-manual/tax/tax-1040-return-workflow.md` | SOP-017 (`23920712`) |

**6 pages** — a focused, high-quality anchor batch adapted from **source content actually read**
(not from titles). Each carries full front matter, provenance, a source assessment, expected results,
validation/evidence, troubleshooting, escalation, related links, and inline `SME CONFIRMATION REQUIRED`
markers. The Atlas-specific "AI Metadata" cruft was dropped; policy/checklist content was split out
(recorded as related/queued, not merged into the SOP).

## 2. Source pages used (read in full, read-only)

Schwab: SOP-006 (`24772609`), SOP-009 (`24870913`). Tax: SOP-016 (`23920691`), SOP-017 (`23920712`).
AssetMark: SOP-013 (`24838166`), SOP-011 (`25133057`). Page **titles/inventory** also gathered for the
full CAP-002/003/004 trees (Schwab ~10, AssetMark ~14, Tax ~22) for queue planning.

## 3. Register rows used + proposed gaps (resolved)

- **Register homes confirmed:** WLTH and TAXOPS exist (hybrid, 27 doc types each) with **generic
  coverage rows only** (`WLTH-SOP`, `TAXOPS-SOP`, …) — **no numbered instances existed** (a gap).
- **Smallest compliant additions (justified & applied):** 6 **instance** rows (`WLTH-SOP-01..04`,
  `TAXOPS-SOP-01/02`) — same areas, same `SOP` doc type, `canonical_source: git`, real
  `repository_path`, `status: needs_review`, `confluence_page_id: TBD`. **No new area/type/taxonomy**
  was introduced; no coverage row was duplicated (instances ≠ the generic `WLTH-SOP` row). Register is
  now **560 rows** (was 554). Added via the register bootstrap and regenerated deterministically.
- **New content home:** `docs/operations-manual/{wealth,tax}/` — a git content directory for
  git-canonical operational pages (per P1B "Git-canonical Operations Manual documentation"); not an IA
  or Confluence change.

## 4. Duplicate / contradictory sources discovered

- **Split, not duplicate:** each source SOP embeds policy + checklist material (e.g. SOP-009 overlaps
  POL-004 + CHK-007; SOP-006 overlaps CHK-004). These are recorded for **splitting** into policy/
  checklist pages (queued), not merged into the SOP.
- **Software vs operational (one-home rule):** Git `SCHWAB_PORTFOLIO_ENGINE.md` / `EPIC_5_TAX_*` are the
  **software** facet; the new pages are the **operational** facet — linked, not duplicated.
- **No contradictions** were found within the six sources.

## 5. Pages left in `needs_review`

**All 6** — none is `published` (P1B rule 4). Each has open `SME CONFIRMATION REQUIRED` items (see the
SME register).

## 6. SME questions generated

**15 consolidated questions** in `docs/releases/0.12.0/P1B_SME_REVIEW_REGISTER.md` (per page: current
platform/tooling, approval/compliance authority, billing-calendar dates, organizer templates, Drake
deployment, live-integration status, disclosures, fee schedules). Each has a recommended reviewer,
operational effect, and priority.

## 7. AD-5 exclusions

**None authored.** No suitability, replacement/1035, licensing, or CE rule sets. No AD-5 subject
appears in Client-Platform Operations. Compliance-sensitive touchpoints (e.g. proposal disclosures) are
flagged for business/operational SME confirmation — **not** regulatory certification, and not AD-5.

## 8. Validation results

| Check | Result |
|---|---|
| One canonical home per document | ✅ each row `canonical_source: git` with a unique `repository_path` |
| No duplicate semantic IDs | ✅ `page_id` unique (validator) |
| Repository paths match the register | ✅ front-matter `git_source` == register `repository_path` |
| Taxonomy valid | ✅ WLTH/TAXOPS + `SOP` (no new taxonomy; no `MANUAL`) |
| Provenance complete | ✅ every page has `source_*` + `supersedes` + source assessment |
| Unresolved facts visibly marked | ✅ `SME CONFIRMATION REQUIRED` inline; consolidated in the SME register |
| No document marked `published` | ✅ all 6 `needs_review` |
| No AD-5 content | ✅ none |
| No secrets / client PII | ✅ DoD strict clean; procedures reference data by name only |
| No Confluence changes | ✅ read-only reads only |
| No legacy page reconciled/archived | ✅ none touched |
| D1–D10 / A1–A4 unchanged; `v0.11.0` immutable | ✅ |
| Register validator + crosswalk `--check` | ✅ 560 rows OK; crosswalk current |
| DoD `--strict` (whole repo) | ✅ 0 errors / 0 warnings |

## 9. Remaining work (queued, not done)

- **Schwab:** MoneyLink (SOP-007), ACAT (SOP-008), checklists CHK-004–007, policies POL-003/004.
- **Tax:** Business return (SOP-018), Review & Delivery (SOP-019), **E-file & Acknowledgements**
  (SOP-020), IRS Notice (SOP-021), Extensions (SOP-023), Estimates (SOP-024); checklists CHK-013–020;
  policies POL-007–010.
- **AssetMark:** Household (SOP-010), Model Selection (SOP-012), Funding & Transfers (SOP-014), Billing
  (SOP-015); checklists CHK-008–012; policies POL-005/006.
- Split policy/checklist pages from the adapted SOPs; author the area **Policy** pages (WLTH-POL-01,
  TAXOPS-POL-01) and Overview/Purpose.

## 10. Recommendation for the next batch

1. **P1B is a complete first anchor batch** — 6 faithfully adapted, provenance-bearing, standard-
   compliant pages; 15 SME questions consolidated; register/DoD green; nothing published or reconciled.
2. **Do not proceed to SME-driven corrections, reconciliation, Confluence changes, or the next authoring
   batch without approval** (per the stop instruction).
3. **Recommended next step:** route the **SME Review Register** to the operational SME (Michael
   Shelton) to resolve the High-priority items (current platforms, Portfolio Connect billing tool,
   Drake deployment, live-integration status). On resolution, complete the Client-Platform tier
   (remaining Schwab/Tax/AssetMark pages + policies/checklists), then P3 quality review before any
   `published` status or P4 reconciliation.

---

**Stopping after this P1B batch and the consolidated SME Review Register.** Awaiting explicit approval
before any SME-driven corrections, legacy reconciliation, Confluence changes, or subsequent authoring
batch.
