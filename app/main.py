"""
FlashForge AD5X Printer Queue - Transparent Proxy Mode
=======================================================

Sits between OrcaSlicer and your AD5X. Proxies all status/control endpoints
live to the printer. Intercepts /uploadGcode, saves the raw request body to
disk and stores metadata in SQLite. /queue/next replays the saved request
byte-for-byte to the printer.

OrcaSlicer setup (FlashForge, not OctoPrint):
  IP:           <this server's IP>
  Port:         8898
  Serial:       same as PRINTER_SERIAL env var
  Check Code:   same as PRINTER_CHECK_CODE env var

Endpoints
---------
PROXIED:
  POST /product      printer capabilities
  POST /detail       live status + IFS slot info → populates OrcaSlicer picker
  POST /control      pause/resume/cancel etc.
  POST /gcodeList    file list on printer
  POST /gcodeThumb   thumbnail from printer

INTERCEPTED:
  POST /uploadGcode  save raw body + headers, queue the job

QUEUE (Home Assistant / UI):
  GET  /queue                     list jobs (add ?include_deleted=true for all)
  GET  /queue/status              live printer state + next queued job
  POST /queue/next                send next queued job to printer
  DELETE /queue/{id}              soft-delete a job
  POST /queue/cleanup             delete orphaned files + purge deleted DB rows

UTILITY:
  GET  /health                    no-auth health check
"""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import db
from .config import settings
from .threemf import extract_meta, extract_thumbnail

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

app = FastAPI(title="FlashForge Printer Queue Proxy", version="2.0.0")

PRINTER_BASE = f"http://{settings.PRINTER_IP}:8898"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_auth(serial: str, check_code: str) -> bool:
    return (serial == settings.PRINTER_SERIAL and
            check_code == settings.PRINTER_CHECK_CODE)


async def _proxy(path: str, body: dict) -> dict:
    url = f"{PRINTER_BASE}{path}"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                return await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        log.error("Proxy to %s failed: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Printer unreachable: {exc}")


def _decode_mappings(raw_b64: str) -> list:
    """Decode base64 materialMappings header → list."""
    if not raw_b64:
        return []
    try:
        return json.loads(base64.b64decode(raw_b64).decode())
    except Exception as exc:
        log.warning("Failed to decode materialMappings: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Proxied endpoints
# ---------------------------------------------------------------------------

@app.post("/product")
async def product(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/product", body))


@app.post("/detail")
async def detail(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/detail", body))


@app.post("/control")
async def control(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/control", body))


@app.post("/gcodeList")
async def gcode_list(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/gcodeList", body))


@app.post("/gcodeThumb")
async def gcode_thumb(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/gcodeThumb", body))


# ---------------------------------------------------------------------------
# Intercepted: /uploadGcode
# ---------------------------------------------------------------------------

@app.post("/uploadGcode")
async def upload_gcode(request: Request) -> JSONResponse:
    """
    Save the raw multipart body to disk and record the job in the queue.
    Nothing is forwarded to the printer yet.
    """
    serial = request.headers.get("serialnumber", "")
    check_code = request.headers.get("checkcode", "")
    if not _check_auth(serial, check_code):
        return JSONResponse({"code": 1, "message": "Authentication failed"})

    # -- Read the raw body as-is --
    raw_body = await request.body()

    # -- Parse multipart just enough to pull UI metadata --
    try:
        form = await request.form()
    except Exception as exc:
        log.error("Failed to parse multipart form: %s", exc)
        return JSONResponse({"code": 1, "message": "Bad multipart body"})

    file_field = form.get("gcodeFile")
    if file_field is None:
        return JSONResponse({"code": 1, "message": "No gcodeFile field"})

    filename = file_field.filename or "unknown.gcode"
    file_bytes = await file_field.read()
    file_size = len(file_bytes)

    # -- Decode UI metadata from headers --
    material_mappings = _decode_mappings(request.headers.get("materialmappings", ""))
    use_matl_station  = request.headers.get("usematlstation", "false").lower() == "true"
    leveling          = request.headers.get("levelingbeforeprint", "false").lower() == "true"
    tool_count        = int(request.headers.get("gcodetoolcnt", 1))
    printing_time     = int(request.headers.get("printingtime", 0))
    total_layers      = int(request.headers.get("totallayers", 0))

    # -- Strip hop-by-hop headers, keep OrcaSlicer metadata ones --
    skip = {"host", "content-length", "transfer-encoding", "connection"}
    raw_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}

    # -- Save raw body to disk, named by job ID to avoid any collisions --
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # We need the job ID before we can name the file, so insert the row first
    # with a placeholder path, then update it once we know the ID.
    job = db.add_job(
        raw_headers=raw_headers,
        body_path="",  # filled in below
        content_type=request.headers.get("content-type", ""),
        filename=filename,
        file_size=file_size,
        tool_count=tool_count,
        use_matl_station=use_matl_station,
        leveling_before_print=leveling,
        printing_time=printing_time,
        total_layers=total_layers,
        material_mappings=material_mappings,
    )

    body_path = Path(settings.UPLOAD_DIR) / f"{job['id']}.body"
    body_path.write_bytes(raw_body)
    db.set_body_path(job["id"], str(body_path))
    job["body_path"] = str(body_path)

    # Preserve compound extensions like .gcode.3mf
    name = Path(filename).name
    suffix = name[name.index("."):]  if "." in name else ""
    gcode_path = Path(settings.UPLOAD_DIR) / f"{job['id']}{suffix}"
    gcode_path.write_bytes(file_bytes)
    db.set_gcode_path(job["id"], str(gcode_path))

    # Extract print time + layer count from the 3mf metadata.
    # OrcaSlicer does not send these as headers for .gcode.3mf uploads so we
    # parse them out of slice_info.config inside the archive.
    meta = extract_meta(gcode_path)
    if meta.printing_time or meta.total_layers:
        db.set_print_meta(job["id"], meta.printing_time, meta.total_layers)
        log.info("Job %d: printing_time=%ds total_layers=%d",
                 job["id"], meta.printing_time, meta.total_layers)

    log.info("Queued job id=%d filename=%s size=%d", job["id"], filename, file_size)

    # -- Auto-dispatch if OrcaSlicer requested printNow and printer is idle --
    print_now = request.headers.get("printnow", "false").lower() == "true"
    if print_now:
        if await _printer_is_idle():
            log.info("Job %d: printNow=true and printer idle — dispatching immediately", job["id"])
            asyncio.create_task(_dispatch_job(job))
        else:
            log.info("Job %d: printNow=true but printer is busy — job queued", job["id"])

    return JSONResponse({"code": 0, "message": "Success"})


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------

async def _printer_is_idle() -> bool:
    """Return True if the printer is connected and not currently printing."""
    if not settings.PRINTER_IP:
        return False
    try:
        result = await _proxy("/detail", {
            "serialNumber": settings.PRINTER_SERIAL,
            "checkCode":    settings.PRINTER_CHECK_CODE,
        })
        state = result.get("detail", {}).get("status", "").lower()
        return state in ("ready", "idle", "free")
    except Exception:
        return False


async def _dispatch_job(job: dict) -> None:
    """
    Upload a queued job's saved raw body to the real printer.
    Updates job status to 'sending' → 'sent' or 'error'.
    Safe to call from a background task.
    """
    job_id = job["id"]
    db.set_status(job_id, "sending")

    body_path = Path(job["body_path"])
    if not body_path.exists():
        msg = f"Raw body file missing from disk: {body_path}"
        log.error("Job %d: %s", job_id, msg)
        db.set_status(job_id, "error", msg)
        return

    try:
        raw_body = body_path.read_bytes()
        headers = dict(job["raw_headers"])
        headers["serialnumber"] = settings.PRINTER_SERIAL
        headers["checkcode"]    = settings.PRINTER_CHECK_CODE
        headers["printnow"]     = "true"

        url     = f"{PRINTER_BASE}/uploadGcode"
        timeout = aiohttp.ClientTimeout(total=120)

        log.info("Dispatching job %d to printer: %s (%d bytes)",
                 job_id, job["filename"], len(raw_body))

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=raw_body, headers=headers) as resp:
                result = await resp.json(content_type=None)

        if result.get("code") != 0:
            msg = f"Printer rejected job: {result}"
            log.error("Job %d: %s", job_id, msg)
            db.set_status(job_id, "error", msg)
            return

        db.set_status(job_id, "sent")
        log.info("Job %d sent OK", job_id)

    except Exception as exc:
        msg = str(exc)
        log.exception("_dispatch_job failed for job %d", job_id)
        db.set_status(job_id, "error", msg)


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

@app.get("/queue")
async def get_queue(include_deleted: bool = False) -> dict[str, Any]:
    jobs = db.list_jobs(include_deleted=include_deleted)

    # Fetch live IFS slot colours from the printer and patch them into the
    # material_mappings so the UI always shows the current filament colours,
    # even if spools were swapped since the job was uploaded.
    live_slots: dict[int, dict] = {}
    if settings.PRINTER_IP:
        try:
            result = await _proxy("/detail", {
                "serialNumber": settings.PRINTER_SERIAL,
                "checkCode":    settings.PRINTER_CHECK_CODE,
            })
            for slot in result.get("detail", {}).get("matlStationInfo", {}).get("slotInfos", []):
                slot_id = slot.get("slotIndex") or slot.get("slotId")
                if slot_id is not None:
                    live_slots[int(slot_id)] = slot
        except Exception:
            pass  # printer offline — return stored colours as-is

    if live_slots:
        for job in jobs:
            mappings = job.get("material_mappings") or []
            for m in mappings:
                slot = live_slots.get(int(m.get("slotId", -1)))
                if slot:
                    m["slotMaterialColor"] = slot.get("materialColor", m.get("slotMaterialColor"))
                    m["materialName"] = slot.get("materialType", m.get("materialName"))

    return {"jobs": jobs, "count": len(jobs)}


@app.get("/queue/status")
async def queue_status() -> dict[str, Any]:
    next_job = db.next_queued_job()
    queued_count = len(db.list_jobs(status="queued"))

    printer_state: dict[str, Any] = {"state": "unknown"}
    if settings.PRINTER_IP:
        try:
            raw = await _proxy("/detail", {
                "serialNumber": settings.PRINTER_SERIAL,
                "checkCode":    settings.PRINTER_CHECK_CODE,
            })
            detail = raw.get("detail", {})
            printer_state = {
                "state":    detail.get("status", "unknown"),
                "file":     detail.get("printFileName", ""),
                "progress": detail.get("printProgress", 0),
            }
        except HTTPException:
            printer_state = {"state": "offline"}

    return {
        "printer":      printer_state,
        "next_job":     next_job,
        "queued_count": queued_count,
    }


@app.post("/queue/reorder")
async def reorder_queue(body: dict) -> dict[str, Any]:
    """
    Reorder queued jobs. Body: {"order": [id1, id2, id3, ...]}
    The supplied list defines the new queue_position order, first = next to print.
    Non-queued job IDs are silently ignored.
    """
    order = body.get("order", [])
    if not isinstance(order, list) or not all(isinstance(i, int) for i in order):
        raise HTTPException(status_code=422, detail="order must be a list of integers")
    db.reorder_queue(order)
    return {"reordered": True, "order": order}


@app.post("/queue/next")
async def send_next_job() -> dict[str, Any]:
    """Dispatch the next queued job to the printer. Called from Home Assistant."""
    job = db.next_queued_job()
    if not job:
        raise HTTPException(status_code=409, detail="Queue is empty")

    await _dispatch_job(job)

    job = db.get_job(job["id"])
    if job["status"] == "error":
        raise HTTPException(status_code=502, detail=job.get("error", "Printer error"))

    return {"success": True, "job": job}


@app.post("/queue/{job_id}/requeue")
async def requeue_job(job_id: int) -> dict[str, Any]:
    """Move any job back to queued status so it will be picked up by Send Next."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.requeue_job(job_id)
    return {"requeued": True, "job": db.get_job(job_id)}


@app.post("/queue/{job_id}/send")
async def send_specific_job(job_id: int) -> dict[str, Any]:
    """Immediately dispatch a specific job to the printer regardless of queue position or printer state."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "deleted":
        raise HTTPException(status_code=400, detail="Cannot send a deleted job")

    # Force status back to queued so _dispatch_job can update it properly
    db.requeue_job(job_id)
    job = db.get_job(job_id)

    await _dispatch_job(job)

    job = db.get_job(job_id)
    if job["status"] == "error":
        raise HTTPException(status_code=502, detail=job.get("error", "Printer error"))

    return {"success": True, "job": job}


@app.get("/queue/{job_id}/thumbnail")
async def job_thumbnail(job_id: int) -> Response:
    """Return the plate thumbnail PNG extracted from the job's .gcode.3mf."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    gcode_path = job.get("gcode_path")
    if not gcode_path:
        raise HTTPException(status_code=404, detail="No file saved for this job")
    png = extract_thumbnail(gcode_path)
    if not png:
        raise HTTPException(status_code=404, detail="No thumbnail in this file")
    return Response(content=png, media_type="image/png")


@app.delete("/queue/{job_id}")
async def delete_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    db.delete_job(job_id)
    return {"deleted": True, "job_id": job_id}


@app.post("/queue/clear")
async def clear_queue() -> dict[str, Any]:
    """Soft-delete all queued (and errored) jobs that haven't been sent."""
    count = db.clear_queue()
    return {"cleared": count}


@app.post("/queue/cleanup")
async def cleanup() -> dict[str, Any]:
    """
    Delete body files on disk no longer referenced by active jobs,
    then hard-purge deleted rows from the DB.
    """
    active = db.active_file_paths()
    upload_dir = Path(settings.UPLOAD_DIR)
    removed_files: list[str] = []
    errors: list[str] = []

    if upload_dir.exists():
        for f in upload_dir.iterdir():
            if not f.is_file():
                continue
            if str(f) not in active:
                try:
                    f.unlink()
                    removed_files.append(f.name)
                    log.info("Cleanup: removed %s", f.name)
                except OSError as exc:
                    errors.append(f"{f.name}: {exc}")

    purged_rows = db.hard_delete_deleted_jobs()
    log.info("Cleanup: purged %d deleted rows, removed %d files", purged_rows, len(removed_files))
    return {"removed_files": removed_files, "purged_rows": purged_rows, "errors": errors}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    """Serve the queue management UI."""
    ui_path = Path(__file__).parent / "ui.html"
    if not ui_path.exists():
        raise HTTPException(status_code=404, detail="UI not built. See ui.html.")
    return HTMLResponse(ui_path.read_text())



async def health() -> dict[str, str]:
    return {"status": "ok"}