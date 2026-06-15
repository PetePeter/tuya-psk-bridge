# Home Assistant Custom Integration

## Overview

The **Tuya PSK Bridge** custom integration for Home Assistant manages device
configurations that the bridge add-on or container uses to communicate with
Tuya IoT devices over the local network.

### Architecture

```
+-------------------+        MQTT         +-------------------+
|   Home Assistant  | <--- discovery --- |   Tuya PSK Bridge |
|   (this integra-  |                     |   (add-on /       |
|    tion + MQTT)   |                     |    container)      |
+-------------------+                     +-------------------+
                                                    |
                                              Tuya Local API
                                              (encrypted with
                                               PSK / local_key)
```

- **MVP**: This integration stores device configs. The bridge process runs
  as a Home Assistant add-on (or standalone container) and publishes entity
  state via MQTT discovery messages. Home Assistant picks up entities through
  the built-in MQTT integration.
- **Future**: Direct entity management without MQTT discovery once the bridge
  transport layer is stable. This eliminates the MQTT dependency and gives
  finer control over entity lifecycle.

## Installation

### Via HACS (recommended)

1. Add this repository as a [custom HACS repository][hacs-custom].
2. Install the **Tuya PSK Bridge** integration.
3. Restart Home Assistant.

### Manual (custom_components)

1. Copy the `custom_components/tuya_psk_bridge/` directory into your
   Home Assistant configuration directory:
   ```
   config/custom_components/tuya_psk_bridge/
   ```
2. Restart Home Assistant.

## Configuration

### Config Flow Walkthrough

After installation, go to **Settings > Devices & Services > Add Integration**
and search for **Tuya PSK Bridge**.

#### Step 1: Device Details

Enter the device information from the [Tuya IoT Platform][tuya-dev]:

| Field         | Description                                        |
|---------------|----------------------------------------------------|
| **Device ID** | 20-character hex string (e.g. `123456789abcdef0123`) |
| **Name**      | Human-readable name for this device                 |
| **Profile**   | `door_sensor` for Tuya door/gateway sensors, or `custom` |

#### Step 2: Local Key

Enter the **Local Key** (PSK) for the device. This is found in the Tuya IoT
Platform under the device details. Home Assistant encrypts this value in the
config entry.

#### Step 3: DPS Mappings

Configure which Tuya Data Point (DPS) maps to a Home Assistant entity.
For known profiles, sensible defaults are pre-populated:

| Profile        | DPS | Platform        | Device Class |
|----------------|-----|-----------------|--------------|
| `door_sensor`  | 1   | `binary_sensor` | `door`       |

#### Step 4: Add More Devices

You can add additional devices before finishing setup.

### Reconfiguration

After initial setup, you can reconfigure the integration via
**Settings > Devices & Services > Tuya PSK Bridge > Configure**.

## Diagnostics

The integration supports Home Assistant diagnostics. When diagnostics are
downloaded or shared:

- **Collected**: Integration version, device count, device IDs, names,
  profiles, and DPS mappings.
- **Redacted**: Local keys and any other sensitive fields are replaced with
  `**REDACTED**` before the data leaves your system.

No raw secrets, passwords, or encryption keys are ever included in
diagnostic dumps.

## Roadmap

| Phase | Description                                         |
|-------|-----------------------------------------------------|
| MVP   | Config storage + bridge add-on coordination          |
| v0.2  | Options flow with full device editing               |
| v0.3  | Direct entity management (no MQTT discovery needed) |
| v0.4  | Auto-discovery of Tuya devices on the local network  |

[hacs-custom]: https://hacs.xyz/docsfaq/custom_repositories
[tuya-dev]: https://iot.tuya.com/
