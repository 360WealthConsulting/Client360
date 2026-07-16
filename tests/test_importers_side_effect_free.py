"""Importers must be importable without client data present.

`app/importers/schwab.py` and `wealthbox.py` used to do their work at module
scope: they globbed `01 Raw Imports/` (gitignored client data), raised
FileNotFoundError when it was absent, and then ran the whole import as a side
effect of being imported. That made `test_app_module_imports_cleanly` impossible
to pass in a clean checkout, and meant importing the module wrote client data to
the database.

File discovery and execution now sit behind `find_*`/`main()`. These tests pin
that: import is inert, the error still surfaces when an import is actually
invoked, and the importers still behave correctly on real fixture files.
"""
import csv
import io
import os
import pathlib
import subprocess
import sys
import zipfile

import pytest
from sqlalchemy import select

from app.db import DATABASE_URL, accounts, engine, source_contacts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Deterministic ids: the importers upsert, so re-runs update one row rather than
# accumulating litter in the shared database.
TEST_ACCOUNT = "RC1-IMPORT-TEST-0001"
TEST_WB_ID = "rc1-import-test-0001"


@pytest.fixture
def cleanup():
    yield
    with engine.begin() as c:
        c.execute(accounts.delete().where(accounts.c.account_number == TEST_ACCOUNT))
        c.execute(source_contacts.delete().where(source_contacts.c.source_record_id.in_([TEST_ACCOUNT, TEST_WB_ID])))


# --- 1. importing is inert, even with no client-data folder -------------------

@pytest.mark.parametrize("module_name", ["app.importers.schwab", "app.importers.wealthbox"])
def test_module_imports_cleanly_without_raw_imports_folder(module_name, tmp_path):
    """Import from a cwd that has no `01 Raw Imports/` at all.

    Runs in a subprocess so the import genuinely re-executes (the in-process
    module is already cached) and from tmp_path so the client-data folder cannot
    be found even by accident.
    """
    assert not (tmp_path / "01 Raw Imports").exists()
    env = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,   # app/.env is unreachable from tmp_path
        "PYTHONPATH": str(REPO_ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"import failed:\n{result.stderr}"
    assert "FileNotFoundError" not in result.stderr


@pytest.mark.parametrize(
    "module_name,marker",
    [
        ("app.importers.schwab", "Schwab import complete"),
        ("app.importers.wealthbox", "Wealthbox contact import complete"),
    ],
)
def test_importing_does_not_run_the_import(module_name, marker, tmp_path):
    """Importing must not execute the importer — it used to write to the DB."""
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0
    assert marker not in result.stdout
    assert "Opening" not in result.stdout and "Imported" not in result.stdout


# --- 2. the error surfaces only when an import is explicitly invoked ----------

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
        n = c.execute(
            select(source_contacts).where(
                source_contacts.c.source_record_id == TEST_WB_ID,
                source_contacts.c.source_system == "Wealthbox",
            )
        ).mappings().all()
    assert len(n) == 1
