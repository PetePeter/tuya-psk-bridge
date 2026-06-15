# Router Configuration for Tuya PSK Bridge (OpenWrt / GL.iNet)

This guide explains how to configure an OpenWrt-based router (including GL.iNet devices) to redirect a specific Tuya device's cloud traffic to your local PSK bridge instead of the Tuya cloud.

## How It Works

```mermaid
graph LR
    TD["Tuya Device"] -->|"PSK-encrypted MQTT<br/>destination: Tuya Cloud"| R["Router<br/>DNAT + SNAT"]
    R -->|"rewritten destination:<br/>Bridge Host"| BH["Bridge Host (HA)<br/>tuya-psk-bridge"]
    BH -->|"decrypted MQTT<br/>local MQTT broker"| HA["Home Assistant"]
```

The bridge acts as a transparent proxy. It presents itself to the Tuya device as the cloud server, decrypts the PSK-encrypted traffic using the local key, and republishes the plaintext data to your local MQTT broker for Home Assistant to consume.

## Why DNAT + MASQUERADE Are Both Needed

On a typical home network the Tuya device, the router, and the bridge host are all on the **same LAN subnet**. This means:

- **DNAT (Destination NAT)** rewrites the *destination* IP and port of packets leaving the Tuya device so they arrive at `<BRIDGE_IP>:<MQTT_PSK_PORT>` instead of the Tuya cloud.
- **MASQUERADE (Source NAT)** rewrites the *source* IP of the return packet so the Tuya device sees a reply from the router's own LAN address rather than from `<BRIDGE_IP>`. Without MASQUERADE the Tuya device ignores the reply because the source address does not match the address it sent to.

In short: DNAT gets the packet *to* the bridge; MASQUERADE gets the reply *back* to the device.

## Runtime vs Persistent Rules

### Runtime-Only (Recommended for Initial Testing)

Runtime rules are applied directly via `iptables` or `nft` commands. They take effect immediately and are lost on reboot. This is the safest way to start because:

- You can verify the bridge works before committing anything permanent.
- A reboot clears everything if something goes wrong.
- No risk of breaking router upgrades or factory resets.

Use the provided `router-openwrt-runtime-rules.sh` script to apply rules, and `router-openwrt-rollback.sh` to remove them.

### Persistent (Advanced)

Persistent rules survive reboots by hooking into OpenWrt's firewall framework. Two approaches exist:

- **`/etc/firewall.user`** -- OpenWrt runs this script after the firewall is initialized. Add your iptables/nft commands here.
- **Hotplug scripts** (`/etc/hotplug.d/iface/`) -- Run when a network interface comes up. Useful if you need the rules only on a specific interface event.

Persistent rules are brittle across firmware upgrades and can interfere with the router's own firewall management. Use them only after runtime rules are confirmed working.

## Runtime Rules (iptables)

See `../examples/router-openwrt-runtime-rules.sh` for a ready-to-use script.

Key rules applied:

```bash
# DNAT: redirect Tuya device MQTT traffic to the bridge host
iptables -t nat -A prerouting_lan_rule \
  -s <DEVICE_IP> \
  -p tcp --dport 443 \
  -j DNAT --to-destination <BRIDGE_IP>:<MQTT_PSK_PORT> \
  -m comment --comment "tuya-psk-bridge-<DEVICE_NAME>-dnat"

# MASQUERADE: rewrite source so return packets route back through the router
iptables -t nat -A postrouting_lan_rule \
  -s <DEVICE_IP> \
  -d <BRIDGE_IP> \
  -p tcp --dport <MQTT_PSK_PORT> \
  -j MASQUERADE \
  -m comment --comment "tuya-psk-bridge-<DEVICE_NAME>-snat"
```

## Runtime Rules (nftables, OpenWrt 22.03+)

OpenWrt 22.03 and later use nftables by default. See `../examples/router-openwrt-nftables-runtime-rules.sh` for the equivalent script.

## Verification

After applying rules, confirm they are active:

```bash
# iptables
iptables -t nat -S | grep tuya-psk-bridge || echo "No bridge rules found"

# nftables (OpenWrt 22.03+)
nft list ruleset | grep tuya-psk-bridge || echo "No bridge rules found"
```

You should see two rules (one DNAT, one MASQUERADE) with the comment tag matching your `<DEVICE_NAME>`.

Also verify from the Tuya device side (if it has a debug shell) that its MQTT connection is reaching `<BRIDGE_IP>`:

```bash
# From the device or via the bridge host, watch for the incoming connection
tcpdump -i br-lan -n host <DEVICE_IP> and port <MQTT_PSK_PORT>
```

## Warnings

1. **DO NOT redirect all Tuya domains globally.** These rules target a single `<DEVICE_IP>`. A blanket DNAT for all traffic to `*.tuya*.com` or `*.smartlife*.com` will break every other Tuya/Smart Life device on your network.

2. **DO NOT affect other Smart Life devices.** The `-s <DEVICE_IP>` match ensures only the target device is redirected. Double-check the IP before applying rules.

3. **DO NOT put local keys in router config.** The router never sees the PSK local key. All cryptographic operations happen on the bridge host. The router's job is strictly packet redirection.

## Same-LAN Note

The MASQUERADE rule is required because the device and bridge share the same LAN subnet. If the bridge were on a different subnet reachable only through the router, the router would perform routing NAT naturally and MASQUERADE would not be needed. However, the typical Home Assistant setup places the bridge on the same LAN as the Tuya devices, making explicit MASQUERADE necessary.

## Removing Rules

Use the provided `../examples/router-openwrt-rollback.sh` (iptables) or `../examples/router-openwrt-nftables-runtime-rules.sh` with the delete flag (nftables) to cleanly remove all rules without errors.
