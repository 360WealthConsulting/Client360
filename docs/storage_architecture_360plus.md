# 360Plus Storage Architecture — Audit & Proposed Permanent Layout

**Status:** design proposal (audit + target layout). **No production changes.** This document does not
rename any environment variable, database value, source-system identifier, or Windows service. It proposes
a permanent *data* layout separated from *application code*, reachable entirely through the **existing**
`CLIENT360_*` / `*_ROOT` environment variables (so adoption is a configuration change, not a code change).

Scope: TaxDome source, SharePoint staging, canonical documents, manual-review files, database/backups,
logs, temp. Every path below is cited to `file:line`.

---

## Part A — Audit of existing configurable paths

Env-var precedence is left→right (first non-empty wins). Paths are shown as they appear in code.

### 1. TaxDome / Drake source (read-only source drives)
| Purpose | Env var(s) → default | Where read |
|---|---|---|
| TaxDome drive (live importer) | `TAXDOME_DRIVE_ROOT` → `Z:\` | `app/importers/taxdome_drive.py:62` (`DEFAULT_SOURCE_ROOT`), used `:338` |
| TaxDome source (migration framework) | `CLIENT360_TAXDOME_SOURCE_ROOT` → `Z:\` | `app/services/migration/config.py:79` |
| TaxDome one-time migration source | `CLIENT360_TAXDOME_MIGRATION_ROOT` → OneDrive backup path | `app/services/migration/config.py:90-92` |
| Drake export source | `DRAKE_EXPORT_ROOT` → `DRAKE_DRIVE_ROOT` → `D:\DrakeExport` | `app/importers/drake.py:51`, used `:134` |
| Drake program roots (artifact counts only) | `CLIENT360_DRAKE_ROOTS` (`;`-sep) → `C:\DRAKE21..25`, … | `app/services/migration/config.py:29-33, 86-87` |

*Note:* TaxDome source has **two** independent definitions (live importer vs migration framework) that do
not share an env var.

### 2. SharePoint staging (downloaded/staged files, `_delta`/`_staging`)
| Purpose | Env var(s) → default | Where read |
|---|---|---|
| Connector staging root (Graph download target) | `CLIENT360_SHAREPOINT_STAGING_ROOT` → `C:\Client360\Data\Staging\SharePoint` | connector `DEFAULT_STAGING_ROOT` (`sharepoint_content.py:63-64`) |
| Ingestion-side staging (adapter view) | `CLIENT360_SHAREPOINT_SOURCE_ROOT` → `CLIENT360_SHAREPOINT_DOCUMENT_ROOT` → `C:\Client360\Data\Documents\SharePoint\_staging` | `microsoft_ingestion.py:190-194` (`_staging_root`) |
| Real-run staging root (co-locate temp+dest) | connector `DEFAULT_/STAGING_/CONTENT_ROOT`, then `CLIENT360_SHAREPOINT_STAGING_ROOT`, then `_staging_root()` | `microsoft_ingestion.py:197-209` (`_connector_staging_root`) |
| Delta download subdir | `<staging>/_delta` | `microsoft_ingestion.py:989` |
| Delta checkpoints | **DB, not filesystem**: `microsoft_drives.canonical_delta_link` | `microsoft_ingestion.py:771-799` |

*Note:* `CLIENT360_SHAREPOINT_SOURCE_ROOT` and `CLIENT360_SHAREPOINT_DOCUMENT_ROOT` are **overloaded** —
each means one thing to the migration framework and another (a staging fallback) to ingestion.

### 3. Canonical documents (durable local copies / destination roots)
| Purpose | Env var(s) → default | Where read |
|---|---|---|
| TaxDome canonical dest | `CLIENT360_TAXDOME_DOCUMENT_ROOT` → `C:\Client360\Data\Documents\TaxDome` | `taxdome_drive.py:63-64`, storage set `:515` |
| Drake canonical dest | `CLIENT360_DRAKE_DOCUMENT_ROOT` → `C:\Client360\Data\Documents\Drake` | `drake.py:52-53`, `:222` |
| SharePoint canonical dest | `CLIENT360_SHAREPOINT_DOCUMENT_ROOT` → `C:\Client360\Data\Documents\SharePoint` | `sharepoint.py:51-52`, `:356` |
| Migration framework document root | `CLIENT360_DOCUMENT_ROOT` → `C:\Client360\Data\Documents` | `migration/config.py:84` |
| **Authoritative** repository (relocation dest) | `CLIENT360_MIGRATION_DEST_ROOT` → `D:\Client360Data` | `migration/config.py:68,93`; relocation repoints `storage_uri` here (`relocation.py:312-315`) |
| Repository logical areas | code enum (Objects/Staging/Archive/Vault/Derivatives/Index/Audit/Exports) under the dest root | `migration/storage.py:14-35` |
| Scanner drop-folder | `CLIENT360_SCANNER_ROOT` → `C:\Shares\Scans` | `migration/config.py:83` |

**`storage_provider` values written to `documents`** (source-system identifiers — do NOT rename): `Client360
Local` (importer local copy: `taxdome_drive.py:59`, `drake.py:49`, `sharepoint.py:49`), `Client360
Repository` (after relocation: `relocation.py:315`), `local` (Vault upload: `document_library.py:88`), plus
`SharePoint` / `TaxDome Drive` / `Wealthbox` / `Schwab` / `AssetMark`.

### 4. Manual-review / exception files
**No dedicated configurable path exists.** Exceptions are carried in the DB (`exception_engine.py`,
`document_review_queue.py`) and as per-run migration artifacts (`manifest.json`, `reconciliation.csv`,
`exceptions.csv`, `summary.txt`) under `<CLIENT360_MIGRATION_ROOT="Migration">/reports/<source>/<mode>/<stamp>`
(`migration/base.py:16-17, 163`). No `MANUAL_REVIEW` / `_quarantine` env var or path is defined.

### 5. Database + backups
| Purpose | Env var → default | Where |
|---|---|---|
| DB connection | `DATABASE_URL` (**no default — required**) | `app/db.py:8-13`; `app/config.py:222-225` |
| `.env` file | `app/.env` | `deploy/cli.py:47`, `migration/config.py:20` |
| Pre-migration backup | `CLIENT360_BACKUP_CMD` (a command, unset→warn) | `deploy/migrate.py:45-52` |

**No filesystem backup directory is configured in code** — backup is delegated to an external command.

### 6. Logs
| Purpose | Env var → default | Where |
|---|---|---|
| Level / format | `LOG_LEVEL`→`INFO`, `LOG_FORMAT`→`plain` | `observability/logging.py:56,63` |
| Rotating file-log dir | `LOG_DIR` (unset→stderr) | `observability/logging.py:83-96` |
| Windows service logs | `--log-dir` → `C:\Client360\logs` | `deploy/service.py:32,86` |

### 7. Temp / scratch
- TaxDome & connector & StorageService writes use a `.part` temp **in the destination dir** then atomic
  `os.replace` (`taxdome_drive.py:177-191`, `sharepoint_content.py:260-268`, `migration/storage.py`).
- OCR: no configurable temp; `pdf2image` uses the system temp (`ocr_backend.py:229-236`).
- OCR tool paths (configurable, not storage): `TESSERACT_CMD`, `POPPLER_PATH`, `OCR_PAGE_TIMEOUT_SECONDS`,
  `OCR_DOCUMENT_TIMEOUT_SECONDS` (`ocr_backend.py`).

---

## Part B — Problems found

1. **Application code and data share the `C:\Client360` root.** The Windows service `AppDirectory`, the venv
   (`C:\Client360\.venv`), logs (`C:\Client360\logs`; `deploy/service.py:32,82,86`), AND the default
   canonical corpus (`C:\Client360\Data\Documents\{TaxDome,Drake,SharePoint}`) and SharePoint staging all
   default under one tree. Backups, disk-full, and redeploys of code can therefore endanger data.
2. **Two subsystems disagree on where canonical bytes live.** Live importers default to
   `C:\Client360\Data\Documents\…`; migration/relocation moves the authoritative corpus to `D:\Client360Data`
   and repoints `storage_uri` there (provider `Client360 Repository`). New documents land on `C:` until a
   relocation pass moves them to `D:`.
3. **Overloaded env vars** (`CLIENT360_SHAREPOINT_SOURCE_ROOT`, `CLIENT360_SHAREPOINT_DOCUMENT_ROOT`) mean
   one variable set for a single intent silently changes another subsystem's path resolution.
4. **Docs vs resolved value drift:** relocation/naming docstrings say `D:\Client360\Content`
   (`relocation.py:3`, `naming.py:4,23`) but the configured default is `D:\Client360Data` (`config.py:68`).
5. **No first-class manual-review / quarantine storage location.**

---

## Part C — Proposed permanent 360Plus layout (data separated from code)

Principle: **code and runtime on the app volume; ALL durable/mutable data on a dedicated data volume**, so
the corpus is backed up and grows independently of code deploys. Achieved purely by pointing the **existing**
env vars at the new roots — **no code or env-var-name changes required.** Filesystem folder names may use the
360Plus brand (folders are not the technical identifiers the instructions protect); the underlying env-var
names and DB `storage_provider` values stay exactly as they are.

```
# --- application volume (code + runtime only; disposable/redeployable) ---
C:\360Plus\
  app\                     # source tree (was C:\Client360)
  .venv\                   # Python venv
  logs\                    # LOG_DIR / service --log-dir
  tools\                   # Tesseract, Poppler (TESSERACT_CMD, POPPLER_PATH)

# --- data volume (durable corpus + operational data; independently backed up) ---
D:\360PlusData\
  Documents\               # canonical durable copies (authoritative repository)
    TaxDome\               # CLIENT360_TAXDOME_DOCUMENT_ROOT
    Drake\                 # CLIENT360_DRAKE_DOCUMENT_ROOT
    SharePoint\            # CLIENT360_SHAREPOINT_DOCUMENT_ROOT
  Repository\              # CLIENT360_MIGRATION_DEST_ROOT (relocation target; provider "Client360 Repository")
    Objects\ Staging\ Archive\ Vault\ Derivatives\ Index\ Audit\ Exports\   # migration/storage.py areas
  Staging\
    SharePoint\            # CLIENT360_SHAREPOINT_STAGING_ROOT (temp+dest same volume -> no WinError 17)
  ManualReview\            # NEW: quarantine/manual-review working files (see Part D)
  Migration\              # CLIENT360_MIGRATION_ROOT (run artifacts: manifest/reconciliation/exceptions)
  Temp\                    # scratch (optional dedicated temp for OCR/pdf2image)
  Backups\                # DB dumps written by CLIENT360_BACKUP_CMD (see Part D)

# --- read-only source drives (unchanged; owned by external systems) ---
Z:\                        # TAXDOME_DRIVE_ROOT (read-only)
D:\DrakeExport\            # DRAKE_EXPORT_ROOT (read-only)
C:\Users\...\OneDrive...\  # CLIENT360_SHAREPOINT_SOURCE_ROOT (migration source, read-only)
```

### Env-var → target mapping (configuration only; names unchanged)
| Existing env var | Proposed value |
|---|---|
| `CLIENT360_TAXDOME_DOCUMENT_ROOT` | `D:\360PlusData\Documents\TaxDome` |
| `CLIENT360_DRAKE_DOCUMENT_ROOT` | `D:\360PlusData\Documents\Drake` |
| `CLIENT360_SHAREPOINT_DOCUMENT_ROOT` | `D:\360PlusData\Documents\SharePoint` |
| `CLIENT360_SHAREPOINT_STAGING_ROOT` | `D:\360PlusData\Staging\SharePoint` |
| `CLIENT360_MIGRATION_DEST_ROOT` | `D:\360PlusData\Repository` |
| `CLIENT360_DOCUMENT_ROOT` | `D:\360PlusData\Documents` |
| `CLIENT360_MIGRATION_ROOT` | `D:\360PlusData\Migration` |
| `LOG_DIR` / service `--log-dir` | `C:\360Plus\logs` |
| `TESSERACT_CMD` / `POPPLER_PATH` | `C:\360Plus\tools\...` |
| `TAXDOME_DRIVE_ROOT`, `DRAKE_EXPORT_ROOT`, `CLIENT360_SHAREPOINT_SOURCE_ROOT` (source) | unchanged |

---

## Part D — Recommended additions (design only; not implemented here)

1. **First-class manual-review storage** — add an env var (e.g. `CLIENT360_MANUAL_REVIEW_ROOT` →
   `D:\360PlusData\ManualReview`) and route quarantined/unresolvable staged files there instead of leaving
   them only in the staging tree. Pairs with the existing DB review queues (see the pipeline-readiness doc).
2. **Explicit backup directory** — the code delegates backups to `CLIENT360_BACKUP_CMD` with no known target
   dir. Standardize on `D:\360PlusData\Backups` for the dump the migration APPLY guard already requires
   (`relocation.py:256-257`).
3. **De-overload the two SharePoint env vars** — give ingestion staging its own variable distinct from the
   migration OneDrive-source variable, so setting one intent cannot move another subsystem's path. (Kept as
   a proposal to honor "do not rename env vars" without an approved migration window.)
4. **Reconcile the docstring path** (`D:\Client360\Content`) to the resolved default so operators aren't
   misled. Documentation-only.

**Adoption is non-destructive:** because every root above is already an env var, moving to this layout is a
`.env` edit + a data copy + (optionally) a one-time relocation pass — no code change and no rename of any
protected identifier.
