from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class ResumeVersion(Base):
    """One generated snapshot of a Resume.

    Blob paths point into Blob Storage (or the local filesystem fallback -
    see app/storage/); the structured content is duplicated into
    `content_json` here too so history/JSON-view reads don't need a storage
    round trip for what's usually a few KB of text.
    """

    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id"), nullable=False, index=True)
    resume: Mapped["Resume"] = relationship(back_populates="versions")

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tone: Mapped[str] = mapped_column(String(32), nullable=False, default="Professional")

    content_json: Mapped[str] = mapped_column(Text, nullable=False)

    # Recruiter-controlled DISPLAY visibility for this version's rendered
    # PDF/DOCX/preview (show/hide email, phone, LinkedIn, address,
    # employment dates) - JSON text, same "small settings blob, not one
    # column per field" precedent as content_json itself. NULL means "no
    # preferences set yet" - every reader treats that as "show everything",
    # so every row created before this column existed (and every new row
    # that doesn't explicitly set one) renders EXACTLY as it always has.
    # Deliberately NEVER used to remove/alter content_json or any other
    # stored field - see resume_service.update_version_visibility, the only
    # writer of this column, which re-renders just the PDF/DOCX blobs.
    visibility_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    original_blob_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    json_blob_path: Mapped[str] = mapped_column(String(512), nullable=False)
    pdf_blob_path: Mapped[str] = mapped_column(String(512), nullable=False)
    docx_blob_path: Mapped[str] = mapped_column(String(512), nullable=False)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
