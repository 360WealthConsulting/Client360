# SharePoint subscription automatic renewal

The SharePoint change-notification webhook depends on a Microsoft Graph **subscription**. Graph
subscriptions expire; if one lapses, SharePoint silently stops notifying Client360 and document sync
quietly goes stale with no error anywhere. This is the mechanism that keeps it alive.

Nothing here is installed automatically. These are versioned assets to be deployed and installed as a
separate, reviewed production step.

## The arithmetic

| quantity | value | source |
|---|---|---|
| Subscription lifetime | 4200 minutes (~70 h) | `DEFAULT_LIFETIME_MINUTES` |
| Renews when remaining ≤ | 720 minutes (12 h) | `RENEW_BEFORE_MINUTES` |
| **Renewal window** | **12 hours wide** | the two above |
| Scheduled interval | **4 hours** | `Install-SharePointRenewalTask.ps1` |
| Attempts inside each window | **3** | 12 ÷ 4 |

`--ensure` only acts inside the final 12 hours of the subscription's life, so the task has a 12-hour
window in which to succeed. Running every 4 hours puts three attempts in that window, which means the
subscription still renews after **two consecutive failed runs** — a transient Graph 5xx, a reboot, a
network blip. Every 12 hours would leave no margin; every 6 hours leaves one. A run is a single Graph
list call plus at most one create/renew, so the extra frequency is essentially free.

The installer **refuses** an interval above 12 hours, because such a cadence cannot guarantee a run
inside the window. `test_installer_cadence_guarantees_a_run_inside_the_renewal_window` derives these
numbers from the service constants, so the cadence and the code cannot drift apart.

## Components

| file | role |
|---|---|
| `deploy/windows/Renew-SharePointSubscription.ps1` | Task Scheduler entry point |
| `deploy/windows/Install-SharePointRenewalTask.ps1` | idempotent task installer |
| `scripts/manage_sharepoint_subscription.py` | `--status` / `--ensure` CLI |
| `app/services/sharepoint_subscription.py` | Graph calls + secret redaction |

### The wrapper

Runs from `C:\Client360`, parses `C:\Client360\app\.env` into the process environment, and invokes:

```
C:\Client360\.venv\Scripts\python.exe -m scripts.manage_sharepoint_subscription --ensure
```

It exits with the Python process's exit code, so Task Scheduler records a failed renewal as a failed
run. It logs to `C:\Client360\logs\sharepoint-subscription\renew-<timestamp>.log` (30-day retention)
with the resolved action (`created` / `renewed` / `unchanged`).

It deliberately does **not** restart Client360, run a document sync, write to the database, or modify
`.env`.

## Installation (production, separate change)

```powershell
# Elevated PowerShell on the production host
powershell -NoProfile -ExecutionPolicy Bypass -File `
  C:\Client360\deploy\windows\Install-SharePointRenewalTask.ps1
```

Preview without changing anything by adding `-WhatIf`. Re-running is safe: the installer unregisters
and re-registers **only** `Client360 SharePoint Subscription Renewal`, so repeated installs converge
instead of duplicating. It never touches `Client360 TaxDome Sync` or any other task.

Verify:

```powershell
Get-ScheduledTask -TaskName 'Client360 SharePoint Subscription Renewal' | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName 'Client360 SharePoint Subscription Renewal'   # run once now
Get-Content (Get-ChildItem C:\Client360\logs\sharepoint-subscription\*.log | Sort-Object LastWriteTime -Descending)[0].FullName
```

### Account

Defaults to **`NT AUTHORITY\SYSTEM`** (`LogonType ServiceAccount`, `RunLevel Limited`): non-interactive,
no stored password, unaffected by password rotation. Appropriate because the task reads a local file
and makes an outbound HTTPS call — it needs no network identity, no mapped drive, and no user profile.

This differs from `Client360 TaxDome Sync`, which must run as a real user because it depends on the
interactively-mapped `Z:` drive. Pass `-UserId` / `-Password` only if local policy forbids SYSTEM for
outbound-calling tasks; the password is prompted for at install time and never stored in the repo.

## Security properties

- **No secret in the repository, the task definition, or any command line.** Configuration is read
  from `app\.env` at run time. The task's argument list is just `-File <wrapper path>`.
- **No secret in logs.** The wrapper logs the *names* of the settings it loaded, never their values.
- **`clientState` is redacted.** Graph echoes `clientState` — the shared secret the webhook handler
  uses to prove a notification is genuine — back in the subscription object. Before this change,
  `--ensure` printed it verbatim on creation (observed in production). `redact_secrets()` now scrubs it
  at the **service** boundary, so every caller is safe, with a second pass in the CLI. Redaction is by
  exact field name plus a substring sweep (`secret`, `token`, `password`, `credential`, `privatekey`),
  so a field Graph adds later is caught rather than published. Keys stay visible, values become
  `***REDACTED***`; `id`, `resource`, `notificationUrl`, `expirationDateTime` and `changeType` survive
  intact.
- **Errors do not leak payloads.** The CLI's failure path prints the exception type and message only,
  never the request or response body.
- **No ambiguity about identity.** Renewal requires exactly one connected Microsoft 365 account; zero
  or several is a hard error, so a renewal can never be pointed at the wrong tenant's drive.

## If renewal fails

The subscription survives ~70 hours, so a failed run is not an incident — there are two more attempts
before the window closes. Investigate if runs fail repeatedly:

1. `Get-ScheduledTaskInfo` → `LastTaskResult` (0 = success).
2. Newest log in `C:\Client360\logs\sharepoint-subscription\`.
3. `python -m scripts.manage_sharepoint_subscription --status` for current expiry.

A lapsed subscription is repaired by the same command: `--ensure` recreates a missing subscription.
