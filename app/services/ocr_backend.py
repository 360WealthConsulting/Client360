"""Production OCR extraction backend (PR 5B).

Concrete engine behind the ``extractor`` interface established by :mod:`app.services.document_ocr`
(PR 5A). It does NOT redesign the OCR service — ``run_ocr`` still owns candidate selection, state,
retry, search, and audit; this module only turns a canonical document's local file into text.

Strategy (honest, no wasted work):
- PDF: read the selectable **text layer** page by page (fast, no image work). Only pages with no real
  text layer (image-only / scanned) fall back to rendering that page + Tesseract OCR.
- Images (PNG / JPG / JPEG / TIFF): Tesseract OCR. TIFF may be multi-page (each frame is a page).
- Reports page count, engine name + Tesseract version, and clear errors: ``OcrBackendUnavailable`` when
  the engine/libraries are not installed, and ordinary exceptions for a genuine extraction failure.

The heavy dependencies (pypdf, pytesseract, pdf2image + Poppler, Pillow) are imported LAZILY so this
module — and the whole app — imports cleanly on hosts (and in CI) that do not have them. The extraction
primitives are grouped in :class:`OcrDeps` so every code path is unit-testable with fakes; production
wires the real primitives via :func:`production_deps`.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.ocr_exceptions import OcrBackendUnavailable, OcrEncryptedPdf, OcrTimeout

log = logging.getLogger(__name__)

_IMAGE_EXT = {"png", "jpg", "jpeg", "tif", "tiff", "heic", "heif"}
# A PDF page with fewer real selectable characters than this is treated as image-only → OCR fallback.
_MIN_TEXT_CHARS = 12
_REQUIRED_LIBS = ("pypdf", "pytesseract", "pdf2image", "PIL")

# OCR is bounded so one problematic scanned page/document can never hang the whole ingestion batch.
_DEFAULT_PAGE_TIMEOUT = 45          # seconds per page (Tesseract), production-appropriate
_DEFAULT_DOCUMENT_TIMEOUT = 300     # seconds per document (across all its OCR'd pages)


def _int_env(name, default):
    import os
    try:
        v = int(os.getenv(name, str(default)))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def ocr_page_timeout():
    """Per-page Tesseract timeout (seconds). Config: ``OCR_PAGE_TIMEOUT_SECONDS`` (default 45)."""
    return _int_env("OCR_PAGE_TIMEOUT_SECONDS", _DEFAULT_PAGE_TIMEOUT)


def ocr_document_timeout():
    """Per-document OCR budget (seconds) across all pages. Config: ``OCR_DOCUMENT_TIMEOUT_SECONDS``
    (default 300)."""
    return _int_env("OCR_DOCUMENT_TIMEOUT_SECONDS", _DEFAULT_DOCUMENT_TIMEOUT)


def _bounded(call, seconds):
    """Run ``call()`` with a wall-clock bound; raise :class:`OcrTimeout` if it overruns. The worker is a
    daemon thread so even a stuck native call cannot keep the process alive — and in production Tesseract
    is also given its own ``timeout=`` so the subprocess is killed, not just abandoned."""
    if not seconds or seconds <= 0:
        return call()
    import threading
    box = {}

    def _w():
        try:
            box["r"] = call()
        except BaseException as exc:  # noqa: BLE001 — propagate to the caller thread
            box["e"] = exc

    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise OcrTimeout(f"OCR step exceeded {seconds}s")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def _ocr_page(deps, make_image, *, page_timeout, page_no, total, name):
    """OCR one rendered page under the per-page timeout. Normalizes a Tesseract process timeout (which
    pytesseract raises as RuntimeError) into :class:`OcrTimeout`. Logs page progress (no contents)."""
    log.info("OCR page %d/%d of %s", page_no, total, name)
    try:
        return _bounded(lambda: deps.ocr_image(make_image()), page_timeout) or ""
    except OcrTimeout:
        raise
    except RuntimeError as exc:
        if "timeout" in str(exc).lower():
            raise OcrTimeout(f"page {page_no}/{total} of {name} timed out: {exc}") from exc
        raise


@dataclass
class OcrDeps:
    """Injectable extraction primitives (real in production, fakes in tests)."""
    pdf_page_texts: Callable[[Path], list[str]]      # selectable text for each PDF page
    render_pdf_page: Callable[[Path, int], object]   # render a 0-based PDF page index → image
    image_pages: Callable[[Path], list[object]]      # load an image file → list of page images
    ocr_image: Callable[[object], str]               # OCR a single image → text
    engine_version: Callable[[], str]                # Tesseract version string
    pdf_render_all: Callable[[Path], list] | None = None  # render ALL PDF pages (fallback when the text
    #                                                        layer is unreadable by pypdf); poppler tolerates
    #                                                        many malformed PDFs pypdf rejects.
    pdf_is_encrypted: Callable[[Path], bool] | None = None  # True only when a PDF needs a password we do
    #                                                        NOT have (read-only, cheap; checked BEFORE any
    #                                                        render/OCR). None on older/fake deps -> skipped.


def _ext(name: str | None) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def _extract(row, path, deps: OcrDeps) -> dict:
    name = row.get("original_name") or (str(path) if path else "document")
    ext = _ext(name)
    if not path or not Path(path).exists():
        raise FileNotFoundError(f"OCR source file not found for document {row.get('id')}: {path!r}")
    p = Path(path)
    if ext == "pdf":
        return _extract_pdf(p, deps)
    if ext in _IMAGE_EXT:
        return _extract_image(p, deps)
    raise ValueError(f"Unsupported OCR file type: .{ext}")   # document_ocr already gates this; defensive


def _extract_pdf_via_render(p: Path, deps: OcrDeps, *, page_to, doc_to, reason) -> dict:
    """Fallback for a PDF whose text layer is unreadable (e.g. pypdf 'Cannot find Root object'): render
    EVERY page with poppler (more tolerant of malformed PDFs) and OCR them. If the renderer also cannot
    open the file it is genuinely corrupt and the error propagates (recorded truthfully as failed)."""
    import time
    render_all = getattr(deps, "pdf_render_all", None)
    if render_all is None:
        raise RuntimeError(f"PDF text layer unreadable and no render fallback available: {reason}")
    images = list(_bounded(lambda: list(render_all(p)), doc_to))   # bounded; raises if poppler can't open
    total = len(images)
    deadline = time.monotonic() + doc_to if doc_to else None
    texts = []
    for i, img in enumerate(images):
        if deadline is not None and time.monotonic() > deadline:
            raise OcrTimeout(f"document {p.name} OCR exceeded {doc_to}s at page {i + 1}/{total}")
        texts.append(_ocr_page(deps, lambda img=img: img, page_timeout=page_to,
                               page_no=i + 1, total=total, name=p.name))
    text = "\n\n".join(t.strip() for t in texts).strip()
    return {"text": text, "page_count": total,
            "engine": f"tesseract {deps.engine_version()} (rendered; text layer unreadable)"}


def _extract_pdf(p: Path, deps: OcrDeps) -> dict:
    import time
    # Deterministic, read-only encryption check BEFORE any text extraction, poppler render, or OCR. An
    # encrypted/password-protected PDF is a distinct TERMINAL outcome (recorded 'unsupported' +
    # password_required), never a transient failure and never retried — so we stop here rather than burning
    # the render fallback + OCR budget on a file we cannot read. We never attempt to guess the password.
    is_encrypted = getattr(deps, "pdf_is_encrypted", None)
    if is_encrypted is not None and is_encrypted(p):
        raise OcrEncryptedPdf(f"encrypted/password-protected PDF: {p.name}")
    page_to, doc_to = ocr_page_timeout(), ocr_document_timeout()
    deadline = time.monotonic() + doc_to if doc_to else None
    try:
        texts = list(deps.pdf_page_texts(p))
    except OcrTimeout:
        raise
    except Exception as exc:  # noqa: BLE001 — pypdf couldn't parse the PDF; try a poppler render fallback
        log.warning("PDF text-layer read failed for %s (%s) — falling back to full-page render", p.name, exc)
        return _extract_pdf_via_render(p, deps, page_to=page_to, doc_to=doc_to, reason=str(exc))
    total = len(texts)
    used_ocr = False
    for i, layer in enumerate(texts):
        if len((layer or "").strip()) < _MIN_TEXT_CHARS:       # image-only page → render + OCR
            if deadline is not None and time.monotonic() > deadline:
                raise OcrTimeout(f"document {p.name} OCR exceeded {doc_to}s at page {i + 1}/{total}")
            texts[i] = _ocr_page(deps, lambda i=i: deps.render_pdf_page(p, i),
                                 page_timeout=page_to, page_no=i + 1, total=total, name=p.name)
            used_ocr = True
    text = "\n\n".join((t or "").strip() for t in texts).strip()
    engine = ("pdf-text-layer" if not used_ocr
              else f"pdf-text-layer+tesseract {deps.engine_version()}")
    return {"text": text, "engine": engine, "page_count": len(texts)}


def _extract_image(p: Path, deps: OcrDeps) -> dict:
    import time
    page_to, doc_to = ocr_page_timeout(), ocr_document_timeout()
    deadline = time.monotonic() + doc_to if doc_to else None
    pages = list(deps.image_pages(p))                          # TIFF may hold multiple frames
    total = len(pages)
    texts = []
    for i, img in enumerate(pages):
        if deadline is not None and time.monotonic() > deadline:
            raise OcrTimeout(f"document {p.name} OCR exceeded {doc_to}s at page {i + 1}/{total}")
        texts.append(_ocr_page(deps, lambda img=img: img, page_timeout=page_to,
                               page_no=i + 1, total=total, name=p.name))
    text = "\n\n".join(t.strip() for t in texts).strip()
    return {"text": text, "engine": f"tesseract {deps.engine_version()}", "page_count": len(pages)}


def build_extractor(deps: OcrDeps):
    """Wrap extraction primitives into an ``extractor(row, path)`` callable for ``run_ocr``."""
    def extractor(row, path):
        return _extract(row, path, deps)
    return extractor


def production_deps() -> OcrDeps:
    """Real extraction primitives. Raises :class:`OcrBackendUnavailable` if the engine/libraries are
    not installed on this host. Honors ``TESSERACT_CMD`` and ``POPPLER_PATH`` from the environment."""
    try:
        import pypdf
        import pytesseract
        from pdf2image import convert_from_path
        from PIL import Image, ImageSequence
    except ImportError as exc:      # a missing library is a backend problem, not an extraction failure
        raise OcrBackendUnavailable(f"OCR backend libraries missing: {exc}") from exc

    try:      # HEIC/HEIF decode support (pillow-heif) is optional — register it when present.
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 — HEIC simply stays unsupported if the lib is absent
        pass

    tesseract_cmd = os.getenv("TESSERACT_CMD")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    poppler_path = os.getenv("POPPLER_PATH") or None

    def pdf_page_texts(p: Path) -> list[str]:
        reader = pypdf.PdfReader(str(p))
        return [(page.extract_text() or "") for page in reader.pages]

    def render_pdf_page(p: Path, index: int):
        imgs = convert_from_path(str(p), first_page=index + 1, last_page=index + 1,
                                 poppler_path=poppler_path)
        if not imgs:
            raise RuntimeError(f"Failed to render page {index + 1} of {p.name}")
        return imgs[0]

    def pdf_render_all(p: Path):
        # Poppler renders every page; tolerates many malformed PDFs pypdf rejects (the text-layer fallback).
        return convert_from_path(str(p), poppler_path=poppler_path)

    def pdf_is_encrypted(p: Path) -> bool:
        # True ONLY when the PDF needs a password we do not have. Encrypted-but-empty-password PDFs (common:
        # encrypted for permissions/metadata yet readable) return False. Cheap + read-only: no rendering, no
        # OCR, no password guessing beyond the standard empty user/owner password pypdf itself tries.
        reader = pypdf.PdfReader(str(p))
        if not getattr(reader, "is_encrypted", False):
            return False
        try:
            reader.decrypt("")                       # attempt the empty user/owner password only
            _ = reader.pages[0] if len(reader.pages) else None   # readable => not password-required
            return False
        except Exception:  # noqa: BLE001 — still locked: a real password we don't have is required
            return True

    def image_pages(p: Path) -> list:
        img = Image.open(str(p))
        return [frame.copy() for frame in ImageSequence.Iterator(img)]

    def ocr_image(img) -> str:
        # Give Tesseract its OWN process timeout so a stuck page kills the subprocess (pytesseract raises
        # RuntimeError('Tesseract process timeout'), normalized to OcrTimeout by _ocr_page).
        return pytesseract.image_to_string(img, timeout=ocr_page_timeout())

    def engine_version() -> str:
        return str(pytesseract.get_tesseract_version())

    return OcrDeps(pdf_page_texts, render_pdf_page, image_pages, ocr_image, engine_version,
                   pdf_render_all=pdf_render_all, pdf_is_encrypted=pdf_is_encrypted)


def build_production_extractor():
    """The production ``extractor`` for ``run_ocr``. Raises ``OcrBackendUnavailable`` if not installed."""
    return build_extractor(production_deps())


def preflight() -> dict:
    """Operational health check: verify every OCR library imports and Tesseract is reachable. Returns
    ``{ok, engine, libraries, tesseract_cmd, poppler_path, errors}`` — used by the deploy preflight."""
    result: dict = {"ok": False, "engine": None, "libraries": {}, "errors": [],
                    "tesseract_cmd": os.getenv("TESSERACT_CMD"),
                    "poppler_path": os.getenv("POPPLER_PATH")}
    for mod in _REQUIRED_LIBS:
        try:
            __import__(mod)
            result["libraries"][mod] = True
        except Exception as exc:      # noqa: BLE001 — report every missing library, don't stop
            result["libraries"][mod] = False
            result["errors"].append(f"{mod}: {exc}")
    try:
        import pytesseract
        if result["tesseract_cmd"]:
            pytesseract.pytesseract.tesseract_cmd = result["tesseract_cmd"]
        result["engine"] = f"tesseract {pytesseract.get_tesseract_version()}"
    except Exception as exc:      # noqa: BLE001
        result["errors"].append(f"tesseract: {exc}")
    result["ok"] = not result["errors"]
    return result


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m app.services.ocr_backend",
                                description="OCR production backend preflight / health check.")
    p.add_argument("--preflight", action="store_true", help="Check engine + libraries and exit.")
    args = p.parse_args(argv)
    if args.preflight or True:      # preflight is the only action
        r = preflight()
        print(f"OCR backend preflight: {'OK' if r['ok'] else 'NOT READY'}")
        print(f"  engine: {r['engine']}")
        print(f"  tesseract_cmd: {r['tesseract_cmd']}")
        print(f"  poppler_path: {r['poppler_path']}")
        for lib, ok in r["libraries"].items():
            print(f"  library {lib}: {'ok' if ok else 'MISSING'}")
        for e in r["errors"]:
            print(f"  error: {e}")
        return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
