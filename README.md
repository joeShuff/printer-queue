# AD5X Printer Queue

A self-hosted print queue server for the FlashForge AD5X (with IFS).  
Upload sliced jobs from OrcaSlicer, then trigger the next print from a Home Assistant button.

---

## How it works

```
OrcaSlicer  ──POST /api/files/local──►  Queue server  ──FlashForge LAN API──►  AD5X
                                              ▲
                              POST /queue/next│
                                         Home Assistant button
```

1. You slice in OrcaSlicer and click **Send** (Print Host upload).
2. The server stores the file and adds it to the queue.
3. When the previous print is done and you've cleared the bed, press the Home Assistant button.
4. The server picks the oldest queued file, uploads it to the printer over LAN, and starts the print.

---

## Quick start

### 1. Configure and start the container

```bash
git clone <this-repo>
cd printer-queue

# Edit docker-compose.yml — fill in PRINTER_IP, PRINTER_SERIAL, PRINTER_CHECK_CODE, API_KEY
nano docker-compose.yml

docker compose up -d
docker compose logs -f   # watch startup
```

The service listens on **port 7125** (you can change this in `docker-compose.yml`).

### 2. Get your printer credentials

On the AD5X touchscreen:

```
Settings → Network → LAN Mode → Enable
```

Note the **Serial Number** and **Check Code** shown on screen.  
Assign the printer a static IP in your router's DHCP settings.

### 3. Configure OrcaSlicer

In OrcaSlicer, open your **Printer settings → General**:

| Field | Value |
|---|---|
| Print Host | `OctoPrint` |
| Hostname / IP | `http://<your-server-ip>:7125` |
| API Key | same value you set as `API_KEY` in docker-compose.yml |

Click **Test** — you should see a success message.

After slicing, use **Print → Send** (or the cloud/upload icon) to push the file to the queue.

> **OrcaSlicer 2.4+ and .gcode.3mf files**  
> OrcaSlicer 2.4 added an option to send sliced jobs as `.gcode.3mf` packages instead of plain `.gcode`.  
> Enable this in your AD5X printer profile for the best IFS material-mapping support:  
> `Printer settings → General → Send as .gcode.3mf`  
> The server will automatically extract the filament/colour data and pass it to the IFS.

### 4. Add a Home Assistant button

In Home Assistant, create a **REST command** and then add a **Button card**.

**`configuration.yaml`** (or a separate `rest_command.yaml`):

```yaml
rest_command:
  print_next_job:
    url: "http://<your-server-ip>:7125/queue/next"
    method: POST
    headers:
      X-Api-Key: "change-me-to-something-secret"
    content_type: "application/json"
```

Restart Home Assistant, then add a **Button card** to a dashboard:

```yaml
type: button
name: "Print Next Job"
icon: mdi:printer-3d
tap_action:
  action: call-service
  service: rest_command.print_next_job
```

**Optional — queue status sensor:**

```yaml
sensor:
  - platform: rest
    name: "Printer Queue Status"
    resource: "http://<your-server-ip>:7125/queue/status"
    headers:
      X-Api-Key: "change-me-to-something-secret"
    json_attributes:
      - printer
      - next_job
      - queued_count
    value_template: "{{ value_json.printer.state }}"
    scan_interval: 30
```

---

## API reference

All endpoints (except `/health`) require `X-Api-Key: <your-key>` header when `API_KEY` is set.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/api/version` | OctoPrint shim — OrcaSlicer compatibility check |
| `POST` | `/api/files/local` | **Upload file** — OrcaSlicer posts here |
| `GET` | `/api/files/local` | List files (OctoPrint shim) |
| `GET` | `/queue` | Full job list with statuses |
| `GET` | `/queue/status` | Live printer state + next queued job |
| `POST` | `/queue/next` | **Send next job to printer** — Home Assistant button |
| `DELETE` | `/queue/{id}` | Remove a job from the queue |

### Job statuses

| Status | Meaning |
|---|---|
| `queued` | Waiting to be sent |
| `sending` | Currently being uploaded to printer |
| `sent` | Successfully sent and print started |
| `error` | Send failed — check the `error` field for detail |

---

## IFS material mapping — important note

The AD5X's IFS (Integrated Filament System) maps **slicer tool indices** to **physical IFS slots**.  
This server assumes:

```
OrcaSlicer tool 0  →  IFS slot 1  (leftmost)
OrcaSlicer tool 1  →  IFS slot 2
OrcaSlicer tool 2  →  IFS slot 3
OrcaSlicer tool 3  →  IFS slot 4
```

This matches what OrcaSlicer's own FlashForge upload does.  

**If your spools are in a different physical order**, either:
- Rearrange the spools on the IFS to match the expected order, **or**
- Re-slice with the filament order in OrcaSlicer matching your physical spool order.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PRINTER_IP` | ✅ | — | AD5X local IP address |
| `PRINTER_SERIAL` | ✅ | — | Serial number from LAN mode screen |
| `PRINTER_CHECK_CODE` | ✅ | — | 8-digit check code from LAN mode screen |
| `API_KEY` | — | (none) | API key for request authentication |
| `LEVELING_BEFORE_PRINT` | — | `true` | Run ABL before each print |
| `START_PRINT_IMMEDIATELY` | — | `true` | Start print immediately after upload |
| `DATA_DIR` | — | `/data` | Directory for uploads and SQLite DB |

---

## File structure

```
printer-queue/
├── app/
│   ├── __init__.py
│   ├── config.py       # Environment-variable settings
│   ├── db.py           # SQLite job queue
│   ├── main.py         # FastAPI app (OctoPrint shim + queue endpoints)
│   ├── printer.py      # FlashForge API client (flashforge-python-api)
│   └── threemf.py      # .gcode.3mf metadata parser (IFS slot extraction)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Updating

```bash
docker compose pull   # or rebuild after code changes:
docker compose build --no-cache
docker compose up -d
```

The SQLite database and uploaded files live in the `printer-queue-data` Docker volume and survive container updates.

---

## Troubleshooting

**OrcaSlicer "Test" button fails**  
→ Check the server is running: `curl http://<server>:7125/health`  
→ Confirm the URL in OrcaSlicer has no trailing slash and includes `http://`  
→ Check the API key matches exactly

**`POST /queue/next` returns 502**  
→ Check printer is on and LAN mode is enabled  
→ Verify `PRINTER_IP`, `PRINTER_SERIAL`, `PRINTER_CHECK_CODE` in docker-compose.yml  
→ Run `docker compose logs printer-queue` to see the detailed error from the FlashForge API

**IFS doesn't load the right spool**  
→ See the "IFS material mapping" section above — rearrange physical spools to match the slicer tool order

**File uploaded but not printing**  
→ Check `START_PRINT_IMMEDIATELY=true` in your environment  
→ Or press the Home Assistant button if you left it as `false` (manual mode)
