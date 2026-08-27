# Client360 — Windows Server deployment

## PRODUCTION ENVIRONMENT FILE: `C:\Client360\app\.env`

That is the **only** file the running Client360 service loads. The NSSM service starts uvicorn with
`--env-file C:\Client360\app\.env`, and uvicorn reads that path and nothing else.

> ### ⚠ These are NOT the production runtime env file
>
> | Path | Status |
> | --- | --- |
> | `C:\Client360\app\.env` | **PRODUCTION — this is the one** |
> | `C:\Client360\.env` | NOT loaded by the service. Legacy/stray. |
> | `C:\Client360\app.env` | NOT loaded by the service. Legacy/stray. |
>
> **Operators must never edit another env file when changing production runtime configuration.**
> Editing `C:\Client360\.env` or `C:\Client360\app.env` changes nothing about the running service.
> The change will appear to have been made, survive a restart, and have no effect.

### Why this keeps happening

Every env-file reference in the codebase used to be **relative** — `app\.env` in the service
installer, `app/.env` in the Python tooling. A relative path only resolves to the right file while
the working directory (or the NSSM `AppDirectory`) is `C:\Client360`, and it forces an operator
reading it to *infer* the absolute path. `C:\Client360\app.env` and `C:\Client360\.env` are both
plausible readings of `app\.env`, and both are silently ignored at runtime.

Worse, `python -m app.deploy check-config` used to load `app/.env` relative to the current directory
and silently continue if it was not there — so an operator who edited the wrong file and then ran the
validator still got `RESULT: OK`, because the check fell through to the ambient process environment.

Both halves are now fixed: the canonical path is absolute everywhere, and `check-config` prints the
path of the file it actually loaded.

## Changing production configuration

1. Edit **`C:\Client360\app\.env`** — and only that file.
2. Validate without restarting anything:

   ```powershell
   powershell -File C:\Client360\deploy\windows\validate_production_env.ps1
   ```

   It is read-only. It confirms the service's `--env-file`, confirms the file exists, lists the
   configuration **variable names** it defines, and warns loudly if either ambiguous sibling is
   present. It never prints a value.
3. Restart the service:

   ```powershell
   powershell -File C:\Client360\deploy\windows\Install-Client360Service.ps1 -Action restart
   ```

## Scripts in this directory

| Script | Purpose |
| --- | --- |
| `Deploy-Client360.ps1` | Full deployment sequence. Runs `validate_production_env.ps1 -RequireService` at step 6b — **after** installing the service and **before** restarting it — so a service pointed at the wrong env file fails the deploy instead of being started into production. |
| `Install-Client360Service.ps1` | Installs/manages the service. `-EnvFile` defaults to `C:\Client360\app\.env` and `-Action install` **refuses** any other path unless `-AllowNonCanonicalEnvFile` is passed (non-production hosts only). |
| `validate_production_env.ps1` | Read-only validator. Exit codes: `0` ok, `2` service/env-path mismatch, `3` env file missing/unreadable, `4` service configuration unreadable (with `-RequireService`). |
| `client360.env.example` | Placeholder template. Contains no real values. Copy to `C:\Client360\app\.env`. |

## Verifying the installer on the Windows server (safe dry run)

The installer's NSSM invocation cannot be executed on a non-Windows development machine, so verify it
on the server **before** relying on it. Neither step below touches the real service, NSSM, or the
environment file.

**Step 1 — parse the script without running it.** Empty output means no syntax/parse errors:

```powershell
$path = 'C:\Client360\deploy\windows\Install-Client360Service.ps1'
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors) | Out-Null
$errors
```

**Step 2 — preview the exact nssm argv with a shim.** This puts a fake `nssm` ahead of the real one on
PATH *for that shell only*, so the install action prints the arguments it would pass and installs
nothing. The `throw` guard is what makes it safe: if the shim is not the resolved `nssm`, it stops
before running anything.

```powershell
$shim = Join-Path $env:TEMP 'nssm-dryrun'
New-Item -ItemType Directory -Force -Path $shim | Out-Null
Set-Content -Path (Join-Path $shim 'nssm.cmd') -Value '@echo NSSM-WOULD-RUN: %*' -Encoding Ascii
$env:PATH = "$shim;$env:PATH"
if ((Get-Command nssm).Source -ne (Join-Path $shim 'nssm.cmd')) { throw 'Shim not in front of the real nssm - ABORT.' }
powershell -NoProfile -File C:\Client360\deploy\windows\Install-Client360Service.ps1 -Action install
```

The first `NSSM-WOULD-RUN:` line must read, in this exact order:

```
NSSM-WOULD-RUN: install Client360 C:\Client360\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8360 --env-file C:\Client360\app\.env
```

If `--env-file C:\Client360\app\.env` is missing from that line, the installer is still dropping the
uvicorn arguments — do not use it. Close the shell afterwards (the PATH edit is process-local), and
delete the shim:

```powershell
Remove-Item -Recurse -Force (Join-Path $env:TEMP 'nssm-dryrun')
```

**Step 3 — confirm the live service separately**, without installing anything:

```powershell
nssm get Client360 AppParameters
powershell -File C:\Client360\deploy\windows\validate_production_env.ps1
```

## Development vs production

This canonical **absolute** path governs the Windows production server only.

- **Development, tests, CI, Docker, non-Windows:** unchanged. The Python tooling resolves the
  repo-relative `app/.env` **first**, so a checkout, a container, and the test suite keep loading
  their own file. `config/.env.example` is the developer template.
- **Production Windows:** `C:\Client360\app\.env`, absolute, named explicitly in the service
  arguments. The absolute path is only used as a fallback by the Python tooling — it matters when
  `check-config` is run from the wrong directory on the server, which used to validate nothing.

The application itself never queries NSSM. Service inspection lives entirely in
`validate_production_env.ps1`; `app.main` has no dependency on the service manager.

## Cleanup of the ambiguous files — SEPARATE, EXPLICIT PRODUCTION STEP

Nothing in this repository references `C:\Client360\.env` or `C:\Client360\app.env`. They are not
deleted or renamed by any script here, and they must not be removed as part of a code deployment.

Archive them **outside the active runtime naming convention** so no future glob, backup job, or
tab-completion can resurface them as an env file — do not simply rename them in place:

```powershell
# Run only after the code/tooling change is reviewed and deployed, and only with a change record.
$stamp = Get-Date -Format 'yyyyMMdd'
New-Item -ItemType Directory -Force -Path 'C:\Client360\config-archive' | Out-Null
Move-Item -LiteralPath 'C:\Client360\.env'    -Destination "C:\Client360\config-archive\root-dot-env-$stamp.bak"
Move-Item -LiteralPath 'C:\Client360\app.env' -Destination "C:\Client360\config-archive\legacy-app-env-$stamp.bak"
```

Before running it: confirm `validate_production_env.ps1` reports OK, confirm the archive directory is
covered by the same access restrictions and backup exclusions as the env file itself (these files may
still contain live credentials), and restrict it — e.g. `icacls C:\Client360\config-archive /inheritance:r /grant:r "Administrators:(OI)(CI)F"`.
Move, never copy: a copy leaves the ambiguous file exactly where it was.
