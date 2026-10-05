"""Helpers for building consistent, collision-free storage paths.

Bucket layout (same shape regardless of which StorageService backend is
active - see app/storage/), matching the two folders already created in the
production bucket (resume_bucket08):

    Resume_uploads/{user_id}/{unique_name}   - the original file a user uploaded (upload/convert flow)
    Resume_output/{user_id}/{unique_name}    - every AI-produced PDF/DOCX/JSON, whether from the
                                                 manual Resume Generator or the Resume Converter -
                                                 both flows write to the same folder; only the
                                                 filename's content (not its location) says which
                                                 flow produced it.

Every path includes a timestamp + short UUID (see `_unique_name`), so paths
are never reused/overwritten and never need to be reconstructed from
resume_id/version_number elsewhere - the generated path is the only copy of
truth, stored directly in ResumeVersion's *_blob_path columns.
"""
import re
import uuid
from datetime import datetime, timezone


def safe_slug(value: str, fallback: str = "resume") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()) if value else ""
    slug = slug.strip("_")
    return slug or fallback


def _unique_name(descriptive: str) -> str:
    """e.g. "20260709_102530_4fd2_resume.pdf" - timestamp for readability/
    sorting, a short UUID suffix so two requests in the same second can never
    collide, then the descriptive part last so it's still human-scannable in
    a bucket listing."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:4]
    return f"{timestamp}_{short_uuid}_{descriptive}"


def uploaded_original_path(user_id: int, filename: str) -> str:
    return f"Resume_uploads/{user_id}/{_unique_name(safe_slug(filename))}"


def generated_output_path(user_id: int, version_number: int, filename: str) -> str:
    return f"Resume_output/{user_id}/{_unique_name(f'v{version_number}_{filename}')}"
