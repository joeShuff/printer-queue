"""
Configuration for the printer queue service.
Everything is supplied via environment variables so it can be configured
entirely through the Docker Compose file / `docker run -e ...`.
"""
import os


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- Storage ---
    # Defaults to ./data (relative to cwd) so it works when running locally.
    # In Docker this is overridden to /data via the DATA_DIR env var.
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data"))
    UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads")
    DB_PATH: str = os.path.join(DATA_DIR, "queue.db")

    # --- API auth (optional) ---
    # If set, requests must include header `X-Api-Key: <API_KEY>`.
    # OrcaSlicer's Print Host settings always send an X-Api-Key header
    # (it's required by the OctoPrint API it's emulating), so this lines up
    # with what OrcaSlicer will send without any special config on your end.
    API_KEY: str | None = os.getenv("API_KEY") or None

    # --- Printer connection (FlashForge AD5X, LAN mode) ---
    PRINTER_IP: str = os.getenv("PRINTER_IP", "")
    PRINTER_SERIAL: str = os.getenv("PRINTER_SERIAL", "")
    PRINTER_CHECK_CODE: str = os.getenv("PRINTER_CHECK_CODE", "")

    # --- Print job defaults ---
    LEVELING_BEFORE_PRINT: bool = _bool_env("LEVELING_BEFORE_PRINT", True)
    START_PRINT_IMMEDIATELY: bool = _bool_env("START_PRINT_IMMEDIATELY", True)

    # If a .gcode.3mf doesn't have IFS/material info at all (single-color
    # file), we just do a plain upload+print with no material mappings.
    # If it does have multiple filaments, see threemf.py for how the
    # tool->slot mapping is derived, and README.md for the important caveat
    # about what OrcaSlicer actually embeds here.


settings = Settings()