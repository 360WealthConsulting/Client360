"""HEIC/HEIF end to end over the canonical document model.

Covers the DB-backed half of iPhone-photo support: an upload keeps its ORIGINAL byte for byte, a
normalized JPEG derivative is produced and recorded in ``document_derivatives`` with full provenance,
and the downstream consumers (OCR, and any AI image call) receive the JPEG rather than the HEIC. A file
that cannot be converted is recorded as a truthful failure — never as a silent success.

Fixtures are generated in-memory; derivatives are written to ``tmp_path`` via ``IMAGE_DERIVATIVE_ROOT``
and originals to a ``tmp_path`` document root, so the suite never writes into the repository, the real
document store or the real derivative directory.
"""
from __future__ import annotations

import hashlib
import io
import uuid

import pytest
from sqlalchemy import select

from app.db import documents, engine, people
from app.services import document_derivatives as deriv
from app.services import documents as documents_service
from app.services import image_normalization as norm
from app.services.vault.storage import VaultStorageError

pytest.importorskip("PIL")
pytest.importorskip("pillow_heif")
import pillow_heif  # noqa: E402
from PIL import Image  # noqa: E402

pillow_heif.register_heif_opener()


# --- fixtures ----------------------------------------------------------------

_CREATED: list[int] = []


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Originals and derivatives both land under tmp_path — nothing touches the real stores.

    Teardown soft-deletes the documents this module created. Without it they stay visible to the
    firm-wide OCR/normalization sweeps other test modules run, which would both skew those tests'
    counts and convert a tmp_path image into a derivative under the REAL derivative root (this
    module's ``IMAGE_DERIVATIVE_ROOT`` override is no longer in effect by then)."""
    monkeypatch.setenv("IMAGE_DERIVATIVE_ROOT", str(tmp_path / "derivatives"))
    monkeypatch.setattr(documents_service, "DOCUMENT_ROOT", tmp_path / "documents")
    _CREATED.clear()
    yield
    if _CREATED:
        with engine.begin() as connection:
            connection.execute(documents.update()
                               .where(documents.c.id.in_(tuple(_CREATED)))
                               .values(status="deleted"))
        _CREATED.clear()


def _person() -> int:
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as connection:
        return connection.execute(people.insert().values(
            full_name=f"HEIC Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()


def _heic_bytes(size=(96, 64), colour=(200, 120, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="HEIF")
    return buffer.getvalue()


def _jpeg_bytes(size=(96, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (20, 40, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _upload(data: bytes, name: str, content_type: str, *, verify_content=True) -> int:
    document_id = documents_service.save_person_document(
        person_id=_person(), original_name=name, source=io.BytesIO(data),
        content_type=content_type, verify_content=verify_content)
    _CREATED.append(document_id)
    return document_id


def _row(document_id: int):
    with engine.connect() as connection:
        return connection.execute(
            select(documents).where(documents.c.id == document_id)).mappings().one()


# --- acceptance + original preservation --------------------------------------

def test_heic_upload_is_accepted_and_the_original_is_preserved_exactly():
    data = _heic_bytes()
    document_id = _upload(data, "IMG_5178.HEIC", "image/heic")
    row = _row(document_id)

    assert row["original_name"] == "IMG_5178.HEIC"           # filename preserved verbatim
    assert row["content_type"] == "image/heic"               # original MIME preserved
    assert row["sha256"] == hashlib.sha256(data).hexdigest()  # original hash preserved
    assert row["size_bytes"] == len(data)
    stored = deriv.original_path(row)
    assert stored is not None and stored.read_bytes() == data   # byte-identical on disk


def test_heif_extension_and_lowercase_are_accepted():
    for name, content_type in (("photo.heif", "image/heif"), ("photo.heic", "image/heic"),
                               ("PHOTO.HEIF", "image/heif")):
        assert _upload(_heic_bytes(), name, content_type) > 0


def test_extension_spoofing_is_rejected_and_stores_nothing():
    with pytest.raises(VaultStorageError):
        _upload(_jpeg_bytes(), "actually_a_jpeg.heic", "image/heic")
    with pytest.raises(VaultStorageError):
        _upload(b"MZ\x90\x00 not an image at all", "malware.heic", "image/heic")
    # And the reverse: HEIC bytes wearing a .jpg name.
    with pytest.raises(VaultStorageError):
        _upload(_heic_bytes(), "actually_heic.jpg", "image/jpeg")


def test_existing_jpeg_and_png_uploads_are_unchanged():
    jpeg_id = _upload(_jpeg_bytes(), "scan.jpg", "image/jpeg")
    assert _row(jpeg_id)["content_type"] == "image/jpeg"

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(buffer, format="PNG")
    png_id = _upload(buffer.getvalue(), "logo.png", "image/png")
    assert _row(png_id)["content_type"] == "image/png"

    # Neither gets a derivative: they are already downstream-safe, so nothing is converted and no
    # derivative row is created for the (large) non-HEIF population.
    for document_id in (jpeg_id, png_id):
        source = deriv.image_source_for_document(document_id)
        assert source.is_derivative is False
        assert deriv.derivative_state(document_id) is None
        # Asked explicitly (e.g. by a pipeline sweep), the answer is a truthful 'skipped'.
        assert deriv.ensure_normalized_image(document_id)["status"] == deriv.SKIPPED


# --- provenance --------------------------------------------------------------

def test_upload_queues_normalization_as_pending():
    document_id = _upload(_heic_bytes(), "IMG_0001.HEIC", "image/heic")
    state = deriv.derivative_state(document_id)
    assert state["status"] == deriv.PENDING
    assert state["kind"] == deriv.KIND_NORMALIZED_IMAGE
    assert state["source_mime"] == "image/heic"
    assert state["derivative_path"] is None                  # nothing decoded on the upload request
    assert _row(document_id)["preview_status"] == deriv.PENDING


def test_completed_conversion_records_full_provenance():
    data = _heic_bytes(size=(120, 90))
    document_id = _upload(data, "IMG_0002.HEIC", "image/heic")
    state = deriv.ensure_normalized_image(document_id)

    assert state["status"] == deriv.COMPLETED
    assert state["source_mime"] == "image/heic"
    assert state["source_hash"] == hashlib.sha256(data).hexdigest()
    assert state["derivative_mime"] == "image/jpeg"
    assert state["derivative_hash"] != state["source_hash"]  # a genuinely different artifact
    assert state["derivative_size_bytes"] > 0
    assert (state["width"], state["height"]) == (120, 90)
    assert state["engine"] == "pillow+pillow-heif"
    assert state["converted_at"] is not None
    assert state["last_error"] is None

    derivative = deriv.Path(state["derivative_path"])
    assert derivative.exists()
    assert hashlib.sha256(derivative.read_bytes()).hexdigest() == state["derivative_hash"]
    # The status is mirrored onto the existing documents column.
    assert _row(document_id)["preview_status"] == deriv.COMPLETED


def test_conversion_never_mutates_the_original():
    data = _heic_bytes()
    document_id = _upload(data, "IMG_0003.HEIC", "image/heic")
    row = _row(document_id)
    stored = deriv.original_path(row)

    deriv.ensure_normalized_image(document_id)
    deriv.ensure_normalized_image(document_id, force=True)

    after = _row(document_id)
    assert stored.read_bytes() == data                       # bytes untouched
    assert after["sha256"] == row["sha256"]                  # hash untouched
    assert after["content_type"] == "image/heic"             # MIME untouched
    assert after["original_name"] == row["original_name"]    # identity untouched
    assert after["storage_path"] == row["storage_path"]      # source path untouched


def test_conversion_is_idempotent():
    document_id = _upload(_heic_bytes(), "IMG_0004.HEIC", "image/heic")
    first = deriv.ensure_normalized_image(document_id)
    second = deriv.ensure_normalized_image(document_id)
    assert second["derivative_path"] == first["derivative_path"]
    assert second["derivative_hash"] == first["derivative_hash"]
    assert second["attempts"] == first["attempts"]           # no re-conversion, no attempt burned


# --- downstream consumers ----------------------------------------------------

def test_ai_image_consumer_receives_the_jpeg_derivative_not_the_heic():
    """The seam every OpenAI / Advisor AI / 360Plus AI image call goes through."""
    data = _heic_bytes()
    document_id = _upload(data, "IMG_5178.HEIC", "image/heic")

    source = deriv.ai_image_source(document_id)
    assert source.is_derivative is True
    assert source.mime == "image/jpeg"
    assert source.original_mime == "image/heic"
    assert source.original_name == "IMG_5178.HEIC"

    attached = source.path.read_bytes()
    assert attached[:3] == b"\xff\xd8\xff"                   # what the model actually receives
    assert norm.sniff_image_type(attached[:64]) == "jpeg"
    assert attached != data
    assert len(attached) <= norm.AI_IMAGE_MAX_BYTES
    Image.open(io.BytesIO(attached)).verify()


def test_ai_image_consumer_gets_an_already_safe_original_untouched():
    data = _jpeg_bytes()
    document_id = _upload(data, "scan.jpg", "image/jpeg")
    source = deriv.ai_image_source(document_id)
    assert source.is_derivative is False
    assert source.path.read_bytes() == data                  # no needless re-encode


def test_ocr_reads_the_derivative_for_heic_and_the_original_for_everything_else():
    from app.services.document_ocr import _ocr_source_path

    heic_id = _upload(_heic_bytes(), "IMG_0005.HEIC", "image/heic")
    heic_row = _row(heic_id)
    original = deriv.original_path(heic_row)
    resolved = _ocr_source_path(heic_row, original)
    assert resolved != original
    assert resolved.read_bytes()[:3] == b"\xff\xd8\xff"      # Tesseract gets a JPEG

    jpeg_id = _upload(_jpeg_bytes(), "scan.jpg", "image/jpeg")
    jpeg_row = _row(jpeg_id)
    jpeg_original = deriv.original_path(jpeg_row)
    assert _ocr_source_path(jpeg_row, jpeg_original) == jpeg_original   # unchanged flow


def test_browser_preview_uses_the_same_single_conversion_implementation(tmp_path):
    from app.routes.documents import convert_image_to_jpeg

    source = tmp_path / "preview.heic"
    source.write_bytes(_heic_bytes())
    jpeg = convert_image_to_jpeg(source)
    assert jpeg is not None and jpeg[:3] == b"\xff\xd8\xff"
    assert convert_image_to_jpeg(tmp_path / "missing.heic") is None      # still fails safely


# --- failure handling --------------------------------------------------------

def _corrupt_heic_document() -> int:
    truncated = _heic_bytes()[:200]                          # valid ftyp header, unusable payload
    # Accepted at upload: the header IS a genuine HEIC. The damage is only found at decode time.
    return _upload(truncated, "broken.HEIC", "image/heic")


def test_corrupt_heic_is_recorded_unsupported_and_the_original_is_kept():
    document_id = _corrupt_heic_document()
    row = _row(document_id)
    stored_before = deriv.original_path(row).read_bytes()

    state = deriv.ensure_normalized_image(document_id)
    assert state["status"] == deriv.UNSUPPORTED
    assert state["derivative_path"] is None
    assert "broken.HEIC" in state["last_error"]              # actionable, names the file
    assert "could not be decoded" in state["last_error"]
    assert deriv.original_path(_row(document_id)).read_bytes() == stored_before
    assert _row(document_id)["preview_status"] == deriv.UNSUPPORTED


def test_a_failed_conversion_never_hands_a_consumer_the_heic():
    document_id = _corrupt_heic_document()
    with pytest.raises(deriv.DerivativeUnavailable) as excinfo:
        deriv.ai_image_source(document_id)
    assert excinfo.value.document_id == document_id
    assert excinfo.value.retryable is False                  # terminal: the file itself is the problem


def test_best_effort_consumer_can_fall_back_to_the_original():
    document_id = _corrupt_heic_document()
    source = deriv.image_source_for_document(document_id, require_normalized=False)
    assert source.is_derivative is False                     # the preview page has its own fallback
    assert source.mime == "image/heic"


def test_missing_stored_file_is_recorded_as_a_retryable_failure():
    document_id = _upload(_heic_bytes(), "IMG_0006.HEIC", "image/heic")
    deriv.original_path(_row(document_id)).unlink()
    state = deriv.ensure_normalized_image(document_id)
    assert state["status"] == deriv.FAILED
    assert "could not be found" in state["last_error"]
    assert state["attempts"] >= 1                            # a retryable failure counts an attempt


def test_ocr_records_an_honest_failure_rather_than_success():
    """A HEIC with no usable image must never be reported as OCR'd."""
    from app.services import document_ocr

    document_id = _corrupt_heic_document()
    summary = document_ocr.run_ocr(document_ids=[document_id],
                                   extractor=lambda row, path: "TEXT THAT MUST NOT BE RECORDED",
                                   isolate=False)
    assert summary["completed"] == 0
    assert summary["unsupported"] == 1
    with engine.connect() as connection:
        from app.db import document_ocr as ocr_table
        state = connection.execute(select(ocr_table).where(
            ocr_table.c.document_id == document_id)).mappings().one()
    assert state["status"] == "unsupported"
    assert not state["text"]
    assert "broken.HEIC" in state["last_error"]


# --- test-suite safety -------------------------------------------------------

def test_this_module_never_writes_into_the_repository(tmp_path):
    """Originals and derivatives are both redirected into tmp_path by the autouse fixture, so a run
    of this file cannot touch the repository's ``documents/`` tree or the real derivative store."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    assert tmp_path in documents_service.DOCUMENT_ROOT.parents
    assert tmp_path in norm.derivative_root().parents or norm.derivative_root() == tmp_path / "derivatives"
    assert repo not in norm.derivative_root().parents

    document_id = _upload(_heic_bytes(), "IMG_9999.HEIC", "image/heic")
    state = deriv.ensure_normalized_image(document_id)
    for path in (deriv.original_path(_row(document_id)), deriv.Path(state["derivative_path"])):
        assert tmp_path in path.parents
        assert repo not in path.parents


# --- lifecycle: deletion -----------------------------------------------------

def test_hard_deleting_a_document_removes_its_derivative_row():
    """The FK is ON DELETE CASCADE, so a permanently deleted document leaves no derivative row."""
    from sqlalchemy import text

    document_id = _upload(_heic_bytes(), "IMG_DEL.HEIC", "image/heic")
    deriv.ensure_normalized_image(document_id)
    assert deriv.derivative_state(document_id) is not None

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM documents WHERE id = :i"), {"i": document_id})
    _CREATED.remove(document_id)                             # already gone; nothing to soft-delete

    with engine.connect() as connection:
        remaining = connection.execute(
            text("SELECT count(*) FROM document_derivatives WHERE document_id = :i"),
            {"i": document_id}).scalar_one()
    assert remaining == 0


def test_the_only_hard_delete_path_treats_a_derivative_as_a_blocking_dependent():
    """``taxdome_drive`` is the one place a documents row is physically deleted, and it refuses any
    row that still has dependents. Its dependency list is read from the LIVE FK catalog, so the new
    table is covered automatically — asserted here so a future hardcoded list cannot regress it."""
    from app.importers import taxdome_drive

    document_id = _upload(_heic_bytes(), "IMG_DEP.HEIC", "image/heic")
    deriv.ensure_normalized_image(document_id)
    taxdome_drive._DOCUMENT_REFERENCES = None                # ignore any cross-test cache
    with engine.connect() as connection:
        dependents = taxdome_drive._dependent_tables(connection, document_id)
    taxdome_drive._DOCUMENT_REFERENCES = None
    assert "document_derivatives.document_id" in dependents  # => the row is NOT deletable


# --- lifecycle: orphaned derivative files ------------------------------------

def _unclaimed_digest() -> str:
    """A digest no document in the (shared, accumulating) test database can hold.

    Repeated-character digests like ``"a" * 64`` are not safe here: other suites insert documents
    with hand-written sha256 values, and a collision would make the sweep correctly KEEP the file
    and this test wrongly fail."""
    return uuid.uuid4().hex + uuid.uuid4().hex


def _orphan_file(root, digest, *, age_days=30):
    import os
    import time

    path = root / digest[:2] / f"{digest}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff not-really-a-jpeg")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_orphan_sweep_reports_before_it_deletes_anything(tmp_path):
    root = tmp_path / "store"
    orphan = _orphan_file(root, _unclaimed_digest())
    result = deriv.prune_orphan_derivatives(root=root)       # dry run is the default
    assert result["dry_run"] is True
    assert result["orphans"] == 1 and result["deleted"] == 0
    assert orphan.exists()


def test_orphan_sweep_deletes_only_unclaimed_derivatives(tmp_path):
    root = tmp_path / "store"
    orphan = _orphan_file(root, _unclaimed_digest())
    result = deriv.prune_orphan_derivatives(root=root, dry_run=False)
    assert result["deleted"] == 1 and result["reclaimed_bytes"] > 0
    assert not orphan.exists()


def test_orphan_sweep_keeps_a_derivative_another_document_still_claims(tmp_path):
    """Content addressing means one file serves every document with identical bytes. A live
    ``documents.sha256`` keeps the file even when no derivative row points at it."""
    root = tmp_path / "store"
    data = _heic_bytes()
    document_id = _upload(data, "IMG_SHARED.HEIC", "image/heic")
    shared = _orphan_file(root, _row(document_id)["sha256"])
    result = deriv.prune_orphan_derivatives(root=root, dry_run=False)
    assert result["kept"] >= 1 and result["deleted"] == 0
    assert shared.exists()


def test_orphan_sweep_never_touches_a_recent_file(tmp_path):
    root = tmp_path / "store"
    fresh = _orphan_file(root, _unclaimed_digest(), age_days=0)         # a conversion could be in flight
    result = deriv.prune_orphan_derivatives(root=root, dry_run=False)
    assert result["deleted"] == 0 and fresh.exists()


def test_orphan_sweep_can_never_select_an_original_document(tmp_path):
    """Only ``<64 hex>.jpg`` — the one shape this module writes — is ever considered. An uploaded
    original (``<uuid>.<ext>``, and in a different root entirely) cannot match."""
    root = tmp_path / "store"
    root.mkdir(parents=True, exist_ok=True)
    originals = [root / "3f2a9c1e4b6d8a0f2c4e6a8b0d2f4a6c.jpg",   # a stored_name-shaped file
                 root / "IMG_5178.HEIC",
                 root / "tax-return-2024.pdf",
                 root / (("d" * 63) + ".jpg"),                    # 63 hex — not a digest
                 root / (("D" * 64) + ".jpg")]                    # uppercase — not our shape
    for path in originals:
        path.write_bytes(b"ORIGINAL CONTENT")
    import os
    import time
    stamp = time.time() - 365 * 86400                             # ancient: age is not the guard
    for path in originals:
        os.utime(path, (stamp, stamp))

    result = deriv.prune_orphan_derivatives(root=root, dry_run=False)
    assert result["deleted"] == 0
    for path in originals:
        assert path.read_bytes() == b"ORIGINAL CONTENT", path.name


def test_orphan_sweep_stays_inside_the_derivative_root(tmp_path):
    """A symlinked entry that resolves outside the root is skipped, never followed and deleted."""
    root = tmp_path / "store"
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    digest = _unclaimed_digest()
    real = outside / f"{digest}.jpg"
    real.write_bytes(b"NOT OURS")
    link = root / f"{digest}.jpg"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):                   # pragma: no cover — no symlink support
        pytest.skip("symlinks unavailable on this host")
    import os
    import time
    stamp = time.time() - 365 * 86400
    os.utime(real, (stamp, stamp))

    result = deriv.prune_orphan_derivatives(root=root, dry_run=False)
    assert result["deleted"] == 0
    assert any("outside the derivative root" in s for s in result["skipped"])
    assert real.read_bytes() == b"NOT OURS"
