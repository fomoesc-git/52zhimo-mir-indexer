from __future__ import annotations

import threading
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.crawler import CrawlStats, run_job_sync


@dataclass
class TaskState:
    running: bool = False
    current_kind: str | None = None
    last_kind: str | None = None
    last_status: str | None = None
    last_message: str | None = None


state = TaskState()
lock = threading.Lock()
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def start_job(kind: str) -> bool:
    with lock:
        if state.running:
            return False
        state.running = True
        state.current_kind = kind
        state.last_status = None
        state.last_message = None

    thread = threading.Thread(target=_run_job_thread, args=(kind,), daemon=True)
    thread.start()
    return True


def _run_job_thread(kind: str) -> None:
    try:
        stats: CrawlStats = run_job_sync(kind)
        message = (
            f"出版商 {stats.publishers_seen}，链接 {stats.resource_links_seen}，"
            f"新增资源 {stats.resources_created}，更新资源 {stats.resources_updated}，"
            f"新增出版商 {stats.publishers_created}，错误 {stats.errors}"
        )
        status = "ok" if stats.errors == 0 else "warning"
    except Exception as exc:
        status = "failed"
        message = str(exc)
    with lock:
        state.running = False
        state.last_kind = kind
        state.current_kind = None
        state.last_status = status
        state.last_message = message


def ensure_scheduler() -> None:
    if scheduler.running:
        return
    settings = get_settings()
    scheduler.add_job(
        lambda: start_job("news"),
        CronTrigger(hour=settings.daily_check_hour, minute=settings.daily_check_minute),
        id="daily-news-check",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
