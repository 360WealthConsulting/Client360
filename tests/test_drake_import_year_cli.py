"""Year selection for the Drake import driver, and the target-safety it must never lose.

The driver could only ever import every year it discovered. Re-importing 2021 and 2022 after the
1120S short-row fix therefore meant re-importing 2023-2025 as a side effect — a wider blast radius
than the operation called for, with no supported way to avoid it.

Two halves are tested here. The first is selection: an explicitly requested year is fully resolved
BEFORE anything is imported, one bad year stops the whole invocation, and selection can only ever
narrow — never silently widen back to discovery.

The second half is target safety, and it exists because of a real incident. The driver calls
``load_dotenv(r"C:\\Client360\\app\\.env")`` at module scope, and ``load_dotenv`` fills in variables
the shell has NOT set. Unsetting ``MICROSOFT_TOKEN_KEY`` (or ``DATABASE_URL``) at the shell to make an
invocation "safe" therefore does the opposite: the production env file supplies both, and the command
connects to production. Every test here injects its dependencies explicitly rather than relying on a
variable being absent, and the module refuses to run at all against a non-disposable database.
"""
import csv
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.importers import drake_client_csv
from app.importers.drake_client_csv import CLIENT_EXPORT_HEADER
from app.safety import SuiteSafetyError, assert_test_database, database_name, is_test_database
from scripts import import_drake_all_years as driver

HEADER = CLIENT_EXPORT_HEADER
_I = {name: index for index, name in enumerate(HEADER) if name}


# --- target safety: refuse to run at all if this session could reach production -------------------

@pytest.fixture(autouse=True)
def _never_production():
    """Fail closed before every test in this module.

    Not a belt-and-braces duplicate of the conftest gate: that one runs once at collection, and this
    module deliberately manipulates the environment, so the guard is re-asserted per test.
    """
    from app.db import engine

    assert is_test_database(os.environ.get("DATABASE_URL", "")), \
        "DATABASE_URL does not name a disposable database"
    assert engine.url.database != "client360", "the live engine is bound to PRODUCTION"
    assert str(engine.url.database or "").endswith(("_test", "_ci", "_restore_rehearsal")), \
        f"refusing to run against {engine.url.database!r}"


def test_removing_a_shell_variable_cannot_fall_through_to_a_production_env_file(monkeypatch, tmp_path):
    """The incident, reproduced exactly — and caught.

    ``env -u MICROSOFT_TOKEN_KEY python -m scripts.import_drake_all_years --year 2021`` was expected to
    fail for want of a token. Instead ``load_dotenv`` supplied the token AND ``DATABASE_URL`` from the
    production env file, and the command imported into production. This asserts both halves: the
    fall-through really happens, and the safety guard refuses the result.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://user:secret@localhost:5432/client360\n"
        "MICROSOFT_TOKEN_KEY=not-a-real-key\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)

    # Exactly what the driver does at module scope.
    load_dotenv(env_file)

    assert database_name(os.environ["DATABASE_URL"]) == "client360", \
        "unsetting the variable did NOT prevent the production value being loaded"
    assert os.environ["MICROSOFT_TOKEN_KEY"] == "not-a-real-key"

    with pytest.raises(SuiteSafetyError):
        assert_test_database()


def test_the_suite_guard_rejects_production_and_accepts_a_disposable_database():
    with pytest.raises(SuiteSafetyError):
        assert_test_database("postgresql://u:p@localhost:5432/client360")

    assert assert_test_database("postgresql://u:p@localhost:5432/client360_test") == "client360_test"


def test_cli_help_and_year_validation_need_no_database(monkeypatch, tmp_path):
    """``--help`` and argument handling must not open a connection.

    ``app.db`` reflects the whole schema at import, so importing it connects; the driver defers that
    import into :func:`run` precisely so this holds.
    """
    monkeypatch.setattr(driver, "ROOT", tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        driver.build_parser().parse_args(["--help"])
    assert exit_info.value.code == 0

    assert driver.build_parser().parse_args([]).years is None
    assert driver.build_parser().parse_args(["--year", "2021"]).years == [2021]


# --- fixtures: real export shapes, no live data ---------------------------------------------------

def _row(values: dict[str, str]) -> list[str]:
    row = [""] * len(HEADER)
    for name, value in values.items():
        row[_I[name]] = value
    return row


def _wellformed(social: str, name: str, form: str = "1040") -> list[str]:
    return _row({"TP_Social": social, "TP_FirstName": name, "TP_LastName": "EXAMPLE",
                 "FS": "1", "AGI": " 1000 ", "Prep_Fee": " 100 ", "Paid": " 0 ", "Type": form})


def _short_1120s(social: str, name: str) -> list[str]:
    """A 1120S row exactly as the 2021/2022 exports emit it: one field fewer."""
    row = _row({"TP_Social": social, "TP_FirstName": name,
                "AGI": " 6294 ", "Prep_Fee": " 650 ", "Wh_Ral": " 0 ", "Paid": " 0 ",
                "Type": "1120S"})
    drop = _I["Misc4"]
    assert row[drop] == ""
    return row[:drop] + row[drop + 1:]


def _export(folder: Path, rows: list[list[str]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "CLIENT.CSV"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row)
    return path


@pytest.fixture
def drake_root(tmp_path, monkeypatch):
    """A stand-in Drake tree: 2021 (52 short 1120S), 2022 (57 short), 2023-2025 well-formed."""
    root = tmp_path / "Drake"

    _export(root / "2021", [_wellformed(f"1000{i:04d}", f"P{i}") for i in range(10)]
            + [_short_1120s(f"2000{i:04d}", f"BIZ{i}") for i in range(52)])
    _export(root / "2022", [_wellformed(f"3000{i:04d}", f"P{i}") for i in range(10)]
            + [_short_1120s(f"4000{i:04d}", f"BIZ{i}") for i in range(57)])
    for year, form in ((2023, "1120S"), (2024, "1065"), (2025, "1040")):
        _export(root / str(year), [_wellformed(f"5{year}{i:04d}", f"P{i}", form) for i in range(8)])

    monkeypatch.setattr(driver, "ROOT", root)
    return root


@pytest.fixture
def captured(monkeypatch):
    """Replace the upsert with a recorder. Nothing reaches any database."""
    calls = []

    def fake_upsert(connection, rows, *, summary=None):
        calls.append({"years": sorted({row["tax_year"] for row in rows}), "rows": len(rows)})
        return {"rows_read": len(rows), "identified": len(rows), "inserted": 0,
                "updated": len(rows), "quarantined": 0, "quarantined_rows": []}

    monkeypatch.setattr(driver, "upsert_return_rows", fake_upsert)
    monkeypatch.setattr(driver, "identifier_hash", lambda value: f"h:{value}" if value else None)
    return calls


def _imported_years(calls) -> list[int]:
    return sorted(year for call in calls for year in call["years"])


# --- selection --------------------------------------------------------------------------------

def test_no_arguments_still_discovers_and_imports_every_year(drake_root, captured):
    folders = driver.discover_years(drake_root)
    assert [f.name for f in folders] == ["2021", "2022", "2023", "2024", "2025"]

    driver.run(folders, connection=object())
    assert _imported_years(captured) == [2021, 2022, 2023, 2024, 2025]


def test_year_2021_selects_only_2021(drake_root, captured):
    driver.run(driver.resolve_years([2021], drake_root), connection=object())
    assert _imported_years(captured) == [2021]


def test_year_2022_selects_only_2022(drake_root, captured):
    driver.run(driver.resolve_years([2022], drake_root), connection=object())
    assert _imported_years(captured) == [2022]


def test_repeatable_year_selects_exactly_the_requested_years(drake_root, captured):
    driver.run(driver.resolve_years([2021, 2022], drake_root), connection=object())
    assert _imported_years(captured) == [2021, 2022]


def test_repeated_and_unordered_years_are_deduplicated_and_sorted(drake_root):
    folders = driver.resolve_years([2022, 2021, 2021], drake_root)
    assert [f.name for f in folders] == ["2021", "2022"]


def test_explicit_years_never_broaden_to_the_discovered_set(drake_root, captured):
    """The whole point: 2023-2025 exist and must NOT be touched."""
    driver.run(driver.resolve_years([2021], drake_root), connection=object())
    years = _imported_years(captured)
    assert years == [2021]
    assert 2023 not in years and 2024 not in years and 2025 not in years


# --- fail closed, before any import ------------------------------------------------------------

@pytest.mark.parametrize("year", [5, 1899, 3000, 20211])
def test_an_implausible_year_is_refused(drake_root, year):
    with pytest.raises(ValueError, match="not a plausible tax year"):
        driver.resolve_years([year], drake_root)


def test_a_missing_year_directory_is_refused(drake_root):
    with pytest.raises(ValueError, match="no Drake directory for 1999"):
        driver.resolve_years([1999], drake_root)


def test_a_directory_with_no_resolvable_export_is_refused(drake_root):
    (drake_root / "2020").mkdir()
    (drake_root / "2020" / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no client export could be resolved for 2020"):
        driver.resolve_years([2020], drake_root)


def test_one_bad_year_among_several_stops_the_whole_invocation(drake_root, captured):
    with pytest.raises(ValueError):
        driver.resolve_years([2021, 1999, 2022], drake_root)

    assert captured == [], "nothing may be imported when any requested year cannot be resolved"


def test_main_exits_nonzero_and_imports_nothing_for_a_bad_year(drake_root, captured, monkeypatch):
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "test-key")
    monkeypatch.setattr(driver, "run", lambda *a, **k: pytest.fail("run() must not be reached"))

    assert driver.main(["--year", "2021", "--year", "1999"]) == 2
    assert captured == []


def test_main_exits_nonzero_without_the_hashing_secret(drake_root, captured, monkeypatch):
    """The secret is checked before any year is resolved and before any connection is opened."""
    monkeypatch.setattr(driver, "_hash_key",
                        lambda: (_ for _ in ()).throw(RuntimeError("MICROSOFT_TOKEN_KEY is required.")))
    monkeypatch.setattr(driver, "run", lambda *a, **k: pytest.fail("run() must not be reached"))

    assert driver.main(["--year", "2021"]) == 2
    assert captured == []


# --- the canonical implementation is the one being used ------------------------------------------

def test_the_driver_uses_the_canonical_parser_and_upsert():
    """A second importer would defeat the point; assert these are the library's own functions."""
    from app.importers.drake_returns import upsert_return_rows

    assert driver.read_client_rows is drake_client_csv.read_client_rows
    assert driver.upsert_return_rows is upsert_return_rows


def test_import_year_delegates_to_read_client_rows(drake_root, monkeypatch):
    seen = {}

    def spy(tax_year, client_file, **kwargs):
        seen.update({"tax_year": tax_year, "file": client_file, "kwargs": sorted(kwargs)})
        return [], []

    monkeypatch.setattr(driver, "read_client_rows", spy)
    monkeypatch.setattr(driver, "upsert_return_rows",
                        lambda c, rows, **k: {"rows_read": 0, "identified": 0, "inserted": 0,
                                              "updated": 0, "quarantined": 0, "quarantined_rows": []})

    driver.import_year(object(), 2021, drake_root / "2021" / "CLIENT.CSV")
    assert seen["tax_year"] == 2021
    assert seen["file"].name == "CLIENT.CSV"
    assert "identifier_hash" in seen["kwargs"] and "counters" in seen["kwargs"]


# --- the short-row cohorts survive year scoping --------------------------------------------------

def test_2021_reports_52_normalized_and_no_unrecognised(drake_root, captured):
    results = driver.run(driver.resolve_years([2021], drake_root), connection=object())
    shapes = results[0]["shapes"]

    assert shapes["normalized"] == 52
    assert shapes["unrecognised_short"] == 0
    assert results[0]["anomalies"] == []
    assert results[0]["summary"]["inserted"] == 0


def test_2022_reports_57_normalized_and_no_unrecognised(drake_root, captured):
    results = driver.run(driver.resolve_years([2022], drake_root), connection=object())
    shapes = results[0]["shapes"]

    assert shapes["normalized"] == 57
    assert shapes["unrecognised_short"] == 0
    assert results[0]["anomalies"] == []
    assert results[0]["summary"]["inserted"] == 0


def test_well_formed_years_report_no_normalization(drake_root, captured):
    for year in (2023, 2024, 2025):
        results = driver.run(driver.resolve_years([year], drake_root), connection=object())
        shapes = results[0]["shapes"]
        assert shapes["normalized"] == 0, f"{year} must need no repair"
        assert shapes["unrecognised_short"] == 0
        assert shapes["unchanged"] == 8


def test_an_unrecognised_short_row_is_reported_and_not_normalized(drake_root, captured):
    odd = _short_1120s("9999999", "ODD SHAPE LLC")
    odd[_I["Paid"]] = "not-a-form"
    _export(drake_root / "2019", [odd])

    results = driver.run(driver.resolve_years([2019], drake_root), connection=object())
    shapes = results[0]["shapes"]

    assert shapes["normalized"] == 0
    assert shapes["unrecognised_short"] == 1
    assert len(results[0]["anomalies"]) == 1
    assert "not a recognised return form" in results[0]["anomalies"][0]["detail"]


# --- transaction --------------------------------------------------------------------------------

def test_every_selected_year_shares_one_transaction(drake_root, monkeypatch):
    """Atomic per invocation, in both modes — the behaviour the all-years run always had."""
    connections = []

    def fake_upsert(connection, rows, *, summary=None):
        connections.append(id(connection))
        return {"rows_read": len(rows), "identified": len(rows), "inserted": 0,
                "updated": len(rows), "quarantined": 0, "quarantined_rows": []}

    monkeypatch.setattr(driver, "upsert_return_rows", fake_upsert)
    monkeypatch.setattr(driver, "identifier_hash", lambda value: f"h:{value}" if value else None)

    sentinel = object()
    driver.run(driver.resolve_years([2021, 2022], drake_root), connection=sentinel)

    assert len(connections) == 2
    assert len(set(connections)) == 1, "both years must share one connection/transaction"
    assert connections[0] == id(sentinel)


def test_importing_the_driver_does_not_run_an_import():
    """The driver used to connect and import at MODULE SCOPE, which is why it was never testable.

    The proof that it no longer does is this very module: it imports the driver at collection time and
    nothing ran. The guard below is what keeps it that way.
    """
    source = Path(driver.__file__).read_text(encoding="utf-8")

    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source
    assert driver.__name__ != "__main__"
