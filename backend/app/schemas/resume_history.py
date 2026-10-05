from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Enterprise-level features (Phase 9) -------------------------------------

class ScoringOut(BaseModel):
    """Resume Scoring Engine (Phase 8) report for one version."""
    score: int
    breakdown: dict[str, int]
    warnings: list[str]
    suggestions: list[str]
    persona: str


class OptimizationHistoryEntry(BaseModel):
    """One row of a resume's per-version score trend (Optimization history)."""
    version_number: int
    version_id: int
    created_at: datetime
    score: int
    breakdown: dict[str, int]


class VersionComparisonOut(BaseModel):
    """Before vs After comparison + AI explanation between two versions."""
    comparison: dict
    explanations: dict


class JobMatchRequest(BaseModel):
    job_description: str


class JobMatchOut(BaseModel):
    """Job Description match + Missing Skill recommendations + tailoring
    suggestions. `suggested_skill_order` is a SUGGESTION only - it is never
    applied automatically to the stored resume."""
    match_score: int
    suggested_skill_order: list[str]
    suggestions: list[str]


class OptimizationReportOut(BaseModel):
    """Exportable optimization report (Phase 9d) - JSON shape; a plain-text
    rendering of the same content is available via ?format=text."""
    generated_at: str
    persona: str
    scoring: ScoringOut
    comparison: Optional[dict] = None
    explanations: Optional[dict] = None
    job_match: Optional[JobMatchOut] = None


# --- Recruiter-controlled visibility (display-only; never deletes data) ----

class ResumeVisibilitySettings(BaseModel):
    """Per-version DISPLAY visibility for the rendered PDF/DOCX/preview.
    Every field defaults to True - a version with no settings ever saved,
    or a partial update, always renders exactly what it always has unless
    someone explicitly turns something off. Turning a field off never
    deletes or alters the underlying stored data (content_json is
    untouched either way) - it only conditionally omits that field from
    the NEXT render pass."""
    show_email: bool = True
    show_phone: bool = True
    show_linkedin: bool = True
    show_address: bool = True
    show_employment_dates: bool = True


class ResumeVersionOut(BaseModel):
    id: int
    version_number: int
    tone: str
    created_at: datetime
    created_by_id: int
    visibility: ResumeVisibilitySettings = Field(default_factory=ResumeVisibilitySettings)


class ResumeVersionDetail(ResumeVersionOut):
    content: dict


class ResumeSummaryOut(BaseModel):
    id: int
    title: str
    source_type: str
    created_at: datetime
    updated_at: datetime
    latest_version: Optional[ResumeVersionOut] = None
    version_count: int


class ResumeDetailOut(ResumeSummaryOut):
    versions: list[ResumeVersionOut]


class RegenerateRequest(BaseModel):
    tone: Optional[str] = None


class SignedUrlOut(BaseModel):
    url: str
    expires_at: datetime
