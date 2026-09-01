"""Image normalization engine — HEIC/HEIF (and any Pillow-readable image) -> a JPEG derivative.

Why this module exists: iPhone photos arrive as HEIC/HEIF, which browsers, Tesseract and every
image-consuming AI API handle inconsistently or not at all. Rather than let each consumer grow its own
conversion, this is the ONE place that turns a stored image into a normalized, downstream-safe JPEG.

Boundaries, deliberately:
  * READ-ONLY on the source. The uploaded original is never rewritten, replaced, moved or re-encoded —
    the derivative is a NEW file with its own path and its own SHA-256.
  * DB-free. This module knows about bytes and paths only; :mod:`app.services.document_derivatives`
    is the orchestration that records provenance against the canonical ``documents`` row (the same
    engine/service split ``ocr_backend`` + ``document_ocr`` already use).
  * Pillow and pillow-heif are imported LAZILY, so this module — and the whole app — imports cleanly on
    a host that does not have them; callers get :class:`ImageNormalizationUnavailable` (retryable)
    rather than an ImportError at startup.

Safety posture:
  * Type is decided by CONTENT (ISO-BMFF ``ftyp`` brands / magic bytes), never by the extension alone.
  * Multi-frame HEIF ("image sequence" brands, ``image/heic-sequence`` / ``image/heif-sequence``) is
    REJECTED EXPLICITLY rather than silently converted to its first frame.
  * Decompression bombs are refused before any pixel is decoded (header-declared pixel count) and again
    by Pillow's own bomb guard during decode.
  * Truncated/corrupt files fail loudly (``LOAD_TRUNCATED_IMAGES`` is left off) and are reported as a
    terminal, non-retryable outcome.
  * No image contents are ever logged; errors carry the file NAME and the exception TYPE only.
  * Derivative and temporary paths are content-addressed, short and bounded, so the Windows Server
    ``MAX_PATH`` limit cannot be reached through a long original filename.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

# --- accepted inputs ---------------------------------------------------------

#: File extensions this module treats as HEIF-family stills.
HEIF_EXTENSIONS = frozenset({"heic", "heif"})

#: MIME types for HEIF-family stills (accepted).
HEIF_MIME_TYPES = frozenset({"image/heic", "image/heif"})

#: MIME types for HEIF-family image SEQUENCES (multi-frame). Recognized so they can be refused with a
#: specific, actionable message — never decoded to a silently-arbitrary single frame.
HEIF_SEQUENCE_MIME_TYPES = frozenset({"image/heic-sequence", "image/heif-sequence"})

#: The normalized derivative format. One target keeps every downstream consumer on one code path.
NORMALIZED_MIME = "image/jpeg"
NORMALIZED_SUFFIX = ".jpg"

# ISO-BMFF brands (ISO/IEC 23008-12 + AVIF). A HEIF file declares a major brand at offset 8 and a list
# of compatible brands from offset 16; either may mark it as a sequence.
_HEIF_STILL_BRANDS = frozenset({"mif1", "mif2", "heic", "heix", "heim", "heis", "avci", "avif"})
_HEIF_SEQUENCE_BRANDS = frozenset({"msf1", "hevc", "hevx", "hevm", "hevs", "avcs", "avis"})

#: Leading-byte signatures for the non-HEIF image types Client360 already accepts. Used to prove an
#: image's declared type against its actual content.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"BM", "bmp"),
)

# --- normalization budget ----------------------------------------------------

#: Longest edge of the derivative. Images are only ever SHRUNK to fit — never enlarged. 2048 keeps a
#: full page of text legible for OCR while staying well inside every current image-input tiling budget.
MAX_DERIVATIVE_PX = 2048

#: Hard ceiling for the derivative, chosen to sit under the current OpenAI image-input limit (20 MB per
#: image). We target well below it so base64 expansion (~1.34x) still fits comfortably.
AI_IMAGE_MAX_BYTES = 20 * 1024 * 1024
MAX_DERIVATIVE_BYTES = 8 * 1024 * 1024

#: JPEG quality ladder: high quality first, stepped down only if the file is still over budget.
_QUALITY_LADDER = (88, 82, 75, 68, 60)

#: Refuse an image whose HEADER declares more pixels than this before decoding a single one. 80 MP is
#: far above any phone camera and far below the memory a decompression bomb needs to hurt the host.
MAX_SOURCE_PIXELS = 80_000_000

#: Refuse a source file larger than this outright (the upload paths cap at 50 MB; this is the decoder's
#: own independent bound so a file that arrived another way cannot bypass it).
MAX_SOURCE_BYTES = 50 * 1024 * 1024

#: Bound for a generated derivative/temporary path. Windows Server's MAX_PATH is 260; we refuse well
#: short of it rather than emitting a path the filesystem will truncate or reject.
MAX_PATH_CHARS = 240

_ENGINE = "pillow+pillow-heif"


# --- errors ------------------------------------------------------------------

class ImageNormalizationError(Exception):
    """Base class for every normalization failure."""


class ImageNormalizationUnavailable(ImageNormalizationError):
    """The imaging libraries are not installed on this host. RETRYABLE once they are."""


class UnsupportedImageError(ImageNormalizationError):
    """The file cannot be normalized and never will be: multi-frame HEIF, a spoofed extension, a
    corrupt/truncated image, or an image bomb. TERMINAL — retrying is pointless."""


# --- content-based type detection -------------------------------------------

def _brands(header: bytes) -> tuple[str, ...]:
    """Every ISO-BMFF brand declared by ``header`` (major brand first, then compatible brands).

    Empty when the header is not an ISO-BMFF ``ftyp`` box. Bounded: reads at most the declared box
    size, capped at the header we were given."""
    if len(header) < 12 or header[4:8] != b"ftyp":
        return ()
    try:
        box_size = int.from_bytes(header[0:4], "big")
    except ValueError:                                    # pragma: no cover — 4 bytes always convert
        return ()
    end = min(len(header), box_size if 16 <= box_size <= 1024 else len(header))
    out = [header[8:12].decode("ascii", "replace").strip().lower()]
    for offset in range(16, end - 3, 4):
        brand = header[offset:offset + 4].decode("ascii", "replace").strip().lower()
        if brand:
            out.append(brand)
    return tuple(out)


def is_heif_header(header: bytes) -> bool:
    """True if ``header`` is a HEIF-family file (still OR sequence), decided by content."""
    brands = _brands(header)
    return any(b in _HEIF_STILL_BRANDS or b in _HEIF_SEQUENCE_BRANDS for b in brands)


def is_heif_sequence_header(header: bytes) -> bool:
    """True if ``header`` declares a HEIF image SEQUENCE (multi-frame), which we refuse.

    A file that declares BOTH a still and a sequence brand is treated as a still: ``heim``/``heis``
    files legitimately carry both, and their primary item is a single displayable image."""
    brands = _brands(header)
    if not brands:
        return False
    if any(b in _HEIF_STILL_BRANDS for b in brands):
        return False
    return any(b in _HEIF_SEQUENCE_BRANDS for b in brands)


def sniff_image_type(header: bytes) -> str | None:
    """Short content-derived type name for ``header`` (``"heif"``, ``"heif-sequence"``, ``"jpeg"``,
    ``"png"``, ``"gif"``, ``"tiff"``, ``"bmp"``, ``"webp"``), or ``None`` when it is not a recognized
    image. Extension and declared MIME are never consulted."""
    if is_heif_sequence_header(header):
        return "heif-sequence"
    if is_heif_header(header):
        return "heif"
    if len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    for signature, name in _IMAGE_MAGIC:
        if header.startswith(signature):
            return name
    return None


def content_matches_heif(header: bytes) -> bool:
    """Upload-time content check for a file claiming ``.heic``/``.heif``: the bytes must actually be a
    HEIF-family still. A multi-frame sequence is rejected here, at acceptance, rather than downstream."""
    return sniff_image_type(header) == "heif"


def extension_of(name: str | None) -> str:
    n = name or ""
    return n.rsplit(".", 1)[-1].lower() if "." in n else ""


def is_heif_name(name: str | None) -> bool:
    """True for ``.heic``/``.heif`` in any case (``IMG_0001.HEIC`` included)."""
    return extension_of(name) in HEIF_EXTENSIONS


def is_heif_content_type(content_type: str | None) -> bool:
    """True for a HEIF still MIME type. A ``*-sequence`` type is deliberately NOT a still."""
    return (content_type or "").split(";")[0].strip().lower() in HEIF_MIME_TYPES


def needs_normalization(*, filename: str | None = None, content_type: str | None = None) -> bool:
    """Does this document need a JPEG derivative before a browser / OCR / an AI image API can use it?

    True for the HEIF family only: every other image type Client360 accepts is already consumable
    downstream, so normalizing it would cost a re-encode and buy nothing."""
    return is_heif_name(filename) or is_heif_content_type(content_type)


# --- decoding ----------------------------------------------------------------

def _load_pillow():
    """Import Pillow and register the HEIF opener. Raises :class:`ImageNormalizationUnavailable` when
    the libraries are absent, so a missing dependency is a HOST problem, never a bad-file verdict."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ImageNormalizationUnavailable(f"Pillow is not installed: {exc}") from exc
    try:
        import pillow_heif
    except ImportError as exc:
        raise ImageNormalizationUnavailable(f"pillow-heif is not installed: {exc}") from exc
    pillow_heif.register_heif_opener()
    return Image, ImageOps


def _read_header(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file — the same digest the upload paths record for the original."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _guard_source(path: Path, label: str) -> str:
    """Validate a source file before any decode. Returns its content-derived type name.

    ``label`` is what the operator sees in the error — the document's ORIGINAL filename, not the
    opaque stored name, so a failure in the review queue names a file a human recognizes."""
    if not path.exists() or not path.is_file():
        raise UnsupportedImageError(f"Image source does not exist: {label}")
    size = path.stat().st_size
    if size == 0:
        raise UnsupportedImageError(f"Image source is empty: {label}")
    if size > MAX_SOURCE_BYTES:
        raise UnsupportedImageError(
            f"{label} is {size // (1024 * 1024)} MB, over the "
            f"{MAX_SOURCE_BYTES // (1024 * 1024)} MB image limit.")
    kind = sniff_image_type(_read_header(path))
    if kind == "heif-sequence":
        raise UnsupportedImageError(
            f"{label} is a multi-frame HEIF image sequence (image/heic-sequence). Client360 stores "
            f"the original but cannot produce a single normalized image from it.")
    if kind is None:
        raise UnsupportedImageError(
            f"{label} is not a recognized image file (its contents do not match any supported "
            f"image format).")
    return kind


def render_jpeg_bytes(path, *, max_px: int = MAX_DERIVATIVE_PX,
                      max_bytes: int = MAX_DERIVATIVE_BYTES,
                      display_name: str | None = None) -> bytes:
    """Decode an image (HEIC/HEIF included) and return normalized JPEG BYTES. Never writes anything.

    Applies, in order: content validation, EXIF orientation, RGB conversion, downscale-only bounding to
    ``max_px``, and a JPEG quality ladder that steps down until the result fits ``max_bytes``. Raises
    :class:`UnsupportedImageError` for a file that cannot be decoded and
    :class:`ImageNormalizationUnavailable` when the imaging libraries are missing."""
    import io

    path = Path(path)
    label = display_name or path.name
    _guard_source(path, label)
    Image, ImageOps = _load_pillow()

    try:
        with Image.open(path) as source:
            width, height = source.size
            if width * height > MAX_SOURCE_PIXELS:
                raise UnsupportedImageError(
                    f"{label} declares {width}x{height} pixels, over the "
                    f"{MAX_SOURCE_PIXELS // 1_000_000} MP decode limit.")
            if getattr(source, "n_frames", 1) > 1 and (source.format or "").upper() in {"HEIF", "AVIF"}:
                raise UnsupportedImageError(
                    f"{label} contains {source.n_frames} frames; Client360 does not normalize "
                    f"multi-frame HEIF images.")
            source.load()
            # EXIF orientation FIRST — an iPhone photo is stored landscape with a rotation tag, and a
            # derivative that ignored it would reach OCR and the browser sideways.
            image = ImageOps.exif_transpose(source) or source
            if image.mode != "RGB":
                image = image.convert("RGB")
            else:
                image = image.copy()
    except UnsupportedImageError:
        raise
    except ImageNormalizationUnavailable:                  # pragma: no cover — raised before decode
        raise
    except Exception as exc:  # noqa: BLE001 — any decoder failure is a terminal bad-file verdict
        # Deliberately reports the type only: never the exception's payload, which can echo file bytes.
        raise UnsupportedImageError(
            f"{label} could not be decoded as an image ({type(exc).__name__}).") from exc

    try:
        # thumbnail() only ever shrinks: an image already inside the bound is left at its native size.
        image.thumbnail((max_px, max_px))
        for quality in _QUALITY_LADDER:
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True,
                       progressive=False, subsampling=2)
            data = buffer.getvalue()
            if len(data) <= max_bytes:
                return data
        # Still over budget at the lowest quality: halve the longest edge and try the ladder again.
        image.thumbnail((max(1, image.width // 2), max(1, image.height // 2)))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=_QUALITY_LADDER[-1], optimize=True, subsampling=2)
        return buffer.getvalue()
    finally:
        image.close()


# --- derivative storage ------------------------------------------------------

#: Development default only — a REPO-RELATIVE path, resolved against the working directory, matching
#: the vault's ``data/vault`` convention (``/data/`` is gitignored). Production must not use it; see
#: :func:`derivative_root`.
DEFAULT_DERIVATIVE_ROOT = "data/derivatives"

#: The environment variable naming the derivative store. Read at CALL time (never captured at import)
#: so operations can change it without a code change and tests can override it — the same pattern
#: ``vault.storage.storage_root`` and ``app.config`` use for every other tunable.
DERIVATIVE_ROOT_ENV = "IMAGE_DERIVATIVE_ROOT"


def _is_production() -> bool:
    """Production posture, read at call time. Mirrors ``app.config.is_production_now`` and
    ``app.deploy.checks.is_production``; read directly here so this module stays import-light and
    free of application configuration side effects."""
    return os.getenv("CLIENT360_ENVIRONMENT", "development").strip().lower() == "production"


def derivative_root() -> Path:
    """Root for normalized derivatives, created if missing. Configured with ``IMAGE_DERIVATIVE_ROOT``.

    Development defaults to ``data/derivatives`` — beside the vault's ``data/vault`` store, inside the
    gitignored ``/data/`` directory.

    PRODUCTION FAILS CLOSED. The default is repo-relative and therefore resolved against whatever the
    service's working directory happens to be, which is exactly how derivatives could end up written
    inside a deployed source tree. So in production ``IMAGE_DERIVATIVE_ROOT`` must be set, and must be
    an ABSOLUTE path (e.g. ``C:\\Client360\\data\\derivatives``). A misconfigured production host
    raises :class:`ImageNormalizationError` here, which the caller records as a retryable ``failed``
    conversion — the uploaded ORIGINAL is untouched either way."""
    configured = (os.getenv(DERIVATIVE_ROOT_ENV) or "").strip()
    if _is_production():
        if not configured:
            raise ImageNormalizationError(
                f"{DERIVATIVE_ROOT_ENV} must be set in production. The development default "
                f"({DEFAULT_DERIVATIVE_ROOT!r}) is relative to the working directory and could place "
                f"derivatives inside the deployed source tree. Set it to an absolute path such as "
                f"C:\\Client360\\data\\derivatives.")
        if not Path(configured).is_absolute():
            raise ImageNormalizationError(
                f"{DERIVATIVE_ROOT_ENV} must be an ABSOLUTE path in production (got a relative path). "
                f"A relative path resolves against the service working directory.")
    root = Path(configured or DEFAULT_DERIVATIVE_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def derivative_path_for(source_sha256: str, *, root: Path | None = None) -> Path:
    """Content-addressed derivative path: ``<root>/<first two hex>/<sha256>.jpg``.

    Content addressing makes normalization idempotent (the same original always yields the same
    derivative path) and keeps the path SHORT and fixed-length — nothing from the uploaded filename
    reaches the filesystem, so neither traversal nor a Windows ``MAX_PATH`` overrun is possible."""
    if not isinstance(source_sha256, str) or len(source_sha256) != 64 \
            or any(c not in "0123456789abcdef" for c in source_sha256):
        raise ValueError("source_sha256 must be a 64-character lowercase hex digest")
    base = (root or derivative_root())
    path = base / source_sha256[:2] / f"{source_sha256}{NORMALIZED_SUFFIX}"
    if len(str(path)) > MAX_PATH_CHARS:
        raise ImageNormalizationError(
            f"Derivative path would be {len(str(path))} characters, over the {MAX_PATH_CHARS}-character "
            f"bound. Set IMAGE_DERIVATIVE_ROOT to a shorter directory.")
    return path


@dataclass(frozen=True)
class NormalizedImage:
    """The provenance record for one normalization. ``source_*`` describes the untouched original."""
    path: Path
    mime: str
    sha256: str
    size_bytes: int
    width: int
    height: int
    source_sha256: str
    source_type: str
    engine: str = _ENGINE
    reused: bool = False

    def as_metadata(self) -> dict:
        return {"derivative_path": str(self.path), "derivative_mime": self.mime,
                "derivative_hash": self.sha256, "derivative_size_bytes": self.size_bytes,
                "width": self.width, "height": self.height, "source_hash": self.source_sha256,
                "engine": self.engine}


def normalize_image_file(path, *, source_sha256: str | None = None, root: Path | None = None,
                         force: bool = False, display_name: str | None = None) -> NormalizedImage:
    """Produce (or reuse) the JPEG derivative for the image at ``path``.

    The source is opened read-only and left byte-identical. The derivative is written to a bounded,
    content-addressed path under :func:`derivative_root` via a temporary file + atomic replace, so a
    crash mid-conversion can never leave a half-written derivative in place of a good one.

    ``display_name`` is the human-facing filename used in error messages (the document's
    ``original_name``), so an operator sees "IMG_5178.HEIC", not the opaque stored name.

    Raises :class:`UnsupportedImageError` (terminal) or :class:`ImageNormalizationUnavailable`
    (retryable); the caller records the outcome and always keeps the original."""
    path = Path(path).resolve()
    digest = source_sha256 or sha256_file(path)
    destination = derivative_path_for(digest, root=root)
    if destination.resolve() == path:
        # Defensive: the derivative is a NEW file and must never be the uploaded original. Reaching
        # this would mean the source already lives in the derivative store under its own digest, and
        # writing would overwrite it. Refuse rather than touch the source.
        raise ImageNormalizationError(
            "Refusing to normalize: the derivative path resolves to the source file itself.")

    if destination.exists() and not force:
        from PIL import Image as _Image  # noqa: N813 — only reached once Pillow is known present
        try:
            with _Image.open(destination) as existing:
                width, height = existing.size
        except Exception:  # noqa: BLE001 — an unreadable cached derivative is simply rebuilt
            destination.unlink(missing_ok=True)
        else:
            return NormalizedImage(
                path=destination, mime=NORMALIZED_MIME, sha256=sha256_file(destination),
                size_bytes=destination.stat().st_size, width=width, height=height,
                source_sha256=digest, source_type=sniff_image_type(_read_header(path)) or "unknown",
                reused=True)

    source_type = _guard_source(path, display_name or path.name)
    data = render_jpeg_bytes(path, display_name=display_name)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, destination)                 # atomic on both POSIX and Windows
    finally:
        temporary.unlink(missing_ok=True)

    from PIL import Image as _Image  # noqa: N813
    with _Image.open(destination) as written:
        width, height = written.size

    return NormalizedImage(
        path=destination, mime=NORMALIZED_MIME, sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), width=width, height=height, source_sha256=digest,
        source_type=source_type)


# --- operational preflight ---------------------------------------------------

def preflight() -> dict:
    """Verify this host can normalize images, and that the derivative root is writable.

    The concrete answer to "does the HEIC support actually work in the Client360 venv on this
    Windows Server?" — run it after deploying with::

        python -m app.services.image_normalization --preflight

    Returns ``{ok, libraries, derivative_root, writable, round_trip, errors}``. Secret-free and
    read-only apart from writing and deleting one temporary marker in the derivative root."""
    result: dict = {"ok": False, "libraries": {}, "derivative_root": None, "writable": False,
                    "round_trip": False, "errors": []}
    for module in ("PIL", "pillow_heif"):
        try:
            imported = __import__(module)
            result["libraries"][module] = getattr(imported, "__version__", "installed")
        except Exception as exc:  # noqa: BLE001 — report every missing library, don't stop
            result["libraries"][module] = None
            result["errors"].append(f"{module}: {exc}")

    try:
        root = derivative_root()
        result["derivative_root"] = str(root)
        marker = root / f".preflight-{uuid.uuid4().hex[:8]}"
        marker.write_bytes(b"ok")
        marker.unlink()
        result["writable"] = True
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"derivative root: {exc}")

    if not result["errors"]:
        # Prove the whole path end to end: encode a tiny HEIF in memory, decode it, emit a JPEG.
        try:
            import io

            from PIL import Image
            _load_pillow()
            buffer = io.BytesIO()
            Image.new("RGB", (16, 16), (1, 2, 3)).save(buffer, format="HEIF")
            assert sniff_image_type(buffer.getvalue()[:64]) == "heif"
            probe = derivative_root() / f".preflight-{uuid.uuid4().hex[:8]}.heic"
            try:
                probe.write_bytes(buffer.getvalue())
                data = render_jpeg_bytes(probe, display_name="preflight.heic")
                result["round_trip"] = data[:3] == b"\xff\xd8\xff"
            finally:
                probe.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"HEIC round trip: {type(exc).__name__}: {exc}")

    result["ok"] = not result["errors"] and result["writable"] and result["round_trip"]
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m app.services.image_normalization",
        description="Image normalization (HEIC/HEIF -> JPEG) preflight / health check.")
    parser.add_argument("--preflight", action="store_true", help="Check libraries + storage and exit.")
    parser.parse_args(argv)
    report = preflight()
    print(f"Image normalization preflight: {'OK' if report['ok'] else 'NOT READY'}")
    print(f"  derivative root: {report['derivative_root']} (writable: {report['writable']})")
    for library, version in report["libraries"].items():
        print(f"  library {library}: {version or 'MISSING'}")
    print(f"  HEIC -> JPEG round trip: {'ok' if report['round_trip'] else 'FAILED'}")
    for error in report["errors"]:
        print(f"  error: {error}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover — operational entry point
    raise SystemExit(main())
