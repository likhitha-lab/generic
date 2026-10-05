"""Public API for the extraction layer.

reader.py orchestrates parsing (docx_parser.py/pdf_parser.py, themselves
built on xml_parser.py/table_parser.py/textbox_parser.py/ocr_parser.py),
validation (validator.py), and scoring (scorer.py) into one retry-aware
pipeline. Everything outside this package should import ONLY from here -
`from app.services.extraction import extract_pdf_text, extract_docx_text,
...` - not reach into a submodule directly, so this package's internal
structure stays free to change without becoming a cross-cutting refactor
every time.
"""
from app.services.extraction.reader import (
    extract_docx_text,
    extract_pdf_text,
    retry_if_incomplete,
    validate_and_score,
)
from app.services.extraction.scorer import ExtractionScore, score_extraction
from app.services.extraction.validator import SectionValidationResult, validate_sections

__all__ = [
    "extract_pdf_text",
    "extract_docx_text",
    "retry_if_incomplete",
    "validate_and_score",
    "validate_sections",
    "SectionValidationResult",
    "score_extraction",
    "ExtractionScore",
]
