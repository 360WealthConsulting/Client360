# OCR Production Deployment — Windows Server (PR 5B)

The OCR **service** (schema, status, retry/reprocess, search, audit, workspace display) shipped in PR 5A.
PR 5B adds the concrete **extraction backend** (`app/services/ocr_backend.py`) and the **operational
runner** (`app/jobs/ocr_runner.py`). OCR enriches the canonical document (ADR-072) — no second document
system, no OCR pages, no schema change (Alembic head remains `dococr01`).

The backend imports its heavy dependencies lazily, so the app and CI run without them; a Windows Server
becomes OCR-capable only after the steps below.

---

## Engine & dependencies

| Component | Purpose |
|-----------|---------|
| **Tesseract OCR 5.x** (UB-Mannheim build) | image/scanned OCR engine |
| **Poppler for Windows** | PDF → image rendering (`pdf2image` backend) for scanned pages |
| **pypdf** | PDF selectable text-layer extraction (avoids OCR when text already exists) |
| **pdf2image** | renders image-only PDF pages for Tesseract |
| **pytesseract** | Python wrapper around the Tesseract binary |
| **Pillow (PIL)** | image loading incl. multi-page TIFF frames |

Extraction strategy: PDFs use the **text layer** first; only image-only pages fall back to Tesseract.
PNG/JPG/JPEG/TIFF (incl. multi-page TIFF) go straight to Tesseract. Engine + version are recorded per
document (`document_ocr.engine`, e.g. `pdf-text-layer` or `tesseract 5.3.1`).

---

## 1. Install Tesseract OCR

Install the UB-Mannheim Tesseract build (includes the English language data):

```
winget install --id UB-Mannheim.TesseractOCR -e
```

Default path: `C:\Program Files\Tesseract-OCR\tesseract.exe`. (Alternatively run the installer from
https://github.com/UB-Mannheim/tesseract/wiki and keep the "English" language data checkbox.)

## 2. Install the PDF rendering dependency (Poppler)

Download the latest Poppler-for-Windows release, extract to `C:\Poppler`, so that
`C:\Poppler\Library\bin\pdftoppm.exe` exists:

```
powershell -Command "Invoke-WebRequest -Uri https://github.com/oschwartz10612/poppler-windows/releases/latest/download/Release-24.08.0-0.zip -OutFile C:\poppler.zip"
powershell -Command "Expand-Archive -Path C:\poppler.zip -DestinationPath C:\Poppler -Force"
```

(Adjust the release version to the current one; the `poppler-windows` project publishes versioned zips.)

## 3. Install the required Python packages

From the application directory, in the same virtual environment that runs Client360:

```
.venv\Scripts\python -m pip install pypdf pdf2image pytesseract Pillow
```

## 4. Executable paths & environment variables — `app\.env`

Append to `app\.env` (values must match the install locations above):

```
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
POPPLER_PATH=C:\Poppler\Library\bin
CLIENT360_OCR_BATCH_SIZE=50
```

`TESSERACT_CMD` points `pytesseract` at the binary; `POPPLER_PATH` is passed to `pdf2image`. Both are
read at runtime by `app/services/ocr_backend.py`.

## 5. Preflight / health check

Confirms every library imports and the Tesseract binary is reachable **before** processing anything:

```
.venv\Scripts\python -m app.services.ocr_backend --preflight
```

Expect `OCR backend preflight: OK` and a printed `engine: tesseract 5.3.x`. A non-zero exit means a
library or the binary is missing (the output lists exactly which).

## 6. Apply database migrations (head `dococr01`)

```
.venv\Scripts\python -m app.deploy.migrate
.venv\Scripts\alembic current
```

`alembic current` must report `dococr01` (the `document_ocr` table from PR 5A). PR 5B adds **no** new
migration.

## 7. One-document OCR validation

Pick a real canonical document id (see Validation SQL below), then force-process just that document:

```
.venv\Scripts\python -m app.jobs.ocr_runner --mode reprocess --document-id 12345
```

Expect `status: completed` and `completed: 1`. Verify with the Validation SQL, then open the client's
**Documents** tab — the row shows `completed · <date>` and `🔍 searchable`.

## 8. Initial batch OCR (whole corpus)

Resumable; safe to re-run. Processes every not-yet-attempted document to completion in batches:

```
.venv\Scripts\python -m app.jobs.ocr_runner --mode initial --batch-size 50
```

## 9. Incremental OCR (newly ingested documents)

```
.venv\Scripts\python -m app.jobs.ocr_runner --mode incremental --batch-size 50
```

## 10. Retry failed documents

One batch of failures per invocation (attempts spread across runs; capped by `--max-attempts`):

```
.venv\Scripts\python -m app.jobs.ocr_runner --mode retry --max-attempts 3
```

## 11. Force reprocess

Content-changed / not-yet-complete documents firm-wide:

```
.venv\Scripts\python -m app.jobs.ocr_runner --mode reprocess
```

Specific documents (re-OCR even if already completed and unchanged):

```
.venv\Scripts\python -m app.jobs.ocr_runner --mode reprocess --document-id 12345 --document-id 12346
```

---

## Validation SQL

```sql
-- Overall OCR state across the canonical corpus
SELECT status, COUNT(*) FROM document_ocr GROUP BY status ORDER BY status;

-- A specific document's OCR result (engine, page/char counts, completion time)
SELECT d.id, d.original_name, o.status, o.engine, o.page_count, o.char_count, o.ocr_completed_at
FROM documents d JOIN document_ocr o ON o.document_id = d.id
WHERE d.id = 12345;

-- Failures still eligible for retry
SELECT document_id, attempts, last_error FROM document_ocr
WHERE status = 'failed' AND attempts < 3 ORDER BY document_id;

-- Confirm search indexing: documents whose extracted text contains a term
SELECT document_id FROM document_ocr
WHERE status = 'completed' AND text ILIKE '%dividends%';

-- Audit trail of OCR runs
SELECT occurred_at, action, metadata FROM audit_events
WHERE action = 'document.ocr_run' ORDER BY occurred_at DESC LIMIT 20;
```

---

## Scheduling & operations

- **Runners**: `python -m app.jobs.ocr_runner --mode {initial|incremental|retry|reprocess}`.
- **Batch size & progress**: `--batch-size` (default 50); each batch logs
  `candidates/completed/failed/skipped` at INFO to the application log.
- **Resumable**: every mode is idempotent — completed, unchanged documents are skipped, so an
  interrupted run is safely resumed by re-invoking the same command.
- **Concurrent-run protection**: each sweep takes a PostgreSQL **session advisory lock**
  (`pg_try_advisory_lock`); a second concurrent runner returns `status: locked` and does nothing, so a
  manual run never collides with the scheduled job.
- **In-process scheduler**: when the app runs the APScheduler (`app/jobs/scheduler.py`), the
  `ocr-incremental-sweep` job runs every 30 min and `ocr-retry-sweep` every 60 min
  (`max_instances=1, coalesce=True`). No extra setup is needed if the app scheduler is enabled.
- **Windows Scheduled Task** (alternative to the in-process scheduler — recommended for a dedicated OCR
  cadence). Incremental every 30 minutes:

  ```
  schtasks /Create /TN "Client360 OCR Incremental" /SC MINUTE /MO 30 ^
    /TR "\"C:\Client360\app\.venv\Scripts\python.exe\" -m app.jobs.ocr_runner --mode incremental" ^
    /RU "DOMAIN\svc_client360" /RL LIMITED

  schtasks /Create /TN "Client360 OCR Retry" /SC HOURLY ^
    /TR "\"C:\Client360\app\.venv\Scripts\python.exe\" -m app.jobs.ocr_runner --mode retry" ^
    /RU "DOMAIN\svc_client360" /RL LIMITED
  ```

  The advisory lock makes it safe if a scheduled task overlaps a long-running initial sweep.
- **Logging & operational validation**: watch the application log for `OCR <mode> batch N:` lines; run
  the Validation SQL after each sweep to confirm the `completed` count climbs and `failed` stays bounded.

---

## Operational summary

| Action | Command |
|--------|---------|
| Preflight | `python -m app.services.ocr_backend --preflight` |
| Migrate | `python -m app.deploy.migrate` (head `dococr01`) |
| One document | `python -m app.jobs.ocr_runner --mode reprocess --document-id <id>` |
| Initial batch | `python -m app.jobs.ocr_runner --mode initial --batch-size 50` |
| Incremental | `python -m app.jobs.ocr_runner --mode incremental --batch-size 50` |
| Retry | `python -m app.jobs.ocr_runner --mode retry --max-attempts 3` |
| Force reprocess | `python -m app.jobs.ocr_runner --mode reprocess [--document-id <id>]` |
