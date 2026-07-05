# AD5X Printer Queue

A self-hosted print queue server for the FlashForge AD5X (with IFS).
Upload sliced jobs from OrcaSlicer, manage them via a web UI, then trigger prints manually or automatically from Home Assistant.

---

## How it works

```
OrcaSlicer  ──/uploadGcode──►  Queue server  ──FlashForge HTTP API──►  AD5X
                                    ▲
                  POST /queue/next  │
                               Home Assistant / Web UI
```

The server acts as a **transparent proxy** — OrcaSlicer thinks it's talking directly to the printer. Status and IFS slot queries are forwarded live to the real printer so the material picker in OrcaSlicer shows your actual loaded spools. Uploads are intercepted and queued instead of being sent immediately.

---

## Quick start

### 1. Configure and start the container

```bash
git clone https://github.com/joeShuff/printer-queue.git
cd printer-queue

# Edit docker-compose.yml with your printer IP, serial, and check code
nano docker-compose.yml

docker compose up -d
docker compose logs -f
```

The service listens on **port 8898** — the same port OrcaSlicer expects for FlashForge printers.

### 2. Get your printer credentials

On the AD5X touchscreen:

```
Settings → Network → LAN Mode → Enable
```

Note the **Serial Number** and **Check Code**. Assign the printer a static IP in your router.

### 3. Configure OrcaSlicer

In OrcaSlicer, open your **Printer settings → General** and set the connection type to **FlashForge** (not OctoPrint):

| Field | Value |
|---|---|
| IP Address | your queue server's IP |
| Port | 8898 |
| Serial Number | same as `PRINTER_SERIAL` env var |
| Check Code | same as `PRINTER_CHECK_CODE` env var |

Click **Test** — OrcaSlicer will connect to the queue server, which proxies the request to the real printer. The IFS slot picker will show your actual loaded filaments.

When you click **Print**, the file is queued rather than sent immediately (unless the printer is idle and `printNow` is set, in which case it auto-dispatches).

### 4. Web UI

Visit `http://<server-ip>:8898` in your browser for the queue management UI.

Features:
- Live printer status with progress bar
- Queue stats (queued / sending / sent / errors / removed)
- Model thumbnail previews extracted from the `.gcode.3mf`
- Drag-to-reorder queued jobs
- Per-job buttons: **Send Now**, **Requeue**, **Remove**
- Toggle to show soft-deleted jobs
- **Send Next**, **Clear Queue**, and **Purge** global actions

---

## Docker Compose

```yaml
services:
  printer-queue:
    image: ghcr.io/joeshuff/printer-queue:latest
    ports:
      - "8898:8898"
    volumes:
      - printer-queue-data:/data
    environment:
      PRINTER_IP: "192.168.1.XXX"
      PRINTER_SERIAL: "YOUR_SERIAL"
      PRINTER_CHECK_CODE: "YOUR_CODE"

volumes:
  printer-queue-data:
```

---

## API reference

### Proxied to printer (live)

| Method | Path | Description |
|---|---|---|
| `POST` | `/product` | Printer capabilities |
| `POST` | `/detail` | Live status + IFS slot info |
| `POST` | `/control` | Pause / resume / cancel |
| `POST` | `/gcodeList` | File list on printer |
| `POST` | `/gcodeThumb` | Thumbnail from printer |

### Queue management

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `GET` | `/queue` | List jobs (`?include_deleted=true` to show removed) |
| `GET` | `/queue/status` | Live printer state + next queued job |
| `POST` | `/queue/next` | Send next queued job to printer |
| `POST` | `/queue/reorder` | Reorder queue — body: `{"order": [id1, id2, ...]}` |
| `POST` | `/queue/clear` | Soft-delete all queued/error jobs |
| `POST` | `/queue/cleanup` | Delete orphaned files + purge deleted DB rows |
| `POST` | `/queue/{id}/requeue` | Move any job back to queued |
| `POST` | `/queue/{id}/send` | Immediately dispatch a specific job |
| `GET` | `/queue/{id}/thumbnail` | PNG thumbnail extracted from the `.gcode.3mf` |
| `DELETE` | `/queue/{id}` | Soft-delete a job (file kept until cleanup) |
| `GET` | `/health` | Health check (no auth) |

### Job statuses

| Status | Meaning |
|---|---|
| `queued` | Waiting to be sent |
| `sending` | Currently uploading to printer |
| `sent` | Successfully sent, print started |
| `error` | Send failed — see `error` field |
| `deleted` | Soft-deleted, hidden by default |

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `PRINTER_IP` | ✅ | AD5X local IP address |
| `PRINTER_SERIAL` | ✅ | Serial number from LAN mode screen |
| `PRINTER_CHECK_CODE` | ✅ | Check code from LAN mode screen |
| `DATA_DIR` | — | Storage directory (default: `./data` locally, `/data` in Docker) |

---

## File structure

```
printer-queue/
├── .github/
│   └── workflows/
│       └── build.yml        # Build & push to GHCR on push to main
├── app/
│   ├── __init__.py
│   ├── config.py            # Environment variable settings
│   ├── db.py                # SQLite queue store
│   ├── main.py              # FastAPI app — proxy + queue endpoints
│   ├── threemf.py           # .gcode.3mf parser (thumbnail, print time, layers)
│   └── ui.html              # Self-contained React web UI (served at GET /)
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run.py                   # PyCharm / local dev entry point
└── README.md
```

### Storage (per job)

Each upload creates two files under `DATA_DIR/uploads/`:

| File | Purpose |
|---|---|
| `{id}.body` | Raw multipart body — replayed byte-for-byte to the printer |
| `{id}.gcode.3mf` | Extracted 3mf file — used for thumbnails and metadata |

The SQLite database (`DATA_DIR/queue.db`) stores all job metadata including material mappings, queue position, print time, layer count, and file size.

---

## Building and deploying

The GitHub Actions workflow (`.github/workflows/build.yml`) builds a multi-arch image (`linux/amd64` + `linux/arm64`) and pushes it to GHCR on every push to `main`.

Tagged releases (`git tag v1.0.0 && git push --tags`) additionally publish versioned tags.

To update a running server:

```bash
docker compose pull
docker compose up -d
```

Data in the `printer-queue-data` volume survives container updates.

---

## Local development (PyCharm)

1. Set your run configuration to use `run.py` as the script
2. Set working directory to the project root
3. Add environment variables: `PRINTER_IP`, `PRINTER_SERIAL`, `PRINTER_CHECK_CODE`
4. Run — the server starts on port 8898 with auto-reload
