"""
Configuration loaded from environment variables.
"""
import os



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




settings = Settings()