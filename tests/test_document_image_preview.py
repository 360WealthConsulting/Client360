"""Read-only HEIC/HEIF image preview for the admin document-review View.

Covers the Pillow/pillow-heif conversion helper (HEIC -> JPEG, source untouched, safe failure) and the
View-URL routing (HEIC -> image preview; Excel/PDF/others unchanged). Conversion tests require the image
libraries (present in production via requirements.txt); they skip cleanly if absent.
"""
import io

import pytest

from app.routes.admin import _view_url
from app.routes.documents import convert_image_to_jpeg

pil = pytest.importorskip("PIL")
pytest.importorskip("pillow_heif")
import pillow_heif  # noqa: E402
from PIL import Image  # noqa: E402

pillow_heif.register_heif_opener()


def _make_heic(tmp_path, name="IMG_5178.HEIC", size=(64, 48)):
    img = Image.new("RGB", size, (180, 90, 40))
    f = tmp_path / name
    img.save(f, format="HEIF")
    return f


def test_heic_converts_to_jpeg(tmp_path):
    f = _make_heic(tmp_path)
    jpeg = convert_image_to_jpeg(f)
    assert jpeg is not None
    assert jpeg[:2] == b"\xff\xd8"                 # JPEG magic
    Image.open(io.BytesIO(jpeg)).verify()          # a valid JPEG the browser can render


def test_preview_does_not_modify_source_heic(tmp_path):
    f = _make_heic(tmp_path)
    before = f.read_bytes()
    convert_image_to_jpeg(f)
    convert_image_to_jpeg(f)
    assert f.read_bytes() == before                # strictly read-only


def test_large_image_is_downscaled(tmp_path):
    from app.routes.documents import _IMAGE_PREVIEW_MAX_PX
    img = Image.new("RGB", (_IMAGE_PREVIEW_MAX_PX + 800, _IMAGE_PREVIEW_MAX_PX + 600), (10, 20, 30))
    f = tmp_path / "big.heic"
    img.save(f, format="HEIF")
    jpeg = convert_image_to_jpeg(f)
    w, h = Image.open(io.BytesIO(jpeg)).size
    assert w <= _IMAGE_PREVIEW_MAX_PX and h <= _IMAGE_PREVIEW_MAX_PX


def test_convert_fails_safely_on_non_image(tmp_path):
    f = tmp_path / "notimage.heic"
    f.write_bytes(b"this is not an image")
    assert convert_image_to_jpeg(f) is None        # safe fallback -> caller shows Download page


def test_view_url_routes_heic_to_image_preview_and_others_unchanged():
    assert _view_url(5, "IMG_5178.HEIC") == "/documents/5/image-preview"
    assert _view_url(5, "photo.heif") == "/documents/5/image-preview"
    assert _view_url(9, "Expenses.xlsx") == "/documents/9/preview"            # Excel unchanged
    assert _view_url(9, "2021 8879 S.pdf") == "/documents/9/download?inline=1"  # PDF unchanged
    assert _view_url(9, "scan.jpg") == "/documents/9/download?inline=1"        # browser-native image
    assert _view_url(9, "notes.docx") == "/documents/9/download?inline=1"      # other -> existing
