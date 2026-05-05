from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS publishers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'publisher',
    source_url TEXT UNIQUE,
    source_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_crawled_at TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_publishers_name ON publishers(name);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL UNIQUE,
    source_id TEXT,
    title TEXT NOT NULL,
    title_cn TEXT,
    publisher TEXT,
    publisher_url TEXT,
    scale TEXT,
    file_format TEXT,
    paper_format TEXT,
    file_size TEXT,
    total_pages TEXT,
    download_url TEXT,
    category TEXT,
    published_at TEXT,
    last_modified_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_crawled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_description TEXT,
    crawl_status TEXT NOT NULL DEFAULT 'ok',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_resources_publisher ON resources(publisher);
CREATE INDEX IF NOT EXISTS idx_resources_published_at ON resources(published_at);
CREATE INDEX IF NOT EXISTS idx_resources_source_id ON resources(source_id);

CREATE TABLE IF NOT EXISTS daily_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_date TEXT NOT NULL,
    run_id INTEGER,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_created INTEGER NOT NULL DEFAULT 0,
    items_updated INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    UNIQUE(update_date, run_id)
);

CREATE TABLE IF NOT EXISTS daily_update_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id INTEGER NOT NULL,
    resource_url TEXT NOT NULL,
    resource_id INTEGER,
    publisher TEXT,
    title TEXT,
    action TEXT NOT NULL DEFAULT 'seen',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(update_id, resource_url),
    FOREIGN KEY(update_id) REFERENCES daily_updates(id),
    FOREIGN KEY(resource_id) REFERENCES resources(id)
);

CREATE INDEX IF NOT EXISTS idx_daily_updates_date ON daily_updates(update_date);
CREATE INDEX IF NOT EXISTS idx_daily_items_update ON daily_update_items(update_id);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    publishers_seen INTEGER NOT NULL DEFAULT 0,
    resource_links_seen INTEGER NOT NULL DEFAULT 0,
    resources_created INTEGER NOT NULL DEFAULT 0,
    resources_updated INTEGER NOT NULL DEFAULT 0,
    publishers_created INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS crawl_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    url TEXT,
    stage TEXT,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES crawl_runs(id)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def database_path() -> Path:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(database_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "publishers", "expected_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "publishers", "resource_links_seen", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "publishers", "resources_collected", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "publishers", "canonical_key", "TEXT")
    ensure_column(conn, "publishers", "previous_names", "TEXT")
    ensure_column(conn, "resources", "daily_seen_at", "TEXT")
    ensure_column(conn, "daily_updates", "window_started_at", "TEXT")
    ensure_column(conn, "daily_updates", "window_finished_at", "TEXT")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def all_rows(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid
