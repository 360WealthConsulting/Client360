"""The S4U preflight must validate the roots Client360 actually needs — no more, no less.

The defect these tests pin: ``install_ocr_supervisor_task.ps1`` gated registration on a hard-coded
``@('C','D','T')``. Nothing in the repository derives that list; no config default, env var, document
or test mentions ``T:``. So the preflight FAILED a host that was perfectly able to run the supervisor,
and — because ``Win32_LogicalDisk`` enumerates drive letters only — PASSED a host whose required UNC
share was unreachable.

The correction makes the required set the union of two independently-derived halves: the permanent
roots the RUNTIME has effectively configured, and the roots eligible DATABASE rows actually reference.
Both directions of error are pinned here, because only fixing the false failure would have been easy
and wrong:

  * a false FAILURE — requiring a volume nothing uses (``T:``; a legacy source-code default; an
    ingestion-only root), and
  * an unsafe false PASS — accepting a volume the S4U task cannot really read (a mapped drive, a
    cloud-sync mount on a local disk, an unattested UNC share, a root that exists but is unreadable).

These run without a database and without PowerShell: the classification takes the host volume table
and the filesystem probe as injected data, so CI (ubuntu) exercises the real decision logic rather
than asserting on script text. The script-text assertions at the bottom cover only the parts that are
genuinely PowerShell.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.deploy import ocr_source_roots as roots
from app.deploy.ocr_source_roots import (
    DRIVE_TYPE_LOCAL_DISK,
    DRIVE_TYPE_NETWORK,
    DRIVE_TYPE_OPTICAL,
    DeclaredRoot,
    Probe,
    RequiredRoot,
    Volume,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "deploy" / "windows" / "install_ocr_supervisor_task.ps1"


# --- helpers -------------------------------------------------------------------------------------

def _volumes(*specs) -> dict[str, Volume]:
    """``_volumes(('C:', 3), ('Z:', 4, {'session_mapped': True}))`` -> the inventory mapping."""
    out = {}
    for spec in specs:
        anchor, drive_type = spec[0], spec[1]
        extra = spec[2] if len(spec) > 2 else {}
        out[anchor.rstrip("\\").casefold()] = Volume(anchor=anchor, drive_type=drive_type, **extra)
    return out


def _probe(**paths):
    """A filesystem double. Any path not named is missing, which is the fail-closed default."""
    table = {k.replace("/", "\\").rstrip("\\").casefold(): v for k, v in paths.items()}

    def probe(path):
        return table.get(str(path).replace("/", "\\").rstrip("\\").casefold(),
                         Probe(exists=False, readable=False, error="does not exist"))
    return probe


_READABLE = Probe(exists=True, readable=True)


def _required(path, anchor, origin="configured", detail="a root"):
    return RequiredRoot(path=path, anchor=anchor, origin=origin, detail=detail)


def _verdict(root, *, volumes, probe, attested=()):
    return roots.classify_root(root, volumes=volumes, probe=probe, attested_unc=attested)


# --- anchor extraction ----------------------------------------------------------------------------

@pytest.mark.parametrize(("reference", "expected"), [
    (r"C:\Client360\Data\Documents\Drake", "C:"),
    (r"d:\360PlusData\Documents", "D:"),
    ("C:/Client360/Data", "C:"),                              # forward slashes are still Windows
    (r"\\fileserver\clientdocs\2025\return.pdf", r"\\fileserver\clientdocs"),
    (r"\\?\UNC\fileserver\clientdocs\x.pdf", r"\\fileserver\clientdocs"),
    (r"\\?\D:\360PlusData\x.pdf", "D:"),                      # extended-length prefix is stripped
    ("file:///D:/360PlusData/x.pdf", "D:"),
    (r"\\fileserver", None),                                  # a host with no share names no root
    ("", None),
    (None, None),
])
def test_the_anchor_of_a_reference_is_the_volume_it_depends_on(reference, expected):
    assert roots.path_anchor(reference) == expected


def test_a_relative_reference_belongs_to_the_working_directory():
    """The supervisor's cwd is the project root, so a repo-relative storage_path lives on its volume."""
    assert roots.path_anchor("documents/42/x.pdf", relative_base=r"C:\Client360") == "C:"
    assert roots.path_anchor("documents/42/x.pdf") is None


# --- the good case --------------------------------------------------------------------------------

def test_a_root_on_a_local_fixed_disk_passes():
    root = _required(r"D:\360PlusData\Documents\Drake", "D:")
    verdict = _verdict(root, volumes=_volumes(("D:", DRIVE_TYPE_LOCAL_DISK)),
                       probe=_probe(**{r"D:\360PlusData\Documents\Drake": _READABLE}))
    assert verdict.ok
    assert verdict.code == "local_fixed_disk"
    assert "D:" in verdict.message


def test_every_required_root_is_named_in_the_diagnostics_with_its_reason():
    """Requirement: the operator must be able to read off WHICH root and WHY, not just pass/fail."""
    report = roots.evaluate(
        configured=[_required(r"D:\360PlusData", "D:", detail="canonical Drake local-copy root")],
        database=[_required("Q:\\", "Q:", origin="database", detail="7 outstanding documents")],
        volumes=_volumes(("D:", DRIVE_TYPE_LOCAL_DISK)),
        probe=_probe(**{r"D:\360PlusData": _READABLE}))
    rendered = report.render()
    assert not report.ok
    assert r"D:\360PlusData" in rendered and "canonical Drake local-copy root" in rendered
    assert "Q:" in rendered and "7 outstanding documents" in rendered
    assert "configured" in rendered and "database" in rendered
    assert "PREFLIGHT FAILED" in rendered


# --- false failures: a drive nothing uses is not a prerequisite -------------------------------------

def test_an_absent_drive_that_nothing_requires_is_never_checked():
    """The original bug, in one test. T: is absent AND required by nothing, so it is not a failure."""
    report = roots.evaluate(
        configured=[_required(r"C:\Client360\Data\Documents\Drake", "C:")],
        database=[_required("C:\\", "C:", origin="database", detail="12 outstanding documents")],
        volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)),                 # no T: on this host
        probe=_probe(**{r"C:\Client360\Data\Documents\Drake": _READABLE, "C:\\": _READABLE}))
    assert report.ok, report.render()
    assert not any("T:" in f.root.anchor for f in report.findings)


def test_a_permanent_root_left_on_its_legacy_source_code_default_is_not_required():
    """A literal in a module is not an operator's decision. Requiring it would be the same bug again."""
    declared = [DeclaredRoot(path=r"C:\Client360\Data\Documents\TaxDome",
                             detail="canonical TaxDome local-copy root (…)",
                             kind="permanent", configured=False)]
    assert roots.configured_roots(declared=declared) == []

    report = roots.evaluate(configured=[], database=[], volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)),
                            probe=_probe(), declared=declared)
    assert report.ok
    assert any("not configured at runtime" in note for note in report.reported), report.render()


def test_a_configured_permanent_root_is_required_before_any_document_lives_there():
    """The half the database cannot supply: a freshly configured root has no rows pointing at it yet."""
    declared = [DeclaredRoot(path=r"E:\360PlusData\Documents\Drake",
                             detail="canonical Drake local-copy root (…)",
                             kind="permanent", configured=True)]
    required = roots.configured_roots(declared=declared)
    assert [r.anchor for r in required] == ["E:"]

    report = roots.evaluate(configured=required, database=[],
                            volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)), probe=_probe())
    assert not report.ok, "a configured root missing from the host must fail closed"
    assert report.failures[0].code == "volume_absent"


def test_an_ingestion_only_root_is_reported_but_not_required():
    """Z: is where the TaxDome importer READS FROM. OCR opens the canonical copy, never the source."""
    declared = [DeclaredRoot(path="Z:\\", detail="TaxDome Drive INGESTION source (…)",
                             kind="ingestion", configured=True)]
    assert roots.configured_roots(declared=declared) == []

    report = roots.evaluate(configured=[], database=[], volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)),
                            probe=_probe(), declared=declared)
    assert report.ok, "an unmapped ingestion drive must not block the OCR task"
    assert any("ingestion-only" in note and "Z:" in note for note in report.reported), report.render()


def test_z_becomes_required_when_eligible_documents_actually_reference_it():
    """The database half is what promotes a root, and a mapped Z: then fails closed rather than
    registering a task that silently cannot read those documents."""
    database = roots.database_roots([
        {"storage_uri": r"Z:\TaxDome\client\return.pdf", "storage_path": None,
         "storage_provider": "Client360 Local", "documents": 4}])
    assert [r.anchor for r in database] == ["Z:"]

    report = roots.evaluate(
        configured=[], database=database,
        volumes=_volumes(("Z:", DRIVE_TYPE_NETWORK, {"session_mapped": True})), probe=_probe(),
        declared=[DeclaredRoot(path="Z:\\", detail="TaxDome Drive INGESTION source (…)",
                               kind="ingestion", configured=True)])
    assert not report.ok
    assert report.failures[0].code == "mapped_drive"
    assert "4 outstanding documents" in report.render()
    assert report.reported == [], "a root promoted by the database is required, not merely reported"


# --- unsafe false passes: present is not the same as readable under S4U ------------------------------

def test_a_mapped_network_drive_fails_even_though_it_is_present():
    root = _required("Z:\\", "Z:", origin="database", detail="3 outstanding documents")
    verdict = _verdict(root, volumes=_volumes(("Z:", DRIVE_TYPE_NETWORK,
                                               {"provider_name": r"\\nas\taxdome"})),
                       probe=_probe(**{"Z:\\": _READABLE}))
    assert not verdict.ok
    assert verdict.code == "mapped_drive"
    assert "interactive logon session" in verdict.message


def test_a_session_mapping_fails_even_when_it_reports_itself_as_a_local_disk():
    """Win32_MappedLogicalDisk is consulted separately precisely because DriveType can look benign."""
    root = _required("Y:\\", "Y:", origin="database", detail="1 outstanding document")
    verdict = _verdict(root, volumes=_volumes(("Y:", DRIVE_TYPE_LOCAL_DISK, {"session_mapped": True})),
                       probe=_probe(**{"Y:\\": _READABLE}))
    assert not verdict.ok
    assert verdict.code == "mapped_drive"


def test_a_cloud_sync_mount_fails_although_it_sits_on_a_local_fixed_disk():
    """The check the old DriveType test could never make: OneDrive placeholders are on C:, and are
    materialised by a per-user session filter that a session-less task does not have."""
    root = _required(r"C:\Users\svc\OneDrive - 360 Wealth\Documents", "C:")
    verdict = _verdict(
        root, volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)),
        probe=_probe(**{r"C:\Users\svc\OneDrive - 360 Wealth\Documents":
                        Probe(exists=True, readable=True, cloud=True)}))
    assert not verdict.ok
    assert verdict.code == "cloud_mount"
    assert "session" in verdict.message


def test_a_required_root_on_a_volume_that_is_absent_fails_closed():
    root = _required(r"T:\Scans", "T:", origin="database", detail="9 outstanding documents")
    verdict = _verdict(root, volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)), probe=_probe())
    assert not verdict.ok
    assert verdict.code == "volume_absent"
    assert "cannot be skipped" in verdict.message


def test_a_root_whose_directory_is_missing_fails_even_on_a_good_disk():
    root = _required(r"D:\360PlusData\Documents\Drake", "D:")
    verdict = _verdict(root, volumes=_volumes(("D:", DRIVE_TYPE_LOCAL_DISK)), probe=_probe())
    assert not verdict.ok
    assert verdict.code == "path_missing"


def test_a_root_that_exists_but_cannot_be_read_by_this_identity_fails():
    """Inaccessible is not the same as absent, and neither is acceptable."""
    root = _required(r"D:\360PlusData\Documents\Drake", "D:")
    verdict = _verdict(root, volumes=_volumes(("D:", DRIVE_TYPE_LOCAL_DISK)),
                       probe=_probe(**{r"D:\360PlusData\Documents\Drake":
                                       Probe(exists=True, readable=False,
                                             error="permission denied: [WinError 5]")}))
    assert not verdict.ok
    assert verdict.code == "path_unreadable"
    assert "permission denied" in verdict.message


@pytest.mark.parametrize("drive_type", [DRIVE_TYPE_OPTICAL, 0, 1, 2, 6])
def test_any_volume_that_is_not_a_local_fixed_disk_fails(drive_type):
    """Fail closed on every unknown: a type the check does not recognise is not a pass."""
    root = _required(r"R:\docs", "R:")
    verdict = _verdict(root, volumes=_volumes(("R:", drive_type)),
                       probe=_probe(**{r"R:\docs": _READABLE}))
    assert not verdict.ok
    assert verdict.code == "not_local_disk"


def test_a_root_whose_volume_is_missing_from_the_inventory_fails_rather_than_defaulting_to_pass():
    root = _required(r"D:\docs", "D:")
    verdict = _verdict(root, volumes={}, probe=_probe(**{r"D:\docs": _READABLE}))
    assert not verdict.ok
    assert verdict.code == "volume_absent"


# --- UNC ----------------------------------------------------------------------------------------------

def test_a_required_unc_root_fails_closed_without_an_explicit_attestation():
    """An S4U task has no network credentials; an interactive probe proves only that the OPERATOR
    can reach the share."""
    root = _required(r"\\fileserver\clientdocs", r"\\fileserver\clientdocs",
                     origin="database", detail="22 outstanding documents")
    verdict = _verdict(root, volumes=_volumes(("C:", DRIVE_TYPE_LOCAL_DISK)),
                       probe=_probe(**{r"\\fileserver\clientdocs": _READABLE}))
    assert not verdict.ok
    assert verdict.code == "unc_not_attested"
    assert "-AttestUncRoot" in verdict.message, "the diagnostic must say how to proceed legitimately"


def test_an_attested_unc_root_passes_only_when_it_is_present_and_readable():
    root = _required(r"\\fileserver\clientdocs", r"\\fileserver\clientdocs")
    attested = [r"\\fileserver\clientdocs"]

    ok = _verdict(root, volumes={}, probe=_probe(**{r"\\fileserver\clientdocs": _READABLE}),
                  attested=attested)
    assert ok.ok and ok.code == "attested_unc"

    missing = _verdict(root, volumes={}, probe=_probe(), attested=attested)
    assert not missing.ok and missing.code == "path_missing", \
        "attestation answers the credential question only; it must not skip existence"

    unreadable = _verdict(root, volumes={},
                          probe=_probe(**{r"\\fileserver\clientdocs":
                                          Probe(exists=True, readable=False, error="denied")}),
                          attested=attested)
    assert not unreadable.ok and unreadable.code == "path_unreadable", \
        "attestation must not skip readability either"


def test_attesting_one_share_does_not_attest_another():
    root = _required(r"\\other\share", r"\\other\share")
    verdict = _verdict(root, volumes={}, probe=_probe(**{r"\\other\share": _READABLE}),
                       attested=[r"\\fileserver\clientdocs"])
    assert not verdict.ok and verdict.code == "unc_not_attested"


# --- the database half ----------------------------------------------------------------------------------

def test_document_rows_collapse_onto_the_distinct_volumes_they_require():
    required = roots.database_roots([
        {"storage_uri": r"D:\360PlusData\a.pdf", "storage_path": None,
         "storage_provider": "Client360 Local", "documents": 3},
        {"storage_uri": r"d:\360PlusData\b.pdf", "storage_path": None,
         "storage_provider": "Client360 Repository", "documents": 2},
        {"storage_uri": r"\\nas\share\c.pdf", "storage_path": None,
         "storage_provider": "Client360 Local", "documents": 1},
    ])
    by_anchor = {r.anchor: r for r in required}
    assert set(by_anchor) == {"D:", r"\\nas\share"}, "case must not split one volume in two"
    assert "5 outstanding documents" in by_anchor["D:"].detail
    assert "1 outstanding document" in by_anchor[r"\\nas\share"].detail


def test_a_remotely_stored_document_implies_no_local_root():
    assert roots.database_roots([
        {"storage_uri": "https://graph.microsoft.com/x", "storage_path": None,
         "storage_provider": "SharePoint Online", "documents": 900}]) == []


def test_storage_uri_wins_over_storage_path_exactly_as_ocr_reads_them():
    required = roots.database_roots([
        {"storage_uri": r"D:\canonical\a.pdf", "storage_path": r"T:\legacy\a.pdf",
         "storage_provider": "Client360 Local", "documents": 1}])
    assert [r.anchor for r in required] == ["D:"], \
        "document_ocr._local_path prefers storage_uri, and the preflight must agree"


def test_a_relative_storage_path_is_charged_to_the_working_directory():
    required = roots.database_roots(
        [{"storage_uri": None, "storage_path": "documents/42/x.pdf",
          "storage_provider": "Client360 Local", "documents": 1}],
        project_root=r"C:\Client360")
    assert [r.anchor for r in required] == ["C:"]


def test_only_documents_the_supervisor_will_attempt_can_require_a_volume():
    """A completed or terminally-unsupported document is never opened again, so it must not pin a
    volume; a pending or retryable one must."""
    sql = " ".join(roots.SQL_DATABASE_ANCHORS.split())
    assert "d.status = 'active'" in sql and "d.deleted_at IS NULL" in sql
    assert "d.archived = false" in sql and "d.archived_at IS NULL" in sql
    assert "o.document_id IS NULL" in sql, "a never-attempted document still requires its volume"
    for state in ("pending", "processing", "failed", "timed_out"):
        assert f"'{state}'" in sql
    for terminal in ("completed", "unsupported", "encrypted"):
        assert f"'{terminal}'" not in sql, f"{terminal} documents are never reopened"


def test_the_union_requires_a_root_that_either_half_names():
    configured = [_required(r"E:\new", "E:")]
    database = [_required("D:\\", "D:", origin="database", detail="5 outstanding documents")]
    assert {r.anchor for r in roots.required_roots(configured=configured, database=database)} \
        == {"E:", "D:"}


def test_the_union_does_not_check_one_root_twice():
    same = [_required(r"D:\x", "D:")]
    assert len(roots.required_roots(configured=same, database=list(same))) == 1


def test_a_failed_database_read_is_never_silently_an_empty_required_set():
    """"Cannot tell" degrading into "nothing required" is the shape of the original defect."""
    class Boom:
        def connect(self):
            raise RuntimeError("could not connect to server")

    with pytest.raises(RuntimeError, match="could not connect"):
        roots.read_database_anchors(Boom())


# --- the host inventory the installer pipes in -----------------------------------------------------------

def test_the_cim_volume_table_is_parsed_including_a_mapping_with_no_logical_disk_row():
    parsed = roots.volumes_from_inventory({
        "volumes": [{"anchor": "C:", "drive_type": 3, "provider_name": None, "file_system": "NTFS"},
                    {"anchor": "Z:", "drive_type": 4, "provider_name": r"\\nas\taxdome"}],
        "mapped_anchors": ["Z:", "Y:"]})
    assert parsed["c:"].drive_type == DRIVE_TYPE_LOCAL_DISK and not parsed["c:"].session_mapped
    assert parsed["z:"].session_mapped and parsed["z:"].provider_name == r"\\nas\taxdome"
    assert parsed["y:"].session_mapped, "a session mapping counts even with no logical-disk row"


def test_an_empty_inventory_yields_no_volumes_and_therefore_no_passes():
    assert roots.volumes_from_inventory({}) == {}


# --- the installer script --------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def installer() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_the_installer_no_longer_carries_a_hard_coded_drive_list(installer):
    assert not re.search(r"\$required\s*=\s*@\(\s*'C'", installer), \
        "the historical @('C','D','T') inventory is the defect"
    body = "\n".join(ln for ln in installer.splitlines()
                     if not ln.strip().startswith(("#", "Write-Host")))
    assert "'T'" not in body and "T:" not in body, \
        "no drive letter may be required by virtue of appearing in the script"


def test_the_installer_delegates_the_decision_to_the_source_root_authority(installer):
    assert "app.deploy.ocr_source_roots" in installer
    assert "Win32_MappedLogicalDisk" in installer, "session mappings must be enumerated separately"
    assert "ConvertTo-Json" in installer, "the volume table is handed over as data"
    assert 'throw "S4U preflight failed' in installer, "a failed preflight must stop, not warn"


def test_the_installer_still_proves_the_database_is_reachable(installer):
    assert "DATABASE_URL" in installer and "database unreachable" in installer


def test_registration_terminates_on_failure_and_never_claims_a_success_it_did_not_have(installer):
    """Access Denied used to print "Registered ...": the result was piped to Out-Null and the success
    line ran unconditionally."""
    assert not re.search(r"Register-ScheduledTask[^\n]*\|\s*Out-Null", installer), \
        "discarding the result is what hid the failure"
    register = re.search(r"\$registered\s*=\s*Register-ScheduledTask.*?(?=\n\s*\}\s*catch)",
                         installer, re.S)
    assert register and "-ErrorAction Stop" in register.group(0)

    lines = installer.splitlines()
    start = next(i for i, ln in enumerate(lines) if "$registered = Register-ScheduledTask" in ln)
    success = next(i for i, ln in enumerate(lines) if 'Write-Host "Registered' in ln)
    between = "\n".join(lines[start:success])
    assert "catch" in between and "throw" in between, "a failure must terminate before the success line"
    assert "Get-ScheduledTask" in between, "the task must be re-queried before success is claimed"
    assert "Access Denied" in between, "the likely cause should be named in the error"


def test_the_unc_attestation_is_documented_as_credentials_only(installer):
    assert "$AttestUncRoot" in installer
    assert "--attest-unc" in installer
    assert re.search(r"no network credentials", installer, re.I)
