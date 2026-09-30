"""Registration and browser-session authentication routes."""

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
from ..models import User, UserRole
from ..security import (
    csrf_is_valid,
    hash_password,
    safe_next_url,
    set_flash,
    validate_email,
    validate_password,
    validate_username,
    verify_password,
)
from ..web import render


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/register", name="register")
    def register_form(
        request: Request,
        user: Optional[User] = Depends(current_user),
    ):
        if user:
            return RedirectResponse("/", status_code=303)
        return render(request, templates, "auth/register.html")

    @router.post("/register")
    def register(
        request: Request,
        username: str = Form(...),
        email: str = Form(...),
        password: str = Form(...),
        password_confirm: str = Form(...),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
    ):
        username = username.strip()
        email = email.strip().lower()
        error = None
        if not csrf_is_valid(request, csrf_token):
            error = "Your form expired. Please try again."
        else:
            error = validate_username(username) or validate_email(email) or validate_password(password)
        if not error and password != password_confirm:
            error = "Passwords do not match."
        if not error:
            existing = db.scalar(select(User).where(or_(User.username == username, User.email == email)))
            if existing:
                error = "That username or email is already registered."
        if error:
            return render(
                request,
                templates,
                "auth/register.html",
                status_code=400,
                context={"error": error, "username": username, "email": email},
            )

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.USER,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return render(
                request,
                templates,
                "auth/register.html",
                status_code=400,
                context={
                    "error": "That username or email is already registered.",
                    "username": username,
                    "email": email,
                },
            )
        db.refresh(user)
        request.session.clear()
        request.session["user_id"] = user.id
        set_flash(request, "Welcome to CodeHarness.", "success")
        return RedirectResponse("/", status_code=303)

    @router.get("/login", name="login")
    def login_form(
        request: Request,
        next: Optional[str] = None,
        user: Optional[User] = Depends(current_user),
    ):
        if user:
            return RedirectResponse("/", status_code=303)
        return render(
            request,
            templates,
            "auth/login.html",
            context={"next_url": safe_next_url(next)},
        )

    @router.post("/login")
    def login(
        request: Request,
        identity: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
        next_url: str = Form("/"),
        db: Session = Depends(get_db),
    ):
        identity = identity.strip()
        if not csrf_is_valid(request, csrf_token):
            error = "Your form expired. Please try again."
        else:
            user = db.scalar(
                select(User).where(
                    or_(User.username == identity, User.email == identity.lower())
                )
            )
            if user is None or not user.is_active or not verify_password(user.password_hash, password):
                error = "Invalid username/email or password."
            else:
                request.session.clear()
                request.session["user_id"] = user.id
                set_flash(request, "Signed in successfully.", "success")
                return RedirectResponse(safe_next_url(next_url), status_code=303)

        return render(
            request,
            templates,
            "auth/login.html",
            status_code=400,
            context={"error": error, "identity": identity, "next_url": safe_next_url(next_url)},
        )

    @router.post("/logout", name="logout")
    def logout(request: Request, csrf_token: str = Form(...)):
        if not csrf_is_valid(request, csrf_token):
            return render(
                request,
                templates,
                "error.html",
                status_code=400,
                context={"title": "Invalid request", "detail": "Your form expired."},
            )
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    return router

