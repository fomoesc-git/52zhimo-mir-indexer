from __future__ import annotations

import html
import json
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.config import get_settings
from app.models import PublisherRecord, ResourceRecord


NEWS_DETAIL_RE = re.compile(r"/news/.+/\d{4}-\d{2}-\d{2}-\d+/?$")
NEWS_CATEGORY_RE = re.compile(r"/news/[^/]+/1-0-\d+/?$")
PUBL_DETAIL_RE = re.compile(r"/publ/.+/1-1-0-\d+/?$")
SOURCE_ID_RE = re.compile(r"-(\d+)/?$")
NEWS_URL_DATE_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})-\d+/?$")
FILE_SIZE_RE = re.compile(
    r"(\d+(?:[\s.,]\d+)?)\s*([kmgtкмгт]\s*(?:b|б|байт|bytes?)?|килобайт(?:а|ов)?|кілобайт(?:а|ів)?|"
    r"мегабайт(?:а|ов)?|мегабайт(?:а|ів)?|гигабайт(?:а|ов)?|гігабайт(?:а|ів)?|"
    r"терабайт(?:а|ов)?|терабайт(?:а|ів)?)",
    re.IGNORECASE,
)
FILE_FORMAT_RE = re.compile(
    r"\b(JPG|JPEG|PDF|PDO|PSD|CDR|BMP|PNG|TIFF?|GIF|RAR|ZIP|7Z|AI|EPS|SVG|DXF|DOCX?|TXT)\b",
    re.IGNORECASE,
)
PAPER_FORMAT_RE = re.compile(r"(?<![A-Za-zА-Яа-я])([ABCАВС][0-6])(?![A-Za-zА-Яа-я])", re.IGNORECASE)
ARCHIVE_FORMATS = {"RAR", "ZIP", "7Z"}
CYRILLIC_PAPER_TRANSLATION = str.maketrans({"А": "A", "В": "B", "С": "C", "а": "A", "в": "B", "с": "C"})

FIELD_MAP = {
    "Издательство": "publisher",
    "Издатель": "publisher",
    "Издание": "publisher",
    "Автор": "publisher",
    "Авторы": "publisher",
    "Формат файла": "file_format",
    "Формат": "file_format",
    "Масштаб макета": "scale",
    "Масштаб": "scale",
    "Формат листа": "paper_format",
    "Формат листов": "paper_format",
    "Формат страницы": "paper_format",
    "Формат страниц": "paper_format",
    "Размер файла": "file_size",
    "Размер": "file_size",
    "Листов всего/выкройки": "total_pages",
    "Листов всего/с выкройками": "total_pages",
    "Листов всего": "total_pages",
    "Листов с выкройками": "total_pages",
    "Количество страниц": "total_pages",
    "Страниц": "total_pages",
    "Листов": "total_pages",
}

FIELD_LABEL_RE = re.compile(
    r"^(Издательство|Издатель|Издание|Автор|Авторы|Формат файла|Формат листов|Формат листа|"
    r"Формат страниц|Формат страницы|Формат|Масштаб макета|Масштаб|Размер файла|Размер|"
    r"Листов всего/выкройки|Листов всего/с выкройками|Листов всего|Листов с выкройками|"
    r"Количество страниц|Страниц|Листов)\s*(?::|-|–|—)\s*(.*)$",
    re.IGNORECASE,
)


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
    soup = BeautifulSoup(html_text, "html.parser")
    text = clean_text(soup.get_text(" "))
    total_match = re.search(r"В категории материалов\s*:\s*(\d+)", text)
    shown_match = re.search(r"Показано материалов\s*:\s*(\d+)\s*[-–]\s*(\d+)", text)
    total = int(total_match.group(1)) if total_match else None
    per_page = None
    if shown_match:
        per_page = int(shown_match.group(2)) - int(shown_match.group(1)) + 1
    elif total:
        current_count = len(parse_publisher_index(html_text))
        per_page = current_count or None
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


def parse_news_list(
    html_text: str,
    only_today: bool = False,
    target_date: date | None = None,
) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    container = soup.select_one("#allEntries") or soup
    links: list[str] = []
    seen: set[str] = set()

    if only_today:
        for article in container.select("article.short"):
            link = article.select_one("h2 a[href*='/news/']")
            if not link:
                continue
            url = normalize_url(link["href"])
            if not is_news_detail(url) or url in seen:
                continue
            time_node = article.select_one("time[itemprop='datePublished'], time")
            time_text = clean_text(time_node.get_text(" ")) if time_node else ""
            url_date = news_date_from_url(url)
            is_today = time_text.lower() in {"сегодня", "today"}
            matches_target = bool(target_date and url_date == target_date.isoformat())
            if is_today or matches_target:
                seen.add(url)
                links.append(url)
        return links

    for link in container.select("a[href*='/news/']"):
        url = normalize_url(link["href"])
        if is_news_detail(url) and url not in seen:
            seen.add(url)
            links.append(url)

    return links


def news_date_from_url(url: str) -> str | None:
    match = NEWS_URL_DATE_RE.search(urlparse(normalize_url(url)).path)
    return match.group(1) if match else None


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
    normalize_record_fields(record)
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
    pending_attr: str | None = None
    for line in lines:
        match = FIELD_LABEL_RE.match(line)
        if not match:
            if pending_attr and line:
                setattr(record, pending_attr, line)
                pending_attr = None
            continue
        label = normalize_label(match.group(1))
        value = clean_text(match.group(2))
        attr = FIELD_MAP.get(label)
        if attr:
            if value:
                setattr(record, attr, normalize_field_value(attr, value))
                pending_attr = None
            else:
                pending_attr = attr

    if not record.publisher:
        match = re.search(r"[\[(]([^][()]+?)(?:\s+\d{2,4}(?:[-/]\d{1,2})?)?[\])]", record.title)
        if match and is_plausible_publisher(clean_text(match.group(1))):
            record.publisher = clean_text(match.group(1))


def normalize_label(label: str) -> str:
    compact = clean_text(label)
    for key in FIELD_MAP:
        if compact.lower() == key.lower():
            return key
    return compact


def is_plausible_publisher(value: str) -> bool:
    if not value or value in {"?", "??", "???", "!"}:
        return False
    return bool(re.search(r"[A-Za-zА-Яа-я0-9]", value))


def normalize_record_fields(record: ResourceRecord) -> None:
    combined_format = record.file_format or ""
    combined_paper = record.paper_format or ""
    extracted_format = normalize_file_format(combined_format)
    extracted_paper = normalize_paper_format(" ".join(part for part in [combined_paper, combined_format] if part))
    if extracted_format:
        record.file_format = extracted_format
    if extracted_paper:
        record.paper_format = extracted_paper
    record.file_size = normalize_file_size(record.file_size)
    record.total_pages = normalize_total_pages(record.total_pages)


def normalize_field_value(attr: str, value: str) -> str:
    if attr == "file_format":
        return normalize_file_format(value) or clean_text(value)
    if attr == "paper_format":
        return normalize_paper_format(value) or clean_text(value)
    if attr == "file_size":
        return normalize_file_size(value) or clean_text(value)
    if attr == "total_pages":
        return normalize_total_pages(value) or clean_text(value)
    return clean_text(value)


def normalize_file_format(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    formats = [match.group(1).upper() for match in FILE_FORMAT_RE.finditer(text)]
    formats = ["TIF" if item == "TIFF" else item for item in formats]
    primary = [item for item in formats if item not in ARCHIVE_FORMATS]
    if primary:
        return unique_join(primary)
    if formats:
        return unique_join(formats)
    return None


def normalize_paper_format(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    papers = [match.group(1).translate(CYRILLIC_PAPER_TRANSLATION).upper() for match in PAPER_FORMAT_RE.finditer(text)]
    return unique_join(papers) if papers else None


def unique_join(values: list[str]) -> str:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return "/".join(unique)


def normalize_file_size(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = FILE_SIZE_RE.search(text.replace("\u00a0", " "))
    if not match:
        return text
    number = normalize_decimal(match.group(1))
    unit = normalize_size_unit(match.group(2))
    return f"{number}{unit}" if unit else number


def normalize_decimal(value: str) -> str:
    compact = re.sub(r"\s+", "", value).replace(",", ".")
    if "." in compact:
        compact = compact.rstrip("0").rstrip(".")
    return compact


def normalize_size_unit(value: str) -> str:
    unit = re.sub(r"[\s.]+", "", value.lower())
    if unit.startswith(("k", "к", "кило", "кіло")):
        return "KB"
    if unit.startswith(("m", "м", "мега")):
        return "MB"
    if unit.startswith(("g", "г", "гига", "гіга")):
        return "GB"
    if unit.startswith(("t", "т", "тера")):
        return "TB"
    return unit.upper()


def normalize_total_pages(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    return match.group(1) if match else text


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
