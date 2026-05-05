from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.db import connect
from app.models import PublisherRecord, ResourceRecord


@dataclass
class UpsertResult:
    created: bool
    id: int


def upsert_publisher(record: PublisherRecord, status: str = "active") -> UpsertResult:
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM publishers WHERE source_url = ?",
            (record.source_url,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE publishers
                SET name = ?, kind = ?, source_id = ?, status = ?,
                    last_seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (record.name, record.kind, record.source_id, status, existing["id"]),
            )
            return UpsertResult(False, int(existing["id"]))

        cur = conn.execute(
            """
            INSERT INTO publishers (name, kind, source_url, source_id, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record.name, record.kind, record.source_url, record.source_id, status),
        )
        return UpsertResult(True, int(cur.lastrowid))


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
            "SELECT id FROM publishers WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        if existing_named:
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


def mark_publisher_crawled(source_url: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE publishers SET last_crawled_at = CURRENT_TIMESTAMP WHERE source_url = ?",
            (source_url,),
        )


def upsert_resource(record: ResourceRecord) -> UpsertResult:
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM resources WHERE source_url = ?",
            (record.source_url,),
        ).fetchone()
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
        if existing:
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
                values + (existing["id"],),
            )
            return UpsertResult(False, int(existing["id"]))

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
    sql = "SELECT * FROM publishers"
    params: tuple[object, ...] = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY status DESC, name COLLATE NOCASE LIMIT ?"
    params += (limit,)
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def get_publisher(publisher_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM publishers WHERE id = ?", (publisher_id,)).fetchone()


def list_resources(
    q: str | None = None,
    publisher: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    where = []
    params: list[object] = []
    if q:
        where.append("(title LIKE ? OR publisher LIKE ? OR category LIKE ?)")
        needle = f"%{q}%"
        params.extend([needle, needle, needle])
    if publisher:
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
