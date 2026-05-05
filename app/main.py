from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE_NAME, create_session_token, is_authenticated, login_required, verify_credentials
from app.config import get_settings
from app.db import init_db
from app.exporter import export_csv, export_xlsx
from app.repository import (
    dashboard_stats,
    list_missing_resources,
    list_publisher_options,
    list_publishers,
    list_resources,
    recent_runs,
)
from app.tasks import ensure_scheduler, pause_job, resume_job, start_job, state


settings = get_settings()
app = FastAPI(title=settings.app_name)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    ensure_scheduler()


@app.get("/login")
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "settings": settings,
            "error": "",
            "active": "login",
        },
    )


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "settings": settings,
                "error": "账号或密码不正确",
                "active": "login",
            },
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/")
@login_required
def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "settings": settings,
            "stats": dashboard_stats(),
            "runs": recent_runs(),
            "task": state,
            "active": "dashboard",
        },
    )


@app.post("/jobs/start")
@login_required
def start_crawl(request: Request, kind: str = Form(...)):
    if kind not in {"full", "publishers", "news", "repair"}:
        return RedirectResponse("/", status_code=303)
    start_job(kind)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/pause")
@login_required
def pause_crawl(request: Request):
    pause_job()
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/resume")
@login_required
def resume_crawl(request: Request):
    resume_job()
    return RedirectResponse("/", status_code=303)


@app.get("/resources")
@login_required
def resources(
    request: Request,
    q: str | None = Query(default=None),
    publisher: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    limit = 50
    offset = (page - 1) * limit
    rows, total = list_resources(q=q, publisher=publisher, limit=limit, offset=offset)
    page_count = max((total + limit - 1) // limit, 1)
    return templates.TemplateResponse(
        "resources.html",
        {
            "request": request,
            "settings": settings,
            "rows": rows,
            "total": total,
            "page": page,
            "page_count": page_count,
            "limit": limit,
            "q": q or "",
            "publisher": publisher or "",
            "publisher_options": list_publisher_options(),
            "task": state,
            "active": "resources",
        },
    )


@app.get("/resources/missing")
@login_required
def missing_resources(request: Request, page: int = Query(default=1, ge=1)):
    limit = 100
    offset = (page - 1) * limit
    rows, total = list_missing_resources(limit=limit, offset=offset)
    page_count = max((total + limit - 1) // limit, 1)
    return templates.TemplateResponse(
        "missing_resources.html",
        {
            "request": request,
            "settings": settings,
            "rows": rows,
            "total": total,
            "page": page,
            "page_count": page_count,
            "limit": limit,
            "task": state,
            "active": "resources",
        },
    )


@app.get("/publishers")
@login_required
def publishers(request: Request, status: str | None = Query(default=None)):
    return templates.TemplateResponse(
        "publishers.html",
        {
            "request": request,
            "settings": settings,
            "rows": list_publishers(limit=10000, status=status),
            "status": status or "",
            "task": state,
            "active": "publishers",
        },
    )


@app.get("/exports/{kind}")
@login_required
def download_export(request: Request, kind: str):
    if kind == "csv":
        path = export_csv()
        media_type = "text/csv"
    elif kind == "xlsx":
        path = export_xlsx()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        return RedirectResponse("/", status_code=303)
    return FileResponse(
        path=Path(path),
        media_type=media_type,
        filename=Path(path).name,
    )
