from sqlalchemy import select

from oj.server.models import Problem, User, UserRole

from .conftest import csrf_from, login


def problem_form(token: str, **overrides):
    values = {
        "problem_id": "two-sum",
        "title": "Two Sum",
        "statement": "Find two values whose sum is the target.",
        "input_specification": "An array and target.",
        "output_specification": "Two indices.",
        "notes": "Indices are distinct.",
        "time_limit_ms": "1000",
        "memory_limit_mb": "128",
        "source": "original",
        "source_id": "CH-001",
        "source_url": "https://example.com/two-sum",
        "rating": "800",
        "tags": "arrays, hash-map, arrays",
        "csrf_token": token,
    }
    values.update(overrides)
    return values


def test_regular_user_cannot_open_admin(client, create_user):
    create_user()
    login(client, "tester", "correct-horse-battery")
    assert client.get("/admin").status_code == 403


def test_admin_problem_crud_and_public_pages(client, create_user, app):
    create_user(username="root", email="root@example.com", role=UserRole.ADMIN)
    login(client, "root", "correct-horse-battery")

    token = csrf_from(client.get("/admin/problems/new"))
    response = client.post(
        "/admin/problems/new",
        data=problem_form(token),
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail = client.get("/problems/two-sum")
    assert detail.status_code == 200
    assert "Find two values" in detail.text
    assert "hash-map" in detail.text
    assert "Two Sum" in client.get("/problems").text

    token = csrf_from(client.get("/admin/problems/two-sum/edit"))
    update = problem_form(token, title="Pair Sum", tags="arrays")
    update.pop("problem_id")
    response = client.post(
        "/admin/problems/two-sum/edit",
        data=update,
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        problem = session.get(Problem, "two-sum")
        assert problem.title == "Pair Sum"
        assert problem.tags == ["arrays"]

    token = csrf_from(client.get("/admin/problems"))
    response = client.post(
        "/admin/problems/two-sum/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/problems/two-sum").status_code == 404


def test_problem_validation_rejects_bad_id_and_url(client, create_user):
    create_user(username="root", email="root@example.com", role=UserRole.ADMIN)
    login(client, "root", "correct-horse-battery")
    token = csrf_from(client.get("/admin/problems/new"))
    response = client.post(
        "/admin/problems/new",
        data=problem_form(token, problem_id="Bad ID", source_url="file:///etc/passwd"),
    )
    assert response.status_code == 400
    assert "Problem ID" in response.text


def test_admin_can_manage_other_user_but_not_demote_self(client, create_user, app):
    admin = create_user(username="root", email="root@example.com", role=UserRole.ADMIN)
    user = create_user(username="member", email="member@example.com")
    login(client, "root", "correct-horse-battery")

    token = csrf_from(client.get("/admin/users"))
    response = client.post(
        f"/admin/users/{user.id}",
        data={"role": "admin", "is_active": "on", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        updated = session.get(User, user.id)
        assert updated.role is UserRole.ADMIN
        assert updated.is_active

    token = csrf_from(client.get("/admin/users"))
    client.post(
        f"/admin/users/{admin.id}",
        data={"role": "user", "is_active": "on", "csrf_token": token},
        follow_redirects=False,
    )
    with app.state.session_factory() as session:
        unchanged = session.get(User, admin.id)
        assert unchanged.role is UserRole.ADMIN

