"""SQLAlchemy 2.0 database engine & session factory (SQLite default, Postgres-ready)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def get_data_dir() -> Path:
    base = Path(os.getenv("ROARING_LION_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".data")))
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_database_url() -> str:
    env_url = os.getenv("ROARING_LION_DB_URL")
    if env_url:
        return env_url
    db_path = get_data_dir() / "roaring_lion.db"
    return f"sqlite:///{db_path}"


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def init_engine(db_url: str | None = None):
    global _engine, _SessionLocal
    url = db_url or get_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args, future=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=_engine)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal()
