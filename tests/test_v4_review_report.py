"""Regression tests for the V4 manual-review report generator.

Covers the report-generation bug where a folder whose evidence contained a non-ASCII character
(the production "Debi McDaniel" folder) crashed the whole run: printing with ensure_ascii=False on a
non-UTF-8 stdout raised UnicodeEncodeError and terminated the loop. The fixes are ASCII-safe output
(safe_dumps / aprint) plus a per-folder try/except so one bad folder cannot end the report.

The module is loaded from scripts/ via importlib; a __main__ guard prevents main() from running on
import. Module import triggers a lightweight reflect against the test database.
"""
import importlib.util
import json
import pathlib
import re

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "v4_manual_review_report.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("v4_manual_review_report", _PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # runs module-level reflection, NOT main() (guarded by __name__)
    return m


def test_module_imports_without_running_main(mod):
    assert hasattr(mod, "main")
    assert hasattr(mod, "safe_dumps")
    assert hasattr(mod, "aprint")


def test_safe_dumps_is_pure_ascii_for_non_ascii_evidence(mod):
    # The Debi McDaniel trigger: non-ASCII in a candidate name / filename must not stay raw.
    out = mod.safe_dumps({"name": "Débi McDaniel", "doc": "café résumé — 1099 ☃"})
    assert out.isascii()                       # ASCII-only => cannot raise on a cp1252 stdout
    assert json.loads(out)["name"] == "Débi McDaniel"


def test_safe_dumps_handles_sets_and_none(mod):
    out = mod.safe_dumps({"ids": {3, 1, 2}, "x": None})
    assert out.isascii()
    json.loads(out)                            # valid JSON (set coerced via default=str)


def test_aprint_never_raises_on_non_ascii(mod, capsys):
    mod.aprint("Café résumé ☃ — Debi McDaniel")  # must not raise regardless of stdout encoding
    assert "Caf" in capsys.readouterr().out


def test_norm_and_person_heuristic(mod):
    assert mod.norm("Debi McDaniel!") == "debi mcdaniel"
    assert mod.looks_like_person("john smith") is True
    assert mod.looks_like_person("2024") is False
    assert mod.looks_like_person("") is False


# --- institution-in-filename bug: the owner context is the TOP-LEVEL TaxDome folder ---------------

def test_top_level_owner_ignores_child_paths_and_filenames(mod):
    # A payor/institution name deep in the path or filename must NOT become the owner identity.
    assert mod.top_level_owner(r"TaxDome\Chris Lucas\Client uploaded documents\Centra W2.pdf") == "Chris Lucas"
    assert mod.top_level_owner(r"TaxDome\Peter Russell\2021\1098 Lockwood Dr Wells Fargo 2021.pdf") == "Peter Russell"
    # Forward slashes, no TaxDome root, and a bare folder name all resolve to the top-level client.
    assert mod.top_level_owner("Chris Lucas/Client uploaded documents/Centra W2.pdf") == "Chris Lucas"
    assert mod.top_level_owner("Debi McDaniel") == "Debi McDaniel"


def test_owner_token_not_classified_institution_from_filename(mod):
    # The client folders "Chris Lucas" / "Peter Russell" tokenize to person-looking names, and the
    # institution keywords (Centra / Wells Fargo) live only in child paths/filenames -> the owner
    # token itself carries no institution keyword, so it is never labeled INSTITUTION_OR_PAYOR.
    for path in (r"TaxDome\Chris Lucas\Client uploaded documents\Centra W2.pdf",
                 r"TaxDome\Peter Russell\2021\1098 Lockwood Dr Wells Fargo 2021.pdf"):
        token = mod.norm(mod.top_level_owner(path))
        assert not any(re.search(r"\b" + re.escape(k) + r"\b", token) for k in mod.INST_KW)
        assert mod.looks_like_person(token) is True
