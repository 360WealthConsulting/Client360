# 360Plus Storage Migration Runbook — move persistent data off `C:\Client360` to `D:\360PlusData`

**Purpose:** relocate all persistent 360Plus data from the app volume (`C:\Client360`) to a dedicated data
volume rooted at `D:\360PlusData`, so code and data are separated and the corpus is backed up/grows
independently of code deploys. **Copy-first, verify, cutover, then (much later) reclaim** — never move-first.

**Preconditions (hard):**
- Run ONLY after the current SharePoint OCR/import job has fully completed. Do **not** start while it runs.
- A verified, non-empty PostgreSQL backup exists (`pg_dump`) taken immediately before any DB write.
- `release/0.13.0` (with `app/services/storage_paths.py`, commit `0fa57d6`) is deployed **before** cutover so
  `CLIENT360_DATA_ROOT` takes effect. (Fallback without deploy: set the per-source vars directly — see §3.)
- Maintenance window: the Windows service `Client360` will be stopped during cutover.

**Authoritative-location decision (resolves the audit finding):**
The single per-document source of truth is **`documents.storage_uri`** in PostgreSQL — it is what OCR,
serving, and every consumer read. The *root* disagreement (importers → `C:\Client360\Data\Documents`,
relocation repository → `D:\Client360Data`) is resolved by rooting **both** under the data volume:
- importer "Client360 Local" copies → `D:\360PlusData\Documents\{TaxDome,Drake,SharePoint}`
- curated "Client360 Repository" (relocation target) → `D:\360PlusData\Repository`
After cutover, every `documents.storage_uri` is under `D:\360PlusData`, and no new bytes ever land on `C:`.
The DB `storage_provider` values (`Client360 Local` / `Client360 Repository`) are **unchanged** — they are
source-system identifiers, not paths.

---

## 1. Current source paths (audit result)

| Category | Current path(s) | DB / provider |
|---|---|---|
| Canonical docs — TaxDome | `C:\Client360\Data\Documents\TaxDome` | `storage_provider="Client360 Local"` |
| Canonical docs — Drake | `C:\Client360\Data\Documents\Drake` | `Client360 Local` |
| Canonical docs — SharePoint | `C:\Client360\Data\Documents\SharePoint` | `Client360 Local` |
| Curated repository | `D:\Client360Data` (Objects/Staging/Archive/Vault/…) | `Client360 Repository` |
| SharePoint staging | `C:\Client360\Data\Staging\SharePoint` + `…\Documents\SharePoint\_staging` (+ `\_delta`) | (staging only, not canonical) |
| TaxDome source | `Z:\` (read-only) | — |
| Drake source | `D:\DrakeExport` (read-only) | — |
| Manual-review files | none (DB-only queues + per-run migration `reports\…\exceptions.csv`) | — |
| Database | `postgresql://localhost/client360` (`DATABASE_URL`) | — |
| Backups | via `CLIENT360_BACKUP_CMD` (no fixed dir today) | — |
| Logs | `C:\Client360\logs` (`LOG_DIR` / service `--log-dir`) | — |
| Temp | in-destination `.part` files (no dedicated temp); OCR uses system temp | — |

## 2. Target paths under `D:\360PlusData`

```
D:\360PlusData\
  Documents\{TaxDome,Drake,SharePoint}   # canonical local copies (Client360 Local)
  Repository\                            # curated relocation target (Client360 Repository)
  Staging\SharePoint\                    # SharePoint staging (+ _delta)
  ManualReview\                          # (future) quarantine/manual-review working files
  Migration\                             # migration run artifacts (manifest/reconciliation/exceptions)
  Backups\                               # DB dumps
  Temp\                                  # optional scratch
C:\360Plus\logs\                          # logs stay on app volume (code, not data)
```
Read-only source drives (`Z:\`, `D:\DrakeExport`, the OneDrive SharePoint source) are **not** moved.

## 3. Environment/config values to change (in `app\.env`) and the code that consumes them

**Recommended (single base — requires release/0.13.0 deployed):**
| Variable | New value | Consumed by (file) |
|---|---|---|
| `CLIENT360_DATA_ROOT` | `D:\360PlusData` | `app/services/storage_paths.py` → importers' `DEFAULT_DESTINATION_ROOT` (`taxdome_drive.py`, `drake.py`, `sharepoint.py`) + `microsoft_ingestion._staging_root` |
| `CLIENT360_MIGRATION_DEST_ROOT` | `D:\360PlusData\Repository` | `app/services/migration/config.py:93` (`migration_dest_root`) → `relocation.py` |
| `CLIENT360_SHAREPOINT_STAGING_ROOT` | `D:\360PlusData\Staging\SharePoint` | connector `DEFAULT_STAGING_ROOT`; `microsoft_ingestion._connector_staging_root` |
| `LOG_DIR` (+ service `--log-dir`) | `C:\360Plus\logs` | `observability/logging.py`; `deploy/service.py` |

**Fallback (no code deploy — set per-source vars directly):** `CLIENT360_TAXDOME_DOCUMENT_ROOT=D:\360PlusData\Documents\TaxDome`,
`CLIENT360_DRAKE_DOCUMENT_ROOT=D:\360PlusData\Documents\Drake`, `CLIENT360_SHAREPOINT_DOCUMENT_ROOT=D:\360PlusData\Documents\SharePoint`
(consumed at `taxdome_drive.py`, `drake.py`, `sharepoint.py` `DEFAULT_DESTINATION_ROOT`) plus the staging + migration vars above.

**Do NOT change:** any env-var name, `DATABASE_URL`, the Windows service name `Client360`, source drive paths,
or `storage_provider` values.

## 4. Disk-space headroom (STOP if not met)

Require on `D:` **before** copying: `free_D ≥ (bytes to copy) × 1.10` (10% margin) **plus** expected growth
until the C: originals are reclaimed. Get the exact byte total from the relocation PREVIEW (read-only,
computes `relocatable_bytes`, `relocatable_gb`, and `fits_with_10pct_margin` — `relocation.py:155-161`) and
from a robocopy `/L` dry run of the document + staging trees. **GO only if** free space ≥ that total and the
preview reports `fits_with_10pct_margin = true`.

---

## 5. Procedure (copy-first) with STOP/GO checkpoints, PowerShell, and durations

> Set once at the top of the PowerShell session:
> ```powershell
> $Src   = "C:\Client360\Data\Documents"
> $Dst   = "D:\360PlusData\Documents"
> $Stg   = "C:\Client360\Data\Staging\SharePoint"
> $DstStg= "D:\360PlusData\Staging\SharePoint"
> $Repo  = "D:\Client360Data"
> $DstRepo="D:\360PlusData\Repository"
> $DB    = "client360"
> $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
> ```

### Phase 0 — Pre-flight (read-only). Duration: ~5–15 min.
```powershell
# job must be finished; confirm no python OCR/import process is running
Get-Process python -ErrorAction SilentlyContinue | Select Id,StartTime,Path
# free space + source size (dry run gives file/byte totals without copying)
Get-PSDrive D | Select Used,Free
robocopy $Src $Dst /E /L /NFL /NDL /NP | Select-String "Bytes :","Files :"
robocopy $Stg $DstStg /E /L /NFL /NDL /NP | Select-String "Bytes :","Files :"
# baseline DB counts by provider (read-only)
psql -d $DB -c "SELECT storage_provider, count(*), sum(size_bytes) FROM documents GROUP BY 1 ORDER BY 1;"
```
**STOP/GO #1:** proceed only if the job is done, free_D ≥ total×1.10, and you recorded the file/byte totals.

### Phase 1 — Backup the database. Duration: ~2–20 min (DB size dependent).
```powershell
New-Item -ItemType Directory -Force D:\360PlusData\Backups | Out-Null
pg_dump -d $DB -Fc -f "D:\360PlusData\Backups\pre-storage-migration-$Stamp.dump"
(Get-Item "D:\360PlusData\Backups\pre-storage-migration-$Stamp.dump").Length   # must be > 0
```
**STOP/GO #2:** proceed only with a non-empty verified dump.

### Phase 2 — COPY (non-destructive; source retained). Duration: minutes–hours by corpus size (~100 MB/s).
```powershell
# /E all subdirs, /COPY:DAT, /R:2 /W:5 retries, /MT:16 multithread, /XO skip older, logs for verification.
robocopy $Src   $Dst    /E /COPY:DAT /R:2 /W:5 /MT:16 /NP /LOG:"D:\360PlusData\Backups\copy-docs-$Stamp.log"    /TEE
robocopy $Stg   $DstStg /E /COPY:DAT /R:2 /W:5 /MT:16 /NP /LOG:"D:\360PlusData\Backups\copy-staging-$Stamp.log" /TEE
robocopy $Repo  $DstRepo /E /COPY:DAT /R:2 /W:5 /MT:16 /NP /LOG:"D:\360PlusData\Backups\copy-repo-$Stamp.log"   /TEE
# robocopy exit code 0-7 = success (>=8 is a failure)
"docs=$LASTEXITCODE"
```
**STOP/GO #3:** every robocopy exit code < 8, and no `*.part` temp files copied (they are transient).

### Phase 3 — Verify the copy (counts, bytes, SHA-256). Duration: counts ~min; full SHA ~= copy time.
```powershell
# 3a. file-count + byte-count parity (source vs dest) for each tree
function Tree-Stats($p){ $f=Get-ChildItem $p -Recurse -File -Exclude *.part; [pscustomobject]@{Files=$f.Count;Bytes=($f|Measure-Object Length -Sum).Sum} }
Tree-Stats $Src; Tree-Stats $Dst          # Files and Bytes must match
Tree-Stats $Stg; Tree-Stats $DstStg
# 3b. SHA-256 verification against the DB (authoritative hashes live in documents.sha256).
#     Export (storage_path, sha256) for Local docs, then hash each copied file and compare.
psql -d $DB -At -F "`t" -c "SELECT storage_path, sha256 FROM documents WHERE storage_provider='Client360 Local' AND sha256 IS NOT NULL;" > "D:\360PlusData\Backups\hashes-$Stamp.tsv"
$bad = 0; $n = 0
Get-Content "D:\360PlusData\Backups\hashes-$Stamp.tsv" | ForEach-Object {
  $rel,$sha = $_ -split "`t",2
  $full = Join-Path $Dst $rel
  if (Test-Path $full) { $h=(Get-FileHash $full -Algorithm SHA256).Hash.ToLower(); if ($h -ne $sha.ToLower()){ $bad++; "$full  DB=$sha  FILE=$h" } }
  else { $bad++; "MISSING $full" }
  $n++
} | Tee-Object "D:\360PlusData\Backups\hash-mismatches-$Stamp.txt"
"checked=$n mismatches=$bad"
```
**STOP/GO #4 (critical):** counts+bytes match for every tree AND `mismatches=0`. If any mismatch → **abort**,
delete the partial `D:\360PlusData` copy, keep C: originals, investigate. **No DB writes have happened yet.**

### Phase 4 — Cutover (config + DB repoint). Duration: ~2–10 min.
```powershell
# 4a. stop the app so nothing writes during the DB repoint
& C:\Client360\tools\nssm.exe stop Client360     # or: sc.exe stop Client360
# 4b. edit app\.env: set CLIENT360_DATA_ROOT=D:\360PlusData, CLIENT360_MIGRATION_DEST_ROOT=D:\360PlusData\Repository,
#     CLIENT360_SHAREPOINT_STAGING_ROOT=D:\360PlusData\Staging\SharePoint, LOG_DIR=C:\360Plus\logs
# 4c. repoint storage_uri prefixes (storage_path is RELATIVE and unchanged; provider unchanged).
#     Run inside a transaction; the WHERE scopes each prefix. Verify row counts before COMMIT.
psql -d $DB <<'SQL'
BEGIN;
UPDATE documents SET storage_uri = replace(storage_uri, 'C:\Client360\Data\Documents', 'D:\360PlusData\Documents')
  WHERE storage_provider='Client360 Local' AND storage_uri LIKE 'C:\Client360\Data\Documents%';
UPDATE documents SET storage_uri = replace(storage_uri, 'D:\Client360Data', 'D:\360PlusData\Repository')
  WHERE storage_provider='Client360 Repository' AND storage_uri LIKE 'D:\Client360Data%';
-- expect: 0 rows still pointing at C:\Client360 (Local) or D:\Client360Data (Repository)
SELECT count(*) AS still_on_c FROM documents WHERE storage_uri LIKE 'C:\Client360%';
SELECT count(*) AS still_old_repo FROM documents WHERE storage_uri LIKE 'D:\Client360Data%';
COMMIT;
SQL
```
**STOP/GO #5:** `still_on_c = 0` and `still_old_repo = 0` **before** you type `COMMIT`. If not, `ROLLBACK`.

### Phase 5 — Restart + post-cutover verification. Duration: ~5–15 min.
```powershell
& C:\Client360\tools\nssm.exe start Client360    # or: sc.exe start Client360
Start-Sleep 10
# document-link verification: every storage_uri exists on disk and none remain on C:
psql -d $DB -At -c "SELECT count(*) FROM documents WHERE storage_uri LIKE 'C:\Client360%';"   # -> 0
psql -d $DB -At -F "`t" -c "SELECT id, storage_uri FROM documents WHERE storage_uri IS NOT NULL;" |
  ForEach-Object { $id,$u = $_ -split "`t",2; if (-not (Test-Path $u)) { "MISSING doc=$id $u" } } |
  Tee-Object "D:\360PlusData\Backups\post-missing-$Stamp.txt"
# app health + a real document open through the UI/API
Invoke-WebRequest http://127.0.0.1:8360/health | Select -Expand Content
# OCR resume/incremental should now read from D: (no C: paths):
$env:PYTHONPATH="."; C:\360Plus\.venv\Scripts\python.exe -m app.services.document_ocr --mode incremental --dry-run
```
**STOP/GO #6:** `still_on_c=0`, zero MISSING files, `/health` ok, a document opens, and a dry-run OCR/import
reads D: paths. This is the point of no easy return for config; the C: originals are still intact.

### Phase 6 — Soak, then reclaim C: (destructive — days later). Duration: minutes; schedule after soak.
Leave the `C:\Client360\Data\Documents` and `…\Staging` **originals in place** for a soak period (recommend
≥ 7 days of normal operation + at least one good nightly backup on the new layout). Only then:
```powershell
# FINAL destructive step — after soak + verified backups. Move to a temporary holding dir first, not delete.
robocopy $Src "C:\Client360\_RETIRED\Documents-$Stamp" /E /MOVE /R:1 /W:1 /NP    # /MOVE deletes source after copy
# keep _RETIRED for another cycle, then remove manually once fully confident.
```
**STOP/GO #7 (final, destructive):** only after the soak window, a clean backup on D:, and sign-off. Never
delete C: originals before this checkpoint.

---

## 6. Rollback (if any STOP/GO fails)

- **Before Phase 4 (no DB writes yet):** delete the partial `D:\360PlusData\Documents|Staging|Repository`
  copy; nothing else changed. `robocopy` did not touch the source.
- **After the Phase 4 DB repoint, before service start:** `ROLLBACK` if still in the transaction; if already
  committed, restore prefixes:
  ```powershell
  psql -d $DB -c "UPDATE documents SET storage_uri=replace(storage_uri,'D:\360PlusData\Documents','C:\Client360\Data\Documents') WHERE storage_provider='Client360 Local' AND storage_uri LIKE 'D:\360PlusData\Documents%';"
  psql -d $DB -c "UPDATE documents SET storage_uri=replace(storage_uri,'D:\360PlusData\Repository','D:\Client360Data') WHERE storage_provider='Client360 Repository' AND storage_uri LIKE 'D:\360PlusData\Repository%';"
  ```
  then revert `app\.env` (remove `CLIENT360_DATA_ROOT` and the other new values) and restart the service.
- **Worst case:** restore the Phase-1 `pg_dump` (`pg_restore -d $DB --clean "…\pre-storage-migration-$Stamp.dump"`)
  and revert `.env`. Because we copied (never moved) until Phase 6, the C: originals are the ground truth.

## 7. Expected durations (measure with the Phase-0 dry run)

| Phase | Work | Rough estimate |
|---|---|---|
| 0 Pre-flight | dry run + counts | 5–15 min |
| 1 Backup | `pg_dump -Fc` | 2–20 min |
| 2 Copy | robocopy `/MT` (~100 MB/s) | ~1 min per 6 GB; e.g. 60 GB ≈ 10–15 min |
| 3 Verify | counts (min) + full SHA-256 (≈ copy time) | 10 min – ~1 hr |
| 4 Cutover | stop + .env + DB repoint | 2–10 min |
| 5 Restart+verify | start + link checks | 5–15 min |
| 6 Reclaim | after ≥7-day soak | minutes |

## 8. Unresolved risks / cautions

1. **Full SHA-256 over the whole corpus is time-consuming** at 55k+ files; if the window is tight, verify
   100% of `Client360 Local` canonical docs (integrity-critical) and a large random sample of the repository,
   but do not skip verification entirely — counts+bytes alone do not catch corruption.
2. **Concurrent writers:** nothing may write documents during Phases 2–5. The service is stopped in Phase 4;
   confirm no scheduled importer/OCR job or manual `--delta-sync` runs in the window.
3. **`storage_path` assumption:** the repoint only changes the absolute `storage_uri` prefix and relies on
   `storage_path` being relative and the copied tree preserving structure exactly (robocopy `/E` does). Spot-
   check a few rows that `Join-Path $Dst storage_path == storage_uri` after cutover.
4. **The migration `Repository` tier** (`D:\Client360Data`, provider `Client360 Repository`) is only populated
   for *owned* documents relocated by the migration engine. Its move is included above; if that engine is
   re-run later it must point at `D:\360PlusData\Repository` (set via `CLIENT360_MIGRATION_DEST_ROOT`).
5. **Backups target:** standardize `CLIENT360_BACKUP_CMD` to write into `D:\360PlusData\Backups` so future
   pre-migration/pre-deploy dumps live on the data volume (config, not code).
6. **Two overloaded SharePoint env vars** (`CLIENT360_SHAREPOINT_SOURCE_ROOT` / `_DOCUMENT_ROOT`) still exist;
   this runbook uses `CLIENT360_DATA_ROOT` + `CLIENT360_SHAREPOINT_STAGING_ROOT` and does not set the overloaded
   pair, avoiding cross-subsystem coupling. De-overloading them is a separate, non-blocking follow-up.
