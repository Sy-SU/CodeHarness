"""SQLAlchemy engine and request-session helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for OJ persistence models."""


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url.endswith(":memory:"):
        return
    raw_path = database_url[len(prefix) :]
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def build_engine(database_url: str) -> Engine:
    """Create an engine appropriate for the configured database."""

    _ensure_sqlite_parent(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the request-scoped session factory for an engine."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield one SQLAlchemy session for a FastAPI request."""

    factory = request.app.state.session_factory
    with factory() as session:
        yield session

