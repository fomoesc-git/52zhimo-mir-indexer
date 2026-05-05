from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field

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
    create_run,
    finish_run,
    list_publishers,
    log_error,
    mark_publisher_crawled,
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
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = FetchClient()

    async def close(self) -> None:
        await self.client.close()

    async def crawl_publishers_index(self, run_id: int, stats: CrawlStats) -> list[PublisherRecord]:
        first_url = f"{self.settings.base_url}/publ/1"
        html = await self.client.get_text(first_url)
        total, per_page = parse_publisher_count(html)
        pages = math.ceil(total / per_page) if total and per_page else 1

        publishers: list[PublisherRecord] = []
        publishers.extend(parse_publisher_index(html))

        for page in range(2, pages + 1):
            url = f"{self.settings.base_url}/publ/1-{page}"
            try:
                page_html = await self.client.get_text(url)
                publishers.extend(parse_publisher_index(page_html))
            except Exception as exc:
                stats.errors += 1
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
        return unique

    async def crawl_publisher(self, run_id: int, publisher: PublisherRecord, stats: CrawlStats) -> None:
        try:
            html = await self.client.get_text(publisher.source_url)
            links = parse_publisher_resource_links(html)
            stats.resource_links_seen += len(links)
            for url in links:
                await self.crawl_resource(run_id, url, stats, publisher)
            mark_publisher_crawled(publisher.source_url)
        except Exception as exc:
            stats.errors += 1
            log_error(run_id, publisher.source_url, "publisher_detail", str(exc))

    async def crawl_resource(
        self,
        run_id: int,
        url: str,
        stats: CrawlStats,
        publisher: PublisherRecord | None = None,
    ) -> None:
        normalized = normalize_url(url)
        try:
            html = await self.client.get_text(normalized)
            record = parse_detail(html, normalized)
            if publisher:
                record.publisher_url = publisher.source_url
                if not record.publisher:
                    record.publisher = publisher.name
            result = upsert_resource(record)
            if result.created:
                stats.resources_created += 1
            else:
                stats.resources_updated += 1

            for candidate in record.publisher_candidates if publisher is None else []:
                if candidate:
                    discovered = upsert_discovered_publisher(candidate)
                    if discovered.created:
                        stats.publishers_created += 1
        except Exception as exc:
            stats.errors += 1
            log_error(run_id, normalized, "resource_detail", str(exc))

    async def full_crawl(self) -> CrawlStats:
        run_id = create_run("full")
        stats = CrawlStats()
        status = "ok"
        try:
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
            rows = list_publishers(limit=10000, status="active")
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
            stats.publishers_seen = len(publishers)
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
        try:
            await self.crawl_publishers_index(run_id, stats)
            html = await self.client.get_text(f"{self.settings.base_url}/news/")
            links = parse_news_list(html)
            stats.resource_links_seen = len(links)
            for url in links:
                await self.crawl_resource(run_id, url, stats)
        except Exception as exc:
            status = "failed"
            stats.errors += 1
            log_error(run_id, f"{self.settings.base_url}/news/", "news", str(exc))
            stats.messages.append(str(exc))
        finally:
            finish_run(run_id, status, stats.as_dict(), "; ".join(stats.messages) or None)
        return stats


async def run_job(kind: str) -> CrawlStats:
    crawler = Crawler()
    try:
        if kind == "full":
            return await crawler.full_crawl()
        if kind == "publishers":
            return await crawler.crawl_known_publishers()
        if kind == "news":
            return await crawler.check_news()
        raise ValueError(f"Unknown crawl kind: {kind}")
    finally:
        await crawler.close()


def run_job_sync(kind: str) -> CrawlStats:
    return asyncio.run(run_job(kind))
