FROM python:3.12-slim

WORKDIR /srv

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# /data is the persistent volume mount point (uploads + sqlite db)
VOLUME ["/data"]

EXPOSE 7125

# Run with a single worker — the printer client is async so this is fine.
# Using a module path so imports work correctly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7125"]
