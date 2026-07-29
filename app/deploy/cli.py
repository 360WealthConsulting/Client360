"""Client360 deployment CLI: ``python -m app.deploy <command> [args]``.

Subcommands (each delegates to a focused module; all reuse existing app services):
  check-config   validate production configuration (fail-fast, secret-free)
  migrate        report current→target revision and upgrade to head (non-destructive)
  grant-admin    grant the administrator role to an existing Entra user (idempotent)
  smoke          route-registration + auth-gating checks (+ live HTTP with --url)
  service        print the Windows service commands (install/start/stop/restart/status/uninstall)
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.deploy",
                                     description="Client360 deployment readiness tooling.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check-config", help="validate production configuration (fail-fast)")
    m = sub.add_parser("migrate", help="report current/target and upgrade to head")
    m.add_argument("--backup", action="store_true")
    m.add_argument("--plan", action="store_true")
    g = sub.add_parser("grant-admin", help="grant the administrator role to an existing user")
    g.add_argument("--email")
    g.add_argument("--subject")
    g.add_argument("--yes", action="store_true")
    g.add_argument("--noninteractive", action="store_true")
    s = sub.add_parser("smoke", help="deployment smoke test")
    s.add_argument("--url")
    sv = sub.add_parser("service", help="print Windows service commands")
    sv.add_argument("action", choices=("install", "start", "stop", "restart", "status", "uninstall"))
    sv.add_argument("--manager", choices=("nssm", "sc"), default="nssm")
    sv.add_argument("--service-name", default="Client360")
    sv.add_argument("--python", default="C:\\Client360\\.venv\\Scripts\\python.exe")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8360)
    sv.add_argument("--workdir", default="C:\\Client360")
    sv.add_argument("--log-dir", default="C:\\Client360\\logs")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check-config":
        from app.deploy import config_check
        return config_check.main([])
    if args.command == "migrate":
        from app.deploy import migrate
        extra = (["--plan"] if args.plan else []) + (["--backup"] if args.backup else [])
        return migrate.main(extra)
    if args.command == "grant-admin":
        from app.deploy import admin
        extra = []
        if args.email:
            extra += ["--email", args.email]
        if args.subject:
            extra += ["--subject", args.subject]
        if args.yes:
            extra.append("--yes")
        if args.noninteractive:
            extra.append("--noninteractive")
        return admin.main(extra)
    if args.command == "smoke":
        from app.deploy import smoke
        return smoke.main(["--url", args.url] if args.url else [])
    if args.command == "service":
        from app.deploy import service
        extra = [args.action, "--manager", args.manager, "--service-name", args.service_name,
                 "--python", args.python, "--host", args.host, "--port", str(args.port),
                 "--workdir", args.workdir, "--log-dir", args.log_dir]
        return service.main(extra)
    parser.print_help()
    return 2
