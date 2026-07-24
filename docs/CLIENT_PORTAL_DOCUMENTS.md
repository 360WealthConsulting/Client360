# Client Portal Documents (Phase D.43)

How the portal exposes documents. The portal never owns document storage — it reads and serves from the
authoritative document platform. See [`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Listing
`GET /api/v1/portal/documents` returns the documents reachable through the account's grant scope
(`client_documents`). Only person-scoped documents the account is entitled to appear.

## Scoped download (file-security)
`GET /api/v1/portal/documents/{document_id}/download`:
1. resolves the document record (person, storage path, name, content type, archived flag);
2. treats a missing or archived document as **404**;
3. enforces `require_scope(principal, person_id=..., permission="documents")` — a scope denial returns 404
   (never discloses that the document exists);
4. verifies the file exists on the authoritative store before streaming;
5. streams via `FileResponse` with the original filename and content type, and records a low-cardinality
   `downloads` counter.

The download path performs the scope check **before** any file access, and never exposes the storage path
externally. It is gated behind the portal fork (requires a portal session) and reuses the authoritative
document store — there is no second document system.

## Uploads (delegated)
Client uploads occur through the existing document-request flow: `POST
/api/v1/portal/requests/{request_id}/upload` resolves the request, enforces `documents` scope, saves the
file through the authoritative `save_person_document`, and confirms the request upload
(`confirm_request_upload`). Staff approve via the internal request flow. The portal never writes documents
outside this delegated path. See [`CLIENT_PORTAL_REQUESTS.md`](CLIENT_PORTAL_REQUESTS.md).

## Visibility
Document fields (`documents.list`, `documents.download`, `documents.upload`) are declared `conditional` in
the visibility registry, requiring the `documents` grant permission and person scope. See
[`CLIENT_PORTAL_VISIBILITY_REGISTRY.md`](CLIENT_PORTAL_VISIBILITY_REGISTRY.md).

## References
`app/routes/portal.py` (`api_portal_document_download`, `api_request_upload`), `app/services/documents.py`,
`app/portal/service.py` (`confirm_request_upload`, `require_scope`), ADR-048.
