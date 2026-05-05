from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.crawler import CrawlStats, run_job_sync


@dataclass
class TaskState:
    running: bool = False
    paused: bool = False
    current_kind: str | None = None
    stage: str = "空闲"
    current_item: str = ""
    current_url: str = ""
    publishers_seen: int = 0
    resource_links_seen: int = 0
    resources_created: int = 0
    resources_updated: int = 0
    publishers_created: int = 0
    errors: int = 0
    last_kind: str | None = None
    last_status: str | None = None
    last_message: str | None = None


state = TaskState()
lock = threading.Lock()
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def start_job(kind: str) -> bool:
    if kind not in {"full", "publishers", "news", "repair"}:
        return False
    with lock:
        if state.running:
            return False
        state.running = True
        state.paused = False
        state.current_kind = kind
        state.stage = "准备开始"
        state.current_item = ""
        state.current_url = ""
        state.publishers_seen = 0
        state.resource_links_seen = 0
        state.resources_created = 0
        state.resources_updated = 0
        state.publishers_created = 0
        state.errors = 0
        state.last_status = None
        state.last_message = None

    thread = threading.Thread(target=_run_job_thread, args=(kind,), daemon=True)
    thread.start()
    return True


def _run_job_thread(kind: str) -> None:
    try:
        stats: CrawlStats = run_job_sync(kind, progress=update_progress, pause_checker=wait_if_paused)
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
        state.paused = False
        state.last_kind = kind
        state.current_kind = None
        state.stage = "已结束"
        state.current_item = ""
        state.current_url = ""
        state.last_status = status
        state.last_message = message


def update_progress(stats: CrawlStats, stage: str, current_item: str = "", current_url: str = "") -> None:
    with lock:
        state.stage = stage
        state.current_item = current_item
        state.current_url = current_url
        state.publishers_seen = stats.publishers_seen
        state.resource_links_seen = stats.resource_links_seen
        state.resources_created = stats.resources_created
        state.resources_updated = stats.resources_updated
        state.publishers_created = stats.publishers_created
        state.errors = stats.errors


def pause_job() -> bool:
    with lock:
        if not state.running:
            return False
        state.paused = True
        state.stage = "已暂停，等待恢复"
        return True


def resume_job() -> bool:
    with lock:
        if not state.running:
            return False
        state.paused = False
        state.stage = "恢复中"
        return True


def wait_if_paused() -> None:
    while True:
        with lock:
            paused = state.paused
            running = state.running
        if not running or not paused:
            return
        time.sleep(1)


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
