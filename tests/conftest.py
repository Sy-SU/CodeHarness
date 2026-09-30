from __future__ import annotations

import os
import re
from typing import Callable

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-import-secret-key")

from oj.server.config import Settings
from oj.server.main import create_app
from oj.server.models import User, UserRole
from oj.server.security import hash_password


CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture()
def app(tmp_path):
    database_path = tmp_path / "test.db"
    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        secret_key="test-secret-key-with-enough-entropy",
        session_https_only=False,
    )
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def create_user(app) -> Callable[..., User]:
    def factory(
        username: str = "tester",
        email: str = "tester@example.com",
        password: str = "correct-horse-battery",
        role: UserRole = UserRole.USER,
        active: bool = True,
    ) -> User:
        with app.state.session_factory() as db:
            user = User(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=role,
                is_active=active,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            db.expunge(user)
            return user

    return factory


def csrf_from(response) -> str:
    match = CSRF_PATTERN.search(response.text)
    assert match, "No CSRF token found in response"
    return match.group(1)


def login(client: TestClient, identity: str, password: str) -> None:
    token = csrf_from(client.get("/login"))
    response = client.post(
        "/login",
        data={
            "identity": identity,
            "password": password,
            "csrf_token": token,
            "next_url": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.fixture()
def db(app):
    with app.state.session_factory() as session:
        yield session
