"""
SQLite-backed queue store.

Each job stores:
  - Raw request headers + body path for byte-for-like replay to the printer
  - UI metadata: filename, file size, material mappings, tool count, estimated
    print time, leveling flag — everything needed to build a queue UI
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
                -- Identity
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                status          TEXT    NOT NULL DEFAULT 'queued',
                created_at      REAL    NOT NULL,
                sent_at         REAL,
                error           TEXT,

                -- Raw replay data
                raw_headers     TEXT    NOT NULL,  -- JSON dict of all request headers
                body_path       TEXT    NOT NULL,  -- path to the saved raw multipart body on disk
                gcode_path      TEXT,              -- path to the extracted 3mf/gcode file

                -- UI metadata (decoded from the request at upload time)
                filename        TEXT    NOT NULL,
                file_size       INTEGER NOT NULL DEFAULT 0,
                tool_count      INTEGER NOT NULL DEFAULT 1,
                use_matl_station INTEGER NOT NULL DEFAULT 0,
                leveling_before_print INTEGER NOT NULL DEFAULT 0,
                printing_time   INTEGER NOT NULL DEFAULT 0,  -- seconds (from prediction key)
                total_layers    INTEGER NOT NULL DEFAULT 0,
                material_mappings TEXT,  -- JSON list decoded from materialMappings header
                content_type    TEXT    NOT NULL  -- multipart content-type with boundary
            )
            """
        )
        _conn.commit()
    return _conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for field in ("raw_headers", "material_mappings"):
        raw = d.get(field)
        if raw:
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[field] = None
    d["use_matl_station"] = bool(d.get("use_matl_station"))
    d["leveling_before_print"] = bool(d.get("leveling_before_print"))
    return d


def add_job(
    raw_headers: dict,
    body_path: str,
    content_type: str,
    filename: str,
    file_size: int,
    tool_count: int,
    use_matl_station: bool,
    leveling_before_print: bool,
    printing_time: int,
    total_layers: int,
    material_mappings: list,
) -> dict[str, Any]:
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            """INSERT INTO jobs (
                status, created_at,
                raw_headers, body_path, content_type,
                filename, file_size, tool_count,
                use_matl_station, leveling_before_print,
                printing_time, total_layers, material_mappings
               ) VALUES ('queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                json.dumps(raw_headers),
                body_path,
                content_type,
                filename,
                file_size,
                tool_count,
                1 if use_matl_station else 0,
                1 if leveling_before_print else 0,
                printing_time,
                total_layers,
                json.dumps(material_mappings),
            ),
        )
        conn.commit()
        return get_job(cur.lastrowid)  # type: ignore[arg-type]


def set_body_path(job_id: int, body_path: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE jobs SET body_path = ? WHERE id = ?", (body_path, job_id))
        conn.commit()


def set_gcode_path(job_id: int, gcode_path: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE jobs SET gcode_path = ? WHERE id = ?", (gcode_path, job_id))
        conn.commit()


def set_print_meta(job_id: int, printing_time: int, total_layers: int) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "UPDATE jobs SET printing_time = ?, total_layers = ? WHERE id = ?",
            (printing_time, total_layers, job_id),
        )
        conn.commit()


def get_job(job_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(status: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC", (status,)
        ).fetchall()
    elif include_deleted:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status != 'deleted' ORDER BY id ASC"
        ).fetchall()
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


def clear_queue() -> int:
    """Soft-delete all jobs that haven't been sent yet. Returns count affected."""
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "UPDATE jobs SET status = 'deleted' WHERE status IN ('queued', 'error')"
        )
        conn.commit()
        return cur.rowcount
    with _lock:
        conn = get_conn()
        cur = conn.execute("UPDATE jobs SET status = 'deleted' WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0


def active_file_paths() -> set[str]:
    """Return all file paths (body + gcode) referenced by any non-deleted job."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT body_path, gcode_path FROM jobs WHERE status != 'deleted'"
    ).fetchall()
    paths = set()
    for r in rows:
        if r["body_path"]:
            paths.add(r["body_path"])
        if r["gcode_path"]:
            paths.add(r["gcode_path"])
    return paths


def hard_delete_deleted_jobs() -> int:
    """Permanently remove all soft-deleted rows. Call after files are cleaned up."""
    with _lock:
        conn = get_conn()
        cur = conn.execute("DELETE FROM jobs WHERE status = 'deleted'")
        conn.commit()
        return cur.rowcount