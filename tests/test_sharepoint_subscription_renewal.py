"""Automatic renewal of the SharePoint Graph subscription: secret redaction, CLI, and task assets.

Three things are pinned here, because each fails silently and expensively:

  1. REDACTION. Graph echoes ``clientState`` back in the subscription object. It is the shared secret
     the webhook handler checks to prove a notification really came from Graph, so leaking it lets a
     caller forge notifications. ``--ensure`` output goes to Task Scheduler logs; the raw payload must
     never reach them. (Observed in production: initial creation output exposed clientState.)

  2. CLI CONTRACT. Exit 0 on success and non-zero on failure, or the scheduler cannot tell a failed
     renewal from a successful one — the subscription would expire with every run "green".

  3. RENEWAL ARITHMETIC. The subscription lives 4200 minutes and renews only inside its final 720.
     The scheduled cadence has to fit in that window with margin; the numbers in the task installer
     are asserted against the numbers in the service, so the two cannot drift apart.

The PowerShell assets are validated STATICALLY (they cannot execute on CI or a developer Mac): the
checks below are the properties that would actually hurt if they regressed — a hard-coded secret, a
missing non-zero exit, a cadence too wide to guarantee renewal, or an installer that stops being
idempotent.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

import app.services.sharepoint_subscription as sub

_REPO = pathlib.Path(__file__).resolve().parents[1]
_WRAPPER = _REPO / "deploy" / "windows" / "Renew-SharePointSubscription.ps1"
_INSTALLER = _REPO / "deploy" / "windows" / "Install-SharePointRenewalTask.ps1"

SECRET = "super-secret-client-state-value"


# --- 1. redaction ------------------------------------------------------------

def test_client_state_is_redacted_from_a_subscription_payload():
    payload = {"id": "abc", "clientState": SECRET, "resource": "drives/d/root"}
    result = sub.redact_secrets(payload)
    assert result["clientState"] == sub.REDACTED
    assert SECRET not in json.dumps(result)


def test_redaction_preserves_every_useful_field():
    """Redaction must not gut the payload — an operator still needs to read it."""
    payload = {
        "id": "sub-1", "resource": "drives/d/root",
        "notificationUrl": "https://example.invalid/api/microsoft/sharepoint/webhook",
        "expirationDateTime": "2026-09-04T00:00:00Z", "changeType": "updated",
        "applicationId": "app-1", "creatorId": "user-1", "latestSupportedTlsVersion": "v1_2",
        "clientState": SECRET,
    }
    result = sub.redact_secrets(payload)
    for field in ("id", "resource", "notificationUrl", "expirationDateTime", "changeType",
                  "applicationId", "creatorId", "latestSupportedTlsVersion"):
        assert result[field] == payload[field], field
    assert result["clientState"] == sub.REDACTED
    assert set(result) == set(payload), "redaction must not add or drop keys"


@pytest.mark.parametrize("key", [
    "clientState", "clientstate", "CLIENTSTATE",          # case-insensitive
    "encryptionCertificate", "encryptionCertificateId",
    "accessToken", "refresh_token", "apiSecret", "password", "privateKey", "credentials",
])
def test_every_secret_shaped_field_is_redacted(key):
    assert sub.redact_secrets({key: SECRET})[key] == sub.REDACTED


def test_redaction_reaches_nested_structures():
    """The real payload is nested under "subscription"; a shallow scrub would miss it."""
    payload = {"action": "created",
               "subscription": {"id": "a", "clientState": SECRET},
               "history": [{"clientState": SECRET}, {"id": "b"}]}
    assert SECRET not in json.dumps(sub.redact_secrets(payload))


def test_redaction_leaves_non_secret_values_untouched():
    payload = {"count": 3, "ok": True, "none": None, "list": ["a", "b"], "nested": {"id": "x"}}
    assert sub.redact_secrets(payload) == payload


@pytest.mark.parametrize("action,setup", [
    ("created", "create"),
    ("renewed", "renew"),
    ("unchanged", "healthy"),
])
def test_ensure_never_returns_client_state(monkeypatch, action, setup):
    """Every one of ensure_subscription's return paths must be scrubbed, not just creation."""
    leaky = {"id": "abc", "clientState": SECRET}

    if setup == "create":
        monkeypatch.setattr(sub, "find_matching_subscription", lambda: None)
        monkeypatch.setattr(sub, "create_subscription", lambda: dict(leaky))
    elif setup == "renew":
        expires = (datetime.now(UTC) + timedelta(minutes=60)).isoformat()
        monkeypatch.setattr(sub, "find_matching_subscription",
                            lambda: {**leaky, "expirationDateTime": expires})
        monkeypatch.setattr(sub, "renew_subscription", lambda value: dict(leaky))
    else:
        expires = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        monkeypatch.setattr(sub, "find_matching_subscription",
                            lambda: {**leaky, "expirationDateTime": expires})

    result = sub.ensure_subscription()
    assert result["action"] == action
    assert SECRET not in json.dumps(result, default=str)
    assert result["subscription"]["clientState"] == sub.REDACTED
    assert result["subscription"]["id"] == "abc", "useful identity must survive redaction"


def test_status_output_carries_no_client_state(monkeypatch):
    monkeypatch.setattr(sub, "find_matching_subscription",
                        lambda: {"id": "abc", "clientState": SECRET,
                                 "resource": "drives/d/root", "notificationUrl": "https://x.invalid",
                                 "expirationDateTime": "2026-09-04T00:00:00Z", "changeType": "updated"})
    assert SECRET not in json.dumps(sub.subscription_status(), default=str)


# --- 2. ensure behaviour is unchanged ----------------------------------------

def test_renewal_threshold_and_lifetime_are_unchanged():
    """The behaviour the production runbook documents. If either constant moves, the scheduled cadence
    below has to be re-derived, so this fails deliberately loudly."""
    assert sub.DEFAULT_LIFETIME_MINUTES == 4200      # ~70 hours
    assert sub.RENEW_BEFORE_MINUTES == 720           # 12 hours


def test_ensure_is_unchanged_at_the_boundary(monkeypatch):
    """Just inside the window renews; just outside leaves it alone."""
    monkeypatch.setattr(sub, "renew_subscription", lambda value: {"id": value})

    just_inside = (datetime.now(UTC) + timedelta(minutes=719)).isoformat()
    monkeypatch.setattr(sub, "find_matching_subscription",
                        lambda: {"id": "abc", "expirationDateTime": just_inside})
    assert sub.ensure_subscription()["action"] == "renewed"

    just_outside = (datetime.now(UTC) + timedelta(minutes=721)).isoformat()
    monkeypatch.setattr(sub, "find_matching_subscription",
                        lambda: {"id": "abc", "expirationDateTime": just_outside})
    assert sub.ensure_subscription()["action"] == "unchanged"


# --- 3. connected-account behaviour ------------------------------------------

@pytest.mark.parametrize("accounts,expected", [
    ([], "found 0"),
    ([{"id": 1}, {"id": 2}], "found 2"),
    ([{"id": 1}, {"id": 2}, {"id": 3}], "found 3"),
])
def test_zero_or_multiple_connected_accounts_are_refused(monkeypatch, accounts, expected):
    """Ambiguity is refused rather than guessed: renewing against the wrong tenant would silently
    point the subscription at another account's drive."""
    import app.services.microsoft_identity as identity
    monkeypatch.setattr(identity, "connected_accounts", lambda: accounts)
    with pytest.raises(RuntimeError, match=expected):
        sub._headers()


def test_exactly_one_connected_account_is_used(monkeypatch):
    import app.services.microsoft_identity as identity
    account = {"id": 99}
    monkeypatch.setattr(identity, "connected_accounts", lambda: [account])
    monkeypatch.setattr(identity, "get_microsoft_access_token", lambda a: "tok")
    assert sub._headers()["Authorization"] == "Bearer tok"


def test_a_token_never_reaches_redacted_output():
    """Headers carry a bearer token; if one is ever folded into a result it must be scrubbed."""
    assert sub.redact_secrets({"Authorization": "Bearer abc",
                               "access_token": "abc"})["access_token"] == sub.REDACTED


# --- 4. the CLI contract -----------------------------------------------------

def _run_cli(*args):
    return subprocess.run([sys.executable, "-m", "scripts.manage_sharepoint_subscription", *args],
                          capture_output=True, text=True, cwd=_REPO, timeout=120)


def test_cli_exits_non_zero_when_ensure_fails():
    """Without production configuration the Graph call cannot succeed. The point is the EXIT CODE: a
    scheduler must see a failure, not a green run that renewed nothing."""
    result = _run_cli("--ensure")
    assert result.returncode != 0
    assert "ERROR:" in result.stderr


def test_cli_requires_an_explicit_mode():
    assert _run_cli().returncode != 0


def test_cli_failure_output_carries_no_secret_values():
    result = _run_cli("--ensure")
    combined = result.stdout + result.stderr
    for marker in ("clientState", "DATABASE_URL", "Bearer ", "postgresql://"):
        assert marker not in combined, marker


# --- 5. the Windows assets (static validation) -------------------------------

def test_the_wrapper_and_installer_are_versioned():
    assert _WRAPPER.is_file()
    assert _INSTALLER.is_file()


def _wrapper_text():
    return _WRAPPER.read_text(encoding="utf-8")


def _installer_text():
    return _INSTALLER.read_text(encoding="utf-8")


def test_wrapper_invokes_the_ensure_command_from_the_install_root():
    text = _wrapper_text()
    assert "scripts.manage_sharepoint_subscription --ensure" in text
    assert r".venv\Scripts\python.exe" in text
    assert "Set-Location" in text and "InstallRoot" in text


def test_wrapper_propagates_a_non_zero_exit_code():
    text = _wrapper_text()
    assert "$LASTEXITCODE" in text
    assert re.search(r"^exit \$exitCode", text, re.MULTILINE), "wrapper must exit with the real code"


def test_wrapper_reads_the_production_env_file_without_echoing_values():
    text = _wrapper_text()
    assert r"app\.env" in text
    # The loaded NAMES may be logged; a VALUE must never reach an output/log call. Check every line
    # that writes output, rather than slicing the file.
    writers = [ln for ln in text.splitlines()
               if re.search(r"(Write-Log|Write-Output|Write-Host|Add-Content)", ln)]
    assert writers, "no output calls found — the check would be vacuous"
    for line in writers:
        assert "$value" not in line, f"a .env value is echoed: {line.strip()}"
    # And the name list that IS logged carries names only.
    assert "$names.Count" in text or "{0} settings" in text


def test_wrapper_does_not_restart_the_app_or_run_a_document_sync():
    text = _wrapper_text().lower()
    for forbidden in ("restart-service", "start-service", "stop-service", "iisreset",
                      "sync_sharepoint", "run_sharepoint_sync", "--sync", "microsoft_ingestion"):
        assert forbidden not in text, forbidden


def test_no_secret_is_embedded_in_either_windows_asset():
    for text in (_wrapper_text(), _installer_text()):
        assert "MICROSOFT_SHAREPOINT_WEBHOOK_CLIENT_STATE=" not in text
        assert "postgresql://" not in text
        assert not re.search(r"(?i)password\s*=\s*['\"][^'\"]+['\"]", text)
        assert not re.search(r"(?i)(client_secret|api_key|access_token)\s*=\s*['\"][^'\"]+['\"]", text)


def test_installer_registers_the_expected_task_name():
    assert "Client360 SharePoint Subscription Renewal" in _installer_text()


def test_installer_does_not_touch_the_taxdome_task():
    """Every MUTATING scheduled-task call must target $TaskName and nothing else. A literal task name
    or a wildcard passed to Unregister/Disable/Stop could take out 'Client360 TaxDome Sync'."""
    text = _installer_text()
    mutators = re.findall(r"(?:Unregister|Disable|Stop|Set)-ScheduledTask[^\r\n]*", text)
    assert mutators, "no mutating task calls found — the check would be vacuous"
    for call in mutators:
        assert "-TaskName $TaskName" in call, f"mutating call not scoped to $TaskName: {call}"
        assert "*" not in call, f"wildcard in a mutating task call: {call}"
    # TaxDome is only ever mentioned in prose explaining why this task differs, never in a command.
    for line in text.splitlines():
        if "TaxDome" in line:
            assert line.strip().startswith(("#", "*", "ACCOUNT", "user because")) or not re.search(
                r"-ScheduledTask", line), f"TaxDome referenced in a command: {line.strip()}"


def test_installer_is_idempotent():
    text = _installer_text()
    assert "Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue" in text
    assert "Unregister-ScheduledTask" in text
    assert "Register-ScheduledTask" in text


def test_installer_runs_non_interactively_from_the_install_root():
    text = _installer_text()
    assert "-NonInteractive" in text
    assert "-WorkingDirectory $InstallRoot" in text
    assert "ServiceAccount" in text and "NT AUTHORITY\\SYSTEM" in text


def test_installer_cadence_guarantees_a_run_inside_the_renewal_window():
    """The cadence is derived from the service's own constants, so the two cannot drift apart."""
    text = _installer_text()
    default = int(re.search(r"\$IntervalHours\s*=\s*(\d+)", text).group(1))
    ceiling = int(re.search(r"\$MaxIntervalHours\s*=\s*(\d+)", text).group(1))

    window_hours = sub.RENEW_BEFORE_MINUTES / 60
    assert ceiling <= window_hours, "the ceiling would allow a run to miss the renewal window"
    assert default <= window_hours / 2, "the default leaves no margin for a failed run"
    # Three attempts inside the window tolerates two consecutive failures.
    assert window_hours / default >= 3


def test_installer_refuses_an_interval_that_could_miss_the_window():
    """The guard must THROW, not warn: a too-wide interval silently lets the subscription expire."""
    text = _installer_text()
    guard = re.search(r"if \(\$IntervalHours[^}]*\}", text, re.DOTALL)
    assert guard, "no IntervalHours validation found"
    assert "throw" in guard.group(0)
    assert "IntervalHours must be between" in guard.group(0)


def test_installer_prevents_overlapping_runs():
    assert "MultipleInstances IgnoreNew" in _installer_text()


def test_installer_catches_up_a_missed_run():
    assert "-StartWhenAvailable" in _installer_text()
