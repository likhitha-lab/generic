"""Unit tests for app/services/canonical_model.py - Phase A of the Resume
Intelligence Engine redesign. Verifies the from_legacy_dict/to_legacy_dict
round-trip is lossless (the adapter-layer promise every later phase - entity
linking, identity validation - depends on), and that every entity gets a
stable, unique id."""
from app.services.canonical_model import (
    CanonicalResume,
    Employment,
    Person,
    Project,
    from_legacy_dict,
    to_legacy_dict,
)

# Representative of the FULL standardized shape - every field
# extraction_pipeline.extract_resume/normalization.normalize_parsed/
# enforce_limits/section_recovery.recover_missing_sections can produce,
# already normalized (all keys present, correct types) - the actual state
# `structured` is in by the time Document Understanding (Phase B) will run.
_FULL_LEGACY_DICT = {
    "name": "Anil Kuppa",
    "email": "kuppa.anil@gmail.com",
    "phone": "+91 95059 79959",
    "linkedin": "https://ae.linkedin.com/in/anilkuppa",
    "github": "",
    "portfolio": "",
    "location": "",
    "open_to_relocate": None,
    "open_to_remote": None,
    "summary": "An accomplished Senior Management Professional with 21 years of experience.",
    "skills": ["Enterprise Architecture", "Data Governance", "TOGAF"],
    "tools": ["Oracle Database", "MS Project", "Visio"],
    "certifications": ["PMP", "TOGAF level 1 & 2 certified"],
    "education": ["Master of Computer Applications, Gujarat University"],
    "experience": [
        {
            "company": "Schlumberger", "role": "Enterprise IT Architect", "duration": "May-10 to April-13",
            "points": ["Wrote and reviewed Enterprise IT standards.", "Served as Lead Architect."],
            "reason_for_leaving": "", "notes": "", "is_career_break": False, "break_detail": "",
        },
        {
            "company": "Career Break", "role": "", "duration": "April-13 to Mar-14",
            "points": [], "reason_for_leaving": "", "notes": "", "is_career_break": True, "break_detail": "",
        },
    ],
    "projects": [
        {
            "title": "HRMS", "role": "", "client": "Globex Corp", "description": "Understanding business requirements.",
            "technologies": ".NET Core, Azure SQL", "responsibilities": ["Designed scalable solutions."],
        },
    ],
    "achievements": ["Recognized as 'Best Employee of the Month' on three occasions."],
    "languages": ["English", "Hindi"],
    "publications": [],
    "volunteer_experience": ["Weekend coding mentor, Code for Good"],
    "leadership": ["Led a 5-person feature team"],
}


def test_from_legacy_dict_to_legacy_dict_round_trips_losslessly():
    resume = from_legacy_dict(_FULL_LEGACY_DICT)
    assert to_legacy_dict(resume) == _FULL_LEGACY_DICT


def test_from_legacy_dict_assigns_unique_sequential_ids():
    resume = from_legacy_dict(_FULL_LEGACY_DICT)
    assert resume.employments[0].id == "employment-1"
    assert resume.employments[1].id == "employment-2"
    assert resume.projects[0].id == "project-1"
    assert resume.skills[0].id == "skill-1"
    all_ids = [e.id for e in resume.employments + resume.projects + resume.skills + resume.tools]
    assert len(all_ids) == len(set(all_ids))


def test_from_legacy_dict_person_fields():
    resume = from_legacy_dict(_FULL_LEGACY_DICT)
    assert resume.person == Person(
        name="Anil Kuppa", email="kuppa.anil@gmail.com", phone="+91 95059 79959",
        linkedin="https://ae.linkedin.com/in/anilkuppa", github="", portfolio="", location="",
        open_to_relocate=None, open_to_remote=None,
    )


def test_from_legacy_dict_career_break_flag_preserved():
    resume = from_legacy_dict(_FULL_LEGACY_DICT)
    assert resume.employments[1].is_career_break is True
    assert resume.employments[1].company == "Career Break"


def test_from_legacy_dict_volunteer_experience_maps_to_volunteer_field():
    resume = from_legacy_dict(_FULL_LEGACY_DICT)
    assert [v.value for v in resume.volunteer] == ["Weekend coding mentor, Code for Good"]


def test_from_legacy_dict_handles_missing_keys_and_empty_dict():
    resume = from_legacy_dict({})
    assert resume.person == Person()
    assert resume.employments == []
    assert resume.skills == []
    assert to_legacy_dict(resume)["experience"] == []
    assert to_legacy_dict(resume)["skills"] == []


def test_from_legacy_dict_drops_blank_text_entity_items():
    resume = from_legacy_dict({**_FULL_LEGACY_DICT, "skills": ["Python", "", "  ", "AWS"]})
    assert [s.value for s in resume.skills] == ["Python", "AWS"]


def test_from_legacy_dict_skips_non_dict_experience_and_project_entries():
    # Defensive - same tolerance normalization.py already has for a Gemini
    # response that flattens an entry to a bare string.
    resume = from_legacy_dict({**_FULL_LEGACY_DICT, "experience": [_FULL_LEGACY_DICT["experience"][0], "a bare string"]})
    assert len(resume.employments) == 1


def test_to_legacy_dict_never_leaks_id_or_merged_from():
    resume = from_legacy_dict(_FULL_LEGACY_DICT)
    resume.employments[0].merged_from = ["employment-2"]
    legacy = to_legacy_dict(resume)
    assert "id" not in legacy["experience"][0]
    assert "merged_from" not in legacy["experience"][0]


def test_canonical_resume_defaults_are_empty_not_none():
    resume = CanonicalResume()
    assert resume.person == Person()
    assert resume.employments == []
    assert resume.skills == []


def test_employment_and_project_merged_from_defaults_to_empty_list():
    assert Employment(id="employment-1").merged_from == []
    assert Project(id="project-1").merged_from == []
