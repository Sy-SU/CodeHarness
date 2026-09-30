"""Authenticated user profile and password settings."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import current_user
from ..models import User
from ..security import (
    csrf_is_valid,
    hash_password,
    set_flash,
    validate_email,
    validate_password,
    validate_username,
    verify_password,
)
from ..web import render


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def login_redirect() -> RedirectResponse:
        return RedirectResponse("/login?next=/settings", status_code=303)

    @router.get("/settings", name="settings")
    def settings_page(
        request: Request,
        user: Optional[User] = Depends(current_user),
    ):
        if user is None:
            return login_redirect()
        return render(request, templates, "settings/index.html", user=user)

    @router.post("/settings/profile")
    def update_profile(
        request: Request,
        username: str = Form(...),
        email: str = Form(...),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
        user: Optional[User] = Depends(current_user),
    ):
        if user is None:
            return login_redirect()
        username = username.strip()
        email = email.strip().lower()
        error = None
        if not csrf_is_valid(request, csrf_token):
            error = "Your form expired. Please try again."
        else:
            error = validate_username(username) or validate_email(email)
        if not error:
            duplicate = db.scalar(
                select(User).where(
                    User.id != user.id,
                    or_(User.username == username, User.email == email),
                )
            )
            if duplicate:
                error = "That username or email is already registered."
        if error:
            return render(
                request,
                templates,
                "settings/index.html",
                user=user,
                status_code=400,
                context={"profile_error": error},
            )
        user.username = username
        user.email = email
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return render(
                request,
                templates,
                "settings/index.html",
                user=user,
                status_code=400,
                context={"profile_error": "That username or email is already registered."},
            )
        set_flash(request, "Profile updated.", "success")
        return RedirectResponse("/settings", status_code=303)

    @router.post("/settings/password")
    def update_password(
        request: Request,
        current_password: str = Form(...),
        new_password: str = Form(...),
        new_password_confirm: str = Form(...),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
        user: Optional[User] = Depends(current_user),
    ):
        if user is None:
            return login_redirect()
        error = None
        if not csrf_is_valid(request, csrf_token):
            error = "Your form expired. Please try again."
        elif not verify_password(user.password_hash, current_password):
            error = "Current password is incorrect."
        else:
            error = validate_password(new_password)
        if not error and new_password != new_password_confirm:
            error = "New passwords do not match."
        if error:
            return render(
                request,
                templates,
                "settings/index.html",
                user=user,
                status_code=400,
                context={"password_error": error},
            )
        user.password_hash = hash_password(new_password)
        db.commit()
        set_flash(request, "Password updated.", "success")
        return RedirectResponse("/settings", status_code=303)

    return router

