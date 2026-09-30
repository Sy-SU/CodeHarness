"""Password, session, CSRF, and form-validation helpers."""

from __future__ import annotations

import re
import secrets
from typing import List, Optional
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request


PASSWORD_HASHER = PasswordHasher()
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$")
PROBLEM_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$|^[a-z0-9]$")


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""

    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Safely verify a password without leaking hash parser failures."""

    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_username(username: str) -> Optional[str]:
    if not USERNAME_PATTERN.fullmatch(username):
        return "Username must be 3-40 characters using letters, numbers, dot, dash, or underscore."
    return None


def validate_email(email: str) -> Optional[str]:
    if len(email) > 320 or email.count("@") != 1:
        return "Enter a valid email address."
    local, domain = email.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return "Enter a valid email address."
    return None


def validate_password(password: str) -> Optional[str]:
    if len(password) < 10:
        return "Password must contain at least 10 characters."
    if len(password) > 200:
        return "Password is too long."
    return None


def validate_problem_id(problem_id: str) -> Optional[str]:
    if not PROBLEM_ID_PATTERN.fullmatch(problem_id):
        return "Problem ID must be lowercase letters, numbers, and dashes (1-80 characters)."
    return None


def csrf_token(request: Request) -> str:
    """Return the existing per-session token or create one."""

    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


def csrf_is_valid(request: Request, supplied: str) -> bool:
    expected = request.session.get("csrf_token")
    return bool(expected and supplied and secrets.compare_digest(str(expected), supplied))


def set_flash(request: Request, message: str, category: str = "info") -> None:
    request.session["flash"] = {"message": message, "category": category}


def pop_flash(request: Request) -> Optional[dict]:
    value = request.session.pop("flash", None)
    return value if isinstance(value, dict) else None


def safe_next_url(candidate: Optional[str], default: str = "/") -> str:
    """Allow only local absolute paths as post-login destinations."""

    if not candidate:
        return default
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return default
    return candidate


def parse_tags(raw: str) -> List[str]:
    """Normalize a comma-separated tag form field without duplicates."""

    seen = set()
    tags: List[str] = []
    for value in raw.split(","):
        tag = value.strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags

