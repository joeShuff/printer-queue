#!/usr/bin/with-contenv bashio

# Read options from HA addon configuration
PRINTER_IP=$(bashio::config 'printer_ip')
PRINTER_SERIAL=$(bashio::config 'printer_serial')
PRINTER_CHECK_CODE=$(bashio::config 'printer_check_code')
CAMERA_STREAM_URL=$(bashio::config 'camera_stream_url' '')

# Get the ingress port assigned by HA supervisor
INGRESS_PORT=$(bashio::addon.ingress_port)
INGRESS_ENTRY=$(bashio::addon.ingress_entry)

bashio::log.info "Starting AD5X Print Queue"
bashio::log.info "Printer IP: ${PRINTER_IP}"
bashio::log.info "Ingress port: ${INGRESS_PORT}"
bashio::log.info "Ingress path: ${INGRESS_ENTRY}"

# Data directory on the HA share volume
DATA_DIR="/share/printer-queue"
mkdir -p "${DATA_DIR}"

# Export environment variables for the app
export PRINTER_IP="${PRINTER_IP}"
export PRINTER_SERIAL="${PRINTER_SERIAL}"
export PRINTER_CHECK_CODE="${PRINTER_CHECK_CODE}"
export DATA_DIR="${DATA_DIR}"
export INGRESS_PATH="${INGRESS_ENTRY}"

if [ -n "${CAMERA_STREAM_URL}" ] && [ "${CAMERA_STREAM_URL}" != "null" ]; then
    export CAMERA_STREAM_URL="${CAMERA_STREAM_URL}"
fi

# Start the queue server on the OrcaSlicer port (8898) in the background
# OrcaSlicer connects here directly — not through ingress
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8898 \
    --log-level info &

# Start the ingress-facing server on the HA ingress port
# Only accepts connections from the HA supervisor (172.30.32.2)
export INGRESS_MODE=true
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${INGRESS_PORT}" \
    --log-level info