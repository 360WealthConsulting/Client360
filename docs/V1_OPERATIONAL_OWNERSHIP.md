# Client360 — Version 1.0 Operational Ownership & Accountability

Accountability document for the Version 1.0 production cutover and ongoing production support.
It names the roles required to execute [`V1_CUTOVER_CHECKLIST.md`](V1_CUTOVER_CHECKLIST.md) and to
run production afterward. Roles here map to the placeholders used in that checklist.

**Naming rule:** `[NAME REQUIRED]` means the repository does not identify a person; a named
individual **and** their recorded acceptance are required before cutover. No names are invented.
The only role the repository explicitly attaches to a named person is the *product-decision /
business owner* (Michael Shelton, per `PRODUCT_DECISIONS.md`); that is a **candidate note** for the
business/sponsor roles below, not an assignment to an operational cutover role.

## Authority summary — who can do what
| Action | Authorized role |
|--------|-----------------|
| Authorize deployment to begin | **Release Manager** (within the window approved by the Executive Sponsor) |
| Pause / hold the cutover | **Release Manager** or **Incident Owner** |
| Order a rollback | **Rollback Decision Authority** |
| Accept the business workflows (UAT sign-off) | **Business Acceptance Owner** |
| Announce go-live | **Business Acceptance Owner** (after acceptance) |
| Close the release | **Release Manager** (with **Executive Sponsor** sign-off) |

*Operational execution authority (Release Manager, Deployment Owner) is distinct from business
acceptance authority (Business Acceptance Owner, Executive Sponsor). Incident coordination
(Incident Owner) is distinct from technical remediation (performed by the Deployment/Monitoring/
Backup owners under the Incident Owner's coordination).*

---

## Release Manager  ·  placeholder `[RELEASE-MANAGER]`
- **Responsibilities:** Owns and coordinates the end-to-end cutover; runs the checklist; confirms
  each phase gate is met; communicates status; declares go / no-go for each phase.
- **Decisions authorized:** Authorize deployment to begin; pause/hold the cutover; sequence the
  phases; propose release closeout.
- **Required availability during cutover:** Present and reachable for the entire cutover window.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Executive Sponsor.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Deployment Owner  ·  placeholder `[DEPLOY-OWNER]`
- **Responsibilities:** Executes the deployment (`scripts/deploy.sh`), migrations, post-deploy
  smoke (`scripts/smoke.sh`); verifies health/readiness and migration status; performs technical
  remediation during an incident.
- **Decisions authorized:** Technical execution choices within the runbook; declare a deploy step
  passed/failed. Does **not** unilaterally authorize the cutover or order a rollback.
- **Required availability during cutover:** Hands-on for Phases 1–3 and on-call through Phase 5.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Release Manager → Executive Sponsor.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Rollback Decision Authority  ·  placeholder `[ROLLBACK-AUTHORITY]`
- **Responsibilities:** Single point of authority to order a rollback; owns the rollback go/no-go.
- **Decisions authorized:** **Order a rollback** (`scripts/rollback.sh` executed by the Deployment
  Owner); abort the cutover on a failed verification.
- **Required availability during cutover:** Reachable for immediate decision throughout Phases 3–5.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Executive Sponsor.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Incident Owner  ·  placeholder `[INCIDENT-OWNER]`
- **Responsibilities:** Coordinates incident response (not technical remediation itself); convenes
  the right owners; tracks timeline; owns communications during an incident.
- **Decisions authorized:** Declare/close an incident; pause the cutover; recommend a rollback to
  the Rollback Decision Authority.
- **Required availability during cutover:** On-call for the full cutover window and the
  stabilization period.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Release Manager → Executive Sponsor.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Monitoring Owner  ·  placeholder `[MONITOR-OWNER]`
- **Responsibilities:** Ensures `/health` and `/readiness` are wired to alerting; watches
  production during and after cutover; verifies scheduled jobs are running.
- **Decisions authorized:** Raise an alert to an incident; confirm monitoring green/red.
- **Required availability during cutover:** Active watch during Phase 3 and the stabilization window.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Incident Owner → Release Manager.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Backup and Restore Owner  ·  placeholder `[BACKUP-OWNER]`
- **Responsibilities:** Configures scheduled encrypted backups (+ RPO/RTO); confirms the first
  backup executes; owns restore capability (`scripts/restore_rehearsal.sh`).
- **Decisions authorized:** Confirm backup/restore readiness green/red.
- **Required availability during cutover:** Available for Phase 2 verification and Phase 5 backup
  confirmation.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Incident Owner → Release Manager.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Support Owner  ·  placeholder `[SUPPORT-OWNER]`
- **Responsibilities:** Owns end-user support intake/triage; captures production issues; is the
  staff-facing point of contact post-go-live.
- **Decisions authorized:** Triage severity; route issues to the Incident Owner.
- **Required availability during cutover:** Ready from go-live through stabilization.
- **Primary owner:** `[NAME REQUIRED]`
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Incident Owner → Business Acceptance Owner.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Business Acceptance Owner  ·  placeholder `[BUSINESS-OWNER]`
- **Responsibilities:** Represents the staff/business; validates key workflows (UAT); provides the
  business acceptance sign-off; announces go-live to staff.
- **Decisions authorized:** **Accept business workflows** (UAT sign-off); **announce go-live**.
  Does **not** hold operational execution or rollback authority.
- **Required availability during cutover:** Available for Phase 4 acceptance.
- **Primary owner:** `[NAME REQUIRED]`  _(candidate per evidence: Michael Shelton is the documented
  business/product-decision owner — confirm whether this role applies, then record acceptance)_
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** → Executive Sponsor.
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

## Executive Sponsor  ·  placeholder `[EXEC-SPONSOR]`
- **Responsibilities:** Owns the go-live decision at the business level; approves the deployment
  window; provides final release closeout sign-off; owns unresolved product/compliance decisions.
- **Decisions authorized:** Approve the cutover window; final sign-off on release closeout; ultimate
  escalation point.
- **Required availability during cutover:** Reachable for go/no-go and closeout.
- **Primary owner:** `[NAME REQUIRED]`  _(candidate per evidence: Michael Shelton is the documented
  business/product-decision owner — confirm whether this role applies, then record acceptance)_
- **Backup owner:** `[NAME REQUIRED]`
- **Contact method:** `[TO PROVIDE]`
- **Escalation path:** — (top of chain).
- **Acceptance / sign-off:** ☐ accepted — name: ____________  date: ____

---

## Release ownership gate
**Version 1.0 must not be tagged, deployed, or announced until every required primary owner and
backup owner above has been named and has recorded acceptance of responsibility.** As of this
document, **no role has a named/accepted primary or backup owner** — the gate is **NOT satisfied**.
