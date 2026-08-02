# TaxDome Drive Document Synchronization

Client360 keeps **durable local copies** of the documents stored on the firm's TaxDome Drive so the
client record does not depend on a mapped network drive being present at read time.

- **Source** (`TAXDOME_DRIVE_ROOT`, default `Z:\`) is treated as **read-only**. The sync never
  renames, moves, modifies, or deletes anything on it.
- **Destination** (`CLIENT360_TAXDOME_DOCUMENT_ROOT`, default `C:\Client360\Data\Documents\TaxDome`)
  holds Client360's own copies, preserving the complete source-relative directory structure.

This is a **one-way** sync: TaxDome Drive → Client360. It uses the existing `documents` and
`import_jobs` tables — there is no parallel document platform and **no database migration**.

## What the sync does

1. Recursively discovers files in each top-level TaxDome account folder.
2. Copies **new** files into the local store.
3. When a source file **changes**, it copies the replacement safely: stream to a temporary file in the
   destination directory, verify **size and SHA-256**, then **atomically replace** the prior local
   file (`os.replace`). A partial file is never exposed.
4. **Skips unchanged** files using the stored source size + modified-time first; it only hashes when
   those disagree, to confirm whether the content actually changed.
5. Records **every error** and continues; reruns are **idempotent**.
6. When a source file **disappears**, the local copy is **retained**. The document is flagged
   `available_from_source=false` and `source_status="missing"`; its `status` stays `active` and it is
   **not** archived merely because the source vanished. Removing retained copies requires the explicit
   `--purge-missing` flag (never automatic).
7. Prints periodic progress (folders / files examined, copied, updated, skipped, bytes, errors) so a
   long scan does not look frozen, and records the run as `interrupted` on Ctrl+C.

Synchronized documents carry `storage_provider = "Client360 Local"`, an absolute `storage_uri` (the
local copy), a destination-relative `storage_path`, the verified `sha256`/`size_bytes`, and JSONB
`tags` including `source_system`, `source_root`, `source_path`, `source_relative_path`,
`taxdome_folder`, `local_relative_path`, `source_created`, `source_modified`, `source_size`,
`available_from_source`, `retained_locally`, `last_scan_id`, `last_synced_at`, and `sync_version`.

## Person linking

Each top-level TaxDome folder is one account. A folder is auto-linked to a canonical person **only on a
unique exact normalized-name match** — never a weak/partial match. Zero or multiple matches leave the
documents unresolved (`person_id` NULL) for human review (the TaxDome Drive demo review queue). Linked
documents appear on the person's **Documents** tab, and the file link downloads the **Client360 local
copy** (never `Z:\`). Directly-uploaded Client360 documents and the Microsoft 365 panel are unaffected.

## Configuration (`app\.env`)

```
TAXDOME_DRIVE_ROOT=Z:\
CLIENT360_TAXDOME_DOCUMENT_ROOT=C:\Client360\Data\Documents\TaxDome
TAXDOME_SYNC_DELETE_MISSING=false
TAXDOME_SYNC_PROGRESS_INTERVAL=100
```

`TAXDOME_SYNC_DELETE_MISSING` documents the firm's retention posture (default `false` = retain local
copies of removed source files). Deletion of retained copies is performed **only** by the explicit
`--purge-missing` / `-PurgeMissing` flag — it is never triggered automatically, regardless of this
variable.

## Manual commands

Run from `C:\Client360` with the virtualenv active:

```powershell
# Dry run — report what would change; makes no file or database changes
python -m app.importers.taxdome_drive --dry-run

# Normal sync (uses the .env roots)
python -m app.importers.taxdome_drive

# Explicit roots
python -m app.importers.taxdome_drive --source-root Z:\ --destination-root C:\Client360\Data\Documents\TaxDome

# Remove local copies whose source file has disappeared (deliberate; never automatic)
python -m app.importers.taxdome_drive --purge-missing
```

## Runner script

`scripts\sync_taxdome_documents.ps1` is the Windows runner used by automation. It changes to
`C:\Client360`, activates `.venv`, loads `app\.env`, verifies the source and destination roots, creates
the destination if missing, runs the sync, writes a timestamped log to
`C:\Client360\logs\taxdome-sync\`, and returns a nonzero exit code on any fatal failure. A single-
instance lock (`%TEMP%\client360-taxdome-sync.lock`) prevents overlapping executions.

```powershell
# Dry run through the runner
C:\Client360\scripts\sync_taxdome_documents.ps1 -DryRun

# Live sync through the runner
C:\Client360\scripts\sync_taxdome_documents.ps1
```

If `Z:` is not visible in the security context the script is running under, it exits with a fatal error
and a clear message — `Z:` is mapped per user, so the automation must run as an account that can see it.

## Scheduled Task setup

The task must run as **Michael Shelton's Windows account**, because that account is the one that has
`Z:` (TaxDome Drive) mapped.

**Preferred (only if `Z:` is available when not logged on):** many environments map `Z:` per interactive
logon, so a "run whether user is logged on or not" task will **not** see `Z:`. Test it first:

```powershell
# As Michael's account, in a non-interactive context, confirm Z: resolves.
# If this prints the folders, "run whether logged on or not" is viable.
powershell -NoProfile -Command "Test-Path 'Z:\'"
```

If `Test-Path 'Z:\'` is `True` in a non-interactive/service context (e.g. the drive is mapped machine-
wide or via a logon script that also applies to the task), create a "run whether user is logged on or
not" task:

```powershell
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\Client360\scripts\sync_taxdome_documents.ps1"'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
# MultipleInstances Ignore-New prevents overlapping runs
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName 'Client360 TaxDome Sync' -Action $action -Trigger $trigger `
  -Settings $settings -User 'DOMAIN\mshelton' -Password (Read-Host -AsSecureString 'Password') `
  -RunLevel Limited
```

**Otherwise (the common case — `Z:` only exists after interactive logon):** run the task at Michael's
**logon** and repeat **every 15 minutes**, so it always executes in the security context where `Z:` is
mapped:

```powershell
$action   = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\Client360\scripts\sync_taxdome_documents.ps1"'
$atLogon  = New-ScheduledTaskTrigger -AtLogOn -User 'DOMAIN\mshelton'
$every15  = New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName 'Client360 TaxDome Sync' -Action $action `
  -Trigger @($atLogon, $every15) -Settings $settings -User 'DOMAIN\mshelton' -RunLevel Limited
```

`-MultipleInstances IgnoreNew` (plus the script's own lock file) prevents overlapping executions. Adjust
`DOMAIN\mshelton` to the real account. Review results in `C:\Client360\logs\taxdome-sync\` and, for a
history of runs, the `import_jobs` table (`source_system = 'TaxDome Drive'`).

## First-time rollout (recommended order)

1. `...\sync_taxdome_documents.ps1 -DryRun` — confirm counts and that `Z:` and the destination resolve.
2. `...\sync_taxdome_documents.ps1` — first real sync (may take a while; watch the progress + log).
3. Spot-check a linked person's **Documents** tab and download a file (serves the local copy).
4. Register the Scheduled Task using the appropriate variant above.
