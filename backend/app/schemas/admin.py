from datetime import datetime

from pydantic import BaseModel

from app.schemas.resume_history import ResumeVersionOut


class AdminUserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    resume_count: int


class AdminResumeOut(BaseModel):
    id: int
    title: str
    source_type: str
    owner_id: int
    owner_email: str
    created_at: datetime
    updated_at: datetime
    latest_version: ResumeVersionOut | None = None
    version_count: int


class DashboardStats(BaseModel):
    total_users: int
    total_admins: int
    total_regular_users: int
    total_resumes: int
    total_versions: int
    resumes_created_last_7_days: int
    resumes_created_today: int
    recent_actions: list[dict]
