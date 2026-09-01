"""HEIC/HEIF acceptance + the shared image-normalization engine.

Covers the pure, DB-free half of iPhone-photo support: content-based type detection (including the
explicit refusal of multi-frame HEIF sequences), the upload-acceptance content check, and the
HEIC -> JPEG conversion itself (EXIF orientation, RGB, downscale-only, size budget, safe failure).

Every fixture is GENERATED in-memory / in tmp_path — no client photographs, no committed binaries —
and every derivative is written to a tmp_path root, so the suite never touches the repository, the
document store or the real derivative directory. Conversion tests need Pillow + pillow-heif (pinned in
requirements.txt); they skip cleanly on a host without them.
"""
from __future__ import annotations

import hashlib
import io

import pytest

from app.services import image_normalization as norm
from app.services.vault.storage import ALLOWED_EXTENSIONS, content_matches_extension

pytest.importorskip("PIL")
pytest.importorskip("pillow_heif")
import pillow_heif  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

pillow_heif.register_heif_opener()


# --- fixtures ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def _derivative_root(tmp_path, monkeypatch):
    """Every derivative in this module lands in tmp_path — never in data/derivatives."""
    root = tmp_path / "derivatives"
    monkeypatch.setenv("IMAGE_DERIVATIVE_ROOT", str(root))
    return root


def _heic(tmp_path, name="IMG_0001.HEIC", size=(64, 48), colour=(180, 90, 40), mode="RGB"):
    image = Image.new(mode, size, colour if mode == "RGB" else 128)
    path = tmp_path / name
    image.save(path, format="HEIF")
    return path


def _jpeg(tmp_path, name="scan.jpg", size=(64, 48)):
    path = tmp_path / name
    Image.new("RGB", size, (10, 20, 30)).save(path, format="JPEG")
    return path


def _sequence_header(tmp_path):
    """A HEIF header declaring ONLY image-SEQUENCE brands (``image/heic-sequence``).

    Built by rewriting a real file's ``ftyp`` brands rather than committing a multi-frame binary."""
    raw = bytearray(_heic(tmp_path, "seq.heic").read_bytes())
    raw[8:12] = b"msf1"                       # major brand: image sequence
    raw[16:28] = b"msf1hevciso8"              # compatible brands: no still brand remains
    return bytes(raw)


# --- content-based type detection -------------------------------------------

def test_sniff_recognizes_every_supported_image_by_content(tmp_path):
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    for fmt, expected in (("HEIF", "heif"), ("JPEG", "jpeg"), ("PNG", "png"),
                          ("GIF", "gif"), ("TIFF", "tiff"), ("BMP", "bmp"), ("WEBP", "webp")):
        path = tmp_path / f"x.{fmt.lower()}"
        image.save(path, format=fmt)
        assert norm.sniff_image_type(path.read_bytes()[:64]) == expected, fmt


def test_sniff_rejects_non_images():
    assert norm.sniff_image_type(b"this is not an image") is None
    assert norm.sniff_image_type(b"") is None
    assert norm.sniff_image_type(b"%PDF-1.4 hello") is None


def test_multi_frame_heif_sequence_is_detected_and_refused(tmp_path):
    header = _sequence_header(tmp_path)
    assert norm.is_heif_header(header) is True
    assert norm.is_heif_sequence_header(header) is True
    assert norm.sniff_image_type(header) == "heif-sequence"
    # A sequence is NOT accepted as an uploadable HEIC still.
    assert norm.content_matches_heif(header) is False


def test_still_heic_is_not_mistaken_for_a_sequence(tmp_path):
    header = _heic(tmp_path).read_bytes()[:64]
    assert norm.is_heif_sequence_header(header) is False
    assert norm.content_matches_heif(header) is True


def test_mime_and_extension_detection_is_case_insensitive():
    assert norm.is_heif_name("IMG_5178.HEIC") and norm.is_heif_name("photo.heif")
    assert not norm.is_heif_name("scan.jpg") and not norm.is_heif_name("noextension")
    assert norm.is_heif_content_type("image/heic") and norm.is_heif_content_type("IMAGE/HEIF")
    assert norm.is_heif_content_type("image/heic; charset=binary")
    # Sequence MIME types are recognized but are never treated as a normalizable still.
    for sequence_type in norm.HEIF_SEQUENCE_MIME_TYPES:
        assert not norm.is_heif_content_type(sequence_type)
        assert not norm.needs_normalization(content_type=sequence_type)


def test_needs_normalization_only_for_heif():
    assert norm.needs_normalization(filename="IMG_0001.HEIC")
    assert norm.needs_normalization(filename="x", content_type="image/heic")
    for name in ("scan.jpg", "scan.jpeg", "logo.png", "anim.gif", "fax.tiff", "form.pdf"):
        assert not norm.needs_normalization(filename=name), name


# --- upload acceptance -------------------------------------------------------

def test_heic_and_heif_are_accepted_extensions():
    assert {"heic", "heif"} <= ALLOWED_EXTENSIONS
    # The pre-existing allow-list is intact — no accepted type was dropped.
    assert {"pdf", "docx", "xlsx", "csv", "jpg", "jpeg", "png", "txt"} <= ALLOWED_EXTENSIONS


def test_acceptance_uses_content_not_extension(tmp_path):
    heic_header = _heic(tmp_path).read_bytes()[:1024]
    jpeg_header = _jpeg(tmp_path).read_bytes()[:1024]
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    assert content_matches_extension("heic", heic_header) is True
    assert content_matches_extension("heif", heic_header) is True
    # Extension spoofing in both directions is rejected.
    assert content_matches_extension("heic", jpeg_header) is False
    assert content_matches_extension("heic", png_header) is False
    assert content_matches_extension("heic", b"MZ\x90\x00 executable") is False
    assert content_matches_extension("jpg", heic_header) is False
    assert content_matches_extension("png", heic_header) is False
    # A multi-frame sequence renamed .heic is refused at acceptance.
    assert content_matches_extension("heic", _sequence_header(tmp_path)) is False


def test_existing_type_acceptance_is_unchanged(tmp_path):
    assert content_matches_extension("jpg", _jpeg(tmp_path).read_bytes()[:1024]) is True
    assert content_matches_extension("png", b"\x89PNG\r\n\x1a\n rest") is True
    assert content_matches_extension("pdf", b"%PDF-1.7 ...") is True
    assert content_matches_extension("docx", b"PK\x03\x04 ...") is True
    assert content_matches_extension("txt", b"anything at all") is True
    assert content_matches_extension("pdf", b"<html>nope</html>") is False


# --- conversion --------------------------------------------------------------

def test_heic_converts_to_jpeg(tmp_path):
    result = norm.normalize_image_file(_heic(tmp_path))
    assert result.mime == "image/jpeg"
    data = result.path.read_bytes()
    assert data[:3] == b"\xff\xd8\xff"                       # JPEG magic
    assert norm.sniff_image_type(data[:64]) == "jpeg"        # and NOT heif
    Image.open(io.BytesIO(data)).verify()


def test_uppercase_heic_extension_is_handled(tmp_path):
    source = _heic(tmp_path, name="IMG_5178.HEIC")
    assert norm.normalize_image_file(source).mime == "image/jpeg"


def test_original_remains_byte_identical(tmp_path):
    source = _heic(tmp_path)
    before = source.read_bytes()
    norm.normalize_image_file(source)
    norm.normalize_image_file(source, force=True)
    assert source.read_bytes() == before
    assert source.suffix == ".HEIC"                          # not renamed either


def test_derivative_hash_differs_from_original(tmp_path):
    source = _heic(tmp_path)
    result = norm.normalize_image_file(source)
    assert result.source_sha256 == norm.sha256_file(source)
    assert result.sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert result.sha256 != result.source_sha256


def test_conversion_is_idempotent_and_content_addressed(tmp_path):
    source = _heic(tmp_path)
    first = norm.normalize_image_file(source)
    second = norm.normalize_image_file(source)
    assert second.reused is True and second.path == first.path
    assert first.path.name.startswith(first.source_sha256)


def test_exif_orientation_is_honored(tmp_path):
    """A camera stores the sensor image landscape and records "rotate 90°" as an EXIF tag; the
    derivative must come out upright, or OCR and the browser both see it sideways.

    The fixture is a JPEG because pillow-heif's ENCODER applies the rotation at save time and resets
    the tag, so a generated .heic cannot carry a live orientation flag. The decode path under test is
    the same one a real iPhone HEIC takes: ``ImageOps.exif_transpose`` on whatever Pillow opened."""
    image = Image.new("RGB", (80, 40), (200, 30, 30))
    exif = image.getexif()
    exif[274] = 6                                            # Orientation: rotate 90° CW
    source = tmp_path / "rotated.jpg"
    image.save(source, format="JPEG", exif=exif.tobytes())

    with Image.open(source) as stored:
        assert stored.size == (80, 40)                       # stored landscape...
        assert stored.getexif().get(274) == 6                # ...with the rotation flag
    with Image.open(io.BytesIO(norm.render_jpeg_bytes(source))) as out:
        assert out.size == (40, 80)                          # ...and normalized upright
        assert out.getexif().get(274) in (None, 1)           # the flag is spent, not re-applied


def test_heic_orientation_survives_the_round_trip(tmp_path):
    """A rotation-tagged HEIC comes out of normalization with the SAME visual orientation it had when
    opened — the derivative is never rotated a second time."""
    image = Image.new("RGB", (80, 40), (200, 30, 30))
    exif = image.getexif()
    exif[274] = 6
    source = tmp_path / "rotated.heic"
    image.save(source, format="HEIF", exif=exif.tobytes())

    with Image.open(source) as stored:
        expected = ImageOps.exif_transpose(stored).size
    result = norm.normalize_image_file(source)
    assert (result.width, result.height) == expected


def test_non_rgb_modes_are_converted_to_rgb(tmp_path):
    for mode in ("L", "RGBA", "P"):
        source = tmp_path / f"{mode}.png"
        Image.new(mode, (32, 24)).save(source, format="PNG")
        data = norm.render_jpeg_bytes(source)
        with Image.open(io.BytesIO(data)) as out:
            assert out.mode == "RGB", mode


def test_large_image_is_downscaled_and_small_image_is_not_enlarged(tmp_path):
    big = tmp_path / "big.heic"
    Image.new("RGB", (norm.MAX_DERIVATIVE_PX + 900, norm.MAX_DERIVATIVE_PX + 300),
              (10, 20, 30)).save(big, format="HEIF")
    result = norm.normalize_image_file(big)
    assert max(result.width, result.height) == norm.MAX_DERIVATIVE_PX

    small = _heic(tmp_path, name="small.heic", size=(64, 48))
    assert (norm.normalize_image_file(small).width,
            norm.normalize_image_file(small).height) == (64, 48)     # never upscaled


def test_derivative_stays_under_the_ai_image_input_limit(tmp_path):
    assert norm.MAX_DERIVATIVE_BYTES <= norm.AI_IMAGE_MAX_BYTES
    noisy = tmp_path / "noisy.heic"
    # Photographic noise is the worst case for JPEG: it does not compress away.
    import random
    random.seed(7)
    image = Image.new("RGB", (3000, 2400))
    image.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                   for _ in range(3000 * 2400)])
    image.save(noisy, format="HEIF")
    result = norm.normalize_image_file(noisy)
    assert result.size_bytes <= norm.MAX_DERIVATIVE_BYTES < norm.AI_IMAGE_MAX_BYTES


# --- failure handling --------------------------------------------------------

def test_corrupt_heic_fails_safely_and_leaves_the_source(tmp_path):
    source = tmp_path / "corrupt.heic"
    truncated = _heic(tmp_path, name="whole.heic").read_bytes()[:200]
    source.write_bytes(truncated)
    with pytest.raises(norm.UnsupportedImageError):
        norm.normalize_image_file(source)
    assert source.read_bytes() == truncated                  # untouched


def test_not_an_image_at_all_is_refused(tmp_path):
    source = tmp_path / "notimage.heic"
    source.write_bytes(b"this is not an image")
    with pytest.raises(norm.UnsupportedImageError):
        norm.normalize_image_file(source)


def test_empty_file_is_refused(tmp_path):
    source = tmp_path / "empty.heic"
    source.write_bytes(b"")
    with pytest.raises(norm.UnsupportedImageError):
        norm.normalize_image_file(source)


def test_multi_frame_sequence_is_refused_by_the_converter(tmp_path):
    source = tmp_path / "sequence.heic"
    source.write_bytes(_sequence_header(tmp_path))
    with pytest.raises(norm.UnsupportedImageError) as excinfo:
        norm.normalize_image_file(source)
    assert "sequence" in str(excinfo.value).lower()


def test_oversized_source_file_is_refused_before_decoding(tmp_path, monkeypatch):
    source = _heic(tmp_path)
    monkeypatch.setattr(norm, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(norm.UnsupportedImageError) as excinfo:
        norm.render_jpeg_bytes(source)
    assert "limit" in str(excinfo.value)


def test_image_bomb_is_refused_before_pixels_are_decoded(tmp_path, monkeypatch):
    source = _heic(tmp_path, size=(64, 48))                  # 3072 pixels
    monkeypatch.setattr(norm, "MAX_SOURCE_PIXELS", 100)
    with pytest.raises(norm.UnsupportedImageError) as excinfo:
        norm.render_jpeg_bytes(source)
    assert "MP decode limit" in str(excinfo.value)


def test_errors_never_leak_image_contents(tmp_path):
    source = tmp_path / "secret.heic"
    source.write_bytes(b"SENSITIVE-PAYLOAD-" + b"\x00" * 64)
    with pytest.raises(norm.UnsupportedImageError) as excinfo:
        norm.normalize_image_file(source)
    assert "SENSITIVE-PAYLOAD" not in str(excinfo.value)
    assert "secret.heic" in str(excinfo.value)               # the NAME is fine; the bytes are not


# --- bounded paths (Windows MAX_PATH) ----------------------------------------

def test_derivative_path_is_short_fixed_length_and_ignores_the_original_filename(tmp_path):
    long_name = ("a" * 200) + ".HEIC"
    source = tmp_path / long_name
    Image.new("RGB", (32, 24), (5, 5, 5)).save(source, format="HEIF")
    result = norm.normalize_image_file(source)
    assert long_name not in str(result.path)                 # nothing from the upload reaches the path
    assert result.path.name == f"{result.source_sha256}.jpg"
    assert len(result.path.name) == 68


def test_derivative_root_over_the_windows_bound_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_DERIVATIVE_ROOT", str(tmp_path / ("d" * 200)))
    with pytest.raises(norm.ImageNormalizationError) as excinfo:
        norm.derivative_path_for("a" * 64)
    assert str(norm.MAX_PATH_CHARS) in str(excinfo.value)


def test_derivative_path_rejects_a_non_digest_key():
    for bad in ("", "../escape", "A" * 64, "abc"):
        with pytest.raises(ValueError):
            norm.derivative_path_for(bad)


def test_no_temporary_files_are_left_behind(tmp_path, _derivative_root):
    norm.normalize_image_file(_heic(tmp_path))
    assert [p.name for p in _derivative_root.rglob("*.tmp")] == []


def test_conversion_writes_only_under_the_derivative_root(tmp_path, _derivative_root):
    source_dir = tmp_path / "store"
    source_dir.mkdir()
    source = _heic(source_dir)
    before = sorted(p.name for p in source_dir.iterdir())
    result = norm.normalize_image_file(source)
    assert sorted(p.name for p in source_dir.iterdir()) == before      # source dir untouched
    assert _derivative_root in result.path.parents


# --- host preflight ----------------------------------------------------------

def test_preflight_reports_a_working_host(tmp_path):
    """The check a deploy runs on Windows Server to confirm the venv can actually do this."""
    report = norm.preflight()
    assert report["ok"] is True, report["errors"]
    assert report["libraries"]["PIL"] and report["libraries"]["pillow_heif"]
    assert report["writable"] is True and report["round_trip"] is True
    assert report["errors"] == []
    assert not list(tmp_path.glob("**/.preflight-*"))        # leaves no marker behind


def test_preflight_reports_an_unwritable_derivative_root(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_bytes(b"x")
    monkeypatch.setenv("IMAGE_DERIVATIVE_ROOT", str(blocker / "under-a-file"))
    report = norm.preflight()
    assert report["ok"] is False and report["writable"] is False
    assert any("derivative root" in error for error in report["errors"])


# --- configuration -----------------------------------------------------------

def test_derivative_root_is_read_from_the_environment_at_call_time(tmp_path, monkeypatch):
    """Configured, never captured at import — the pattern vault.storage and app.config both use."""
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, str(first))
    assert norm.derivative_root() == first.resolve()
    monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, str(second))
    assert norm.derivative_root() == second.resolve()


def test_development_default_is_the_gitignored_data_directory(monkeypatch):
    monkeypatch.delenv(norm.DERIVATIVE_ROOT_ENV, raising=False)
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    assert norm.DEFAULT_DERIVATIVE_ROOT == "data/derivatives"     # beside data/vault; /data/ ignored


def test_production_refuses_an_unset_derivative_root(monkeypatch):
    """Fails closed rather than resolve the relative default against the service working directory,
    which is how generated derivatives could land inside a deployed source tree."""
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.delenv(norm.DERIVATIVE_ROOT_ENV, raising=False)
    with pytest.raises(norm.ImageNormalizationError) as excinfo:
        norm.derivative_root()
    assert norm.DERIVATIVE_ROOT_ENV in str(excinfo.value)


def test_production_refuses_a_relative_derivative_root(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, "data/derivatives")
    with pytest.raises(norm.ImageNormalizationError) as excinfo:
        norm.derivative_root()
    assert "ABSOLUTE" in str(excinfo.value)


def test_production_accepts_an_absolute_derivative_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, str(tmp_path / "prod"))
    assert norm.derivative_root() == (tmp_path / "prod").resolve()


def test_production_never_writes_derivatives_into_the_repository(monkeypatch):
    """The only way a derivative could land in the source tree is the working-directory-relative
    default. Production refuses every relative form, so no repository-relative path is ever
    constructed and no directory under the checkout is ever created."""
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    for value in ("", "data/derivatives", "./derivatives", "app/static", "../outside"):
        monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, value)
        with pytest.raises(norm.ImageNormalizationError):
            norm.derivative_root()


def test_deploy_preflight_reports_the_derivative_store(tmp_path, monkeypatch):
    """`python -m app.deploy check-config` — step 1 of the Windows deploy — validates it."""
    from app.deploy import checks, config_check

    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "development")
    monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, str(tmp_path / "deriv"))
    ok, detail = checks.image_derivative_storage_writable()
    assert ok is True and detail == str((tmp_path / "deriv").resolve())
    assert "IMAGE_DERIVATIVE_ROOT" in checks.RECOMMENDED
    assert config_check.validate_config()["checks"]["image_derivative_storage"] == "ok"


def test_deploy_preflight_fails_production_without_a_derivative_root(monkeypatch):
    from app.deploy import checks

    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    monkeypatch.delenv(norm.DERIVATIVE_ROOT_ENV, raising=False)
    ok, detail = checks.image_derivative_storage_writable()
    assert ok is False and "IMAGE_DERIVATIVE_ROOT" in detail


def test_both_environment_templates_document_the_setting():
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    for template in ("config/.env.example", "deploy/windows/client360.env.example"):
        text = (repo / template).read_text()
        assert "IMAGE_DERIVATIVE_ROOT" in text, template


def test_a_derivative_can_never_overwrite_its_own_source(tmp_path, monkeypatch):
    """Defensive: if a source somehow sat in the derivative store under its own digest, normalizing
    would target the source path. Refused rather than written."""
    root = tmp_path / "store"
    monkeypatch.setenv(norm.DERIVATIVE_ROOT_ENV, str(root))
    source = _heic(tmp_path, name="src.heic")
    digest = norm.sha256_file(source)
    collision = norm.derivative_path_for(digest)
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_bytes(source.read_bytes())

    with pytest.raises(norm.ImageNormalizationError) as excinfo:
        norm.normalize_image_file(collision)
    assert "source file itself" in str(excinfo.value)
    assert collision.read_bytes() == source.read_bytes()     # untouched
