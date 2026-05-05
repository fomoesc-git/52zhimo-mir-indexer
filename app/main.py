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
from app.exporter import export_daily_csv, export_daily_xlsx
from app.repository import (
    confirm_discovered_publisher,
    count_no_publisher_resources,
    dashboard_stats,
    get_daily_update,
    list_daily_update_items,
    list_daily_updates,
    list_missing_resources,
    list_publisher_options,
    list_publishers,
    list_resources,
    recent_runs,
)
from app.tasks import ensure_scheduler, get_daily_schedule, pause_job, resume_job, start_job, state, update_daily_schedule


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
    if kind not in {"full", "publishers", "news", "repair", "publisher_index"}:
        return RedirectResponse("/", status_code=303)
    start_job(kind)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/publisher/{publisher_id}")
@login_required
def start_publisher_crawl(request: Request, publisher_id: int):
    start_job("publisher", publisher_id=publisher_id)
    return RedirectResponse("/publishers", status_code=303)


@app.post("/publishers/{publisher_id}/confirm")
@login_required
def confirm_publisher(request: Request, publisher_id: int):
    confirm_discovered_publisher(publisher_id)
    return RedirectResponse("/publishers?status=discovered", status_code=303)


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
    publisher_url: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    limit = 50
    offset = (page - 1) * limit
    rows, total = list_resources(
        q=q,
        publisher=publisher,
        publisher_url=publisher_url,
        limit=limit,
        offset=offset,
    )
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
            "publisher_url": publisher_url or "",
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
            "no_publisher_count": count_no_publisher_resources(),
            "status": status or "",
            "task": state,
            "active": "publishers",
        },
    )


@app.get("/updates")
@login_required
def updates(request: Request):
    return templates.TemplateResponse(
        "updates.html",
        {
            "request": request,
            "settings": settings,
            "rows": list_daily_updates(),
            "schedule": get_daily_schedule(),
            "task": state,
            "active": "updates",
        },
    )


@app.post("/updates/schedule")
@login_required
def update_news_schedule(
    request: Request,
    enabled: str | None = Form(default=None),
    hour: int = Form(...),
    minute: int = Form(...),
):
    update_daily_schedule(enabled == "1", hour, minute)
    return RedirectResponse("/updates", status_code=303)


@app.get("/updates/{update_id}")
@login_required
def update_detail(request: Request, update_id: int):
    update = get_daily_update(update_id)
    if not update:
        return RedirectResponse("/updates", status_code=303)
    return templates.TemplateResponse(
        "update_detail.html",
        {
            "request": request,
            "settings": settings,
            "update": update,
            "rows": list_daily_update_items(update_id),
            "task": state,
            "active": "updates",
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


@app.get("/updates/{update_id}/export/{kind}")
@login_required
def download_update_export(request: Request, update_id: int, kind: str):
    if kind == "csv":
        path = export_daily_csv(update_id)
        media_type = "text/csv"
    elif kind == "xlsx":
        path = export_daily_xlsx(update_id)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        return RedirectResponse(f"/updates/{update_id}", status_code=303)
    return FileResponse(path=Path(path), media_type=media_type, filename=Path(path).name)
