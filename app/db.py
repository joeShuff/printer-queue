"""
SQLite-backed queue store.

Schema stores everything needed to replay a job to the real printer:
- The file itself (filepath)
- Upload metadata headers OrcaSlicer sent (upload_headers JSON)
- The materialMappings from /printGcode (material_mappings JSON)
- Whether the IFS was enabled (use_matl_station)
- Whether to level before print (leveling_before_print)
"""
import json
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
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                filename             TEXT    NOT NULL,
                filepath             TEXT    NOT NULL,
                status               TEXT    NOT NULL DEFAULT 'queued',
                upload_headers       TEXT,   -- JSON dict of headers from /uploadGcode
                material_mappings    TEXT,   -- JSON list from /printGcode
                use_matl_station     INTEGER DEFAULT 0,
                leveling_before_print INTEGER DEFAULT 1,
                created_at           REAL    NOT NULL,
                sent_at              REAL,
                error                TEXT
            )
            """
        )
        _conn.commit()
    return _conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    # Deserialise JSON blobs
    for field in ("upload_headers", "material_mappings"):
        raw = d.get(field)
        if raw:
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[field] = None
    # Booleans
    d["use_matl_station"] = bool(d.get("use_matl_station"))
    d["leveling_before_print"] = bool(d.get("leveling_before_print", 1))
    return d


def add_job(
    filename: str,
    filepath: str,
    upload_headers: dict | None = None,
) -> dict[str, Any]:
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            """INSERT INTO jobs
               (filename, filepath, status, upload_headers, leveling_before_print, created_at)
               VALUES (?, ?, 'queued', ?, ?, ?)""",
            (
                filename,
                filepath,
                json.dumps(upload_headers or {}),
                1 if settings.LEVELING_BEFORE_PRINT else 0,
                time.time(),
            ),
        )
        conn.commit()
        return get_job(cur.lastrowid)  # type: ignore[arg-type]


def set_print_gcode_data(
    job_id: int,
    material_mappings: list,
    use_matl_station: bool,
    leveling_before_print: bool,
) -> None:
    """Attach /printGcode payload to an existing queued job."""
    with _lock:
        conn = get_conn()
        conn.execute(
            """UPDATE jobs
               SET material_mappings = ?, use_matl_station = ?, leveling_before_print = ?
               WHERE id = ?""",
            (
                json.dumps(material_mappings),
                1 if use_matl_station else 0,
                1 if leveling_before_print else 0,
                job_id,
            ),
        )
        conn.commit()


def get_job(job_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(status: str | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
    return [_row_to_dict(r) for r in rows]


def next_queued_job() -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None


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