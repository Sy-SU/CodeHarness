"""Public OJ pages."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import current_user
from ..models import Problem, User
from ..web import render


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", name="home")
    def home(
        request: Request,
        db: Session = Depends(get_db),
        user: Optional[User] = Depends(current_user),
    ):
        recent = db.scalars(select(Problem).order_by(Problem.created_at.desc()).limit(5)).all()
        return render(
            request,
            templates,
            "home.html",
            user=user,
            context={"recent_problems": recent},
        )

    @router.get("/problems", name="problem_list")
    def problem_list(
        request: Request,
        db: Session = Depends(get_db),
        user: Optional[User] = Depends(current_user),
    ):
        problems = db.scalars(select(Problem).order_by(Problem.id)).all()
        return render(
            request,
            templates,
            "problems.html",
            user=user,
            context={"problems": problems},
        )

    @router.get("/problems/{problem_id}", name="problem_detail")
    def problem_detail(
        problem_id: str,
        request: Request,
        db: Session = Depends(get_db),
        user: Optional[User] = Depends(current_user),
    ):
        problem = db.get(Problem, problem_id)
        if problem is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")
        return render(
            request,
            templates,
            "problem_detail.html",
            user=user,
            context={"problem": problem},
        )

    return router
