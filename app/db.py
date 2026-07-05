"""
SQLite-backed queue store.

Each job stores:
  - Raw request headers + body path for byte-for-byte replay to the printer
  - UI metadata: filename, file size, material mappings, tool count, estimated
    print time, leveling flag — everything needed to build a queue UI
  - queue_position (REAL): explicit ordering for queued jobs. Float so we can
    insert between items cheaply. Sorted ascending; lowest = next to print.
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
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                status          TEXT    NOT NULL DEFAULT 'queued',
                created_at      REAL    NOT NULL,
                sent_at         REAL,
                error           TEXT,

                queue_position  REAL    NOT NULL DEFAULT 0,

                raw_headers     TEXT    NOT NULL,
                body_path       TEXT    NOT NULL,
                gcode_path      TEXT,

                filename        TEXT    NOT NULL,
                file_size       INTEGER NOT NULL DEFAULT 0,
                tool_count      INTEGER NOT NULL DEFAULT 1,
                use_matl_station INTEGER NOT NULL DEFAULT 0,
                leveling_before_print INTEGER NOT NULL DEFAULT 0,
                printing_time   INTEGER NOT NULL DEFAULT 0,
                total_layers    INTEGER NOT NULL DEFAULT 0,
                material_mappings TEXT,
                content_type    TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        _conn.commit()
        # Migration: add queue_position to existing DBs
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(jobs)")}
        if "queue_position" not in cols:
            _conn.execute("ALTER TABLE jobs ADD COLUMN queue_position REAL NOT NULL DEFAULT 0")
            # Assign positions based on existing id order
            rows = _conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY id ASC"
            ).fetchall()
            for i, row in enumerate(rows):
                _conn.execute(
                    "UPDATE jobs SET queue_position = ? WHERE id = ?",
                    (float(i + 1), row[0]),
                )
            _conn.commit()
    return _conn


def _next_position(conn: sqlite3.Connection) -> float:
    """Return a position value one step after the current last queued job."""
    row = conn.execute(
        "SELECT MAX(queue_position) FROM jobs WHERE status = 'queued'"
    ).fetchone()
    current_max = row[0] if row[0] is not None else 0.0
    return current_max + 1.0


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
        pos = _next_position(conn)
        cur = conn.execute(
            """INSERT INTO jobs (
                status, created_at, queue_position,
                raw_headers, body_path, content_type,
                filename, file_size, tool_count,
                use_matl_station, leveling_before_print,
                printing_time, total_layers, material_mappings
               ) VALUES ('queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                pos,
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
            "SELECT * FROM jobs WHERE status = ? ORDER BY queue_position ASC, id ASC",
            (status,),
        ).fetchall()
    elif include_deleted:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY queue_position ASC, id ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status != 'deleted' ORDER BY queue_position ASC, id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def next_queued_job() -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'queued' ORDER BY queue_position ASC, id ASC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None


def reorder_queue(ordered_ids: list[int]) -> None:
    """
    Set queue_position for queued jobs based on the supplied ID order.
    Only queued jobs are repositioned; non-queued IDs in the list are ignored.
    Positions are assigned as 1.0, 2.0, 3.0 ... for clean integer values.
    """
    with _lock:
        conn = get_conn()
        queued_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued'"
            ).fetchall()
        }
        pos = 1.0
        for job_id in ordered_ids:
            if job_id in queued_ids:
                conn.execute(
                    "UPDATE jobs SET queue_position = ? WHERE id = ?",
                    (pos, job_id),
                )
                pos += 1.0
        conn.commit()


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


def requeue_job(job_id: int) -> bool:
    """Reset any job back to queued at the end of the queue."""
    with _lock:
        conn = get_conn()
        pos = _next_position(conn)
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', sent_at = NULL, error = NULL, queue_position = ? WHERE id = ?",
            (pos, job_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_job(job_id: int) -> bool:
    """Soft-delete a job."""
    with _lock:
        conn = get_conn()
        cur = conn.execute("UPDATE jobs SET status = 'deleted' WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0


def clear_queue() -> int:
    """Soft-delete all queued/error jobs."""
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "UPDATE jobs SET status = 'deleted' WHERE status IN ('queued', 'error')"
        )
        conn.commit()
        return cur.rowcount


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
    """Permanently remove all soft-deleted rows."""
    with _lock:
        conn = get_conn()
        cur = conn.execute("DELETE FROM jobs WHERE status = 'deleted'")
        conn.commit()
        return cur.rowcount