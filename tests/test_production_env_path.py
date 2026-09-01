"""The Windows production environment file is C:\\Client360\\app\\.env — one path, everywhere.

The recurring production incident these tests exist to prevent: every env-file reference used to be
RELATIVE (``app\\.env`` / ``app/.env``). A relative path only resolves correctly while the working
directory (or the NSSM ``AppDirectory``) is ``C:\\Client360``, and it leaves an operator to infer the
absolute path — so configuration was repeatedly written to ``C:\\Client360\\.env`` or
``C:\\Client360\\app.env``, which the running service never loads. ``check-config`` then reported OK
from the ambient process environment, making the wrong edit look successful.

These tests pin the canonical path, the detection of a non-canonical NSSM ``--env-file``, the
absence of secret values in validation output, and consistency across the deployment scripts. They
are pure — no Windows, no NSSM, no service required.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.deploy import config_check, service

REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DEPLOY = REPO_ROOT / "deploy" / "windows"

CANONICAL = "C:\\Client360\\app\\.env"
AMBIGUOUS = ("C:\\Client360\\.env", "C:\\Client360\\app.env")


# --- the canonical path ----------------------------------------------------------

def test_canonical_production_env_path_is_declared_absolutely():
    """NSSM already starts the service with this exact path; the code must name the same one."""
    assert service.PRODUCTION_ENV_FILE == CANONICAL
    assert service.DEFAULT_ENV_FILE == CANONICAL, (
        "the default env file must be the absolute canonical path — a relative 'app\\.env' silently "
        "depends on the working directory and is what caused the ambiguity")


def test_the_two_ambiguous_siblings_are_declared_by_name():
    """Tooling warns about these by name rather than relying on operator folklore."""
    assert set(service.AMBIGUOUS_ENV_FILES) == set(AMBIGUOUS)
    assert service.PRODUCTION_ENV_FILE not in service.AMBIGUOUS_ENV_FILES


def test_generated_service_commands_pass_the_canonical_env_file():
    for builder in (service.nssm_commands, service.sc_commands):
        flat = " ".join(" ".join(c) for c in builder("install"))
        assert "--env-file" in flat, "the service would boot with no production configuration"
        assert CANONICAL in flat, f"{builder.__name__} did not pass the canonical env file"
        for bad in AMBIGUOUS:
            assert f"--env-file {bad}" not in flat


# --- detecting a wrong NSSM env path ---------------------------------------------

@pytest.mark.parametrize("configured, expected", [
    (f"-m uvicorn app.main:app --host 127.0.0.1 --port 8360 --env-file {CANONICAL}", CANONICAL),
    (f'-m uvicorn app.main:app --env-file "{CANONICAL}"', CANONICAL),
    (f"-m uvicorn app.main:app --env-file={CANONICAL}", CANONICAL),
    ("-m uvicorn app.main:app --env-file app\\.env", "app\\.env"),
    ("-m uvicorn app.main:app --env-file C:\\Client360\\.env", "C:\\Client360\\.env"),
    ("-m uvicorn app.main:app --host 127.0.0.1", None),          # no --env-file at all
    ("", None),
    (None, None),
])
def test_env_file_is_extracted_from_nssm_app_parameters(configured, expected):
    assert service.env_file_from_app_parameters(configured) == expected


def test_only_the_canonical_path_is_accepted():
    assert service.env_file_is_canonical(CANONICAL) is True
    # Windows paths are case-insensitive and NSSM echoes back whatever was typed.
    assert service.env_file_is_canonical("c:\\client360\\app\\.env") is True
    assert service.env_file_is_canonical(f'"{CANONICAL}"') is True
    assert service.env_file_is_canonical("C:/Client360/app/.env") is True


@pytest.mark.parametrize("wrong", [
    "C:\\Client360\\.env",              # the root sibling
    "C:\\Client360\\app.env",           # the dot-instead-of-separator sibling
    "app\\.env",                        # relative — resolves only if cwd happens to be right
    "app/.env",
    "C:\\Client360\\app\\env",
    "D:\\Client360\\app\\.env",         # right shape, wrong root
    "",
    None,
])
def test_a_non_canonical_env_path_is_rejected(wrong):
    assert service.env_file_is_canonical(wrong) is False


def test_a_service_configured_with_a_sibling_is_detected_as_a_mismatch():
    """The exact production failure: the service loads a sibling while operators edit the real file."""
    for sibling in AMBIGUOUS:
        params = f"-m uvicorn app.main:app --host 127.0.0.1 --port 8360 --env-file {sibling}"
        found = service.env_file_from_app_parameters(params)
        assert found == sibling
        assert service.env_file_is_canonical(found) is False


# --- check-config resolves, reports, and never leaks -------------------------------

def _write_env(directory: Path, secret: str) -> Path:
    (directory / "app").mkdir(parents=True, exist_ok=True)
    env = directory / "app" / ".env"
    env.write_text(
        "CLIENT360_ENVIRONMENT=production\nDATABASE_URL=postgresql://h/db\n"
        f"SESSION_SECRET={secret}\nOIDC_ISSUER=https://i\nOIDC_CLIENT_ID=c\n"
        f"OIDC_CLIENT_SECRET={secret}\nVAULT_STORAGE_ROOT={directory / 'vault'}\n"
        f"IMAGE_DERIVATIVE_ROOT={directory / 'derivatives'}\n")
    return env


def _clear_config_env(monkeypatch):
    for var in ("CLIENT360_ENVIRONMENT", "DATABASE_URL", "SESSION_SECRET", "OIDC_ISSUER",
                "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "VAULT_STORAGE_ROOT",
                "IMAGE_DERIVATIVE_ROOT"):
        monkeypatch.delenv(var, raising=False)


def test_check_config_reports_which_env_file_it_loaded(tmp_path, monkeypatch, capsys):
    """An operator must be able to see that validation read the file they just edited."""
    _write_env(tmp_path, "a-strong-secret")
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    assert config_check.main([]) == 0
    out = capsys.readouterr().out
    assert "Environment file:" in out and "app/.env" in out


def test_check_config_never_prints_a_configuration_value(tmp_path, monkeypatch, capsys):
    """Names, paths and states only — the whole point of the preflight being safe to run and paste."""
    canary = "SUPERSECRET-CANARY-VALUE-9f3a"
    _write_env(tmp_path, canary)
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    config_check.main([])
    out = capsys.readouterr().out
    assert canary not in out, "a configuration VALUE was printed by the config preflight"
    assert "SESSION_SECRET" in out, "variable names should still be reported"


def test_check_config_warns_when_an_ambiguous_sibling_exists(tmp_path, monkeypatch, capsys):
    """The sibling is never read — only its existence is reported, by path."""
    _write_env(tmp_path, "a-strong-secret")
    stray = tmp_path / "stray.env"
    stray.write_text("SESSION_SECRET=NOT-READ-BY-THE-VALIDATOR\n")
    monkeypatch.setattr(service, "AMBIGUOUS_ENV_FILES", (str(stray),))
    monkeypatch.chdir(tmp_path)
    _clear_config_env(monkeypatch)
    config_check.main([])
    out = capsys.readouterr().out
    assert str(stray) in out and "NOT the file the service loads" in out
    assert "NOT-READ-BY-THE-VALIDATOR" not in out, "the sibling's contents were read out"


def test_check_config_falls_back_to_the_canonical_path_from_the_wrong_directory(
        tmp_path, monkeypatch):
    """Run from the wrong cwd on the server, validation must still find the real production file
    instead of silently validating the ambient process environment."""
    env = _write_env(tmp_path, "a-strong-secret")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(service, "PRODUCTION_ENV_FILE", str(env))
    _clear_config_env(monkeypatch)
    loaded = config_check.load_environment_file()
    assert loaded["env_file"] == str(env)


def test_repo_relative_env_file_still_wins_for_development(tmp_path, monkeypatch):
    """Dev/CI/Docker are unaffected: a checkout keeps loading its own app/.env, never the server's."""
    _write_env(tmp_path, "dev-secret")
    other = tmp_path / "production"
    other.mkdir()
    (other / ".env").write_text("SESSION_SECRET=production-value\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "PRODUCTION_ENV_FILE", str(other / ".env"))
    _clear_config_env(monkeypatch)
    assert config_check.load_environment_file()["env_file"] == "app/.env"


# --- the PowerShell tooling agrees with the Python tooling --------------------------

def test_the_production_env_validator_script_exists():
    assert (WINDOWS_DEPLOY / "validate_production_env.ps1").is_file()


@pytest.mark.parametrize("script", ["Install-Client360Service.ps1", "Deploy-Client360.ps1",
                                    "validate_production_env.ps1", "README.md",
                                    "client360.env.example"])
def test_windows_deploy_files_name_the_canonical_path(script):
    text = (WINDOWS_DEPLOY / script).read_text()
    assert CANONICAL in text, f"{script} does not name the canonical production env file"


def test_no_windows_deploy_script_configures_an_ambiguous_path():
    """The siblings may only appear as warnings, never as a value passed to --env-file or -EnvFile."""
    for script in WINDOWS_DEPLOY.glob("*.ps1"):
        text = script.read_text()
        for bad in AMBIGUOUS:
            assert f"--env-file {bad}" not in text
            assert f"-EnvFile '{bad}'" not in text
            assert f"$EnvFile     = '{bad}'" not in text


def test_the_installer_defaults_to_the_canonical_env_file_and_guards_it():
    text = (WINDOWS_DEPLOY / "Install-Client360Service.ps1").read_text()
    assert f"[string]$EnvFile     = '{CANONICAL}'" in text, "installer default is not canonical"
    assert "AllowNonCanonicalEnvFile" in text, "installing a non-canonical env file is unguarded"
    assert "Refusing to install" in text


def test_deployment_validates_the_env_path_before_restarting_the_service():
    """Order matters: a service pointed at the wrong file must fail the deploy, not be started."""
    text = (WINDOWS_DEPLOY / "Deploy-Client360.ps1").read_text()
    validate_at = text.find("validate_production_env.ps1")
    restart_at = text.find("'-Action','restart'")
    assert validate_at != -1, "the deploy sequence never validates the production env path"
    assert restart_at != -1
    assert validate_at < restart_at, "validation runs after the restart — too late to prevent it"


def test_the_validator_reports_variable_names_and_never_values():
    """Structural guard: the value side of each line is split off and discarded, never emitted."""
    text = (WINDOWS_DEPLOY / "validate_production_env.ps1").read_text()
    assert ".Split('=', 2)[0]" in text, "the validator does not reduce each line to its name"
    # Nothing that could hold a line, a value, or file contents may be written to the host.
    forbidden = re.compile(r"Write-(Host|Output|Warning|Information)[^\n]*\$(line|value|content|raw)\b")
    assert not forbidden.search(text), "the validator writes raw environment-file content to output"
    assert "Get-Content" in text and "$EnvFile" in text          # it does read the file...
    assert "$value" not in text, "the validator binds a value; it must never hold one"


def test_the_validator_is_read_only_and_never_touches_production_files():
    """It must never write, rename, delete, or restart anything."""
    text = (WINDOWS_DEPLOY / "validate_production_env.ps1").read_text()
    for destructive in ("Remove-Item", "Move-Item", "Rename-Item", "Set-Content", "Out-File",
                        "Restart-Service", "Stop-Service", "nssm restart", "nssm set"):
        assert destructive not in text, f"the validator performs a mutating action: {destructive}"


def test_the_readme_states_the_canonical_path_and_the_warning():
    text = (WINDOWS_DEPLOY / "README.md").read_text().replace("`", "")
    assert f"PRODUCTION ENVIRONMENT FILE: {CANONICAL}" in text
    for bad in AMBIGUOUS:
        assert bad in text, f"{bad} is not called out as a non-production file"
    assert "NOT the production runtime env file" in text
    assert "never edit another env file" in text.lower()


def test_the_cleanup_plan_is_documented_but_not_executed_by_any_script():
    """The two ambiguous files must be archived as a separate, explicit production step."""
    readme = (WINDOWS_DEPLOY / "README.md").read_text()
    assert "config-archive" in readme and "root-dot-env-" in readme
    assert "legacy-app-env-" in readme
    for script in WINDOWS_DEPLOY.glob("*.ps1"):
        text = script.read_text()
        for bad in AMBIGUOUS:
            for destructive in ("Remove-Item", "Move-Item", "Rename-Item"):
                assert f"{destructive} -LiteralPath '{bad}'" not in text, (
                    f"{script.name} deletes or renames {bad}; cleanup must be a separate step")
