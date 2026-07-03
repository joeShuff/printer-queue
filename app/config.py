"""
Configuration loaded from environment variables.
"""
import os


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- Storage ---
    # Defaults to ./data relative to cwd so it works locally without Docker.
    # In Docker the DATA_DIR env var overrides this to /data.
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data"))
    UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads")
    DB_PATH: str = os.path.join(DATA_DIR, "queue.db")

    # --- Printer connection (FlashForge AD5X LAN mode) ---
    # Found on the printer: Settings → Network → LAN Mode
    PRINTER_IP: str = os.getenv("PRINTER_IP", "")
    PRINTER_SERIAL: str = os.getenv("PRINTER_SERIAL", "")
    PRINTER_CHECK_CODE: str = os.getenv("PRINTER_CHECK_CODE", "")

    # --- Print job defaults ---
    # Whether to run auto bed-levelling before each print.
    # OrcaSlicer also sends this per-job; this is the fallback default.
    LEVELING_BEFORE_PRINT: bool = _bool_env("LEVELING_BEFORE_PRINT", True)


settings = Settings()