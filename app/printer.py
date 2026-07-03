"""
Thin async wrapper around flashforge-python-api's FlashForgeClient (FiveMClient).

Attribute names verified against the installed library source:
  client.job_control   - JobControl  (upload, start job)
  client.info          - Info        (machine state, status)
  client.get_printer_status() - returns FFMachineInfo | None
"""

import logging
from pathlib import Path
from typing import Any

from flashforge import (
    AD5XLocalJobParams,
    AD5XMaterialMapping,
    AD5XSingleColorJobParams,
    AD5XUploadParams,
    FiveMClient,
)

from .config import settings
from .threemf import extract_filament_slots

log = logging.getLogger("printer")


def _slots_to_mappings(slots) -> list[AD5XMaterialMapping]:
    return [
        AD5XMaterialMapping(
            tool_id=s.tool_id,
            slot_id=s.slot_id,
            material_name=s.material_name,
            tool_material_color=s.colour,
            slot_material_color=s.colour,
        )
        for s in slots
    ]


async def get_printer_status() -> dict[str, Any]:
    """Return a dict with current machine state and basic info."""
    if not settings.PRINTER_IP:
        return {"state": "unconfigured", "detail": "PRINTER_IP not set"}

    try:
        async with FiveMClient(
            settings.PRINTER_IP,
            settings.PRINTER_SERIAL,
            settings.PRINTER_CHECK_CODE,
        ) as client:
            if not await client.initialize():
                return {"state": "offline", "detail": "Could not connect to printer"}

            machine_info = await client.get_printer_status()
            state = await client.info.get_machine_state()

            info_dict: dict[str, Any] = {}
            if machine_info:
                info_dict = {
                    "name": machine_info.name,
                    "model": machine_info.model,
                    "firmware": machine_info.firmware_version,
                    "nozzle_size": machine_info.nozzle_size,
                    "free_disk_space": machine_info.free_disk_space,
                    "door_open": machine_info.door_open,
                    "error_code": machine_info.error_code,
                }

            return {
                "state": state.value if state else "unknown",
                "printer_name": client.printer_name,
                "firmware": client.firmware_ver,
                "is_printing": await client.info.is_printing(),
                "info": info_dict,
            }
    except Exception as exc:
        log.exception("Status check failed")
        return {"state": "error", "detail": str(exc)}


async def send_job(filepath: str) -> tuple[bool, str]:
    """
    Upload a file to the printer and start it.

    For .gcode.3mf files we extract the IFS material mappings and use the
    AD5X multi-colour job API.  For plain .gcode files (or 3MFs where we
    can't find filament metadata) we fall back to the single-colour upload.

    Returns (success: bool, message: str).
    """
    if not settings.PRINTER_IP:
        return False, "PRINTER_IP is not configured"

    path = Path(filepath)
    if not path.exists():
        return False, f"File not found: {filepath}"

    # --- Extract IFS material mapping from 3MF if available ---
    slots = []
    is_3mf = ".3mf" in path.name.lower()
    if is_3mf:
        slots = extract_filament_slots(path)
        log.info("Extracted %d filament slot(s) from %s", len(slots), path.name)

    try:
        async with FiveMClient(
            settings.PRINTER_IP,
            settings.PRINTER_SERIAL,
            settings.PRINTER_CHECK_CODE,
        ) as client:
            if not await client.initialize():
                return False, "Could not connect to printer"

            await client.init_control()

            # --- Upload + (optionally) start ---
            if is_3mf and slots:
                mappings = _slots_to_mappings(slots)
                upload_params = AD5XUploadParams(
                    file_path=str(path),
                    start_print=settings.START_PRINT_IMMEDIATELY,
                    leveling_before_print=settings.LEVELING_BEFORE_PRINT,
                    flow_calibration=False,
                    first_layer_inspection=False,
                    time_lapse_video=False,
                    material_mappings=mappings,
                )
                log.info(
                    "Uploading %s (AD5X multi-colour, %d tools)", path.name, len(mappings)
                )
                ok = await client.job_control.upload_file_ad5x(upload_params)
            else:
                log.info("Uploading %s (single-colour / plain gcode)", path.name)
                ok = await client.job_control.upload_file(
                    str(path),
                    start_print=settings.START_PRINT_IMMEDIATELY,
                    level_before_print=settings.LEVELING_BEFORE_PRINT,
                )

            if not ok:
                return False, "Printer rejected the upload"

            # If START_PRINT_IMMEDIATELY=False the printer won't auto-start,
            # so we issue an explicit start command.
            if not settings.START_PRINT_IMMEDIATELY:
                if is_3mf and slots:
                    job_params = AD5XLocalJobParams(
                        file_name=path.name,
                        leveling_before_print=settings.LEVELING_BEFORE_PRINT,
                        material_mappings=_slots_to_mappings(slots),
                    )
                    await client.job_control.start_ad5x_multi_color_job(job_params)
                else:
                    job_params_single = AD5XSingleColorJobParams(
                        file_name=path.name,
                        leveling_before_print=settings.LEVELING_BEFORE_PRINT,
                    )
                    await client.job_control.start_ad5x_single_color_job(job_params_single)

            return True, f"Job sent: {path.name}"

    except Exception as exc:
        log.exception("send_job failed for %s", filepath)
        return False, str(exc)
