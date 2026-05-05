from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.crawler import CrawlStats, run_job_sync
from app.repository import get_state_value, set_state_value


@dataclass
class TaskState:
    running: bool = False
    paused: bool = False
    current_kind: str | None = None
    current_publisher_id: int | None = None
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
    queue_total: int = 0
    queue_interval_seconds: float = 0
    request_delay_seconds: float | None = None


state = TaskState()
lock = threading.Lock()
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
DAILY_JOB_ID = "daily-news-check"


def start_job(
    kind: str,
    publisher_id: int | None = None,
    publisher_ids: list[int] | None = None,
    task_interval_seconds: float = 0,
    request_delay_seconds: float | None = None,
) -> bool:
    allowed_kinds = {"full", "publishers", "news", "repair", "publisher_index", "publisher", "publisher_queue"}
    if kind not in allowed_kinds:
        return False
    if kind == "publisher" and not publisher_id:
        return False
    if kind == "publisher_queue" and not publisher_ids:
        return False
    task_interval_seconds = max(0.0, min(float(task_interval_seconds or 0), 3600.0))
    if request_delay_seconds is not None:
        request_delay_seconds = max(0.5, min(float(request_delay_seconds), 120.0))
    with lock:
        if state.running:
            return False
        state.running = True
        state.paused = False
        state.current_kind = kind
        state.current_publisher_id = publisher_id
        state.stage = "准备开始"
        state.current_item = ""
        state.current_url = ""
        state.publishers_seen = 0
        state.resource_links_seen = 0
        state.resources_created = 0
        state.resources_updated = 0
        state.publishers_created = 0
        state.errors = 0
        state.queue_total = len(publisher_ids or [])
        state.queue_interval_seconds = task_interval_seconds
        state.request_delay_seconds = request_delay_seconds
        state.last_status = None
        state.last_message = None

    thread = threading.Thread(
        target=_run_job_thread,
        args=(kind, publisher_id, publisher_ids or [], task_interval_seconds, request_delay_seconds),
        daemon=True,
    )
    thread.start()
    return True


def _run_job_thread(
    kind: str,
    publisher_id: int | None = None,
    publisher_ids: list[int] | None = None,
    task_interval_seconds: float = 0,
    request_delay_seconds: float | None = None,
) -> None:
    try:
        stats: CrawlStats = run_job_sync(
            kind,
            progress=update_progress,
            pause_checker=wait_if_paused,
            publisher_id=publisher_id,
            publisher_ids=publisher_ids,
            task_interval_seconds=task_interval_seconds,
            request_delay_seconds=request_delay_seconds,
            request_jitter_seconds=0 if request_delay_seconds is not None else None,
        )
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
        state.current_publisher_id = None
        state.stage = "已结束"
        state.current_item = ""
        state.current_url = ""
        state.queue_total = 0
        state.queue_interval_seconds = 0
        state.request_delay_seconds = None
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
    if not scheduler.running:
        scheduler.start()
    configure_daily_news_job()


def default_daily_schedule() -> dict[str, int | bool]:
    settings = get_settings()
    return {
        "enabled": True,
        "hour": settings.daily_check_hour,
        "minute": settings.daily_check_minute,
    }


def get_daily_schedule() -> dict[str, int | bool | str | None]:
    defaults = default_daily_schedule()
    enabled = get_state_value("daily_news_enabled")
    hour = get_state_value("daily_news_hour")
    minute = get_state_value("daily_news_minute")
    schedule = {
        "enabled": parse_bool(enabled, bool(defaults["enabled"])),
        "hour": parse_int(hour, int(defaults["hour"]), 0, 23),
        "minute": parse_int(minute, int(defaults["minute"]), 0, 59),
        "next_run_at": None,
    }
    job = scheduler.get_job(DAILY_JOB_ID) if scheduler.running else None
    if job and job.next_run_time:
        schedule["next_run_at"] = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return schedule


def update_daily_schedule(enabled: bool, hour: int, minute: int) -> None:
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    set_state_value("daily_news_enabled", "1" if enabled else "0")
    set_state_value("daily_news_hour", str(hour))
    set_state_value("daily_news_minute", str(minute))
    configure_daily_news_job()


def configure_daily_news_job() -> None:
    schedule = get_daily_schedule()
    if scheduler.get_job(DAILY_JOB_ID):
        scheduler.remove_job(DAILY_JOB_ID)
    if not schedule["enabled"]:
        return
    scheduler.add_job(
        lambda: start_job("news"),
        CronTrigger(hour=int(schedule["hour"]), minute=int(schedule["minute"])),
        id=DAILY_JOB_ID,
        replace_existing=True,
        max_instances=1,
    )


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value) if value is not None else default
    except ValueError:
        number = default
    return max(minimum, min(maximum, number))
