# Developer Demo Mode — Hands-on UX Review

**Branch:** `feature/developer-demo-mode` · **HEAD:** `7dc817c` · **Draft PR #20** (open, mergeable) ·
**Server reviewed live:** http://127.0.0.1:8360 · **Reviewer:** structured role-by-role walkthrough ·
**Code was not modified.**

## Method

Logged in as each persona through `/demo/login` and probed every user-facing route
(53 GET routes, plus portal routes), recording HTTP status and `Content-Type`, and
inspecting rendered HTML for empty states, styling consistency, dead links, and raw
JSON. Parameterized routes used real demo IDs (person=1, household=1, workflow=1,
return=1, entity=1, doc=1, queue=`waiting_on_client`).

## Overall health

- **No 5xx anywhere.** The template bugs fixed earlier (`/work`, `/tax/intake`, etc.)
  are confirmed resolved; every screen renders.
- **Security/RBAC is correct.** Firm-wide screens require `record.read_all`
  (Administrator and Compliance have it); other roles are correctly denied. Portal
  isolation holds.
- **Client Portal persona is the cleanest experience** — all portal pages return 200
  HTML with a consistent nav.
- The dominant problem is **presentation of authorization outcomes**, not correctness:
  three staff personas land on a raw-JSON 403 the instant they log in.

## Persona summary

| Persona | Login lands on | First-screen result | Notable |
|---|---|---|---|
| Administrator | `/` | ✅ 200 — full firm tour | Everything reachable |
| Compliance | `/` | ✅ 200 (has firm-wide read) | Tax screens 403 (correct); `/admin` 403 |
| Advisor | `/` | ❌ **403 raw JSON** | Scoped screens (`/work`, `/people/1`) work |
| Operations | `/` | ❌ **403 raw JSON** | `/work`, `/work/team` work |
| Tax Preparer | `/` | ❌ **403 raw JSON** | `/tax*` works; MS screens 403 (no comms cap) |
| Client Portal | `/portal/` | ✅ 200 — clean | All portal pages 200 |

## Findings

---

### UX-01 — Three staff personas land on a raw-JSON 403 immediately after login
- **Persona:** Advisor, Operations, Tax Preparer
- **Route/page:** `POST /demo/login` → redirect to `/`
- **Severity:** **BLOCKER** (for a demo whose purpose is a hands-on tour)
- **Description:** The demo login redirects every staff persona to `/`. The firm
  dashboard requires `record.read_all`, which only Administrator and Compliance have.
- **Expected:** After login, each persona lands on a page they can actually see.
- **Actual:** Advisor/Operations/Tax Preparer land on `/` and receive HTTP 403 with a
  raw JSON body (`{"detail":"Firm-wide collection access denied","request_id":...}`).
  A reviewer's first impression is "the app is broken."
- **Recommended fix:** In `app/demo/demo_auth.py`, redirect by role to an accessible
  landing page — Advisor/Operations → `/work`, Tax Preparer → `/tax`,
  Administrator/Compliance → `/`. Demo-only change; no production/auth impact.
- **Fix before merging PR #20:** **YES.**

### UX-02 — Authorization errors render as raw JSON to browser users
- **Persona:** Any non-Administrator staff (and unauthenticated users)
- **Route/page:** all capability-gated pages (`/`, `/tax`, `/people`, …) on 401/403
- **Severity:** HIGH
- **Description:** The auth middleware returns `application/json` for 401/403. In a
  browser that is a wall of raw JSON rather than a friendly "not authorized / please
  sign in" page.
- **Expected:** For HTML navigations, a styled 403 page (or redirect to login on 401).
- **Actual:** `Content-Type: application/json`, e.g. `{"detail":"Access denied", …}`.
  Unauthenticated `GET /` returns raw JSON 401 instead of redirecting to a login page.
- **Recommended fix:** Content-negotiate in the middleware error path (HTML for browser
  `Accept: text/html`, JSON for API). This is a **product** change beyond the demo; the
  demo-scoped mitigation is UX-01 (don't land users on a 403).
- **Fix before merging PR #20:** Partial — UX-01's redirect removes the worst case; the
  full HTML-error-page change is a follow-up, not a blocker.

### UX-03 — Global staff navigation is not capability-filtered
- **Persona:** Advisor, Operations, Tax Preparer, Compliance
- **Route/page:** `base.html` nav (`Dashboard / Work / Tax / Tax Intake / Tax Returns / Tax Documents`)
- **Severity:** HIGH
- **Description:** The top nav shows the same links to every staff role. Clicking
  Dashboard (Advisor/Operations/Tax Preparer) or the Tax links (Advisor/Operations/
  Compliance) yields a 403.
- **Expected:** Nav shows only destinations the current role can open (gate each link
  on its capability, mirroring the middleware `RULES`).
- **Actual:** Advisor on `/work` sees 4–5 nav links that all 403; Compliance sees Tax
  links that 403.
- **Recommended fix:** Wrap each nav link in a `{% if principal.can(...) %}`. This is a
  focused template change but touches production; the review is asked to avoid broad
  design changes, so treat as a fast-follow.
- **Fix before merging PR #20:** Recommended, not strictly blocking (UX-01 mitigates the
  landing case). If deferred, document it as a known demo limitation.

### UX-04 — `/matches` uses inconsistent standalone styling
- **Persona:** Administrator, Compliance
- **Route/page:** `/matches`, `/matches/{group_number}`
- **Severity:** MEDIUM
- **Description:** The Match Review page ships its own inline `<style>` (Arial, custom
  header) and does **not** extend `base.html`, so it has no Client360 nav/header and
  looks like a different app.
- **Expected:** Consistent shell (extend `base.html`) like the other staff pages.
- **Actual:** Standalone page, different typography and chrome, no global nav.
- **Recommended fix:** Reparent the template onto `base.html`.
- **Fix before merging PR #20:** No.

### UX-05 — Microsoft 365 links dead-end at real Microsoft OAuth
- **Persona:** Administrator, Advisor, Operations, Compliance
- **Route/page:** `/microsoft365/mail` → `/microsoft365/connect` → `login.microsoftonline.com`
- **Severity:** MEDIUM
- **Description:** "Mail" redirects to "Connect", which 302s to the real Microsoft
  sign-in. The demo has no tenant, so the flow cannot complete; the generated
  `redirect_uri` is also hardcoded to **port 8000** while the demo runs on **8360**.
- **Expected:** In the demo, Microsoft actions should be clearly labeled as
  unavailable, or point at the fictional sync-health view rather than a live OAuth
  redirect.
- **Actual:** Clicking Mail/Connect sends the user to Microsoft's login page.
- **Recommended fix:** In the demo, hide/disable the Connect action or label it
  "unavailable in demo"; the working demo view is `/microsoft365/status` (sync-health).
- **Fix before merging PR #20:** No (documented demo limitation; label as a follow-up).

### UX-06 — Document download returns 404 "Stored file is missing"
- **Persona:** Administrator, Compliance
- **Route/page:** `/documents/{id}/download`
- **Severity:** MEDIUM
- **Description:** Demo documents are metadata-only (no files on disk), so the download
  link 404s with `<h1>Stored file is missing</h1>`.
- **Expected:** Either seed a small placeholder file, or hide/disable the download
  action in the demo.
- **Actual:** 404 with a terse message.
- **Recommended fix:** Seed a placeholder file for demo documents, or suppress the
  download link when the file is absent.
- **Fix before merging PR #20:** No (documented limitation; the guide already notes
  "documents are metadata-only").

### UX-07 — `/portfolio/search` still returns raw JSON at a non-`/api` path
- **Persona:** Administrator, Compliance
- **Route/page:** `/portfolio/search`
- **Severity:** LOW
- **Description:** After the recent fix, nav points at the HTML `/portfolio`, but the
  JSON endpoint remains at `/portfolio/search` (a user-facing path, not under `/api`).
  It is no longer linked, but a user who types it sees raw JSON.
- **Expected:** JSON API lives under `/api/...`, or the path is clearly API-only.
- **Actual:** `/portfolio/search` → 200 `application/json`.
- **Recommended fix:** Optionally alias the JSON under `/api/v1/portfolio/search`
  (keep the old path for compatibility). Cosmetic.
- **Fix before merging PR #20:** No.

### UX-08 — Unauthenticated access shows raw JSON instead of a login prompt
- **Persona:** Any (logged-out)
- **Route/page:** `GET /` (no session)
- **Severity:** LOW (facet of UX-02)
- **Description:** A logged-out browser hitting `/` gets raw JSON 401 rather than being
  sent to a sign-in page.
- **Expected:** Redirect to `/demo/login` (demo) / `/auth/login` (prod) for HTML clients.
- **Actual:** `401 application/json`.
- **Recommended fix:** Same content-negotiation as UX-02.
- **Fix before merging PR #20:** No.

### UX-09 — Firm dashboard links to `/admin` for Compliance (403)
- **Persona:** Compliance
- **Route/page:** `/` firm dashboard → `/admin`
- **Severity:** LOW
- **Description:** Compliance can open the firm dashboard, whose action grid links to
  admin destinations Compliance cannot open (`/admin` → 403; `/admin/audit` works).
- **Expected:** Hide links the role cannot use (same root cause as UX-03).
- **Actual:** A dead link to `/admin`.
- **Recommended fix:** Capability-gate dashboard action links (folds into UX-03).
- **Fix before merging PR #20:** No.

### UX-10 — Responsive layout / empty states (observations, mostly positive)
- **Persona:** All
- **Route/page:** global
- **Severity:** LOW / INFO
- **Description:** Viewport meta is present and tables use `.table-wrap` containers, so
  layout is structurally responsive; full width behavior could not be verified over
  HTTP. Empty states are generally handled well — list templates render friendly
  "No authorized …" rows (e.g. `/tax/documents`, `/relationships/search`, work lists).
- **Expected / Actual:** Acceptable; no defect found.
- **Recommended fix:** None; verify visually at mobile widths during a later design pass.
- **Fix before merging PR #20:** No.

---

## Top 10 UX issues (prioritized)

1. **UX-01 (BLOCKER)** — Advisor/Operations/Tax Preparer land on a raw-JSON 403 after login.
2. **UX-02 (HIGH)** — 401/403 errors render as raw JSON to browser users.
3. **UX-03 (HIGH)** — Global staff nav is not capability-filtered (dead-end links per role).
4. **UX-05 (MEDIUM)** — Microsoft 365 Mail/Connect dead-ends at real Microsoft OAuth (wrong port, no tenant).
5. **UX-06 (MEDIUM)** — Document download 404s ("Stored file is missing") for metadata-only demo docs.
6. **UX-04 (MEDIUM)** — `/matches` uses inconsistent standalone styling (no app shell).
7. **UX-09 (LOW)** — Firm dashboard offers `/admin` link that 403s for Compliance.
8. **UX-08 (LOW)** — Logged-out `/` shows raw JSON 401 instead of a login prompt.
9. **UX-07 (LOW)** — `/portfolio/search` exposes raw JSON at a non-`/api` path.
10. **UX-10 (INFO)** — Responsive/empty states: acceptable; verify visually later.

## Recommendation

### MERGE AFTER FIXES

The demo is functionally sound — no 5xx, correct RBAC, clean portal experience, and a
complete Administrator tour. But three of the five staff personas hit a raw-JSON 403 the
moment they log in (UX-01), which makes the app look broken during exactly the hands-on
tour this branch exists to enable. That single issue should be fixed before PR #20 merges;
the rest can follow.

## Remediation plan (blocking items only)

Scope limited to what should block PR #20 — small and demo-scoped:

1. **UX-01 — role-aware post-login redirect** (`app/demo/demo_auth.py`): after a
   successful staff login, redirect to a page the persona can open —
   Advisor/Operations → `/work`, Tax Preparer → `/tax`, Administrator/Compliance → `/`.
   No production or auth-behavior change.
2. **Documentation touch-up** (`docs/DEVELOPER_DEMO_MODE.md`): state the per-persona
   landing pages and reiterate that non-firm-wide roles see 403 on firm-wide collections
   (partially documented already), so reviewers know it is by-design RBAC, not a bug.
3. **Add the landing to the demo smoke** (`app/demo/smoke.py` or `scripts/demo.sh`):
   assert each persona's post-login landing page returns 200, to prevent regressions.

Everything else (UX-02/03 error pages + nav filtering, UX-04 styling, UX-05/06 demo
dead-ends, UX-07 JSON path) is recommended as fast-follow work and is **not** required to
merge PR #20.

---

## Remediation appendix

### UX-01 — RESOLVED ✅

**Fix (demo-only):** role-aware post-login routing. `app/demo/credentials.py` now
carries a `landing` per persona and `app/demo/demo_auth.py` redirects to it after the
(unchanged) real session-creation path. No production/OIDC change; no RBAC change; no
role gained `record.read_all`.

**Landing pages now in effect:**

| Persona | Redirects to | Landing status |
|---|---|---|
| Administrator | `/` | 200 text/html |
| Compliance | `/` | 200 text/html |
| Advisor | `/work` | 200 text/html |
| Operations | `/work` | 200 text/html |
| Tax Preparer | `/tax` | 200 text/html |
| Client Portal User | `/portal/` | 200 text/html |

**Verification:**
- Live (running demo server): all six personas redirect (303) to the landing above and
  the landing returns **200 text/html** — no staff login lands on a raw-JSON 403.
- Regression tests `tests/test_demo_login_landing.py` (10): landing map matches spec;
  each staff login returns a 303 redirect to its landing (never a 403/JSON); production
  auth routes remain OIDC (`/auth/login`, `/auth/callback`) and `app.main` does not
  import demo code.
- Demo smoke `scripts/demo.sh smoke`: per-persona landing check **PASS**.
- Full suite: **317 passed / 5 skipped**. Demo app startup/shutdown clean.
- `docs/DEVELOPER_DEMO_MODE.md` updated with the landing table and a note that firm-wide
  403s remain expected, correct RBAC.

**Status of other findings:** UX-02, UX-03, UX-04, UX-05, UX-06, UX-07, UX-08, UX-09,
UX-10 remain **open** as approved fast-follow items (not addressed in this change).
