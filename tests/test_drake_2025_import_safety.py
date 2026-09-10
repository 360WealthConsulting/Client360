"""Importing ``scripts.import_drake_2025`` must do nothing. Running it must still do everything.

WHAT THIS REPLACES

Until this was fixed the module ran its entire import at MODULE IMPORT: it read the production env
file, opened a transaction, created tables and wrote both exports, all as top-level statements. A
previous test pinned the ABSENCE of a ``__main__`` guard, so that other tests could safely assume
the module must never be imported. That pin has done its job and is retired here; what is pinned now
is the contract that replaced it.

The distinction these tests hold apart:

    module import  -> zero side effects: no env read, no connection, no schema, no file access,
                      no transaction, no output
    main()         -> unchanged behaviour, still fail-closed without the hashing secret

The module is imported for real below, which is itself the strongest assertion available: if the
hazard returned, collecting this file would perform a Drake import.
"""

import ast
import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

MODULE = "scripts.import_drake_2025"
SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "import_drake_2025.py"
MISSING_SECRET_MESSAGE = "MICROSOFT_TOKEN_KEY is required for deterministic identifier hashing"


@pytest.fixture()
def sql_recorder():
    """Every statement any engine issues while the fixture is active."""
    seen: list[str] = []

    @event.listens_for(Engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement.strip())

    yield seen
    event.remove(Engine, "before_cursor_execute", _record)


@pytest.fixture()
def fresh_import():
    """Import the module from scratch, restoring whatever was loaded before."""
    saved = sys.modules.pop(MODULE, None)
    yield lambda: importlib.import_module(MODULE)
    sys.modules.pop(MODULE, None)
    if saved is not None:
        sys.modules[MODULE] = saved


# --- A. importing the module is inert -----------------------------------------------------------------

def test_importing_the_module_issues_no_sql(fresh_import, sql_recorder):
    fresh_import()
    assert sql_recorder == [], f"import issued SQL: {sql_recorder[:3]}"


def test_importing_the_module_does_not_import_app_db(fresh_import):
    """``app.db`` opens a connection and reflects the schema, so it belongs inside main()."""
    sys.modules.pop("app.db", None)
    fresh_import()
    assert "app.db" not in sys.modules


def test_importing_the_module_reads_no_export_file(fresh_import, monkeypatch):
    opened: list[str] = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail(
        f"import checked for {self}, which is file access"))
    fresh_import()
    assert opened == []


def test_importing_the_module_loads_no_environment_file(fresh_import, monkeypatch):
    called: list[object] = []
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: called.append(a))
    fresh_import()
    assert called == [], "import read an env file"


def test_importing_the_module_prints_nothing(fresh_import, capsys):
    fresh_import()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_importing_the_module_needs_no_hashing_secret(fresh_import, monkeypatch):
    """A bare import must not demand production credentials."""
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)
    fresh_import()  # must not raise


def test_importing_twice_is_still_side_effect_free(fresh_import, sql_recorder, capsys):
    module = fresh_import()
    importlib.reload(module)
    assert sql_recorder == []
    assert capsys.readouterr().out == ""


def test_the_module_exposes_main_behind_a_guard():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    assert any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body), \
        "main() is missing"
    guards = [n for n in tree.body
              if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
    assert len(guards) == 1, "expected exactly one __main__ guard"


def test_no_top_level_statement_executes_work():
    """Nothing at module scope may call anything but the cheap constant builders."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    allowed = {"Path", "text", "main", "SystemExit"}
    called = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                called.add(ast.unparse(sub.func))
    assert called <= allowed, f"module scope calls {sorted(called - allowed)}"


# --- B. running it still behaves ----------------------------------------------------------------------

def test_main_refuses_without_the_hashing_secret(fresh_import, monkeypatch, sql_recorder):
    """Fail closed, ahead of the env read, the file checks, the engine and the transaction."""
    module = fresh_import()
    monkeypatch.delenv("MICROSOFT_TOKEN_KEY", raising=False)

    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail(
        "the env file was read before the secret check"))
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail(
        "a file was checked before the secret check"))

    with pytest.raises(RuntimeError) as caught:
        module.main()

    assert str(caught.value) == MISSING_SECRET_MESSAGE
    assert sql_recorder == [], "main touched the database before refusing"


def test_main_refuses_when_an_export_is_missing(fresh_import, monkeypatch, sql_recorder):
    module = fresh_import()
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "import-safety-test-key")
    monkeypatch.setattr(module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    with pytest.raises(FileNotFoundError):
        module.main()

    assert sql_recorder == [], "main touched the database before checking the exports"


def test_main_runs_the_importer_when_properly_configured(fresh_import, monkeypatch, capsys):
    """The wiring is unchanged: schema, then clients, then e-file, in one transaction."""
    module = fresh_import()
    monkeypatch.setenv("MICROSOFT_TOKEN_KEY", "import-safety-test-key")
    monkeypatch.setattr(module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    executed: list[str] = []

    class FakeConnection:
        def execute(self, statement):
            executed.append(str(statement).strip().split("\n")[0][:40])

    class FakeTransaction:
        def __enter__(self):
            executed.append("<BEGIN>")
            return FakeConnection()

        def __exit__(self, *exc):
            executed.append("<COMMIT>")
            return False

    class FakeEngine:
        def begin(self):
            return FakeTransaction()

    import app.db
    monkeypatch.setattr(app.db, "engine", FakeEngine())
    monkeypatch.setattr(module, "import_clients", lambda conn: executed.append("<CLIENTS>") or 11)
    monkeypatch.setattr(module, "import_efile", lambda conn: executed.append("<EFILE>") or 7)

    assert module.main() == 0

    assert executed[0] == "<BEGIN>"
    assert executed[-1] == "<COMMIT>"
    assert "<CLIENTS>" in executed and "<EFILE>" in executed
    assert executed.index("<CLIENTS>") < executed.index("<EFILE>")
    assert any("CREATE TABLE" in item for item in executed), "schema statements were not executed"

    out = capsys.readouterr().out
    assert "Imported 11 Drake client rows." in out
    assert "Imported 7 Drake e-file rows." in out
    assert "Drake 2025 read-only import completed." in out


def test_main_uses_the_canonical_identifier_hash(fresh_import):
    from app.services.drake_identifier import identifier_hash

    module = fresh_import()
    assert module.identifier_hash is identifier_hash
