"""
FlashForge AD5X Printer Queue - Transparent Proxy Mode
=======================================================

This server sits between OrcaSlicer and your real AD5X printer.

In OrcaSlicer, configure the printer connection as FlashForge (not OctoPrint):
  IP: <this server's IP>
  Port: 8898
  Serial Number: same as your real printer (PRINTER_SERIAL env var)
  Check Code: same as your real printer (PRINTER_CHECK_CODE env var)

OrcaSlicer will then query /detail to get live IFS slot info (material types,
colours, which slots have filament) and show you the picker UI exactly as if
talking to the real printer. When you click Print, the upload is intercepted
and queued here instead of being sent immediately.

Endpoint behaviour
------------------
PROXIED   → forwarded straight to the real printer, response passed back
INTERCEPTED → handled locally, file stored and queued

  POST /product      PROXIED   printer capabilities (tells OrcaSlicer it's an AD5X)
  POST /detail       PROXIED   live printer status + IFS slot info → populates picker
  POST /control      PROXIED   pause/resume/cancel/temp/LED etc.
  POST /gcodeList    PROXIED   file list on the real printer
  POST /gcodeThumb   PROXIED   thumbnail from real printer
  POST /uploadGcode  INTERCEPTED  save file + metadata to queue
  POST /printGcode   INTERCEPTED  save materialMappings to the queued job

Queue management (called from Home Assistant)
---------------------------------------------
  GET  /queue           list all jobs
  GET  /queue/status    live printer state + next job
  POST /queue/next      replay next queued job to real printer → starts print
  DELETE /queue/{id}    remove a job
  GET  /health          no-auth health check
"""

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

app = FastAPI(title="FlashForge Printer Queue Proxy", version="2.0.0")

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_auth(serial: str, check_code: str) -> bool:
    """Validate serial + check code against our configured values."""
    return (serial == settings.PRINTER_SERIAL and
            check_code == settings.PRINTER_CHECK_CODE)


# ---------------------------------------------------------------------------
# Printer proxy helper
# ---------------------------------------------------------------------------

PRINTER_BASE = f"http://{settings.PRINTER_IP}:8898"

async def _proxy_to_printer(path: str, body: dict) -> dict:
    """
    POST body as JSON to the real printer and return its JSON response.
    Raises HTTPException(502) if the printer is unreachable.
    """
    url = f"{PRINTER_BASE}{path}"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                return await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        log.error("Proxy to %s failed: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Printer unreachable: {exc}")


# ---------------------------------------------------------------------------
# PROXIED endpoints  (auth in JSON body: {"serialNumber": ..., "checkCode": ...})
# ---------------------------------------------------------------------------

@app.post("/product")
async def product(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    result = await _proxy_to_printer("/product", body)
    return JSONResponse(result)


@app.post("/detail")
async def detail(request: Request) -> JSONResponse:
    """
    Proxied live printer status. This is what populates OrcaSlicer's IFS
    slot picker — the real printer returns matlStationInfo.slotInfos with
    each slot's material type, colour, and whether it has filament loaded.
    """
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    result = await _proxy_to_printer("/detail", body)
    return JSONResponse(result)


@app.post("/control")
async def control(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    result = await _proxy_to_printer("/control", body)
    return JSONResponse(result)


@app.post("/gcodeList")
async def gcode_list(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    result = await _proxy_to_printer("/gcodeList", body)
    return JSONResponse(result)


@app.post("/gcodeThumb")
async def gcode_thumb(request: Request) -> JSONResponse:
    body = await request.json()
    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})
    result = await _proxy_to_printer("/gcodeThumb", body)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# INTERCEPTED: /uploadGcode
# ---------------------------------------------------------------------------

@app.post("/uploadGcode")
async def upload_gcode(request: Request) -> JSONResponse:
    """
    Intercept OrcaSlicer's file upload.

    Auth comes from HTTP headers (serialNumber / checkCode), not the body.
    The file is in the multipart field 'gcodeFile'.
    Print metadata is in other headers (printingTime, totalLayers, etc).

    We store the file and queue the job. We do NOT forward to the printer yet.
    The job is sent when /queue/next is called from Home Assistant.
    """
    serial = request.headers.get("serialNumber", "")
    check_code = request.headers.get("checkCode", "")
    if not _check_auth(serial, check_code):
        return JSONResponse({"code": 1, "message": "Authentication failed"})

    # Log every header OrcaSlicer sends so we can see exactly what comes in
    log.info("uploadGcode headers: %s", dict(request.headers))

    # -- Parse multipart --
    try:
        form = await request.form()
    except Exception as exc:
        log.error("Failed to parse multipart: %s", exc)
        return JSONResponse({"code": 1, "message": "Bad multipart body"})

    file_field = form.get("gcodeFile")
    if file_field is None:
        return JSONResponse({"code": 1, "message": "No gcodeFile field in upload"})

    filename = file_field.filename or "unknown.gcode"
    content = await file_field.read()

    # -- Save file --
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    dest = Path(settings.UPLOAD_DIR) / filename
    counter = 1
    while dest.exists():
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        suffix = filename[len(stem):]
        dest = Path(settings.UPLOAD_DIR) / f"{stem}_{counter}{suffix}"
        counter += 1

    dest.write_bytes(content)
    log.info("Saved upload: %s (%d bytes)", dest.name, len(content))

    # -- Capture all metadata headers OrcaSlicer sends --
    upload_headers = {
        "printNow":             request.headers.get("printNow", "false"),
        "levelingBeforePrint":  request.headers.get("levelingBeforePrint", "false"),
        "printingTime":         request.headers.get("printingTime", "0"),
        "totalLayers":          request.headers.get("totalLayers", "0"),
        "gcodeToolCnt":         request.headers.get("gcodeToolCnt", "1"),
        "useMatlStation":       request.headers.get("useMatlStation", "false"),
    }

    # OrcaSlicer sends materialMappings as a base64-encoded JSON string in
    # the header when using the single-call flow (printNow=true).
    # Decode it now so it's ready to replay when the job is dispatched.
    material_mappings = []
    use_matl_station = upload_headers["useMatlStation"].lower() == "true"
    raw_mappings = request.headers.get("materialMappings", "")
    if raw_mappings:
        try:
            decoded = base64.b64decode(raw_mappings).decode("utf-8")
            material_mappings = json.loads(decoded)
            log.info("Decoded materialMappings from header: %s", material_mappings)
        except Exception as exc:
            log.warning("Failed to decode materialMappings header: %s (raw=%r)", exc, raw_mappings)

    job = db.add_job(
        filename=dest.name,
        filepath=str(dest),
        upload_headers=upload_headers,
        material_mappings=material_mappings,
        use_matl_station=use_matl_station,
        leveling_before_print=upload_headers["levelingBeforePrint"].lower() == "true",
    )
    log.info("Queued job id=%d filename=%s", job["id"], dest.name)

    return JSONResponse({"code": 0, "message": "Success"})


# ---------------------------------------------------------------------------
# INTERCEPTED: /printGcode
# ---------------------------------------------------------------------------

@app.post("/printGcode")
async def print_gcode(request: Request) -> JSONResponse:
    """
    OrcaSlicer calls this after /uploadGcode to specify materialMappings
    (the IFS slot assignments the user picked in the UI).

    We find the most recent queued job and attach the mappings to it.
    We do NOT forward to the printer yet.
    """
    body = await request.json()
    log.info("printGcode full body: %s", body)  # log everything for debugging

    if not _check_auth(body.get("serialNumber", ""), body.get("checkCode", "")):
        return JSONResponse({"code": 1, "message": "Authentication failed"})

    filename = body.get("fileName", "")
    material_mappings = body.get("materialMappings", [])
    use_matl_station = body.get("useMatlStation", False)
    leveling = body.get("levelingBeforePrint", False)

    log.info(
        "printGcode received: file=%s useMatlStation=%s mappings=%s",
        filename, use_matl_station, material_mappings
    )

    # Match on filename. OrcaSlicer sends the original name (before our
    # dedup suffix), so also try matching on the stem in case we renamed it.
    jobs = db.list_jobs(status="queued")
    matched = next((j for j in reversed(jobs) if j["filename"] == filename), None)

    if not matched:
        # Fallback: OrcaSlicer sent the original name but we stored it with a
        # dedup suffix (e.g. "model.gcode.3mf" vs "model_1.gcode.3mf").
        # Match on the most recently queued job whose filename starts with the stem.
        stem = filename.split(".")[0]  # everything before first dot
        matched = next(
            (j for j in reversed(jobs) if j["filename"].startswith(stem)), None
        )
        if matched:
            log.info(
                "printGcode: matched by stem '%s' → job id=%d filename=%s",
                stem, matched["id"], matched["filename"]
            )

    if matched:
        db.set_print_gcode_data(
            job_id=matched["id"],
            material_mappings=material_mappings,
            use_matl_station=use_matl_station,
            leveling_before_print=leveling,
        )
        log.info("Attached materialMappings to job id=%d", matched["id"])
    else:
        log.warning("printGcode: no queued job found for filename=%s (all queued: %s)",
                    filename, [j["filename"] for j in jobs])

    return JSONResponse({"code": 0, "message": "Success"})


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

@app.get("/queue")
async def get_queue() -> dict[str, Any]:
    jobs = db.list_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/queue/status")
async def queue_status() -> dict[str, Any]:
    """Live printer state + next queued job. Use as a Home Assistant sensor."""
    next_job = db.next_queued_job()
    queued = db.list_jobs(status="queued")

    # Best-effort live printer status (don't fail the whole endpoint if down)
    printer_state: dict[str, Any] = {"state": "unknown"}
    if settings.PRINTER_IP:
        try:
            raw = await _proxy_to_printer("/detail", {
                "serialNumber": settings.PRINTER_SERIAL,
                "checkCode": settings.PRINTER_CHECK_CODE,
            })
            detail_data = raw.get("detail", {})
            printer_state = {
                "state": detail_data.get("status", "unknown"),
                "file": detail_data.get("printFileName", ""),
                "progress": detail_data.get("printProgress", 0),
            }
        except HTTPException:
            printer_state = {"state": "offline"}

    return {
        "printer": printer_state,
        "next_job": next_job,
        "queued_count": len(queued),
    }


@app.post("/queue/next")
async def send_next_job() -> dict[str, Any]:
    """
    Replay the next queued job to the real printer.
    Call this from your Home Assistant button.

    Flow:
      1. POST /uploadGcode to real printer (multipart, same headers as original)
      2. POST /printGcode to real printer (with saved materialMappings)
    """
    job = db.next_queued_job()
    if not job:
        raise HTTPException(status_code=409, detail="Queue is empty")

    db.set_status(job["id"], "sending")

    filepath = Path(job["filepath"])
    if not filepath.exists():
        db.set_status(job["id"], "error", "File missing from disk")
        raise HTTPException(status_code=500, detail="File missing from disk")

    upload_headers = job.get("upload_headers") or {}
    material_mappings = job.get("material_mappings") or []
    use_matl_station = job.get("use_matl_station") or False
    leveling = job.get("leveling_before_print")
    if leveling is None:
        leveling = settings.LEVELING_BEFORE_PRINT

    try:
        # -- Step 1: upload the file to the real printer --
        url_upload = f"{PRINTER_BASE}/uploadGcode"
        timeout = aiohttp.ClientTimeout(total=120)  # uploads can be large

        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {
                "serialNumber":        settings.PRINTER_SERIAL,
                "checkCode":           settings.PRINTER_CHECK_CODE,
                "printNow":            "false",  # we start via printGcode below
                "levelingBeforePrint": str(leveling).lower(),
                "printingTime":        str(upload_headers.get("printingTime", 0)),
                "totalLayers":         str(upload_headers.get("totalLayers", 0)),
                "gcodeToolCnt":        str(upload_headers.get("gcodeToolCnt", 1)),
            }

            with open(filepath, "rb") as f:
                form = aiohttp.FormData()
                form.add_field(
                    "gcodeFile",
                    f,
                    filename=job["filename"],
                    content_type="application/octet-stream",
                )
                async with session.post(url_upload, data=form, headers=headers) as resp:
                    upload_result = await resp.json(content_type=None)

        if upload_result.get("code") != 0:
            msg = f"Printer rejected upload: {upload_result}"
            db.set_status(job["id"], "error", msg)
            raise HTTPException(status_code=502, detail=msg)

        log.info("Job %d: file uploaded to printer OK", job["id"])

        # -- Step 2: send printGcode to start with material mappings --
        print_body = {
            "serialNumber":       settings.PRINTER_SERIAL,
            "checkCode":          settings.PRINTER_CHECK_CODE,
            "fileName":           job["filename"],
            "levelingBeforePrint": leveling,
            "useMatlStation":     use_matl_station,
            "materialMappings":   material_mappings,
        }
        print_result = await _proxy_to_printer("/printGcode", print_body)

        if print_result.get("code") != 0:
            msg = f"Printer rejected printGcode: {print_result}"
            db.set_status(job["id"], "error", msg)
            raise HTTPException(status_code=502, detail=msg)

        log.info("Job %d: print started OK", job["id"])
        db.set_status(job["id"], "sent")
        return {"success": True, "job": job, "message": f"Print started: {job['filename']}"}

    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc)
        log.exception("send_next_job failed for job %d", job["id"])
        db.set_status(job["id"], "error", msg)
        raise HTTPException(status_code=502, detail=msg)


@app.delete("/queue/{job_id}")
async def delete_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    db.delete_job(job_id)
    try:
        Path(job["filepath"]).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not delete file %s: %s", job["filepath"], exc)
    return {"deleted": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Health check (no auth)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}