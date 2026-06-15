# Security

## What This Is Not

This project is **not** a cryptographic break. It does not crack, bypass,
or weaken Tuya's TLS implementation. It uses known device material (the
local key) to terminate TLS-PSK connections the same way existing
open-source tooling (tuya-convert, tinytuya) does.

The local key is a per-device secret already stored on the device itself.
The bridge uses it with the operator's explicit consent and configuration.

## Cryptographic Details

### TLS-PSK Cipher Suite

Confirmed from packet capture: **TLS_PSK_WITH_AES_128_CBC_SHA256**

This is standard TLS with pre-shared keys. The device presents its PSK
identity hint, and the bridge proves knowledge of the shared secret.

### MQTT Payload Encryption

Tuya encrypts local-protocol MQTT payloads with **AES-128-ECB** using the
device's local key. This is Tuya's protocol choice, not ours. ECB mode is
weak (identical plaintext blocks produce identical ciphertext blocks), but
it is what the device firmware implements.

## Threat Model

### Primary Threat

An attacker who obtains a device's local key can impersonate the Tuya
cloud to that device on the local network. This is the same threat surface
that tuya-convert exploits, and it requires:

1. Physical or logical access to the local network.
2. Knowledge of the device's local key.
3. Completion of the initial Tuya cloud pairing (local key is derived
   during provisioning).

### Impact Bounds

- The attacker can **send commands** to the device (e.g., open a door
  sensor's relay, if applicable).
- The attacker **cannot** extract the cloud credentials or session keys.
- The attacker **cannot** communicate with the device over the internet
  without also compromising the router or ISP.

## Mitigations

| Mitigation | Detail |
|---|---|
| **Local network only** | Bridge binds to `0.0.0.0` by default but should be scoped to the LAN interface. No internet-facing exposure. |
| **Source-IP-scoped redirects** | Router DNAT rules should restrict source IPs to known device addresses where possible. |
| **No internet exposure** | The bridge listens on a non-standard port and should never be port-forwarded to the WAN. |
| **Optional TLS on MQTT** | Bridge-to-HA MQTT connection can use TLS for defense-in-depth on shared networks. |

## Secret Handling

### Storage

Local keys are stored **outside** the repository:

| Deployment | Storage |
|---|---|
| HA Add-on | Add-on options (stored in HA Supervisor, not in repo) |
| Docker | Environment variables or mounted secrets file |
| Manual Python | `.env` file or CLI args (`.env` in `.gitignore`) |

### PSK Derivation

The PSK used for TLS termination is derived from the device's local key
plus protocol-derived identity and hint values. The derivation is
documented in the code but intentionally kept opaque in these docs to
avoid simplifying misuse. See the source for the exact algorithm.

### Log Safety

- **Logged**: device IDs, connection events, DPS parse results (for debugging).
- **Never logged**: local keys, PSK secrets, raw encrypted payloads, decrypted
  payloads in production (only at explicit `DEBUG` level with a warning banner).

## Comparison to Related Tools

| Tool | Approach | Key Difference |
|---|---|---|
| **tuya-convert** | Spoofs Tuya cloud during provisioning to extract local key | One-time attack during pairing |
| **tinytuya** | Uses known local key to communicate with device | Python library, no bridge mode |
| **This bridge** | Uses known local key to passively intercept and publish state | Continuous, HA-integrated, discovery-based |
