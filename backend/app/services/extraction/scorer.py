"""Weighted extraction-completeness score (0-100).

A single number is coarser than validator.py's per-field detail, but it's
what "retry if score < 80" (reader.py) needs: one threshold to compare,
not nine separate missing/present flags to reason about. The weights below
reflect how much a missing section actually hurts the generated resume -
Contact/Experience matter most (a resume with no name or no work history is
badly broken), Email/Phone/LinkedIn least (utils/contact.py's own regex
fallback already recovers these independently later in the pipeline, so a
gap here is the least consequential of the nine).
"""
from app.services.extraction.validator import FIELD_ORDER, SectionValidationResult

# NOTE: the originally-requested weights (Contact 20, Experience 20,
# Education 15, Skills 15, Projects 10, Certifications 10, Email 5, Phone 5,
# LinkedIn 5) sum to 105, not 100 - Certifications is trimmed to 5 here
# (every other value kept exactly as specified) so the total is really 100.
_WEIGHTS: dict[str, int] = {
    "contact": 20,
    "experience": 20,
    "education": 15,
    "skills": 15,
    "projects": 10,
    "certifications": 5,
    "email": 5,
    "phone": 5,
    "linkedin": 5,
}
assert set(_WEIGHTS) == set(FIELD_ORDER), "scorer weights must cover exactly validator.py's FIELD_ORDER"
assert sum(_WEIGHTS.values()) == 100, "scorer weights must sum to 100"


class ExtractionScore:
    """`points` is the per-field weight actually earned (0 or the field's
    full weight - this scoring is binary per field, not partial credit);
    `total` is their sum, 0-100."""

    def __init__(self, points: dict[str, int]):
        self.points = points
        self.total = sum(points.values())

    def __repr__(self) -> str:
        return f"ExtractionScore(total={self.total}, points={self.points})"


def score_extraction(result: SectionValidationResult) -> ExtractionScore:
    points = {field: (weight if result.present.get(field) else 0) for field, weight in _WEIGHTS.items()}
    return ExtractionScore(points)
