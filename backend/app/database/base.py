"""Shared SQLAlchemy declarative base.

Kept in its own module (rather than in session.py) so app/models/*.py can
import `Base` without risking a circular import with the engine/session
setup.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
