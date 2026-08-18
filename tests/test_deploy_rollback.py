"""Data-safe rollback gates (Phase 0B).

Proves the rollback refuses to downgrade unless a VERIFIED backup exists and the operator confirmed:
backup success -> allowed; backup failure -> refused; verification failure -> refused; missing
confirmation -> refused; dry-run -> no downgrade; and the backup location is printed BEFORE the downgrade.
The real pg_dump/pg_restore backup+verify round-trip is exercised against the test database.
"""
from __future__ import annotations

import builtins

import pytest

from app.deploy import rollback as rb

_TARGET = "billing01"   # any prior revision; the downgrade itself is spied so the DB is never mutated


@pytest.fixture
def _spy_downgrade(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "_downgrade", lambda target: calls.append(target))
    return calls


# --- real backup tooling (pg_dump -Fc + pg_restore --list) -------------------

def test_real_backup_create_and_verify_roundtrip(tmp_path):
    path = rb.default_backup_path(_TARGET, backup_dir=str(tmp_path))
    rb.create_backup(path)
    assert path.exists() and path.stat().st_size > rb._MIN_BACKUP_BYTES
    rb.verify_backup(path)                     # a real pg_dump archive verifies


def test_verify_backup_rejects_empty_and_corrupt(tmp_path):
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    with pytest.raises(rb.BackupVerificationError):
        rb.verify_backup(empty)                # too small
    corrupt = tmp_path / "corrupt.dump"
    corrupt.write_bytes(b"X" * (rb._MIN_BACKUP_BYTES + 100))
    with pytest.raises(rb.BackupVerificationError):
        rb.verify_backup(corrupt)              # not a pg_restore archive
    missing = tmp_path / "nope.dump"
    with pytest.raises(rb.BackupVerificationError):
        rb.verify_backup(missing)


# --- gate: backup success -> rollback allowed --------------------------------

def test_backup_success_allows_rollback(tmp_path, _spy_downgrade):
    result = rb.run(_TARGET, assume_yes=True, backup_dir=str(tmp_path))
    assert _spy_downgrade == [_TARGET]                       # downgrade reached
    assert result["applied"] is True
    dumps = list(tmp_path.glob("*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_size > rb._MIN_BACKUP_BYTES   # a verified backup exists


def test_backup_location_printed_before_downgrade(tmp_path, _spy_downgrade, capsys):
    rb.run(_TARGET, assume_yes=True, backup_dir=str(tmp_path))
    out = capsys.readouterr().out
    assert "backup VERIFIED:" in out and "downgrading" in out
    assert out.index("backup VERIFIED:") < out.index("downgrading")    # location printed FIRST


# --- gate: backup failure -> rollback refused, no downgrade ------------------

def test_backup_failure_refuses_rollback(monkeypatch, tmp_path, _spy_downgrade):
    def _boom(path):
        raise rb.BackupError("simulated pg_dump failure")
    monkeypatch.setattr(rb, "create_backup", _boom)
    with pytest.raises(rb.BackupError):
        rb.run(_TARGET, assume_yes=True, backup_dir=str(tmp_path))
    assert _spy_downgrade == []                              # NEVER downgraded


# --- gate: verification failure -> rollback refused, no downgrade ------------

def test_verification_failure_refuses_rollback(monkeypatch, tmp_path, _spy_downgrade):
    monkeypatch.setattr(rb, "create_backup", lambda path: path)   # pretend backup "succeeded"
    monkeypatch.setattr(rb, "verify_backup", lambda path: (_ for _ in ()).throw(
        rb.BackupVerificationError("simulated bad dump")))
    with pytest.raises(rb.BackupVerificationError):
        rb.run(_TARGET, assume_yes=True, backup_dir=str(tmp_path))
    assert _spy_downgrade == []                              # NEVER downgraded


# --- gate: missing confirmation -> refused BEFORE backup or downgrade --------

def test_missing_confirmation_refuses_before_backup(monkeypatch, tmp_path, _spy_downgrade):
    backup_calls = []
    monkeypatch.setattr(rb, "create_backup", lambda path: backup_calls.append(path))
    with pytest.raises(rb.ConfirmationRequired):
        rb.run(_TARGET, assume_yes=False, confirm=None, backup_dir=str(tmp_path))
    assert backup_calls == [] and _spy_downgrade == []      # no backup, no downgrade

    with pytest.raises(rb.ConfirmationRequired):
        rb.run(_TARGET, assume_yes=False, confirm=lambda: False, backup_dir=str(tmp_path))
    assert backup_calls == [] and _spy_downgrade == []


# --- dry-run -> no downgrade, no backup, no changes -------------------------

def test_dry_run_makes_no_changes(monkeypatch, _spy_downgrade, capsys):
    backup_calls = []
    monkeypatch.setattr(rb, "create_backup", lambda path: backup_calls.append(path))
    rc = rb.main(["--to", _TARGET, "--dry-run"])
    assert rc == 0
    assert backup_calls == [] and _spy_downgrade == []
    out = capsys.readouterr().out
    assert "DRY RUN" in out and _TARGET in out


# --- CLI: missing confirmation returns non-zero (interactive 'no') ----------

def test_main_refuses_without_confirmation(monkeypatch, tmp_path, _spy_downgrade):
    backup_calls = []
    monkeypatch.setattr(rb, "create_backup", lambda path: backup_calls.append(path))
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "no")     # operator declines
    rc = rb.main(["--to", _TARGET, "--backup-dir", str(tmp_path)])
    assert rc == 1                                          # ConfirmationRequired -> exit 1
    assert backup_calls == [] and _spy_downgrade == []
