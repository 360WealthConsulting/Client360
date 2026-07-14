"""Demo smoke tests (DEMO-ONLY).

Runs against the seeded demo database and asserts: the safety guard works, every
role can log in through the REAL session machinery, capabilities differ by role,
and the major role-scoped screens return data. Exits non-zero on any failure.
Kept out of the normal pytest suite (it needs the demo DB + demo data).
"""
import sys

from app.demo.safety import assert_demo_database, DemoSafetyError
from app.demo.credentials import DEMO_PORTAL, DEMO_STAFF
from app.integrations.identity.base import IdentityClaims
from app.security.service import authenticate_claims, create_session, resolve_principal

_failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _login_staff(user):
    claims = IdentityClaims(subject=user.auth_subject, email=user.email,
                            display_name=user.display_name, mfa_authenticated=True)
    user_id = authenticate_claims(claims, require_mfa=True)
    if not user_id:
        return None
    token = create_session(user_id)
    return resolve_principal(token)


def run():
    print("== 1. database safety guard ==")
    name = assert_demo_database()
    check("target is a *_demo database", name.endswith("_demo"), name)
    try:
        assert_demo_database("postgresql://localhost/client360")
        check("refuses non-demo database", False, "did not refuse")
    except DemoSafetyError:
        check("refuses non-demo database", True)

    print("== 2. staff login for every role (real auth path) ==")
    principals = {}
    for user in DEMO_STAFF:
        principal = _login_staff(user)
        check(f"login: {user.persona} ({user.username})", principal is not None)
        if principal:
            principals[user.role_code] = principal
            check(f"  {user.persona} has capabilities", len(principal.capabilities) > 0)

    print("== 3. role-based visibility (capabilities differ) ==")
    admin = principals.get("administrator")
    advisor = principals.get("advisor")
    taxprep = principals.get("tax_preparer")
    if admin and advisor:
        check("administrator has more capabilities than advisor",
              len(admin.capabilities) > len(advisor.capabilities),
              f"admin={len(admin.capabilities)} advisor={len(advisor.capabilities)}")
    if taxprep:
        check("tax preparer can write tax", taxprep.can("tax.write"))
    if advisor:
        check("advisor cannot write tax (least privilege)", not advisor.can("tax.write"))

    print("== 4. major role-scoped screens return data ==")
    try:
        from app.services.work_management import dashboard as work_dashboard
        data = work_dashboard(admin)
        check("work dashboard renders for administrator", isinstance(data, dict) and "items" in data)
    except Exception as exc:
        check("work dashboard renders for administrator", False, str(exc))
    try:
        from app.services.tax_intake import staff_dashboard
        tax = staff_dashboard(taxprep or admin)
        check("tax intake dashboard renders", isinstance(tax, dict) and "items" in tax,
              f"returns={len(tax.get('items', []))}")
    except Exception as exc:
        check("tax intake dashboard renders", False, str(exc))
    try:
        from app.services.dashboard import get_dashboard_data
        check("advisor dashboard data renders", isinstance(get_dashboard_data(), dict))
    except Exception as exc:
        check("advisor dashboard data renders", False, str(exc))

    print("== 5. portal login + dashboard ==")
    try:
        from sqlalchemy import select
        from app.db import engine, portal_accounts
        from app.portal.service import create_portal_session, resolve_portal_session, dashboard as portal_dashboard
        with engine.connect() as c:
            account_id = c.scalar(select(portal_accounts.c.id).where(
                portal_accounts.c.email == DEMO_PORTAL.email, portal_accounts.c.status == "active"))
        check("portal demo account is active", account_id is not None)
        if account_id:
            token = create_portal_session(account_id, device_fingerprint="demo-smoke", device_name="Smoke")
            principal = resolve_portal_session(token)
            check("portal login resolves a principal", principal is not None)
            data = portal_dashboard(principal)
            check("portal dashboard renders", isinstance(data, dict) and "documents" in data)
    except Exception as exc:
        check("portal login + dashboard", False, str(exc))

    print()
    if _failures:
        print(f"SMOKE FAILED: {len(_failures)} check(s) failed: {_failures}")
        return 1
    print("SMOKE PASSED: all demo checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
