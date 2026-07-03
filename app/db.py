"""
Simple SQLite-backed queue store.

Traffic here is tiny (a handful of uploads/sends a day), so a single
sqlite3 connection guarded by a lock is more than sufficient -- no need
for a heavier database.
"""
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(settings.DATA_DIR).mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at REAL NOT NULL,
                sent_at REAL,
                error TEXT
            )
            """
        )
        _conn.commit()
    return _conn


def add_job(filename: str, filepath: str) -> dict[str, Any]:
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO jobs (filename, filepath, status, created_at) VALUES (?, ?, 'queued', ?)",
            (filename, filepath, time.time()),
        )
        conn.commit()
        return get_job(cur.lastrowid)  # type: ignore[arg-type]


def get_job(job_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(status: str | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]


def next_queued_job() -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def set_status(job_id: int, status: str, error: str | None = None) -> None:
    with _lock:
        conn = get_conn()
        if status == "sent":
            conn.execute(
                "UPDATE jobs SET status = ?, sent_at = ?, error = ? WHERE id = ?",
                (status, time.time(), error, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ? WHERE id = ?",
                (status, error, job_id),
            )
        conn.commit()


def delete_job(job_id: int) -> bool:
    with _lock:
        conn = get_conn()
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
