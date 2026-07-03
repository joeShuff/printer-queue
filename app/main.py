"""
3D Printer Queue Service
========================

OctoPrint-compatible upload endpoint so OrcaSlicer can push directly to this
server via its "Print Host" setting.  Queue management and print dispatch are
separate endpoints for Home Assistant to trigger.

OctoPrint API subset implemented
---------------------------------
GET  /api/version              -- version handshake (OrcaSlicer checks this)
GET  /api/files                -- list files (OrcaSlicer checks after upload)
GET  /api/files/local          -- same
POST /api/files/local          -- upload a file (OrcaSlicer's upload endpoint)

Queue endpoints
---------------
GET  /queue                    -- list all jobs + their status
GET  /queue/status             -- current printer state + next queued job
POST /queue/next               -- send next queued job to printer  <-- HA button
DELETE /queue/{id}             -- remove a queued job
"""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from . import db
from .config import settings
from .printer import get_printer_status, send_job

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

app = FastAPI(title="3D Printer Queue", version="1.0.0")

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    OrcaSlicer always sends X-Api-Key in its upload request.  If API_KEY is
    set in the environment, every request must carry a matching header.
    Leave API_KEY unset to run without auth (fine on a private LAN).
    """
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# OctoPrint compatibility shim
# ---------------------------------------------------------------------------

@app.get("/api/version", dependencies=[Depends(verify_api_key)])
async def api_version() -> dict[str, Any]:
    """OrcaSlicer checks this endpoint before uploading to confirm the host is valid."""
    return {
        "api": "0.1",
        "server": "1.0.0",
        "text": "OctoPrint (Printer Queue shim)",
    }


@app.get("/api/files", dependencies=[Depends(verify_api_key)])
@app.get("/api/files/local", dependencies=[Depends(verify_api_key)])
async def list_files() -> dict[str, Any]:
    """
    OrcaSlicer polls this after upload to confirm the file arrived.
    We return all queued jobs formatted as OctoPrint file entries.
    """
    jobs = db.list_jobs()
    files = [
        {
            "name": j["filename"],
            "path": j["filename"],
            "type": "machinecode",
            "typePath": ["machinecode"],
            "size": _file_size(j["filepath"]),
            "date": int(j["created_at"]),
            "refs": {"resource": f"/api/files/local/{j['filename']}"},
            "queue_id": j["id"],
            "queue_status": j["status"],
        }
        for j in jobs
    ]
    return {"files": files, "free": 0, "total": 0}


def _file_size(filepath: str) -> int:
    try:
        return os.path.getsize(filepath)
    except OSError:
        return 0


@app.post("/api/files/local", dependencies=[Depends(verify_api_key)])
async def upload_file(
    file: UploadFile = File(..., alias="file"),
) -> JSONResponse:
    """
    OctoPrint-compatible upload endpoint.

    OrcaSlicer posts the sliced file here (either .gcode or .gcode.3mf
    depending on your OrcaSlicer version and printer profile settings).
    We save it to disk and add it to the queue.

    In OrcaSlicer:
        Printer → Print Host → set to OctoPrint
        URL: http://<this-server-ip>:<port>
        API key: whatever you set in API_KEY env var
    """
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # OrcaSlicer can send either .gcode or .gcode.3mf
    filename = file.filename or "unknown.gcode"
    dest = Path(settings.UPLOAD_DIR) / filename

    # Avoid clobbering duplicates by appending a counter
    counter = 1
    while dest.exists():
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        suffix = filename[len(stem):]
        dest = Path(settings.UPLOAD_DIR) / f"{stem}_{counter}{suffix}"
        counter += 1

    content = await file.read()
    dest.write_bytes(content)
    log.info("Saved upload: %s (%d bytes)", dest.name, len(content))

    job = db.add_job(filename=dest.name, filepath=str(dest))
    log.info("Queued job id=%d filename=%s", job["id"], dest.name)

    return JSONResponse(
        status_code=201,
        content={
            "done": True,
            "files": {
                "local": {
                    "name": dest.name,
                    "path": dest.name,
                    "type": "machinecode",
                    "refs": {"resource": f"/api/files/local/{dest.name}"},
                }
            },
            "queue_id": job["id"],
        },
    )


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

@app.get("/queue", dependencies=[Depends(verify_api_key)])
async def get_queue() -> dict[str, Any]:
    """Return the full job list."""
    jobs = db.list_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/queue/status", dependencies=[Depends(verify_api_key)])
async def queue_status() -> dict[str, Any]:
    """
    Return live printer state plus the next file waiting in the queue.
    This is what Home Assistant can call to show printer state as a sensor.
    """
    printer = await get_printer_status()
    next_job = db.next_queued_job()
    return {
        "printer": printer,
        "next_job": next_job,
        "queued_count": len(db.list_jobs(status="queued")),
    }


@app.post("/queue/next", dependencies=[Depends(verify_api_key)])
async def send_next_job() -> dict[str, Any]:
    """
    Pick the oldest queued job and send it to the printer.
    This is the endpoint your Home Assistant button should call:

        POST http://<server>:<port>/queue/next
        Header: X-Api-Key: <your-key>

    Returns 200 on success, 409 if the queue is empty, 502 if the printer
    rejects the job.
    """
    job = db.next_queued_job()
    if not job:
        raise HTTPException(status_code=409, detail="Queue is empty")

    db.set_status(job["id"], "sending")

    ok, message = await send_job(job["filepath"])

    if ok:
        db.set_status(job["id"], "sent")
        log.info("Job %d sent successfully: %s", job["id"], message)
        return {"success": True, "job": job, "message": message}
    else:
        db.set_status(job["id"], "error", error=message)
        log.error("Job %d failed: %s", job["id"], message)
        raise HTTPException(status_code=502, detail=f"Printer error: {message}")


@app.delete("/queue/{job_id}", dependencies=[Depends(verify_api_key)])
async def delete_job(job_id: int) -> dict[str, Any]:
    """Remove a job from the queue (also deletes the file from disk)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Delete from DB
    db.delete_job(job_id)

    # Delete the file (best-effort)
    try:
        Path(job["filepath"]).unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not delete file %s: %s", job["filepath"], e)

    return {"deleted": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Health check (no auth required — useful for Docker healthcheck)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
