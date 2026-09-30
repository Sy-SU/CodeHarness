"""Small server-rendered administration interface."""

from __future__ import annotations

from typing import Dict, Optional, Union
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_admin
from ..models import Problem, User, UserRole
from ..security import csrf_is_valid, parse_tags, set_flash, validate_problem_id
from ..web import render


FormValue = Union[str, int, None]


def _problem_values(
    problem_id: str,
    title: str,
    statement: str,
    input_specification: str,
    output_specification: str,
    notes: str,
    time_limit_ms: int,
    memory_limit_mb: int,
    source: str,
    source_id: str,
    source_url: str,
    rating: Optional[str],
    tags: str,
) -> Dict[str, FormValue]:
    return {
        "id": problem_id.strip(),
        "title": title.strip(),
        "statement": statement.strip(),
        "input_specification": input_specification.strip(),
        "output_specification": output_specification.strip(),
        "notes": notes.strip(),
        "time_limit_ms": time_limit_ms,
        "memory_limit_mb": memory_limit_mb,
        "source": source.strip(),
        "source_id": source_id.strip(),
        "source_url": source_url.strip(),
        "rating": rating.strip() if rating else None,
        "tags_text": tags.strip(),
    }


def _validate_problem(values: Dict[str, FormValue]) -> Optional[str]:
    problem_id = str(values["id"])
    error = validate_problem_id(problem_id)
    if error:
        return error
    if not values["title"]:
        return "Title is required."
    if not values["statement"]:
        return "Statement is required."
    if int(values["time_limit_ms"] or 0) < 1:
        return "Time limit must be positive."
    if int(values["memory_limit_mb"] or 0) < 1:
        return "Memory limit must be positive."
    source_url = str(values["source_url"] or "")
    if source_url and urlsplit(source_url).scheme not in {"http", "https"}:
        return "Source URL must use http or https."
    rating = values["rating"]
    if rating:
        try:
            if int(str(rating)) < 0:
                raise ValueError
        except ValueError:
            return "Rating must be a non-negative integer."
    return None


def _apply_problem_values(problem: Problem, values: Dict[str, FormValue]) -> None:
    problem.title = str(values["title"])
    problem.statement = str(values["statement"])
    problem.input_specification = str(values["input_specification"])
    problem.output_specification = str(values["output_specification"])
    problem.notes = str(values["notes"])
    problem.time_limit_ms = int(values["time_limit_ms"] or 0)
    problem.memory_limit_mb = int(values["memory_limit_mb"] or 0)
    problem.source = str(values["source"]) or None
    problem.source_id = str(values["source_id"]) or None
    problem.source_url = str(values["source_url"]) or None
    problem.rating = int(str(values["rating"])) if values["rating"] else None
    problem.tags = parse_tags(str(values["tags_text"]))


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/admin")

    @router.get("", name="admin_home")
    def admin_home(
        request: Request,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        counts = {
            "users": db.scalar(select(func.count()).select_from(User)) or 0,
            "problems": db.scalar(select(func.count()).select_from(Problem)) or 0,
        }
        return render(
            request,
            templates,
            "admin/index.html",
            user=admin,
            context={"counts": counts},
        )

    @router.get("/problems", name="admin_problems")
    def problem_admin_list(
        request: Request,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        problems = db.scalars(select(Problem).order_by(Problem.id)).all()
        return render(
            request,
            templates,
            "admin/problems.html",
            user=admin,
            context={"problems": problems},
        )

    @router.get("/problems/new", name="admin_problem_new")
    def problem_new_form(request: Request, admin: User = Depends(require_admin)):
        return render(
            request,
            templates,
            "admin/problem_form.html",
            user=admin,
            context={"problem": None, "editing": False},
        )

    @router.post("/problems/new")
    def problem_create(
        request: Request,
        problem_id: str = Form(...),
        title: str = Form(...),
        statement: str = Form(...),
        input_specification: str = Form(""),
        output_specification: str = Form(""),
        notes: str = Form(""),
        time_limit_ms: int = Form(2000),
        memory_limit_mb: int = Form(256),
        source: str = Form(""),
        source_id: str = Form(""),
        source_url: str = Form(""),
        rating: Optional[str] = Form(None),
        tags: str = Form(""),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        values = _problem_values(
            problem_id,
            title,
            statement,
            input_specification,
            output_specification,
            notes,
            time_limit_ms,
            memory_limit_mb,
            source,
            source_id,
            source_url,
            rating,
            tags,
        )
        error = None if csrf_is_valid(request, csrf_token) else "Your form expired. Please try again."
        error = error or _validate_problem(values)
        if not error and db.get(Problem, str(values["id"])):
            error = "That problem ID already exists."
        if error:
            return render(
                request,
                templates,
                "admin/problem_form.html",
                user=admin,
                status_code=400,
                context={"error": error, "problem": values, "editing": False},
            )
        problem = Problem(id=str(values["id"]), created_by=admin.id)
        _apply_problem_values(problem, values)
        db.add(problem)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return render(
                request,
                templates,
                "admin/problem_form.html",
                user=admin,
                status_code=400,
                context={"error": "That problem ID already exists.", "problem": values, "editing": False},
            )
        set_flash(request, f"Problem {problem.id} created.", "success")
        return RedirectResponse("/admin/problems", status_code=303)

    @router.get("/problems/{problem_id}/edit", name="admin_problem_edit")
    def problem_edit_form(
        problem_id: str,
        request: Request,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        problem = db.get(Problem, problem_id)
        if problem is None:
            raise HTTPException(status_code=404, detail="Problem not found")
        problem.tags_text = ", ".join(problem.tags or [])
        return render(
            request,
            templates,
            "admin/problem_form.html",
            user=admin,
            context={"problem": problem, "editing": True},
        )

    @router.post("/problems/{problem_id}/edit")
    def problem_update(
        problem_id: str,
        request: Request,
        title: str = Form(...),
        statement: str = Form(...),
        input_specification: str = Form(""),
        output_specification: str = Form(""),
        notes: str = Form(""),
        time_limit_ms: int = Form(2000),
        memory_limit_mb: int = Form(256),
        source: str = Form(""),
        source_id: str = Form(""),
        source_url: str = Form(""),
        rating: Optional[str] = Form(None),
        tags: str = Form(""),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        problem = db.get(Problem, problem_id)
        if problem is None:
            raise HTTPException(status_code=404, detail="Problem not found")
        values = _problem_values(
            problem_id,
            title,
            statement,
            input_specification,
            output_specification,
            notes,
            time_limit_ms,
            memory_limit_mb,
            source,
            source_id,
            source_url,
            rating,
            tags,
        )
        error = None if csrf_is_valid(request, csrf_token) else "Your form expired. Please try again."
        error = error or _validate_problem(values)
        if error:
            return render(
                request,
                templates,
                "admin/problem_form.html",
                user=admin,
                status_code=400,
                context={"error": error, "problem": values, "editing": True},
            )
        _apply_problem_values(problem, values)
        db.commit()
        set_flash(request, f"Problem {problem.id} updated.", "success")
        return RedirectResponse("/admin/problems", status_code=303)

    @router.post("/problems/{problem_id}/delete")
    def problem_delete(
        problem_id: str,
        request: Request,
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        if not csrf_is_valid(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid CSRF token")
        problem = db.get(Problem, problem_id)
        if problem is None:
            raise HTTPException(status_code=404, detail="Problem not found")
        db.delete(problem)
        db.commit()
        set_flash(request, f"Problem {problem_id} deleted.", "success")
        return RedirectResponse("/admin/problems", status_code=303)

    @router.get("/users", name="admin_users")
    def user_admin_list(
        request: Request,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        users = db.scalars(select(User).order_by(User.created_at)).all()
        return render(
            request,
            templates,
            "admin/users.html",
            user=admin,
            context={"users": users},
        )

    @router.post("/users/{user_id}")
    def user_update(
        user_id: int,
        request: Request,
        role: str = Form(...),
        is_active: Optional[str] = Form(None),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ):
        if not csrf_is_valid(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid CSRF token")
        target = db.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            new_role = UserRole(role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid role") from exc
        new_active = is_active == "on"
        removes_active_admin = (
            target.role is UserRole.ADMIN
            and target.is_active
            and (new_role is not UserRole.ADMIN or not new_active)
        )
        if target.id == admin.id and (new_role is not UserRole.ADMIN or not new_active):
            set_flash(request, "You cannot remove your own active admin access.", "error")
            return RedirectResponse("/admin/users", status_code=303)
        if removes_active_admin:
            active_admins = db.scalar(
                select(func.count()).select_from(User).where(
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            ) or 0
            if active_admins <= 1:
                set_flash(request, "At least one active admin is required.", "error")
                return RedirectResponse("/admin/users", status_code=303)
        target.role = new_role
        target.is_active = new_active
        db.commit()
        set_flash(request, f"User {target.username} updated.", "success")
        return RedirectResponse("/admin/users", status_code=303)

    return router

