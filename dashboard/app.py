"""Local SSR/read APIs plus explicitly confirmed, same-origin task control."""
from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from jinja2 import pass_context

from .config import DashboardSettings
from .i18n import COOKIE_NAME, LANGUAGES, ZH, local_redirect, presentation_message, translate
from .repository.configuration import ConfigurationRepository
from .repository.trace import TraceReader
from .repository.workspace import UnsafePath, WorkspaceIndex, WorkspaceRepository
from .security import Sanitizer
from .services.metrics import aggregate, experiments
from .services.tasks import TaskService
from .repository.contests import ContestRepository


PACKAGE = Path(__file__).parent


def display(value):
    return "Unknown" if value is None or value == "" else value


def duration(value):
    return "Unknown" if value is None else f"{value:,.3f} s"


def timestamp(value):
    if not value:
        return "Unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S") + " (zone unrecorded)"
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, AttributeError):
        return value


def create_app(settings: Optional[DashboardSettings] = None, *, launcher=None) -> FastAPI:
    settings = settings or DashboardSettings()
    sanitizer = Sanitizer(env_file=settings.env_file)
    repository = WorkspaceRepository(settings.workspace_root, sanitizer)
    index, traces = WorkspaceIndex(repository), TraceReader(repository)
    tasks = TaskService(repository, index, traces)
    contests = ContestRepository(repository)
    configurations = ConfigurationRepository(settings.model_config, sanitizer)
    @asynccontextmanager
    async def lifespan(app):
        yield
        if app.state.launcher is not None:
            app.state.launcher.close()

    app = FastAPI(title="CodeHarness Local Dashboard", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.state.repository, app.state.index, app.state.traces, app.state.tasks = repository, index, traces, tasks
    if settings.enable_launch and launcher is None:
        from .services.launcher import LaunchService
        launcher = LaunchService(settings)
    app.state.launcher = launcher if settings.enable_launch else None
    csrf_token = secrets.token_urlsafe(32)
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    @pass_context
    def localized_display(context, value):
        return context["t"]("Unknown") if value is None or value == "" else value

    @pass_context
    def localized_duration(context, value):
        return context["t"]("Unknown") if value is None else duration(value)

    @pass_context
    def localized_timestamp(context, value):
        result = timestamp(value)
        return context["t"]("Unknown") if result == "Unknown" else result.replace("(zone unrecorded)", context["t"]("(zone unrecorded)"))

    @pass_context
    def localized_message(context, value):
        return presentation_message(value, context["lang"])

    templates.env.filters.update(display=localized_display, duration=localized_duration,
                                 timestamp=localized_timestamp, message=localized_message,
                                 pretty=lambda value: json.dumps(value, ensure_ascii=False, indent=2))

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def detail(task_id):
        try:
            return tasks.detail(task_id)
        except (OSError, UnsafePath):
            raise HTTPException(404, "Task unavailable")

    def contest_detail(run_id):
        try:
            value = contests.detail(run_id)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            raise HTTPException(404, "Contest run unavailable")
        if app.state.launcher is not None:
            value["launch_job"] = app.state.launcher.contest_job(run_id)
            value["resumable"] = app.state.launcher.can_resume_contest(run_id)
            if (value["status"] in {"queued", "running", "fetching"}
                    and value["launch_job"] and value["launch_job"]["status"] in {"interrupted", "failed"}):
                value["status"] = value["launch_job"]["status"]
        return sanitizer.value(value)

    def page(request, name, **context):
        language = request.cookies.get(COOKIE_NAME, "en")
        if language not in LANGUAGES:
            language = "en"
        current = request.url.path + ("?" + request.url.query if request.url.query else "")
        return templates.TemplateResponse(request=request, name=name,
            context={"active": name.split(".")[0], "lang": language,
                     "launch_enabled": settings.enable_launch, "csrf_token": csrf_token,
                     "t": lambda message, **values: translate(message, language, **values),
                     "translations": ZH if language == "zh" else {},
                     "language_links": {lang: f"/language/{lang}?{urlencode({'next': current})}" for lang in sorted(LANGUAGES)},
                     **context})

    @app.get("/language/{language}")
    def switch_language(language: str, next: str = "/"):
        if language not in LANGUAGES:
            raise HTTPException(404, "Language unavailable")
        response = RedirectResponse(local_redirect(next), status_code=303)
        response.set_cookie(COOKIE_NAME, language, max_age=365 * 24 * 60 * 60,
                            httponly=True, samesite="lax", path="/")
        return response

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        return page(request, "launch.html" if settings.enable_launch else "overview.html",
            metrics=aggregate(index.tasks()), recent=tasks.list()[:10], launch_settings=settings,
            default_config=settings.task_defaults() if settings.enable_launch else {})

    @app.get("/contests", response_class=HTMLResponse)
    def contest_page(request: Request):
        return page(request, "launch.html" if settings.enable_launch else "contests.html",
            active="contests", contest_mode=True, metrics=aggregate(index.tasks()),
            recent=tasks.list()[:10], contests=contests.list(),
            launch_settings=settings, default_config=settings.task_defaults() if settings.enable_launch else {})

    @app.get("/contests/{run_id}", response_class=HTMLResponse)
    def contest_report(request: Request, run_id: str):
        return page(request, "contest_detail.html", active="contests", report=contest_detail(run_id))

    @app.get("/api/dashboard/contests")
    def api_contests():
        return contests.list()

    @app.get("/api/dashboard/contests/{run_id}")
    def api_contest(run_id: str):
        return contest_detail(run_id)

    @app.get("/tasks", response_class=HTMLResponse)
    def task_list(request: Request, q: str = "", task_id: str = "", problem_id: str = "", mode: str = "",
                  profile: str = "", status: str = "", verdict: str = "", sort: str = "updated"):
        filters = dict(q=q, task_id=task_id, problem_id=problem_id, mode=mode, profile=profile, status=status, verdict=verdict, sort=sort)
        all_tasks = index.tasks()
        options = {field: sorted({getattr(task, field) or "unknown" for task in all_tasks})
                   for field in ("mode", "profile", "status")}
        options["verdict"] = sorted({task.final_verdict or "Unknown" for task in all_tasks} | {"AC", "WA", "CE", "RE", "TLE", "MLE", "OLE", "IE"})
        return page(request, "tasks.html", rows=tasks.list(**filters), filters=filters, options=options)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_detail(request: Request, task_id: str):
        return page(request, "task_detail.html", detail=detail(task_id),
            resumable=bool(app.state.launcher and app.state.launcher.can_resume(task_id)))

    @app.get("/tasks/{task_id}/artifacts/{name:path}", response_class=PlainTextResponse)
    def artifact(task_id: str, name: str):
        try:
            index.task(task_id)
            return repository.artifact(task_id, name)
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise HTTPException(404, "Artifact unavailable or not allowed")

    @app.get("/experiments", response_class=HTMLResponse)
    def experiment_page(request: Request):
        return page(request, "experiments.html", groups=experiments(index.tasks()))

    @app.get("/models", response_class=HTMLResponse)
    def model_page(request: Request):
        return page(request, "models.html", models=configurations.read())

    @app.get("/api/dashboard/summary")
    def api_summary():
        return sanitizer.value(asdict(aggregate(index.tasks())))

    @app.get("/api/dashboard/tasks")
    def api_tasks(q: str = "", task_id: str = "", problem_id: str = "", mode: str = "", profile: str = "",
                  status: str = "", verdict: str = "", sort: str = "updated",
                  offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        rows = tasks.list(q=q, task_id=task_id, problem_id=problem_id, mode=mode, profile=profile,
                          status=status, verdict=verdict, sort=sort)
        return sanitizer.value({"tasks": [asdict(row) for row in rows[offset:offset + limit]], "total": len(rows)})

    @app.get("/api/dashboard/tasks/{task_id}")
    def api_detail(task_id: str):
        # Timeline has its own incremental endpoint and is not retransmitted during polling.
        value = asdict(detail(task_id))
        value.pop("timeline")
        value.pop("judge_results")
        value.pop("state_changes")
        if app.state.launcher is not None:
            value["launch_job"] = app.state.launcher.task_job(task_id)
            value["resumable"] = app.state.launcher.can_resume(task_id)
        return sanitizer.value(value)

    @app.get("/api/dashboard/tasks/{task_id}/events")
    def api_events(task_id: str, cursor: int = Query(0, ge=0), generation: Optional[int] = None,
                   limit: int = Query(500, ge=1, le=1000)):
        try:
            index.task(task_id)
        except (OSError, ValueError):
            raise HTTPException(404, "Task unavailable")
        snapshot = traces.read(task_id)
        reset = (generation is not None and generation != snapshot.generation) or cursor > len(snapshot.events)
        start = 0 if reset else cursor
        events = snapshot.events[start:start + limit]
        return sanitizer.value({"events": [asdict(event) for event in events], "next_cursor": start + len(events),
            "generation": snapshot.generation, "reset": reset, "total": len(snapshot.events), "warnings": snapshot.warnings})

    @app.get("/api/dashboard/experiments")
    def api_experiments():
        return sanitizer.value([asdict(group) for group in experiments(index.tasks())])

    @app.get("/api/dashboard/models")
    def api_models():
        return sanitizer.value(asdict(configurations.read()))

    if settings.enable_launch:
        from .services.launcher import LaunchBusy, LaunchConflict

        async def control_payload(request, *, resume=False, contest=False):
            # Loopback alone is insufficient: reject browser cross-origin/rebinding
            # requests, then require a non-simple header carrying the local CSRF token.
            origin = str(request.base_url).rstrip("/")
            if request.headers.get("Origin") != origin or request.headers.get("Sec-Fetch-Site") not in {None, "same-origin"}:
                raise HTTPException(403, "Local same-origin request required")
            if not secrets.compare_digest(request.headers.get("X-CodeHarness-CSRF", ""), csrf_token):
                raise HTTPException(403, "Invalid local request token")
            if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise HTTPException(415, "JSON required")
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 8192:
                    raise HTTPException(413, "Request too large")
                chunks.append(chunk)
            try:
                value = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError):
                raise HTTPException(422, "Invalid JSON")
            allowed = {"request_id", "confirm_remote_calls"} if resume else {
                "request_id", "confirm_remote_calls", "problem_id", "mode", "profile", "max_cost_cny", "task_config"}
            if contest and not resume:
                allowed = (allowed - {"problem_id"}) | {"contest_id", "total_cost_cny"}
            if not isinstance(value, dict) or set(value) - allowed or value.get("confirm_remote_calls") is not True:
                raise HTTPException(422, "Explicit remote-call confirmation and supported fields required")
            request_id = value.get("request_id")
            import re
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9-]{16,64}", request_id):
                raise HTTPException(422, "A fresh request ID is required")
            return value

        @app.post("/api/dashboard/launch", status_code=202)
        async def launch_task(request: Request):
            payload = await control_payload(request)
            try:
                run = settings.task_request(payload)
                return sanitizer.value(app.state.launcher.enqueue(payload["request_id"], run))
            except LaunchConflict as exc:
                raise HTTPException(409, sanitizer.text(str(exc)))
            except LaunchBusy:
                raise HTTPException(429, "Local task queue is full")
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
                raise HTTPException(422, f"Task/config unavailable ({type(exc).__name__})")

        @app.post("/api/dashboard/contests/launch", status_code=202)
        async def launch_contest(request: Request):
            payload = await control_payload(request, contest=True)
            try:
                return sanitizer.value(app.state.launcher.enqueue_contest(payload["request_id"],
                    settings.contest_request(payload)))
            except LaunchConflict as exc:
                raise HTTPException(409, sanitizer.text(str(exc)))
            except LaunchBusy:
                raise HTTPException(429, "Local task queue is full")
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
                raise HTTPException(422, f"Contest/config unavailable ({type(exc).__name__})")

        @app.post("/api/dashboard/contests/{run_id}/resume", status_code=202)
        async def resume_contest(run_id: str, request: Request):
            payload = await control_payload(request, resume=True)
            try:
                run = app.state.launcher.contest_request(run_id)
                return sanitizer.value(app.state.launcher.enqueue_contest(payload["request_id"], run,
                    resume_run_id=run_id))
            except LaunchConflict as exc:
                raise HTTPException(409, sanitizer.text(str(exc)))
            except LaunchBusy:
                raise HTTPException(429, "Local task queue is full")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise HTTPException(422, f"Contest resume unavailable ({type(exc).__name__})")

        @app.post("/api/dashboard/contests/{run_id}/performance")
        async def refresh_contest_performance(run_id: str, request: Request):
            await control_payload(request, resume=True)
            # This path deliberately never constructs a model registry/launcher job.
            from dotenv import dotenv_values
            from agent.config import ClientSettings
            from experiments.contest import refresh_performance
            from starlette.concurrency import run_in_threadpool
            import os
            values = dict(dotenv_values(settings.env_file)) if settings.env_file else {}
            values.update(os.environ)
            try:
                client_settings = ClientSettings.from_environment(values)
                return sanitizer.value(await run_in_threadpool(refresh_performance,
                    settings.workspace_root, run_id, client_settings))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise HTTPException(422, f"Performance refresh unavailable ({type(exc).__name__})")

        @app.post("/api/dashboard/tasks/{task_id}/resume", status_code=202)
        async def resume_task(task_id: str, request: Request):
            payload = await control_payload(request, resume=True)
            try:
                run = app.state.launcher.resume_request(task_id)
                return sanitizer.value(app.state.launcher.enqueue(payload["request_id"], run,
                                                                 resume_task_id=task_id))
            except LaunchConflict as exc:
                raise HTTPException(409, sanitizer.text(str(exc)))
            except LaunchBusy:
                raise HTTPException(429, "Local task queue is full")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise HTTPException(422, f"Resume unavailable ({type(exc).__name__})")

        @app.get("/api/dashboard/jobs/{request_id}")
        def api_job(request_id: str):
            try:
                return sanitizer.value(app.state.launcher.job(request_id))
            except (OSError, ValueError, KeyError):
                raise HTTPException(404, "Job unavailable")

    return app
