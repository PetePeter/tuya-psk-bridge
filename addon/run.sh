#!/usr/bin/env bash
# Entrypoint for the Tuya PSK Bridge Home Assistant add-on.
#
# Reads /data/options.json (HA add-on options), writes a bridge-compatible
# YAML config to /data/bridge_config.yaml, and starts the bridge process.
# Handles SIGTERM for graceful shutdown.

set -euo pipefail

CONFIG_PATH="/data/bridge_config.yaml"
OPTIONS_PATH="/data/options.json"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

log_info()  { echo "[INFO]  $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_warn()  { echo "[WARN]  $(date '+%Y-%m-%d %H:%M:%S') $*" >&2; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2; }

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

BRIDGE_PID=""

cleanup() {
    log_info "Shutdown signal received (SIGTERM)."
    if [[ -n "${BRIDGE_PID}" ]]; then
        kill -TERM "${BRIDGE_PID}" 2>/dev/null || true
        # Wait up to 10 seconds for the bridge to exit
        timeout=10
        while kill -0 "${BRIDGE_PID}" 2>/dev/null && (( timeout > 0 )); do
            sleep 1
            (( timeout -= 1 ))
        done
        if kill -0 "${BRIDGE_PID}" 2>/dev/null; then
            log_warn "Bridge did not exit within ${timeout}s; sending SIGKILL."
            kill -KILL "${BRIDGE_PID}" 2>/dev/null || true
        fi
    fi
    log_info "Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT

# ---------------------------------------------------------------------------
# Read add-on options
# ---------------------------------------------------------------------------

if [[ ! -f "${OPTIONS_PATH}" ]]; then
    log_error "Add-on options file not found at ${OPTIONS_PATH}"
    log_error "Configure the add-on in Home Assistant before starting."
    exit 1
fi

# Extract scalar options (jq returns empty string for missing keys)
LISTEN_HOST=$(jq -r '.listen_host // "0.0.0.0"' "${OPTIONS_PATH}")
MQTT_PSK_PORT=$(jq -r '.mqtt_psk_port // 8886' "${OPTIONS_PATH}")
HA_MQTT_HOST=$(jq -r '.ha_mqtt_host // "core-mosquitto"' "${OPTIONS_PATH}")
HA_MQTT_PORT=$(jq -r '.ha_mqtt_port // 1883' "${OPTIONS_PATH}")
HA_MQTT_USERNAME=$(jq -r '.ha_mqtt_username // ""' "${OPTIONS_PATH}")
HA_MQTT_PASSWORD=$(jq -r '.ha_mqtt_password // ""' "${OPTIONS_PATH}")
LOG_LEVEL=$(jq -r '.log_level // "INFO"' "${OPTIONS_PATH}")

log_info "Add-on options loaded from ${OPTIONS_PATH}"

# ---------------------------------------------------------------------------
# Build the bridge config YAML
# The bridge reads this via config.load_config().
# ---------------------------------------------------------------------------

log_info "Writing bridge config to ${CONFIG_PATH}"

# Write the top-level bridge settings
cat > "${CONFIG_PATH}" <<EOF
listen_host: "${LISTEN_HOST}"
mqtt_psk_port: ${MQTT_PSK_PORT}
ha_mqtt_host: "${HA_MQTT_HOST}"
ha_mqtt_port: ${HA_MQTT_PORT}
EOF

# Optional HA MQTT credentials
if [[ -n "${HA_MQTT_USERNAME}" ]]; then
    echo "ha_mqtt_username: \"${HA_MQTT_USERNAME}\"" >> "${CONFIG_PATH}"
fi
if [[ -n "${HA_MQTT_PASSWORD}" ]]; then
    echo "ha_mqtt_password: \"${HA_MQTT_PASSWORD}\"" >> "${CONFIG_PATH}"
fi

# Log level
echo "log_level: \"${LOG_LEVEL}\"" >> "${CONFIG_PATH}"

# ---------------------------------------------------------------------------
# Write device entries
# Each device's local_key is written directly because HA add-on options
# are encrypted at rest and never committed to the repository.
# ---------------------------------------------------------------------------

DEVICE_COUNT=$(jq '.devices | length' "${OPTIONS_PATH}")
echo "devices:" >> "${CONFIG_PATH}"

if [[ "${DEVICE_COUNT}" -eq 0 ]]; then
    log_warn "No devices configured. Add devices in the add-on options."
fi

for i in $(seq 0 $((DEVICE_COUNT - 1))); do
    DEVICE_ID=$(jq -r ".devices[${i}].device_id" "${OPTIONS_PATH}")
    LOCAL_KEY=$(jq -r ".devices[${i}].local_key" "${OPTIONS_PATH}")
    NAME=$(jq -r ".devices[${i}].name" "${OPTIONS_PATH}")
    PROFILE=$(jq -r ".devices[${i}].profile" "${OPTIONS_PATH}")

    if [[ -z "${DEVICE_ID}" || "${DEVICE_ID}" == "null" ]]; then
        log_error "Device at index ${i} is missing required field 'device_id'."
        exit 1
    fi
    if [[ -z "${LOCAL_KEY}" || "${LOCAL_KEY}" == "null" ]]; then
        log_error "Device ${DEVICE_ID} is missing required field 'local_key'."
        exit 1
    fi
    if [[ -z "${NAME}" || "${NAME}" == "null" ]]; then
        log_error "Device ${DEVICE_ID} is missing required field 'name'."
        exit 1
    fi
    if [[ -z "${PROFILE}" || "${PROFILE}" == "null" ]]; then
        log_error "Device ${DEVICE_ID} is missing required field 'profile'."
        exit 1
    fi

    cat >> "${CONFIG_PATH}" <<DEVBLOCK
  - device_id: "${DEVICE_ID}"
    local_key: "${LOCAL_KEY}"
    name: "${NAME}"
    profile: "${PROFILE}"
    mappings:
DEVBLOCK

    MAPPING_COUNT=$(jq ".devices[${i}].mappings | length" "${OPTIONS_PATH}")
    for j in $(seq 0 $((MAPPING_COUNT - 1))); do
        DPS=$(jq -r ".devices[${i}].mappings[${j}].dps" "${OPTIONS_PATH}")
        PLATFORM=$(jq -r ".devices[${i}].mappings[${j}].platform" "${OPTIONS_PATH}")
        DEVICE_CLASS=$(jq -r ".devices[${i}].mappings[${j}].device_class // empty" "${OPTIONS_PATH}")
        VALUES=$(jq -c ".devices[${i}].mappings[${j}].values // empty" "${OPTIONS_PATH}")

        if [[ -z "${DPS}" || "${DPS}" == "null" ]]; then
            log_error "Mapping at devices[${i}].mappings[${j}] missing 'dps'."
            exit 1
        fi
        if [[ -z "${PLATFORM}" || "${PLATFORM}" == "null" ]]; then
            log_error "Mapping at devices[${i}].mappings[${j}] missing 'platform'."
            exit 1
        fi

        echo "      - dps: \"${DPS}\"" >> "${CONFIG_PATH}"
        echo "        platform: \"${PLATFORM}\"" >> "${CONFIG_PATH}"
        if [[ -n "${DEVICE_CLASS}" && "${DEVICE_CLASS}" != "null" ]]; then
            echo "        device_class: \"${DEVICE_CLASS}\"" >> "${CONFIG_PATH}"
        fi
        if [[ -n "${VALUES}" && "${VALUES}" != "null" ]]; then
            echo "        values: ${VALUES}" >> "${CONFIG_PATH}"
        fi
    done

    log_info "Device configured: ${NAME} (${DEVICE_ID})"
done

# ---------------------------------------------------------------------------
# Validate the generated config with the bridge's own loader
# ---------------------------------------------------------------------------

log_info "Validating generated configuration..."
if python -c "
from tuya_psk_bridge.config import load_config
cfg = load_config('${CONFIG_PATH}')
print(f'Config OK: {len(cfg.devices)} device(s), listen on {cfg.listen_host}:{cfg.mqtt_psk_port}')
" 2>&1; then
    log_info "Configuration validated successfully."
else
    log_error "Generated configuration failed validation. Check options and try again."
    exit 1
fi

# ---------------------------------------------------------------------------
# Start the bridge process
# ---------------------------------------------------------------------------

log_info "Starting Tuya PSK Bridge (log level: ${LOG_LEVEL})..."
log_info "Listening on ${LISTEN_HOST}:${MQTT_PSK_PORT}"
log_info "Forwarding to HA MQTT at ${HA_MQTT_HOST}:${HA_MQTT_PORT}"

python -m tuya_psk_bridge.main --config "${CONFIG_PATH}" &
BRIDGE_PID=$!

# Wait for the bridge to exit (or be killed by cleanup on SIGTERM)
wait "${BRIDGE_PID}"
BRIDGE_EXIT=$?

if [[ ${BRIDGE_EXIT} -ne 0 ]]; then
    log_error "Bridge exited with code ${BRIDGE_EXIT}."
fi

exit ${BRIDGE_EXIT}
