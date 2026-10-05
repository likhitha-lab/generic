"""Live-Gemini regression suite - validates the full extraction ->
normalization -> Section Recovery -> Resume Intelligence Engine -> DOCX/PDF
pipeline against REAL sample resumes and REAL Gemini responses, for every
resume file dropped into `reference_docs/` or `reference_docs/
regression_samples/` (any role/format - Cloud Engineer, AI Engineer,
Project Manager, Supply Chain Consultant, React Developer, Informatica
Developer, Content Writer, fresher, ... - see the project's own request for
the full list this is meant to cover).

Opt-in only (see pytest.ini's `live_gemini` marker, EXCLUDED by default via
`addopts`) - unlike every other test in this suite, these hit the real
Gemini API, so they're slower and cost money. `tests/conftest.py`'s
autouse `fake_gemini` fixture no-ops for any test carrying this marker (see
that fixture's own docstring), and `GEMINI_API_KEY` must be a real key
exported in the shell before running (conftest.py only supplies a
placeholder if the real environment doesn't already have one).

Run explicitly:
    GEMINI_API_KEY=<your real key> pytest -m live_gemini tests/test_regression_sample_resumes.py -v

Per-sample assertions (see the user's own validation requirements):
  1. No section the Section Recovery Engine flagged as "unrecovered" - i.e.
     detect_missing_sections found a signal in the source text, but none of
     retry/inference/deterministic extraction could recover it.
  2. No duplicated content (skills/certifications/experience bullets) in
     the final optimized output.
  3. ATS keyword coverage >= 95%: of every recognizable technical term
     mentioned anywhere in the RAW source text, at least 95% must still
     appear somewhere in the final output (skills/tools/experience bullets/
     project text combined). This is a DIFFERENT, narrower metric than
     ats_intelligence.py's own `keyword_coverage_ratio` (which measures
     cross-section reinforcement of already-kept skills, not
     source-to-output retention) - deliberately not reused here, since it
     answers a different question than "did we lose real keywords".
  4. The generated DOCX/PDF actually render (no exception) and contain the
     resolved candidate name somewhere in the document.
"""
import glob
import os

import pytest
from docx import Document

from app.services.extraction import extract_docx_text, extract_pdf_text
from app.services.extraction_pipeline import extract_resume
from app.services.file_generator import build_docx_bytes, build_pdf_bytes
from app.services.normalization import enforce_limits, normalize_parsed
from app.services.resume_optimizer import optimize_resume
from app.services.section_recovery import (
    _TECHNICAL_KEYWORD_CORPUS,
    _word_boundary_matches,
    recover_missing_sections,
)

_TESTS_DIR = os.path.dirname(__file__)
_REFERENCE_DOCS_DIR = os.path.join(_TESTS_DIR, "..", "reference_docs")
_REGRESSION_SAMPLES_DIR = os.path.join(_REFERENCE_DOCS_DIR, "regression_samples")


def _discover_sample_files() -> list[str]:
    """Every .docx/.pdf directly in reference_docs/ (the project's existing
    2 real sample resumes) PLUS anything placed under reference_docs/
    regression_samples/ (where additional real, anonymized samples for the
    full role list should be added) - not the regression_samples/ folder
    itself, which only ever holds a README until samples are added."""
    patterns = [
        os.path.join(_REFERENCE_DOCS_DIR, "*.docx"),
        os.path.join(_REFERENCE_DOCS_DIR, "*.pdf"),
        os.path.join(_REGRESSION_SAMPLES_DIR, "**", "*.docx"),
        os.path.join(_REGRESSION_SAMPLES_DIR, "**", "*.pdf"),
    ]
    files: set[str] = set()
    for pattern in patterns:
        files.update(glob.glob(pattern, recursive=True))
    return sorted(files)


_SAMPLE_FILES = _discover_sample_files()


def _combined_output_text(polished: dict) -> str:
    parts = [" ".join(polished.get("skills") or []), " ".join(polished.get("tools") or [])]
    for exp in polished.get("experience") or []:
        parts.extend(exp.get("points") or [])
    for proj in polished.get("projects") or []:
        parts.append(proj.get("description") or "")
        parts.extend(proj.get("responsibilities") or [])
    return " ".join(parts)


@pytest.mark.skipif(
    not _SAMPLE_FILES,
    reason="No sample resumes in reference_docs/ or reference_docs/regression_samples/ to validate against.",
)
@pytest.mark.parametrize("file_path", _SAMPLE_FILES, ids=[os.path.basename(f) for f in _SAMPLE_FILES])
@pytest.mark.live_gemini
def test_pipeline_end_to_end_against_real_sample_resume(file_path):
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    suffix = ".pdf" if file_path.lower().endswith(".pdf") else ".docx"
    raw_text = extract_pdf_text(file_bytes) if suffix == ".pdf" else extract_docx_text(file_bytes)
    assert raw_text.strip(), f"{file_path}: text extraction produced nothing"

    structured = extract_resume(raw_text)
    structured = normalize_parsed(structured)
    structured = enforce_limits(structured)
    structured, recovery_log = recover_missing_sections(raw_text, structured)

    # --- Assertion 1: nothing left "unrecovered" ----------------------------
    unrecovered = [section for section, tier in recovery_log.items() if tier == "unrecovered"]
    assert not unrecovered, f"{file_path}: sections flagged present-in-source but never recovered: {unrecovered}"

    polished = optimize_resume(structured, tone="Professional")

    # --- Assertion 2: no duplicated content ----------------------------------
    def _lowered(items):
        return [str(item).strip().lower() for item in items if str(item).strip()]

    skills_lower = _lowered(polished["skills"])
    assert len(skills_lower) == len(set(skills_lower)), f"{file_path}: duplicate skills in final output"
    certs_lower = _lowered(polished["certifications"])
    assert len(certs_lower) == len(set(certs_lower)), f"{file_path}: duplicate certifications in final output"
    for exp in polished["experience"]:
        points_lower = _lowered(exp.get("points") or [])
        assert len(points_lower) == len(set(points_lower)), (
            f"{file_path}: duplicate bullet(s) for {exp.get('company')!r}"
        )

    # --- Assertion 3: >=95% ATS keyword coverage from source to output ------
    source_keywords = {kw.lower() for kw in _word_boundary_matches(raw_text, _TECHNICAL_KEYWORD_CORPUS)}
    if source_keywords:
        output_keywords = {
            kw.lower() for kw in _word_boundary_matches(_combined_output_text(polished), _TECHNICAL_KEYWORD_CORPUS)
        }
        coverage = len(source_keywords & output_keywords) / len(source_keywords)
        assert coverage >= 0.95, (
            f"{file_path}: ATS keyword coverage {coverage:.0%} below the 95% threshold - "
            f"lost keywords: {sorted(source_keywords - output_keywords)}"
        )

    # --- Assertion 4: DOCX/PDF actually render and contain the candidate's name ---
    name = polished.get("name") or structured.get("name") or "Candidate"
    contact = {"email": polished.get("email")}
    docx_bytes = build_docx_bytes(polished, name, contact)
    pdf_bytes = build_pdf_bytes(polished, name, contact)
    assert docx_bytes, f"{file_path}: DOCX generation produced no bytes"
    assert pdf_bytes, f"{file_path}: PDF generation produced no bytes"

    import io
    doc = Document(io.BytesIO(docx_bytes))
    docx_text = "\n".join(p.text for p in doc.paragraphs)
    assert name in docx_text, f"{file_path}: candidate name {name!r} missing from generated DOCX"


@pytest.mark.skipif(
    not _SAMPLE_FILES,
    reason="No sample resumes in reference_docs/ or reference_docs/regression_samples/ to validate against.",
)
@pytest.mark.parametrize("file_path", _SAMPLE_FILES, ids=[os.path.basename(f) for f in _SAMPLE_FILES])
def test_sample_fixture_is_extractable(file_path):
    """Fast, ALWAYS-ON sanity check (deliberately not @pytest.mark.live_gemini,
    unlike the test above) - every committed regression fixture must be a
    well-formed, text-extractable document, so a corrupted/empty fixture is
    caught on every normal test run, not only whenever someone happens to
    run the opt-in live suite. Does NOT scan for PII - anyone adding a new
    fixture is responsible for anonymizing it themselves before committing
    (see reference_docs/regression_samples/README.md)."""
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    suffix = ".pdf" if file_path.lower().endswith(".pdf") else ".docx"
    text = extract_pdf_text(file_bytes) if suffix == ".pdf" else extract_docx_text(file_bytes)
    assert text.strip(), f"{file_path}: text extraction produced nothing"
