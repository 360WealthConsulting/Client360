#!/usr/bin/env python
"""Issue, list and revoke MCP access tokens (operator CLI).

    python scripts/mcp_token.py issue  --email advisor@firm.com --scopes client:read,document:read
    python scripts/mcp_token.py list   [--email advisor@firm.com]
    python scripts/mcp_token.py revoke --id 7
    python scripts/mcp_token.py revoke --email advisor@firm.com --all

An issued token is printed ONCE and never recoverable — only its SHA-256 is stored. Capture it into
the client configuration immediately; if it is lost, revoke it and issue another.

This CLI does not GRANT anything. A token can only ever reach what its owner's roles already permit
(see app/mcp/auth.py); if the owner lacks ``mcp.access``, every call with the token is denied. That
capability is granted through the normal role administration, not here.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db import engine, users
from app.mcp import tokens as mcp_tokens
from app.mcp.scopes import ALL_SCOPES, CAPABILITY_MCP_ACCESS, normalize_scopes


def _user_id(email: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(select(users.c.id, users.c.status).where(
            users.c.normalized_email == email.strip().lower())).mappings().first()
    if row is None:
        raise SystemExit(f"No user with email {email!r}")
    if row["status"] != "active":
        raise SystemExit(f"User {email!r} is not active (status={row['status']})")
    return row["id"]


def _warn_if_ungranted(user_id: int, email: str) -> None:
    """Say so plainly when the owner cannot actually use the token yet.

    Issuing a token to someone without ``mcp.access`` is legal but useless, and silently handing back
    a credential that denies every call is the kind of thing an operator discovers at 6pm.
    """
    from app.db import capabilities, role_capabilities, roles, user_roles
    from app.security.policy import capability_codes_query
    with engine.connect() as conn:
        codes = set(conn.scalars(capability_codes_query(
            user_id, users=users, user_roles=user_roles, roles=roles,
            role_capabilities=role_capabilities, capabilities=capabilities)))
    if CAPABILITY_MCP_ACCESS not in codes:
        print(f"WARNING: {email} does not hold {CAPABILITY_MCP_ACCESS}; every call with this token "
              f"will be denied until a role granting it is assigned.", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mcp_token", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", help="mint a token")
    p_issue.add_argument("--email", required=True, help="staff user the token acts as")
    p_issue.add_argument("--scopes", required=True,
                         help=f"comma-separated, from: {', '.join(ALL_SCOPES)}")
    p_issue.add_argument("--label", default="", help="what this token is for (shown in listings)")
    p_issue.add_argument("--ttl-hours", type=int, default=None, help="lifetime (default from config)")

    p_list = sub.add_parser("list", help="show tokens (never secrets)")
    p_list.add_argument("--email", default=None)

    p_revoke = sub.add_parser("revoke", help="revoke a token, or all of a user's")
    p_revoke.add_argument("--id", type=int, default=None)
    p_revoke.add_argument("--email", default=None)
    p_revoke.add_argument("--all", action="store_true", help="revoke every live token for --email")

    args = parser.parse_args(argv)

    if args.command == "issue":
        requested = [s.strip() for s in args.scopes.split(",") if s.strip()]
        granted = normalize_scopes(requested)
        unknown = sorted(set(requested) - set(granted))
        if unknown:
            raise SystemExit(f"Unknown scope(s): {', '.join(unknown)}. "
                             f"Valid scopes: {', '.join(ALL_SCOPES)}")
        user_id = _user_id(args.email)
        _warn_if_ungranted(user_id, args.email)
        secret = mcp_tokens.issue_token(user_id=user_id, scopes=granted, label=args.label,
                                        ttl_hours=args.ttl_hours)
        print(f"user   : {args.email}")
        print(f"scopes : {', '.join(granted)}")
        print("token  : shown once, store it now")
        print(secret)
        return 0

    if args.command == "list":
        user_id = _user_id(args.email) if args.email else None
        rows = mcp_tokens.list_tokens(user_id=user_id)
        if not rows:
            print("No MCP tokens.")
            return 0
        for r in rows:
            state = ("revoked" if r["revoked_at"] else "active")
            print(f"#{r['id']:<5} user={r['user_id']:<5} {state:<8} "
                  f"scopes={','.join(normalize_scopes(r['scopes'])) or '-':<45} "
                  f"expires={r['expires_at']} last_used={r['last_used_at']} "
                  f"label={r['label'] or '-'}")
        return 0

    if args.command == "revoke":
        if args.all:
            if not args.email:
                raise SystemExit("--all requires --email")
            count = mcp_tokens.revoke_all_for_user(_user_id(args.email))
            print(f"Revoked {count} token(s) for {args.email}.")
            return 0
        if args.id is None:
            raise SystemExit("revoke requires --id, or --email with --all")
        print("Revoked." if mcp_tokens.revoke_token(args.id)
              else f"Token #{args.id} was not found or was already revoked.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
