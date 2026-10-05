"""Public entrypoint for the extraction layer.

Reads uploaded file bytes, extracts text (docx_parser/pdf_parser),
validates it (validator), scores it (scorer), and retries extraction once
if the score is too low - all before Gemini is ever called. Every other
module in this package is implementation detail behind this one; external
code should only ever import from app.services.extraction (see this
package's __init__.py), never reach into a submodule directly - that's
what keeps the internal module split free to change later without becoming
a cross-cutting refactor every time.

Retrying extraction here means re-running the SAME extractor
(extract_pdf_text/extract_docx_text - the only extraction methods this
pipeline has, both already comprehensive multi-stage extractors in their
own right). Because they're pure functions of the same input bytes, a
second call cannot discover anything the first call didn't already find
UNLESS something inside that extractor is itself non-deterministic (PDF
extraction's OCR fallback is the one case - see pdf_parser.py). This module
does not change what those extractors do - it only decides whether to call
one again and which result to keep, and logs honestly when the retry
didn't help rather than pretending it always will.
"""
import logging

from app.core.config import settings
from app.services.extraction.docx_parser import extract_docx_text as _extract_docx_text
from app.services.extraction.pdf_parser import extract_pdf_text as _extract_pdf_text
from app.services.extraction.scorer import ExtractionScore, score_extraction
from app.services.extraction.validator import SectionValidationResult, validate_sections

logger = logging.getLogger(__name__)

RETRY_SCORE_THRESHOLD = 80


def extract_pdf_text(data: bytes) -> str:
    return _extract_pdf_text(data)


def extract_docx_text(data: bytes) -> str:
    return _extract_docx_text(data)


def _validate_and_score(raw_text: str) -> tuple[SectionValidationResult, ExtractionScore]:
    result = validate_sections(raw_text)
    score = score_extraction(result)
    if settings.DEBUG_RESUME_PIPELINE:
        logger.info("Extraction score: %d/100 (%s)", score.total, score.points)
    elif score.total < RETRY_SCORE_THRESHOLD:
        logger.warning("Extraction score %d/100 is below %d - missing field(s): %s", score.total, RETRY_SCORE_THRESHOLD, result.missing)
    return result, score


def validate_and_score(raw_text: str) -> tuple[SectionValidationResult, ExtractionScore]:
    """Validates and scores `raw_text` without attempting a retry - for
    callers that have no original file bytes to re-extract from (e.g.
    regenerating a resume from its already-stored extracted text). Logging
    only; see retry_if_incomplete for the version that can actually retry.
    """
    return _validate_and_score(raw_text)


def retry_if_incomplete(
    raw_text: str, suffix: str, file_bytes: bytes
) -> tuple[str, SectionValidationResult, ExtractionScore]:
    """Validates and scores `raw_text`; if the score is below
    RETRY_SCORE_THRESHOLD (80), re-runs the same extractor once and keeps
    whichever pass scored higher. Returns (text_to_use, result_for_that_
    text, score_for_that_text) - always matching whatever text is
    returned, so callers never have to re-validate/re-score themselves.
    """
    result, score = _validate_and_score(raw_text)
    if score.total >= RETRY_SCORE_THRESHOLD:
        return raw_text, result, score

    logger.warning(
        "Extraction score %d/100 is below the retry threshold (%d) - missing field(s): %s - "
        "retrying extraction once before calling Gemini.",
        score.total, RETRY_SCORE_THRESHOLD, result.missing,
    )
    retry_text = _extract_pdf_text(file_bytes) if suffix == ".pdf" else _extract_docx_text(file_bytes)
    retry_result, retry_score = _validate_and_score(retry_text)

    if retry_score.total > score.total:
        logger.warning(
            "Retry extraction improved the score from %d/100 to %d/100 - using the retried text.",
            score.total, retry_score.total,
        )
        return retry_text, retry_result, retry_score

    logger.warning(
        "Retry extraction did not improve the score (still %d/100, missing: %s) - proceeding to "
        "Gemini with the original extraction. This is expected whenever the content genuinely "
        "isn't in the source file (or, for DOCX, when nothing about the retry could differ - "
        "extract_docx_text has no alternate mode to retry with); it's only actionable if this "
        "keeps happening for files that visibly do contain the missing section, which would "
        "point at a gap in docx_parser.py/pdf_parser.py itself.",
        retry_score.total, retry_result.missing,
    )
    return raw_text, result, score
