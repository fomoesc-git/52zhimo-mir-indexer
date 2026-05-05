from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from app.client import FetchClient
from app.config import get_settings
from app.models import PublisherRecord
from app.parser import (
    normalize_url,
    parse_detail,
    parse_news_list,
    parse_publisher_count,
    parse_publisher_index,
    parse_publisher_resource_links,
)
from app.repository import (
    add_daily_update_item,
    create_run,
    create_daily_update,
    finish_daily_update,
    finish_run,
    list_publishers,
    log_error,
    mark_publisher_crawled,
    merge_resource,
    missing_resource_urls,
    publisher_by_id,
    update_publisher_counts,
    upsert_discovered_publisher,
    upsert_publisher,
    upsert_resource,
)


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
    ) -> None:
        self.settings = get_settings()
        self.client = FetchClient(pause_checker=pause_checker)
        self.progress = progress
        self.pause_checker = pause_checker

    def report(self, stats: CrawlStats, stage: str, current_item: str = "", current_url: str = "") -> None:
        if self.pause_checker:
            self.pause_checker()
        if self.progress:
            self.progress(stats, stage, current_item, current_url)

    async def close(self) -> None:
        await self.client.close()

    async def crawl_publishers_index(self, run_id: int, stats: CrawlStats) -> list[PublisherRecord]:
        first_url = f"{self.settings.base_url}/publ/1"
        self.report(stats, "读取出版商目录", "第 1 页", first_url)
        html = await self.client.get_text(first_url)
        total, per_page = parse_publisher_count(html)
        pages = math.ceil(total / per_page) if total and per_page else 1

        publishers: list[PublisherRecord] = []
        publishers.extend(parse_publisher_index(html))

        for page in range(2, pages + 1):
            url = f"{self.settings.base_url}/publ/1-{page}"
            try:
                self.report(stats, "读取出版商目录", f"第 {page}/{pages} 页", url)
                page_html = await self.client.get_text(url)
                publishers.extend(parse_publisher_index(page_html))
            except Exception as exc:
                stats.errors += 1
                self.report(stats, "出版商目录失败", f"第 {page}/{pages} 页", url)
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
        return unique

    async def crawl_publisher(self, run_id: int, publisher: PublisherRecord, stats: CrawlStats) -> None:
        try:
            self.report(stats, "读取出版商页面", publisher.name, publisher.source_url)
            html = await self.client.get_text(publisher.source_url)
            links = parse_publisher_resource_links(html)
            stats.resource_links_seen += len(links)
            self.report(stats, "出版商资源链接完成", f"{publisher.name}：{len(links)} 个链接", publisher.source_url)
            for url in links:
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
        try:
            self.report(stats, "检查 news 开始")
            update_id = create_daily_update(run_id)
            await self.crawl_publishers_index(run_id, stats)
            html = await self.client.get_text(f"{self.settings.base_url}/news/")
            today = date.today()
            links = parse_news_list(html, only_today=True, target_date=today)
            stats.resource_links_seen = len(links)
            self.report(stats, "news 列表完成", f"{today.isoformat()}：{len(links)} 个资源", f"{self.settings.base_url}/news/")
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
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
            if update_id:
                finish_daily_update(update_id, status)
        return stats

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
            await self.crawl_publishers_index(run_id, stats)
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
) -> CrawlStats:
    crawler = Crawler(progress=progress, pause_checker=pause_checker)
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
        raise ValueError(f"Unknown crawl kind: {kind}")
    finally:
        await crawler.close()


def run_job_sync(
    kind: str,
    progress: Callable[[CrawlStats, str, str, str], None] | None = None,
    pause_checker: Callable[[], None] | None = None,
    publisher_id: int | None = None,
) -> CrawlStats:
    return asyncio.run(
        run_job(kind, progress=progress, pause_checker=pause_checker, publisher_id=publisher_id)
    )
