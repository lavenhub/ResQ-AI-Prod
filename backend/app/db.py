"""Database engine + session helpers (SQLite via SQLModel).

SQLite is deliberate for Phase 1: zero external setup for a hackathon demo, and
the SQLModel/SQLAlchemy layer makes a later move to Postgres a one-line change
(`DATABASE_URL`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_settings = get_settings()

# check_same_thread=False: FastAPI/pipeline may touch the session from different
# threads (we run sync DB calls in a worker thread). Safe for SQLite here.
_connect_args = (
    {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    _settings.database_url,
    echo=False,
    connect_args=_connect_args,
)


def init_db() -> None:
    """Create tables. Import models first so they register on SQLModel.metadata."""
    from . import models  # noqa: F401  (registers Incident on metadata)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a request-scoped session."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone context manager for use outside request handlers (e.g. voice tools)."""
    with Session(engine) as session:
        yield session
