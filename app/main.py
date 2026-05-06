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
    count_publishers,
    dashboard_stats,
    get_daily_update,
    list_crawl_errors,
    list_crawl_runs,
    list_daily_update_items,
    list_daily_updates,
    list_missing_resources,
    list_publisher_options,
    list_publishers,
    list_resources,
    recent_runs,
)
from app.tasks import (
    ensure_scheduler,
    get_daily_schedule,
    pause_job,
    resume_job,
    start_job,
    state,
    stop_job,
    update_daily_schedule,
)


settings = get_settings()
app = FastAPI(title=settings.app_name)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

PAGE_SIZE_OPTIONS = ("20", "50", "100", "all")


def parse_page_size(value: str, default: int = 50) -> tuple[int, str]:
    if value == "all":
        return 1_000_000, "all"
    try:
        number = int(value)
    except ValueError:
        number = default
    if number not in {20, 50, 100}:
        number = default
    return number, str(number)


def page_count_for(total: int, limit: int) -> int:
    return max((total + limit - 1) // limit, 1)


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


@app.post("/jobs/publisher-queue")
@login_required
def start_publisher_queue(
    request: Request,
    publisher_ids: list[int] = Form(default=[]),
    task_interval_seconds: float = Form(default=60),
    request_delay_seconds: float = Form(default=5),
):
    start_job(
        "publisher_queue",
        publisher_ids=publisher_ids,
        task_interval_seconds=task_interval_seconds,
        request_delay_seconds=request_delay_seconds,
    )
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


@app.post("/jobs/stop")
@login_required
def stop_crawl(request: Request):
    stop_job()
    return RedirectResponse("/", status_code=303)


@app.get("/resources")
@login_required
def resources(
    request: Request,
    q: str | None = Query(default=None),
    publisher: str | None = Query(default=None),
    publisher_url: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: str = Query(default="50"),
):
    limit, per_page = parse_page_size(per_page, 50)
    offset = (page - 1) * limit
    rows, total = list_resources(
        q=q,
        publisher=publisher,
        publisher_url=publisher_url,
        limit=limit,
        offset=offset,
    )
    page_count = page_count_for(total, limit)
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
            "per_page": per_page,
            "page_size_options": PAGE_SIZE_OPTIONS,
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
def missing_resources(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: str = Query(default="100"),
):
    limit, per_page = parse_page_size(per_page, 100)
    offset = (page - 1) * limit
    rows, total = list_missing_resources(limit=limit, offset=offset)
    page_count = page_count_for(total, limit)
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
            "per_page": per_page,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "task": state,
            "active": "resources",
        },
    )


@app.get("/publishers")
@login_required
def publishers(
    request: Request,
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: str = Query(default="50"),
    sort: str = Query(default="status"),
    order: str = Query(default="desc"),
):
    if sort not in {"name", "status", "progress"}:
        sort = "status"
    if order not in {"asc", "desc"}:
        order = "desc"
    limit, per_page = parse_page_size(per_page, 50)
    total = count_publishers(status)
    page_count = page_count_for(total, limit)
    page = min(page, page_count)
    offset = (page - 1) * limit
    no_publisher_count = count_no_publisher_resources()
    return templates.TemplateResponse(
        "publishers.html",
        {
            "request": request,
            "settings": settings,
            "rows": list_publishers(
                limit=limit,
                status=status,
                offset=offset,
                sort=sort,
                order=order,
            ),
            "total": total,
            "page": page,
            "page_count": page_count,
            "limit": limit,
            "per_page": per_page,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "sort": sort,
            "order": order,
            "next_status_order": "asc" if sort == "status" and order == "desc" else "desc",
            "next_progress_order": "asc" if sort == "progress" and order == "desc" else "desc",
            "no_publisher_count": no_publisher_count,
            "show_no_publisher": bool(no_publisher_count and (not status or status == "confirmed") and page == 1),
            "status": status or "",
            "task": state,
            "active": "publishers",
        },
    )


@app.get("/logs")
@login_required
def logs(request: Request, run_id: int | None = Query(default=None)):
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "settings": settings,
            "runs": list_crawl_runs(),
            "errors": list_crawl_errors(run_id=run_id),
            "run_id": run_id,
            "task": state,
            "active": "logs",
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
