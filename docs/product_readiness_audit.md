# 360Plus Product Readiness Audit (staff · existing clients · new-client onboarding)

Read-only assessment of the current implementation. Classification per area:
**READY** · **FUNCTIONAL BUT NEEDS WORK** · **BLOCKING CLIENT USE** · **NOT IMPLEMENTED**.
Objective driving the plan: **usable internal staff product first → a few real clients on the portal safely
→ gradual client expansion.**

## Readiness by area

| # | Area | Rating | Evidence / gap |
|---|---|---|---|
| 1 | Authentication — **staff** | FUNCTIONAL, NEEDS WORK | OIDC + MFA + session + logout + invite complete (`routes/auth.py`, `integrations/identity/oidc.py`, `security/service.py`); requires a configured external OIDC provider (unconfigured → 503). |
| 1 | Authentication — **client portal** | **BLOCKING CLIENT USE** | Account/invite/session/MFA/reset plumbing is real and password-less (`portal/service.py:28-64`), but the ONLY IdP is a local test stub (`portal/identity_local.py:24`, disabled once `portal.production_signed_off`), and `templates/portal/login.html` is a placeholder. No production client sign-in. |
| 2 | Client portal (overall) | **BLOCKING CLIENT USE** | Strong scoped/audited service core (`portal/service.py`, `portal_api.py`), but every `templates/portal/*.html` is view-only (no `<form>`/file input) and there is no production IdP. Clients can view, not transact. |
| 3 | Staff portal / workspace | **READY** | Advisor "Today" (`routes/workspace.py`) + Client 360 (`routes/client360.py`, ~40 gated tabs, real reads + audited writes). One honestly-stubbed field (`sections.py:251 ai_status`). |
| 4 | Role & permission boundaries | **READY** (staff) / NEEDS WORK (defense-in-depth) | Layered, default-deny, server-side (`security/middleware.py:35-347`, `dependencies.py`, `policy.py`); strict staff/client principal fork. Risk: a NEW staff route not covered by a middleware RULE **and** lacking a `require_capability` dep would be authenticate-only — no coverage guarantee exists. |
| 5 | Client/household/business relationships | FUNCTIONAL, NEEDS WORK | Linked, navigable relationship graph (`workspace.html:75-125`, `services/relationships.py`); households + members real (`routes/households.py`). Gaps: org routes buried in `routes/benefits.py:338-365`; household profile shows no joint documents. |
| 6 | Dashboard / navigation | FUNCTIONAL, NEEDS WORK | Primary capability-gated nav + `/` + `/home` are real; ~23 `templates/*/home.html` are thin enterprise-scaffolding composition shells reached mostly via deep links. |
| 7 | Document vault browsing | FUNCTIONAL, NEEDS WORK | Scoped per-person browsing works (`routes/documents.py`, `document_library`); no household-doc view, no folder navigation (folders computed but unrendered), non-clickable owner links (`document_library/detail.html:32`). |
| 8 | Document preview / download | **READY** | PDF/image/HEIC/Excel preview + download with middleware-enforced, audited authorization (`routes/documents.py:111-302`, `middleware.py:333-364`). |
| 9 | Secure client upload | FUNCTIONAL, NEEDS WORK | Real scoped/size/extension-safe upload API + vault storage (`portal_api.py:108-197`, `vault/storage.py`); **no browser UI**, no MIME sniff, no AV scan. |
| 10 | Staff upload | FUNCTIONAL | Real binary upload + person link (`POST /people/{id}/documents`, `routes/documents.py:52`) and vault API; the polished Document Library is metadata-only. |
| 11 | Document categorization / organization | FUNCTIONAL, NEEDS WORK | Rich classifiers exist (`document_classification.py` 24-type; `documents.category`; `classification`), but rule-based `doc_type` reaches only search, not browsing; flat lists, folders unrendered. |
| 12 | Document search | **READY** | Name + OCR-text + type + person, fully scope-enforced, working UI (`services/universal_search.py`, `routes/search.py`, `search/universal.html`). |
| 13 | Client/staff communications & messaging | **BLOCKING CLIENT USE** | Real scoped messaging backend (`portal/service.py:129-178`), but no client compose/reply UI and no staff-reply route (`staff_send_message` called only by demo seed). No two-way conversation in-app. |
| 14 | Notifications | FUNCTIONAL, NEEDS WORK | Event-driven in-app records real (`portal/service.py:227-243`, triggered by tax/exception/benefits flows); **no email/SMS/push delivery** (all `DisabledNotificationHook`) — invitation & reset emails never sent. |
| 15 | Tasks / workflow | FUNCTIONAL | Task create/assign/complete real + audited (`services/tasks.py`, `routes/tasks.py`, `client360.py`); full workflow instance lifecycle (`routes/workflows.py`, `workflow_automation.py`). |
| 16 | New-client intake / onboarding | FUNCTIONAL, NEEDS WORK | Portal-invite, household-create, tax-intake all real, but portal-invite is JSON-API-only (no HTML form), and there is no direct "create person" UI — person creation is indirect (proposal/import). Disjointed multi-step. |
| 17 | Client profile / contact / account | FUNCTIONAL, NEEDS WORK | Audited editable profile service (`portal/profile.py`) via PATCH API; **no profile page/form** in the portal UI (`PAGE_NAMES` has no `profile`; `settings.html` is static). |
| 18 | Manual-review / document-exception | FUNCTIONAL, NEEDS WORK | Coherent, guarded, audited resolution of every exception class (unassigned / review-queue / high-confirm / entity-proposals / context-review). Fragmented across 5 pages, mostly per-document, no unified inbox/counts, live-recompute at scale. |
| 19 | Audit trail | **READY** | DB-enforced append-only trigger + tamper-evident hash chain (`security/audit.py`, migration `c410f4a1b2c3:49-50`, `f2h3c4a5i6n7`), broad coverage, capability-gated viewer. |
| 20 | Mobile / responsive usability | FUNCTIONAL, NEEDS WORK | Viewport meta everywhere; staff shell has breakpoints (`app.css`, 4 media queries) + fixed sidebar; client portal is minimal single-column (`main.css`, 0 breakpoints) — usable but unpolished. |
| 21 | User-facing 360Plus branding | **READY** | Rebrand complete; residual `Client360` strings are docstrings/comments + the machine-facing `/health` `application` field — none user-visible. |

## True blockers to putting the FIRST real client on 360Plus

1. **No production portal identity provider** — only `LocalTestIdentityProvider`; a real external client cannot authenticate. (Requires wiring an OIDC/SAML/magic-link portal adapter — external config/infra.)
2. **No out-of-band delivery (email/SMS)** — invitation and password-reset tokens are generated but never delivered (all delivery hooks disabled; no SMTP anywhere). (Requires an email provider — external infra.)
3. **Portal UI is view-only** — no upload form, no message compose/reply, no profile form; the client can see data but cannot act.
4. **No staff-reply route for portal messages** — two-way client↔staff conversation is impossible in-app.

Blockers 1–2 need external infrastructure (IdP host, SMTP) and are **not safely implementable dev-only**; they are config/integration items for a later phase. Blockers 3–4 and the staff-productivity gaps ARE safe, additive, dev-only, and job-independent — the focus of this task's implementation.

## Prioritized implementation plan

**Phase 1 — usable internal staff product (do first; all safe/dev-only):**
- Unified staff **Review Inbox** with live counts + links across all five review lanes (fixes the fragmentation flagged in areas 5 & 18). Highest staff-productivity leverage over the current document backlog.
- **No-ungated-route RBAC coverage guard** (test) — guarantees no staff route ships without a capability gate (closes the area-4 defense-in-depth caveat). Security hardening, zero behavior change.
- Staff **HTML invite-client form** over the existing scoped/audited `invite_portal_account` service (area 16) — a real onboarding entry point (the backend already exists; only the form was missing).

**Phase 2 — allow a few real clients safely (mix of dev-only + external infra):**
- Portal **message compose/reply UI** + a **staff reply route** (unblocks area 13; backend already exists).
- Portal **upload form** + **profile page/form** (areas 9, 17; backends already exist).
- Wire a **production portal IdP** and **email delivery** (blockers 1–2; external, not dev-only).
- **MIME sniff + AV scan** on uploads before real client files (area 9 hardening).

**Phase 3 — expand client adoption:**
- Document folder/grouping navigation + household-document view + clickable owner links (areas 7, 11, 5).
- Bulk actions + persisted/precomputed review backlog for scale (area 18).
- Notification email/SMS providers, portal mobile polish (areas 14, 20).

## What was implemented in this task (dev-only, safe, job-independent)
See the commits referenced in the task report. Implementation is confined to Phase-1 items that build on already-working, scope-enforced, audited services and add tests; nothing touches production, the production DB, or the running OCR/import job.
