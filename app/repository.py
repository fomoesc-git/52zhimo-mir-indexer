from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from app.db import connect
from app.models import PublisherRecord, ResourceRecord


@dataclass
class UpsertResult:
    created: bool
    id: int


def upsert_publisher(record: PublisherRecord, status: str | None = None) -> UpsertResult:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM publishers WHERE source_url = ?",
            (record.source_url,),
        ).fetchone()
        if existing:
            previous_names = previous_names_with(existing, record.name)
            conn.execute(
                """
                UPDATE publishers
                SET name = ?, kind = ?, source_id = ?, status = ?,
                    canonical_key = ?, previous_names = ?,
                    last_seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    record.name,
                    record.kind,
                    record.source_id,
                    status or existing["status"],
                    publisher_key(record.name, record.source_url),
                    previous_names,
                    existing["id"],
                ),
            )
            return UpsertResult(False, int(existing["id"]))

        canonical_key = publisher_key(record.name, record.source_url)
        discovered_named = conn.execute(
            """
            SELECT * FROM publishers
            WHERE lower(name) = lower(?) AND status = 'discovered' AND source_url IS NULL
            """,
            (record.name,),
        ).fetchone()
        if discovered_named:
            previous_names = previous_names_with(discovered_named, record.name)
            conn.execute(
                """
                UPDATE publishers
                SET name = ?, kind = ?, source_url = ?, source_id = ?, status = ?,
                    canonical_key = ?, previous_names = ?, notes = '已匹配到正式出版商目录',
                    last_seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    record.name,
                    record.kind,
                    record.source_url,
                    record.source_id,
                    status or "incomplete",
                    canonical_key,
                    previous_names,
                    discovered_named["id"],
                ),
            )
            return UpsertResult(False, int(discovered_named["id"]))

        if record.source_id:
            same_source_id = conn.execute(
                "SELECT * FROM publishers WHERE source_id = ? AND source_id IS NOT NULL",
                (record.source_id,),
            ).fetchone()
            if same_source_id:
                previous_names = previous_names_with(same_source_id, record.name)
                conn.execute(
                    """
                    UPDATE publishers
                    SET name = ?, kind = ?, source_url = ?, status = ?,
                        canonical_key = ?, previous_names = ?,
                        last_seen_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        record.name,
                        record.kind,
                        record.source_url,
                        status or same_source_id["status"],
                        canonical_key,
                        previous_names,
                        same_source_id["id"],
                    ),
                )
                return UpsertResult(False, int(same_source_id["id"]))

        similar = conn.execute(
            "SELECT * FROM publishers WHERE canonical_key = ? AND source_url IS NOT NULL",
            (canonical_key,),
        ).fetchone()
        if similar:
            previous_names = previous_names_with(similar, record.name)
            conn.execute(
                """
                UPDATE publishers
                SET name = ?, kind = ?, source_url = ?, source_id = ?, status = ?,
                    previous_names = ?, last_seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    record.name,
                    record.kind,
                    record.source_url,
                    record.source_id,
                    status or similar["status"],
                    previous_names,
                    similar["id"],
                ),
            )
            return UpsertResult(False, int(similar["id"]))

        cur = conn.execute(
            """
            INSERT INTO publishers (name, kind, source_url, source_id, status, canonical_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (record.name, record.kind, record.source_url, record.source_id, status or "incomplete", canonical_key),
        )
        return UpsertResult(True, int(cur.lastrowid))


def previous_names_with(row: sqlite3.Row, incoming_name: str) -> str:
    previous_names = row["previous_names"] or ""
    previous = [part for part in previous_names.split("|") if part]
    current_name = row["name"]
    if current_name != incoming_name and current_name not in previous:
        previous.append(current_name)
    return "|".join(previous)


def publisher_key(name: str, source_url: str | None = None) -> str:
    if source_url:
        tail = source_url.rstrip("/").split("/")[-1]
        if tail:
            return tail.lower()
    return "".join(ch for ch in name.lower() if ch.isalnum())


def upsert_discovered_publisher(name: str) -> UpsertResult:
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM publishers WHERE lower(name) = lower(?) AND source_url IS NULL",
            (name,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE publishers SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing["id"],),
            )
            return UpsertResult(False, int(existing["id"]))

        existing_named = conn.execute(
            "SELECT id, source_url FROM publishers WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        if existing_named:
            if existing_named["source_url"]:
                return UpsertResult(False, int(existing_named["id"]))
            conn.execute(
                "UPDATE publishers SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing_named["id"],),
            )
            return UpsertResult(False, int(existing_named["id"]))

        cur = conn.execute(
            """
            INSERT INTO publishers (name, kind, source_url, status, notes)
            VALUES (?, 'publisher', NULL, 'discovered', '从 news 新资源中发现，等待确认出版商目录页')
            """,
            (name,),
        )
        return UpsertResult(True, int(cur.lastrowid))


def confirm_discovered_publisher(publisher_id: int) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT * FROM publishers WHERE id = ?", (publisher_id,)).fetchone()
        if not row or row["status"] != "discovered":
            return False
        status = "incomplete" if row["source_url"] else "active"
        conn.execute(
            """
            UPDATE publishers
            SET status = ?, notes = '已手动确认出版商名称', last_seen_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, publisher_id),
        )
    return True


def mark_publisher_crawled(source_url: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE publishers SET last_crawled_at = CURRENT_TIMESTAMP WHERE source_url = ?",
            (source_url,),
        )


def set_publisher_status(source_url: str) -> None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT p.resource_links_seen, COUNT(r.id) AS collected
            FROM publishers p
            LEFT JOIN resources r ON r.publisher_url = p.source_url
            WHERE p.source_url = ?
            GROUP BY p.id
            """,
            (source_url,),
        ).fetchone()
        if not row:
            return
        if row["resource_links_seen"] and row["collected"] >= row["resource_links_seen"]:
            status = "complete"
        else:
            status = "incomplete"
        conn.execute("UPDATE publishers SET status = ? WHERE source_url = ?", (status, source_url))


def upsert_resource(record: ResourceRecord) -> UpsertResult:
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM resources WHERE source_url = ?",
            (record.source_url,),
        ).fetchone()
        if existing:
            return merge_resource(record)

        values = (
            record.source_id,
            record.title,
            record.publisher,
            record.publisher_url,
            record.scale,
            record.file_format,
            record.paper_format,
            record.file_size,
            record.total_pages,
            record.download_url,
            record.category,
            record.published_at,
            record.last_modified_at,
            record.raw_description,
            record.crawl_status,
            record.error,
        )
        cur = conn.execute(
            """
            INSERT INTO resources (
                source_url, source_id, title, publisher, publisher_url,
                scale, file_format, paper_format, file_size, total_pages,
                download_url, category, published_at, last_modified_at,
                raw_description, crawl_status, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (record.source_url,) + values,
        )
        return UpsertResult(True, int(cur.lastrowid))


def merge_resource(record: ResourceRecord) -> UpsertResult:
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM resources WHERE source_url = ?",
            (record.source_url,),
        ).fetchone()
        if not existing:
            return upsert_resource(record)

        fields = [
            "source_id",
            "title",
            "publisher",
            "publisher_url",
            "scale",
            "file_format",
            "paper_format",
            "file_size",
            "total_pages",
            "download_url",
            "category",
            "published_at",
            "last_modified_at",
            "raw_description",
            "crawl_status",
            "error",
        ]
        values = {}
        for field in fields:
            current = existing[field]
            incoming = getattr(record, field)
            if field in {"publisher", "publisher_url"} and record.force_publisher:
                values[field] = incoming if incoming not in (None, "") else current
            else:
                values[field] = incoming if incoming not in (None, "") else current

        conn.execute(
            """
            UPDATE resources
            SET source_id = ?, title = ?, publisher = ?, publisher_url = ?,
                scale = ?, file_format = ?, paper_format = ?, file_size = ?,
                total_pages = ?, download_url = ?, category = ?,
                published_at = ?, last_modified_at = ?, raw_description = ?,
                crawl_status = ?, error = ?, last_seen_at = CURRENT_TIMESTAMP,
                last_crawled_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            tuple(values[field] for field in fields) + (existing["id"],),
        )
        return UpsertResult(False, int(existing["id"]))


def create_run(run_type: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO crawl_runs (run_type, status) VALUES (?, 'running')",
            (run_type,),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, stats: dict[str, int], message: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE crawl_runs
            SET status = ?, finished_at = CURRENT_TIMESTAMP,
                publishers_seen = ?, resource_links_seen = ?,
                resources_created = ?, resources_updated = ?,
                publishers_created = ?, errors = ?, message = ?
            WHERE id = ?
            """,
            (
                status,
                stats.get("publishers_seen", 0),
                stats.get("resource_links_seen", 0),
                stats.get("resources_created", 0),
                stats.get("resources_updated", 0),
                stats.get("publishers_created", 0),
                stats.get("errors", 0),
                message,
                run_id,
            ),
        )


def log_error(run_id: int | None, url: str, stage: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO crawl_errors (run_id, url, stage, message) VALUES (?, ?, ?, ?)",
            (run_id, url, stage, message[:1000]),
        )


def list_publishers(limit: int = 1000, status: str | None = None) -> list[sqlite3.Row]:
    sql = """
    SELECT
        p.*,
        COALESCE(COUNT(r.id), 0) AS collected_count
    FROM publishers p
    LEFT JOIN resources r ON r.publisher_url = p.source_url
        OR (p.source_url IS NULL AND lower(r.publisher) = lower(p.name))
    """
    params: tuple[object, ...] = ()
    if status:
        if status == "incomplete":
            sql += " WHERE p.status IN ('incomplete', 'active')"
        else:
            sql += " WHERE p.status = ?"
            params = (status,)
    sql += """
    GROUP BY p.id
    ORDER BY
        CASE
            WHEN p.status = 'discovered' THEN 2
            WHEN p.status IN ('incomplete', 'active') THEN 1
            ELSE 0
        END DESC,
        p.name COLLATE NOCASE
    LIMIT ?
    """
    params += (limit,)
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def update_publisher_counts(source_url: str, resource_links_seen: int) -> None:
    with connect() as conn:
        collected = conn.execute(
            "SELECT COUNT(*) AS c FROM resources WHERE publisher_url = ?",
            (source_url,),
        ).fetchone()["c"]
        conn.execute(
            """
            UPDATE publishers
            SET expected_count = ?, resource_links_seen = ?, resources_collected = ?,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE source_url = ?
            """,
            (resource_links_seen, resource_links_seen, collected, source_url),
        )
    set_publisher_status(source_url)


def publisher_by_id(publisher_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM publishers WHERE id = ?", (publisher_id,)).fetchone()


def get_publisher(publisher_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM publishers WHERE id = ?", (publisher_id,)).fetchone()


def list_resources(
    q: str | None = None,
    publisher: str | None = None,
    publisher_url: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    where = []
    params: list[object] = []
    if q:
        where.append("(title LIKE ? OR publisher LIKE ? OR category LIKE ?)")
        needle = f"%{q}%"
        params.extend([needle, needle, needle])
    if publisher_url:
        where.append("publisher_url = ?")
        params.append(publisher_url)
    elif publisher:
        if publisher == "无出版商":
            where.append("(publisher IS NULL OR publisher = '')")
        else:
            where.append("publisher = ?")
            params.append(publisher)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM resources {clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT * FROM resources
            {clause}
            ORDER BY COALESCE(published_at, first_seen_at) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    return rows, int(total)


def missing_fields_clause() -> str:
    return """
    (
        scale IS NULL OR scale = '' OR
        file_format IS NULL OR file_format = '' OR
        paper_format IS NULL OR paper_format = '' OR
        file_size IS NULL OR file_size = '' OR
        total_pages IS NULL OR total_pages = ''
    )
    """


def list_missing_resources(limit: int = 100, offset: int = 0) -> tuple[list[sqlite3.Row], int]:
    clause = missing_fields_clause()
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM resources WHERE {clause}").fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT * FROM resources
            WHERE {clause}
            ORDER BY last_crawled_at ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return rows, int(total)


def missing_resource_urls(limit: int) -> list[str]:
    clause = missing_fields_clause()
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT source_url FROM resources
            WHERE {clause}
            ORDER BY last_crawled_at ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [row["source_url"] for row in rows]


def list_publisher_options() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT publisher, COUNT(*) AS c
            FROM resources
            WHERE publisher IS NOT NULL AND publisher != ''
            GROUP BY publisher
            ORDER BY publisher COLLATE NOCASE
            """
        ).fetchall()
        has_empty = conn.execute(
            "SELECT 1 FROM resources WHERE publisher IS NULL OR publisher = '' LIMIT 1"
        ).fetchone()
    options = [row["publisher"] for row in rows]
    if has_empty:
        options.insert(0, "无出版商")
    return options


def count_no_publisher_resources() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM resources WHERE publisher IS NULL OR publisher = ''"
        ).fetchone()
    return int(row["c"])


def create_daily_update(run_id: int, update_date: str | None = None) -> int:
    update_date = update_date or date.today().isoformat()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO daily_updates (update_date, run_id, status)
            VALUES (?, ?, 'running')
            """,
            (update_date, run_id),
        )
        return int(cur.lastrowid)


def add_daily_update_item(
    update_id: int,
    resource_url: str,
    resource_id: int | None,
    publisher: str | None,
    title: str | None,
    action: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_update_items
                (update_id, resource_url, resource_id, publisher, title, action)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (update_id, resource_url, resource_id, publisher or "无出版商", title, action),
        )


def finish_daily_update(update_id: int, status: str) -> None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS seen,
                SUM(CASE WHEN action = 'created' THEN 1 ELSE 0 END) AS created,
                SUM(CASE WHEN action = 'updated' THEN 1 ELSE 0 END) AS updated
            FROM daily_update_items
            WHERE update_id = ?
            """,
            (update_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE daily_updates
            SET status = ?, finished_at = CURRENT_TIMESTAMP,
                items_seen = ?, items_created = ?, items_updated = ?
            WHERE id = ?
            """,
            (status, row["seen"] or 0, row["created"] or 0, row["updated"] or 0, update_id),
        )


def list_daily_updates(limit: int = 30) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM daily_updates ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def get_daily_update(update_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM daily_updates WHERE id = ?", (update_id,)).fetchone()


def list_daily_update_items(update_id: int, limit: int = 2000) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT dui.*, r.scale, r.file_format, r.paper_format, r.file_size,
                   r.total_pages, r.download_url, r.category, r.published_at
            FROM daily_update_items dui
            LEFT JOIN resources r ON r.id = dui.resource_id OR r.source_url = dui.resource_url
            WHERE dui.update_id = ?
            ORDER BY dui.publisher COLLATE NOCASE, dui.id
            LIMIT ?
            """,
            (update_id, limit),
        ).fetchall()


def dashboard_stats() -> dict[str, int]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM publishers) AS publishers,
                (SELECT COUNT(*) FROM publishers WHERE status = 'discovered') AS discovered_publishers,
                (SELECT COUNT(*) FROM resources) AS resources,
                (SELECT COUNT(*) FROM resources WHERE download_url IS NOT NULL AND download_url != '') AS downloads,
                (SELECT COUNT(*) FROM crawl_errors) AS errors
            """
        ).fetchone()
        return {key: int(row[key]) for key in row.keys()}


def recent_runs(limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
