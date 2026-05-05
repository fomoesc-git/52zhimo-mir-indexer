from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from app.client import FetchClient
from app.config import get_settings
from app.models import PublisherRecord
from app.parser import (
    normalize_url,
    parse_detail,
    parse_news_entries,
    parse_publisher_count,
    parse_publisher_index,
    parse_publisher_page_urls,
    parse_publisher_resource_links,
)
from app.repository import (
    add_daily_update_item,
    create_run,
    create_daily_update_window,
    daily_update_seen_urls,
    existing_resources_for_urls,
    finish_daily_update,
    finish_run,
    get_state_value,
    list_publishers,
    log_error,
    mark_publisher_crawled,
    merge_resource,
    missing_resource_urls,
    publisher_by_id,
    set_state_value,
    update_publisher_counts,
    upsert_discovered_publisher,
    upsert_publisher,
    upsert_resource,
)


DAILY_LAST_CHECKED_KEY = "daily_news_last_checked_at"
NEWS_PAGE_LIMIT = 20


@dataclass
class CrawlStats:
    publishers_seen: int = 0
    resource_links_seen: int = 0
    resources_created: int = 0
    resources_updated: int = 0
    publishers_created: int = 0
    errors: int = 0
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "publishers_seen": self.publishers_seen,
            "resource_links_seen": self.resource_links_seen,
            "resources_created": self.resources_created,
            "resources_updated": self.resources_updated,
            "publishers_created": self.publishers_created,
            "errors": self.errors,
        }


class Crawler:
    def __init__(
        self,
        progress: Callable[[CrawlStats, str, str, str], None] | None = None,
        pause_checker: Callable[[], None] | None = None,
        request_delay_seconds: float | None = None,
        request_jitter_seconds: float | None = None,
    ) -> None:
        self.settings = get_settings()
        self.client = FetchClient(
            pause_checker=pause_checker,
            request_delay_seconds=request_delay_seconds,
            request_jitter_seconds=request_jitter_seconds,
        )
        self.progress = progress
        self.pause_checker = pause_checker

    def report(self, stats: CrawlStats, stage: str, current_item: str = "", current_url: str = "") -> None:
        if self.pause_checker:
            self.pause_checker()
        if self.progress:
            self.progress(stats, stage, current_item, current_url)

    async def close(self) -> None:
        await self.client.close()

    async def crawl_publishers_index(
        self,
        run_id: int,
        stats: CrawlStats,
        refresh_counts: bool = False,
    ) -> list[PublisherRecord]:
        first_url = f"{self.settings.base_url}/publ/1"
        self.report(stats, "读取出版商目录", "第 1 页", first_url)
        html = await self.client.get_text(first_url)
        total, per_page = parse_publisher_count(html)
        pages = math.ceil(total / per_page) if total and per_page else 1

        publishers: list[PublisherRecord] = []
        publishers.extend(parse_publisher_index(html))

        page_urls: list[str] = []
        seen_page_urls: set[str] = set()
        for page in range(2, pages + 1):
            url = f"{self.settings.base_url}/publ/1-{page}"
            page_urls.append(url)
            seen_page_urls.add(url)
        for url in parse_publisher_page_urls(html):
            if url not in seen_page_urls and url.rstrip("/") != first_url.rstrip("/"):
                page_urls.append(url)
                seen_page_urls.add(url)

        for index, url in enumerate(page_urls, start=2):
            try:
                total_pages = max(pages, len(page_urls) + 1)
                self.report(stats, "读取出版商目录", f"第 {index}/{total_pages} 页", url)
                page_html = await self.client.get_text(url)
                publishers.extend(parse_publisher_index(page_html))
            except Exception as exc:
                stats.errors += 1
                self.report(stats, "出版商目录失败", f"第 {index} 页", url)
                log_error(run_id, url, "publisher_index", str(exc))

        seen: set[str] = set()
        unique: list[PublisherRecord] = []
        for publisher in publishers:
            if publisher.source_url in seen:
                continue
            seen.add(publisher.source_url)
            unique.append(publisher)
            result = upsert_publisher(publisher)
            if result.created:
                stats.publishers_created += 1
        stats.publishers_seen += len(unique)
        self.report(stats, "出版商目录完成", f"发现 {len(unique)} 个出版商", first_url)
        if refresh_counts:
            await self.refresh_publisher_link_counts(run_id, unique, stats)
        return unique

    async def refresh_publisher_link_counts(
        self,
        run_id: int,
        publishers: list[PublisherRecord],
        stats: CrawlStats,
    ) -> None:
        total = len(publishers)
        for index, publisher in enumerate(publishers, start=1):
            try:
                self.report(
                    stats,
                    "统计出版商资源数",
                    f"{index}/{total} {publisher.name}",
                    publisher.source_url,
                )
                html = await self.client.get_text(publisher.source_url)
                links = parse_publisher_resource_links(html)
                stats.resource_links_seen += len(links)
                update_publisher_counts(publisher.source_url, len(links))
            except Exception as exc:
                stats.errors += 1
                self.report(stats, "统计出版商资源数失败", publisher.name, publisher.source_url)
                log_error(run_id, publisher.source_url, "publisher_count", str(exc))

    async def crawl_publisher(self, run_id: int, publisher: PublisherRecord, stats: CrawlStats) -> None:
        try:
            self.report(stats, "读取出版商页面", publisher.name, publisher.source_url)
            html = await self.client.get_text(publisher.source_url)
            links = parse_publisher_resource_links(html)
            stats.resource_links_seen += len(links)
            update_publisher_counts(publisher.source_url, len(links))

            existing = existing_resources_for_urls(links)
            pending_links = [
                url
                for url in links
                if url not in existing or existing[url]["publisher_url"] != publisher.source_url
            ]
            skipped = len(links) - len(pending_links)
            self.report(
                stats,
                "出版商资源链接完成",
                f"{publisher.name}：{len(links)} 个链接，待采集 {len(pending_links)} 个，已跳过 {skipped} 个",
                publisher.source_url,
            )
            for url in pending_links:
                await self.crawl_resource(run_id, url, stats, publisher, merge=True)
            update_publisher_counts(publisher.source_url, len(links))
            mark_publisher_crawled(publisher.source_url)
        except Exception as exc:
            stats.errors += 1
            self.report(stats, "出版商页面失败", publisher.name, publisher.source_url)
            log_error(run_id, publisher.source_url, "publisher_detail", str(exc))

    async def crawl_resource(
        self,
        run_id: int,
        url: str,
        stats: CrawlStats,
        publisher: PublisherRecord | None = None,
        merge: bool = False,
    ) -> None:
        normalized = normalize_url(url)
        try:
            self.report(stats, "读取资源详情", publisher.name if publisher else "news", normalized)
            html = await self.client.get_text(normalized)
            record = parse_detail(html, normalized)
            if publisher:
                record.publisher_url = publisher.source_url
                record.publisher = publisher.name
                record.force_publisher = True
            result = merge_resource(record) if merge else upsert_resource(record)
            if result.created:
                stats.resources_created += 1
            else:
                stats.resources_updated += 1
            self.report(stats, "资源详情已保存", record.title, normalized)

            for candidate in record.publisher_candidates if publisher is None else []:
                if candidate:
                    discovered = upsert_discovered_publisher(candidate)
                    if discovered.created:
                        stats.publishers_created += 1
        except Exception as exc:
            stats.errors += 1
            self.report(stats, "资源详情失败", publisher.name if publisher else "news", normalized)
            log_error(run_id, normalized, "resource_detail", str(exc))

    async def full_crawl(self) -> CrawlStats:
        run_id = create_run("full")
        stats = CrawlStats()
        status = "ok"
        try:
            self.report(stats, "全量初始化开始")
            publishers = await self.crawl_publishers_index(run_id, stats)
            for publisher in publishers:
                await self.crawl_publisher(run_id, publisher, stats)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, self.settings.base_url, "full_crawl", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats

    async def crawl_known_publishers(self) -> CrawlStats:
        run_id = create_run("publishers")
        stats = CrawlStats()
        status = "ok"
        try:
            rows = [
                row
                for row in list_publishers(limit=10000)
                if row["status"] != "discovered" and row["source_url"]
            ]
            if not rows:
                self.report(stats, "暂无出版商，先建立出版商索引")
                await self.crawl_publishers_index(run_id, stats)
                rows = [
                    row
                    for row in list_publishers(limit=10000)
                    if row["status"] != "discovered" and row["source_url"]
                ]
            publishers = [
                PublisherRecord(
                    name=row["name"],
                    kind=row["kind"],
                    source_url=row["source_url"],
                    source_id=row["source_id"],
                )
                for row in rows
                if row["source_url"]
            ]
            stats.publishers_seen = max(stats.publishers_seen, len(publishers))
            self.report(stats, "重扫已知出版商开始", f"{len(publishers)} 个出版商")
            for publisher in publishers:
                await self.crawl_publisher(run_id, publisher, stats)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, self.settings.base_url, "known_publishers", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats

    async def check_news(self) -> CrawlStats:
        run_id = create_run("news")
        stats = CrawlStats()
        status = "ok"
        update_id: int | None = None
        window_end = datetime.now().replace(microsecond=0)
        window_start = parse_datetime_state(get_state_value(DAILY_LAST_CHECKED_KEY))
        if window_start is None:
            window_start = window_end - timedelta(days=1)
        try:
            window_label = f"{format_datetime(window_start)} 至 {format_datetime(window_end)}"
            self.report(stats, "检查 news 开始", window_label)
            update_id = create_daily_update_window(
                run_id,
                update_date=window_end.date().isoformat(),
                window_started_at=format_datetime(window_start),
                window_finished_at=format_datetime(window_end),
            )
            await self.crawl_publishers_index(run_id, stats)
            links = await self.collect_news_window(run_id, stats, window_start, window_end)
            stats.resource_links_seen += len(links)
            self.report(
                stats,
                "news 列表完成",
                f"{window_label}：{len(links)} 个未记录过的资源",
                f"{self.settings.base_url}/news/",
            )
            for url in links:
                before_created = stats.resources_created
                normalized = normalize_url(url)
                await self.crawl_resource(run_id, normalized, stats, merge=True)
                if update_id:
                    from app.db import connect

                    with connect() as conn:
                        row = conn.execute("SELECT * FROM resources WHERE source_url = ?", (normalized,)).fetchone()
                    if row:
                        action = "created" if stats.resources_created > before_created else "updated"
                        add_daily_update_item(
                            update_id,
                            row["source_url"],
                            row["id"],
                            row["publisher"] or "无出版商",
                            row["title"],
                            action,
                        )
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, f"{self.settings.base_url}/news/", "news", str(exc))
            stats.messages.append(str(exc))
        finally:
            final_status = "warning" if status == "ok" and stats.errors else status
            finish_run(run_id, final_status, stats.as_dict(), "; ".join(stats.messages) or None)
            if update_id:
                finish_daily_update(update_id, final_status)
            if final_status == "ok":
                set_state_value(DAILY_LAST_CHECKED_KEY, format_datetime(window_end))
        return stats

    async def collect_news_window(
        self,
        run_id: int,
        stats: CrawlStats,
        window_start: datetime,
        window_end: datetime,
    ) -> list[str]:
        lower_date = window_start.date()
        upper_date = window_end.date()
        links: list[str] = []
        seen: set[str] = set()

        for page in range(1, NEWS_PAGE_LIMIT + 1):
            url = f"{self.settings.base_url}/news/" if page == 1 else f"{self.settings.base_url}/news/?page{page}"
            try:
                self.report(stats, "读取 news 列表", f"第 {page} 页", url)
                html = await self.client.get_text(url)
                entries = parse_news_entries(html)
            except Exception as exc:
                stats.errors += 1
                self.report(stats, "news 列表失败", f"第 {page} 页", url)
                log_error(run_id, url, "news_list", str(exc))
                break

            if not entries:
                break

            reached_older_items = False
            for entry in entries:
                if entry.published_date:
                    if entry.published_date < lower_date:
                        reached_older_items = True
                        continue
                    if entry.published_date > upper_date:
                        continue
                elif page > 1:
                    continue

                if entry.url not in seen:
                    seen.add(entry.url)
                    links.append(entry.url)

            if reached_older_items:
                break

        already_seen = daily_update_seen_urls(links)
        return [url for url in links if url not in already_seen]

    async def crawl_one_publisher(self, publisher_id: int) -> CrawlStats:
        run_id = create_run("publisher")
        stats = CrawlStats()
        status = "ok"
        try:
            row = publisher_by_id(publisher_id)
            if not row or not row["source_url"]:
                raise ValueError("出版商不存在或没有源页面")
            publisher = PublisherRecord(
                name=row["name"],
                kind=row["kind"],
                source_url=row["source_url"],
                source_id=row["source_id"],
            )
            stats.publishers_seen = 1
            await self.crawl_publisher(run_id, publisher, stats)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, self.settings.base_url, "publisher", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats

    async def crawl_publisher_queue(
        self,
        publisher_ids: list[int],
        task_interval_seconds: float = 0,
    ) -> CrawlStats:
        run_id = create_run("publisher_queue")
        stats = CrawlStats()
        status = "ok"
        try:
            publishers: list[PublisherRecord] = []
            for publisher_id in publisher_ids:
                row = publisher_by_id(publisher_id)
                if not row or not row["source_url"] or row["status"] == "discovered":
                    continue
                publishers.append(
                    PublisherRecord(
                        name=row["name"],
                        kind=row["kind"],
                        source_url=row["source_url"],
                        source_id=row["source_id"],
                    )
                )

            stats.publishers_seen = len(publishers)
            self.report(stats, "出版商队列开始", f"{len(publishers)} 个出版商")
            for index, publisher in enumerate(publishers, start=1):
                self.report(
                    stats,
                    "出版商队列执行中",
                    f"{index}/{len(publishers)} {publisher.name}",
                    publisher.source_url,
                )
                await self.crawl_publisher(run_id, publisher, stats)
                if task_interval_seconds > 0 and index < len(publishers):
                    await self.sleep_with_pause(task_interval_seconds, stats, "出版商队列间隔等待", publisher.name)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, self.settings.base_url, "publisher_queue", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats

    async def sleep_with_pause(
        self,
        seconds: float,
        stats: CrawlStats,
        stage: str,
        current_item: str = "",
    ) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            self.report(stats, stage, f"{current_item}，剩余 {int(remaining)} 秒")
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def repair_missing(self) -> CrawlStats:
        run_id = create_run("repair")
        stats = CrawlStats()
        status = "ok"
        try:
            urls = missing_resource_urls(self.settings.repair_batch_limit)
            stats.resource_links_seen = len(urls)
            self.report(stats, "缺字段补采开始", f"{len(urls)} 个资源")
            for url in urls:
                await self.crawl_resource(run_id, url, stats, merge=True)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, self.settings.base_url, "repair_missing", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats

    async def refresh_publisher_index(self) -> CrawlStats:
        run_id = create_run("publisher_index")
        stats = CrawlStats()
        status = "ok"
        try:
            await self.crawl_publishers_index(run_id, stats, refresh_counts=True)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, self.settings.base_url, "publisher_index", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats


async def run_job(
    kind: str,
    progress: Callable[[CrawlStats, str, str, str], None] | None = None,
    pause_checker: Callable[[], None] | None = None,
    publisher_id: int | None = None,
    publisher_ids: list[int] | None = None,
    task_interval_seconds: float = 0,
    request_delay_seconds: float | None = None,
    request_jitter_seconds: float | None = None,
) -> CrawlStats:
    crawler = Crawler(
        progress=progress,
        pause_checker=pause_checker,
        request_delay_seconds=request_delay_seconds,
        request_jitter_seconds=request_jitter_seconds,
    )
    try:
        if kind == "full":
            return await crawler.full_crawl()
        if kind == "publishers":
            return await crawler.crawl_known_publishers()
        if kind == "news":
            return await crawler.check_news()
        if kind == "repair":
            return await crawler.repair_missing()
        if kind == "publisher_index":
            return await crawler.refresh_publisher_index()
        if kind == "publisher" and publisher_id:
            return await crawler.crawl_one_publisher(publisher_id)
        if kind == "publisher_queue" and publisher_ids:
            return await crawler.crawl_publisher_queue(publisher_ids, task_interval_seconds)
        raise ValueError(f"Unknown crawl kind: {kind}")
    finally:
        await crawler.close()


def run_job_sync(
    kind: str,
    progress: Callable[[CrawlStats, str, str, str], None] | None = None,
    pause_checker: Callable[[], None] | None = None,
    publisher_id: int | None = None,
    publisher_ids: list[int] | None = None,
    task_interval_seconds: float = 0,
    request_delay_seconds: float | None = None,
    request_jitter_seconds: float | None = None,
) -> CrawlStats:
    return asyncio.run(
        run_job(
            kind,
            progress=progress,
            pause_checker=pause_checker,
            publisher_id=publisher_id,
            publisher_ids=publisher_ids,
            task_interval_seconds=task_interval_seconds,
            request_delay_seconds=request_delay_seconds,
            request_jitter_seconds=request_jitter_seconds,
        )
    )


def parse_datetime_state(value: str | None) -> datetime | None:
    if not value:
        return None
    for candidate in (value, value.replace("T", " ")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def format_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(sep=" ")
