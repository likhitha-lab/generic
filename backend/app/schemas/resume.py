"""Pydantic request models.

There is no JSON response model anymore - /api/generate, /api/convert, and
/api/render all return raw file bytes via StreamingResponse, with the
structured resume data carried in the X-Resume-Preview header instead of a
response body. See app/routers/ for details.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ResumeFormat = Literal["pdf", "docx"]


class EducationItem(BaseModel):
    degree: str = ""
    institution: str = ""
    year: str = ""


class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    points: List[str] = Field(default_factory=list)
    reason_for_leaving: str = ""
    notes: str = ""  # e.g. PF/UAN or other administrative notes tied to this job
    is_career_break: bool = False
    break_detail: str = ""  # e.g. "UI/UX Firm" - what the candidate did during the break


class ProjectItem(BaseModel):
    title: str = ""
    role: str = ""
    description: str = ""
    technologies: str = ""
    responsibilities: List[str] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    """Payload for POST /api/generate (the manual-entry flow)."""

    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    links: Optional[str] = None
    # Contact Intelligence additions: additive, optional fields - a request
    # that omits them (every caller before this addition) behaves exactly
    # as before. Preserved verbatim if supplied, never inferred/guessed.
    github: Optional[str] = None
    portfolio: Optional[str] = None
    open_to_relocate: Optional[bool] = None
    open_to_remote: Optional[bool] = None
    tone: str = "Professional"

    summary: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    certifications: Optional[List[str]] = None
    education: Optional[List[EducationItem]] = None
    experience: Optional[List[ExperienceItem]] = None
    projects: Optional[List[ProjectItem]] = None


class ExperienceEntry(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    points: List[str] = Field(default_factory=list)
    reason_for_leaving: str = ""
    notes: str = ""
    is_career_break: bool = False
    break_detail: str = ""


class ProjectEntry(BaseModel):
    title: str = ""
    role: str = ""
    description: str = ""
    technologies: str = ""
    responsibilities: List[str] = Field(default_factory=list)


class ResumeContent(BaseModel):
    """Payload for POST /api/render.

    This is the *already-generated* structured resume - the same shape
    returned in the X-Resume-Preview header by /api/generate and
    /api/convert. Re-rendering it into another file format needs no Gemini
    call at all, since the content was already produced once.
    """

    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    links: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    open_to_relocate: Optional[bool] = None
    open_to_remote: Optional[bool] = None

    summary: str = ""
    skills: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    education: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    experience: List[ExperienceEntry] = Field(default_factory=list)
    projects: List[ProjectEntry] = Field(default_factory=list)
