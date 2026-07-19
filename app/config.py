"""
Configuration loaded from environment variables.
"""
import os


class Settings:
    # --- Storage ---
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(os.getcwd(), "data"))
    UPLOAD_DIR: str = os.path.join(DATA_DIR, "uploads")
    DB_PATH: str = os.path.join(DATA_DIR, "queue.db")

    # --- Printer connection (FlashForge AD5X LAN mode) ---
    PRINTER_IP: str = os.getenv("PRINTER_IP", "")
    PRINTER_SERIAL: str = os.getenv("PRINTER_SERIAL", "")
    PRINTER_CHECK_CODE: str = os.getenv("PRINTER_CHECK_CODE", "")

    # --- Camera ---
    # If set, the UI and /printer/stream will use this URL instead of
    # fetching the stream URL from the printer's /detail endpoint.
    # Useful for pointing at a Frigate/go2rtc restream that handles
    # the single-connection limitation externally.
    # Example: http://frigate:8554/ad5x/camera or http://go2rtc:1984/stream.mjpeg
    CAMERA_STREAM_URL: str | None = os.getenv("CAMERA_STREAM_URL") or None


settings = Settings()