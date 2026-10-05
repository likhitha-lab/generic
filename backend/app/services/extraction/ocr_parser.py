"""OCR fallback for scanned/image-only PDF pages.

Used by pdf_parser.py only when none of the text-layer extractors
(pdfplumber, PyMuPDF, pdfminer.six) produce usable text for a page - the
genuinely-scanned-page case, not just "try a different library".
"""
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# DPI used to rasterize a page for OCR - high enough for Tesseract to read
# normal body-text font sizes reliably, without ballooning memory/time on a
# multi-page resume.
_OCR_DPI = 200


def extract_via_ocr(fitz_doc: "fitz.Document", page_index: int) -> tuple[str | None, float | None]:
    """Rasterizes the page (respecting its own /Rotate entry - PyMuPDF's
    get_pixmap renders the page the way a viewer displays it, rotation
    included) and runs Tesseract OCR on the image. Returns (text,
    mean_confidence) - confidence is Tesseract's own per-word score (0-100)
    averaged across recognized words, or None if OCR produced nothing or
    isn't usable on this machine (e.g. the Tesseract binary itself isn't
    installed - a system package, not something pip installs, so this is
    treated as "OCR unavailable", not a request-failing error)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract/Pillow not available - OCR fallback skipped for page %d.", page_index + 1)
        return None, None

    try:
        pixmap = fitz_doc[page_index].get_pixmap(dpi=_OCR_DPI)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError:
        logger.warning(
            "Tesseract OCR binary is not installed on this system - OCR fallback unavailable for "
            "page %d. This is a system-level dependency (e.g. `apt-get install tesseract-ocr`), "
            "not a Python package, and must be installed separately.", page_index + 1,
        )
        return None, None
    except Exception as exc:
        logger.warning("OCR attempt failed for page %d: %s", page_index + 1, exc)
        return None, None

    words = [w for w in ocr_data.get("text", []) if w.strip()]
    confidences = [int(c) for c in ocr_data.get("conf", []) if str(c) not in ("-1", "")]
    text = " ".join(words) if words else None
    confidence = (sum(confidences) / len(confidences)) if confidences else None
    return text, confidence
