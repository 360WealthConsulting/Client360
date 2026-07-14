# Client360 Release 1.0 Readiness

## Purpose

This document defines the remaining work required to promote the Release 0.9.x
platform into a supported production Release 1.0. Feature completion alone does
not authorize production launch. Every applicable security, deployment,
operational, provider, data, and recovery gate below requires an accountable
owner and recorded evidence.

## Completed platform capabilities

- Canonical people, households, source matching, tasks, activities, documents,
  search, dashboards, timeline, and Client Workspace.
- Microsoft Outlook mail/calendar and SharePoint/OneDrive metadata intelligence
  with matching, deduplication, and unmatched review.
- Relationship Intelligence across family, professional, business, trust,
  estate, beneficiary, and household connections.
- Schwab Portfolio Intelligence across accounts, registrations, holdings,
  cash, lots, transactions, performance, billing, beneficiaries, and rollups.
- Firm identities, capability-composed roles, teams, record assignments,
  sessions, record-level authorization, provider adapters, and immutable audit.
- Operational Work Management: My Work, Team Work, queues, priorities, capacity,
  SLA risk, assignments, dashboards, and versioned APIs.
- Workflow Automation: immutable templates, snapshots, dependencies, parallel
  and conditional work, lifecycle controls, approvals, escalations, triggers,
  idempotent actions, metrics, and 12 seeded processes.
- Client Portal foundation: isolated identities/sessions, household grants,
  secure messaging, document requests, client tasks, notifications, and
  provider-neutral e-signature architecture.
- One linear Alembic history supporting clean install and sequential upgrades
  through `f640a6c4e5f6`.

## Remaining Release 1.0 work

- Sprint 4.5 Tax Practice Operations and any explicitly approved remaining Epic
  4 scope required to replace daily TaxDome operations.
- Staff administration UI for portal invitations, delegated grants, devices,
  sessions, notifications, and provider status.
- Production identity adapters and account lifecycle automation for staff and clients.
- Live-provider integration validation, production file security, accessibility,
  observability, recovery, privacy, retention, and operational support readiness.
- Performance testing with representative production volumes and concurrency.
- Formal acceptance by business, compliance, security, operations, and release owners.

## Production launch gates

- Approved production architecture and threat model.
- Managed PostgreSQL, encrypted object storage, secrets manager, TLS, private
  networking, environment separation, and least-privilege service identities.
- Production OIDC/MFA for staff and portal clients; break-glass access documented.
- Content-type enforcement, malware scanning, quarantine, optional content
  disarm, file-size limits, and safe download headers.
- Rate limiting, bot protection, abuse detection, CSRF/session hardening,
  dependency scanning, SAST, DAST, penetration test, and remediation sign-off.
- Privacy notice, consent, data classification, retention, legal hold, deletion,
  subject-access, breach-response, and vendor-processing review.
- Accessibility review to WCAG target, responsive browser matrix, and usability acceptance.
- Production data migration rehearsal, reconciliation, sign-off, and rollback plan.

## Operational launch gates

- Named service owner, release owner, security owner, data owner, and on-call rotation.
- Centralized logs, metrics, traces, immutable audit export, dashboards, alerts,
  scheduler monitoring, provider health, queue depth, and SLA breach alerts.
- Runbooks for authentication failure, provider outage, stuck workflow,
  document quarantine, data correction, security incident, and client support.
- Backup schedule, encryption, retention, restore test, regional recovery plan,
  recovery time objective, recovery point objective, and disaster-recovery exercise.
- Capacity and load tests for portal login, dashboard, messaging, uploads,
  workflow processing, Microsoft sync, portfolio imports, and reporting.
- Change management, release approvals, maintenance windows, user communication,
  support training, knowledge base, and post-launch hypercare.

## Third-party provider decisions

| Capability | Decision required | Release gate |
|---|---|---|
| Staff identity | Select managed OIDC/MFA tenant and claims contract | Required before staff production access |
| Portal identity | Select consumer/client identity and MFA provider | Required before public portal activation |
| E-signature | Select DocuSign, Adobe Sign, or approved alternative | Optional for base launch; required before signature enablement |
| Email | Select transactional provider, verified domains, DKIM/SPF/DMARC | Required before external email notifications |
| SMS | Select provider, consent/opt-out process, quiet hours | Required before SMS enablement |
| Push | Select mobile/push provider and device-consent model | Required before push enablement |
| File scanning | Select malware/quarantine/content-disarm services | Required before client uploads |
| Microsoft 365 | Approve tenant permissions, throttling, retry, and monitoring | Required before live synchronization |
| Schwab | Approve supported export/API acquisition and reconciliation | Required before production portfolio automation |
| Tax systems | Decide Drake/TaxDome migration and coexistence strategy | Required for tax-operation cutover |

No adapter should be enabled merely because its interface exists. Each provider
requires security, privacy, procurement, resilience, support, and exit review.

## Security checklist

- [ ] Staff and portal MFA enforced by verified provider assertions
- [ ] Least-privilege roles, capabilities, teams, grants, and service accounts reviewed
- [ ] Self, joint, trusted, and delegated access acceptance-tested
- [ ] Session duration, revocation, device, cookie, CSRF, and origin policies approved
- [ ] Secrets absent from source, logs, errors, audit metadata, and client responses
- [ ] Encryption in transit and at rest verified
- [ ] Upload scanning/quarantine and safe download controls verified
- [ ] Internal notes and staff-only records proven absent from portal APIs
- [ ] Audit immutability, export, retention, access, and alerting verified
- [ ] Dependency, container, infrastructure, SAST, DAST, and penetration findings closed
- [ ] Rate limits, lockouts, abuse monitoring, and incident response tested
- [ ] Privacy, retention, consent, delegated-access, and vendor reviews approved

## Deployment checklist

- [ ] Release commit, tag, artifacts, checksums, and approvals recorded
- [ ] Exactly one Alembic head and clean migration rehearsal confirmed
- [ ] Production backup and tested restore point created
- [ ] Maintenance/cutover window and stakeholder communication approved
- [ ] Secrets, provider callbacks, domains, certificates, and firewall rules validated
- [ ] Database migration timing and locks accepted
- [ ] Application health, routes, startup, schedulers, workers, and provider probes green
- [ ] Smoke tests for staff login, portal login, record isolation, documents,
      messaging, workflows, notifications, Microsoft sync, and portfolio data passed
- [ ] Audit events, logs, metrics, traces, alerts, backups, and dashboards verified
- [ ] Rollback decision point, owner, commands, and data implications documented
- [ ] Post-deployment reconciliation and business-owner acceptance completed
- [ ] Hypercare and incident contacts published

## Recommended Epic 5 roadmap

**Epic 5 — Intelligence, Planning, and Growth** should begin only after Epic 4
and the Release 1.0 gates are accepted.

1. Tax Intelligence: normalized returns, organizers, notices, estimates,
   transcripts, planning facts, and provider-neutral acquisition.
2. AI Meeting Preparation and Client Briefs using permission-filtered Microsoft,
   relationship, portfolio, tax, workflow, and timeline context.
3. Planning Opportunity Engine for estate, tax, insurance, retirement,
   beneficiary, cash, concentration, RMD, and business-owner risks.
4. Compliance Intelligence for supervision, correspondence sampling, reviews,
   retention, exception management, attestations, and evidence packages.
5. Revenue and Growth Intelligence for billing, profitability, pipeline,
   referrals, service segmentation, capacity, and acquisition analysis.
6. Live Custodian and Financial Data APIs behind the existing acquisition adapters.

AI features must preserve the existing capability, record, household, portal,
and delegated-access boundaries and provide explainable source attribution,
human approval, auditability, evaluation, and safe fallback behavior.
