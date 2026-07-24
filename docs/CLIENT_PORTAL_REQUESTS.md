# Client Portal Requests & Client-Service Workflows (Phase D.43)

The portal surfaces governed client-service requests and action items. Every request is owned and completed
by an authoritative service — the portal is a delegated-action surface, never a workflow engine. See
[`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Document requests
Staff create document requests through the authoritative flow (`create_document_request`). Clients see
their open requests (`GET /api/v1/portal/requests`) and satisfy them by uploading through the delegated
document path (see [`CLIENT_PORTAL_DOCUMENTS.md`](CLIENT_PORTAL_DOCUMENTS.md)). Staff approve uploads
(`approve_request_upload`). The portal never invents request state.

## Action items
`GET /portal/action-needed` and `GET /api/v1/portal/exceptions` surface scope-guarded client action items
from the authoritative exception engine (`exception_engine.client_action_items`). Employer/organization
action items come from `exception_engine.employer_action_items` under the `benefits` grant. These are
read-only projections of authoritative exceptions; resolution happens in the owning service.

## Tasks & workflow steps
`GET /api/v1/portal/tasks` lists the client's workflow steps; `POST
/api/v1/portal/tasks/{step_id}/complete` delegates to `complete_client_task`, which advances the
authoritative workflow (`workflow_automation.complete_step`) under scope enforcement. The portal never
mutates workflow state directly.

## Appointment requests (delegated)
`POST /api/v1/portal/appointments/request` records a client's appointment request as a governed secure
message thread (`app/portal/appointments.py`), gated by `portal.appointments_enabled` and the client's
person scope. The advisor sees the thread and books the real meeting in the authoritative
`scheduling.service`. Upcoming appointments are read from the scheduling-owned `calendar_event` timeline
(`GET /api/v1/portal/appointments`). The portal never books a meeting directly.

## Consents & preferences
`GET/POST /api/v1/portal/consents` (+ `/withdraw`) record versioned consent / electronic-delivery
decisions via the `portal_consents` ledger (`app/portal/consent.py`), each audited. See
[`CLIENT_PORTAL_GOVERNANCE.md`](CLIENT_PORTAL_GOVERNANCE.md).

## References
`app/routes/portal.py`, `app/portal/appointments.py`, `app/portal/consent.py`,
`app/services/exception_engine`, `app/services/workflow_automation.py`, `app/services/scheduling`, ADR-048.
