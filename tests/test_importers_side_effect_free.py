"""Importing an importer must do nothing at all.

`app/importers/schwab.py` and `wealthbox.py` used to do their work at module
scope: read `app/.env`, build an engine, reflect the schema, glob
`01 Raw Imports/` (gitignored client data), raise FileNotFoundError when it was
absent, and then run the whole import as a side effect of being imported. That
made `test_app_module_imports_cleanly` impossible to pass in a clean checkout,
and meant importing the module wrote client data to the database.

Discovery now sits behind `find_*`, database setup behind a cached `_database()`,
and execution behind `main()`. These tests pin the whole contract: import is
inert (no filesystem, no database, no discovery, no execution, no raise), the
errors still surface when an import is actually invoked, and the importers still
behave correctly on real fixture files.
"""
import csv
import io
import json
import os
import pathlib
import subprocess
import sys
import zipfile

import pytest
from sqlalchemy import select

from app.db import accounts, engine, source_contacts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULES = ["app.importers.schwab", "app.importers.wealthbox", "app.importers.dave_ramsey"]

# A URL that cannot be connected to: any attempt to reach the database at import
# time fails loudly instead of silently succeeding against the real one.
UNREACHABLE = "postgresql://nobody:nobody@127.0.0.1:1/nonexistent"

# A plausible, resolvable URL — used where the point is that import does not
# connect *even when it could*, rather than that a connection failed.
REACHABLE = "postgresql://localhost/client360"

# Deterministic ids: the importers upsert, so re-runs update one row rather than
# accumulating litter in the shared database.
TEST_ACCOUNT = "RC1-IMPORT-TEST-0001"
TEST_WB_ID = "rc1-import-test-0001"


def _run(script, cwd, database_url=None):
    """Run `script` in a clean subprocess, from `cwd`, with DATABASE_URL controlled.

    A subprocess is required: the in-process module is already cached, so a plain
    `import` here would prove nothing about import-time behaviour.
    """
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture
def cleanup():
    yield
    with engine.begin() as c:
        c.execute(accounts.delete().where(accounts.c.account_number == TEST_ACCOUNT))
        c.execute(source_contacts.delete().where(source_contacts.c.source_record_id.in_([TEST_ACCOUNT, TEST_WB_ID])))


# --- 1. importing does no work ------------------------------------------------

@pytest.mark.parametrize("module_name", MODULES)
def test_import_needs_no_database_configured(module_name, tmp_path):
    """With DATABASE_URL unset and app/.env unreachable, import must still succeed."""
    result = _run(f"import {module_name}", cwd=tmp_path, database_url=None)
    assert result.returncode == 0, f"import failed:\n{result.stderr}"
    assert "RuntimeError" not in result.stderr


@pytest.mark.parametrize("module_name", MODULES)
def test_import_never_connects_to_the_database(module_name, tmp_path):
    """Pointed at an unreachable database, import must not notice."""
    result = _run(f"import {module_name}", cwd=tmp_path, database_url=UNREACHABLE)
    assert result.returncode == 0, f"import connected to the database:\n{result.stderr}"
    assert "could not connect" not in result.stderr.lower()


@pytest.mark.parametrize("module_name", MODULES)
def test_import_opens_no_network_connection(module_name, tmp_path):
    """Audit the socket layer: import must not connect, or even resolve a host.

    Stronger than pointing at an unreachable database — this proves no attempt is
    made at all, rather than that an attempt happened to fail.
    """
    script = (
        "import sys, json\n"
        "events = []\n"
        "def hook(event, args):\n"
        "    if event in ('socket.connect', 'socket.getaddrinfo'):\n"
        "        events.append(event)\n"
        "sys.addaudithook(hook)\n"
        f"import {module_name}\n"
        "print(json.dumps(events))\n"
    )
    # A *reachable* database is configured: if import wanted to connect, it could.
    result = _run(script, cwd=tmp_path, database_url=os.environ.get("DATABASE_URL") or REACHABLE)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


@pytest.mark.parametrize("module_name", MODULES)
def test_import_creates_no_engine_and_reflects_nothing(module_name, tmp_path):
    """The lazy helper must still be cold after import."""
    script = (
        "import sys, json\n"
        f"import {module_name}\n"
        f"mod = sys.modules['{module_name}']\n"
        "print(json.dumps({\n"
        "    'engine_attr': 'engine' in vars(mod),\n"
        "    'metadata_attr': 'metadata' in vars(mod),\n"
        "    'database_cached': mod._database.cache_info().currsize > 0,\n"
        "}))\n"
    )
    result = _run(script, cwd=tmp_path, database_url=UNREACHABLE)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state == {"engine_attr": False, "metadata_attr": False, "database_cached": False}


@pytest.mark.parametrize("module_name", MODULES)
def test_import_succeeds_with_no_dotenv_file(module_name, tmp_path):
    """load_dotenv reads a path relative to cwd; from here app/.env cannot exist."""
    assert not (tmp_path / "app" / ".env").exists()
    result = _run(f"import {module_name}", cwd=tmp_path, database_url=None)
    assert result.returncode == 0, f"import failed without app/.env:\n{result.stderr}"


@pytest.mark.parametrize("module_name", MODULES)
def test_import_reads_no_client_data_and_no_dotenv(module_name, tmp_path):
    """Audit every file open during import; none may touch client data or app/.env.

    Opening .py/.pyc files is Python's own import machinery, so only the module's
    own reads are asserted on.
    """
    script = (
        "import sys, json\n"
        "opened = []\n"
        "def hook(event, args):\n"
        "    if event == 'open':\n"
        "        opened.append(str(args[0]))\n"
        "sys.addaudithook(hook)\n"
        f"import {module_name}\n"
        "bad = [p for p in opened if '01 Raw Imports' in p or p.endswith('.env')]\n"
        "print(json.dumps(bad))\n"
    )
    result = _run(script, cwd=tmp_path, database_url=UNREACHABLE)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


@pytest.mark.parametrize("module_name", MODULES)
def test_import_does_not_discover_client_data(module_name, tmp_path):
    """Even when a client-data folder IS present, import must not glob it."""
    (tmp_path / "01 Raw Imports" / "Schwab").mkdir(parents=True)
    (tmp_path / "01 Raw Imports" / "Wealthbox").mkdir(parents=True)
    script = (
        "import sys\n"
        "seen = []\n"
        "def hook(event, args):\n"
        "    if event in ('os.scandir', 'os.listdir') and args and '01 Raw Imports' in str(args[0]):\n"
        "        seen.append(str(args[0]))\n"
        "sys.addaudithook(hook)\n"
        f"import {module_name}\n"
        "print('SCANNED' if seen else 'NOT_SCANNED')\n"
    )
    result = _run(script, cwd=tmp_path, database_url=UNREACHABLE)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "NOT_SCANNED"


@pytest.mark.parametrize(
    "module_name,marker",
    [
        ("app.importers.schwab", "Schwab import complete"),
        ("app.importers.wealthbox", "Wealthbox contact import complete"),
        ("app.importers.dave_ramsey", "Dave Ramsey import complete"),
    ],
)
def test_importing_does_not_run_the_import(module_name, marker, tmp_path):
    """Importing must not execute the importer — it used to write to the DB."""
    result = _run(f"import {module_name}", cwd=tmp_path, database_url=UNREACHABLE)
    assert result.returncode == 0
    assert marker not in result.stdout
    assert "Opening" not in result.stdout and "Imported" not in result.stdout


# --- 2. errors surface only when the importer is executed ---------------------

def test_schwab_missing_files_raise_only_when_invoked(tmp_path):
    from app.importers import schwab

    with pytest.raises(FileNotFoundError, match="No Schwab AccountsList CSV found"):
        schwab.find_input_files(tmp_path)
    with pytest.raises(FileNotFoundError, match="No Schwab AccountsList CSV found"):
        schwab.main(tmp_path)


def test_schwab_missing_profile_raises_its_own_error(tmp_path):
    from app.importers import schwab

    (tmp_path / "AccountsList_x.csv").write_text("Account Number\n123\n")
    with pytest.raises(FileNotFoundError, match="No Schwab Profile CSV found"):
        schwab.find_input_files(tmp_path)


def test_wealthbox_missing_zip_raises_only_when_invoked(tmp_path):
    from app.importers import wealthbox

    with pytest.raises(FileNotFoundError, match="No Wealthbox contacts ZIP found"):
        wealthbox.find_contact_zips(tmp_path)
    with pytest.raises(FileNotFoundError, match="No Wealthbox contacts ZIP found"):
        wealthbox.main(tmp_path)


def test_missing_database_url_raises_only_when_invoked(tmp_path):
    """The RuntimeError moved from import time to first explicit use."""
    script = (
        "import app.importers.wealthbox as wb\n"
        "print('IMPORTED')\n"
        "try:\n"
        "    wb._database()\n"
        "except RuntimeError as e:\n"
        "    print('RAISED_ON_USE:', e)\n"
    )
    result = _run(script, cwd=tmp_path, database_url=None)
    assert result.returncode == 0, result.stderr
    assert "IMPORTED" in result.stdout
    assert "RAISED_ON_USE: DATABASE_URL is missing from app/.env" in result.stdout


# --- 3. behaviour preserved on fixture files ---------------------------------

def _write_accounts_csv(folder):
    path = folder / "AccountsList_fixture.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Account Number", "Name", "Registration", "Status",
                    "Total Value", "Cash Available", "Performance Start Date", "Closed Date"])
        w.writerow([TEST_ACCOUNT, "Fixture Investor", "Individual", "Open",
                    "$1,234.56", "$100.00", "01/15/2020", "N/A"])
        w.writerow(["", "Skipped — no account number", "", "", "", "", "", ""])
    return path


def _write_profile_csv(folder):
    """Schwab profile files carry three preamble rows before the header."""
    path = folder / "Profile_Firm_fixture.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Schwab Profile Export"])
        w.writerow(["Generated"])
        w.writerow([])
        w.writerow(["Account#", "Primary Account Holder", "Primary Email Address",
                    "Cell Phone", "Taxpayer ID"])
        w.writerow([TEST_ACCOUNT, "Fixture Investor", "Fixture@Example.COM",
                    "(555) 123-4567", "123-45-6789"])
    return path


def test_schwab_accounts_import_still_works_on_fixture(tmp_path, cleanup):
    from app.importers import schwab

    schwab.import_accounts_file(_write_accounts_csv(tmp_path))

    with engine.connect() as c:
        row = c.execute(
            select(accounts).where(accounts.c.account_number == TEST_ACCOUNT)
        ).mappings().one()
    assert row["custodian"] == "Schwab"
    assert row["account_name"] == "Fixture Investor"
    assert row["registration_type"] == "Individual"
    assert str(row["total_value"]) == "1234.56"      # $ and , stripped
    assert str(row["open_date"]) == "2020-01-15"     # m/d/Y parsed
    assert row["closed_date"] is None                # "N/A" -> None


def test_schwab_profile_import_still_excludes_taxpayer_id(tmp_path, cleanup):
    """The Taxpayer ID exclusion is security-relevant — pin it explicitly."""
    from app.importers import schwab

    schwab.import_profile_file(_write_profile_csv(tmp_path))

    with engine.connect() as c:
        row = c.execute(
            select(source_contacts).where(
                source_contacts.c.source_record_id == TEST_ACCOUNT,
                source_contacts.c.source_system == "Schwab Profile",
            )
        ).mappings().one()
    assert row["full_name"] == "Fixture Investor"
    assert row["normalized_email"] == "fixture@example.com"
    assert row["normalized_phone"] == "5551234567"
    assert "Taxpayer ID" not in (row["raw_data"] or {})
    assert "123-45-6789" not in str(row["raw_data"])


def test_schwab_main_runs_both_halves(tmp_path, cleanup):
    """main() still drives accounts + profile together, as the script did."""
    from app.importers import schwab

    _write_accounts_csv(tmp_path)
    _write_profile_csv(tmp_path)
    schwab.main(tmp_path)

    with engine.connect() as c:
        assert c.execute(
            select(accounts).where(accounts.c.account_number == TEST_ACCOUNT)
        ).mappings().one()
        assert c.execute(
            select(source_contacts).where(
                source_contacts.c.source_record_id == TEST_ACCOUNT,
                source_contacts.c.source_system == "Schwab Profile",
            )
        ).mappings().one()


def _write_contacts_zip(folder):
    path = folder / "wealthbox-contacts-fixture.zip"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "first_name", "last_name", "name", "primary_email",
                "primary_phone", "mailing_address_city", "ssn"])
    w.writerow([TEST_WB_ID, "Fixture", "Contact", "Fixture Contact",
                "WB.Fixture@Example.COM", "1 (555) 987-6543", "Nashville", "999-88-7777"])
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("contacts.csv", buf.getvalue())
    return path


def test_wealthbox_import_still_works_and_strips_sensitive_fields(tmp_path, cleanup):
    from app.importers import wealthbox

    _write_contacts_zip(tmp_path)
    result = wealthbox.main(tmp_path)
    assert result == {"rows_read": 1, "rows_inserted": 1}

    with engine.connect() as c:
        row = c.execute(
            select(source_contacts).where(
                source_contacts.c.source_record_id == TEST_WB_ID,
                source_contacts.c.source_system == "Wealthbox",
            )
        ).mappings().one()
    assert row["full_name"] == "Fixture Contact"
    assert row["normalized_email"] == "wb.fixture@example.com"
    assert row["normalized_phone"] == "5559876543"   # leading 1 stripped
    assert row["city"] == "Nashville"
    assert "ssn" not in (row["raw_data"] or {})
    assert "999-88-7777" not in str(row["raw_data"])


def test_wealthbox_import_is_idempotent_on_rerun(tmp_path, cleanup):
    """The upsert keys on (source_system, source_hash) — re-import must not duplicate."""
    from app.importers import wealthbox

    _write_contacts_zip(tmp_path)
    wealthbox.main(tmp_path)
    wealthbox.main(tmp_path)

    with engine.connect() as c:
        rows = c.execute(
            select(source_contacts).where(
                source_contacts.c.source_record_id == TEST_WB_ID,
                source_contacts.c.source_system == "Wealthbox",
            )
        ).mappings().all()
    assert len(rows) == 1


def test_wealthbox_import_records_an_import_job(tmp_path, cleanup):
    """The Wealthbox import now records an import_jobs run (like Schwab), for auditability."""
    from sqlalchemy import text

    from app.importers import wealthbox

    zip_path = _write_contacts_zip(tmp_path)
    wealthbox.main(tmp_path)

    with engine.connect() as c:
        job = c.execute(text(
            "SELECT source_system, status, rows_read, rows_inserted FROM import_jobs "
            "WHERE source_system = 'Wealthbox' AND source_file = :f "
            "ORDER BY id DESC LIMIT 1"), {"f": zip_path.name}).mappings().first()
    assert job is not None
    assert job["source_system"] == "Wealthbox" and job["status"] == "completed"
    assert job["rows_read"] == 1 and job["rows_inserted"] == 1


def test_wealthbox_validation_report_is_content_free_and_consistent(tmp_path, cleanup):
    """validation_report returns counts only (no names/emails/phones) and is internally consistent."""
    from app.importers import wealthbox

    _write_contacts_zip(tmp_path)
    wealthbox.main(tmp_path)

    with engine.connect() as c:
        report = wealthbox.validation_report(c)
    # internal consistency (accumulated DB -> use >= / equalities, never exact totals)
    assert report["wealthbox_source_contacts"] >= 1
    assert report["with_email"] + report["missing_email"] == report["wealthbox_source_contacts"]
    assert report["with_phone"] + report["missing_phone"] == report["wealthbox_source_contacts"]
    assert report["linked_to_person"] + report["unlinked"] == report["wealthbox_source_contacts"]
    # content-free: values are all integers, no strings/PII
    assert all(isinstance(v, int) for v in report.values())
