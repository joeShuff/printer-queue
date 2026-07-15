<!--
  ⚠️ AI-Assisted Project
  This project was developed with the assistance of Claude (Anthropic).
  All code has been reviewed, tested, and modified by a professional developer.
  Use at your own risk — always review code before deploying to your network.
-->

# 🖨️ AD5X Print Queue

A self-hosted print queue server for the **FlashForge AD5X** with IFS (Integrated Filament System).

Upload sliced jobs directly from OrcaSlicer, manage them from a web UI, and print them when you're ready — without leaving OrcaSlicer open or babysitting the printer.

> **AI Disclosure:** This project was developed with AI assistance (Claude by Anthropic). All code has been reviewed, tested, and modified by a professional developer before deployment.

📖 **[API Documentation](https://joeshuff.github.io/printer-queue)** — full endpoint reference via Swagger UI

---

## Screenshots

### Web UI — Queue overview
<!-- screenshot: screenshots/queue-overview.png -->
> _Screenshot: main queue view showing queued jobs, thumbnails, and stats strip_

### Web UI — Printer panel (printing)
<!-- screenshot: screenshots/printer-panel-printing.png -->
> _Screenshot: printer panel with active print, thumbnail, progress bar, and controls_

### Web UI — Printer panel (expanded)
<!-- screenshot: screenshots/printer-panel-expanded.png -->
> _Screenshot: expanded printer panel showing all four IFS slots and extra stats_

### Web UI — Connection modal
<!-- screenshot: screenshots/connection-modal.png -->
> _Screenshot: OrcaSlicer connection details modal_

### OrcaSlicer — Device tab
<!-- screenshot: screenshots/orcaslicer-device-tab.png -->
> _Screenshot: the Device tab in OrcaSlicer showing live printer status via the proxy_

---

## How it works

```
OrcaSlicer ──/uploadGcode──► Queue server ──FlashForge HTTP API──► AD5X
                                   │
                                Web UI
```

The server acts as a **transparent proxy** between OrcaSlicer and your printer:

- **Status queries** are forwarded live — OrcaSlicer's IFS material picker shows your actual loaded spools
- **Uploads** are intercepted, saved to disk, and queued — nothing prints yet
- **Dispatch** replays the original upload to the printer, preserving all material mappings you selected in OrcaSlicer
- **Auto-dispatch** fires immediately if the printer is idle when you hit Print

---

## Features

- 🖨️ **Transparent FlashForge proxy** — OrcaSlicer connects as if talking directly to the printer
- 🎨 **Live IFS slot picker** — real filament colours and types from the printer, not cached data
- 📋 **Drag-to-reorder queue** — reorganise jobs before they print
- 🖼️ **Model thumbnails** — extracted from `.gcode.3mf`, correct plate selected automatically
- ✏️ **Change filament** — reassign IFS slots on queued jobs before dispatch
- ⏸️ **Printer controls** — pause, resume, cancel, clear platform, light toggle
- 📱 **Mobile-friendly UI** — responsive layout
- 🔄 **Per-job actions** — Send Now, Requeue, Remove
- 🐳 **Docker** — single container, multi-arch (`amd64` + `arm64`)

---

## Requirements

- Docker + Docker Compose
- FlashForge AD5X with LAN mode enabled
- OrcaSlicer 2.4.1+
- A static IP assigned to the printer on your LAN

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/joeShuff/printer-queue.git
cd printer-queue
```

### 2. Find your printer credentials

On the AD5X touchscreen:

```
Settings → Network → LAN Mode → Enable
```

Note the **Serial Number** and **Check Code**. Set a static IP for the printer in your router's DHCP settings.

### 3. Configure docker-compose.yml

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
      PRINTER_SERIAL: "SNXXXXXXXX"
      PRINTER_CHECK_CODE: "xxxxxxxx"
      DATA_DIR: "/data"

volumes:
  printer-queue-data:
```

### 4. Start the container

```bash
docker compose up -d
docker compose logs -f
```

### 5. Verify

Open `http://<server-ip>:8898` — you should see the queue UI.

---

## Connecting OrcaSlicer

> Point OrcaSlicer at this proxy server, **not** directly at the printer.

1. In OrcaSlicer open **Printer settings → General**
2. Set connection type to **FlashForge**
3. Click the **Connect** button in the queue UI header — it shows the exact values to enter:

| Field | Value |
|---|---|
| IP Address | IP of the machine running the queue server |
| Port | `8898` |
| Serial Number | same as `PRINTER_SERIAL` in docker-compose |
| Check Code | same as `PRINTER_CHECK_CODE` in docker-compose |

4. Click **Test** in OrcaSlicer
5. Open the **Device** tab in OrcaSlicer to see live printer status

---

## Uploading a job

<!-- screenshot: screenshots/orcaslicer-send.png -->
> _Screenshot: OrcaSlicer IFS material picker_

1. Slice your model as normal
2. Click **Print** — the IFS material picker shows your live loaded spools
3. Assign each slicer tool to the correct physical IFS slot
4. Click **Send** — the file is added to the queue

---

## Changing filament assignments

<!-- screenshot: screenshots/filament-picker.png -->
> _Screenshot: filament picker modal showing live IFS slot colours_

Click **Filament** on any queued multi-tool job to open the picker. It shows your currently loaded spools (fetched live from the printer) and lets you reassign which physical slot each slicer tool uses. Save before dispatching.

---

## API

📖 **[Interactive API docs](https://joeshuff.github.io/printer-queue)** — Swagger UI with full endpoint reference, request/response schemas, and control command examples.

Also available on your running server at:
- `/docs` — Swagger UI (interactive)
- `/redoc` — ReDoc
- `/openapi.json` — raw OpenAPI spec

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PRINTER_IP` | ✅ | — | AD5X local IP address |
| `PRINTER_SERIAL` | ✅ | — | Serial number from LAN mode screen |
| `PRINTER_CHECK_CODE` | ✅ | — | Check code from LAN mode screen |
| `DATA_DIR` | — | `./data` / `/data` in Docker | Storage directory |

---

## File structure

```
printer-queue/
├── app/
│   ├── config.py       # Environment variable settings
│   ├── db.py           # SQLite queue store
│   ├── main.py         # FastAPI application
│   ├── threemf.py      # .gcode.3mf metadata + thumbnail extractor
│   └── ui.html         # Self-contained React UI (served at GET /)
├── docs/
│   ├── index.html      # GitHub Pages — Swagger UI
│   └── openapi.json    # Generated OpenAPI spec
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── run.py              # Local dev entry point
```

---

## Local development

```bash
git clone https://github.com/yourname/printer-queue.git
cd printer-queue
pip install -r requirements.txt

export PRINTER_IP=192.168.1.XXX
export PRINTER_SERIAL=SNXXXXXXXX
export PRINTER_CHECK_CODE=xxxxxxxx

python run.py
```

In PyCharm: script `run.py`, working directory = project root, add the three env vars.

---

## Contributing

Issues and PRs welcome. Keep it small.

---

## Licence

Licensed under the **GNU Affero General Public Licence v3.0 (AGPL-3.0)**.

- ✅ Free to use, modify, and self-host
- ⚠️ Modified versions run as a network service must publish their source under the same licence

See [choosealicense.com/licenses/agpl-3.0](https://choosealicense.com/licenses/agpl-3.0/) for a plain-English summary.