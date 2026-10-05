"""Authenticated resume endpoints: upload, generate, history, JSON view,
download, regenerate, delete.

Every route here requires a logged-in user (any role) and only ever
operates on that user's own resumes - see `_get_owned_resume`. Admins have
their own, separate set of "any user's resume" endpoints in routers/admin.py
rather than overloaded versions of these, so the ownership check here can
stay unconditional and easy to audit.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.core.http import stream_resume_file
from app.core.limiter import limiter
from app.database.session import get_db
from app.models.resume import Resume
from app.models.resume_version import ResumeVersion
from app.models.user import User
from app.schemas.resume import ResumeFormat, ResumeRequest
from app.schemas.resume_history import (
    JobMatchOut,
    JobMatchRequest,
    OptimizationHistoryEntry,
    RegenerateRequest,
    ResumeDetailOut,
    ResumeSummaryOut,
    ResumeVersionDetail,
    ResumeVersionOut,
    ResumeVisibilitySettings,
    ScoringOut,
    SignedUrlOut,
    VersionComparisonOut,
)
from app.services.optimization_report_export import build_optimization_report, render_report_as_text
from app.services.resume_scoring_engine import PERSONAS, score_resume
from app.services.resume_service import (
    compare_resume_versions,
    create_manual_resume,
    create_uploaded_resume,
    delete_resume_files,
    get_optimization_history,
    get_version_content,
    get_version_visibility,
    match_resume_version_to_job,
    regenerate_resume,
    rollback_resume_to_version,
    update_version_visibility,
)
from app.storage.base import StorageError, StorageService
from app.storage.factory import get_storage
from app.utils.audit import log_action

router = APIRouter(prefix="/resumes", tags=["resumes"])

_ALLOWED_EXTENSIONS = {".pdf", ".docx"}
_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # Some browsers/OSes report legacy .doc's MIME type for .docx uploads if
    # misconfigured client-side - rejected by extension check regardless,
    # but not worth a confusing false-negative on content_type alone.
    "application/msword",
    # Sent by some clients when they can't determine a specific type -
    # extension + (for PDF) magic-byte sniffing below still guards this.
    "application/octet-stream",
}
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _version_out(v: ResumeVersion) -> ResumeVersionOut:
    return ResumeVersionOut(
        id=v.id, version_number=v.version_number, tone=v.tone, created_at=v.created_at, created_by_id=v.created_by_id,
        visibility=ResumeVisibilitySettings(**get_version_visibility(v)),
    )


def _resume_summary(r: Resume) -> ResumeSummaryOut:
    latest = r.latest_version
    return ResumeSummaryOut(
        id=r.id,
        title=r.title,
        source_type=r.source_type,
        created_at=r.created_at,
        updated_at=r.updated_at,
        latest_version=_version_out(latest) if latest else None,
        version_count=len(r.versions),
    )


def _get_owned_resume(db: Session, resume_id: int, user: User) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.is_deleted or resume.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    return resume


def _get_version_or_404(resume: Resume, version_id: int) -> ResumeVersion:
    for v in resume.versions:
        if v.id == version_id:
            return v
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume version not found")


def _validate_file_signature(suffix: str, contents: bytes) -> bool:
    """Check the file's actual magic bytes match the extension it claims to
    be - a client-declared filename/Content-Type can be wrong or spoofed,
    the first few bytes of the file itself can't be (without re-encoding it
    into something that would also fail to open as that format)."""
    if suffix == ".pdf":
        return contents.startswith(b"%PDF-")
    if suffix == ".docx":
        return contents.startswith(b"PK\x03\x04")  # .docx is a zip archive
    return False


@router.post("/generate", response_model=ResumeDetailOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT)
async def generate_resume(
    request: Request,
    data: ResumeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> Resume:
    resume = create_manual_resume(db, storage, user, data)
    log_action(db, user_id=user.id, action="RESUME_CREATE", entity_type="resume", entity_id=resume.id, details={"source": "manual"}, ip_address=_client_ip(request))
    return ResumeDetailOut(**_resume_summary(resume).model_dump(), versions=[_version_out(v) for v in resume.versions])


@router.post("/upload", response_model=ResumeDetailOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT)
async def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> Resume:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported")

    if file.content_type and file.content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {file.content_type}")

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (5MB max)")

    if not _validate_file_signature(suffix, contents):
        raise HTTPException(status_code=400, detail="File content doesn't match its extension")

    try:
        resume = create_uploaded_resume(db, storage, user, file.filename or "resume", contents, suffix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    log_action(db, user_id=user.id, action="RESUME_CREATE", entity_type="resume", entity_id=resume.id, details={"source": "upload"}, ip_address=_client_ip(request))
    return ResumeDetailOut(**_resume_summary(resume).model_dump(), versions=[_version_out(v) for v in resume.versions])


@router.get("", response_model=list[ResumeSummaryOut])
async def list_resumes(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ResumeSummaryOut]:
    resumes = (
        db.query(Resume)
        .filter(Resume.user_id == user.id, Resume.is_deleted.is_(False))
        .order_by(Resume.updated_at.desc())
        .all()
    )
    return [_resume_summary(r) for r in resumes]


@router.get("/{resume_id}", response_model=ResumeDetailOut)
async def get_resume(resume_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ResumeDetailOut:
    resume = _get_owned_resume(db, resume_id, user)
    return ResumeDetailOut(**_resume_summary(resume).model_dump(), versions=[_version_out(v) for v in resume.versions])


@router.get("/{resume_id}/versions/{version_id}/json", response_model=ResumeVersionDetail)
async def get_version_json(
    resume_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ResumeVersionDetail:
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    return ResumeVersionDetail(**_version_out(version).model_dump(), content=json.loads(version.content_json))


@router.put("/{resume_id}/versions/{version_id}/visibility", response_model=ResumeVersionOut)
@limiter.limit(settings.RATE_LIMIT)
async def update_visibility_endpoint(
    resume_id: int,
    version_id: int,
    request: Request,
    data: ResumeVisibilitySettings,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> ResumeVersionOut:
    """Recruiter-controlled display visibility (show/hide email, phone,
    LinkedIn, address, employment dates) for ONE version - re-renders that
    version's stored PDF/DOCX to match immediately, but never touches
    content_json or any other stored field (see resume_service.
    update_version_visibility's own docstring): hiding a field here never
    removes the underlying data, only what the NEXT render of this
    version's files chooses to display."""
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    version = update_version_visibility(db, storage, version, data.model_dump())

    log_action(
        db, user_id=user.id, action="RESUME_VISIBILITY_UPDATE", entity_type="resume_version",
        entity_id=version.id, details=data.model_dump(), ip_address=_client_ip(request),
    )
    return _version_out(version)


def _version_blob(version: ResumeVersion, file_format: ResumeFormat) -> tuple[str, str]:
    if file_format == "pdf":
        return version.pdf_blob_path, "application/pdf"
    return version.docx_blob_path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{resume_id}/versions/{version_id}/download")
async def download_version(
    resume_id: int,
    version_id: int,
    request: Request,
    file_format: ResumeFormat = Query("pdf", alias="format"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> StreamingResponse:
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    blob_path, content_type = _version_blob(version, file_format)

    try:
        file_bytes = storage.download_file(blob_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stored file is missing") from None
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    log_action(db, user_id=user.id, action="RESUME_DOWNLOAD", entity_type="resume_version", entity_id=version.id, details={"format": file_format}, ip_address=_client_ip(request))

    safe_name = resume.title.strip().replace(" ", "_") or "resume"
    return stream_resume_file(file_bytes, f"{safe_name}_v{version.version_number}.{file_format}", content_type)


@router.get("/{resume_id}/versions/{version_id}/signed-url", response_model=SignedUrlOut)
async def get_signed_url(
    resume_id: int,
    version_id: int,
    request: Request,
    file_format: ResumeFormat = Query("pdf", alias="format"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> SignedUrlOut:
    """Time-limited direct-to-storage download link, as an alternative to
    /download's proxy-through-the-API bytes - the bucket path itself is
    never exposed, only this expiring signed URL."""
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    blob_path, _ = _version_blob(version, file_format)

    expires_in = timedelta(minutes=settings.SIGNED_URL_EXPIRATION_MINUTES)
    try:
        url = storage.generate_signed_url(blob_path, expires_in)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stored file is missing") from None
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    log_action(db, user_id=user.id, action="RESUME_DOWNLOAD", entity_type="resume_version", entity_id=version.id, details={"format": file_format, "via": "signed_url"}, ip_address=_client_ip(request))

    return SignedUrlOut(url=url, expires_at=datetime.now(timezone.utc) + expires_in)


@router.post("/{resume_id}/regenerate", response_model=ResumeDetailOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT)
async def regenerate(
    resume_id: int,
    request: Request,
    data: RegenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> ResumeDetailOut:
    resume = _get_owned_resume(db, resume_id, user)
    regenerate_resume(db, storage, user, resume, data.tone)
    db.refresh(resume)

    log_action(db, user_id=user.id, action="RESUME_REGENERATE", entity_type="resume", entity_id=resume.id, ip_address=_client_ip(request))
    return ResumeDetailOut(**_resume_summary(resume).model_dump(), versions=[_version_out(v) for v in resume.versions])


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume(
    resume_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> None:
    resume = _get_owned_resume(db, resume_id, user)

    # Delete the actual blobs (original upload + every version's PDF/DOCX/
    # JSON) BEFORE the DB commit - if storage deletion fails, the resume
    # stays visible/undeleted rather than the DB saying "gone" while the
    # files are still sitting in the bucket with no way to find them again.
    try:
        delete_resume_files(storage, resume)
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    resume.is_deleted = True
    resume.deleted_at = datetime.now(timezone.utc)
    db.commit()

    log_action(db, user_id=user.id, action="RESUME_DELETE", entity_type="resume", entity_id=resume.id, ip_address=_client_ip(request))


# --- Enterprise-level features (Phase 9) -------------------------------------
#
# Resume versioning already existed (every route above appends a new
# ResumeVersion, never overwriting one) - the routes below add Before vs
# After comparison, an AI explanation of every optimization, Job
# Description matching + Missing Skill recommendations + tailoring,
# multiple recruiter personas, exportable optimization reports,
# optimization history, and version rollback, all reusing the ALREADY-
# persisted ResumeVersion.content_json (no DB schema changes) - see
# resume_service.py's own "Enterprise-level features" section.

def _validate_persona(persona: str) -> str:
    if persona not in PERSONAS:
        raise HTTPException(status_code=400, detail=f"Unknown persona {persona!r}. Valid personas: {list(PERSONAS)}")
    return persona


@router.get("/{resume_id}/versions/{version_id}/scoring", response_model=ScoringOut)
async def get_version_scoring(
    resume_id: int,
    version_id: int,
    persona: str = Query("general"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScoringOut:
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    report = score_resume(get_version_content(version), persona=_validate_persona(persona))
    return ScoringOut(**report)


@router.get("/{resume_id}/optimization-history", response_model=list[OptimizationHistoryEntry])
async def get_optimization_history_endpoint(
    resume_id: int,
    persona: str = Query("general"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[OptimizationHistoryEntry]:
    resume = _get_owned_resume(db, resume_id, user)
    history = get_optimization_history(resume, persona=_validate_persona(persona))
    return [OptimizationHistoryEntry(**entry) for entry in history]


@router.get("/{resume_id}/versions/compare", response_model=VersionComparisonOut)
async def compare_versions_endpoint(
    resume_id: int,
    before_version_id: int = Query(...),
    after_version_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> VersionComparisonOut:
    resume = _get_owned_resume(db, resume_id, user)
    before_version = _get_version_or_404(resume, before_version_id)
    after_version = _get_version_or_404(resume, after_version_id)
    result = compare_resume_versions(before_version, after_version)
    return VersionComparisonOut(**result)


@router.post("/{resume_id}/versions/{version_id}/match-job", response_model=JobMatchOut)
@limiter.limit(settings.RATE_LIMIT)
async def match_job_endpoint(
    resume_id: int,
    version_id: int,
    request: Request,
    data: JobMatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobMatchOut:
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    result = match_resume_version_to_job(version, data.job_description)
    return JobMatchOut(**result)


@router.post(
    "/{resume_id}/versions/{version_id}/rollback",
    response_model=ResumeDetailOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(settings.RATE_LIMIT)
async def rollback_version_endpoint(
    resume_id: int,
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
) -> ResumeDetailOut:
    """Creates a NEW version reproducing `version_id`'s exact stored
    content - never deletes or overwrites any existing version, so full
    history (including the version being rolled back FROM) is preserved."""
    resume = _get_owned_resume(db, resume_id, user)
    target_version = _get_version_or_404(resume, version_id)
    rollback_resume_to_version(db, storage, resume, user, target_version)
    db.refresh(resume)

    log_action(
        db, user_id=user.id, action="RESUME_ROLLBACK", entity_type="resume", entity_id=resume.id,
        details={"target_version_id": version_id}, ip_address=_client_ip(request),
    )
    return ResumeDetailOut(**_resume_summary(resume).model_dump(), versions=[_version_out(v) for v in resume.versions])


@router.get("/{resume_id}/versions/{version_id}/export-report")
async def export_report_endpoint(
    resume_id: int,
    version_id: int,
    persona: str = Query("general"),
    export_format: str = Query("json", alias="format"),
    compare_to_version_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    resume = _get_owned_resume(db, resume_id, user)
    version = _get_version_or_404(resume, version_id)
    parsed = get_version_content(version)

    before = None
    if compare_to_version_id is not None:
        before_version = _get_version_or_404(resume, compare_to_version_id)
        before = get_version_content(before_version)

    report = build_optimization_report(parsed, before=before, persona=_validate_persona(persona))
    if export_format == "text":
        return PlainTextResponse(render_report_as_text(report))
    return report
