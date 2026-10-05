"""Canonical Resume Model - Phase A of the Resume Intelligence Engine redesign.

Root cause this addresses: the pipeline used to be 5-6 independent Gemini
calls whose results were concatenated and cleaned with exact-string rules,
with no single "understanding" of the resume as a set of real-world
entities (this employer, this project, this credential). That shape is
exactly what let the same real job survive 2-4+ times in a rendered resume
(see entity_linking.py's docstring for the concrete, confirmed case this
was found from) - nothing upstream of rendering ever treated "Schlumberger,
Lead Architect" mentioned three different ways as ONE entity to reconcile,
only as three unrelated dict rows to concatenate.

This module is a NEW, TYPED internal representation - every entity (Person,
Employment, Project, Skill, Tool, Certification, EducationEntry,
Achievement, Leadership, Publication, Language, Volunteer) gets a stable
`id`, so later stages (entity_linking.py, identity_validation.py) can
reason about and merge/report on specific entities instead of anonymous
list positions.

Deliberately an ADAPTER, not a pipeline rewrite: `from_legacy_dict()` and
`to_legacy_dict()` are the only two places this module touches the
existing dict shape (see resume_service.py's `structured` dict, the same
shape extraction_pipeline.extract_resume/normalization.normalize_parsed/
enforce_limits/section_recovery.recover_missing_sections already produce
and expect). Every stage downstream of Entity Linking - resume_optimizer.py,
file_generator.py, ats_intelligence.py, resume_scoring_engine.py,
resume_comparison.py - is completely unaware this module exists and keeps
reading/writing the same dict it always has. This is what makes the
migration incremental: each new stage (Understanding, Entity Linking,
Validation) can ship and be validated against real resumes independently,
without a single existing module or test needing to change.

`id`s are simple, sequential, PER-TYPE strings assigned once during
`from_legacy_dict()` (e.g. "employment-1", "employment-2", "skill-1") - not
content hashes or UUIDs. They only need to be unique and stable WITHIN one
canonical resume's lifetime (extraction through rendering of that one
resume), for entity-linking bookkeeping (`merged_from`) and the section-
validation comparison report to reference specific entities - they don't
need to be stable across separate re-generations of the same document.

`merged_from` on every list-item entity records which OTHER entity ids
Entity Linking folded into this one (empty list = nothing merged) - the
same "log every merge, never silent" discipline this codebase already uses
for skill/tool near-duplicate merging (normalization._fuzzy_merge,
skill_intelligence._fuzzy_merge), extended to employer/project/
certification/achievement/career-break identity.
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Person:
    """Contact identity - the highest-priority entity in the whole model
    (see identity_validation.py's hard gate). Every field defaults to ""
    (or None for the two tri-state preference flags), never a placeholder -
    an empty Person field means "not found", to be decided by the
    Validation Engine, never silently invented here."""
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    location: str = ""
    open_to_relocate: bool | None = None
    open_to_remote: bool | None = None


@dataclass
class Employment:
    id: str
    company: str = ""
    role: str = ""
    duration: str = ""
    points: list[str] = field(default_factory=list)
    reason_for_leaving: str = ""
    notes: str = ""
    is_career_break: bool = False
    break_detail: str = ""
    merged_from: list[str] = field(default_factory=list)


@dataclass
class Project:
    id: str
    title: str = ""
    role: str = ""
    client: str = ""
    description: str = ""
    technologies: str = ""
    responsibilities: list[str] = field(default_factory=list)
    merged_from: list[str] = field(default_factory=list)


@dataclass
class TextEntity:
    """Shared shape for every entity that's just a short labeled string in
    the legacy schema - Skill, Tool, Certification, EducationEntry,
    Achievement, Leadership, Publication, Language, Volunteer are all
    structurally identical (an id, a text value, and a merge-audit trail),
    so one dataclass serves all nine rather than nine near-duplicates. The
    type aliases below exist purely for readability at call sites."""
    id: str
    value: str = ""
    merged_from: list[str] = field(default_factory=list)


Skill = TextEntity
Tool = TextEntity
Certification = TextEntity
EducationEntry = TextEntity
Achievement = TextEntity
Leadership = TextEntity
Publication = TextEntity
Language = TextEntity
Volunteer = TextEntity


@dataclass
class CanonicalResume:
    person: Person = field(default_factory=Person)
    summary: str = ""
    employments: list[Employment] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    certifications: list[Certification] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    achievements: list[Achievement] = field(default_factory=list)
    leadership: list[Leadership] = field(default_factory=list)
    publications: list[Publication] = field(default_factory=list)
    languages: list[Language] = field(default_factory=list)
    volunteer: list[Volunteer] = field(default_factory=list)


# Legacy dict key -> canonical field name, for every TextEntity-shaped list.
# "volunteer_experience" (legacy) intentionally maps to "volunteer"
# (canonical) - shortened since the canonical field is already inside a
# `CanonicalResume`/typed `Volunteer` context where "experience" would be
# redundant; from_legacy_dict/to_legacy_dict below are the only two places
# that need to know about this rename.
TEXT_LIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("skills", "skills"),
    ("tools", "tools"),
    ("certifications", "certifications"),
    ("education", "education"),
    ("achievements", "achievements"),
    ("leadership", "leadership"),
    ("publications", "publications"),
    ("languages", "languages"),
    ("volunteer_experience", "volunteer"),
)


def _make_text_entities(items: list | None, id_prefix: str) -> list[TextEntity]:
    return [
        TextEntity(id=f"{id_prefix}-{i + 1}", value=str(item))
        for i, item in enumerate(items or [])
        if str(item).strip()
    ]


def from_legacy_dict(structured: dict) -> CanonicalResume:
    """Builds a CanonicalResume from the standardized pipeline dict (the
    shape extraction_pipeline.extract_resume/normalization.normalize_parsed/
    enforce_limits/section_recovery.recover_missing_sections already
    produce) - assigns every entity a sequential id, otherwise a pure,
    lossless reshaping. Never invents, drops, or reorders content; see
    to_legacy_dict for the inverse."""
    person = Person(
        name=str(structured.get("name") or ""),
        email=str(structured.get("email") or ""),
        phone=str(structured.get("phone") or ""),
        linkedin=str(structured.get("linkedin") or ""),
        github=str(structured.get("github") or ""),
        portfolio=str(structured.get("portfolio") or ""),
        location=str(structured.get("location") or ""),
        open_to_relocate=structured.get("open_to_relocate"),
        open_to_remote=structured.get("open_to_remote"),
    )

    employments = [
        Employment(
            id=f"employment-{i + 1}",
            company=str(exp.get("company") or ""),
            role=str(exp.get("role") or ""),
            duration=str(exp.get("duration") or ""),
            points=[str(p) for p in (exp.get("points") or [])],
            reason_for_leaving=str(exp.get("reason_for_leaving") or ""),
            notes=str(exp.get("notes") or ""),
            is_career_break=bool(exp.get("is_career_break")),
            break_detail=str(exp.get("break_detail") or ""),
        )
        for i, exp in enumerate(structured.get("experience") or [])
        if isinstance(exp, dict)
    ]

    projects = [
        Project(
            id=f"project-{i + 1}",
            title=str(proj.get("title") or ""),
            role=str(proj.get("role") or ""),
            client=str(proj.get("client") or ""),
            description=str(proj.get("description") or ""),
            technologies=str(proj.get("technologies") or ""),
            responsibilities=[str(r) for r in (proj.get("responsibilities") or [])],
        )
        for i, proj in enumerate(structured.get("projects") or [])
        if isinstance(proj, dict)
    ]

    text_lists = {
        canonical_field: _make_text_entities(structured.get(legacy_key), id_prefix=canonical_field.rstrip("s"))
        for legacy_key, canonical_field in TEXT_LIST_FIELDS
    }

    return CanonicalResume(
        person=person,
        summary=str(structured.get("summary") or ""),
        employments=employments,
        projects=projects,
        **text_lists,
    )


def to_legacy_dict(resume: CanonicalResume) -> dict:
    """Inverse of from_legacy_dict - returns exactly the standardized
    pipeline dict shape every downstream module (resume_optimizer.py,
    file_generator.py, ats_intelligence.py, ...) already expects. `id`/
    `merged_from` bookkeeping is internal-only and never leaks into this
    output - those modules have no idea this module exists."""
    legacy: dict = {
        "name": resume.person.name,
        "email": resume.person.email,
        "phone": resume.person.phone,
        "linkedin": resume.person.linkedin,
        "github": resume.person.github,
        "portfolio": resume.person.portfolio,
        "location": resume.person.location,
        "open_to_relocate": resume.person.open_to_relocate,
        "open_to_remote": resume.person.open_to_remote,
        "summary": resume.summary,
        "experience": [
            {
                "company": emp.company,
                "role": emp.role,
                "duration": emp.duration,
                "points": list(emp.points),
                "reason_for_leaving": emp.reason_for_leaving,
                "notes": emp.notes,
                "is_career_break": emp.is_career_break,
                "break_detail": emp.break_detail,
            }
            for emp in resume.employments
        ],
        "projects": [
            {
                "title": proj.title,
                "role": proj.role,
                "client": proj.client,
                "description": proj.description,
                "technologies": proj.technologies,
                "responsibilities": list(proj.responsibilities),
            }
            for proj in resume.projects
        ],
    }
    for legacy_key, canonical_field in TEXT_LIST_FIELDS:
        legacy[legacy_key] = [entity.value for entity in getattr(resume, canonical_field)]
    return legacy


def understand_resume(structured: dict) -> CanonicalResume:
    """Document Understanding - Phase B of the Resume Intelligence Engine
    redesign. Builds a CanonicalResume from `structured`, assigning every
    entity a stable id in the process, and logs entity counts for
    visibility. A no-op ON CONTENT by construction - nothing is merged,
    dropped, or reordered here (see entity_linking.link_entities for the
    stage that actually acts on these ids); this stage's job is
    establishing that every later stage has a real, addressable set of
    entities to work with. Called from resume_service.py right after
    Stage 1 extraction/normalization/Section Recovery, immediately before
    Entity Linking - the caller is expected to run entity_linking.
    link_entities() on the result and then to_legacy_dict() it before
    handing off to the Resume Optimizer, exactly like:

        resume = understand_resume(structured)
        resume = link_entities(resume)
        structured = to_legacy_dict(resume)
    """
    resume = from_legacy_dict(structured)
    logger.info(
        "Document Understanding: person=%r, %d employment(s), %d project(s), %d skill(s), "
        "%d tool(s), %d certification(s), %d education entr(y/ies), %d achievement(s), "
        "%d leadership item(s), %d publication(s), %d language(s), %d volunteer entr(y/ies)",
        resume.person.name or "(none found)", len(resume.employments), len(resume.projects),
        len(resume.skills), len(resume.tools), len(resume.certifications), len(resume.education),
        len(resume.achievements), len(resume.leadership), len(resume.publications),
        len(resume.languages), len(resume.volunteer),
    )
    return resume
