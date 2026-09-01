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
import shutil
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


# --- 4. action-verb parsing --------------------------------------------------
#
# PRODUCTION DEFECT (observed on the deployed release): the wrapper parsed the action verb with
#
#     if ($output -match '"action"...') { $action = $Matches[1] }
#
# $output is an ARRAY of captured child-process lines. PowerShell's -match on an array is a FILTER —
# it returns the matching ELEMENTS and never populates $Matches. Under the wrapper's own
# Set-StrictMode the following $Matches read throws "The variable '$Matches' cannot be retrieved
# because it has not been set", so a SUCCESSFUL `--ensure` was logged as a FAILED task run; in a
# session where $Matches already existed it logged a stale or blank verb instead.
#
# The verb is how an operator reads the scheduler log to tell "renewed" from "unchanged", so a wrong
# or missing verb hides whether the subscription is actually being renewed.
#
# The parse block is EXTRACTED FROM THE WRAPPER rather than restated here, so these tests exercise the
# shipped code and cannot drift from it.

_PWSH = shutil.which("pwsh") or shutil.which("powershell")
_needs_pwsh = pytest.mark.skipif(_PWSH is None, reason="PowerShell not available on this host")


def _action_parse_snippet():
    """The wrapper's own action-parsing block, from the fallback assignment to the success log."""
    text = _wrapper_text()
    start = text.index("$action = 'unknown'")
    end = text.index('Write-Log "SUCCESS:', start)
    return text[start:end]


def _run_action_parse(tmp_path, lines):
    """Run the wrapper's real parse block under StrictMode against a synthetic $output array."""
    literal = ", ".join("'" + ln.replace("'", "''") + "'" for ln in lines)
    script = (
        "Set-StrictMode -Version Latest\n"
        "$ErrorActionPreference = 'Stop'\n"
        f"$output = @({literal})\n"
        f"{_action_parse_snippet()}\n"
        "Write-Output \"ACTION=$action\"\n"
    )
    path = tmp_path / "parse.ps1"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)],
        capture_output=True, text=True, timeout=60,
    )


# -- static: the defect cannot come back --------------------------------------

def test_wrapper_never_reads_matches_after_an_array_match():
    """No CODE may read $Matches. The comment explaining the defect names it, and should."""
    code = [ln for ln in _wrapper_text().splitlines() if not ln.lstrip().startswith("#")]
    assert code, "no code lines found - the check would be vacuous"
    for line in code:
        assert "$Matches" not in line, (
            f"$Matches is unreliable here: -match on the $output ARRAY filters and never "
            f"populates it: {line.strip()}"
        )


def test_wrapper_matches_the_action_on_a_joined_scalar():
    """The array must be flattened before matching, and the match tested via its own result object."""
    snippet = _action_parse_snippet()
    assert "-join" in snippet, "the $output array must be joined into a scalar before matching"
    assert "[regex]::Match" in snippet, "an explicit scalar regex match is required"
    assert ".Success" in snippet and ".Groups[1].Value" in snippet


def test_wrapper_still_defaults_the_action_to_unknown():
    """No match must leave a readable placeholder, never an empty or undefined verb."""
    assert "$action = 'unknown'" in _action_parse_snippet()


def test_action_parse_only_runs_on_a_successful_child_exit():
    """A non-zero `--ensure` exit must log FAILURE and never report an action verb."""
    text = _wrapper_text()
    success_branch = text[text.index("if ($exitCode -eq 0) {"):text.index("catch {")]
    body, _, else_body = success_branch.partition("else {")
    assert "$action" in body, "action parsing is not in the success branch"
    assert "$action" not in else_body, "action parsing must not run on a failed child exit"
    assert "FAILURE: ensure exited with code $exitCode" in else_body


# -- behavioural: the real block, executed under StrictMode --------------------

@_needs_pwsh
@pytest.mark.parametrize("verb", ["unchanged", "renewed", "created"])
def test_action_parse_reports_the_verb_from_ensure_output(tmp_path, verb):
    """The production case: --ensure returns 'unchanged' and the log must say so."""
    proc = _run_action_parse(tmp_path, [
        "2026-09-01T12:00:00Z [INFO] Running: python -m scripts.manage_sharepoint_subscription --ensure",
        "{",
        f'  "action": "{verb}",',
        '  "subscription": {',
        '    "clientState": "***REDACTED***",',
        '    "id": "sub-1"',
        "  }",
        "}",
    ])
    assert proc.returncode == 0, proc.stderr
    assert f"ACTION={verb}" in proc.stdout


@_needs_pwsh
def test_action_parse_falls_back_to_unknown_when_the_verb_is_absent(tmp_path):
    proc = _run_action_parse(tmp_path, ["{", '  "subscription": {"id": "sub-1"}', "}"])
    assert proc.returncode == 0, proc.stderr
    assert "ACTION=unknown" in proc.stdout


@_needs_pwsh
def test_action_parse_falls_back_to_unknown_on_empty_output(tmp_path):
    proc = _run_action_parse(tmp_path, [])
    assert proc.returncode == 0, proc.stderr
    assert "ACTION=unknown" in proc.stdout


@_needs_pwsh
@pytest.mark.parametrize("lines", [
    ['  "action": "unchanged"'],
    ["no action here"],
    [],
])
def test_action_parse_never_trips_strict_mode(tmp_path, lines):
    """The regression itself: StrictMode + an unset $Matches turned a success into a failed run."""
    proc = _run_action_parse(tmp_path, lines)
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    assert "$Matches" not in combined
    assert "has not been set" not in combined


# --- 6. scheduled-task repetition duration -----------------------------------
#
# PRODUCTION DEFECT, TWICE. Registering the renewal task was rejected by Task Scheduler on two
# consecutive releases, each time with 0x80041318 ("The task XML contains a value which is
# incorrectly formatted or out of range"):
#
#     2ffbe4cd  -RepetitionDuration ([TimeSpan]::MaxValue)  ->  (14,42):Duration:P99999999DT23H59M59S
#     93c624ef  -RepetitionDuration ([TimeSpan]::Zero)      ->  (14,26):Duration:PT0S
#
# ONE schema rule explains both. Task Scheduler declares the element as
#
#     <xs:element name="Duration" minOccurs="0">
#       <xs:restriction base="duration"><xs:minInclusive value="PT1M"/></xs:restriction>
#
# and documents "If no value is specified for the duration, then the pattern is repeated
# indefinitely. The minimum value is one minute." MaxValue serializes ABOVE the accepted range;
# Zero serializes BELOW the PT1M floor. Indefinite repetition is therefore the ABSENCE of the
# element, not any value of it -- so the installer must not pass -RepetitionDuration at all.
# ([TimeSpan]::MaxValue was the Server 2012 idiom, where the cmdlet required both parameters
# together; Windows 10 / Server 2016+ accepts the interval alone.)
#
# Both failures reached production because this value is validated only by the Windows service when
# it parses the XML. These static checks pin the source; deploy/windows/Test-RenewalTaskRegistration
# .ps1 is the Windows-side counterpart that actually registers a disposable task and inspects the
# stored XML, which is the only place this class of defect can genuinely be caught.

_VALIDATOR = _REPO / "deploy" / "windows" / "Test-RenewalTaskRegistration.ps1"

# Every value that has been observed to be rejected, in both its PowerShell and serialized forms.
REJECTED_DURATIONS = [
    ("[TimeSpan]::MaxValue", "P99999999DT23H59M59S"),
    ("[TimeSpan]::Zero", "PT0S"),
]


def _executable_lines(asset):
    """``asset`` with PowerShell comments removed - both ``#`` lines and ``<# ... #>`` blocks.

    The defect commentary in these files quotes the rejected values on purpose, so the assertions
    below must look at executable code only or they would match the warning itself.
    """
    out, in_block = [], False
    for line in asset.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if in_block:
            if "#>" in line:
                in_block = False
            continue
        if stripped.startswith("<#"):
            if "#>" not in stripped[2:]:
                in_block = True
            continue
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_the_installer_passes_no_repetition_duration_at_all():
    """Indefinite repetition is the ABSENCE of a Duration. Any value here is a value that can be
    out of range, and both values tried so far were."""
    code = _executable_lines(_INSTALLER)
    assert "-RepetitionDuration" not in code, (
        "the installer passes -RepetitionDuration; omit it entirely so no Duration element is "
        "emitted, which is Task Scheduler's documented way to repeat indefinitely")


def test_the_interval_still_stands_alone_on_the_trigger():
    """Omitting the duration must not have taken the repetition with it — no interval, no renewal."""
    code = _executable_lines(_INSTALLER)
    assert re.search(r"-RepetitionInterval\s*\(New-TimeSpan\s+-Hours\s+\$IntervalHours\)", code), \
        "the trigger no longer repeats on the IntervalHours cadence"


@pytest.mark.parametrize("asset", [_WRAPPER, _INSTALLER, _VALIDATOR],
                         ids=["wrapper", "installer", "validator"])
@pytest.mark.parametrize("powershell,serialized", REJECTED_DURATIONS,
                         ids=["MaxValue", "Zero"])
def test_no_windows_asset_reintroduces_a_rejected_duration(asset, powershell, serialized):
    """Neither rejected value may come back, in either its PowerShell or its serialized form."""
    code = _executable_lines(asset)
    assert powershell not in code, (
        f"{asset.name} uses {powershell}, which serializes to {serialized} and is rejected by "
        "Task Scheduler with 0x80041318")
    # The validator names the serialized forms inside string literals on purpose (it asserts their
    # absence from the exported XML), so only the installer/wrapper are checked for those.
    if asset is not _VALIDATOR:
        assert serialized not in code, f"{asset.name} hard-codes the rejected duration {serialized}"


def test_execution_time_limit_stays_bounded():
    """The 10-minute cap is the hung-run guard and was never the cause of either rejection —
    neither fix may quietly turn it into an unlimited limit."""
    text = _installer_text()
    match = re.search(r"-ExecutionTimeLimit\s*\(([^)]*)\)", text)
    assert match, "no -ExecutionTimeLimit found"
    limit = match.group(1)
    assert "New-TimeSpan" in limit and "TimeSpan]::Zero" not in limit, (
        f"-ExecutionTimeLimit is {limit!r}; an unlimited limit lets a hung run block the next")
    assert int(re.search(r"-Minutes\s+(\d+)", limit).group(1)) <= 60


def test_the_installer_refuses_a_platform_that_cannot_express_indefinite_repetition():
    """On Server 2012 the interval cannot stand alone. Fail loudly rather than register a task that
    silently stops repeating."""
    text = _installer_text()
    guard = re.search(r"if \(\[Environment\]::OSVersion[^}]*\}", text, re.DOTALL)
    assert guard, "no OS-version guard found"
    assert "throw" in guard.group(0)


def test_the_task_contract_survives_the_duration_fix():
    """Everything the deployment depends on must be unchanged by this fix."""
    text = _installer_text()
    assert "'Client360 SharePoint Subscription Renewal'" in text
    assert "NT AUTHORITY\\SYSTEM" in text and "ServiceAccount" in text
    assert re.search(r"\$IntervalHours\s*=\s*4", text)
    assert "-StartWhenAvailable" in text
    assert "MultipleInstances IgnoreNew" in text
    assert "Renew-SharePointSubscription.ps1" in text


# --- 7. the Windows-side registration test -----------------------------------

def test_a_windows_registration_validator_exists():
    """Static checks cannot catch a value only the Task Scheduler service validates. A Windows-side
    test that actually registers the task is the only thing that can."""
    assert _VALIDATOR.is_file(), f"missing {_VALIDATOR.name}"


def test_the_validator_drives_the_real_installer():
    """It must exercise the shipped installer, not a reconstructed copy of its trigger that would
    drift away from the thing being deployed."""
    text = _VALIDATOR.read_text(encoding="utf-8")
    assert "Install-SharePointRenewalTask.ps1" in text
    assert "New-ScheduledTaskTrigger" not in _executable_lines(_VALIDATOR), \
        "the validator builds its own trigger; it must call the installer instead"


def test_the_validator_inspects_the_stored_xml_for_both_rejected_durations():
    text = _VALIDATOR.read_text(encoding="utf-8")
    assert "Export-ScheduledTask" in text, "the validator must inspect the XML the service stored"
    assert "P99999999" in text and "PT0S" in text, \
        "the validator must assert both rejected durations are absent from the stored XML"
    assert "Duration" in text


def test_the_validator_cannot_touch_the_production_task():
    """It registers and DELETES its task, so it must refuse the production name and clean up."""
    text = _VALIDATOR.read_text(encoding="utf-8")
    assert "Refusing to run against the production task name" in text
    assert "SELFTEST" in text
    assert "finally" in text and "Unregister-ScheduledTask" in text
    assert "Disable-ScheduledTask" in text, "the disposable task must not be able to fire"
