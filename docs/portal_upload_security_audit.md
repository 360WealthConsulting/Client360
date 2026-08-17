# Client Portal — Upload Security Audit

Scope: the **client-facing** upload path a portal user reaches through the browser
(`POST /portal/upload`) and the portal JSON APIs (`POST /api/portal/documents`,
`POST /api/portal/requests/{id}/upload`). All of these delegate to
`app/portal/vault_documents.py :: upload_document`, which stores bytes through
`app/services/vault/storage.py :: save_stream`.

Legend: ✅ present · 🟡 partial/opt-in · ⛔ missing (gap) · 🔒 fixed in this pass.

| Control | Status | Where / notes |
|---|---|---|
| **Extension allow-list** | ✅ | `storage.validate_extension` → `ALLOWED_EXTENSIONS = {pdf, docx, xlsx, csv, jpg, jpeg, png, txt}`. Anything else raises `VaultStorageError`. |
| **File-size limit** | ✅ | `storage.MAX_UPLOAD_BYTES = 50 MB`, enforced **while streaming** (aborted + partial file deleted if exceeded). Empty files rejected. |
| **Filename sanitization** | ✅ | The original filename is **never** used to build a path. Storage key is an internally generated `shard/uuid.ext`. Original name kept only as metadata. |
| **Path traversal** | ✅ | `storage.resolve_path` accepts only the exact `^[0-9a-f]{2}/[0-9a-f]{32}\.[0-9a-z]{1,8}$` shape **and** re-checks the resolved path stays within `storage_root()`. A crafted `../../` filename cannot escape. |
| **Magic-byte / content sniffing** | 🔒 | **Was missing** (only the extension was trusted). Added `storage.content_matches_extension` + opt-in `save_stream(..., verify_content=True)`, enabled on the **untrusted client path** in `upload_document`. A file whose leading bytes don't match its claimed type (e.g. HTML/EXE renamed to `.pdf`) is now rejected before it is kept. Signatures: `%PDF` (within first 1 KB), PNG `\x89PNG…`, JPEG `\xff\xd8\xff`, docx/xlsx ZIP `PK\x03\x04` (+`PK\x05\x06`/`PK\x07\x08`). `csv`/`txt` are intentionally **not** sniffed (no reliable signature; legit UTF-16 text carries NUL bytes). |
| **MIME/content-type validation** | 🟡 | The routes receive the browser-supplied `content_type` but it is advisory and **not** trusted for authorization; `mime_type` is stored as `NULL`. Content is now validated by bytes (above), which is stronger than trusting the client-declared MIME. No further action needed; a future enhancement could persist a server-derived MIME for display. |
| **Quarantine / pending-review** | ✅ | Client uploads land as `status='uploaded'`, `security_classification='client_upload'`, `uploaded_by_portal_account_id` set. They are **not official** and are **not** served as an approved download until an employee approves them through the Vault RBAC (`vault.manage`). Effectively a quarantine-before-trust workflow. |
| **Duplicate / hash behavior** | ✅ (by design) | SHA-256 is computed while streaming and stored as `checksum_sha256`. No dedup/collision handling — each upload is a distinct document version. This is deliberate (client re-uploads must not silently merge); not a security gap. Hash is available for later integrity / AV correlation. |
| **AV / malware scanning** | ⛔ (gap — vendor) | No antivirus/malware scan hook exists. **Not stubbed** (a fake scanner would give false assurance). The pending-review quarantine limits blast radius (nothing is served as approved without staff action), but a genuine AV integration is required before real clients. See "Recommended AV hook" below. |
| **Encrypted / password-protected documents** | ⛔ (gap — decision) | Not detected or rejected. A password-protected PDF/Office file still has a valid signature, so it passes sniffing and lands as pending. Staff will simply be unable to open it in review. Decision needed: reject at upload (needs format-aware inspection) vs. handle in the review workflow. Currently handled implicitly by the human review step. |

## What was fixed in this pass (code)

- `app/services/vault/storage.py`
  - `content_matches_extension(ext, header) -> bool` — pure, unit-tested predicate.
  - `save_stream(..., verify_content=False)` — opt-in leading-byte validation of the first chunk; trusted/import/staff callers keep the default (behavior unchanged).
- `app/portal/vault_documents.py` — `upload_document` calls `save_stream(..., verify_content=True)` (client uploads are untrusted).
- Tests: `tests/test_upload_content_validation.py` (predicate + client-path rejection/acceptance + staff-path unchanged). Portal upload fixtures updated to use genuine content (`tests/_portal_util.sample_upload`).

## Recommended AV hook (not implemented — needs a vendor)

The clean insertion point is **after** `save_stream` returns a `storage_key`/`checksum_sha256` and
**before** the document is marked approvable — i.e. in `upload_document`, or as an async step keyed on
the pending `status='uploaded'`. Contract (illustrative only, no vendor chosen):

```
scan_result = av.scan(storage.resolve_path(stored["storage_key"]))   # PROVIDER DECISION
if scan_result.infected:
    storage.delete(stored["storage_key"]); raise VaultStorageError("File failed a security scan.")
```

Options to weigh (no decision made): ClamAV (self-hosted, on-box), a cloud scanning API, or scanning at
the storage layer. Because approval is already human-gated, AV can also run as a background job that
blocks approval rather than blocking upload — a throughput vs. immediacy tradeoff for the vendor decision.

## Residual gaps → owner action

1. **AV/malware scan** — vendor decision + integration (hook point identified above).
2. **Encrypted/password-protected document policy** — product decision (reject at upload vs. handle in review).
3. **Legacy path** `POST /api/v1/portal/requests/{id}/upload` (in `app/routes/portal.py`) still uses
   `app/services/documents.py :: save_person_document`, which is materially weaker than the vault path:
   **no extension allow-list, no size cap, and no content sniffing** (it does sanitize the stored
   filename and is person-scope checked). The primary browser upload (`POST /portal/upload`) and the
   portal JSON APIs use the hardened vault path, so this endpoint is not on the main client flow —
   but it is reachable by an authenticated client. **Recommendation:** route it through
   `upload_document` (preferred — one hardened backend) or apply the same allow-list + size cap +
   `verify_content` there. Left as a documented gap in this pass to avoid changing that endpoint's
   existing versioning/`confirm_request_upload` semantics without a dedicated change.

None of the above blocks a **staging/test-client** exercise; all are prerequisites before a **real** client with sensitive documents.
