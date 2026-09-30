"""Shared server-rendered response helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from .models import User
from .security import csrf_token, pop_flash


def render(
    request: Request,
    templates: Jinja2Templates,
    name: str,
    *,
    user: Optional[User] = None,
    status_code: int = 200,
    context: Optional[Dict[str, Any]] = None,
) -> Response:
    """Render a page with the common browser context."""

    values: Dict[str, Any] = {
        "request": request,
        "current_user": user,
        "csrf_token": csrf_token(request),
        "flash": pop_flash(request),
    }
    if context:
        values.update(context)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=values,
        status_code=status_code,
    )

