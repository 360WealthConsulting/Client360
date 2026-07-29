"""Windows service command construction for Client360.

Client360 runs as ``uvicorn app.main:app`` (the production entry point — NEVER the demo app). uvicorn
is not itself a Windows service, so the service wraps the python process. This module builds the exact
commands for the two supported managers — NSSM (preferred, gives auto-restart + auto-start + log
redirection) and the built-in ``sc.exe`` (fallback) — for install/start/stop/restart/status/uninstall.
The PowerShell wrapper (deploy/windows/) executes these; keeping construction here makes it testable
and guarantees the entry point is app.main:app, bound to a configurable host/port.
"""
from __future__ import annotations

import shlex

APP_TARGET = "app.main:app"          # the production ASGI app — asserted by tests, never demo_app
ACTIONS = ("install", "start", "stop", "restart", "status", "uninstall")


DEFAULT_ENV_FILE = "app\\.env"           # production config the app relies on (loaded by uvicorn)


def uvicorn_args(host: str, port: int, env_file: str = DEFAULT_ENV_FILE) -> list[str]:
    # --env-file is REQUIRED: app.config reads SESSION_SECRET at import, before app.db loads the
    # dotenv, so uvicorn must load the production environment file itself or the app cannot boot.
    args = ["-m", "uvicorn", APP_TARGET, "--host", str(host), "--port", str(port)]
    if env_file:
        args += ["--env-file", env_file]
    return args


def nssm_commands(action: str, *, service_name="Client360", nssm="nssm",
                  python="python", host="127.0.0.1", port=8360,
                  workdir="C:\\Client360", log_dir="C:\\Client360\\logs",
                  env_file=DEFAULT_ENV_FILE) -> list[list[str]]:
    """Return the NSSM command(s) (each an argv list) for the action."""
    if action == "install":
        return [
            [nssm, "install", service_name, python, *uvicorn_args(host, port, env_file)],
            [nssm, "set", service_name, "AppDirectory", workdir],
            [nssm, "set", service_name, "AppStdout", f"{log_dir}\\service-stdout.log"],
            [nssm, "set", service_name, "AppStderr", f"{log_dir}\\service-stderr.log"],
            [nssm, "set", service_name, "AppRotateFiles", "1"],
            [nssm, "set", service_name, "Start", "SERVICE_AUTO_START"],    # start after reboot
            [nssm, "set", service_name, "AppExit", "Default", "Restart"],  # auto-restart on failure
        ]
    if action == "uninstall":
        return [[nssm, "stop", service_name], [nssm, "remove", service_name, "confirm"]]
    if action in ("start", "stop", "restart", "status"):
        return [[nssm, action, service_name]]
    raise ValueError(f"Unknown action: {action}")


def sc_commands(action: str, *, service_name="Client360", python="python",
                host="127.0.0.1", port=8360, workdir="C:\\Client360",
                env_file=DEFAULT_ENV_FILE) -> list[list[str]]:
    """Fallback using the built-in sc.exe (no auto-restart configuration beyond the OS default)."""
    if action == "install":
        bin_path = f'"{python}" ' + " ".join(uvicorn_args(host, port, env_file))
        return [["sc.exe", "create", service_name, "binPath=", bin_path, "start=", "auto"]]
    if action == "uninstall":
        return [["sc.exe", "stop", service_name], ["sc.exe", "delete", service_name]]
    if action == "start":
        return [["sc.exe", "start", service_name]]
    if action == "stop":
        return [["sc.exe", "stop", service_name]]
    if action == "restart":
        return [["sc.exe", "stop", service_name], ["sc.exe", "start", service_name]]
    if action == "status":
        return [["sc.exe", "query", service_name]]
    raise ValueError(f"Unknown action: {action}")


def render(commands: list[list[str]]) -> str:
    return "\n".join(shlex.join(c) for c in commands)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Print the Windows service commands for Client360.")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--manager", choices=("nssm", "sc"), default="nssm")
    parser.add_argument("--service-name", default="Client360")
    parser.add_argument("--python", default="C:\\Client360\\.venv\\Scripts\\python.exe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8360)
    parser.add_argument("--workdir", default="C:\\Client360")
    parser.add_argument("--log-dir", default="C:\\Client360\\logs")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    args = parser.parse_args(argv)
    if args.manager == "nssm":
        cmds = nssm_commands(args.action, service_name=args.service_name, python=args.python,
                             host=args.host, port=args.port, workdir=args.workdir,
                             log_dir=args.log_dir, env_file=args.env_file)
    else:
        cmds = sc_commands(args.action, service_name=args.service_name, python=args.python,
                           host=args.host, port=args.port, workdir=args.workdir, env_file=args.env_file)
    print(render(cmds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
