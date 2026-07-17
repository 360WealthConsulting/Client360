# Documentation Authoring Standard

_The editorial and operational standard every Release 0.12 **authored or adapted** Operations Manual
page must follow. **Editorial/procedural only** — it does **not** change the information architecture,
the templates, decisions **D1–D10**, or decisions **A1–A4**. It complements the framework
(`docs/documentation-framework/`) and the register (`docs/registers/pages.yml`); where they conflict,
the framework and register govern structure and the register's `canonical_source` is the contract._

Status: **PROPOSED** (Release 0.12 · P1A). No Confluence changes; no legacy pages reconciled/archived;
nothing published; AD-5 unresolved.

---

## 1. Required front matter & metadata

Every authored/adapted page carries YAML front matter = the framework shared model (framework `02`)
**plus additive provenance/verification fields** (editorial; no schema change to the register):

```yaml
---
# --- framework shared model (02) ---
title: "<Area> — <Document Type>[: <Specific>]"
page_id: "<AREA>-<TYPE>[-nn]"          # canonical identifier (see §2)
area: "<area code>"                     # one of the 26 + SHARED + GOV (never MANUAL)
profile: software|infrastructure|operations
doc_type: "<TYPE code>"                 # from 02 (SOP, POLICY, RUNBOOK, CHECKLIST, ...)
canonical_source: git|confluence        # the register row is authoritative
git_source: "<repo path> | n/a"
confluence_page_id: "<id | TBD>"
owner: "<accountable role/name>"
reviewer: "<independent reviewer | UNFILLED>"
status: planned|draft|published|needs_review   # (D4 enum; criteria §23)
effective_or_release: "vX.Y.Z | YYYY-MM-DD"
last_reviewed: "YYYY-MM-DD | TBD"
review_cycle: per_release|quarterly|semiannual|annual
next_review: "YYYY-MM-DD | TBD"
related: ["<page_id>", "..."]
compliance_gate: "none | AD-5"          # gated => never published (D9)
# --- additive provenance / verification (editorial) ---
source_system: "360os_atlas | none"
source_page_id: "<legacy Confluence id | n/a>"
source_title: "<legacy page title | n/a>"
source_link: "<legacy page URL | n/a>"
source_status: "current | draft | archived | unknown"
supersedes: ["<legacy id/page_id>", "..."]   # what this replacement supersedes (§ legacy rule 9)
sme_verification: "verified | partial | unverified"
sme_verified_by: "<role/name | UNCONFIRMED>"
provenance_notes: "<short note>"
---
```

Unknown values use visible placeholders (`TBD`, `UNFILLED`, `UNCONFIRMED`, `SME CONFIRMATION
REQUIRED`) — **never invented values** (A2).

## 2. Canonical document identifier

`page_id = <AREA>-<TYPE>[-nn]` (framework IA §3), e.g. `SEC-SOP-01`, `DR-BCDR`, `M365-ADMINGUIDE-01`.
Area/type codes are position-scoped and disjoint. The `page_id` is stable and unique across the
register; it is the canonical identifier for cross-links and provenance.

## 3. Title & naming conventions

- Page title: `"<Area> — <Document Type>[: <specific subject>]"` (e.g. `"Microsoft 365 — SOP: Tenant
  Administration"`).
- File name (Git): lowercase, hyphenated, matching the `page_id` intent (e.g. `m365-admin.md`).
- One doc_type per page; do not merge two types onto one page (framework 02 §C2).

## 4. Purpose & scope
A 1–3 sentence **Purpose** (why the page exists) and an explicit **Scope** (what is and is not
covered), at the top of the body. State out-of-scope explicitly.

## 5. Intended audience
Name the role(s) the page is written for (e.g., "IT operator", "tax preparer", "client-service
associate"). Assume that audience's baseline; do not re-explain firm-wide basics.

## 6. Owner & responsible role
Every page names an **accountable owner** (role first, then name) and an **independent reviewer**
(reviewer ≠ author where possible). Michael Shelton may be the **business owner** — that is
operational authority only, **never** regulatory certification (A-series / AD-5).

## 7. Prerequisites
List what must be true/complete before the procedure runs (state, access, prior steps, inputs).

## 8. Required systems & permissions
List the systems, roles, and permissions needed (e.g., "M365 Global Admin", "SonicWall admin",
"Schwab Advisor Center access"). **Reference credentials/secrets by name only** (§17) — never values.

## 9. Step-by-step procedure format
Numbered, imperative, one action per step. Each step: the action, the target system/screen, and any
decision branch. Prefer text; use visuals only per §16. Long procedures use titled sub-sections.

## 10. Expected results
State the observable outcome of the procedure (and of key steps): what the operator should see when it
worked.

## 11. Validation & evidence requirements
Define how completion is verified (a check, a confirmation screen, a record) and what **evidence** is
retained (link to a system record/document by reference — never paste sensitive evidence inline).

## 12. Warnings, cautions & compliance notices
Use consistent callouts:
- `> ⚠️ WARNING` — risk of data loss, outage, or irreversible action.
- `> ⚠️ CAUTION` — easy-to-make error or precondition.
- `> ⚖️ COMPLIANCE` — a regulatory/compliance-sensitive step. If the topic is **AD-5-gated**
  (suitability, replacement/1035, licensing/CE), the page is **gated and unpublished** (§ legacy rule
  8, D9) — do not author the regulated rule set.

## 13. Troubleshooting format
A `## Troubleshooting` section as a table: **Symptom | Likely cause | Resolution | Escalate if**.
Keep entries specific and verified.

## 14. Escalation path
State who to escalate to and when (role first). Where the real contact is unknown, use
`SME CONFIRMATION REQUIRED` — never invent an escalation contact (A2 / legacy rule 5).

## 15. Related documents & cross-links
A `## Related` section linking by `page_id` (and relative path for Git). Link the **software** facet
(Git architecture) from the **operational** page and vice-versa — one canonical home per page, linked
not copied.

## 16. Screenshots & visual evidence rules
- Prefer **text**; add a screenshot only when a UI is genuinely ambiguous in prose.
- Screenshots must contain **no** client PII, account numbers, balances, credentials, tokens, or
  internal IPs/serials. Redact before inclusion.
- Store images as repo assets (or Confluence attachments at publish); never embed remote/external
  images. Caption every image.

## 17. Sensitive-data & credential restrictions
**Never** include passwords, private keys, access tokens, API keys, secrets, client PII, SSNs, account
numbers, or any secret value. Reference them by name/location (secret store, system of record). The
advisory DoD checker's secret scan is a backstop, not a license.

## 18. Source provenance
For any **adapted** page, record `source_system/source_page_id/source_title/source_link/source_status`
and `supersedes`. Provenance is mandatory: a reader must be able to trace an adapted page to the
legacy Atlas page it came from (legacy rules 2 & 9).

## 19. SME verification status
`sme_verification: verified | partial | unverified`, plus `sme_verified_by`. Any unverified fact in the
body is flagged inline with **`SME CONFIRMATION REQUIRED`**. A page with open `SME CONFIRMATION
REQUIRED` items **cannot** be `published` (§23).

## 20. Last-reviewed & next-review dates
`last_reviewed` and `next_review` per `review_cycle`. Unknown → `TBD`. Continuity/security pages review
at least semiannually; policies at least annually.

## 21. Revision history
A `## Revision history` table: **Version | Date | Author | Change**. First adapted version notes the
source page and "adapted from Atlas <id>".

## 22. Publication-readiness criteria
A page is publication-ready only when **all** hold:
1. Front matter complete and valid (§1); `page_id` unique; `area` valid; `status` valid.
2. **No open `SME CONFIRMATION REQUIRED`** placeholders; `sme_verification: verified`.
3. Owner **and** reviewer assigned (reviewer ≠ author where possible).
4. Provenance complete for adapted pages; `supersedes` recorded.
5. **No secrets / client data** (§17); screenshots redacted (§16).
6. `compliance_gate: none` (a gated page is never publication-ready — D9).
7. Advisory DoD checker clean for the page; cross-links resolve.
8. Register row present and consistent; crosswalk regenerates cleanly.

## 23. Status criteria (`draft` / `needs_review` / `published`)
- **`draft`** — authored/adapted and structurally complete, but not yet SME-verified and/or not yet
  reviewed. May contain `SME CONFIRMATION REQUIRED`. **Not publishable.**
- **`needs_review`** — awaiting a specific reviewer/SME action; has unresolved confirmation items or a
  pending independent review. **Not publishable.**
- **`published`** — meets **all** publication-readiness criteria (§22): SME-verified, reviewed,
  owner/reviewer assigned, no open confirmations, non-gated, secret/PII-clean. **Only `published`
  (non-gated) rows are eligible for P6 Confluence publication.**

## 24. Rules for adapting legacy Atlas content (mandatory)

1. **Existing Atlas content is evidence, not automatically current truth.** Treat every legacy
   statement as a claim to verify (A2).
2. **Preserve the original Atlas page ID, title, and source link** in provenance metadata (§18).
3. **Do not copy obsolete system configurations as current instructions.** Verify configs before
   presenting them as current.
4. **Identify statements requiring SME confirmation** — flag them inline with `SME CONFIRMATION
   REQUIRED`.
5. **Do not invent** missing procedures, configurations, ownership, recovery objectives (RTO/RPO), or
   escalation contacts.
6. **Use visible placeholders** (`SME CONFIRMATION REQUIRED`, `TBD`, `UNCONFIRMED`) where verified
   information is unavailable.
7. **Never include** passwords, private keys, access tokens, client PII, or secret values (§17).
8. **Regulated AD-5 material remains gated and unpublished** — do not author suitability, replacement/
   1035, licensing, or CE rule sets (D9).
9. **Each replacement document must identify the legacy page(s) it supersedes** (`supersedes`).
10. **No legacy page may be archived until its replacement passes quality review and is approved**
    (P3 quality gate → P4 reconciliation; A3).

## 25. Source assessment (required per adapted document)

Every adapted page carries a `## Source assessment` block (or an entry in a batch assessment file):

| Field | Content |
|---|---|
| Source page | legacy title |
| Source identifier | legacy Confluence id (+ any CAP/SOP id) |
| Source status | current / draft / archived / unknown |
| Suspected age | last-modified / era estimate |
| Current-system applicability | does it match the systems in use now? |
| Duplication | other pages/sources covering the same thing |
| Contradictions | conflicts with other sources or known state |
| Facts verified | what has been SME-confirmed |
| Facts awaiting confirmation | open `SME CONFIRMATION REQUIRED` items |
| Disposition recommendation | retain / merge / split / replace / archive-after-replacement |

## 26. Canonical page skeleton (assembled)

```
---
<front matter per §1>
---

# <Title>

## Purpose & scope            (§4)
## Audience                   (§5)
## Prerequisites              (§7)
## Required systems & permissions   (§8)
## Procedure                  (§9)  — numbered steps
## Expected results           (§10)
## Validation & evidence      (§11)
> ⚠️ WARNING / CAUTION / ⚖️ COMPLIANCE  (§12, inline where relevant)
## Troubleshooting            (§13)  — table
## Escalation                 (§14)
## Related                    (§15)
## Source assessment          (§25, adapted pages)
## Revision history           (§21)
```

## 27. Validation of this standard (see the P1A report)

Validated against: the 0.11 **framework**, existing **templates**, the Publication Register
**taxonomy**, the advisory **DoD**, decisions **D1–D10** and **A1–A4**, and **AD-5** restrictions. The
standard is **editorial/procedural**; it introduces additive metadata and authoring rules only, and
**does not redesign the information architecture or templates**.
