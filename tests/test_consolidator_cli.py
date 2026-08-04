"""MDM-2 consolidation CLI — console output / footer (regression).

The APPLY footer bug printed "(PREVIEW — no database changes were made)" during an --apply-clear-only
run. These tests isolate main()'s footer by stubbing consolidator.consolidate (no DB), so the console
wording is asserted for preview, --apply, and --apply-clear-only without changing merge behavior.
"""
import importlib

import pytest

cli = importlib.import_module("scripts.consolidate_duplicate_people")

_SUMMARY = {"groups": 1, "merged": 3, "skipped": 0, "ambiguous": 0, "blocked": 0, "failed": 0,
            "clear_only_qualified": 1, "report_path": "reports/mdm2_merge_report.csv",
            "group_summary_path": "reports/mdm2_group_summary.csv"}


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    calls = {}

    def _fake(**kw):
        calls.update(kw)
        return dict(_SUMMARY)
    monkeypatch.setattr(cli.consolidator, "consolidate", _fake)
    return calls


def test_preview_footer(capsys, _stub):
    cli.main(["--preview"])
    out = capsys.readouterr().out
    assert "PREVIEW (no changes)" in out
    assert "(PREVIEW — no database changes were made)" in out
    assert "APPLY COMPLETE" not in out
    assert _stub["apply"] is False and _stub["apply_clear_only"] is False


def test_apply_footer(capsys, _stub):
    cli.main(["--apply"])
    out = capsys.readouterr().out
    assert "(APPLY COMPLETE — database changes were made)" in out
    assert "PREVIEW — no database changes" not in out
    assert _stub["apply"] is True


def test_apply_clear_only_footer(capsys, _stub):
    cli.main(["--apply-clear-only"])
    out = capsys.readouterr().out
    assert "APPLY (clear-only)" in out
    assert "(APPLY COMPLETE — database changes were made)" in out       # the fixed bug
    assert "PREVIEW — no database changes" not in out
    assert _stub["apply_clear_only"] is True


def test_default_is_preview_footer(capsys, _stub):
    cli.main([])
    out = capsys.readouterr().out
    assert "(PREVIEW — no database changes were made)" in out
