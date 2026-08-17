# 360Plus Storage Migration — Operator Checklist (PowerShell, line-by-line)

Run **only after the current OCR/import job has fully finished.** Copy-first; nothing on `C:` is deleted
until the final soak step. Do each `[ ]`; do not pass a **STOP** until its condition is true. Full detail:
`docs/storage_migration_runbook.md`.

```powershell
# ---- session variables ----
$Src="C:\Client360\Data\Documents"; $Dst="D:\360PlusData\Documents"
$Stg="C:\Client360\Data\Staging\SharePoint"; $DstStg="D:\360PlusData\Staging\SharePoint"
$Repo="D:\Client360Data"; $DstRepo="D:\360PlusData\Repository"
$DB="client360"; $Stamp=Get-Date -Format "yyyyMMdd-HHmmss"; $BK="D:\360PlusData\Backups"
New-Item -ItemType Directory -Force $BK | Out-Null
```

- [ ] **Pre-flight** — confirm the job is done and there is enough space:
  ```powershell
  Get-Process python -ErrorAction SilentlyContinue          # expect: nothing running
  Get-PSDrive D | Select Used,Free
  robocopy $Src $Dst /E /L /NFL /NDL /NP | Select-String "Bytes :","Files :"
  robocopy $Stg $DstStg /E /L /NFL /NDL /NP | Select-String "Bytes :","Files :"
  ```
  **STOP #1:** job finished AND `Free(D) ≥ totalBytes × 1.10`.

- [ ] **Backup DB:**
  ```powershell
  pg_dump -d $DB -Fc -f "$BK\pre-storage-migration-$Stamp.dump"
  (Get-Item "$BK\pre-storage-migration-$Stamp.dump").Length     # must be > 0
  ```
  **STOP #2:** dump exists and is non-empty.

- [ ] **Copy (non-destructive):**
  ```powershell
  robocopy $Src  $Dst    /E /COPY:DAT /R:2 /W:5 /MT:16 /NP /LOG:"$BK\copy-docs-$Stamp.log" /TEE;    "docs=$LASTEXITCODE"
  robocopy $Stg  $DstStg /E /COPY:DAT /R:2 /W:5 /MT:16 /NP /LOG:"$BK\copy-stg-$Stamp.log" /TEE;     "stg=$LASTEXITCODE"
  robocopy $Repo $DstRepo /E /COPY:DAT /R:2 /W:5 /MT:16 /NP /LOG:"$BK\copy-repo-$Stamp.log" /TEE;   "repo=$LASTEXITCODE"
  ```
  **STOP #3:** each exit code `< 8`.

- [ ] **Verify counts + bytes + SHA-256:**
  ```powershell
  function TS($p){ $f=Get-ChildItem $p -Recurse -File -Exclude *.part; [pscustomobject]@{Files=$f.Count;Bytes=($f|Measure-Object Length -Sum).Sum} }
  TS $Src; TS $Dst; TS $Stg; TS $DstStg          # Files+Bytes must match per pair
  psql -d $DB -At -F "`t" -c "SELECT storage_path, sha256 FROM documents WHERE storage_provider='Client360 Local' AND sha256 IS NOT NULL;" > "$BK\hashes-$Stamp.tsv"
  $bad=0;$n=0; Get-Content "$BK\hashes-$Stamp.tsv" | % { $rel,$sha=$_ -split "`t",2; $full=Join-Path $Dst $rel
    if(Test-Path $full){ if((Get-FileHash $full -Algorithm SHA256).Hash.ToLower() -ne $sha.ToLower()){$bad++;"BAD $full"} } else {$bad++;"MISSING $full"}; $n++ } | Tee-Object "$BK\hash-mismatch-$Stamp.txt"
  "checked=$n mismatches=$bad"
  ```
  **STOP #4 (critical):** all pairs match AND `mismatches=0`. If not → delete `D:\360PlusData\*` copy, keep C:, abort (no DB writes yet).

- [ ] **Cutover — stop service, edit .env, repoint DB:**
  ```powershell
  & C:\Client360\tools\nssm.exe stop Client360        # or: sc.exe stop Client360
  # Edit app\.env:  CLIENT360_DATA_ROOT=D:\360PlusData
  #                 CLIENT360_MIGRATION_DEST_ROOT=D:\360PlusData\Repository
  #                 CLIENT360_SHAREPOINT_STAGING_ROOT=D:\360PlusData\Staging\SharePoint
  #                 LOG_DIR=C:\360Plus\logs
  psql -d $DB -c "BEGIN;
    UPDATE documents SET storage_uri=replace(storage_uri,'C:\Client360\Data\Documents','D:\360PlusData\Documents') WHERE storage_provider='Client360 Local' AND storage_uri LIKE 'C:\Client360\Data\Documents%';
    UPDATE documents SET storage_uri=replace(storage_uri,'D:\Client360Data','D:\360PlusData\Repository') WHERE storage_provider='Client360 Repository' AND storage_uri LIKE 'D:\Client360Data%';
    SELECT count(*) AS still_on_c FROM documents WHERE storage_uri LIKE 'C:\Client360%';
    SELECT count(*) AS still_old_repo FROM documents WHERE storage_uri LIKE 'D:\Client360Data%';
    COMMIT;"
  ```
  **STOP #5:** `still_on_c=0` and `still_old_repo=0` (shown before COMMIT). If not → `ROLLBACK`.

- [ ] **Restart + verify links:**
  ```powershell
  & C:\Client360\tools\nssm.exe start Client360; Start-Sleep 10
  psql -d $DB -At -c "SELECT count(*) FROM documents WHERE storage_uri LIKE 'C:\Client360%';"    # -> 0
  psql -d $DB -At -F "`t" -c "SELECT id,storage_uri FROM documents WHERE storage_uri IS NOT NULL;" | % { $id,$u=$_ -split "`t",2; if(-not(Test-Path $u)){"MISSING doc=$id $u"} } | Tee-Object "$BK\post-missing-$Stamp.txt"
  Invoke-WebRequest http://127.0.0.1:8360/health | Select -Expand Content
  $env:PYTHONPATH="."; C:\360Plus\.venv\Scripts\python.exe -m app.services.document_ocr --mode incremental --dry-run
  ```
  **STOP #6:** count `=0`, zero MISSING, `/health` ok, a document opens in the app, dry-run reads D: paths.

- [ ] **Soak ≥ 7 days** with normal operation and a good nightly backup on the new layout. Do **not** delete C: yet.

- [ ] **Reclaim C: (destructive — only after soak + sign-off):**
  ```powershell
  robocopy $Src "C:\Client360\_RETIRED\Documents-$Stamp" /E /MOVE /R:1 /W:1 /NP
  ```
  **STOP #7:** soak passed, backup verified, sign-off obtained.

**Rollback at any point:** before Cutover → just delete the `D:\360PlusData\*` copy. After the DB repoint →
reverse the two `replace(...)` UPDATEs (D:→C: / Repository→D:\Client360Data) and revert `.env`. Worst case →
`pg_restore -d client360 --clean "$BK\pre-storage-migration-$Stamp.dump"`. C: originals are ground truth until STOP #7.
