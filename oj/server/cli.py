"""Operator commands for database setup and admin creation."""

from __future__ import annotations

import argparse
import getpass
import sys
from typing import Optional, Sequence

from sqlalchemy import or_, select

from .config import Settings
from .database import Base, build_engine, build_session_factory
from .models import User, UserRole
from .security import hash_password, validate_email, validate_password, validate_username


def _runtime():
    settings = Settings.from_env()
    engine = build_engine(settings.database_url)
    return engine, build_session_factory(engine)


def init_db() -> int:
    """Create the current V1 schema if it does not exist."""

    engine, _factory = _runtime()
    Base.metadata.create_all(engine)
    print("Database initialized.")
    return 0


def create_admin(username: str, email: str) -> int:
    """Interactively create an administrator account."""

    username = username.strip()
    email = email.strip().lower()
    error = validate_username(username) or validate_email(email)
    if error:
        print(error, file=sys.stderr)
        return 2
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    error = validate_password(password)
    if error:
        print(error, file=sys.stderr)
        return 2
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    engine, factory = _runtime()
    Base.metadata.create_all(engine)
    with factory() as db:
        existing = db.scalar(select(User).where(or_(User.username == username, User.email == email)))
        if existing:
            print("That username or email already exists.", file=sys.stderr)
            return 2
        db.add(
            User(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
            )
        )
        db.commit()
    print(f"Admin user '{username}' created.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CodeHarness OJ administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create the database schema")
    create = subparsers.add_parser("create-admin", help="Create an admin account")
    create.add_argument("--username", required=True)
    create.add_argument("--email", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        return init_db()
    if args.command == "create-admin":
        return create_admin(args.username, args.email)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

