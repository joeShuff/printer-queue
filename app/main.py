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
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request / response models (used by OpenAPI docs)
# ---------------------------------------------------------------------------

class ControlRequest(BaseModel):
    cmd: str = Field(..., example="jobCtl_cmd",
                     description="Control command name")
    args: dict = Field(default={}, example={"action": "pause"},
                       description="Command arguments")

class ReorderRequest(BaseModel):
    order: list[int] = Field(..., example=[3, 1, 2],
                             description="Job IDs in desired queue order (first = next to print)")

# ---------------------------------------------------------------------------
# Tags for grouping endpoints in Swagger UI
# ---------------------------------------------------------------------------

TAGS = [
    {"name": "Queue",   "description": "Manage the print queue"},
    {"name": "Printer", "description": "Live printer status and control"},
    {"name": "Proxy",   "description": "FlashForge protocol endpoints proxied to the real printer — used by OrcaSlicer, not intended for direct use"},
    {"name": "Utility", "description": "Health check and server info"},
]

from .threemf import extract_meta, extract_thumbnail
from . import db
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

app = FastAPI(
    title="AD5X Print Queue",
    version="2.0.0",
    description="""
A self-hosted print queue proxy for the FlashForge AD5X with IFS.

OrcaSlicer connects to this server instead of the printer directly.
Status and control requests are forwarded live to the real printer; uploads are intercepted and queued.

Use `/queue/next` or per-job `/queue/{id}/send` to dispatch jobs when ready.
""",
    openapi_tags=TAGS,
)

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

@app.post("/product", tags=["Proxy"], summary="Printer capabilities", include_in_schema=True)
async def product(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/product", body))


@app.post("/detail", tags=["Proxy"], summary="Live printer status and IFS slot info")
async def detail(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/detail", body))


@app.post("/control", tags=["Proxy"], summary="Raw control passthrough")
async def control(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/control", body))


@app.post("/gcodeList", tags=["Proxy"], summary="File list on printer")
async def gcode_list(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/gcodeList", body))


@app.post("/gcodeThumb", tags=["Proxy"], summary="Thumbnail from printer storage")
async def gcode_thumb(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    return JSONResponse(await _proxy("/gcodeThumb", body))


# ---------------------------------------------------------------------------
# Intercepted: /uploadGcode
# ---------------------------------------------------------------------------

@app.post("/uploadGcode", tags=["Proxy"], summary="Intercept OrcaSlicer upload and queue the job")
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

@app.get("/queue", tags=["Queue"], summary="List all jobs")
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



# ---------------------------------------------------------------------------
# MJPEG camera stream relay
# ---------------------------------------------------------------------------
# The AD5X camera only accepts one connection at a time. We connect once and
# fan the stream out to every browser client via per-client asyncio queues.

_stream_clients: set[asyncio.Queue] = set()
_stream_task: asyncio.Task | None = None
_stream_url: str | None = None   # populated from /detail on first use


async def _fetch_stream_url() -> str | None:
    """Get the camera stream URL from the printer's /detail endpoint."""
    try:
        result = await _proxy("/detail", {
            "serialNumber": settings.PRINTER_SERIAL,
            "checkCode":    settings.PRINTER_CHECK_CODE,
        })
        url = result.get("detail", {}).get("cameraStreamUrl", "")
        return url if url else None
    except Exception:
        return None


async def _stream_relay() -> None:
    """
    Background task: connect to the camera MJPEG stream and broadcast
    each frame to all connected client queues.
    Reconnects automatically on disconnect.
    """
    global _stream_url
    SENTINEL = None  # signals clients to close

    while True:
        if not _stream_clients:
            await asyncio.sleep(1)
            continue

        if not _stream_url:
            _stream_url = await _fetch_stream_url()
            if not _stream_url:
                log.warning("Camera stream URL not available — retrying in 5s")
                await asyncio.sleep(5)
                continue

        log.info("Camera relay: connecting to %s (%d client(s))", _stream_url, len(_stream_clients))
        try:
            timeout = aiohttp.ClientTimeout(total=None, connect=5, sock_read=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_stream_url) as resp:
                    if resp.status != 200:
                        log.warning("Camera returned %d — retrying in 5s", resp.status)
                        await asyncio.sleep(5)
                        continue

                    # Forward raw bytes chunks to every client queue
                    async for chunk in resp.content.iter_any():
                        if not chunk:
                            continue
                        dead = set()
                        for q in _stream_clients:
                            try:
                                q.put_nowait(chunk)
                            except asyncio.QueueFull:
                                dead.add(q)   # slow/gone client — drop it
                        _stream_clients.difference_update(dead)
                        if not _stream_clients:
                            log.info("Camera relay: no clients, disconnecting")
                            return

        except Exception as exc:
            log.warning("Camera relay disconnected (%s) — reconnecting in 2s", exc)
            await asyncio.sleep(2)


def _ensure_relay_running() -> None:
    global _stream_task
    if _stream_task is None or _stream_task.done():
        _stream_task = asyncio.create_task(_stream_relay())


@app.get("/printer/stream", tags=["Printer"], summary="MJPEG camera stream (multi-client relay)")
async def camera_stream() -> Response:
    """
    Relay the printer's MJPEG camera stream. Connects to the camera once
    and fans the stream to all connected clients, working around the
    camera's single-connection limit.

    Use as an <img src="/printer/stream"> in the UI.
    """
    if not settings.PRINTER_IP:
        raise HTTPException(status_code=503, detail="Printer not configured")

    q: asyncio.Queue = asyncio.Queue(maxsize=30)
    _stream_clients.add(q)
    _ensure_relay_running()

    async def generate():
        try:
            while True:
                chunk = await asyncio.wait_for(q.get(), timeout=15)
                if chunk is None:
                    break
                yield chunk
        except asyncio.TimeoutError:
            pass
        finally:
            _stream_clients.discard(q)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=boundarydonotcross",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



async def printer_thumbnail() -> Response:
    """
    Proxy the current print thumbnail from the printer.
    The printer serves it over plain HTTP which browsers block when the UI
    is served over HTTPS (mixed content). This endpoint fetches it server-side
    and re-serves it over the same HTTPS connection as the UI.
    """
    if not settings.PRINTER_IP:
        raise HTTPException(status_code=404, detail="Printer not configured")
    url = f"http://{settings.PRINTER_IP}:8898/getThum"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=404, detail="No thumbnail available")
                data = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                return Response(content=data, media_type=content_type)
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach printer: {exc}")


@app.get("/server/info", tags=["Utility"], summary="OrcaSlicer connection details for this proxy")
async def server_info(request: Request) -> dict[str, Any]:
    """
    Returns the connection details the user should enter in OrcaSlicer
    (i.e. this proxy server's details, not the printer's).
    """
    host = request.headers.get("host", "").split(":")[0]
    port = request.url.port or 8898
    return {
        "proxy": {
            "host": host,
            "port": port,
            "serial": settings.PRINTER_SERIAL,
            "check_code": settings.PRINTER_CHECK_CODE,
        },
        "printer": {
            "ip": settings.PRINTER_IP,
            "serial": settings.PRINTER_SERIAL,
            "check_code": settings.PRINTER_CHECK_CODE,
        },
    }


@app.post("/printer/control", tags=["Printer"], summary="Send a control command to the printer")
async def printer_control(body: ControlRequest) -> JSONResponse:
    """
    Send a control command to the printer.

    Common commands:

    | cmd | args | Description |
    |-----|------|-------------|
    | `jobCtl_cmd` | `{"action": "pause"}` | Pause current print |
    | `jobCtl_cmd` | `{"action": "continue"}` | Resume paused print |
    | `jobCtl_cmd` | `{"action": "cancel"}` | Cancel current print |
    | `stateCtrl_cmd` | `{"action": "setClearPlatform"}` | Confirm bed is empty |
    | `lightControl_cmd` | `{"status": "open"}` | Turn light on |
    | `lightControl_cmd` | `{"status": "close"}` | Turn light off |
    """
    result = await _proxy("/control", {
        "serialNumber": settings.PRINTER_SERIAL,
        "checkCode":    settings.PRINTER_CHECK_CODE,
        "payload": {
            "cmd":  body.cmd,
            "args": body.args,
        },
    })
    return JSONResponse(result)


@app.get("/printer/detail", tags=["Printer"], summary="Full raw detail response from printer")
async def printer_detail() -> JSONResponse:
    """Return the full live /detail response from the printer."""
    result = await _proxy("/detail", {
        "serialNumber": settings.PRINTER_SERIAL,
        "checkCode":    settings.PRINTER_CHECK_CODE,
    })
    return JSONResponse(result)


@app.get("/queue/status", tags=["Queue"], summary="Live printer state and next queued job")
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
                "state":              detail.get("status", "unknown"),
                "file":               detail.get("printFileName", ""),
                "thumb_url":          "/printer/thumbnail" if detail.get("printFileThumbUrl") else "",
                "progress":           detail.get("printProgress", 0),
                "current_layer":      detail.get("printLayer", 0),
                "total_layers":       detail.get("targetPrintLayer", 0),
                "remaining_time":     detail.get("estimatedTime", 0),
                "print_duration":     detail.get("printDuration", 0),
                "nozzle_temp":        detail.get("rightTemp", 0),
                "nozzle_target":      detail.get("rightTargetTemp", 0),
                "bed_temp":           detail.get("platTemp", 0),
                "bed_target":         detail.get("platTargetTemp", 0),
                "chamber_temp":       detail.get("chamberTemp", 0),
                "print_speed":        detail.get("printSpeedAdjust", 100),
                "current_speed":      detail.get("currentPrintSpeed", 0),
                "cooling_fan":        detail.get("coolingFanSpeed", 0),
                "chamber_fan":        detail.get("chamberFanSpeed", 0),
                "light":              detail.get("lightStatus", "close"),
                "z_offset":           detail.get("zAxisCompensation", 0),
                "error_code":         detail.get("errorCode", ""),
                "door_open":          detail.get("doorStatus", "close") == "open",
                "has_camera":         bool(detail.get("cameraStreamUrl")),
                "estimated_right_len": detail.get("estimatedRightLen", 0),
                "cumulative_filament": detail.get("cumulativeFilament", 0),
                "cumulative_time":    detail.get("cumulativePrintTime", 0),
                "firmware":           detail.get("firmwareVersion", ""),
                "current_slot":       detail.get("matlStationInfo", {}).get("currentSlot"),
                "slot_infos":         detail.get("matlStationInfo", {}).get("slotInfos", []),
            }
        except HTTPException:
            printer_state = {"state": "offline"}

    return {
        "printer":      printer_state,
        "next_job":     next_job,
        "queued_count": queued_count,
    }


@app.post("/queue/reorder", tags=["Queue"], summary="Reorder queued jobs")
async def reorder_queue(body: ReorderRequest) -> dict[str, Any]:
    """
    Supply a list of job IDs in the desired order — first ID will print next.
    Only queued jobs are repositioned; other statuses are ignored.
    """
    order = body.order
    if not isinstance(order, list) or not all(isinstance(i, int) for i in order):
        raise HTTPException(status_code=422, detail="order must be a list of integers")
    db.reorder_queue(order)
    return {"reordered": True, "order": order}


@app.post("/queue/next", tags=["Queue"], summary="Send the next queued job to the printer")
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


class UpdateMappingsRequest(BaseModel):
    material_mappings: list = Field(..., description="Updated material mappings list")


@app.post("/queue/{job_id}/mappings", tags=["Queue"], summary="Update material mappings for a queued job")
async def update_mappings(job_id: int, body: UpdateMappingsRequest) -> dict[str, Any]:
    """
    Update the IFS slot assignments for a queued job before it's sent.

    materialMappings is stored as a base64 header in raw_headers — we update
    that so the correct mappings are used when the job is dispatched.
    """
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "queued":
        raise HTTPException(status_code=400, detail="Can only update mappings on queued jobs")

    mappings = body.material_mappings

    # Update the DB record
    if not db.update_material_mappings(job_id, mappings):
        raise HTTPException(status_code=400, detail="Failed to update — job may no longer be queued")

    # Also update the materialMappings header in raw_headers so dispatch
    # sends the correct value (it re-encodes from the DB mappings anyway,
    # but keep raw_headers consistent)
    raw_headers = job.get("raw_headers") or {}
    raw_headers["materialmappings"] = base64.b64encode(
        json.dumps(mappings).encode()
    ).decode()
    db.update_raw_headers(job_id, raw_headers)

    return {"updated": True, "job": db.get_job(job_id)}


@app.post("/queue/{job_id}/requeue", tags=["Queue"], summary="Move a job back to queued")
async def requeue_job(job_id: int) -> dict[str, Any]:
    """Move any job back to queued status so it will be picked up by Send Next."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.requeue_job(job_id)
    return {"requeued": True, "job": db.get_job(job_id)}


@app.post("/queue/{job_id}/send", tags=["Queue"], summary="Immediately dispatch a specific job")
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


@app.get("/queue/{job_id}/thumbnail", tags=["Queue"], summary="Thumbnail extracted from the job 3MF")
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


@app.delete("/queue/{job_id}", tags=["Queue"], summary="Soft-delete a job")
async def delete_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    db.delete_job(job_id)
    return {"deleted": True, "job_id": job_id}


@app.post("/queue/clear", tags=["Queue"], summary="Soft-delete all queued and errored jobs")
async def clear_queue() -> dict[str, Any]:
    """Soft-delete all queued (and errored) jobs that haven't been sent."""
    count = db.clear_queue()
    return {"cleared": count}


@app.post("/queue/cleanup", tags=["Queue"], summary="Purge deleted rows and orphaned files from disk")
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

@app.get("/", response_class=HTMLResponse, tags=["Utility"], summary="Web UI", include_in_schema=False)
async def ui() -> HTMLResponse:
    """Serve the queue management UI."""
    ui_path = Path(__file__).parent / "ui.html"
    if not ui_path.exists():
        raise HTTPException(status_code=404, detail="UI not built. See ui.html.")
    return HTMLResponse(ui_path.read_text())



@app.get("/health", tags=["Utility"], summary="Health check")
async def health() -> dict[str, str]:
    return {"status": "ok"}