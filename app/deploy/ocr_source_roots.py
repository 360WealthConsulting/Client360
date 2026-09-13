"""Which source roots the OCR supervisor actually needs, and whether an S4U task can read them.

WHY THIS EXISTS
    ``deploy/windows/install_ocr_supervisor_task.ps1`` used to gate registration on a hard-coded
    ``@('C','D','T')`` — an inventory of drive letters someone once used, with no derivation anywhere
    in the tree. Nothing else in the repository mentions ``T:``: not a config default, not an env var,
    not a document, not a test. So the preflight could fail on a host that was perfectly able to run
    the supervisor (``T:`` absent but irrelevant), while passing a host that was not (a required UNC
    share, which ``Win32_LogicalDisk`` cannot even see).

    OCR does not read a drive list. ``app.services.document_ocr._local_path`` reads ``storage_uri``
    then ``storage_path`` off the database row. The roots that matter are therefore the ones the
    CONFIGURATION declares and the ones the DATABASE actually references — nothing historical.

THE AUTHORITY
    The required set is the UNION of two independently-derived halves, and every member must pass:

      1. EFFECTIVELY CONFIGURED PERMANENT roots, resolved through ``app.services.storage_paths`` —
         the repository's single declared root authority. No drive letter is written down here;
         whatever that chain resolves to is what gets checked. Two exclusions keep this half honest:

           * a root still sitting on its LEGACY SOURCE-CODE DEFAULT is not required. A literal in a
             module is not an operator's decision, and requiring one would recreate the original bug
             in a new costume — failing a host over a path nothing on it uses.
           * an INGESTION-only root is not required. ``TAXDOME_DRIVE_ROOT`` (``Z:\\``),
             ``DRAKE_EXPORT_ROOT`` and the SharePoint staging tree are what importers READ FROM
             before writing a canonical copy. OCR opens the canonical copy, never the ingestion
             source. Conflating the two is what made a per-session ``Z:`` mapping look like an OCR
             prerequisite.

         Both are REPORTED rather than dropped, so the output shows they were considered.
      2. DATABASE-REQUIRED roots: the distinct path ANCHORS (``C:``, or ``\\\\server\\share`` for UNC)
         referenced by rows the supervisor will actually attempt. This half is what can still make an
         unconfigured or ingestion root required — if eligible documents genuinely live there, the
         volume is required no matter which half declared it.

    Neither half is sufficient alone, which is the entire point of taking the union. The config half
    alone would miss a drive that configuration no longer mentions but documents still live on. The
    database half alone would let a newly configured root pass merely because nothing has been written
    there yet. Together, a root is required the moment EITHER says so.

    A drive is never required because it is famous. ``T:`` is absent from both halves, so it is not
    probed at all — the claim made here is the checkable "T: is not a source root", never the
    unfalsifiable "T: is safe". Equally, nothing in this module counts rows or compares a count to a
    threshold: a corpus that grows onto a new volume tomorrow makes that volume required tomorrow.

FAIL CLOSED
    Every unknown is a failure. A root whose volume is missing from the inventory fails; a volume of
    unknown type fails; a path that cannot be probed fails. :func:`read_database_anchors` propagates
    its errors rather than returning an empty set, because "the query failed" degrading silently to
    "no roots required" is precisely how the original defect would come back.

S4U REACHABILITY
    An S4U task runs with no logon session and NO NETWORK CREDENTIALS. Three things that work fine
    when an operator tests them interactively do not survive that:

      * a MAPPED DRIVE belongs to the interactive logon session and simply is not there;
      * a CLOUD SYNC mount (OneDrive and friends) is per-user and, with Files On-Demand, is a
        placeholder that must be materialised by a user-session filter driver. It commonly sits on a
        DriveType 3 volume, which is why "is it a local disk" was never a sufficient question;
      * a UNC path may resolve interactively on the operator's own credentials and be unreachable
        under S4U. It is failed closed unless the operator explicitly attests that the MACHINE
        account has been granted the share (``attested_unc``). That attestation covers only the
        credential question — existence and readability are still checked for an attested root, so it
        can never be used to skip missing-source validation.

STRICTLY READ-ONLY
    Queries with SELECT, probes with stat/scandir. It never writes, mkdirs, registers, or mutates
    anything, and it is safe to run against a production database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

# Win32_LogicalDisk.DriveType. Only a local fixed disk is visible to a session-less task.
DRIVE_TYPE_UNKNOWN = 0
DRIVE_TYPE_NO_ROOT = 1
DRIVE_TYPE_REMOVABLE = 2
DRIVE_TYPE_LOCAL_DISK = 3
DRIVE_TYPE_NETWORK = 4
DRIVE_TYPE_OPTICAL = 5
DRIVE_TYPE_RAM_DISK = 6

_DRIVE_TYPE_NAMES = {
    DRIVE_TYPE_UNKNOWN: "unknown",
    DRIVE_TYPE_NO_ROOT: "no root directory",
    DRIVE_TYPE_REMOVABLE: "removable",
    DRIVE_TYPE_LOCAL_DISK: "local disk",
    DRIVE_TYPE_NETWORK: "network",
    DRIVE_TYPE_OPTICAL: "optical",
    DRIVE_TYPE_RAM_DISK: "RAM disk",
}

#: Environment variables the OneDrive client sets for the roots it syncs into the user profile.
_CLOUD_ROOT_ENV = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")

#: IO_REPARSE_TAG_CLOUD and its _1.._F siblings (0x9000001A, 0x9000101A, ... 0x9000F01A). The nibble
#: that varies identifies the sync provider, so it is masked out before comparing.
_CLOUD_REPARSE_TAG = 0x9000001A
_CLOUD_REPARSE_MASK = 0xFFFF0FFF

#: ``storage_provider`` values whose bytes are a local file this host can read. Mirrors
#: ``app.deploy.document_integrity._LOCAL_PROVIDERS``; anything else is remote-stored and contributes
#: no local root.
LOCAL_PROVIDERS = frozenset(
    {None, "", "local", "file", "filesystem", "Client360 Local", "Client360 Repository"})


# --- anchor extraction ----------------------------------------------------------------------------
# Deliberately NOT imported from app.deploy.document_integrity: that module does ``from app.db import
# ...`` at import time, which connects and reflects the schema. The preflight has to be able to
# classify configured roots on a host whose database is down, and report that as the database failure
# it is rather than as an import error.

def path_anchor(reference, *, relative_base=None):
    """The filesystem anchor a reference depends on: ``'C:'``, or ``'\\\\server\\share'`` for UNC.

    Returns ``None`` for a reference that names no anchor at all. A RELATIVE reference resolves
    against ``relative_base`` (the supervisor's working directory is the project root, which is what
    ``app.deploy.document_integrity._resolve_documents_path`` assumes too), so its anchor is the
    project root's; with no base given a relative reference yields ``None``.
    """
    if not reference:
        return None
    ref = str(reference).strip()
    if not ref:
        return None
    if ref[:7].lower() == "file://":
        ref = unquote(urlsplit(ref).path).lstrip("/")
    ref = ref.replace("/", "\\")

    # Strip the \\?\ and \\?\UNC\ extended-length prefixes before anything else, so an extended-length
    # path is classified by what it actually points at rather than looking like a UNC host named "?".
    if ref[:8].upper() == "\\\\?\\UNC\\":
        ref = "\\\\" + ref[8:]
    elif ref[:4] == "\\\\?\\":
        ref = ref[4:]

    if ref[:2] == "\\\\":
        parts = [p for p in ref[2:].split("\\") if p]
        if len(parts) >= 2:
            return f"\\\\{parts[0]}\\{parts[1]}"
        return None                                  # \\server with no share names no readable root
    if len(ref) >= 2 and ref[1] == ":" and ref[0].isalpha():
        return f"{ref[0].upper()}:"
    return path_anchor(relative_base) if relative_base else None


def is_unc(anchor) -> bool:
    return bool(anchor) and str(anchor).startswith("\\\\")


def _normalise_anchor(anchor) -> str:
    """Case-folded anchor, for comparison only. Windows paths are case-insensitive."""
    return str(anchor).rstrip("\\").casefold() if anchor else ""


# --- the required set -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RequiredRoot:
    """One root the supervisor must be able to read, and why it is required."""

    path: str                 # the root itself: a full configured path, or an anchor for a DB root
    anchor: str               # the volume/share the root depends on
    origin: str               # "configured" | "database"
    detail: str               # human-readable provenance, named in the diagnostics

    @property
    def key(self) -> tuple[str, str]:
        return (_normalise_anchor(self.anchor), str(self.path).rstrip("\\").casefold())


@dataclass(frozen=True)
class DeclaredRoot:
    """A root the source tree knows about, and whether the RUNTIME actually configured it.

    ``configured`` is the distinction that keeps the preflight honest. ``storage_paths`` always
    returns a path, falling back to a legacy literal baked into the source. A legacy default is not
    evidence that an operator chose that location — requiring it would reintroduce the original bug
    in a new costume, failing a host over ``C:\\Client360\\Data\\Documents\\TaxDome`` that nothing uses.
    Only an EFFECTIVELY configured permanent root is required on the strength of configuration alone.
    """

    path: str
    detail: str
    kind: str          # "permanent" (OCR reads it) | "ingestion" (importers read it; OCR never does)
    configured: bool   # the runtime set this, rather than falling back to a source-code default


def declared_roots() -> list[DeclaredRoot]:
    """Every root the configuration layer knows about, split by kind and by whether it is set.

    PERMANENT roots hold the canonical local copies ``documents.storage_uri`` points at — the bytes
    OCR opens. INGESTION roots are where importers READ FROM before writing a canonical copy
    (``TAXDOME_DRIVE_ROOT``, ``DRAKE_EXPORT_ROOT``, the SharePoint staging tree). The supervisor never
    touches an ingestion root, so one is reported for context and required only if eligible database
    rows turn out to depend on it. Conflating the two is what made a per-session ``Z:`` mapping look
    like an OCR prerequisite.
    """
    from app.services.storage_paths import (
        data_root,
        document_root,
        repository_root,
        sharepoint_staging_root,
    )

    # CLIENT360_DATA_ROOT relocates every derived root at once, so setting it configures each of them
    # just as explicitly as setting the per-source variable would.
    base_set = bool(data_root())

    def document(source, env_var):
        return DeclaredRoot(
            path=document_root(source, env_var),
            detail=f"canonical {source} local-copy root ({env_var})",
            kind="permanent", configured=bool(os.getenv(env_var)) or base_set)

    return [
        document("TaxDome", "CLIENT360_TAXDOME_DOCUMENT_ROOT"),
        document("SharePoint", "CLIENT360_SHAREPOINT_DOCUMENT_ROOT"),
        document("Drake", "CLIENT360_DRAKE_DOCUMENT_ROOT"),
        document("Email", "CLIENT360_EMAIL_DOCUMENT_ROOT"),
        DeclaredRoot(path=repository_root(),
                     detail="relocation repository root (CLIENT360_MIGRATION_DEST_ROOT)",
                     kind="permanent",
                     configured=bool(os.getenv("CLIENT360_MIGRATION_DEST_ROOT")) or base_set),
        DeclaredRoot(path=os.getenv("VAULT_STORAGE_ROOT") or "data/vault",
                     detail="vault storage root (VAULT_STORAGE_ROOT)",
                     kind="permanent", configured=bool(os.getenv("VAULT_STORAGE_ROOT"))),
        DeclaredRoot(path=os.getenv("TAXDOME_DRIVE_ROOT") or "Z:\\",
                     detail="TaxDome Drive INGESTION source (TAXDOME_DRIVE_ROOT); importers read "
                            "it, OCR never does",
                     kind="ingestion", configured=bool(os.getenv("TAXDOME_DRIVE_ROOT"))),
        DeclaredRoot(path=(os.getenv("DRAKE_EXPORT_ROOT") or os.getenv("DRAKE_DRIVE_ROOT")
                           or "D:\\DrakeExport"),
                     detail="Drake export INGESTION source (DRAKE_EXPORT_ROOT); importers read it, "
                            "OCR never does",
                     kind="ingestion",
                     configured=bool(os.getenv("DRAKE_EXPORT_ROOT")
                                     or os.getenv("DRAKE_DRIVE_ROOT"))),
        DeclaredRoot(path=sharepoint_staging_root(),
                     detail="SharePoint STAGING tree (CLIENT360_SHAREPOINT_SOURCE_ROOT); staged "
                            "bytes are copied to the canonical root before OCR",
                     kind="ingestion",
                     configured=bool(os.getenv("CLIENT360_SHAREPOINT_SOURCE_ROOT")) or base_set),
    ]


def configured_roots(*, project_root=None, declared=None) -> list[RequiredRoot]:
    """The PERMANENT OCR source roots the runtime has effectively configured.

    An unconfigured permanent root (still sitting on its legacy source-code default) and every
    ingestion root are excluded here: they are reported by :func:`evaluate` instead, and become
    required only through the database half, if eligible rows actually depend on them.
    """
    roots: list[RequiredRoot] = []
    seen: set[tuple[str, str]] = set()
    for entry in (declared_roots() if declared is None else declared):
        if entry.kind != "permanent" or not entry.configured or not entry.path:
            continue
        anchor = path_anchor(entry.path, relative_base=project_root)
        if anchor is None:
            # A relative root names no volume of its own; it lives under the supervisor's working
            # directory, which is a required root in its own right.
            continue
        root = RequiredRoot(path=str(entry.path), anchor=anchor, origin="configured",
                            detail=entry.detail)
        if root.key not in seen:
            seen.add(root.key)
            roots.append(root)
    return roots


#: Rows the supervisor will actually attempt: active, not deleted, not archived, locally stored, and
#: either never OCR'd or in a retryable state. A document whose OCR is terminally complete or
#: terminally unsupported is never read again, so it cannot make a volume required.
SQL_DATABASE_ANCHORS = """
SELECT d.storage_uri AS storage_uri, d.storage_path AS storage_path,
       d.storage_provider AS storage_provider, count(*) AS documents
FROM documents d
LEFT JOIN document_ocr o ON o.document_id = d.id
WHERE d.status = 'active' AND d.deleted_at IS NULL
  AND d.archived = false AND d.archived_at IS NULL
  AND (o.document_id IS NULL
       OR o.status IN ('pending', 'processing', 'failed', 'timed_out'))
GROUP BY d.storage_uri, d.storage_path, d.storage_provider
"""


def database_roots(rows, *, project_root=None) -> list[RequiredRoot]:
    """Collapse document storage references onto the distinct anchors they require.

    ``rows`` is any iterable of mappings with ``storage_uri`` / ``storage_path`` /
    ``storage_provider`` (and optionally ``documents`` for the count shown in diagnostics). Pure, so
    the classification can be tested without a database.
    """
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for row in rows:
        provider = row.get("storage_provider")
        if provider not in LOCAL_PROVIDERS:
            continue                                  # remote-stored: no local root is implied
        anchor = None
        for key in ("storage_uri", "storage_path"):   # same precedence as document_ocr._local_path
            anchor = path_anchor(row.get(key), relative_base=project_root)
            if anchor is not None:
                break
        if anchor is None:
            continue
        norm = _normalise_anchor(anchor)
        display.setdefault(norm, anchor)
        counts[norm] = counts.get(norm, 0) + int(row.get("documents") or 1)

    roots = []
    for norm, count in sorted(counts.items()):
        anchor = display[norm]
        phrase = ("1 outstanding document references this root" if count == 1
                  else f"{count} outstanding documents reference this root")
        roots.append(RequiredRoot(
            path=anchor if is_unc(anchor) else f"{anchor}\\",
            anchor=anchor, origin="database", detail=phrase))
    return roots


def read_database_anchors(engine):
    """Read the storage references of every row the supervisor will attempt. SELECT only.

    Errors PROPAGATE. A failed query must surface as a failed preflight, never as an empty required
    set — silently treating "cannot tell" as "nothing required" is the shape of the original bug.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r) for r in conn.execute(text(SQL_DATABASE_ANCHORS)).mappings()]


def required_roots(*, configured, database) -> list[RequiredRoot]:
    """The union. A root required by EITHER half is required."""
    roots: list[RequiredRoot] = []
    seen: set[tuple[str, str]] = set()
    for root in list(configured) + list(database):
        if root.key not in seen:
            seen.add(root.key)
            roots.append(root)
    return roots


# --- host inventory --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Volume:
    """One entry of the host's volume table, as gathered by the installer via CIM."""

    anchor: str
    drive_type: int = DRIVE_TYPE_UNKNOWN
    provider_name: str | None = None    # non-empty => the letter is a network mapping
    file_system: str | None = None
    session_mapped: bool = False        # present in Win32_MappedLogicalDisk => interactive session


def volumes_from_inventory(payload) -> dict[str, Volume]:
    """Parse the JSON volume table the installer pipes in. Unknown fields are ignored."""
    mapped = {_normalise_anchor(a) for a in (payload.get("mapped_anchors") or [])}
    volumes: dict[str, Volume] = {}
    for entry in payload.get("volumes") or []:
        anchor = entry.get("anchor") or entry.get("DeviceID")
        if not anchor:
            continue
        norm = _normalise_anchor(anchor)
        provider = entry.get("provider_name", entry.get("ProviderName")) or None
        drive_type = entry.get("drive_type", entry.get("DriveType"))
        volumes[norm] = Volume(
            anchor=str(anchor).rstrip("\\"),
            drive_type=DRIVE_TYPE_UNKNOWN if drive_type is None else int(drive_type),
            provider_name=provider,
            file_system=entry.get("file_system", entry.get("FileSystem")) or None,
            session_mapped=bool(entry.get("session_mapped")) or norm in mapped)
    for norm in mapped:                               # a mapping with no logical-disk row still counts
        if norm not in volumes:
            volumes[norm] = Volume(anchor=norm.upper(), drive_type=DRIVE_TYPE_NETWORK,
                                   session_mapped=True)
    return volumes


@dataclass(frozen=True)
class Probe:
    """What the filesystem says about one root path, from this identity."""

    exists: bool = False
    readable: bool = False
    cloud: bool = False
    error: str | None = None


def _is_cloud_path(path, *, environ=None) -> bool:
    """True when the path is inside a cloud-sync tree, by reparse tag or by provider env var."""
    environ = os.environ if environ is None else environ
    normalised = str(path).replace("/", "\\").rstrip("\\").casefold()
    for var in _CLOUD_ROOT_ENV:
        root = (environ.get(var) or "").replace("/", "\\").rstrip("\\").casefold()
        if root and (normalised == root or normalised.startswith(root + "\\")):
            return True
    try:
        tag = getattr(os.stat(path, follow_symlinks=False), "st_reparse_tag", 0)
    except OSError:
        return False
    return bool(tag) and (tag & _CLOUD_REPARSE_MASK) == _CLOUD_REPARSE_TAG


def filesystem_probe(path) -> Probe:
    """Real probe: does the root exist, and can THIS identity actually enumerate it?

    ``os.access`` is not meaningful for directory permissions on Windows, so readability is settled by
    attempting the operation the supervisor needs — opening the directory — rather than by asking.
    """
    try:
        if not os.path.isdir(path):
            return Probe(exists=os.path.exists(path), readable=False,
                         error=None if os.path.exists(path) else "does not exist")
    except OSError as exc:
        return Probe(error=f"{type(exc).__name__}: {exc}")
    cloud = _is_cloud_path(path)
    try:
        with os.scandir(path) as entries:
            next(iter(entries), None)
    except PermissionError as exc:
        return Probe(exists=True, readable=False, cloud=cloud, error=f"permission denied: {exc}")
    except OSError as exc:
        return Probe(exists=True, readable=False, cloud=cloud,
                     error=f"{type(exc).__name__}: {exc}")
    return Probe(exists=True, readable=True, cloud=cloud)


# --- classification ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """The verdict on one required root, with the reason it passed or failed."""

    root: RequiredRoot
    ok: bool
    code: str
    message: str

    def line(self) -> str:
        return (f"{'OK  ' if self.ok else 'FAIL'} {self.root.path}  "
                f"[{self.root.origin}: {self.root.detail}] - {self.message}")


def classify_root(root, *, volumes, probe, attested_unc=()) -> Finding:
    """Decide whether an S4U task can read one required root. Every unknown is a failure."""
    attested = {_normalise_anchor(a) for a in attested_unc}
    norm = _normalise_anchor(root.anchor)

    def fail(code, message):
        return Finding(root=root, ok=False, code=code, message=message)

    if is_unc(root.anchor):
        if norm not in attested:
            return fail("unc_not_attested",
                        f"{root.anchor} is a UNC share and an S4U task carries no network "
                        f"credentials. If the MACHINE account has been granted this share, "
                        f"re-run with -AttestUncRoot '{root.anchor}'.")
        # Attested: the credential question is answered, the filesystem questions are NOT skipped.
        result = probe(root.path)
        if not result.exists:
            return fail("path_missing",
                        f"attested UNC share {root.anchor} is not reachable: "
                        f"{result.error or 'does not exist'}")
        if not result.readable:
            return fail("path_unreadable",
                        f"attested UNC share {root.anchor} exists but cannot be read: "
                        f"{result.error or 'unreadable'}")
        return Finding(root=root, ok=True, code="attested_unc",
                       message=f"{root.anchor} is an operator-attested UNC share, present and readable")

    volume = volumes.get(norm)
    if volume is None:
        return fail("volume_absent",
                    f"{root.anchor} is not present on this host, and a root that is required "
                    f"cannot be skipped")
    if volume.session_mapped or volume.provider_name:
        return fail("mapped_drive",
                    f"{root.anchor} is a mapped drive"
                    + (f" for {volume.provider_name}" if volume.provider_name else "")
                    + "; a mapping belongs to an interactive logon session and does not exist "
                      "under S4U. Documents stored there stay recoverable OCR failures, and this "
                      "is not a regression - it is why the task must not be registered as though "
                      "they were readable")
    if volume.drive_type == DRIVE_TYPE_NETWORK:
        return fail("network_drive",
                    f"{root.anchor} is a network drive (DriveType 4); an S4U task has no network "
                    f"credentials to reach it")
    if volume.drive_type != DRIVE_TYPE_LOCAL_DISK:
        name = _DRIVE_TYPE_NAMES.get(volume.drive_type, "unrecognised")
        return fail("not_local_disk",
                    f"{root.anchor} is DriveType {volume.drive_type} ({name}), not a local fixed "
                    f"disk; an S4U task cannot rely on it")

    result = probe(root.path)
    if result.cloud:
        return fail("cloud_mount",
                    f"{root.path} is inside a cloud-sync tree. It sits on a local disk, but its "
                    f"contents are materialised by a per-user session filter and are unavailable "
                    f"to a session-less S4U task")
    if not result.exists:
        return fail("path_missing",
                    f"{root.path} does not exist on {root.anchor}: {result.error or 'missing'}")
    if not result.readable:
        return fail("path_unreadable",
                    f"{root.path} exists but cannot be read by this identity: "
                    f"{result.error or 'unreadable'}")
    return Finding(root=root, ok=True, code="local_fixed_disk",
                   message=f"{root.anchor} is a local fixed disk and {root.path} is readable")


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    not_required: list[str] = field(default_factory=list)
    reported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "required_root_count": len(self.findings),
            "roots": [{"path": f.root.path, "anchor": f.root.anchor, "origin": f.root.origin,
                       "detail": f.root.detail, "ok": f.ok, "code": f.code, "message": f.message}
                      for f in self.findings],
            "reported_not_required": self.reported,
            "not_required": self.not_required,
        }

    def render(self) -> str:
        lines = [f"Required OCR source roots: {len(self.findings)}"]
        lines += [f"  {f.line()}" for f in self.findings]
        if self.reported:
            lines.append("Reported, NOT required (nothing eligible depends on them):")
            lines += [f"  --   {note}" for note in self.reported]
        for anchor in self.not_required:
            lines.append(f"  --   {anchor} is present but is NOT an OCR source root "
                         f"(no configuration and no outstanding document references it); "
                         f"not required, not checked")
        lines.append("PREFLIGHT PASSED" if self.ok
                     else f"PREFLIGHT FAILED: {len(self.failures)} required root(s) unusable under S4U")
        return "\n".join(lines)


def evaluate(*, configured, database, volumes, probe=filesystem_probe, attested_unc=(),
             declared=()) -> Report:
    """Classify every required root; report the declared roots that nothing eligible depends on."""
    roots = required_roots(configured=configured, database=database)
    findings = [classify_root(r, volumes=volumes, probe=probe, attested_unc=attested_unc)
                for r in roots]
    needed = {_normalise_anchor(r.anchor) for r in roots}

    # A declared root that is not required is stated explicitly rather than dropped, so an operator
    # reading the output can see that an ingestion-only or unconfigured root was considered and
    # deliberately not validated — not that it was forgotten.
    reported = []
    for entry in declared:
        anchor = path_anchor(entry.path)
        if anchor is not None and _normalise_anchor(anchor) in needed:
            continue
        if entry.kind == "ingestion":
            why = ("an ingestion-only source; importers read it, OCR never does, and no eligible "
                   "document depends on it")
        elif not entry.configured:
            why = ("not configured at runtime (this is the legacy source-code default), and no "
                   "eligible document depends on it")
        else:
            continue
        reported.append(f"{entry.path}  [{entry.detail}] - {why}")

    spare = sorted(v.anchor for norm, v in volumes.items()
                   if norm not in needed and v.drive_type == DRIVE_TYPE_LOCAL_DISK)
    return Report(findings=findings, not_required=spare, reported=reported)


# --- CLI -------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    """Read the volume inventory on stdin, print the verdict, exit non-zero on any failure."""
    parser = argparse.ArgumentParser(
        prog="python -m app.deploy.ocr_source_roots",
        description="Validate the OCR source roots an S4U scheduled task must be able to read.")
    parser.add_argument("--project-root", default=os.getcwd(),
                        help="working directory of the task; anchors relative storage references")
    parser.add_argument("--attest-unc", action="append", default=[], metavar="PATH",
                        help="a UNC share the MACHINE account has been granted (repeatable)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    payload = json.loads(sys.stdin.read() or "{}")
    volumes = volumes_from_inventory(payload)

    try:
        declared = declared_roots()
        configured = configured_roots(project_root=args.project_root, declared=declared)
    except Exception as exc:  # noqa: BLE001 — an unresolvable configuration is a failed preflight
        print(f"FAIL cannot resolve configured document roots: {type(exc).__name__}: {exc}")
        return 1
    try:
        from app.db import engine
        rows = read_database_anchors(engine)
    except Exception as exc:  # noqa: BLE001 — never degrade "cannot tell" into "nothing required"
        print(f"FAIL cannot read document storage roots from the database: "
              f"{type(exc).__name__}: {exc}")
        return 1

    report = evaluate(configured=configured,
                      database=database_roots(rows, project_root=args.project_root),
                      volumes=volumes, attested_unc=args.attest_unc, declared=declared)
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
