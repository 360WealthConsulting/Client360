# Microsoft Document Intelligence

Client360 stores metadata and links for files managed by OneDrive and SharePoint.
It does not download or duplicate Microsoft-managed file content.

## Discovery and synchronization

- The signed-in user's drives are discovered through `/me/drives`.
- SharePoint libraries are discovered for the comma-separated site IDs in
  `MICROSOFT_SHAREPOINT_SITE_IDS` through `/sites/{site-id}/drives`.
- Every drive is synchronized with Microsoft Graph's driveItem delta feed.
- Pagination follows `@odata.nextLink`; the final `@odata.deltaLink` is retained
  for the next scheduled run.
- Deleted drive items are marked deleted locally and hidden from workspaces.
- Short-lived preauthenticated download URLs are discarded rather than stored.

Microsoft documents the relevant APIs at:

- https://learn.microsoft.com/en-us/graph/api/drive-list?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0

## Matching

The matcher evaluates these signals in order:

1. Active rules in `microsoft_document_matching_rules`, ordered by priority.
2. Client email in created-by or modified-by metadata.
3. Client email embedded in the filename, path, or metadata.
4. A normalized client full name in the folder path.

Supported rule types are `filename`, `folder`, `email`, and `metadata`. Rules
contain a case-insensitive substring pattern and a canonical `person_id`.
Ambiguous automatic matches remain pending for manual review.

## Review and client display

Unmatched files appear at `/microsoft365/documents-review`. Reviewers can link a
file to an existing person or ignore it. Contacts are never created automatically.
Matched metadata appears beside uploaded files in the Client Workspace and the
dedicated client Documents page. Links always open the original Microsoft file.

## Operations

- Scheduled sync runs every 30 minutes.
- Controlled sync: `POST /microsoft365/documents/sync`.
- Command-line sync: `python -m app.jobs.microsoft_document_sync`.
- Apply migrations before deployment: `alembic upgrade head`.
- Reconnect Microsoft 365 if the delegated access token has expired.

If Sprint 8 migrations merge first, rebase this branch and update the migration
parent before merging so the repository retains a single Alembic head.
