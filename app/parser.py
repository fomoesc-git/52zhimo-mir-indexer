from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.config import get_settings
from app.models import PublisherRecord, ResourceRecord


NEWS_DETAIL_RE = re.compile(r"/news/.+/\d{4}-\d{2}-\d{2}-\d+/?$")
NEWS_CATEGORY_RE = re.compile(r"/news/[^/]+/1-0-\d+/?$")
PUBL_DETAIL_RE = re.compile(r"/publ/.+/1-1-0-\d+/?$")
SOURCE_ID_RE = re.compile(r"-(\d+)/?$")

FIELD_MAP = {
    "Издательство": "publisher",
    "Издание": "publisher",
    "Автор": "publisher",
    "Формат файла": "file_format",
    "Масштаб макета": "scale",
    "Масштаб": "scale",
    "Формат листа": "paper_format",
    "Размер файла": "file_size",
    "Листов всего/выкройки": "total_pages",
    "Количество страниц": "total_pages",
    "Страниц": "total_pages",
    "Листов": "total_pages",
}


def normalize_url(url: str) -> str:
    settings = get_settings()
    absolute = urljoin(settings.base_url, html.unescape(url.strip()))
    parsed = urlparse(absolute)
    if parsed.netloc == "mir-modeley.at.ua":
        parsed = parsed._replace(netloc="mir-modeley.com")
    return parsed.geturl()


def source_id_from_url(url: str) -> str | None:
    match = SOURCE_ID_RE.search(url)
    return match.group(1) if match else None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def is_news_detail(url: str) -> bool:
    parsed = urlparse(normalize_url(url))
    return bool(NEWS_DETAIL_RE.search(parsed.path))


def is_news_category(url: str) -> bool:
    parsed = urlparse(normalize_url(url))
    return bool(NEWS_CATEGORY_RE.search(parsed.path))


def parse_publisher_index(html_text: str) -> list[PublisherRecord]:
    soup = BeautifulSoup(html_text, "html.parser")
    records: list[PublisherRecord] = []
    seen: set[str] = set()

    for article in soup.select("#allEntries article.short"):
        link = article.select_one("h2 a[href*='/publ/']")
        if not link:
            continue
        title = clean_text(link.get_text(" "))
        if ":" in title:
            kind_label, name = title.split(":", 1)
            kind = {
                "Издательство": "publisher",
                "Издание": "edition",
                "Автор": "author",
            }.get(kind_label.strip(), "publisher")
        else:
            name = title
            kind = "publisher"
        source_url = normalize_url(link.get("href", ""))
        if source_url in seen:
            continue
        seen.add(source_url)
        records.append(
            PublisherRecord(
                name=clean_text(name),
                kind=kind,
                source_url=source_url,
                source_id=source_id_from_url(source_url),
            )
        )

    return records


def parse_publisher_count(html_text: str) -> tuple[int | None, int | None]:
    text = clean_text(BeautifulSoup(html_text, "html.parser").get_text(" "))
    total_match = re.search(r"В категории материалов:\s*(\d+)", text)
    shown_match = re.search(r"Показано материалов:\s*(\d+)-(\d+)", text)
    total = int(total_match.group(1)) if total_match else None
    per_page = None
    if shown_match:
        per_page = int(shown_match.group(2)) - int(shown_match.group(1)) + 1
    return total, per_page


def parse_publisher_resource_links(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    content = soup.select_one(".fdesc.full-text") or soup
    links: list[str] = []
    seen: set[str] = set()

    for link in content.select("a[href]"):
        url = normalize_url(link["href"])
        if is_news_detail(url) and url not in seen:
            seen.add(url)
            links.append(url)

    return links


def parse_news_list(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    links: list[str] = []
    seen: set[str] = set()

    for link in soup.select("a[href*='/news/']"):
        url = normalize_url(link["href"])
        if is_news_detail(url) and url not in seen:
            seen.add(url)
            links.append(url)

    return links


def parse_detail(html_text: str, source_url: str) -> ResourceRecord:
    soup = BeautifulSoup(html_text, "html.parser")
    title = first_attr(soup, "meta[property='og:title']", "content")
    if not title:
        title_node = soup.select_one("h1.sect-title") or soup.select_one("h1")
        title = title_node.get_text(" ") if title_node else source_url

    record = ResourceRecord(
        source_url=normalize_url(source_url),
        source_id=source_id_from_url(source_url),
        title=clean_text(title),
    )

    record.published_at = first_attr(soup, "meta[name='DC.Date']", "content")
    record.last_modified_at = first_attr(soup, "meta[property='article:modified_time']", "content")
    record.category = parse_category(soup)

    description = parse_description_block(soup)
    record.raw_description = description
    apply_description_fields(record, description)
    apply_json_ld(record, soup)
    apply_download_link(record, soup)

    record.publisher_candidates = [record.publisher] if record.publisher else []
    return record


def first_attr(soup: BeautifulSoup, selector: str, attr: str) -> str | None:
    node = soup.select_one(selector)
    value = node.get(attr) if node else None
    return clean_text(value) or None


def parse_category(soup: BeautifulSoup) -> str | None:
    breadcrumb = soup.select_one(".foriginal")
    if not breadcrumb:
        return None
    links = [clean_text(a.get_text(" ")) for a in breadcrumb.select("a")]
    if len(links) >= 3:
        return links[-1]
    return None


def parse_description_block(soup: BeautifulSoup) -> str:
    block = soup.select_one(".fdesc.full-text") or soup.select_one(".fdesc") or soup
    for script in block.select("script, style"):
        script.extract()
    lines = [clean_text(line) for line in block.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def apply_description_fields(record: ResourceRecord, description: str) -> None:
    lines = [clean_text(line) for line in description.split("\n") if clean_text(line)]
    for line in lines:
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = clean_text(label)
        value = clean_text(value)
        attr = FIELD_MAP.get(label)
        if attr and value:
            setattr(record, attr, value)

    if not record.publisher:
        match = re.search(r"[\[(]([^][()]+?)(?:\s+\d{2,4}(?:[-/]\d{1,2})?)?[\])]", record.title)
        if match:
            record.publisher = clean_text(match.group(1))


def apply_json_ld(record: ResourceRecord, soup: BeautifulSoup) -> None:
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            download = item.get("potentialAction", {})
            target = download.get("target") if isinstance(download, dict) else None
            if target and not record.download_url:
                record.download_url = clean_text(target)
            encodings = item.get("encoding")
            if isinstance(encodings, list):
                for encoding in encodings:
                    if isinstance(encoding, dict) and encoding.get("contentUrl") and not record.download_url:
                        record.download_url = clean_text(encoding["contentUrl"])


def apply_download_link(record: ResourceRecord, soup: BeautifulSoup) -> None:
    if record.download_url:
        return

    selectors = [
        ".fbtns a[href]",
        "a[title*='Google'][href]",
        "a[href*='drive.google.com']",
        "a[href*='docs.google.com']",
    ]
    for selector in selectors:
        for link in soup.select(selector):
            href = clean_text(link.get("href"))
            if href:
                record.download_url = href
                return
