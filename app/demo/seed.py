"""Fictional demo data seeder (DEMO-ONLY).

Guards on a `*_demo` database, then seeds realistic **fictional** data across
every major Client360 domain by reusing the real service layer (so all rows are
valid and side effects fire). Intended to run against a freshly migrated,
otherwise-empty demo database (the reset command drops/recreates first), which
makes reseeding idempotent.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select

from app.db import (
    accounts, account_beneficiaries, account_holdings, activities, capabilities,
    documents, engine, households, microsoft_accounts, microsoft_documents,
    microsoft_drives, microsoft_unmatched_calendar_attendees,
    microsoft_unmatched_messages, people, role_capabilities,
    roles, securities, tasks, users, user_roles,
)
from app.demo.credentials import DEMO_PORTAL, DEMO_STAFF, TAX_PREPARER_ROLE
from app.demo.safety import assert_demo_database
from app.portal.service import (
    accept_invitation, create_document_request, create_thread, invite_portal_account,
    notify, staff_send_message, PortalPrincipal,
)
from app.services.relationships import create_relationship
from app.services.tax_domain import create_engagement
from app.services.timeline import add_timeline_event
from app.services.work_management import assign_work
from app.services.workflow_automation import launch_workflow

NOW = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)


def _rid(tag):
    return f"demo-{tag}"


def _role_id(connection, code):
    return connection.scalar(select(roles.c.id).where(roles.c.code == code))


# --- reference: demo-only tax_preparer role --------------------------------

def _seed_tax_preparer_role(connection):
    existing = _role_id(connection, TAX_PREPARER_ROLE["code"])
    if existing:
        return existing
    role_id = connection.execute(
        roles.insert().values(code=TAX_PREPARER_ROLE["code"], name=TAX_PREPARER_ROLE["name"], active=True)
        .returning(roles.c.id)
    ).scalar_one()
    cap_ids = connection.execute(
        select(capabilities.c.id).where(capabilities.c.code.in_(TAX_PREPARER_ROLE["capabilities"]))
    ).scalars().all()
    for cap_id in cap_ids:
        connection.execute(role_capabilities.insert().values(role_id=role_id, capability_id=cap_id))
    return role_id


# --- staff users ------------------------------------------------------------

def _seed_staff(connection):
    ids = {}
    for user in DEMO_STAFF:
        if user.role_code == TAX_PREPARER_ROLE["code"]:
            role_id = _seed_tax_preparer_role(connection)
        else:
            role_id = _role_id(connection, user.role_code)
        uid = connection.execute(
            users.insert().values(
                email=user.email, normalized_email=user.email.lower(),
                display_name=user.display_name, auth_subject=user.auth_subject, status="active",
            ).returning(users.c.id)
        ).scalar_one()
        connection.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
        ids[user.role_code] = uid
    return ids


# --- households / people / prospects / entities / relationships -------------

HOUSEHOLDS = [
    ("Hawthorne Family", [("Taylor Hawthorne", "taylor.hawthorne@example-demo.example"),
                          ("Jamie Hawthorne", "jamie.hawthorne@example-demo.example")]),
    ("Okoro Family", [("Chidi Okoro", "chidi.okoro@example-demo.example"),
                      ("Ada Okoro", "ada.okoro@example-demo.example")]),
    ("Delgado Family", [("Marisol Delgado", "marisol.delgado@example-demo.example")]),
    ("Kensington Trust", [("Edmund Kensington", "edmund.kensington@example-demo.example")]),
]
PROSPECTS = [
    ("Priya Nair", "priya.nair@example-demo.example"),
    ("Wesley Booker", "wesley.booker@example-demo.example"),
]


def _seed_people(connection):
    hh = {}
    for name, members in HOUSEHOLDS:
        hid = connection.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
        person_ids = []
        for full_name, email in members:
            pid = connection.execute(
                people.insert().values(household_id=hid, full_name=full_name, primary_email=email,
                                       normalized_email=email.lower(), active=True)
                .returning(people.c.id)
            ).scalar_one()
            person_ids.append((pid, full_name, email))
        hh[name] = {"household_id": hid, "people": person_ids}
    prospects = []
    for full_name, email in PROSPECTS:
        pid = connection.execute(
            people.insert().values(full_name=full_name, primary_email=email, normalized_email=email.lower(),
                                   contact_type="prospect", active=True).returning(people.c.id)
        ).scalar_one()
        prospects.append((pid, full_name))
    return hh, prospects


def _seed_relationships(hh, staff, prospects):
    # spouse links, a business, and a trust relationship — fictional.
    haw = hh["Hawthorne Family"]["people"]
    create_relationship(person_id=haw[0][0], relationship_code="spouse", target_person_id=haw[1][0],
                        source="demo", created_by=staff["advisor"])
    create_relationship(person_id=haw[0][0], relationship_code="owner",
                        target_entity_type="business", target_name="Hawthorne Design Studio LLC (DEMO)",
                        source="demo", created_by=staff["advisor"])
    oko = hh["Okoro Family"]["people"]
    create_relationship(person_id=oko[0][0], relationship_code="spouse", target_person_id=oko[1][0],
                        source="demo", created_by=staff["advisor"])
    ken = hh["Kensington Trust"]["people"]
    create_relationship(person_id=ken[0][0], relationship_code="trustee",
                        target_entity_type="trust", target_name="Kensington Family Trust (DEMO)",
                        source="demo", created_by=staff["advisor"])


# --- portfolio --------------------------------------------------------------

SECURITIES = [("VTSAX", "Vanguard Total Stock Market", "Equity"),
              ("VBTLX", "Vanguard Total Bond Market", "Bond"),
              ("AAPL", "Apple Inc.", "Equity"),
              ("VMFXX", "Vanguard Federal Money Market", "Cash")]


def _seed_portfolio(connection, hh):
    sec_ids = {}
    for symbol, name, asset_class in SECURITIES:
        sid = connection.execute(
            insert(securities).values(name=name, symbol=symbol, asset_class=asset_class).returning(securities.c.id)
        ).scalar_one()
        sec_ids[symbol] = sid
    n = 0
    for hname, holdings_plan in (("Hawthorne Family", [("VTSAX", 480000, "Individual"), ("VBTLX", 120000, "Individual")]),
                                 ("Okoro Family", [("AAPL", 210000, "Roth IRA"), ("VMFXX", 40000, "Roth IRA")]),
                                 ("Delgado Family", [("VTSAX", 95000, "Traditional IRA")])):
        info = hh[hname]; pid = info["people"][0][0]; hid = info["household_id"]
        acct = connection.execute(
            accounts.insert().values(custodian="Schwab", person_id=pid, household_id=hid,
                                     account_number=f"DEMO-{1000+n}", registration_type=holdings_plan[0][2],
                                     total_value=sum(v for _, v, _ in holdings_plan),
                                     cash_value=next((v for s, v, _ in holdings_plan if s == "VMFXX"), 0))
            .returning(accounts.c.id)
        ).scalar_one()
        for symbol, value, _reg in holdings_plan:
            connection.execute(account_holdings.insert().values(
                account_id=acct, security_id=sec_ids[symbol], as_of_date=NOW.date(), market_value=value))
        connection.execute(account_beneficiaries.insert().values(
            account_id=acct, beneficiary_name="Estate of Account Holder (DEMO)", beneficiary_type="primary", active=True))
        n += 1
    return n


# --- documents (direct insert; no file IO) ----------------------------------

def _seed_documents(connection, hh):
    count = 0
    plan = [("Hawthorne Family", "2025 W-2 - Taylor.pdf", "tax_document", "ready_for_review"),
            ("Hawthorne Family", "Brokerage Statement Q4.pdf", "statement", "not_required"),
            ("Okoro Family", "1099-DIV 2025.pdf", "tax_document", "pending"),
            ("Delgado Family", "Signed Advisory Agreement.pdf", "agreement", "not_required")]
    for hname, original, category, review in plan:
        pid = hh[hname]["people"][0][0]
        blob = f"DEMO DOCUMENT {original}".encode()
        connection.execute(documents.insert().values(
            person_id=pid, original_name=original, stored_name=f"demo-{count}-{original}",
            storage_path=f"demo/{pid}/{original}", size_bytes=len(blob),
            sha256=hashlib.sha256(blob).hexdigest(), category=category, review_status=review,
            review_due_at=NOW + timedelta(days=3) if review != "not_required" else None))
        count += 1
    return count


# --- activities & timeline (incl. Microsoft-sourced) ------------------------

def _seed_activities_timeline(hh):
    count = 0
    for hname, note in (("Hawthorne Family", "Annual review meeting completed"),
                        ("Okoro Family", "Discussed Roth conversion strategy"),
                        ("Delgado Family", "Onboarding call scheduled")):
        pid = hh[hname]["people"][0][0]
        with engine.begin() as c:
            c.execute(activities.insert().values(person_id=pid, activity_type="note", title=note,
                                                 occurred_at=NOW - timedelta(days=count)))
        add_timeline_event(source="advisor", event_type="note", title=note, person_id=pid,
                           external_id=f"demo-activity-{pid}-{count}", event_time=NOW - timedelta(days=count))
        count += 1
    # Microsoft-sourced timeline examples
    haw = hh["Hawthorne Family"]["people"][0][0]
    add_timeline_event(source="microsoft", event_type="email", title="Re: Q3 portfolio rebalancing",
                       person_id=haw, summary="Client confirmed the proposed allocation changes.",
                       external_id="demo-ms-mail-1", event_time=NOW - timedelta(days=2),
                       event_metadata={"from": "taylor.hawthorne@example-demo.example"})
    add_timeline_event(source="microsoft", event_type="calendar_event", title="Annual Review — Hawthorne",
                       person_id=haw, summary="60-minute review meeting.", external_id="demo-ms-cal-1",
                       event_time=NOW + timedelta(days=5), event_metadata={"location": "Video call"})
    return count + 2


# --- Microsoft integration examples -----------------------------------------

def _seed_microsoft(connection, hh):
    connection.execute(microsoft_accounts.insert().values(
        tenant_id="demo-tenant", user_id="demo-advisor", email="advisor@northwind-demo.example",
        last_sync_status="ok", last_sync_at=NOW - timedelta(hours=1)))
    connection.execute(microsoft_drives.insert().values(
        microsoft_drive_id="demo-drive-1", source_type="user"))
    haw = hh["Hawthorne Family"]["people"][0][0]
    connection.execute(microsoft_documents.insert().values(
        microsoft_drive_id="demo-drive-1", microsoft_item_id="demo-item-1", name="Engagement Letter 2025.pdf",
        person_id=haw, size_bytes=48211, status="matched", raw_metadata={"folder": "Clients/Hawthorne"}))
    connection.execute(microsoft_unmatched_messages.insert().values(
        microsoft_message_id="demo-msg-1", sender_address="unknown.sender@example-demo.example",
        subject="Question about my account", status="pending"))
    connection.execute(microsoft_unmatched_calendar_attendees.insert().values(
        microsoft_event_id="demo-evt-1", attendee_email="prospect@example-demo.example",
        starts_at=NOW + timedelta(days=1), subject="Intro call", status="pending",
        event_metadata={"organizer": "advisor@northwind-demo.example"}))
    return 5


# --- work management: tasks + assignments -----------------------------------

def _seed_work(connection, hh, staff):
    task_ids = []
    plan = [("Hawthorne Family", "Prepare annual review deck", "high", "advisor"),
            ("Okoro Family", "Collect missing 1099", "urgent", "operations"),
            ("Delgado Family", "Complete onboarding paperwork", "normal", "operations")]
    for hname, title, priority, role in plan:
        info = hh[hname]; pid = info["people"][0][0]
        tid = connection.execute(tasks.insert().values(
            person_id=pid, household_id=info["household_id"], title=title, priority=priority,
            status="open", estimated_minutes=45, due_date=(NOW + timedelta(days=3)).date()).returning(tasks.c.id)
        ).scalar_one()
        task_ids.append((tid, role))
    return task_ids


def _assign_work(hh, staff, task_ids):
    n = 0
    for tid, role in task_ids:
        assign_work(entity_type="task", entity_id=tid, assignment_role="primary",
                    actor_user_id=staff["advisor"], user_id=staff.get(role, staff["advisor"]),
                    reason="Demo assignment", request_id=_rid(f"assign-task-{tid}"))
        n += 1
    # assign a client relationship to the advisor
    haw_pid = hh["Hawthorne Family"]["people"][0][0]
    assign_work(entity_type="person", entity_id=haw_pid, assignment_role="primary",
                actor_user_id=staff["advisor"], user_id=staff["advisor"], reason="Primary advisor",
                request_id=_rid(f"assign-person-{haw_pid}"))
    return n + 1


# --- workflows --------------------------------------------------------------

def _seed_workflows(hh, staff):
    n = 0
    for hname, template in (("Delgado Family", "client_onboarding"),
                           ("Hawthorne Family", "annual_review")):
        info = hh[hname]
        launch_workflow(template, actor_user_id=staff["advisor"], person_id=info["people"][0][0],
                        household_id=info["household_id"], priority="normal",
                        idempotency_key=_rid(f"wf-{template}-{info['household_id']}"),
                        request_id=_rid(f"wf-{template}"))
        n += 1
    return n


# --- tax engagements / returns / intake -------------------------------------

def _seed_tax(hh, staff):
    engagements = []
    for hname, return_type, filing in (("Hawthorne Family", "1040", "mfj"),
                                       ("Okoro Family", "1040", "mfj"),
                                       ("Delgado Family", "1040", "single")):
        info = hh[hname]
        result = create_engagement(
            {"tax_year": 2025, "return_type": return_type, "filing_status": filing,
             "person_id": info["people"][0][0], "household_id": info["household_id"],
             "assignee_user_id": staff["tax_preparer"]},
            actor_user_id=staff["administrator"], request_id=_rid(f"engagement-{info['household_id']}"))
        engagements.append(result)
    return engagements


# --- portal -----------------------------------------------------------------

def _seed_portal(hh, staff):
    haw = hh["Hawthorne Family"]
    pid = None
    for p, name, email in haw["people"]:
        if email == DEMO_PORTAL.email:
            pid = p
            break
    if pid is None:
        pid = haw["people"][0][0]
    account_id, token = invite_portal_account(
        person_id=pid, household_id=haw["household_id"], email=DEMO_PORTAL.email,
        display_name=DEMO_PORTAL.display_name, access_type="self", invited_by_user_id=staff["advisor"],
        permissions={"tasks": True, "documents": True, "messages": True})
    accept_invitation(token, auth_subject="demo|portal", mfa_verified=True)
    principal = PortalPrincipal(account_id, pid, DEMO_PORTAL.email, DEMO_PORTAL.display_name)
    create_document_request(person_id=pid, household_id=haw["household_id"],
                            title="Upload your 2025 W-2", requested_by_user_id=staff["advisor"],
                            description="Needed to begin your tax return.")
    thread_id = create_thread(principal, household_id=haw["household_id"], person_id=pid,
                              subject="Welcome to your client portal",
                              body="Hi! This is a demo secure message thread.")
    staff_send_message(thread_id=thread_id, user_id=staff["advisor"],
                       body="Thanks for reaching out — happy to help with anything.")
    notify(account_id, "document_request", "New document request", body="Please upload your 2025 W-2.",
           idempotency_key=_rid(f"portal-notify-{account_id}"))
    return {"account_id": account_id, "thread_id": thread_id}


# --- orchestration ----------------------------------------------------------

def seed_all():
    assert_demo_database()
    summary = {}
    with engine.begin() as connection:
        staff = _seed_staff(connection)
        hh, prospects = _seed_people(connection)
        summary["securities_accounts"] = _seed_portfolio(connection, hh)
        summary["documents"] = _seed_documents(connection, hh)
        summary["microsoft"] = _seed_microsoft(connection, hh)
        task_ids = _seed_work(connection, hh, staff)
    summary["staff_users"] = len(staff)
    summary["households"] = len(hh)
    summary["prospects"] = len(prospects)
    _seed_relationships(hh, staff, prospects)
    summary["activities_timeline"] = _seed_activities_timeline(hh)
    summary["work_assignments"] = _assign_work(hh, staff, task_ids)
    summary["workflows"] = _seed_workflows(hh, staff)
    summary["tax_engagements"] = len(_seed_tax(hh, staff))
    summary["portal"] = _seed_portal(hh, staff)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(seed_all(), indent=2, default=str))
