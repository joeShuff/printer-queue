# AD5X Print Queue — Home Assistant Addon

Adds the AD5X Print Queue as a native Home Assistant addon, accessible through the HA sidebar and remote via Nabu Casa.

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**
2. Click the three-dot menu (⋮) → **Repositories**
3. Add: `https://github.com/yourname/printer-queue`
4. Find **AD5X Print Queue** and click **Install**

## Configuration

| Option | Required | Description |
|---|---|---|
| `printer_ip` | ✅ | AD5X local IP — from Settings → Network → LAN Mode |
| `printer_serial` | ✅ | Serial number from LAN mode screen |
| `printer_check_code` | ✅ | Check code from LAN mode screen |
| `camera_stream_url` | — | Override camera URL (e.g. go2rtc MJPEG endpoint) |

## Connecting OrcaSlicer

OrcaSlicer connects **directly** to the addon on port 8898 — not through the HA ingress. Use your HA host's local IP:

| Field | Value |
|---|---|
| IP Address | Your HA host IP (e.g. `192.168.1.10`) |
| Port | `8898` |
| Serial Number | Same as `printer_serial` in addon config |
| Check Code | Same as `printer_check_code` in addon config |

Click the **Connect** button in the queue UI to see these values.

## Web UI

The queue UI is accessible:
- **Locally**: via the HA sidebar → Print Queue
- **Remotely**: via Nabu Casa / HA Cloud, same sidebar link
- **Direct**: `http://<ha-host>:8898` on your LAN

## Data storage

Files and the queue database are stored in `/share/printer-queue` — shared HA storage that survives addon updates and reinstalls.