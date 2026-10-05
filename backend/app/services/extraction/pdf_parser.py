"""PDF extraction entrypoint.

Waterfall per page: pdfplumber (primary), then - only if that page's text
looks poor - PyMuPDF, then pdfminer.six, then (ocr_parser) OCR as the last
resort. Different PDF producers (Word's own exporter, LaTeX, Canva, a
phone-scanned image embedded in a PDF wrapper, ...) trip up different
libraries in different ways - a font with a broken ToUnicode CMap can make
pdfplumber return garbage while PyMuPDF reads it fine, and a genuinely
scanned/image-only page has no text layer for any of the three to find at
all, which is the one case OCR actually helps with.
"""
import io
import logging

import fitz  # PyMuPDF
import pdfplumber
from pdfminer.high_level import extract_text as _pdfminer_extract_text

from app.core.config import settings
from app.services.extraction.ocr_parser import extract_via_ocr

logger = logging.getLogger(__name__)

# A page's extracted text is treated as "poor quality" - worth trying the
# next extractor on - if it's suspiciously short or mostly non-alphanumeric
# noise (a classic symptom of a font with a broken/missing ToUnicode CMap:
# the library still emits *characters*, just not the right ones). Neither
# threshold claims to detect every bad extraction; they're only meant to
# catch the two failure shapes actually seen from pdfplumber/PyMuPDF/
# pdfminer on real-world resume PDFs.
_MIN_QUALITY_CHARS = 20
_MIN_ALNUM_RATIO = 0.3


def _text_quality_ok(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_QUALITY_CHARS:
        return False
    alnum = sum(1 for c in stripped if c.isalnum())
    return (alnum / len(stripped)) >= _MIN_ALNUM_RATIO


def _extract_via_pymupdf(fitz_doc: "fitz.Document", page_index: int) -> str | None:
    try:
        return fitz_doc[page_index].get_text()
    except Exception as exc:
        logger.warning("PyMuPDF failed to extract page %d: %s", page_index + 1, exc)
        return None


def _extract_via_pdfminer(data: bytes, page_index: int) -> str | None:
    try:
        return _pdfminer_extract_text(io.BytesIO(data), page_numbers=[page_index])
    except Exception as exc:
        logger.warning("pdfminer.six failed to extract page %d: %s", page_index + 1, exc)
        return None


def _longer(candidate: str | None, current: str | None) -> bool:
    return bool(candidate and candidate.strip()) and len((candidate or "").strip()) > len((current or "").strip())


def _extract_page_text(
    page: "pdfplumber.page.Page", page_index: int, data: bytes, fitz_doc: "fitz.Document | None"
) -> tuple[str | None, str]:
    """Waterfall for one page: pdfplumber, then (only if that looks poor)
    PyMuPDF, then (only if still poor) pdfminer.six. Returns (text,
    extractor_name) - `extractor_name` is whichever stage's text ended up
    being used, which is never discarded even if it didn't clear the
    quality bar: a later stage only replaces it if its own text is
    strictly longer, so a partial-but-real result is never thrown away in
    favor of an equally-partial one from a different library."""
    text = page.extract_text()
    used = "pdfplumber"

    if not _text_quality_ok(text) and fitz_doc is not None:
        candidate = _extract_via_pymupdf(fitz_doc, page_index)
        if _text_quality_ok(candidate) or _longer(candidate, text):
            text, used = candidate, "pymupdf"

    if not _text_quality_ok(text):
        candidate = _extract_via_pdfminer(data, page_index)
        if _text_quality_ok(candidate) or _longer(candidate, text):
            text, used = candidate, "pdfminer"

    return text, used


def extract_pdf_text(data: bytes) -> str:
    chunks: list[str] = []
    extractor_counts: dict[str, int] = {}
    rotated_pages: list[int] = []
    ocr_pages: list[int] = []

    try:
        fitz_doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        # PyMuPDF fallback, rotation detection, and OCR all depend on being
        # able to open the file with PyMuPDF - if that itself fails (a
        # malformed PDF pdfplumber can still partially read), degrade to
        # pdfplumber-only rather than failing the whole upload.
        logger.warning(
            "PyMuPDF could not open this PDF (%s) - PyMuPDF fallback, rotation detection, and OCR "
            "are unavailable for this file; continuing with pdfplumber only.", exc,
        )
        fitz_doc = None

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        # A file that's neither a valid PDF pdfplumber nor PyMuPDF can open
        # at all (corrupted, or not actually a PDF despite the extension) -
        # return "" rather than raising, so resume_service.py's existing
        # "no text extracted" check turns this into the same clean 422 a
        # genuinely-empty file already gets.
        logger.error("Failed to open this file as a PDF (%s) - it may be corrupted or not a real "
                     "PDF despite the extension.", exc)
        if fitz_doc is not None:
            fitz_doc.close()
        return ""

    with pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages):
            page_number = index + 1
            text, used = _extract_page_text(page, index, data, fitz_doc)

            rotation = 0
            if fitz_doc is not None:
                rotation = fitz_doc[index].rotation
                if rotation:
                    rotated_pages.append(page_number)

            confidence = None
            if not _text_quality_ok(text) and fitz_doc is not None:
                # Nothing usable from any of the three text-layer extractors -
                # this is the "genuinely scanned/image-only page" case OCR is
                # actually for, not just another "try a different library".
                ocr_text, confidence = extract_via_ocr(fitz_doc, index)
                if ocr_text and ocr_text.strip():
                    text, used = ocr_text, "ocr"
                    ocr_pages.append(page_number)

            if not text or not text.strip():
                used = "none"
                logger.warning(
                    "PDF page %d/%d produced no extractable text from pdfplumber, PyMuPDF, "
                    "pdfminer.six, or OCR - likely a scanned image page with no usable text layer "
                    "and OCR unavailable/unsuccessful. Any resume content on this page will be "
                    "missing from the extraction entirely.", page_number, page_count,
                )
            else:
                chunks.append(text.strip())

            extractor_counts[used] = extractor_counts.get(used, 0) + 1
            if settings.DEBUG_RESUME_PIPELINE:
                logger.info(
                    "PDF page %d/%d: extractor=%s, rotation=%d deg, chars=%d%s",
                    page_number, page_count, used, rotation, len(text) if text else 0,
                    f", ocr_confidence={confidence:.1f}" if confidence is not None else "",
                )

    if fitz_doc is not None:
        fitz_doc.close()

    full_text = "\n".join(chunks)
    if settings.DEBUG_RESUME_PIPELINE:
        logger.info(
            "PDF extraction complete: %d/%d pages yielded text, extractor usage=%s, "
            "%d rotated page(s), %d OCR page(s), %d total characters",
            len(chunks), page_count, extractor_counts, len(rotated_pages), len(ocr_pages), len(full_text),
        )
    return full_text
