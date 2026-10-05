from typing import List

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Role(Base):
    """Role lookup table - normalized rather than a free-text column on User
    so role names are consistent and new roles can be added without a data
    migration on every user row."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    users: Mapped[List["User"]] = relationship(back_populates="role")

    ADMIN = "admin"
    USER = "user"
