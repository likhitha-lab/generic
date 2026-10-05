"""Unit tests for app/services/entity_linking.py - Phase C of the Resume
Intelligence Engine redesign, and the direct fix for the confirmed
real-world defect (a real 21-years-experience resume producing 16-40
duplicated "Professional Experience" entries across repeated runs, instead
of the ~10 real jobs it actually describes - see entity_linking.py's own
module docstring for the full audit trail this was found from).

Several tests below reproduce the EXACT structural pattern observed in that
real resume (company names with location/legal-suffix variants, a role
restated under a different title with near-identical bullets, several
genuinely different career breaks) using synthetic company/person names -
no real candidate data."""
from app.services.canonical_model import CanonicalResume, Employment, Project, TextEntity
from app.services.entity_linking import (
    link_certifications,
    link_education,
    link_employments,
    link_entities,
    link_projects,
    link_text_entities,
    drop_achievements_duplicated_in_employments,
    should_merge_project,
)


def _emp(id, company="", role="", duration="", points=None, is_career_break=False) -> Employment:
    return Employment(id=id, company=company, role=role, duration=duration, points=points or [],
                       is_career_break=is_career_break)


# --- link_employments: the core real-world fix -------------------------------

def test_merges_company_with_location_suffix_variant():
    # Reproduces "Spry Resources" vs "Spry Resources, Hyderabad, India".
    employments = [
        _emp("employment-1", company="Spry Resources", role="Team Lead DBA", duration="Apr-02 to Dec-02",
             points=["Led and designed Data Models."]),
        _emp("employment-2", company="Spry Resources, Hyderabad, India", role="Team Lead DBA",
             duration="From Apr-02 to Dec-02", points=["Led and designed Data Models, ensuring ERP delivery."]),
    ]
    result = link_employments(employments)
    assert len(result) == 1
    assert result[0].merged_from == ["employment-2"]


def test_merges_company_with_legal_suffix_and_descriptor_variant():
    # Reproduces "Applitech Solution Ltd." vs "Applitech Solution Limited,
    # a CMM level 5 organization -, Ahmedabad, India".
    employments = [
        _emp("employment-1", company="Applitech Solution Ltd.", points=["Received appreciation certificates."]),
        _emp("employment-2", company="Applitech Solution Limited, a CMM level 5 organization -, Ahmedabad, India",
             role="Several; starting from Software Engineer, Junior DBA, Dev DBA, Senior DBA",
             duration="4 yrs Mar-98 to Mar-02", points=["Led and mentored the Design team."]),
    ]
    result = link_employments(employments)
    assert len(result) == 1
    assert result[0].role == "Several; starting from Software Engineer, Junior DBA, Dev DBA, Senior DBA"


def test_merges_same_company_multiple_variants_via_year_normalization():
    # Reproduces "CMA CGM SYSTEMS (an IBM subsidiary)" appearing 4 ways for
    # what is really 2 distinct real employments at that company (a promotion) -
    # entries sharing a duration must merge; entries with a DIFFERENT real
    # duration must NOT merge into each other.
    employments = [
        _emp("employment-1", company="CMA CGM SYSTEMS (an IBM subsidiary)", duration="Aug-09 to April 10",
             points=["CMA CGM Systems (CCS) is a CMMi level 3 organization."]),
        _emp("employment-2", company="CMA CGM SYSTEMS (an IBM subsidiary), Dubai",
             role="Manager, SEPT (Software Engineering Practices & Tools)", duration="Aug-09 to April 10",
             points=["Established software engineering quality standards."]),
        _emp("employment-3", company="CMA CGM SYSTEMS (an IBM subsidiary), Dubai", duration="July-06 to July 09",
             points=[]),
        _emp("employment-4", company="CMA CGM SYSTEMS (an IBM subsidiary)", role="Architecture Manager",
             duration="July-06 to July 09", points=["Managed the Architecture Team."]),
    ]
    result = link_employments(employments)
    assert len(result) == 2
    durations = {r.duration for r in result}
    assert durations == {"Aug-09 to April 10", "July-06 to July 09"}
    aug_entry = next(r for r in result if r.duration == "Aug-09 to April 10")
    assert aug_entry.role == "Manager, SEPT (Software Engineering Practices & Tools)"


def test_merges_same_tenure_restated_under_a_different_role_title_via_bullet_overlap():
    # Reproduces the Schlumberger case: same real tenure, restated once as
    # "Enterprise IT Architect" and again as "Lead Architect" with a blank
    # duration on the second mention but near-identical bullet content.
    employments = [
        _emp("employment-1", company="Schlumberger", role="Enterprise IT Architect", duration="May-10 to April-13",
             points=[
                 "Wrote and reviewed Enterprise IT standards and guidelines.",
                 "Served as Lead Architect for the GeoServices Integration Program.",
                 "Integrated TMS with external and internal systems using graphviz software.",
             ]),
        _emp("employment-2", company="Schlumberger", role="Lead Architect", duration="",
             points=[
                 "Developed architecture standards.",
                 "Served as Lead Architect for the GeoServices Integration Program, contributing to the roadmap.",
                 "Integrated the TMS with external and internal systems and developed a method using graphviz.",
             ]),
    ]
    result = link_employments(employments)
    assert len(result) == 1
    assert result[0].merged_from == ["employment-2"]
    # Union of bullets, near-duplicates merged - never just one side's bullets kept.
    assert len(result[0].points) >= 3


def test_does_not_merge_two_genuinely_different_real_stints_at_the_same_company():
    # Genuinely different duration AND role - must stay separate (a real
    # promotion/second stint, not a duplicate).
    employments = [
        _emp("employment-1", company="CMA CGM SYSTEMS (an IBM subsidiary)", role="Architecture Manager",
             duration="July-06 to July 09", points=["Managed the Architecture Team."]),
        _emp("employment-2", company="CMA CGM SYSTEMS (an IBM subsidiary), Dubai",
             role="Manager, SEPT (Software Engineering Practices & Tools)", duration="Aug-09 to April 10",
             points=["Established software engineering quality standards."]),
    ]
    result = link_employments(employments)
    assert len(result) == 2


def test_does_not_merge_different_companies_that_happen_to_share_a_word():
    employments = [
        _emp("employment-1", company="Applitech Solution Ltd.", role="Engineer"),
        _emp("employment-2", company="Solution Partners Inc.", role="Engineer"),
    ]
    result = link_employments(employments)
    assert len(result) == 2


def test_never_drops_bullets_when_merging_employments():
    employments = [
        _emp("employment-1", company="Acme Corp", role="Engineer", duration="2018-2020",
             points=["Built the payments service."]),
        _emp("employment-2", company="Acme Corp", role="Engineer", duration="2018-2020",
             points=["Built the payments service.", "Led the on-call rotation."]),
    ]
    result = link_employments(employments)
    assert len(result) == 1
    assert "Built the payments service." in result[0].points
    assert "Led the on-call rotation." in result[0].points


# --- Career breaks: must NOT be over-merged ----------------------------------

def test_does_not_merge_genuinely_different_career_breaks():
    # Reproduces the real case of 4 distinct career breaks in one long
    # career, including two that are merely ADJACENT (one ends where the
    # next begins) - adjacency must not cause a false merge.
    employments = [
        _emp("employment-1", company="Career Break", duration="April-13 to Mar-14", is_career_break=True),
        _emp("employment-2", company="Career Break", duration="July 1990 - March 1991", is_career_break=True),
        _emp("employment-3", company="Career Break", duration="July 1991 - March 1994", is_career_break=True),
        _emp("employment-4", company="Career Break", duration="February 1995 - February 1998", is_career_break=True),
    ]
    result = link_employments(employments)
    assert len(result) == 4


def test_merges_exact_duplicate_career_break():
    employments = [
        _emp("employment-1", company="Career Break", duration="April-13 to Mar-14", is_career_break=True),
        _emp("employment-2", company="Career Break", duration="April-13 to Mar-14", is_career_break=True),
    ]
    result = link_employments(employments)
    assert len(result) == 1


def test_career_break_never_merges_with_a_real_job():
    employments = [
        _emp("employment-1", company="Career Break", duration="2013-2014", is_career_break=True),
        _emp("employment-2", company="Career Break", role="", duration="2013-2014"),  # NOT flagged as a break
    ]
    result = link_employments(employments)
    assert len(result) == 2


# --- link_projects ------------------------------------------------------------

def test_merges_projects_with_similar_title_and_description():
    projects = [
        Project(id="project-1", title="Customer Portal", description="Built a customer-facing self-service portal.",
                responsibilities=["Built the frontend."]),
        Project(id="project-2", title="Customer Portal Redesign",
                description="Built a customer facing self service portal.", responsibilities=["Built the backend."]),
    ]
    result = link_projects(projects)
    assert len(result) == 1
    assert "Built the frontend." in result[0].responsibilities
    assert "Built the backend." in result[0].responsibilities


def test_does_not_merge_unrelated_projects():
    projects = [
        Project(id="project-1", title="Customer Portal", description="A self-service web portal."),
        Project(id="project-2", title="Inventory Sync Service", description="A backend batch job."),
    ]
    assert len(link_projects(projects)) == 2


def test_does_not_merge_projects_missing_a_title():
    projects = [Project(id="project-1", title=""), Project(id="project-2", title="")]
    assert len(link_projects(projects)) == 2


# --- link_text_entities / link_certifications --------------------------------

def test_link_text_entities_merges_near_duplicates():
    entities = [TextEntity(id="a-1", value="Led a 5-person feature team"),
                TextEntity(id="a-2", value="Led a 5 person feature team")]
    result = link_text_entities(entities)
    assert len(result) == 1


def test_link_text_entities_keeps_distinct_items():
    entities = [TextEntity(id="a-1", value="Led a 5-person feature team"),
                TextEntity(id="a-2", value="Mentored 3 junior engineers")]
    assert len(link_text_entities(entities)) == 2


def test_link_certifications_never_merges_different_certification_levels():
    certs = [
        TextEntity(id="c-1", value="AWS Certified Solutions Architect - Associate"),
        TextEntity(id="c-2", value="AWS Certified Solutions Architect - Professional"),
    ]
    assert len(link_certifications(certs)) == 2


def test_link_certifications_merges_verbatim_repeat():
    certs = [
        TextEntity(id="c-1", value="PMP (May 2010 valid till 2016)"),
        TextEntity(id="c-2", value="PMP (May 2010 valid till 2016)"),
    ]
    assert len(link_certifications(certs)) == 1


# --- _numbers_conflict: confirmed false-merge guard --------------------------
#
# Regression case found by running this session's Phase F real-resume
# validation: "12th, Green Valley Public School, 75.4% (2014)" and "10th,
# Green Valley Public School, 85% (2012)" - two genuinely different real
# qualifications - scored 0.91 raw text similarity (same school, same
# sentence shape) and were silently collapsed into one under the previous
# 0.9 threshold, losing a real qualification. Same failure mode found for
# templated Leadership/Achievement phrases differing only in a number.

def test_link_text_entities_never_merges_different_education_levels_at_same_school():
    entities = [
        TextEntity(id="education-1", value="12th, Green Valley Public School, 75.4% (2014)"),
        TextEntity(id="education-2", value="10th, Green Valley Public School, 85% (2012)"),
    ]
    result = link_education(entities)
    assert len(result) == 2


def test_link_text_entities_never_merges_leadership_claims_differing_only_in_team_size():
    entities = [
        TextEntity(id="lead-1", value="Led a 5-person feature team"),
        TextEntity(id="lead-2", value="Led a 8-person feature team"),
    ]
    # 0.963 raw similarity - would incorrectly merge even at a 0.95 threshold
    # without the numeric-conflict guard.
    assert len(link_text_entities(entities, threshold=0.95)) == 2


def test_link_text_entities_never_merges_achievements_differing_only_in_date():
    entities = [
        TextEntity(id="ach-1", value="Employee of the Month, March 2023"),
        TextEntity(id="ach-2", value="Employee of the Month, June 2021"),
    ]
    assert len(link_text_entities(entities)) == 2


def test_link_education_merges_true_verbatim_duplicate():
    entities = [
        TextEntity(id="education-1", value="Bachelor of Science in Computer Science, ABC University, 2018"),
        TextEntity(id="education-2", value="Bachelor of Science in Computer Science, ABC University, 2018"),
    ]
    assert len(link_education(entities)) == 1


def test_should_merge_project_never_merges_different_years_of_a_recurring_event():
    a = Project(id="project-1", title="Annual Hackathon 2022", description="Company-wide innovation hackathon.")
    b = Project(id="project-2", title="Annual Hackathon 2023", description="Company-wide innovation hackathon.")
    assert should_merge_project(a, b) is False


# --- Achievements not duplicated from experience -----------------------------

def test_drops_achievement_already_stated_as_an_experience_bullet():
    employments = [_emp("employment-1", company="Acme", points=["Recognized as Best Employee of the Month."])]
    achievements = [TextEntity(id="ach-1", value="Recognized as 'Best Employee of the Month'.")]
    result = drop_achievements_duplicated_in_employments(achievements, employments)
    assert result == []


def test_keeps_achievement_not_stated_elsewhere():
    employments = [_emp("employment-1", company="Acme", points=["Built the payments service."])]
    achievements = [TextEntity(id="ach-1", value="Won the company-wide Innovation Award.")]
    result = drop_achievements_duplicated_in_employments(achievements, employments)
    assert len(result) == 1


# --- link_entities: full orchestration ---------------------------------------

def test_link_entities_runs_every_pass_and_returns_the_same_object():
    resume = CanonicalResume(
        employments=[
            _emp("employment-1", company="Acme Corp", role="Engineer", duration="2018-2020", points=["Did X."]),
            _emp("employment-2", company="Acme Corp", role="Engineer", duration="2018-2020", points=["Did X."]),
        ],
        achievements=[TextEntity(id="ach-1", value="Did X.")],
    )
    result = link_entities(resume)
    assert result is resume
    assert len(result.employments) == 1
    assert result.achievements == []  # duplicated verbatim from the (surviving) Experience bullet


# --- Integration case: the REAL 25-entry duplicate-laden list -------------
#
# This is the actual Stage 1 extraction result (company/person names
# anonymized, every other word verbatim) for the real 21-years-experience
# resume that motivated this whole redesign - confirmed directly from real
# generated output before this fix: this exact list rendered as
# 24 separate "Professional Experience" entries, for a career that is
# really about 10 distinct real jobs. See entity_linking.py's module
# docstring for the full audit trail.
_REAL_DUPLICATE_LADEN_EMPLOYMENTS = [
    {"company": "Northgate Solutions Ltd.", "role": "", "duration": "",
     "points": ["Received appreciation certificates for 'Excellence in Leadership by Example'.",
                "Recognized as 'Best Employee of the Month' on three occasions."]},
    {"company": "Meridian Energy Corp", "role": "Enterprise IT Architect", "duration": "May-10 to April-13",
     "points": ["Wrote and reviewed Enterprise IT standards and guidelines.",
                "Served as Lead Architect for the GeoServices Integration Program.",
                "Architected a transformation of the company-wide Transportation Management System (TMS based on OTM).",
                "Integrated TMS with external and internal systems using graphviz software.",
                "Deployed Atlassian Confluence as the company's knowledge base platform."]},
    {"company": "Career Break", "role": "", "duration": "April-13 to Mar-14", "points": [], "is_career_break": True},
    {"company": "Skyline Consulting Group",
     "role": "Consultant, Visiting Faculty: Business Intelligence, its implementation tactics, Roadmapping",
     "duration": "Mar-14 to Dec-14",
     "points": ["Set up a platform with overall integrity of corporate values in line with business goals.",
                "Transformed clients into partners by increasing business agility and profitability."]},
    {"company": "Meridian Energy Corp", "role": "Lead Architect", "duration": "",
     "points": ["Developed architecture standards.",
                "Served as Lead Architect for the GeoServices Integration Program, contributing to the roadmap.",
                "Architected a transformation of the company-wide Transportation Management System based on OTM.",
                "Integrated the TMS with external and internal systems and developed a method using graphviz.",
                "Deployed Atlassian Confluence as the company's knowledge base platform, ensuring SharePoint interop."]},
    {"company": "Orbital Systems (a global technology subsidiary)", "role": "", "duration": "Aug-09 to April 10",
     "points": ["Orbital Systems is a CMMi level 3, multinational organization."]},
    {"company": "Orbital Systems (a global technology subsidiary), Dubai",
     "role": "Manager, SEPT (Software Engineering Practices & Tools)", "duration": "Aug-09 to April 10",
     "points": ["Established software engineering quality standards.",
                "Guided the team and monitored improvement activities."]},
    {"company": "Orbital Systems (a global technology subsidiary), Dubai", "role": "", "duration": "July-06 to July 09",
     "points": []},
    {"company": "Meridian Energy Corp", "role": "Lead Architect", "duration": "",
     "points": ["Contributed to the technological roadmap for a merger.",
                "Architected a transformation of the company-wide Transportation Management System based on OTM.",
                "Deployed Atlassian Confluence as the company's knowledge base platform."]},
    {"company": "Orbital Systems (a global technology subsidiary)",
     "role": "Manager, SEPT (Software Engineering Practices & Tools)", "duration": "Aug-09 to April 10",
     "points": ["Established software engineering quality standards.",
                "Guided teams and monitored improvement activities."]},
    {"company": "Orbital Systems (a global technology subsidiary)", "role": "Architecture Manager",
     "duration": "July-06 to July 09",
     "points": ["Managed the Architecture Team in collaboration with the HO Enterprise Architecture team.",
                "Led and represented the Design Validation team.",
                "Set up and led the Design Validation Service."]},
    {"company": "Port Authority Terminal Group - World's Largest Container Terminals Group",
     "role": "Senior DBA reporting to Senior Manager, IT", "duration": "May-03 to Jun-06",
     "points": ["Led, designed, developed, and presented the Data Warehouse and BI solution for corporate KPIs."]},
    {"company": "Ridgeline Management Services", "role": "Software Quality Consultancy assignment reporting to Director",
     "duration": "Jan-03 to Mar-03",
     "points": ["Studied the client's business execution and Software Development methodologies."]},
    {"company": "Falcon Resources", "role": "Team Lead DBA", "duration": "Apr-02 to Dec-02",
     "points": ["Led and designed Data Models, ensuring the development of ERP systems."]},
    {"company": "Northgate Solutions Limited, a CMM level 5 organization",
     "role": "Several; starting from Software E", "duration": "Mar-98 to Mar-02", "points": []},
    {"company": "Lakeside Hotel, Ahmedabad", "role": "Receptionist", "duration": "From Apr-90 to June-90", "points": []},
    {"company": "Career Break", "role": "", "duration": "July 1990 - March 1991", "points": [], "is_career_break": True},
    {"company": "Ambassador Hotel, Ahmedabad", "role": "House Keeping Supervisor", "duration": "From Apr-91 to June-91",
     "points": []},
    {"company": "Career Break", "role": "", "duration": "July 1991 - March 1994", "points": [], "is_career_break": True},
    {"company": "Horizon Consultants, Ahmedabad", "role": "Account Executive", "duration": "From Apr-94 to Nov-94",
     "points": ["Conducted cold calls with existing accounts/companies to identify vacancies and requirements."]},
    {"company": "Global Merchandise Inc, US - Ahmedabad division", "role": "Merchandiser - Direct Sales",
     "duration": "From Dec-94 to Jan-95", "points": ["Served the Ahmedabad division."]},
    {"company": "Career Break", "role": "", "duration": "February 1995 - February 1998", "points": [],
     "is_career_break": True},
    {"company": "Northgate Solutions Limited, a CMM level 5 organization -, Ahmedabad, India",
     "role": "Several; starting from Software Engineer, Junior DBA, Dev DBA, Senior DBA",
     "duration": "4 yrs Mar-98 to Mar-02",
     "points": ["Led and mentored the Design team.",
                "Provided Design and Review services to different Project teams within the company.",
                "Managed technical projects and provided Pre-sales technical Support."]},
    {"company": "Falcon Resources, Hyderabad, India", "role": "Team Lead DBA", "duration": "From Apr-02 to Dec-02",
     "points": ["Led and designed Data Models, ensuring the successful development of ERP systems."]},
    {"company": "Ridgeline Management Services, Ahmedabad, India",
     "role": "A brief Software Quality Consultancy assignment reporting to Director",
     "duration": "From Jan-03 to Mar-03",
     "points": ["Studied client's business execution and Software Development methodologies."]},
]


def test_entity_linking_collapses_the_real_25_entry_duplicate_case_to_distinct_jobs():
    employments = [
        Employment(id=f"employment-{i + 1}", **entry)
        for i, entry in enumerate(_REAL_DUPLICATE_LADEN_EMPLOYMENTS)
    ]
    result = link_employments(employments)

    # The 25 raw entries collapse to exactly 16 real entities: 12 distinct
    # real jobs (Northgate x3->1, Meridian x3->1, Skyline, Orbital "Manager
    # SEPT" x4->1, Orbital "Architecture Manager" - a genuinely different
    # stint at the same company, correctly kept separate, Port Authority,
    # Ridgeline x2->1, Falcon x2->1, Lakeside Hotel, Ambassador Hotel,
    # Horizon Consultants, Global Merchandise) + 4 genuinely different
    # career breaks at different points in the career (verified below to
    # have never been merged together, despite two of them being merely
    # ADJACENT - one ending "March 1991", the next starting "July 1991").
    assert len(result) == 16

    non_breaks = [r for r in result if not r.is_career_break]
    assert len(non_breaks) == 12
    # No (company, duration) combination appears twice - "Orbital Systems"
    # legitimately appears twice (two real, differently-dated stints, a
    # promotion - see test_does_not_merge_two_genuinely_different_real_
    # stints_at_the_same_company), which is correct, not a duplicate; what
    # must never happen is the SAME company+duration pair surviving twice.
    identity_keys = [(r.company.split(",")[0].split("(")[0].strip().lower(), r.duration) for r in non_breaks]
    assert len(identity_keys) == len(set(identity_keys)), f"duplicate (company, duration) survived: {identity_keys}"

    # The 4 genuinely different career breaks (different date ranges) must
    # all survive distinctly - this is the critical anti-regression check.
    career_breaks = [r for r in result if r.is_career_break]
    assert len(career_breaks) == 4
    assert len({cb.duration for cb in career_breaks}) == 4

    # The merged Meridian Energy Corp entry keeps bullets from all 3 restatements.
    meridian = next(r for r in result if r.company == "Meridian Energy Corp")
    assert any("GeoServices" in p for p in meridian.points)
    assert any("technological roadmap for a merger" in p for p in meridian.points)


def test_link_entities_leaves_skills_and_tools_untouched():
    resume = CanonicalResume(
        skills=[TextEntity(id="skill-1", value="Python"), TextEntity(id="skill-2", value="Python")],
    )
    result = link_entities(resume)
    # Entity Linking deliberately does not dedupe skills/tools - that's
    # skill_intelligence.py's job, run later in resume_optimizer.py.
    assert len(result.skills) == 2
