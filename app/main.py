from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db
from app.exporter import export_csv, export_xlsx
from app.repository import dashboard_stats, list_publishers, list_resources, recent_runs
from app.tasks import ensure_scheduler, start_job, state


settings = get_settings()
app = FastAPI(title=settings.app_name)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    ensure_scheduler()


@app.get("/")
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
def start_crawl(kind: str = Form(...)):
    if kind not in {"full", "publishers", "news"}:
        return RedirectResponse("/", status_code=303)
    start_job(kind)
    return RedirectResponse("/", status_code=303)


@app.get("/resources")
def resources(
    request: Request,
    q: str | None = Query(default=None),
    publisher: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    limit = 50
    offset = (page - 1) * limit
    rows, total = list_resources(q=q, publisher=publisher, limit=limit, offset=offset)
    return templates.TemplateResponse(
        "resources.html",
        {
            "request": request,
            "settings": settings,
            "rows": rows,
            "total": total,
            "page": page,
            "limit": limit,
            "q": q or "",
            "publisher": publisher or "",
            "task": state,
            "active": "resources",
        },
    )


@app.get("/publishers")
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
def download_export(kind: str):
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
