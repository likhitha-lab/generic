"""Admin-only endpoints: user management, cross-user resume search/download/
delete, and dashboard stats. Every route requires the `admin` role via
`require_admin` (see middleware/rbac.py) - there is no fallback to
ownership checks here, unlike routers/resumes.py.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.http import stream_resume_file
from app.database.session import get_db
from app.middleware.rbac import require_admin
from app.models.audit_log import AuditLog
from app.models.resume import Resume
from app.models.resume_version import ResumeVersion
from app.models.role import Role
from app.models.user import User
from app.schemas.admin import AdminResumeOut, AdminUserOut, DashboardStats
from app.schemas.resume import ResumeFormat
from app.schemas.resume_history import ResumeVersionOut
from app.services.resume_service import delete_resume_files
from app.storage.base import StorageError, StorageService
from app.storage.factory import get_storage
from app.utils.audit import log_action

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _version_out(v: ResumeVersion) -> ResumeVersionOut:
    return ResumeVersionOut(id=v.id, version_number=v.version_number, tone=v.tone, created_at=v.created_at, created_by_id=v.created_by_id)


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: Session = Depends(get_db)) -> list[AdminUserOut]:
    users = db.query(User).order_by(User.created_at.desc()).all()
    counts = dict(
        db.query(Resume.user_id, func.count(Resume.id))
        .filter(Resume.is_deleted.is_(False))
        .group_by(Resume.user_id)
        .all()
    )
    return [
        AdminUserOut(
            id=u.id, email=u.email, full_name=u.full_name, role=u.role_name,
            is_active=u.is_active, created_at=u.created_at, resume_count=counts.get(u.id, 0),
        )
        for u in users
    ]


@router.patch("/users/{user_id}/deactivate", response_model=AdminUserOut)
async def deactivate_user(user_id: int, db: Session = Depends(get_db)) -> AdminUserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return AdminUserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role_name, is_active=user.is_active, created_at=user.created_at, resume_count=0)


@router.get("/resumes", response_model=list[AdminResumeOut])
async def list_all_resumes(search: str | None = Query(None), db: Session = Depends(get_db)) -> list[AdminResumeOut]:
    query = db.query(Resume).join(User).filter(Resume.is_deleted.is_(False))
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Resume.title.ilike(like), User.email.ilike(like)))

    resumes = query.order_by(Resume.updated_at.desc()).all()
    return [
        AdminResumeOut(
            id=r.id, title=r.title, source_type=r.source_type, owner_id=r.user_id, owner_email=r.owner.email,
            created_at=r.created_at, updated_at=r.updated_at,
            latest_version=_version_out(r.latest_version) if r.latest_version else None,
            version_count=len(r.versions),
        )
        for r in resumes
    ]


@router.get("/resumes/{resume_id}/versions/{version_id}/download")
async def admin_download(
    resume_id: int, version_id: int, request: Request,
    file_format: ResumeFormat = Query("pdf", alias="format"),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> StreamingResponse:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.is_deleted:
        raise HTTPException(status_code=404, detail="Resume not found")
    version = next((v for v in resume.versions if v.id == version_id), None)
    if version is None:
        raise HTTPException(status_code=404, detail="Resume version not found")

    if file_format == "pdf":
        blob_path, content_type = version.pdf_blob_path, "application/pdf"
    else:
        blob_path, content_type = version.docx_blob_path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    try:
        file_bytes = storage.download_file(blob_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stored file is missing") from None
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    log_action(db, user_id=None, action="ADMIN_DOWNLOAD", entity_type="resume_version", entity_id=version.id, details={"format": file_format, "resume_owner_id": resume.user_id}, ip_address=_client_ip(request))

    safe_name = resume.title.strip().replace(" ", "_") or "resume"
    return stream_resume_file(file_bytes, f"{safe_name}_v{version.version_number}.{file_format}", content_type)


@router.delete("/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_resume(
    resume_id: int, request: Request, db: Session = Depends(get_db), storage: StorageService = Depends(get_storage)
) -> None:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.is_deleted:
        raise HTTPException(status_code=404, detail="Resume not found")

    try:
        delete_resume_files(storage, resume)
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    resume.is_deleted = True
    resume.deleted_at = datetime.now(timezone.utc)
    db.commit()

    log_action(db, user_id=None, action="ADMIN_DELETE_RESUME", entity_type="resume", entity_id=resume.id, details={"resume_owner_id": resume.user_id}, ip_address=_client_ip(request))


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(db: Session = Depends(get_db)) -> DashboardStats:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    total_users = db.query(func.count(User.id)).scalar() or 0
    total_admins = db.query(func.count(User.id)).join(Role).filter(Role.name == Role.ADMIN).scalar() or 0
    total_resumes = db.query(func.count(Resume.id)).filter(Resume.is_deleted.is_(False)).scalar() or 0
    total_versions = db.query(func.count(ResumeVersion.id)).scalar() or 0
    resumes_week = (
        db.query(func.count(Resume.id))
        .filter(Resume.is_deleted.is_(False), Resume.created_at >= week_ago)
        .scalar()
        or 0
    )
    resumes_today = (
        db.query(func.count(Resume.id))
        .filter(Resume.is_deleted.is_(False), Resume.created_at >= today_start)
        .scalar()
        or 0
    )
    recent = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(20).all()

    return DashboardStats(
        total_users=total_users,
        total_admins=total_admins,
        total_regular_users=total_users - total_admins,
        total_resumes=total_resumes,
        total_versions=total_versions,
        resumes_created_last_7_days=resumes_week,
        resumes_created_today=resumes_today,
        recent_actions=[
            {"action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "user_id": a.user_id, "created_at": a.created_at.isoformat()}
            for a in recent
        ],
    )
