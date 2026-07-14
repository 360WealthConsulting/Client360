"""Fictional demo credentials.

DEMO-ONLY. These are not real accounts and carry no real secrets. The demo login
route verifies the password below and then issues a session through the REAL
`authenticate_claims` + `create_session` path (no auth bypass). Staff personas map
to seeded users/roles; the portal persona maps to a seeded portal account.

The `tax_preparer` role is seeded by the demo seeder (the base RBAC gives tax
capabilities only to `administrator`, so a distinct preparer persona needs its own
role in the demo database).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoStaffUser:
    persona: str            # human label, e.g. "Advisor"
    role_code: str          # roles.code the user is granted
    username: str           # demo login username
    password: str           # demo login password (fictional)
    email: str
    display_name: str
    auth_subject: str       # users.auth_subject (matches demo IdentityClaims.subject)


@dataclass(frozen=True)
class DemoPortalUser:
    persona: str
    username: str
    password: str
    email: str
    display_name: str


# Fictional firm: "Northwind Wealth & Tax (DEMO)".
DEMO_STAFF = [
    DemoStaffUser("Administrator", "administrator", "admin", "demo-admin-pass",
                  "avery.stone@northwind-demo.example", "Avery Stone", "demo|administrator"),
    DemoStaffUser("Advisor", "advisor", "advisor", "demo-advisor-pass",
                  "morgan.reed@northwind-demo.example", "Morgan Reed", "demo|advisor"),
    DemoStaffUser("Operations", "operations", "operations", "demo-operations-pass",
                  "riley.chen@northwind-demo.example", "Riley Chen", "demo|operations"),
    DemoStaffUser("Tax Preparer", "tax_preparer", "taxprep", "demo-taxprep-pass",
                  "jordan.pace@northwind-demo.example", "Jordan Pace", "demo|tax_preparer"),
    DemoStaffUser("Compliance", "compliance", "compliance", "demo-compliance-pass",
                  "sasha.vale@northwind-demo.example", "Sasha Vale", "demo|compliance"),
]

# The portal persona is a client of the fictional "Hawthorne Family" household.
DEMO_PORTAL = DemoPortalUser(
    "Client Portal User", "client", "demo-client-pass",
    "taylor.hawthorne@example-demo.example", "Taylor Hawthorne",
)

# Demo-only role granted tax capabilities so the Tax Preparer persona is useful.
TAX_PREPARER_ROLE = {
    "code": "tax_preparer",
    "name": "Tax Preparer (Demo)",
    "capabilities": [
        "tax.read", "tax.write", "tax.intake.read", "tax.intake.write",
        "tax.document.review", "tax.review",
        "client.read", "work.read",
    ],
}


def staff_by_username(username: str):
    for user in DEMO_STAFF:
        if user.username == username:
            return user
    return None
