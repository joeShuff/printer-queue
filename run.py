"""
Entry point for local development / PyCharm debugging.
Run this file directly instead of app/main.py.

PyCharm run configuration:
  Script: run.py
  Working directory: <project root>  (where this file lives)
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=7125,
        reload=True,   # auto-reload on file changes while debugging
    )