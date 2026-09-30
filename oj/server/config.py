"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Configuration for one OJ server application instance."""

    secret_key: str
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'codeharness.db'}"
    session_https_only: bool = False
    session_cookie: str = "codeharness_session"
    debug: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from process environment variables."""

        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            raise RuntimeError("SECRET_KEY must be set in the environment")
        return cls(
            secret_key=secret_key,
            database_url=os.getenv(
                "DATABASE_URL",
                f"sqlite:///{PROJECT_ROOT / 'data' / 'codeharness.db'}",
            ),
            session_https_only=_as_bool(os.getenv("SESSION_HTTPS_ONLY", "false")),
            debug=_as_bool(os.getenv("DEBUG", "false")),
        )
