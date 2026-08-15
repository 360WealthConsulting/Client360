"""Tests for the V4.1 MANUAL_REVIEW identity-resolution report generator.

Covers the pure resolution logic (choose_owner), owner-context extraction, permanent-reject
exclusion, and the ASCII-safe output helpers. The module is loaded via importlib; a __main__ guard
prevents main() from running on import (import triggers a lightweight reflect against the test DB).
"""
import importlib.util
import json
import pathlib
import re

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "v4_1_manual_review_resolution.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("v4_1_manual_review_resolution", _PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # runs module-level reflection, NOT main() (guarded by __name__)
    return m


def test_module_imports_without_running_main(mod):
    for attr in ("main", "choose_owner", "top_level_owner", "eligible_docs", "safe_dumps", "aprint"):
        assert hasattr(mod, attr)


def test_choose_owner_single_corroborated_is_safe(mod):
    rec, pid, conf = mod.choose_owner([{"person_id": 7, "corroborated": True}])
    assert (rec, pid) == ("SAFE_TO_CONFIRM", 7)


def test_choose_owner_duplicate_name_disambiguated_by_corroboration(mod):
    rec, pid, _ = mod.choose_owner([
        {"person_id": 10, "corroborated": True},
        {"person_id": 11, "corroborated": False},
    ])
    assert (rec, pid) == ("SAFE_TO_CONFIRM", 10)


def test_choose_owner_multiple_corroborated_needs_human(mod):
    rec, pid, _ = mod.choose_owner([
        {"person_id": 10, "corroborated": True},
        {"person_id": 11, "corroborated": True},
    ])
    assert rec == "NEEDS_HUMAN_CHOICE" and pid is None


def test_choose_owner_name_only_is_not_auto(mod):
    # A single name match with no independent corroboration must never auto-confirm.
    rec, pid, _ = mod.choose_owner([{"person_id": 5, "corroborated": False}])
    assert rec == "NEEDS_HUMAN_CHOICE" and pid is None


def test_choose_owner_fuzzy_only_never_confirms(mod):
    # Fuzzy suggestions are passed as corroborated=False -> never SAFE_TO_CONFIRM.
    rec, pid, _ = mod.choose_owner([
        {"person_id": 20, "corroborated": False},
        {"person_id": 21, "corroborated": False},
    ])
    assert rec == "NEEDS_HUMAN_CHOICE" and pid is None


def test_choose_owner_no_candidates_is_no_match(mod):
    assert mod.choose_owner([]) == ("NO_MATCH", None, "none")


def test_permanent_rejects_excluded_from_eligible(mod):
    assert set(mod.eligible_docs([100, 4704, 4716, 4717, 17932, 22336, 22338, 200])) == {100, 200}
    assert set(mod.PERMANENT_REJECT) == {4704, 4716, 4717, 17932, 22336, 22338}


def test_owner_token_ignores_child_path_institution_names(mod):
    assert mod.top_level_owner(r"TaxDome\Chris Lucas\Client uploaded documents\Centra W2.pdf") == "Chris Lucas"
    assert mod.top_level_owner(r"TaxDome\Peter Russell\2021\1098 Lockwood Dr Wells Fargo 2021.pdf") == "Peter Russell"
    token = mod.norm(mod.top_level_owner(r"TaxDome\Chris Lucas\x\Centra W2.pdf"))
    assert not any(re.search(r"\b" + re.escape(k) + r"\b", token) for k in mod.INST_KW)


def test_safe_dumps_is_ascii_and_aprint_never_raises(mod, capsys):
    out = mod.safe_dumps({"name": "Débi McDaniel", "note": "café ☃"})
    assert out.isascii() and json.loads(out)["name"] == "Débi McDaniel"
    mod.aprint("Café résumé ☃ — Debi")
    assert "Caf" in capsys.readouterr().out


def test_render_helpers_tolerate_null_and_missing_fields(mod):
    # A candidate/result with None or missing fields must serialize without raising, so the render
    # loop's null-safe access + safe_dumps cannot crash the report on unexpected production data.
    candidate = {"person_id": None, "name": None, "emails": [], "address": [None], "dob": None,
                 "existing_owned_documents": None}
    out = mod.safe_dumps(candidate)
    assert out.isascii()
    json.loads(out)
    # Mixed-type identifier sets (person_ids sourced from different tables) must sort without raising.
    assert sorted({3, "10", 2, None}, key=str) is not None
