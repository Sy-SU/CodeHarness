from sqlalchemy import select

from oj.server.models import User, UserRole
from oj.server.security import verify_password

from .conftest import csrf_from, login


def test_register_creates_argon2_user_and_session(client, db):
    token = csrf_from(client.get("/register"))
    response = client.post(
        "/register",
        data={
            "username": "new.user",
            "email": "New.User@example.com",
            "password": "a-secure-password",
            "password_confirm": "a-secure-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = db.scalar(select(User).where(User.username == "new.user"))
    assert user is not None
    assert user.email == "new.user@example.com"
    assert user.role is UserRole.USER
    assert user.password_hash.startswith("$argon2")
    assert "a-secure-password" not in user.password_hash
    assert "Log out" in client.get("/").text


def test_registration_rejects_duplicate_identity(client, create_user):
    create_user(username="existing", email="existing@example.com")
    token = csrf_from(client.get("/register"))
    response = client.post(
        "/register",
        data={
            "username": "existing",
            "email": "another@example.com",
            "password": "a-secure-password",
            "password_confirm": "a-secure-password",
            "csrf_token": token,
        },
    )
    assert response.status_code == 400
    assert "already registered" in response.text


def test_login_rejects_external_next_url_and_logout_is_csrf_protected(client, create_user):
    create_user()
    token = csrf_from(client.get("/login?next=https://evil.example"))
    response = client.post(
        "/login",
        data={
            "identity": "tester",
            "password": "correct-horse-battery",
            "csrf_token": token,
            "next_url": "https://evil.example",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"
    assert client.post("/logout", data={"csrf_token": "wrong"}).status_code == 400
    settings_response = client.get("/settings")
    logout_token = csrf_from(settings_response)
    response = client.post(
        "/logout", data={"csrf_token": logout_token}, follow_redirects=False
    )
    assert response.status_code == 303
    assert client.get("/settings", follow_redirects=False).headers["location"].startswith("/login")


def test_profile_and_password_updates(client, create_user, app):
    create_user()
    login(client, "tester", "correct-horse-battery")
    token = csrf_from(client.get("/settings"))
    response = client.post(
        "/settings/profile",
        data={"username": "renamed", "email": "renamed@example.com", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    token = csrf_from(client.get("/settings"))
    response = client.post(
        "/settings/password",
        data={
            "current_password": "correct-horse-battery",
            "new_password": "new-password-value",
            "new_password_confirm": "new-password-value",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "renamed"))
        assert user is not None
        assert verify_password(user.password_hash, "new-password-value")


def test_inactive_user_cannot_log_in(client, create_user):
    create_user(active=False)
    token = csrf_from(client.get("/login"))
    response = client.post(
        "/login",
        data={
            "identity": "tester",
            "password": "correct-horse-battery",
            "csrf_token": token,
            "next_url": "/",
        },
    )
    assert response.status_code == 400
    assert "Invalid username/email or password" in response.text

