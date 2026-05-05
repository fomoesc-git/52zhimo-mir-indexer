from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PublisherRecord:
    name: str
    source_url: str
    kind: str = "publisher"
    source_id: str | None = None


@dataclass
class ResourceRecord:
    source_url: str
    title: str
    source_id: str | None = None
    publisher: str | None = None
    publisher_url: str | None = None
    scale: str | None = None
    file_format: str | None = None
    paper_format: str | None = None
    file_size: str | None = None
    total_pages: str | None = None
    download_url: str | None = None
    category: str | None = None
    published_at: str | None = None
    last_modified_at: str | None = None
    raw_description: str | None = None
    crawl_status: str = "ok"
    error: str | None = None
    publisher_candidates: list[str] = field(default_factory=list)
