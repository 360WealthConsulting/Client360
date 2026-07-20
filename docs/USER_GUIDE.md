# Client360 — Staff User Guide (v1.0 CRM)

A practical guide to running daily client work in Client360. Covers the Version 1.0 CRM surface:
finding clients, the client profile, notes, communications, tasks, households, and resolving
imported contacts. For administrators, see the deployment runbook; for status, see
`docs/PROJECT_STATUS.md`.

## Signing in
Client360 uses your firm's single sign-on. Open the app and sign in with your work account; you'll
land on the dashboard. Access is scoped by your role — you see the clients and actions your role
permits. Every action you take is recorded in the client's timeline and the audit log.

## Finding a client
- Use the **Search** box (name, email, phone, or city — two characters or more).
- Results open the **Client Profile** when the contact is linked to a canonical client; otherwise
  they open the source record. A client who appears in more than one system shows once.

## The Client Profile
The profile header shows the client's name, click-to-call/email contact line, and quick actions.
Tabs: **Overview**, **Timeline**, **Tasks**, **Documents**, **Notes**, **Activities**, **Calendar**,
**Relationships**, **Portfolio**.
- **Edit details** (header): correct the client's contact and address fields. Changes are audited and
  added to the timeline. (Name changes are handled separately.)
- **Timeline**: the complete, chronological record of everything that happened with this client.

## Notes
Open **Notes** from the profile. There are two kinds:
- **Permanent client note** — one enduring, editable note for lasting facts (preferences, family,
  planning context). Edit and **Save**; it persists.
- **Activity & communications feed** — append-only entries. Each entry is attributed to you and
  timestamped, and appears in the timeline. Entries are never overwritten.

## Logging communications
From the profile header, use **Log Call**, **Log Email**, or **Log Meeting** (or pick the type on the
Notes page). Enter a summary and, optionally, the **direction** (inbound/outbound). Save — the entry
appears in the activity feed with its type (and direction) and on the timeline.
- **Follow-up task:** expand *Also create a follow-up task* to create an assigned task at the same
  time.

## Tasks
- **Create** a task from the profile **Tasks** tab or the Notes follow-up option: title, priority,
  assignee (a staff member), and due date.
- Assignees are real staff users; the assignee shows the same on the Tasks tab and the profile.
- **Complete** a task with **Mark complete**.
- If you accidentally submit twice (double-click or browser back), Client360 will not create a
  duplicate task.

## Households
Open a **Household** to see its members and a roll-up: member count, aggregate household AUM, and open
tasks across all members. Add members from the household page. (Automatic household grouping is not
enabled in v1.0 — households are managed manually.)

## Resolving imported contacts (Match Review)
Imports create client records automatically when they're unambiguous. When a contact is ambiguous —
it could match more than one existing client, or shares contact details with another new contact — it
is held for a person to decide.
- Go to **Match Review → Unresolved single-source contacts**.
- For each, either **Link** it to the correct existing client, or **Create new client**.
- **Run promotion backfill** (button) promotes any straightforward contacts imported earlier.
- Client360 never merges client identities automatically — a person always decides ambiguous cases.

## Getting help / reporting a problem
Every screen and action carries a request id used in the audit log. When reporting an issue, note
what you did, the client, and the approximate time so support can trace it. Your firm's Client360
administrator handles access, environment, and data questions.
