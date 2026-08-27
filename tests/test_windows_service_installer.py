"""The Windows installer must hand NSSM the COMPLETE uvicorn command line.

The defect these tests pin: ``Install-Client360Service.ps1`` called

    Invoke-Nssm @('install',$ServiceName,$Python) + $uvicornArgs

PowerShell parses a command's arguments in *argument mode*, where ``+`` is just another positional
argument — not an operator. So the arrays were never concatenated: the helper received only the
first array, and every uvicorn argument (``-m uvicorn app.main:app --host --port`` and, critically,
``--env-file``) never reached nssm. The identical ``+`` in the sc.exe branch is *expression* mode
(an assignment right-hand side), where it does concatenate — which is what made this subtle.

The consequence in production was a service installed without ``--env-file``, hand-repaired on the
server, and thereafter drifted from the repository's intent with nothing reconciling the two.

PowerShell cannot be executed on the development machine, so these tests reconstruct the effective
nssm argv from the script text — resolving the param() defaults and the array construction — and
assert on the exact resulting sequence. See deploy/windows/README.md for the production-side
PowerShell dry run that verifies the same thing on Windows.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.deploy import service

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "deploy" / "windows" / "Install-Client360Service.ps1"

CANONICAL_ENV_FILE = "C:\\Client360\\app\\.env"


@pytest.fixture(scope="module")
def script() -> str:
    return INSTALLER.read_text()


# --- a small reader for the subset of PowerShell this script uses -------------------

def _param_defaults(text: str) -> dict[str, str]:
    """``[string]$Name = 'value'`` / ``[int]   $Port = 8360`` from the param() block."""
    defaults = dict(re.findall(r"\[string\]\s*\$(\w+)\s*=\s*'([^']*)'", text))
    defaults.update(re.findall(r"\[int\]\s*\$(\w+)\s*=\s*(\d+)", text))
    return defaults


def _split_top_level(expression: str, separator: str) -> list[str]:
    """Split on ``separator`` only outside quotes and parentheses."""
    parts, depth, quote, current = [], 0, None, ""
    for char in expression:
        if quote:
            current += char
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote, current = char, current + char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == separator and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def _resolve_token(token: str, scope: dict[str, object]) -> str:
    """A single array element: 'literal', "interpolated $Var", or a bare $Var."""
    token = token.strip()
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1]
    if token.startswith('"') and token.endswith('"'):
        inner = token[1:-1]
        return re.sub(r"\$(\w+)", lambda m: str(scope.get(m.group(1), m.group(0))), inner)
    if token.startswith("$"):
        name = token[1:]
        assert name in scope, f"unresolved variable ${name}"
        return str(scope[name])
    return token


def _resolve_rhs(rhs: str, scope: dict[str, object]) -> list[str]:
    """Evaluate an assignment right-hand side of ``@(...)`` terms joined by ``+``."""
    result: list[str] = []
    for term in _split_top_level(rhs, "+"):
        term = term.strip()
        if term.startswith("@(") and term.endswith(")"):
            result += [_resolve_token(t, scope)
                       for t in _split_top_level(term[2:-1], ",")]
        elif term.startswith("$"):
            value = scope[term[1:]]
            assert isinstance(value, list), f"{term} is not an array"
            result += value
        else:                                    # pragma: no cover - guards a shape change
            raise AssertionError(f"unsupported term in assignment: {term!r}")
    return result


def _assignment(text: str, variable: str) -> str:
    match = re.search(rf"^\s*\${variable}\s*=\s*(.+)$", text, re.MULTILINE)
    assert match, f"${variable} is never assigned in the installer"
    return match.group(1).strip()


def _effective_install_argv(text: str) -> list[str]:
    """The argv the script would hand to nssm.exe for ``-Action install``, using param() defaults."""
    scope: dict[str, object] = dict(_param_defaults(text))
    scope["uvicornArgs"] = _resolve_rhs(_assignment(text, "uvicornArgs"), scope)
    return _resolve_rhs(_assignment(text, "installArgs"), scope)


def _invoke_nssm_call_sites(text: str) -> list[str]:
    """The argument text of every ``Invoke-Nssm`` CALL (never the function definition)."""
    sites = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "Invoke-Nssm" not in stripped:
            continue
        if re.search(r"function\s+Invoke-Nssm", stripped):
            continue
        for match in re.finditer(r"Invoke-Nssm\s+(.+?)(?:\s*(?:;|\}|#).*)?$", stripped):
            sites.append(match.group(1).strip())
    return sites


# --- the reader itself is trustworthy ----------------------------------------------

#: The defect exactly as it was written, for the guard-the-guard tests below.
BROKEN_CALL = "      Invoke-Nssm @('install',$ServiceName,$Python) + $uvicornArgs"


def test_the_shape_detector_fires_on_the_original_defect():
    """Guard the guard: the argument-mode detector must reject the real pre-fix line."""
    sites = _invoke_nssm_call_sites(BROKEN_CALL)
    assert sites == ["@('install',$ServiceName,$Python) + $uvicornArgs"], (
        "the call-site reader did not see the defective invocation at all")
    assert re.search(r"\)\s*\+", sites[0]), "the argument-mode concatenation went undetected"
    # ...and the corrected form is not falsely flagged.
    assert not re.search(r"\)\s*\+", _invoke_nssm_call_sites("      Invoke-Nssm $installArgs")[0])


def test_the_argv_reader_detects_the_original_defect():
    """On the pre-fix shape there is no assembled argument list to read at all."""
    broken = (
        "  [string]$ServiceName = 'Client360',\n"
        "  [string]$Python      = 'C:\\py.exe',\n"
        "  [string]$BindHost    = '127.0.0.1',\n"
        "  [int]   $Port        = 8360,\n"
        "  [string]$EnvFile     = 'C:\\Client360\\app\\.env'\n"
        "$uvicornArgs = @('-m','uvicorn','app.main:app','--env-file',$EnvFile)\n"
        + BROKEN_CALL + "\n"
    )
    # The pre-fix script never assigned $installArgs — the concatenation happened (or rather, failed
    # to happen) inline at the call site, which is precisely why nothing could inspect it.
    with pytest.raises(AssertionError, match="installArgs is never assigned"):
        _effective_install_argv(broken)


# --- the corrected invocation --------------------------------------------------------

def test_the_install_argv_is_complete_and_in_order(script):
    """The whole point: every element nssm needs, in the right order, in one invocation."""
    argv = _effective_install_argv(script)
    assert argv == [
        "install",
        "Client360",                                    # service name
        "C:\\Client360\\.venv\\Scripts\\python.exe",    # python executable
        "-m",
        "uvicorn",
        "app.main:app",                                 # the production ASGI app, never the demo
        "--host", "127.0.0.1",
        "--port", "8360",
        "--env-file", CANONICAL_ENV_FILE,
    ]


def test_the_install_argv_carries_the_canonical_env_file(script):
    """The argument whose loss caused the production drift."""
    argv = _effective_install_argv(script)
    assert "--env-file" in argv, "the service would be installed with no production configuration"
    assert argv[argv.index("--env-file") + 1] == CANONICAL_ENV_FILE
    assert CANONICAL_ENV_FILE == service.PRODUCTION_ENV_FILE, (
        "the installer and app.deploy.service disagree about the canonical env file")


def test_the_uvicorn_arguments_are_not_lost_between_construction_and_invocation(script):
    """A regression here means the array is built correctly but never actually passed."""
    scope: dict[str, object] = dict(_param_defaults(script))
    uvicorn_args = _resolve_rhs(_assignment(script, "uvicornArgs"), scope)
    argv = _effective_install_argv(script)
    assert uvicorn_args, "$uvicornArgs is empty"
    assert argv[-len(uvicorn_args):] == uvicorn_args, "the uvicorn arguments never reached nssm"


# --- the argument-shape defect cannot come back ---------------------------------------

def test_no_invoke_nssm_call_concatenates_in_argument_mode(script):
    """The defect's exact shape: `Invoke-Nssm @(...) + $more` passes `+` as a positional argument."""
    for arguments in _invoke_nssm_call_sites(script):
        assert not re.search(r"\)\s*\+", arguments), (
            f"Invoke-Nssm {arguments!r} concatenates in argument mode; PowerShell passes '+' as a "
            "positional argument. Build the array first, then invoke.")


def test_every_invoke_nssm_call_passes_exactly_one_argument_expression(script):
    """Each call site must be a single array literal or a single array variable — nothing else."""
    sites = _invoke_nssm_call_sites(script)
    assert len(sites) >= 8, f"expected every nssm call to be checked, found {len(sites)}"
    for arguments in sites:
        assert re.fullmatch(r"@\(.*\)|\$\w+", arguments), (
            f"Invoke-Nssm {arguments!r} is not a single argument expression")


def test_the_helper_does_not_shadow_the_automatic_args_variable(script):
    """$Args is a PowerShell automatic variable; shadowing it is what hid the parsing bug."""
    assert "[string[]]$NssmArgs" in script, "the helper parameter was not renamed"
    assert not re.search(r"param\(\s*\[string\[\]\]\$Args\s*\)", script)
    assert "@NssmArgs" in script, "the renamed parameter is not the one being splatted"
    assert not re.search(r"&\s*\$nssm\.Source\s+@Args\b", script)


def test_the_original_broken_expression_is_gone(script):
    assert "Invoke-Nssm @('install',$ServiceName,$Python) + $uvicornArgs" not in script


def test_the_sc_fallback_still_concatenates_in_expression_mode(script):
    """Contrast worth keeping: `+` on an assignment RHS is an operator and was never broken."""
    assert "$binPath = '\"' + $Python + '\" ' + ($uvicornArgs -join ' ')" in script


def test_the_installer_configures_service_persistence(script):
    """Unchanged behaviour the fix must not disturb."""
    for setting in ("AppDirectory", "AppStdout", "AppStderr", "AppRotateFiles",
                    "SERVICE_AUTO_START", "AppExit"):
        assert setting in script


def test_the_readme_documents_a_safe_windows_dry_run():
    """PowerShell cannot run here, so the server-side verification must be written down."""
    readme = (REPO_ROOT / "deploy" / "windows" / "README.md").read_text()
    assert "dry run" in readme.lower()
    assert "ParseFile" in readme, "no parse check is documented"
    assert "nssm.cmd" in readme, "no argv-preview shim is documented"
