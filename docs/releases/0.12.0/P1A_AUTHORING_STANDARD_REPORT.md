# Release 0.12.0 — Phase P1A Report: Documentation Authoring Standard

_Editorial standard created before substantive authoring (sequencing adjustment per approval).
Branch `release/0.12.0`, 2026-07-17. **No Confluence changes; no legacy pages reconciled/archived;
nothing published.** D1–D10 and A1–A4 unchanged; AD-5 unresolved; `v0.11.0` immutable._

## 1. Deliverable created

- **`docs/standards/DOCUMENTATION_AUTHORING_STANDARD.md`** — the editorial/operational standard every
  Release 0.12 authored/adapted page must follow.
- **`docs/releases/0.12.0/P1A_AUTHORING_STANDARD_REPORT.md`** — this report.

The standard is **editorial/procedural only**. It **does not** redesign the information architecture or
templates, and **does not** change any decision.

## 2. What the standard defines

All required elements are covered (standard §§1–26): front matter & metadata (§1); canonical
identifier (§2); title/naming (§3); purpose & scope (§4); audience (§5); owner & responsible role (§6);
prerequisites (§7); required systems & permissions (§8); step format (§9); expected results (§10);
validation & evidence (§11); warnings/cautions/compliance notices (§12); troubleshooting format (§13);
escalation (§14); related/cross-links (§15); screenshot/visual rules (§16); sensitive-data &
credential restrictions (§17); source provenance (§18); SME verification status (§19); last/next review
(§20); revision history (§21); publication-readiness (§22); `draft`/`needs_review`/`published` criteria
(§23); the 10 legacy-adaptation rules (§24); the per-document source assessment (§25); and an assembled
page skeleton (§26).

## 3. Legacy-content rules — captured verbatim (standard §24)

All 10 rules are stated: (1) Atlas is **evidence, not automatic truth**; (2) **preserve** original
Atlas id/title/link in provenance; (3) **do not copy obsolete configs** as current; (4) **flag**
statements needing SME confirmation; (5) **do not invent** procedures/configs/ownership/RTO-RPO/
escalation; (6) use **`SME CONFIRMATION REQUIRED`** placeholders; (7) **never** include
secrets/keys/tokens/PII; (8) **AD-5 material stays gated & unpublished**; (9) each replacement
**identifies the legacy page(s) it supersedes** (`supersedes`); (10) **no legacy page archived until
its replacement passes quality review and is approved**.

## 4. Source assessment (standard §25)

Every adapted page requires a source assessment: source page · source identifier · source status ·
suspected age · current-system applicability · duplication · contradictions · facts verified · facts
awaiting confirmation · disposition recommendation.

## 5. Validation

| Validated against | Result |
|---|---|
| **0.11 framework** (`documentation-framework/`) | ✅ Complements it; the standard's structure/audience/canonical-home rules follow framework 01/02/06; no IA change |
| **Existing templates** (Area Shell templates; `02`) | ✅ Front matter is the framework shared model **+ additive** provenance/verification fields; templates unchanged |
| **Publication Register taxonomy** (`pages.yml`) | ✅ `page_id`/`area`/`doc_type`/`status`/`canonical_source` use the register's controlled vocabularies (26 + `SHARED` + `GOV`, never `MANUAL`); the register row stays the canonical-home contract |
| **Advisory DoD** (`check_documentation_dod.py`) | ✅ Publication-readiness (§22) maps to DoD checks (front matter, secrets, links, ownership, status, AD-5 invariant); standard stays **advisory** (D6) |
| **D1–D10** | ✅ **Unchanged.** D1 register-canonical honored; D4 status enum reused; D9 AD-5 invariant enforced (§12/§23/§24.8); D10 taxonomy respected |
| **A1–A4** | ✅ **Unchanged & operationalized.** A1 lifecycle (author→review→validate); A2 verified-facts-only / scaffold-not-fabricate (§19/§24.4–6); A3 reconcile-after-replacement (§24.10); A4 editorial-not-structural (this whole standard) |
| **AD-5 restrictions** | ✅ Regulated insurance rule sets stay gated & unpublished (§12/§24.8); no regulated content authored; reviewer remains UNFILLED; business owner ≠ regulatory certification |

## 6. Canonical-source note (git-authored → published)

Per the authoring-first lifecycle, Release 0.12 pages are **authored as Git markdown** (version-
controlled, PR-reviewed, DoD-checked, provenance-bearing); the **register row's `canonical_source`
remains the authoritative contract** for each page's final home, and **publication (P6)** renders
publish-eligible git-canonical rows to Confluence. This is a workflow choice, not a change to D1 or the
framework's canonical-home rules — the register decides the home; the standard governs how the page is
written and reviewed.

## 7. Deviations

- **New directory `docs/standards/`** for the standard (per the instructed path). It is a standards
  location, not an IA/template change; the framework's `01 · How This Manual Works` node references it
  at publication time. No other deviation.

## 8. Unresolved issues (non-blocking)

- The precise **Git home** for each authored operational page (e.g. `governance/` for
  policy/runbook/DR/controls vs a `docs/operations-manual/<area>/` working tree for SOP/guide types) is
  set by the **register row's `repository_path`** and confirmed at P1B — not mandated by the standard
  (keeps it editorial).
- **SME verification path** for infrastructure facts (Batch 3) remains an external dependency (A2).

## 9. Recommendation for P1B

1. P1A is **complete**: the Authoring Standard exists and validates against the framework, templates,
   register, DoD, D1–D10, A1–A4, and AD-5 — editorial only, no IA/template redesign.
2. **Proceed to P1B (first authoring batch)** as proposed — **Schwab Operations, Tax Operations
   (TaxDome + Drake), AssetMark Operations** — adapting **verified** legacy content into git-canonical
   Operations Manual pages under this standard, preserving provenance and marking every unverified fact
   `SME CONFIRMATION REQUIRED`.
3. Before P1B: confirm the **register `repository_path` home** for the batch pages and the **SME
   confirmation channel** for any facts the legacy content cannot self-verify.

---

**Stopping after the Authoring Standard and this P1A report.** No authoring begun. Awaiting explicit
approval before beginning **Phase P1B**.
