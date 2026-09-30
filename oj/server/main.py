"""FastAPI application factory and development server entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .database import Base, build_engine, build_session_factory
from .routes import admin, auth, public, settings


SERVER_ROOT = Path(__file__).resolve().parent


def create_app(settings_override: Optional[Settings] = None) -> FastAPI:
    """Create an isolated OJ web application instance."""

    runtime_settings = settings_override or Settings.from_env()
    engine = build_engine(runtime_settings.database_url)
    Base.metadata.create_all(engine)

    app = FastAPI(title="CodeHarness OJ", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.settings = runtime_settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.add_middleware(
        SessionMiddleware,
        secret_key=runtime_settings.secret_key,
        session_cookie=runtime_settings.session_cookie,
        same_site="lax",
        https_only=runtime_settings.session_https_only,
        max_age=60 * 60 * 24 * 14,
    )

    templates = Jinja2Templates(directory=str(SERVER_ROOT / "templates"))
    app.mount("/static", StaticFiles(directory=str(SERVER_ROOT / "static")), name="static")
    app.include_router(public.build_router(templates))
    app.include_router(auth.build_router(templates))
    app.include_router(settings.build_router(templates))
    app.include_router(admin.build_router(templates))

    @app.get("/healthz", include_in_schema=False)
    def healthcheck() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.exception_handler(404)
    async def not_found(request: Request, _exc: Exception):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "request": request,
                "current_user": None,
                "csrf_token": "",
                "flash": None,
                "title": "Not found",
                "detail": "The requested page does not exist.",
            },
            status_code=404,
        )

    return app


app = create_app()


def run() -> None:
    """Run the development ASGI server."""

    uvicorn.run("oj.server.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
