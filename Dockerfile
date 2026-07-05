FROM python:3.12-slim

WORKDIR /srv

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# /data is the persistent volume mount point (uploads + sqlite db)
VOLUME ["/data"]

EXPOSE 8898

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8898/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8898"]