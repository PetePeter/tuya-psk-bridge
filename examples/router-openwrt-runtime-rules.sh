#!/bin/sh
# router-openwrt-runtime-rules.sh
#
# Apply runtime DNAT + MASQUERADE rules on an OpenWrt / GL.iNet router
# to redirect a specific Tuya device's MQTT-over-TLS traffic to the
# local tuya-psk-bridge host.
#
# These rules are NOT persistent -- they are lost on reboot.
# Use for initial testing only. See docs/router-openwrt-glinet.md for
# persistent configuration guidance.
#
# Usage:
#   sh router-openwrt-runtime-rules.sh
#
# Requires: iptables (nftables equivalent: router-openwrt-nftables-runtime-rules.sh)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration -- edit these values for your setup
# ---------------------------------------------------------------------------

# IP address of the Tuya device on your LAN
DEVICE_IP="<DEVICE_IP>"

# Human-readable name used in iptables comment tags (for identification)
DEVICE_NAME="<DEVICE_NAME>"

# IP address of the host running tuya-psk-bridge (usually your HA server)
BRIDGE_IP="<BRIDGE_IP>"

# Port the bridge listens on for PSK-encrypted MQTT (default: 8886)
MQTT_PSK_PORT="${MQTT_PSK_PORT:-8886}"

# The destination port the Tuya device sends to (usually 443 for TLS)
HTTPS_PORT="${HTTPS_PORT:-443}"

# ---------------------------------------------------------------------------
# Apply DNAT rule (prerouting)
# ---------------------------------------------------------------------------
# Rewrites the destination of packets from DEVICE_IP:443 to BRIDGE_IP:MQTT_PSK_PORT
# so they land on the bridge instead of the Tuya cloud.

iptables -t nat -A prerouting_lan_rule \
  -s "$DEVICE_IP" \
  -p tcp --dport "$HTTPS_PORT" \
  -j DNAT --to-destination "$BRIDGE_IP:$MQTT_PSK_PORT" \
  -m comment --comment "tuya-psk-bridge-${DEVICE_NAME}-dnat"

echo "DNAT rule added: ${DEVICE_IP}:${HTTPS_PORT} -> ${BRIDGE_IP}:${MQTT_PSK_PORT}"

# ---------------------------------------------------------------------------
# Apply MASQUERADE rule (postrouting)
# ---------------------------------------------------------------------------
# Rewrites the source address of return packets so the Tuya device sees
# them coming from the router's LAN address, which is the address the device
# originally sent to. Without this the device ignores the reply.

iptables -t nat -A postrouting_lan_rule \
  -s "$DEVICE_IP" \
  -d "$BRIDGE_IP" \
  -p tcp --dport "$MQTT_PSK_PORT" \
  -j MASQUERADE \
  -m comment --comment "tuya-psk-bridge-${DEVICE_NAME}-snat"

echo "MASQUERADE rule added for ${DEVICE_IP} -> ${BRIDGE_IP}:${MQTT_PSK_PORT}"

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

echo ""
echo "--- Verifying rules ---"

if iptables -t nat -S | grep -q "tuya-psk-bridge-${DEVICE_NAME}"; then
  echo "OK: Bridge rules are active."
  echo ""
  iptables -t nat -S | grep "tuya-psk-bridge-${DEVICE_NAME}"
else
  echo "WARNING: No bridge rules found. Check that iptables is available and the rules were accepted."
  exit 1
fi
