# Home Assistant Deployment and Router Redirect UAT

This document captures the current user-acceptance path for running Tuya PSK
Bridge as a Home Assistant add-on and redirecting exactly one Tuya Wi-Fi door
sensor to it through the router.

Do not place real device IDs, local keys, MQTT passwords, or router credentials
in this file. Use the placeholders below in shared notes and keep the real
values only in Home Assistant add-on options and the router session used for
UAT.

## Current Deployment Path

The current Home Assistant deployment path is the local add-on checkout:

1. Clone this repository into the Home Assistant add-on directory:

   ```bash
   cd /addons
   git clone https://github.com/PetePeter/tuya-psk-bridge.git tuya-psk-bridge
   ```

2. In Home Assistant, open **Settings > Add-ons > Add-on Store**.
3. Refresh the store.
4. Open **Local add-ons** or **Local apps**.
5. Install **Tuya PSK Bridge**.
6. Configure the add-on from **Settings > Add-ons > Tuya PSK Bridge > Configuration**.
7. Start the add-on and leave **Start on boot** enabled for the HA-hosted
   deployment.

The add-on root is the repository root. `config.yaml` defines the add-on
metadata, options schema, and exposed TCP port. `Dockerfile` builds the add-on
image. `addon/run.sh` reads `/data/options.json`, writes the runtime bridge
config to `/data/bridge_config.yaml`, validates it with the bridge loader, and
starts `python -m tuya_psk_bridge.main`.

The default listener is:

```text
listen_host: 0.0.0.0
mqtt_psk_port: 8886
```

Home Assistant exposes TCP `8886` on the HA host by default. Router redirect
rules should point the target door sensor traffic at the Home Assistant host IP
and this port unless the add-on network setting is changed.

## Device Config and HA MQTT Entity Names

Each configured device has one or more DPS mappings. For a door sensor, the
current mapping is DPS `1` as a `binary_sensor` with device class `door`.

Example shape with redacted values:

```json
{
  "device_id": "REDACTED_DEVICE_ID",
  "local_key": "REDACTED_LOCAL_KEY",
  "name": "Front Door",
  "profile": "door_sensor",
  "mappings": [
    {
      "dps": "1",
      "platform": "binary_sensor",
      "device_class": "door",
      "values": "{\"open\":\"ON\",\"closed\":\"OFF\"}"
    }
  ]
}
```

The bridge publishes Home Assistant MQTT discovery from the device config:

| Source field | HA MQTT result |
|---|---|
| `name` | Discovery payload `name`, and the HA display name. HA derives the entity ID from this name, for example `binary_sensor.front_door`; conflicts may add a suffix. |
| `device_id` | Device registry identifier and part of stable topics/unique IDs. |
| `profile` | Device registry model. |
| `mappings[].platform` | MQTT discovery platform and entity domain, for example `binary_sensor`. |
| `mappings[].dps` | Part of the stable unique ID and state topic. |
| `mappings[].device_class` | HA device class, for example `door`. |
| `mappings[].values.open` / `closed` | `payload_on` and `payload_off` values for binary sensors. |

For device ID `<DEVICE_ID>`, platform `binary_sensor`, and DPS `1`, the bridge
uses:

```text
Discovery topic: homeassistant/binary_sensor/tuya_psk_<DEVICE_ID>_1/config
State topic:     homeassistant/binary_sensor/tuya_psk_<DEVICE_ID>_1/state
Availability:    homeassistant/status/tuya_psk_<DEVICE_ID>/availability
Unique ID:       tuya_psk_<DEVICE_ID>_1
```

The retained discovery message lets HA recreate the entity after a broker or HA
restart. State messages are not retained.

## Router Redirect Scope

For UAT, redirect only the selected door sensor IP. Do not redirect Tuya domains
globally and do not redirect all Smart Life or Tuya devices.

Use the existing router examples as references only:

- `examples/router-openwrt-runtime-rules.sh`
- `examples/router-openwrt-nftables-runtime-rules.sh`
- `examples/router-openwrt-rollback.sh`

The intended runtime scope for the current add-on UAT is:

```text
Source device: <DOOR_SENSOR_IP>
Original destination port: 8886
Redirect target: <HA_HOST_IP>:8886
Rule lifetime: runtime-only for UAT; router reboot clears it
```

The required NAT behavior is:

```text
DNAT:
  from <DOOR_SENSOR_IP> tcp/8886
  to   <HA_HOST_IP>:8886

MASQUERADE:
  from <DOOR_SENSOR_IP>
  to   <HA_HOST_IP>:8886
```

The source-IP match is the safety boundary. Before applying rules, confirm the
door sensor DHCP lease and ensure `<DOOR_SENSOR_IP>` belongs to the one target
door sensor only.

## UAT Verification

### Before Redirect

1. Confirm the MQTT broker is running in Home Assistant.
2. Confirm the Tuya PSK Bridge add-on starts successfully.
3. In the add-on log, confirm:

   ```text
   Configuration validated successfully.
   Starting Tuya PSK Bridge
   Listening on 0.0.0.0:8886
   ```

4. Confirm the add-on configuration contains exactly the target door sensor for
   this UAT pass unless intentionally testing multiple configured devices.
5. Confirm the working tree only contains the intended documentation change
   before sharing or committing:

   ```bash
   git status --short
   ```

   If any add-on option export, router transcript, packet capture, or log file
   was created during UAT, inspect it locally and keep it out of git unless it
   has been redacted.

### Apply Runtime Router Redirect

1. SSH to the router.
2. Set the runtime rule variables using the real UAT values in the shell only:

   ```sh
   DEVICE_IP="<DOOR_SENSOR_IP>"
   DEVICE_NAME="<DOOR_SENSOR_NAME>"
   BRIDGE_IP="<HA_HOST_IP>"
   MQTT_PSK_PORT="8886"
   ```

3. Apply the runtime DNAT and MASQUERADE rules using the documented example for
   the router firewall backend.
4. Verify only the target device rules exist:

   ```sh
   iptables -t nat -S | grep "tuya-psk-bridge-${DEVICE_NAME}"
   ```

   or, on nftables-based firmware:

   ```sh
   nft list ruleset | grep "tuya-psk-bridge"
   ```

5. Confirm the output shows one DNAT rule and one MASQUERADE rule for the target
   door sensor name/IP.

### Confirm Bridge and HA Behavior

1. Trigger the door sensor state change.
2. Watch the add-on log for an incoming connection, decoded DPS, and MQTT state
   publish.
3. Subscribe to HA MQTT discovery and state topics:

   ```sh
   mosquitto_sub -h <MQTT_HOST> -p 1883 -u "<MQTT_USER>" -P "<MQTT_PASSWORD>" -v -t "homeassistant/#"
   ```

4. Confirm the discovery topic appears:

   ```text
   homeassistant/binary_sensor/tuya_psk_<DEVICE_ID>_1/config
   ```

5. Confirm state updates appear:

   ```text
   homeassistant/binary_sensor/tuya_psk_<DEVICE_ID>_1/state ON
   homeassistant/binary_sensor/tuya_psk_<DEVICE_ID>_1/state OFF
   ```

6. In Home Assistant, confirm the MQTT entity appears under the Tuya device and
   changes state when the door sensor changes.
7. Confirm other Tuya or Smart Life devices continue operating normally. Any
   impact outside the target door sensor means the redirect scope is wrong and
   UAT should stop.

## Rollback

### Router Redirect Rollback

Remove the runtime redirect rules first:

1. SSH to the router.
2. Run the rollback commands from `examples/router-openwrt-rollback.sh` with the
   same `DEVICE_IP`, `DEVICE_NAME`, `BRIDGE_IP`, and `MQTT_PSK_PORT` used
   during apply.
3. Verify removal:

   ```sh
   iptables -t nat -S | grep "tuya-psk-bridge-${DEVICE_NAME}" || echo "No bridge rules found"
   ```

   or:

   ```sh
   nft list ruleset | grep "tuya-psk-bridge" || echo "No bridge rules found"
   ```

4. If needed, reboot the router to clear runtime-only rules.

### Home Assistant Rollback

1. Stop the **Tuya PSK Bridge** add-on.
2. Disable **Start on boot** if the bridge should remain off.
3. Remove or clear the target device from the add-on configuration if the add-on
   is kept installed.
4. If the MQTT discovery entity should be removed immediately, delete the
   retained discovery config for the target topic from the MQTT broker:

   ```sh
   mosquitto_pub -h <MQTT_HOST> -p 1883 -u "<MQTT_USER>" -P "<MQTT_PASSWORD>" \
     -r -n -t "homeassistant/binary_sensor/tuya_psk_<DEVICE_ID>_1/config"
   ```

5. Restart Home Assistant or reload MQTT discovery if the UI still shows the old
   entity.

## Secret Handling

- Do not commit real `device_id`, `local_key`, MQTT usernames, MQTT passwords,
  router passwords, or screenshots/logs containing those values.
- Store HA add-on secrets only in Home Assistant add-on options. HA Supervisor
  stores those options outside this repository.
- The add-on writes `/data/bridge_config.yaml` at runtime. Treat that file as
  sensitive because it contains resolved local keys and MQTT credentials.
- Router rules must not contain local keys. The router only redirects packets.
- UAT notes should use placeholders such as `<DEVICE_ID>`, `<DOOR_SENSOR_IP>`,
  `<HA_HOST_IP>`, `<MQTT_USER>`, and `<MQTT_PASSWORD>`.
- If sharing logs, redact device IDs and any MQTT credentials before attaching
  them. Local keys and derived PSK material should never appear in logs.
