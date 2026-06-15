# Deploying as a Home Assistant Add-on

The Tuya PSK Bridge is distributed as a [Home Assistant add-on][ha-addons].
This is the preferred deployment mode for HA OS and HA Container installations.

## Prerequisites

- **Home Assistant OS** or **Home Assistant Container** (supervised mode).
- **MQTT broker** accessible from the HA network (the official Mosquitto
  add-on works out of the box).
- Your router configured to redirect Tuya device traffic to the bridge
  (see [OpenWrt/GL.iNet redirect guide](./router-openwrt-glinet.md)).

## Installation

1. Build the Docker image and push to your Docker Hub account. Update the
   `image` field in `addon/config.yaml` with your Docker Hub username.
2. Copy the `addon/` directory into the Home Assistant add-on repository
   structure, or use the [HA Add-on builder][ha-builder] to publish.
3. In Home Assistant, go to **Settings > Add-ons > Add-on Store** and
   install the **Tuya PSK Bridge** add-on.

## Configuration

Configure the add-on through **Settings > Add-ons > Tuya PSK Bridge > Configuration**.

### Options

| Option | Required | Default | Description |
|---|---|---|---|
| `listen_host` | No | `0.0.0.0` | Address the PSK listener binds to. |
| `mqtt_psk_port` | No | `8886` | Port for incoming Tuya TLS-PSK connections. |
| `ha_mqtt_host` | No | `core-mosquitto` | HA MQTT broker hostname. |
| `ha_mqtt_port` | No | `1883` | HA MQTT broker port. |
| `ha_mqtt_username` | No | | MQTT broker username (if auth is enabled). |
| `ha_mqtt_password` | No | | MQTT broker password. |
| `log_level` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `devices` | Yes | | Array of device configurations (see below). |

### Device Entry

Each device in the `devices` array requires:

```json
{
  "device_id": "abcd1234efgh5678ijkl",
  "local_key": "ffffffffffffffff",
  "name": "Front Door Sensor",
  "profile": "door_sensor",
  "mappings": [
    {
      "dps": "1",
      "platform": "binary_sensor",
      "device_class": "door",
      "values": { "true": "open", "false": "closed" }
    }
  ]
}
```

| Field | Required | Description |
|---|---|---|
| `device_id` | Yes | Tuya device identifier (16 hex chars). |
| `local_key` | Yes | AES decryption key for this device. |
| `name` | Yes | Human-readable name (used in HA entity names). |
| `profile` | Yes | Device profile name (e.g. `door_sensor`). |
| `mappings` | Yes | DPS-to-HA entity mappings. |

### Mapping Entry

| Field | Required | Description |
|---|---|---|
| `dps` | Yes | Tuya data point ID (e.g. `"1"`). |
| `platform` | Yes | HA platform (`binary_sensor`, `sensor`, etc.). |
| `device_class` | No | HA device class (e.g. `door`, `temperature`). |
| `values` | No | Key-value map for translating raw values. |

## Network Requirements

The bridge must be reachable on the Tuya TLS-PSK port by your Wi-Fi devices.

- **HA OS**: The add-on runs with host networking. Ensure the `mqtt_psk_port`
  is not blocked by firewall rules.
- **HA Container**: Expose the port in your container runtime configuration.
- **Standard ports (below 1024)**: Require host networking or port forwarding.
  The default of `8886` avoids this issue.

## Verification

After starting the add-on:

1. **Check add-on logs**:
   Settings > Add-ons > Tuya PSK Bridge > Log.
   Look for `"Configuration validated successfully"` and `"Starting Tuya PSK Bridge"`.
2. **Check HA entities**:
   Settings > Devices & Services > MQTT. Your device entities should appear
   within a few seconds of a device reporting state.
3. **Check MQTT messages**:
   Use `mqtt` CLI or an MQTT explorer tool to subscribe to
   `homeassistant/#` and watch for discovery and state messages.

## Troubleshooting

### Port binding failures

```
ERROR: Address already in use
```

- Another process is using the configured `mqtt_psk_port`.
- Change the port to a different value, or stop the conflicting service.

### Device not connecting

- Verify the router DNAT/redirect rules are pointing to the bridge host and port.
- Check the add-on logs for TLS handshake errors.
- Ensure the `device_id` and `local_key` match the device's credentials.

### MQTT connection failures

```
ERROR: Connection refused to core-mosquitto:1883
```

- Confirm the MQTT broker add-on is running.
- If using a custom broker, update `ha_mqtt_host` and `ha_mqtt_port`.
- If auth is enabled, set `ha_mqtt_username` and `ha_mqtt_password`.

## Secrets Management

Add-on options are **encrypted at rest** by Home Assistant. They are never
committed to the repository and survive add-on updates. No real secrets,
device IDs, or local keys are ever present in the public codebase.

---

# Deploying with Docker Compose

For setups without Home Assistant OS, use Docker Compose to run the bridge
as a standalone container.

## Build and Run

1. Clone the repository and build the image:

   ```bash
   cd /path/to/tuya-psk-bridge
   docker compose -f docker-compose.example.yaml build
   ```

2. Copy the example compose file and customize:

   ```bash
   cp docker-compose.example.yaml docker-compose.yaml
   ```

3. Create a `config/` directory with your bridge configuration:

   ```yaml
   # config/bridge_config.yaml
   listen_host: "0.0.0.0"
   mqtt_psk_port: 8886
   ha_mqtt_host: "192.168.1.100"  # Your MQTT broker
   ha_mqtt_port: 1883
   log_level: "INFO"
   devices:
     - device_id: "REDACTED_DEVICE_ID"
       local_key: "!secret my_door_key"
       name: "Front Door"
       profile: "door_sensor"
       mappings:
         - dps: "1"
           platform: "binary_sensor"
           device_class: "door"
   ```

4. Set secrets via environment variables:

   ```bash
   export TUYA_MY_DOOR_KEY=ffffffffffffffff
   ```

5. Start the bridge:

   ```bash
   docker compose up -d
   ```

## Volume Mounts

| Host Path | Container Path | Purpose |
|---|---|---|
| `./config` | `/data` | Bridge configuration and runtime data. |

The bridge reads `/data/bridge_config.yaml` at startup. The `!secret`
directive resolves from environment variables (prefixed with `TUYA_`).

## Networking

The `network_mode: host` setting is recommended so the bridge can bind
to the standard Tuya port (6668 if needed). If using a high port (default
8886), you can switch to port mapping:

```yaml
ports:
  - "8886:8886"
# Remove: network_mode: host
```

[ha-addons]: https://developers.home-assistant.io/docs/add-ons/
[ha-builder]: https://github.com/home-assistant/addon-builder
