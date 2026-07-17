# RC-0.10.0 — Release Candidate Validation (Insurance Operations)

**Release:** 0.10.0 — Insurance Operations · **Branch:** `feature/insurance-operations` ·
**PR:** [#27](https://github.com/360WealthConsulting/Client360/pull/27) (draft) ·
**Alembic head:** `d0l1n2o3i4k5` · **Date:** 2026-07-16

> **Scope & boundary.** **The non-regulated Release 0.10.0 implementation (Phases 0–9) is
> complete and ready to enter Phase 10 RC validation. AD-5 remains an external release blocker
> for any regulated insurance functionality.** Only the implemented **non-regulated** surface is
> entering RC validation — this is **not** a statement that the entire insurance platform is
> release-ready. All AD-5-gated regulated functionality (suitability, replacement/1035,
> licensing/CE validation, sale/issue blocking, compliance approval) remains intentionally
> **excluded** and unbuilt, pending a qualified, named compliance reviewer and approved sign-off.

## 1. What is in scope for this release

Non-regulated Phases 0–9 of the Insurance Operations domain, built **inside** Client360 by
reusing the platform (exception engine, work management, scheduler, portal, documents,
audit/timeline, reporting, integration-provider idiom) — **no parallel subsystems**:

| Phase | Surface | Migration |
|---|---|---|
| 0 | Schema foundation; caps/roles; engine+work registration | `v2b3d4f5a6c7` |
| 1 | Policies core; product-version evolution; CRUD + UI; lifecycle/Timeline | `w3c4e5g6b7d8`,`x4d5f6h7c8e9` |
| 2 | New-business pipeline (skeleton) | `y5e6g7i8d9f0` |
| 3 | In-force reviews + obligation calendar (skeleton) | `z6f7h8j9e0g1` |
| 4 | Producer licensing & CE **records** (skeleton) | `a7g8i9k0f1h2` |
| 5 | Commissions ledger + reconciliation + revenue rollup | `b8i9k1l2g3j4` |
| 6 | Exceptions, work queues & scheduled scanning | `c9k0m1n2h3j4` |
| 7 | Policyholder portal surface (read-only, scoped) | — (read-only) |
| 8 | Reporting & operations dashboard | — (read-only) |
| 9 | Integration ports as **disabled** stubs | — (code-only) |
| — | Pre-Phase-7 cleanup: dedicated `insurance.scan` capability | `d0l1n2o3i4k5` |

## 2. Validation results

| Check | Result |
|---|---|
| Full regression suite (`scripts/test.sh run`) | ✅ **717 passed, 5 skipped, 0 failed** |
| Compile (`compileall` app/scripts/migrations/tests) | ✅ OK |
| Ruff ratchet (`scripts/ruff_gate.py`) | ✅ no new violations (baseline holds) |
| `git diff --check` | ✅ clean |
| Working tree | ✅ clean |
| Single Alembic head | ✅ `d0l1n2o3i4k5` (dev + test) |
| Migration chain integrity | ✅ linear `u1f9c0i9h8g7`→…→`d0l1n2o3i4k5`, single head |
| Migration reversibility (full graph) | ✅ up→down(base)→up ends at head |
| Schema at head (dev + test) | ✅ |
| CHANGELOG structural lint (`check_changelog.py`) | ✅ OK |
| Startup / shutdown (real uvicorn) | ✅ `/health` + `/readiness` 200; clean lifecycle |
| Release script dry-run (`release.sh 0.10.0 --dry-run`) | ✅ all gates pass **except** the intentional pre-tag items (see §6) |
| Secrets / credentials / endpoints / debug artifacts | ✅ none (see note) |

*Secrets note:* the diff scan surfaced only benign matches — shell-script CLI `print()` in the
`pyenv` portability guards, a **runtime-generated** demo Fernet key, a **localhost** demo URL,
and the integration **test guardrails** that deliberately use a fake `"SECRET-XYZ"` to prove
non-leakage. No real secret, production endpoint, or application debug artifact is committed.

## 3. Security review

| Concern | Result |
|---|---|
| Authorization / capability enforcement | ✅ all 58 insurance routes `require_capability`; 3 portal routes `current_portal` |
| Privilege escalation | ✅ every mutation route requires a **write-class** capability (no read-gated mutations) |
| Record-scope enforcement | ✅ `_policy_scope_ok` / `portal_scope` / scoped list services applied **before** aggregation |
| Organization boundaries | ✅ org anchoring on commission exceptions; org-scoped feed boundary defined |
| Client / staff separation | ✅ staff `/insurance/*` (capability) vs client `/portal/*` (portal); dashboard staff-only |
| Information leakage | ✅ compensation & firm-internal exceptions never reach client surfaces; `client_action_items` hard-scoped to `domain='tax'`; portal projection is a fixed portal-safe key set |
| Audit coverage | ✅ mutations write audit events; integration invoke logs metadata only (never payloads/secrets) |
| Reserved (unused) capabilities | ✅ `insurance.suitability`, `insurance.sensitive.read` are **intentional AD-5 reservations** — documented, `sensitive`-flagged, role-granted, segregation-of-duty tested |

## 4. Release checklist

- [x] Full regression suite green (717/5/0)
- [x] Compile, ruff, `git diff --check`, clean working tree
- [x] Single Alembic head; chain linear & reversible; schema at head
- [x] Authorization, record-scope, org boundaries, client/staff separation verified
- [x] Audit coverage verified; no information leakage
- [x] Startup/shutdown clean
- [x] No secrets, credentials, endpoints, or debug artifacts committed
- [x] CHANGELOG structural lint passes; Phases 0–9 documented
- [x] Documentation consistent (RC audit + this pass); version/phase references internally consistent
- [x] Draft PR #27 complete (per-phase summary + current status)
- [ ] **Approval to release** — date the CHANGELOG `[0.10.0]` entry, merge PR #27 to `main`, tag `v0.10.0` *(gated on approval — §6)*

## 5. Release notes — 0.10.0 (non-regulated Insurance Operations)

Individual **life insurance & annuities** as a domain inside Client360, reusing the platform:
- **Policies & product catalog** — carriers/families/versions, policies + coverages/riders/
  parties/producers, lifecycle statuses, shared Timeline/Audit.
- **New-business pipeline & in-force servicing** (non-regulated skeletons) — case progression,
  requirement tracking, reviews state machine + obligation calendar.
- **Producer licensing & CE records** (non-regulated) + expiry reminders.
- **Commissions** — split-aware expected/received ledger, adjustments/reversals/chargebacks,
  carrier-statement import + reconciliation, ledger-derived revenue rollup.
- **Exceptions, work queues & scheduled scanning** — one `run_insurance_scan()` on the shared
  Exception Engine, registered on the existing scheduler; insurance work queues + auto-assignment.
- **Policyholder portal** — read-only, opt-in, scoped policy view (no compensation/exceptions).
- **Reporting & dashboards** — consolidated firm-internal operations dashboard, proportional to
  the viewer's capabilities and record scope.
- **Integration ports** — six vendor-neutral, **disabled** extension-point stubs.

**Excluded (AD-5, not built/not enabled):** suitability, replacement/1035, licensing/CE
validation, sale/issue blocking, compliance approval, and any client-facing exception visibility.

Full detail: `CHANGELOG.md` `[Unreleased]` and `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md`.

## 6. Remaining steps to release (approval-gated)

The repository is in a **releasable state** for the non-regulated surface. The release-script
dry-run's only failures are the **intentional pre-tag** steps, which are performed **only after
approval**:
1. Date the CHANGELOG `[0.10.0]` entry (convert from `[Unreleased]`).
2. Merge draft PR #27 into `main` (the release is cut from `main`).
3. Tag `v0.10.0` (`scripts/release.sh 0.10.0`).

No tag is created and PR #27 is **not** merged as part of this RC validation.

## 7. Release recommendation

**Release 0.10.0 (non-regulated implementation) has successfully passed RC validation and is
recommended for release. AD-5-regulated functionality remains intentionally excluded pending
compliance review.**
